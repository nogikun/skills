"""Local web UI (`scribe serve`): job monitor + speaker editor.

Files under SCRIBE_HOME are the only source of truth: the server polls them and pushes changes
over SSE. The only thing it writes is speaker names, through the same cli.apply_names as the CLI.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import numpy as np

from . import audio, cli
from . import job as J
from .export import EXPORTERS

log = logging.getLogger("scribe")
WEB = Path(__file__).with_name("web")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
# ponytail: fixed stage weights; replace with the ratio of measured stages.*.secs once jobs pile up
WEIGHTS = {"audio": 5, "diarize": 35, "transcribe": 50, "fill": 5, "merge": 2, "samples": 3}
FRESH = 30  # s: progress.json newer than this = the process is alive (no lock probing)
EXT = {"markdown": "md", "webvtt": "vtt"}
HTTP = {"job_not_found": 404, "job_busy": 409, "stale": 409, "not_processed": 409, "not_running": 409}


def progress_pct(job: dict, prog: dict | None) -> float:
    stages = job["stages"]
    done = sum(w for s, w in WEIGHTS.items() if stages.get(s, {}).get("status") == "completed")
    if prog and prog.get("total") and stages.get(prog.get("stage"), {}).get("status") == "running":
        done += WEIGHTS[prog["stage"]] * min(1.0, (prog.get("n") or 0) / prog["total"])
    return round(100 * done / sum(WEIGHTS.values()), 1)


def _read(path: Path):
    try:
        return J.read_json(path)
    except (OSError, ValueError):  # mid-replace on Windows: the next poll gets it
        return None


def job_view(job_id: str) -> dict:
    job = J.load(job_id)
    prog = _read(J.job_dir(job_id) / "progress.json")
    running = next((n for n, s in job["stages"].items() if s.get("status") == "running"), None)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(prog["updated_at"])).total_seconds() \
        if prog and prog.get("updated_at") else None
    alive = True if running and prog and prog.get("stage") == running and age is not None and age < FRESH else None
    out = cli.summary(job, alive)
    if out["status"] == "failed":
        running = None
    out.update(name=Path(job["input"]).name, created_at=job["created_at"], running=running,
               progress=prog if running else None, pct=progress_pct(job, prog if running else None),
               diarize_tag=cli.diarize_tag(job))
    return out


def _job_ids() -> list[str]:
    return [p.parent.name for p in (J.HOME / "jobs").glob("*/job.json")]


def _stamp(job_id: str) -> tuple:
    d = J.job_dir(job_id)
    return tuple(p.stat().st_mtime_ns if p.exists() else 0
                 for p in (d / "job.json", d / "progress.json", d / "speakers.json"))


class Handler(BaseHTTPRequestHandler):
    hosts: set = set()  # filled by serve()

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    # --- plumbing -----------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code=200, headers=None):
        self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8", headers)

    def _error(self, code: int, err: str, msg: str):
        self._json({"error": err, "message": msg}, code)

    def _file(self, path: Path, ctype: str):
        """Serve a file with single-range support (<audio> needs it to seek)."""
        size = path.stat().st_size
        a, b, code, extra = 0, size - 1, 200, {"Accept-Ranges": "bytes"}
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if m and (m[1] or m[2]):
            a, b = (int(m[1]), int(m[2]) if m[2] else size - 1) if m[1] else (max(0, size - int(m[2])), size - 1)
            b = min(b, size - 1)
            if a > b:
                return self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
            code, extra["Content-Range"] = 206, f"bytes {a}-{b}/{size}"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(b - a + 1))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(a)
            left = b - a + 1
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def _dispatch(self, routes):
        host = urlsplit("//" + self.headers.get("Host", "")).hostname
        if host not in self.hosts:  # DNS rebinding: only answer to our own name
            return self._error(403, "forbidden_host", f"host not allowed: {host}")
        url = urlsplit(self.path)
        for pattern, fn in routes:
            if m := re.fullmatch(pattern, url.path):
                try:
                    return fn(self, *m.groups(), query=parse_qs(url.query))
                except J.ScribeError as e:
                    return self._error(HTTP.get(e.code, 400 if e.exit_code == 2 else 500), e.code, str(e))
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return  # browser dropped the request (normal for <audio> seeking)
                except Exception as e:
                    log.exception("%s %s", self.command, self.path)
                    return self._error(500, "internal", f"{type(e).__name__}: {e}")
        self._error(404, "not_found", url.path)

    def do_GET(self):
        self._dispatch(GET)

    def do_HEAD(self):
        self._dispatch(GET)

    def do_DELETE(self):
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):  # CSRF from another site
            return self._error(403, "forbidden_origin", f"origin not allowed: {origin}")
        self._dispatch(DELETE)

    def do_PUT(self):
        # read the body before any early reply: an unread body makes Windows reset the connection
        size = int(self.headers.get("Content-Length") or 0)
        if size > 1 << 20:
            return self._error(413, "invalid_argument", "body too large")
        self._raw = self.rfile.read(size)
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):  # CSRF from another site
            return self._error(403, "forbidden_origin", f"origin not allowed: {origin}")
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._error(415, "invalid_argument", "Content-Type must be application/json")
        self._dispatch(PUT)

    # --- routes -------------------------------------------------------------
    def static(self, query):
        name, ctype = STATIC[urlsplit(self.path).path]
        self._send(200, (WEB / name).read_bytes(), ctype)

    def jobs(self, query):
        views = [job_view(j) for j in _job_ids()]
        self._json({"home": str(J.HOME), "jobs": sorted(views, key=lambda v: v["created_at"], reverse=True)})

    def job(self, job_id, query):
        self._json(job_view(job_id))

    def events(self, query):
        """SSE: a snapshot of every job, then each job whose files (or liveness) changed."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        stamps, sent, last_write = {}, {}, 0.0
        try:
            while True:
                ids = set(_job_ids())
                for job_id in sorted(ids):
                    stamp = _stamp(job_id)
                    if stamp == stamps.get(job_id) and not (sent.get(job_id) or {}).get("running"):
                        continue
                    stamps[job_id] = stamp
                    try:
                        view = job_view(job_id)
                    except (J.ScribeError, OSError, ValueError):
                        continue
                    if view != sent.get(job_id):
                        sent[job_id] = view
                        self.wfile.write(f"event: job\ndata: {json.dumps(view, ensure_ascii=False)}\n\n".encode())
                        last_write = time.monotonic()
                for job_id in set(sent) - ids:
                    del sent[job_id]
                    stamps.pop(job_id, None)
                    self.wfile.write(f"event: gone\ndata: {json.dumps(job_id)}\n\n".encode())
                if time.monotonic() - last_write > 15:
                    self.wfile.write(b": ping\n\n")  # notices a closed tab, ending this thread
                    last_write = time.monotonic()
                self.wfile.flush()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def speakers(self, job_id, query):
        job = J.load(job_id)
        self._json({"job_id": job_id, "speakers": cli.speakers(job), "diarize_tag": cli.diarize_tag(job)})

    def timeline(self, job_id, query):
        job, d = J.load(job_id), J.job_dir(job_id)
        merged = job["stages"].get("merge", {}).get("status") == "completed"
        self._json({"job_id": job_id, "duration": job.get("duration"),
                    "turns": J.read_json(d / "diarization.json", []),
                    "segments": J.read_json(d / "merged.json", []) if merged else [],  # as transcribed
                    "edits": J.text_edits(job_id) if merged else {},  # hand corrections, keyed by J.seg_key
                    "gaps": cli.untranscribed(job)})

    def peaks(self, job_id, query):
        x = audio.read_wav(self._audio_path(job_id))
        n = max(1, min(int((query.get("n") or ["2000"])[0]), 20000, len(x)))
        k = len(x) // n
        blocks = x[: k * n].reshape(n, k)
        self._json({"n": n, "max": np.round(blocks.max(1), 3).tolist(), "min": np.round(blocks.min(1), 3).tolist()})

    def _audio_path(self, job_id) -> Path:
        if J.load(job_id)["stages"].get("audio", {}).get("status") != "completed":
            raise J.ScribeError("not_processed", f"job {job_id} has no audio yet", 1)
        return J.job_dir(job_id) / "audio.wav"

    def audio(self, job_id, query):
        self._file(self._audio_path(job_id), "audio/wav")

    def export(self, job_id, query):
        """Download only: not recorded in job.json (the server cannot rewrite a file it did not place)."""
        fmt = (query.get("format") or ["markdown"])[0]
        if fmt not in EXPORTERS:
            raise J.ScribeError("invalid_argument", f"format must be one of {sorted(EXPORTERS)}", 2)
        job = J.load(job_id)
        if job["stages"].get("merge", {}).get("status") != "completed":
            raise J.ScribeError("not_processed", f"job {job_id} has no merged transcript yet", 1)
        name = f"{Path(job['input']).stem}.{EXT.get(fmt, fmt)}"
        self._send(200, cli.render(job, fmt).encode(), "application/octet-stream",
                   {"Content-Disposition": f"attachment; filename=\"transcript.{EXT.get(fmt, fmt)}\"; "
                                           f"filename*=UTF-8''{quote(name)}"})

    def _body(self) -> dict:
        try:
            body = json.loads(self._raw or b"{}")
        except ValueError:
            raise J.ScribeError("invalid_argument", "body is not JSON", 2)
        if not isinstance(body, dict):
            raise J.ScribeError("invalid_argument", "body must be a JSON object", 2)
        return body

    def put_control(self, job_id, query):
        """{"paused": bool} pauses (drops the running stage's partial work) or resumes; {"first": true} makes
        this job run next. Only for a job whose `scribe process` is alive (running or waiting)."""
        body = self._body()
        if "paused" in body and not isinstance(body["paused"], bool):
            raise J.ScribeError("invalid_argument", '"paused" must be true or false', 2)
        view = job_view(job_id)
        if not (view["running"] or view.get("queue")):
            raise J.ScribeError("not_running", f"no scribe process is working on {job_id}; "
                                f"start it with: scribe process --job {job_id}", 1)
        if "paused" in body:
            J.set_control(job_id, paused=body["paused"])
        if body.get("first"):
            orders = [J.control(p.parent.name).get("order", 0) for p in (J.HOME / "jobs").glob("*/control.json")]
            J.set_control(job_id, order=min(orders, default=0) - 1)
        self._json(job_view(job_id))

    def delete_job(self, job_id, query):
        """Right-click → 削除: the job folder only, and only while no scribe process works on it."""
        J.load(job_id)
        if J.is_locked(job_id) or J.queue_state(job_id):
            raise J.ScribeError("job_busy", f"job {job_id} has a scribe process (running, queued or paused); wait until it ends", 1)
        J.delete(job_id)
        self._json({"job_id": job_id, "deleted": True})

    def put_speakers(self, job_id, query):
        """Ctrl+S in the editor: {"names": {SPEAKER_xx: name|null}, "texts": {seg_key: text}} (either may be absent)."""
        body = self._body()
        names, texts = body.get("names") or {}, body.get("texts") or {}
        if not (isinstance(names, dict) and all(isinstance(v, str | None) for v in names.values())
                and isinstance(texts, dict) and all(isinstance(v, str) for v in texts.values())):
            raise J.ScribeError("invalid_argument",
                                'body must be {"names": {"SPEAKER_00": "name" | null}, "texts": {"1200-3400": "text"}}', 2)
        self._json(cli.apply_edits(job_id, names, texts, self.headers.get("If-Match")))


JOB = r"/api/jobs/([^/]+)"
GET = [(r"/|/app\.js|/style\.css", Handler.static), (r"/api/jobs", Handler.jobs), (r"/api/events", Handler.events),
       (JOB, Handler.job), (JOB + "/speakers", Handler.speakers), (JOB + "/timeline", Handler.timeline),
       (JOB + "/peaks", Handler.peaks), (JOB + "/audio", Handler.audio), (JOB + "/export", Handler.export)]
PUT = [(JOB + "/speakers", Handler.put_speakers), (JOB + "/control", Handler.put_control)]
DELETE = [(JOB, Handler.delete_job)]


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    Handler.hosts = {"127.0.0.1", "localhost", host}

    class Server(ThreadingHTTPServer):
        daemon_threads = True  # SSE threads must not block shutdown
        allow_reuse_address = os.name != "nt"  # on Windows it would let two servers share the port

    return Server((host, port), Handler)


def serve(host: str, port: int, ready) -> None:
    if host not in ("127.0.0.1", "localhost"):
        log.warning("serving on %s: anyone who can reach it can read transcripts and rename speakers", host)
    try:
        srv = make_server(host, port)
    except OSError as e:
        raise J.ScribeError("port_in_use", f"cannot listen on {host}:{port}: {e}", 1)
    url = f"http://{host}:{srv.server_address[1]}/"
    log.info("serving %s (Ctrl+C to stop)", url)
    ready(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()

"""Job directory, atomic JSON I/O, per-job lock, derived status, run queue (runner slot + control.json)."""

import contextlib
import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ.get("SCRIBE_HOME") or Path.home() / ".scribe")
STAGES = ["audio", "diarize", "transcribe", "fill", "merge", "samples"]
# status reported once each stage completes (元仕様の状態名)
DONE_STATUS = {"audio": "audio_extracted", "diarize": "diarized", "transcribe": "transcribed", "merge": "merged"}
WAIT_FRESH = 5  # s: a process waiting for its turn rewrites progress.json every second


class ScribeError(Exception):
    """Error with an exit code and a machine-readable code for the Agent."""

    def __init__(self, code: str, message: str, exit_code: int = 1):
        super().__init__(message)
        self.code, self.exit_code = code, exit_code


def job_dir(job_id: str) -> Path:
    if not job_id or any(c in job_id for c in "/\\.:"):
        raise ScribeError("invalid_job_id", f"invalid job id: {job_id!r}", 2)
    return HOME / "jobs" / job_id


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for i in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: a reader (e.g. `scribe serve`) has the target open
            if i == 39:
                raise
            time.sleep(0.05)


def create(input_path: str, job_id: str | None = None) -> dict:
    job_id = job_id or uuid.uuid4().hex[:8]
    d = job_dir(job_id)
    if (d / "job.json").exists():
        raise ScribeError("job_exists", f"job already exists: {job_id}", 2)
    d.mkdir(parents=True)
    job = {"job_id": job_id, "input": str(Path(input_path).resolve()),
           "created_at": datetime.now(timezone.utc).isoformat(), "stages": {}}
    write_json(d / "job.json", job)
    return job


def load(job_id: str) -> dict:
    job = read_json(job_dir(job_id) / "job.json")
    if job is None:
        raise ScribeError("job_not_found", f"job not found: {job_id}", 2)
    st = job["stages"]
    if st.get("merge", {}).get("status") == "completed" and "fill" not in st:
        st["fill"] = {"status": "completed", "key": None}  # merged before gap fill existed; reruns on next process
    for fmt, entries in job.get("exports", {}).items():
        if isinstance(entries, dict):  # jobs saved before multiple output paths were supported
            job["exports"][fmt] = [entries]
    return job


def save(job: dict) -> None:
    write_json(job_dir(job["job_id"]) / "job.json", job)


def speaker_ids(job_id: str) -> list[str]:
    segs = read_json(job_dir(job_id) / "diarization.json", [])
    return sorted({s["speaker_id"] for s in segs}, key=lambda s: int(s.rsplit("_", 1)[1]))


def speaker_names(job_id: str) -> dict:
    return read_json(job_dir(job_id) / "speakers.json", {})


def seg_key(seg: dict) -> str:
    """Stable id of a merged line: its start-end in ms (unchanged as long as merge's inputs are)."""
    return f"{round(seg['start'] * 1000)}-{round(seg['end'] * 1000)}"


def text_edits(job_id: str) -> dict:
    """Hand corrections of transcript lines: {seg_key: text}; "" removes the line from exports."""
    return read_json(job_dir(job_id) / "edits.json", {})


def segments(job_id: str) -> list[dict]:
    """merged.json with the hand corrections applied: what every export, sample text and check sees."""
    edits, out = text_edits(job_id), []
    for s in read_json(job_dir(job_id) / "merged.json", []):
        text = edits.get(seg_key(s), s["text"])
        if text:
            out.append({**s, "text": text})
    return out


def status(job: dict, locked: bool | None = None) -> str:
    """locked: pass True when the caller already knows the job's process is alive (skips the lock probe)."""
    stages = job["stages"]
    if any(s.get("status") == "failed" for s in stages.values()):
        return "failed"
    if (any(s.get("status") == "running" for s in stages.values())
            and not (is_locked(job["job_id"]) if locked is None else locked)):
        return "failed"  # process died mid-stage
    status = "created"
    for name in STAGES:
        if stages.get(name, {}).get("status") != "completed":
            return status
        status = DONE_STATUS.get(name, status)
    names = speaker_names(job["job_id"])
    if any(not names.get(s) for s in speaker_ids(job["job_id"])):
        return "speaker_identification_required"
    # exported = at least one export since the transcript/names last changed
    return "exported" if job.get("exports") else "ready"


def _try_lock(path: Path):
    """Non-blocking OS-level exclusive lock: the open file (close it to release) or None if held.
    Released automatically if the process dies."""
    f = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


@contextlib.contextmanager
def lock(job_id: str):
    """Per-job lock, held by whoever modifies the job (a whole `process` run, a rename, an export)."""
    try:
        f = _try_lock(job_dir(job_id) / ".lock")
    except FileNotFoundError:
        raise ScribeError("job_not_found", f"job not found: {job_id}", 2)
    if f is None:
        raise ScribeError("job_busy", f"job {job_id} is being modified by another process", 1)
    try:
        yield
    finally:
        f.close()


def delete(job_id: str) -> None:
    """Remove a job's folder for good (audio copy, results, names, corrections). Exported files and the
    input file live elsewhere and stay. The caller checks no process works on the job."""
    trash = HOME / ".deleted"  # outside jobs/: never listed, even if removal below is incomplete
    trash.mkdir(parents=True, exist_ok=True)
    try:
        # atomic, and on Windows it fails while any file inside is open (e.g. audio being played)
        os.replace(job_dir(job_id), trash / f"{job_id}-{uuid.uuid4().hex[:6]}")
    except FileNotFoundError:
        raise ScribeError("job_not_found", f"job not found: {job_id}", 2)
    except PermissionError:
        raise ScribeError("job_busy", f"files of job {job_id} are in use (audio playing?); close them and retry", 1)
    for d in trash.iterdir():  # also retries leftovers of earlier deletions
        shutil.rmtree(d, ignore_errors=True)


def try_runner():
    """The machine-wide runner slot: one job runs a stage at a time (they share one GPU / CPU)."""
    (HOME / "jobs").mkdir(parents=True, exist_ok=True)
    return _try_lock(HOME / "runner.lock")


def control(job_id: str) -> dict:
    """What the user asked for this job's process: {"paused": bool, "order": float (lower runs first)}."""
    try:
        return read_json(job_dir(job_id) / "control.json", {}) or {}
    except ValueError:
        return {}


def set_control(job_id: str, **changes) -> dict:
    c = {**control(job_id), **changes}
    write_json(job_dir(job_id) / "control.json", c)
    return c


def waiting() -> list[dict]:
    """Jobs whose process is alive and waiting for the runner, in run order: [{job_id, state, order}]."""
    out, now = [], datetime.now(timezone.utc)
    for f in (HOME / "jobs").glob("*/progress.json"):
        try:
            p = read_json(f) or {}
            fresh = (now - datetime.fromisoformat(p["updated_at"])).total_seconds() < WAIT_FRESH
        except (OSError, ValueError, KeyError):
            continue
        if fresh and p.get("state") in ("queued", "paused"):
            out.append({"job_id": f.parent.name, "state": p["state"], "order": control(f.parent.name).get("order", 0)})
    return sorted(out, key=lambda w: (w["order"], w["job_id"]))


def queue_state(job_id: str) -> dict | None:
    """{"state": "queued", "position": n (1 = next)} / {"state": "paused"} while the process waits, else None."""
    line = waiting()
    me = next((w for w in line if w["job_id"] == job_id), None)
    if not me:
        return None
    if me["state"] == "paused":
        return {"state": "paused"}
    return {"state": "queued", "position": [w["job_id"] for w in line if w["state"] == "queued"].index(job_id) + 1}


def is_locked(job_id: str) -> bool:
    try:
        with lock(job_id):
            return False
    except ScribeError:
        return True

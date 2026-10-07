"""Scribe CLI. stdout = results only, stderr = logs. Exit: 0 ok, 1 error, 2 bad args, 3 processing failed, 4 user input required."""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import job as J
from . import pipeline
from .export import EXPORTERS, build_doc

log = logging.getLogger("scribe")


def _speakers(job: dict) -> list[dict]:
    if job["stages"].get("diarize", {}).get("status") != "completed":
        return []
    d = J.job_dir(job["job_id"])
    names = J.speaker_names(job["job_id"])
    spans = (J.read_json(d / "samples" / "samples.json", {})
             if job["stages"].get("samples", {}).get("status") == "completed" else {})
    talk = {}
    for s in J.read_json(d / "diarization.json", []):
        talk[s["speaker_id"]] = talk.get(s["speaker_id"], 0) + s["end"] - s["start"]
    merged = (J.read_json(d / "merged.json", [])
              if job["stages"].get("merge", {}).get("status") == "completed" else [])

    def said(spk, span):  # what the speaker says inside the sample (for whoever can't listen)
        return "".join(m["text"] for m in merged if m["speaker_id"] == spk
                       and m["end"] > span[0] and m["start"] < span[1]) if span else None

    return [{"speaker_id": s, "name": names.get(s), "speech_seconds": round(talk[s], 1),
             "sample_audio": str(d / "samples" / f"{s}.wav") if s in spans else None,
             "sample_span": spans.get(s), "sample_text": said(s, spans.get(s))}
            for s in J.speaker_ids(job["job_id"])]


def _summary(job: dict) -> dict:
    out = {"job_id": job["job_id"], "status": J.status(job), "input": job["input"],
           "duration": job.get("duration"), "options": job.get("options"),
           "stages": {n: {k: v for k, v in s.items() if k != "key"} for n, s in job["stages"].items()},
           "exports": job.get("exports", {})}
    if out["status"] == "speaker_identification_required":
        out["speakers"] = [s for s in _speakers(job) if not s["name"]]
    return out


def cmd_process(a):
    if not a.input and not a.job:
        raise J.ScribeError("invalid_argument", "give an input file or --job to resume", 2)
    if a.job and (J.job_dir(a.job) / "job.json").exists():
        job_id = a.job
    elif a.input:
        job_id = J.create(a.input, a.job)["job_id"]
    else:
        raise J.ScribeError("job_not_found", f"job not found: {a.job}", 2)
    given = {k: getattr(a, k) for k in pipeline.DEFAULTS}
    with J.lock(job_id):
        job = J.load(job_id)
        if a.input and Path(a.input).resolve() != Path(job["input"]):
            raise J.ScribeError("input_mismatch", f"job {a.job} was created for {job['input']}", 2)
        opts = pipeline.resolve_opts(job, given)
        J.save(job)
        try:
            pipeline.run(job, opts)
        except J.ScribeError:
            raise
        except Exception as e:
            log.exception("processing failed")
            raise J.ScribeError("processing_failed", f"{type(e).__name__}: {e}", 3)
        out = _summary(job)
    return out, 4 if out["status"] == "speaker_identification_required" else 0


def cmd_jobs(a):
    jobs = [j for f in (J.HOME / "jobs").glob("*/job.json") if (j := J.read_json(f))]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return {"home": str(J.HOME), "jobs": [
        {"job_id": j["job_id"], "status": J.status(j), "created_at": datetime.fromisoformat(j["created_at"]).astimezone().strftime("%Y-%m-%d %H:%M"),
         "duration": round(j["duration"]) if j.get("duration") else None, "input": j["input"]}
        for j in jobs]}, 0


def cmd_status(a):
    return _summary(J.load(a.job)), 0


def cmd_speakers(a):
    job = J.load(a.job)
    return {"job_id": job["job_id"], "status": J.status(job), "speakers": _speakers(job)}, 0


def _render(job: dict, fmt: str) -> str:
    d = J.job_dir(job["job_id"])
    doc = build_doc(job["job_id"], job.get("duration"), J.read_json(d / "merged.json"), J.speaker_names(job["job_id"]))
    return EXPORTERS[fmt](doc)


def _apply_names(job_id: str, updates: dict) -> dict:
    """Save speaker names, then rewrite every file exported earlier so it carries the new names."""
    with J.lock(job_id):
        job = J.load(job_id)
        if job["stages"].get("diarize", {}).get("status") != "completed":
            raise J.ScribeError("not_processed", f"job {job_id} has no completed diarization yet", 1)
        ids = J.speaker_ids(job_id)
        if bad := [s for s in updates if s not in ids]:
            raise J.ScribeError("unknown_speaker", f"{bad} not in {ids}", 2)
        names = J.speaker_names(job_id)
        names.update({s: (n or "").strip() or None for s, n in updates.items()})
        J.write_json(J.job_dir(job_id) / "speakers.json", names)
        refreshed = []
        for fmt, entries in list(job.get("exports", {}).items()):
            for e in list(entries):
                try:
                    if not e.get("path"):
                        raise OSError("printed to stdout")  # nothing on disk to update
                    Path(e["path"]).write_text(_render(job, fmt), encoding="utf-8")
                    e["at"] = datetime.now(timezone.utc).isoformat()
                    refreshed.append(e["path"])
                except OSError:
                    entries.remove(e)  # not refreshed -> no longer counts as exported
            if not entries:
                del job["exports"][fmt]
        J.save(job)
    return {"job_id": job_id, "status": J.status(job), "names": {s: names.get(s) for s in ids},
            "refreshed": refreshed}


def cmd_speaker_set(a):
    return _apply_names(a.job, {a.speaker: a.name}), 0


def cmd_speaker_rename(a):
    pairs = [p.split("=", 1) for p in a.pairs]
    if any(len(p) != 2 for p in pairs):
        raise J.ScribeError("invalid_argument", "use SPEAKER_00=名前 pairs", 2)
    return _apply_names(a.job, dict(pairs)), 0


def _play(path: str) -> None:
    try:
        if os.name == "nt":
            os.startfile(path)  # default player
        else:
            cmd = ["afplay", path] if sys.platform == "darwin" else ["xdg-open", path]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        log.warning("cannot play %s: %s", path, e)


def cmd_speaker_edit(a):
    """Interactive: listen to each sample, type the name. Prompts go to stderr, result JSON to stdout."""
    job = J.load(a.job)
    if not sys.stdin.isatty():
        raise J.ScribeError("not_interactive", "needs a terminal; use `speaker rename` instead", 2)
    say = lambda t: print(t, file=sys.stderr)  # noqa: E731
    say("Enter=そのまま  -=名前を消す  p=もう一度再生")
    updates = {}
    for s in sorted(_speakers(job), key=lambda s: -s["speech_seconds"]):
        say(f"\n{s['speaker_id']}  発話 {s['speech_seconds']}s  現在: {s['name'] or '(未設定)'}")
        say(f"  「{s['sample_text'] or ''}」")
        while True:
            if a.play and s["sample_audio"]:
                _play(s["sample_audio"])
            sys.stderr.write(f"  名前 [{s['name'] or ''}]: ")
            sys.stderr.flush()
            ans = input().strip()
            if ans != "p":
                break
        if ans == "-":
            updates[s["speaker_id"]] = None
        elif ans:
            updates[s["speaker_id"]] = ans
    return _apply_names(a.job, updates), 0


def cmd_export(a):
    with J.lock(a.job):
        job = J.load(a.job)
        if job["stages"].get("merge", {}).get("status") != "completed":
            raise J.ScribeError("not_processed", f"job {a.job} has no merged transcript yet (status: {J.status(job)})", 1)
        text = _render(job, a.format)
        path = str(Path(a.output).resolve()) if a.output else None
        if a.output:
            Path(path).write_text(text, encoding="utf-8")
        exports = job.setdefault("exports", {})
        for fmt, entries in list(exports.items()):
            if path is not None or fmt == a.format:
                entries[:] = [e for e in entries if e.get("path") != path]
                if not entries:
                    del exports[fmt]
        exports.setdefault(a.format, []).append({"path": path, "at": datetime.now(timezone.utc).isoformat()})
        J.save(job)
    if a.output:
        return {"job_id": a.job, "format": a.format, "path": path}, 0
    sys.stdout.write(text)  # transcript itself is the result
    return None, 0


def _parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    p = argparse.ArgumentParser(prog="scribe", description="Meeting transcription and speaker diarization CLI.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("process", parents=[common], help="Process audio or video (or resume with --job)")
    s.add_argument("input", nargs="?")
    s.add_argument("--job", help="job id to resume (or to assign to a new job)")
    s.add_argument("--diarizer", help=f"fallback chain, e.g. pyannote,sherpa (default {pipeline.DEFAULTS['diarizer']})")
    s.add_argument("--asr", help="fallback chain (default faster-whisper)")
    s.add_argument("--model", help="ASR model size/name (default small)")
    s.add_argument("--language", help="default ja")
    s.add_argument("--device", help="auto|cpu|cuda (default auto: cuda then cpu)")
    s.add_argument("--num-speakers", dest="num_speakers", type=int, help="known speaker count")
    s.add_argument("--cluster-threshold", dest="cluster_threshold", type=float,
                   help="sherpa clustering distance; higher = fewer speakers (default 1.0)")
    s.add_argument("--stage-timeout", dest="stage_timeout", type=int, help="seconds per backend attempt")
    s.set_defaults(fn=cmd_process)

    s = sub.add_parser("jobs", parents=[common], help="List jobs (newest first)")
    s.set_defaults(fn=cmd_jobs)

    s = sub.add_parser("status", parents=[common], help="Show job status")
    s.add_argument("job")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("speakers", parents=[common], help="Show detected speakers")
    s.add_argument("job")
    s.set_defaults(fn=cmd_speakers)

    s = sub.add_parser("speaker", help="Manage speaker information")
    ss = s.add_subparsers(dest="action", required=True)
    s = ss.add_parser("edit", parents=[common], help="Interactively name speakers while listening to samples")
    s.add_argument("--job", required=True)
    s.add_argument("--no-play", dest="play", action="store_false", help="do not open sample audio")
    s.set_defaults(fn=cmd_speaker_edit)
    s = ss.add_parser("rename", parents=[common], help="Set several names: SPEAKER_00=田中 SPEAKER_01=佐藤")
    s.add_argument("--job", required=True)
    s.add_argument("pairs", nargs="+", metavar="SPEAKER_XX=名前")
    s.set_defaults(fn=cmd_speaker_rename)
    s = ss.add_parser("set", parents=[common], help="Name a speaker")
    s.add_argument("--job", required=True)
    s.add_argument("--speaker", required=True)
    s.add_argument("--name", required=True)
    s.set_defaults(fn=cmd_speaker_set)

    s = sub.add_parser("export", parents=[common], help="Export transcript")
    s.add_argument("job")
    s.add_argument("--format", choices=sorted(EXPORTERS), default="markdown")
    s.add_argument("-o", "--output", help="write to file instead of stdout")
    s.set_defaults(fn=cmd_export)
    return p


def _print(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    for k, v in result.items():
        if k in ("stages", "options"):
            continue
        if isinstance(v, list):
            print(f"{k}:")
            for item in v:
                print("  " + "  ".join(f"{ik}={iv}" for ik, iv in item.items()))
        else:
            print(f"{k}: {v}")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # Windows consoles default to cp932
    logging.basicConfig(level=logging.INFO, format="scribe: %(message)s", stream=sys.stderr)
    a = _parser().parse_args(argv)  # bad args -> exit 2
    try:
        result, code = a.fn(a)
    except J.ScribeError as e:
        log.error("%s", e)
        result, code = {"error": e.code, "message": str(e)}, e.exit_code
    if result is not None:
        _print(result, a.json)
    return code


if __name__ == "__main__":
    sys.exit(main())

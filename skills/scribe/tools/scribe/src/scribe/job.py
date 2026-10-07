"""Job directory, atomic JSON I/O, per-job lock, derived status."""

import contextlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ.get("SCRIBE_HOME") or Path.home() / ".scribe")
STAGES = ["audio", "diarize", "transcribe", "merge", "samples"]
# status reported once each stage completes (元仕様の状態名)
DONE_STATUS = {"audio": "audio_extracted", "diarize": "diarized", "transcribe": "transcribed", "merge": "merged"}


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
    os.replace(tmp, path)


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


def status(job: dict) -> str:
    stages = job["stages"]
    if any(s.get("status") == "failed" for s in stages.values()):
        return "failed"
    if any(s.get("status") == "running" for s in stages.values()) and not is_locked(job["job_id"]):
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


@contextlib.contextmanager
def lock(job_id: str):
    """OS-level exclusive lock; released automatically if the process dies."""
    try:
        f = open(job_dir(job_id) / ".lock", "a+b")
    except FileNotFoundError:
        raise ScribeError("job_not_found", f"job not found: {job_id}", 2)
    try:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ScribeError("job_busy", f"job {job_id} is being modified by another process", 1)
        yield
    finally:
        f.close()


def is_locked(job_id: str) -> bool:
    try:
        with lock(job_id):
            return False
    except ScribeError:
        return True

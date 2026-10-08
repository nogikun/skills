"""Stage runner: cache keys, backend fallback chain, child processes with timeout, run queue."""

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import audio, backends, inputs, merge, progress
from . import job as J
from .job import STAGES, ScribeError, job_dir, read_json, save, write_json

log = logging.getLogger("scribe")
# ponytail: cluster_threshold calibrated on 2 recordings only (49-min meeting: 0.5->135 speakers,
# 1.0->14, 1.1->8; 2-voice TTS: <=1.0 -> 2, >=1.05 -> 1). Errs on splitting: an over-split speaker is
# fixed by giving two IDs the same name, two people merged into one cannot be fixed. Re-sweep with more data.
DEFAULTS = {"diarizer": "nemotron,pyannote,sherpa", "asr": "faster-whisper", "model": "small", "language": "ja",
            "device": "auto", "num_speakers": None, "cluster_threshold": 1.0, "stage_timeout": None}


class Paused(Exception):
    """The user paused this job (web UI) while a stage was running; the stage's partial work is dropped."""


class Runner:
    """Holds the machine-wide runner slot across this job's stages. At each stage boundary it gives the
    slot up if the user paused this job or put another waiting job ahead of it, then waits for its turn."""

    def __init__(self, job_id: str):
        self.job_id, self.slot = job_id, None

    def turn(self) -> None:
        if self.slot and not self._must_yield():
            return
        self.close()
        said, d = None, job_dir(self.job_id)
        while True:
            paused = J.control(self.job_id).get("paused")
            progress.context(d / "progress.json", state="paused" if paused else "queued", stage=None, attempt=None)
            if not paused:
                line = [w for w in J.waiting() if w["state"] == "queued"]
                if (not line or line[0]["job_id"] == self.job_id) and (slot := J.try_runner()):
                    self.slot = slot
                    return
            msg = "paused: waiting to be resumed in the web UI" if paused else "queued: waiting for other jobs"
            if msg != said:
                log.info(msg)
                said = msg
            time.sleep(1)

    def _must_yield(self) -> bool:
        if J.control(self.job_id).get("paused"):
            return True
        line = [w for w in J.waiting() if w["state"] == "queued"]
        return bool(line) and line[0]["order"] < J.control(self.job_id).get("order", 0)

    def close(self) -> None:
        if self.slot:
            self.slot.close()
            self.slot = None


def _stage(job, name, key, outputs, fn, runner: Runner | None = None) -> bool:
    """Run fn unless a completed result with the same key exists. Returns True if it ran."""
    st = job["stages"].get(name, {})
    step = f"[{STAGES.index(name) + 1}/{len(STAGES)}] {name}"
    if st.get("status") == "completed" and st.get("key") == key and all(p.exists() for p in outputs):
        log.info("%s: cached", step)
        return False
    while True:
        if runner:
            runner.turn()
        try:
            return _attempt(job, name, key, fn, step)
        except Paused:
            job["stages"].pop(name, None)  # partial work is gone; resume reruns the stage from its start
            save(job)
            log.info("%s: paused, partial work discarded", step)


def _attempt(job, name, key, fn, step) -> bool:
    log.info("%s: start", step)
    affected = [name, *{"audio": ["diarize", "transcribe", "fill", "merge", "samples"],
                       "diarize": ["fill", "merge", "samples"], "transcribe": ["fill", "merge"],
                       "fill": ["merge"]}.get(name, [])]
    for downstream in affected[1:]:
        job["stages"].pop(downstream, None)
    if "merge" in affected:
        job.pop("exports", None)
    job["stages"][name] = {"status": "running", "key": key}
    progress.context(job_dir(job["job_id"]) / "progress.json", stage=name, attempt=None, state="running",
                     started_at=datetime.now(timezone.utc).isoformat())
    save(job)
    t = time.monotonic()
    try:
        d = job_dir(job["job_id"])
        if "diarize" in affected:
            (d / "speakers.json").unlink(missing_ok=True)
        artifacts = {"audio": "audio.wav", "diarize": "diarization.json", "transcribe": "transcript.json",
                     "fill": "fill.json", "merge": "merged.json", "samples": "samples"}
        for stage in affected:
            path = d / artifacts[stage]
            if stage == "samples":
                if path.exists():
                    shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        extra = fn() or {}
    except Paused:
        raise
    except Exception as e:
        job["stages"][name].update(status="failed", error=str(e))
        save(job)
        raise
    job["stages"][name].update(status="completed", secs=round(time.monotonic() - t, 1), **extra)
    job["stages"][name].pop("error", None)
    save(job)
    log.info("%s: done in %.1fs%s", step, job["stages"][name]["secs"],
             f" ({extra['backend']})" if "backend" in extra else "")
    return True


def _run_chain(kind, chain, wav, out: Path, opts, timeout):
    """Try each backend (and device) in order in a fresh child process; first success wins."""
    attempts = []
    for name in chain:
        devices = ["cuda", "cpu"] if opts["device"] == "auto" else [opts["device"]]
        for dev in devices:
            if dev == "cuda" and name in backends.CPU_ONLY:
                continue
            err = Path(str(out) + ".err")
            err.unlink(missing_ok=True)
            log.info("%s: trying %s (%s)", kind, name, dev)
            progress.context(attempt=f"{name}/{dev}")
            cmd = [sys.executable, "-m", "scribe.backends", kind, name, str(wav), str(out),
                   json.dumps({**opts, "device": dev})]
            # stdout=2: child chatter goes to our stderr, keeping stdout clean for results
            p, end = subprocess.Popen(cmd, stdout=2), time.monotonic() + timeout
            while (rc := p.poll()) is None:
                paused = J.read_json(out.parent / "control.json", {}).get("paused")  # out lives in the job dir
                if paused or time.monotonic() > end:
                    p.kill()
                    p.wait()
                    if paused:
                        raise Paused()
                    break
                time.sleep(0.5)
            if rc is None:
                rc, reason = -1, f"timeout after {timeout}s"
            else:
                reason = err.read_text(encoding="utf-8") if err.exists() else f"crashed (exit {rc})"
            if rc == 0:
                return {"backend": f"{name}/{dev}", "attempts": attempts}
            log.warning("%s: %s (%s) %s", kind, name, dev, reason)
            attempts.append(f"{name}/{dev}: {reason}")
    code = "model_not_cached" if any("model_not_cached" in a for a in attempts) else "backend_failed"
    raise ScribeError(code, f"all {kind} backends failed: " + " | ".join(attempts), 3)


def run(job: dict, opts: dict) -> None:
    runner = Runner(job["job_id"])
    try:
        _run(job, opts, runner)
    finally:
        runner.close()
        progress.clear()


def _run(job: dict, opts: dict, runner: Runner) -> None:
    d = job_dir(job["job_id"])
    wav, diar, tr, fill, merged, samples = (d / "audio.wav", d / "diarization.json", d / "transcript.json",
                                            d / "fill.json", d / "merged.json", d / "samples")
    src = Path(job["input"])
    if not src.exists():
        raise ScribeError("input_not_found", f"input not found: {src}", 2)

    k_audio = {"input": str(src), "size": src.stat().st_size, "mtime": src.stat().st_mtime}

    def do_audio():  # any extension -> 16 kHz mono WAV (see inputs.py)
        info = inputs.to_wav(str(src), str(wav))
        job["duration"] = info.pop("duration")
        return info  # format / kind / converter, kept in stages.audio

    _stage(job, "audio", k_audio, [wav], do_audio, runner)
    timeout = opts["stage_timeout"] or max(600, 4 * job.get("duration", 0))

    dchain = opts["diarizer"].split(",")
    k_diar = {"up": k_audio, "chain": dchain, "device": opts["device"], "num_speakers": opts["num_speakers"],
              "cluster_threshold": opts["cluster_threshold"],
              "versions": backends.versions(dchain)}

    _stage(job, "diarize", k_diar, [diar], lambda: _run_chain("diarize", dchain, wav, diar, opts, timeout), runner)

    achain = opts["asr"].split(",")
    k_asr = {"up": k_audio, "chain": achain, "device": opts["device"], "model": opts["model"], "language": opts["language"],
             "versions": backends.versions(achain)}
    _stage(job, "transcribe", k_asr, [tr], lambda: _run_chain("transcribe", achain, wav, tr, opts, timeout), runner)

    def do_fill():  # re-transcribe diarized speech the full pass left without text
        gaps = merge.uncovered(read_json(diar), read_json(tr))
        spans = merge.clips(gaps, job.get("duration"))
        if not spans:
            write_json(fill, [])
            return {"clips": 0}
        extra = _run_chain("fill", achain, wav, fill, {**opts, "clips": spans}, timeout)
        return {**extra, "clips": len(spans), "gap_seconds": round(sum(g["end"] - g["start"] for g in gaps), 1)}

    k_fill = {"diar": k_diar, "asr": k_asr, "min_gap": merge.MIN_GAP, "pad": merge.CLIP_PAD}
    _stage(job, "fill", k_fill, [fill], do_fill, runner)

    k_merge = {"fill": k_fill, "max_gap": merge.MAX_GAP}
    _stage(job, "merge", k_merge, [merged], lambda: write_json(merged, merge.merge(
        sorted(read_json(tr) + read_json(fill), key=lambda s: s["start"]), read_json(diar))), runner)

    def do_samples():
        samples.mkdir()
        x = audio.read_wav(wav)
        spans = audio.pick_samples(x, read_json(diar))
        for spk, (a, b) in spans.items():
            audio.write_wav(samples / f"{spk}.wav", x[int(a * audio.SR):int(b * audio.SR)])
        write_json(samples / "samples.json", spans)

    k_samples = {"diar": k_diar, "params": [audio.SAMPLE_MIN, audio.SAMPLE_MAX, audio.JOIN_GAP]}
    sample_outputs = [samples / "samples.json", *(samples / f"{spk}.wav"
                      for spk in {s["speaker_id"] for s in read_json(diar)})]
    _stage(job, "samples", k_samples, sample_outputs, do_samples, runner)


def resolve_opts(job: dict, given: dict) -> dict:
    """Defaults < options saved on the job < options given now; validates backend names."""
    opts = {**DEFAULTS, **job.get("options", {}), **{k: v for k, v in given.items() if v is not None}}
    for kind, key in (("diarize", "diarizer"), ("transcribe", "asr")):
        bad = [n for n in opts[key].split(",") if n not in backends.REGISTRY[kind]]
        if bad:
            raise ScribeError("invalid_backend", f"unknown {key}: {bad}; choose from "
                              f"{sorted(backends.REGISTRY[kind])}", 2)
    if opts["device"] not in ("auto", "cpu", "cuda"):
        raise ScribeError("invalid_argument", "device must be auto|cpu|cuda", 2)
    job["options"] = opts
    return opts


os.environ.setdefault("PYTHONIOENCODING", "utf-8")  # inherited by backend children on Windows

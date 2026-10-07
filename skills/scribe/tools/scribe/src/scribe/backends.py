"""Backends: name -> fn(wav_path, opts) -> list[dict], plus the child-process entry point.

Adding a backend = one function + one dict entry. Raise Unavailable when it cannot run
here (not installed, no token, no GPU) so the pipeline moves to the next one quickly.

Child usage: python -m scribe.backends <diarize|transcribe> <name> <wav> <out.json> <opts-json>
exit 0 = ok, 10 = unavailable, 11 = failed (reason in <out.json>.err).
"""

import contextlib
import json
import logging
import os
import sys
from importlib import metadata
from pathlib import Path

from .audio import SR, read_wav
from .job import write_json
from .progress import bar, heartbeat

log = logging.getLogger("scribe")
SCHEMA_VERSION = 1
# All models live in the standard Hugging Face cache (HF_HOME, default ~/.cache/huggingface),
# so every copy of this tool on the machine shares one download. HF_HUB_OFFLINE=1 works as usual.
# Pinned revisions: every copy resolves the same files, and they are part of the cache key.
PYANNOTE = ("pyannote/speaker-diarization-community-1", "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee")
NEMOTRON = ("nvidia/Nemotron-3-Diarization", "f667ed73aee57d40cc39428eb768b4fd87a0a29e")
SHERPA_SEG = ("csukuangfj/sherpa-onnx-pyannote-segmentation-3-0", "model.onnx",
              "9403a6902bb58e3d5ae8c7e77c3422de279db2e0")
SHERPA_EMB = ("csukuangfj/speaker-embedding-models",
              os.environ.get("SCRIBE_SHERPA_EMBEDDING", "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"),
              "0743f301363dec56491a490f6d6cbc9d67f9a3bf")
# ponytail: only the default size is pinned; other --model values follow the repo's main
WHISPER_REVISIONS = {"small": "536b0662742c02347bc0e980a01041f333bce120"}
THREADS = min(4, os.cpu_count() or 1)


class Unavailable(Exception):
    pass


def _hf_file(spec) -> str:
    from huggingface_hub import hf_hub_download
    repo, filename, revision = spec
    try:
        return hf_hub_download(repo, filename, revision=revision)  # cached + file-locked across processes
    except Exception as e:
        tag = "model_not_cached" if os.environ.get("HF_HUB_OFFLINE") else "model download failed"
        raise Unavailable(f"{tag}: {repo}/{filename}@{revision[:8]}: {type(e).__name__}")


# --- diarization ------------------------------------------------------------

def diarize_pyannote(wav, opts):
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise Unavailable("HF_TOKEN not set (accept pyannote/speaker-diarization-community-1 terms on HF)")
    try:
        import torch
        from pyannote.audio import Pipeline
    except ImportError:
        raise Unavailable("pyannote not installed: uv sync --extra pyannote")
    if opts["device"] == "cuda" and not torch.cuda.is_available():
        raise Unavailable("no CUDA")
    pipe = Pipeline.from_pretrained(PYANNOTE[0], revision=PYANNOTE[1], token=token)
    pipe.to(torch.device(opts["device"]))
    n = opts.get("num_speakers")
    # in-memory waveform: avoids torchcodec/FFmpeg DLL issues on Windows
    from pyannote.audio.pipelines.utils.hook import ProgressHook
    with ProgressHook() as hook:
        out = pipe({"waveform": torch.from_numpy(read_wav(wav))[None], "sample_rate": SR},
                   hook=hook, **({"num_speakers": n} if n else {}))
    return [{"speaker_id": spk, "start": t.start, "end": t.end} for t, spk in out.speaker_diarization]


def diarize_nemotron(wav, opts):
    """NVIDIA Nemotron 3 Diarization via transformers (no NeMo). End-to-end, max 8 speakers."""
    try:
        import torch
        from transformers import AutoModelForAudioFrameClassification, AutoProcessor
    except ImportError:
        raise Unavailable("not installed: uv sync --extra nemotron --extra cu128 (or --extra cpu)")
    dev = opts["device"]
    if dev == "cuda" and not torch.cuda.is_available():
        raise Unavailable(f"no CUDA in torch {torch.__version__} (use --extra cu128)")
    repo, rev = NEMOTRON
    with heartbeat(f"nemotron: loading model ({dev})"):
        proc = AutoProcessor.from_pretrained(repo, revision=rev)
        model = AutoModelForAudioFrameClassification.from_pretrained(repo, revision=rev).to(dev).eval()
    # num_speakers is not a model input; the model decides (up to 8)
    inputs = proc(read_wav(wav), sampling_rate=SR).to(dev)
    with heartbeat(f"nemotron: diarizing ({dev})"), torch.inference_mode():
        logits = model(**inputs).logits  # (1, frames, 8), 10 ms frames
    segs = proc.extract_speaker_dict(logits, inputs.attention_mask, threshold=opts.get("threshold", 0.5))[0]
    return [{"speaker_id": s["Speaker"], "start": s["Start"], "end": s["End"]} for s in segs]


def diarize_sherpa(wav, opts):
    if opts["device"] != "cpu":
        raise Unavailable("cpu only")
    try:
        if os.name == "nt":
            # Windows wheel ships no onnxruntime.dll: preload the pip one by path, else the
            # loader picks System32's older copy and the process segfaults.
            import ctypes

            import onnxruntime
            ctypes.WinDLL(str(Path(onnxruntime.__file__).parent / "capi" / "onnxruntime.dll"))
        import sherpa_onnx as so
    except ImportError:
        raise Unavailable("sherpa-onnx not installed")
    seg, emb = _hf_file(SHERPA_SEG), _hf_file(SHERPA_EMB)
    config = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg)), num_threads=THREADS),
        embedding=so.SpeakerEmbeddingExtractorConfig(model=str(emb), num_threads=THREADS),
        clustering=so.FastClusteringConfig(num_clusters=opts.get("num_speakers") or -1,
                                           threshold=opts["cluster_threshold"]),
        min_duration_on=0.3, min_duration_off=0.5)
    if not config.validate():
        raise RuntimeError("invalid sherpa-onnx config / model files")
    sd = so.OfflineSpeakerDiarization(config)
    with bar(desc="sherpa: diarizing", unit="chunk") as b:
        def on_progress(done, total):
            b.total, b.n = total, done
            b.update(0)  # redraw, respecting mininterval
            return 0  # non-zero would abort
        res = sd.process(read_wav(wav), callback=on_progress).sort_by_start_time()
    return [{"speaker_id": r.speaker, "start": r.start, "end": r.end} for r in res]


# --- transcription ----------------------------------------------------------

def transcribe_faster_whisper(wav, opts):
    try:
        import ctranslate2
        from faster_whisper import WhisperModel
    except ImportError:
        raise Unavailable("faster-whisper not installed")
    dev = opts["device"]
    if dev == "cuda" and ctranslate2.get_cuda_device_count() == 0:
        raise Unavailable("no CUDA")
    if dev == "cuda" and os.name == "nt":
        # ctranslate2 needs cuBLAS 12 / cuDNN 9 DLLs; the cu128 torch wheel ships them
        with contextlib.suppress(ImportError):
            import torch
            os.add_dll_directory(str(Path(torch.__file__).parent / "lib"))
    from huggingface_hub.errors import LocalEntryNotFoundError
    try:
        with heartbeat(f"faster-whisper: loading {opts['model']} ({dev})"):
            model = WhisperModel(opts["model"], device=dev, compute_type="int8" if dev == "cpu" else "int8_float16",
                                 cpu_threads=THREADS, revision=WHISPER_REVISIONS.get(opts["model"]))  # HF cache
    except LocalEntryNotFoundError:
        raise Unavailable(f"model_not_cached: faster-whisper {opts['model']}")
    # pass samples, not a path: faster-whisper's PyAV decoding breaks with newer av releases
    segments, info = model.transcribe(read_wav(wav), language=opts["language"], word_timestamps=True,
                                      vad_filter=True, condition_on_previous_text=False)
    out = []
    with bar(total=round(info.duration, 1), desc=f"faster-whisper ({dev})") as b:
        for s in segments:  # generator: decoding happens here
            b.n = round(s.end, 1)  # absolute position: no float drift in the display
            b.set_postfix_str(s.text.strip()[:20], refresh=False)
            b.update(0)
            out.append({"start": s.start, "end": s.end, "text": s.text,
                        "words": [{"start": w.start, "end": w.end, "word": w.word} for w in s.words or []]})
        b.n = b.total
    return out


DIARIZERS = {"nemotron": diarize_nemotron, "pyannote": diarize_pyannote, "sherpa": diarize_sherpa}
ASRS = {"faster-whisper": transcribe_faster_whisper}
REGISTRY = {"diarize": DIARIZERS, "transcribe": ASRS}
CPU_ONLY = {"sherpa"}
# name -> (package, pinned model revisions); both go into the cache key
PINS = {"nemotron": ("transformers", NEMOTRON[1]), "pyannote": ("pyannote.audio", PYANNOTE[1]),
        "sherpa": ("sherpa-onnx", [SHERPA_SEG[2], *SHERPA_EMB[1:]]),
        "faster-whisper": ("faster-whisper", WHISPER_REVISIONS)}


def versions(names) -> dict:
    """Package version + model revision per backend, so upgrades invalidate cached results."""
    out = {"schema": SCHEMA_VERSION}
    for n in names:
        pkg, model = PINS[n]
        try:
            out[n] = [metadata.version(pkg), model]
        except metadata.PackageNotFoundError:
            out[n] = [None, model]
    return out


# --- canonical schema check at the backend boundary ---------------------------

def _span(d):
    s, e = float(d["start"]), float(d["end"])
    return (s, e) if 0 <= s < e else None


def normalize(kind: str, rows: list[dict]) -> list[dict]:
    out = []
    if kind == "diarize":
        labels = {}  # backend label -> SPEAKER_%02d in order of first appearance
        for r in sorted(rows, key=lambda r: float(r["start"])):
            if span := _span(r):
                sid = labels.setdefault(r["speaker_id"], f"SPEAKER_{len(labels):02d}")
                out.append({"speaker_id": sid, "start": round(span[0], 3), "end": round(span[1], 3)})
        return out
    for r in rows:
        if span := _span(r):
            words = [{"start": round(w["start"], 3), "end": round(w["end"], 3), "word": str(w["word"])}
                     for w in r.get("words") or [] if _span(w)]
            out.append({"start": round(span[0], 3), "end": round(span[1], 3), "text": str(r["text"]), "words": words})
    return out


def _child(kind, name, wav, out, opts_json):
    err = Path(out + ".err")
    try:
        rows = REGISTRY[kind][name](wav, json.loads(opts_json))
        write_json(Path(out), normalize(kind, rows))
        return 0
    except Unavailable as e:
        err.write_text(f"unavailable: {e}", encoding="utf-8")
        return 10
    except Exception as e:
        log.exception("%s/%s failed", kind, name)
        err.write_text(f"{type(e).__name__}: {e}", encoding="utf-8")
        return 11


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="scribe[%(process)d]: %(message)s", stream=sys.stderr)
    sys.exit(_child(*sys.argv[1:6]))

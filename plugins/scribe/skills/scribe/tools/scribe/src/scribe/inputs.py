"""Input module: any media path -> normalized 16 kHz mono PCM16 WAV (what every backend expects).

Flow: path -> extension -> converter -> audio.wav. Decoding uses PyAV (FFmpeg libraries bundled in
the wheel, so no ffmpeg binary is needed on any OS); the ffmpeg CLI is only a fallback.
"""

import logging
import shutil
import subprocess
import wave
from pathlib import Path

from .audio import SR
from .job import ScribeError
from .progress import bar

log = logging.getLogger("scribe")

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wma", ".aiff", ".amr"}
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".ts", ".mts", ".3gp", ".mpg", ".mpeg"}


def _is_target_wav(src: Path) -> bool:
    try:
        with wave.open(str(src), "rb") as w:
            return (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getcomptype()) == (SR, 1, 2, "NONE")
    except (wave.Error, EOFError):
        return False  # e.g. float / extensible WAV: decode it like anything else


def _copy(src: Path, dst: Path) -> float:
    shutil.copyfile(src, dst)
    with wave.open(str(dst), "rb") as w:
        return w.getnframes() / SR


def _decode_pyav(src: Path, dst: Path) -> float:
    import av

    with av.open(str(src)) as c:
        if not c.streams.audio:
            raise ScribeError("invalid_input", f"no audio stream in {src}", 3)
        st = c.streams.audio[0]
        st.thread_type = "AUTO"
        total = float(c.duration / av.time_base) if c.duration else None
        rs = av.AudioResampler(format="s16", layout="mono", rate=SR)
        n = 0
        with wave.open(str(dst), "wb") as w, bar(total=round(total, 1) if total else None,
                                                  desc=f"decode {src.suffix or 'input'}") as b:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            for frame in c.decode(st):  # only the audio stream is decoded; video packets are skipped
                for f in rs.resample(frame):
                    w.writeframes(f.to_ndarray().tobytes())
                    n += f.samples
                b.n = round(n / SR, 1)
                b.update(0)
            for f in rs.resample(None):  # flush
                w.writeframes(f.to_ndarray().tobytes())
                n += f.samples
    if n == 0:
        raise ScribeError("invalid_input", f"audio stream in {src} decoded to nothing", 3)
    return n / SR


def _decode_ffmpeg(src: Path, dst: Path) -> float:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ScribeError("ffmpeg_not_found", "ffmpeg not on PATH (fallback decoder)", 3)
    r = subprocess.run([exe, "-nostdin", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", str(SR),
                        "-c:a", "pcm_s16le", str(dst)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise ScribeError("decode_failed", r.stderr.strip()[-2000:], 3)
    with wave.open(str(dst), "rb") as w:
        return w.getnframes() / SR


def to_wav(src: str, dst: str) -> dict:
    """Convert by extension; returns {"duration", "format", "converter"} for the job record."""
    src_p, dst_p = Path(src), Path(dst)
    if not src_p.is_file():
        raise ScribeError("input_not_found", f"input not found: {src}", 2)
    ext = src_p.suffix.lower()
    kind = "audio" if ext in AUDIO_EXTS else "video" if ext in VIDEO_EXTS else "unknown"
    if kind == "unknown":
        log.warning("unknown extension %r; trying to decode anyway", ext)
    if ext == ".wav" and _is_target_wav(src_p):
        converter, duration = "copy", _copy(src_p, dst_p)
    else:
        try:
            converter, duration = "pyav", _decode_pyav(src_p, dst_p)
        except ScribeError:
            raise  # no audio stream: another decoder will not find one either
        except Exception as e:  # corrupt/odd container: give the ffmpeg CLI a try
            log.warning("pyav failed (%s: %s); falling back to ffmpeg", type(e).__name__, e)
            try:
                converter, duration = "ffmpeg", _decode_ffmpeg(src_p, dst_p)
            except ScribeError as fe:
                raise ScribeError("decode_failed", f"cannot decode {src}: pyav: {e}; ffmpeg: {fe}", 3)
    return {"duration": round(duration, 3), "format": ext or None, "kind": kind, "converter": converter}

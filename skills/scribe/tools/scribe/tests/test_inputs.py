import tempfile
import wave
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from scribe.inputs import to_wav
from scribe.job import ScribeError

TMP = Path(tempfile.mkdtemp())
TONE = (0.3 * np.sin(2 * np.pi * 440 * np.arange(44100 * 2) / 44100)).astype(np.float32)  # 2 s


def _encode(path: Path, codec: str, with_video=False):
    """Write a 2 s tone (and optionally a few black video frames) with PyAV."""
    with av.open(str(path), "w") as c:
        a = c.add_stream(codec, rate=44100 if codec != "libopus" else 48000, layout="stereo")
        v = c.add_stream("mpeg4" if path.suffix == ".mp4" else "libvpx", rate=5) if with_video else None
        if v:
            v.width, v.height, v.pix_fmt = 64, 48, "yuv420p"
            for i in range(10):
                f = av.VideoFrame.from_ndarray(np.zeros((48, 64, 3), np.uint8), format="rgb24")
                f.pts, f.time_base = i, Fraction(1, 5)
                c.mux(v.encode(f))
            c.mux(v.encode())
        rs = av.AudioResampler(format=a.codec_context.format.name, layout="stereo", rate=a.rate)
        frame = av.AudioFrame.from_ndarray(np.stack([TONE, TONE])[None].reshape(1, -1), format="flt", layout="stereo")
        frame.rate = 44100
        for f in rs.resample(frame) + rs.resample(None):
            c.mux(a.encode(f))
        c.mux(a.encode())


def _check(src: Path, converter: str):
    dst = TMP / (src.name + ".out.wav")
    info = to_wav(str(src), str(dst))
    with wave.open(str(dst), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
    assert info["converter"] == converter
    assert abs(info["duration"] - 2.0) < 0.15, info


def test_wav_44k_stereo_is_resampled():
    src = TMP / "in.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(44100)
        w.writeframes((np.repeat(TONE, 2) * 32767).astype(np.int16).tobytes())
    _check(src, "pyav")


def test_wav_already_16k_mono_is_copied():
    src = TMP / "ready.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(np.zeros(32000, np.int16).tobytes())
    _check(src, "copy")


@pytest.mark.parametrize("name,codec,video", [
    ("a.mp3", "libmp3lame", False), ("a.m4a", "aac", False), ("a.flac", "flac", False),
    ("v.mp4", "aac", True), ("v.webm", "libopus", True), ("v.mkv", "libopus", False),
])
def test_media_containers(name, codec, video):
    src = TMP / name
    _encode(src, codec, video)
    _check(src, "pyav")


def test_no_audio_stream_and_missing_file():
    src = TMP / "silent.mp4"
    with av.open(str(src), "w") as c:
        v = c.add_stream("mpeg4", rate=5)
        v.width, v.height, v.pix_fmt = 64, 48, "yuv420p"
        f = av.VideoFrame.from_ndarray(np.zeros((48, 64, 3), np.uint8), format="rgb24")
        c.mux(v.encode(f))
        c.mux(v.encode())
    with pytest.raises(ScribeError) as e:
        to_wav(str(src), str(TMP / "x.wav"))
    assert e.value.code == "invalid_input"
    with pytest.raises(ScribeError) as e:
        to_wav(str(TMP / "nope.mp4"), str(TMP / "y.wav"))
    assert e.value.code == "input_not_found"

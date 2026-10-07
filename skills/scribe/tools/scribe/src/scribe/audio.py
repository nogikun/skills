"""WAV I/O and representative speaker sample selection."""

import wave

import numpy as np

SR = 16000
SAMPLE_MIN, SAMPLE_MAX = 5.0, 10.0
JOIN_GAP = 1.0


def read_wav(path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def write_wav(path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())


def _clean_pieces(turn, others):
    """Parts of `turn` not overlapped by any interval in `others`."""
    pieces = [(turn["start"], turn["end"])]
    for o in others:
        nxt = []
        for a, b in pieces:
            if o["end"] <= a or o["start"] >= b:
                nxt.append((a, b))
                continue
            if o["start"] > a:
                nxt.append((a, o["start"]))
            if o["end"] < b:
                nxt.append((o["end"], b))
        pieces = nxt
    return pieces


def _score(x: np.ndarray) -> float:
    if len(x) == 0:
        return 0.0
    frames = x[: len(x) // 480 * 480].reshape(-1, 480)  # 30 ms
    rms = np.sqrt((frames ** 2).mean(axis=1)) if len(frames) else np.zeros(1)
    speech_ratio = float((rms > 0.01).mean())  # ~ -40 dBFS
    loudness = min(1.0, float(np.sqrt((x ** 2).mean())) / 0.05)
    return len(x) / SR * speech_ratio * loudness


def pick_samples(audio: np.ndarray, diarization: list[dict]) -> dict[str, tuple[float, float]]:
    """Best non-overlapped 5-10 s window per speaker -> {speaker_id: (start, end)}."""
    # ponytail: O(n^2) overlap scan; fine for ~1000 turns (30 min), sort+sweep if hours-long
    joined = []  # bridge short pauses inside one speaker's talk; other speakers are cut out below
    for t in sorted(diarization, key=lambda t: t["start"]):
        prev = next((j for j in reversed(joined) if j["speaker_id"] == t["speaker_id"]), None)
        if prev and t["start"] - prev["end"] <= JOIN_GAP:
            prev["end"] = max(prev["end"], t["end"])
        else:
            joined.append(dict(t))
    best = {}
    for turn in joined:
        spk = turn["speaker_id"]
        others = [o for o in diarization if o["speaker_id"] != spk
                  and o["end"] > turn["start"] and o["start"] < turn["end"]]
        for a, b in _clean_pieces(turn, others):
            while b - a > 0.3:
                end = min(b, a + SAMPLE_MAX)
                key = (end - a >= SAMPLE_MIN, _score(audio[int(a * SR):int(end * SR)]))
                if spk not in best or key > best[spk][0]:
                    best[spk] = (key, (round(a, 2), round(end, 2)))
                a = end
    for turn in sorted(diarization, key=lambda t: t["start"] - t["end"]):  # longest first
        if turn["speaker_id"] not in best:  # only ever spoke over someone: take it anyway
            span = (turn["start"], min(turn["end"], turn["start"] + SAMPLE_MAX))
            best[turn["speaker_id"]] = (None, (round(span[0], 2), round(span[1], 2)))
    return {spk: span for spk, (_, span) in sorted(best.items())}

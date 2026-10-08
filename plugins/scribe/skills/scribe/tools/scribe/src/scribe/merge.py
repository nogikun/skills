"""Timeline merge: attach a speaker to each ASR word (or segment)."""

import bisect

MAX_GAP = 1.0  # beyond this distance from any speaker turn -> speaker_id None
# ponytail: tuned on one news clip (Whisper skipped a 10 s interview inside its 30 s window). Shorter
# untranscribed bits are mostly backchannels/noise; lower it if real short replies go missing.
MIN_GAP = 1.5  # s of diarized speech with no text that counts as "not transcribed"
CLIP_PAD = 0.3  # s of context around each re-transcribed gap


class _Index:
    def __init__(self, diarization):
        self.turns = sorted(diarization, key=lambda d: d["start"])
        self.starts = [d["start"] for d in self.turns]
        self.maxlen = max((d["end"] - d["start"] for d in self.turns), default=0)

    def speaker(self, start, end):
        lo = bisect.bisect_left(self.starts, start - self.maxlen - MAX_GAP)
        hi = bisect.bisect_right(self.starts, end + MAX_GAP)
        near = self.turns[lo:hi]
        overlaps = [(min(end, d["end"]) - max(start, d["start"]), d["speaker_id"]) for d in near]
        ov, spk = max(overlaps, default=(0, None), key=lambda x: x[0])
        if ov > 0:
            return spk
        mid = (start + end) / 2
        gap, spk = min(((max(d["start"] - mid, mid - d["end"]), d["speaker_id"]) for d in near),
                       default=(MAX_GAP + 1, None), key=lambda x: x[0])
        return spk if gap <= MAX_GAP else None


def merge(transcript: list[dict], diarization: list[dict]) -> list[dict]:
    """Split each ASR segment at speaker changes (word-level when words exist)."""
    idx, out = _Index(diarization), []
    for seg in transcript:
        words = seg.get("words") or [{"start": seg["start"], "end": seg["end"], "word": seg["text"]}]
        cur = None
        for w in words:
            spk = idx.speaker(w["start"], w["end"])
            if cur and cur["speaker_id"] == spk:
                cur["end"], cur["text"] = w["end"], cur["text"] + w["word"]
            else:
                cur = {"speaker_id": spk, "start": w["start"], "end": w["end"], "text": w["word"]}
                out.append(cur)
    for m in out:
        m["text"] = m["text"].strip()
    return [m for m in out if m["text"]]


def _union(spans) -> list[list[float]]:
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def uncovered(diarization: list[dict], segments: list[dict], min_gap: float = MIN_GAP) -> list[dict]:
    """Diarized speech that no ASR segment overlaps: [{speaker_id, start, end}] pieces >= min_gap s."""
    cov = _union((s["start"], s["end"]) for s in segments)
    ends = [b for _, b in cov]
    out = []
    for t in sorted(diarization, key=lambda t: t["start"]):
        cur = t["start"]
        for a, b in cov[bisect.bisect_right(ends, t["start"]):]:
            if a >= t["end"]:
                break
            if a - cur >= min_gap:
                out.append({"speaker_id": t["speaker_id"], "start": round(cur, 3), "end": round(a, 3)})
            cur = max(cur, b)
        if t["end"] - cur >= min_gap:
            out.append({"speaker_id": t["speaker_id"], "start": round(cur, 3), "end": round(t["end"], 3)})
    return out


def clips(gaps: list[dict], duration: float | None = None) -> list[list[float]]:
    """Padded, merged [start, end] spans to re-transcribe."""
    end = duration if duration else float("inf")
    return [[round(a, 3), round(b, 3)] for a, b in
            _union((max(0.0, g["start"] - CLIP_PAD), min(end, g["end"] + CLIP_PAD)) for g in gaps)]

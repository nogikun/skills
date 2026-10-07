"""Timeline merge: attach a speaker to each ASR word (or segment)."""

import bisect

MAX_GAP = 1.0  # beyond this distance from any speaker turn -> speaker_id None


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

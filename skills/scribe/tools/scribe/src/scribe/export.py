"""Exporters: name -> function(doc) -> str. doc is the canonical JSON (元仕様 §20)."""

import json


def build_doc(job_id: str, duration: float, merged: list[dict], names: dict) -> dict:
    ids = sorted({m["speaker_id"] for m in merged if m["speaker_id"]} | set(names))
    label = lambda s: names.get(s) or s or "UNKNOWN"  # noqa: E731
    return {
        "job_id": job_id,
        "duration": duration,
        "speakers": [{"id": s, "name": names.get(s)} for s in ids],
        "segments": [{**m, "speaker_name": label(m["speaker_id"])} for m in merged],
    }


def _ts(sec: float, sep: str = "") -> str:
    ms = round(sec * 1000)
    h, m, s = ms // 3_600_000, ms // 60_000 % 60, ms // 1000 % 60
    return f"{h:02}:{m:02}:{s:02}" + (f"{sep}{ms % 1000:03}" if sep else "")


def to_json(doc):
    return json.dumps(doc, ensure_ascii=False, indent=2)


def to_markdown(doc):
    lines, prev = ["# Meeting Transcript"], object()
    for s in doc["segments"]:
        if s["speaker_name"] != prev:  # group consecutive turns of the same speaker
            lines += ["", f"## {_ts(s['start'])} — {s['speaker_name']}", ""]
            prev = s["speaker_name"]
            lines.append(s["text"])
        else:
            lines[-1] += "\n" + s["text"]
    return "\n".join(lines) + "\n"


def to_txt(doc):
    return "".join(f"[{_ts(s['start'])}] {s['speaker_name']}: {s['text']}\n" for s in doc["segments"])


def to_srt(doc):
    return "\n".join(f"{i}\n{_ts(s['start'], ',')} --> {_ts(s['end'], ',')}\n{s['speaker_name']}: {s['text']}\n"
                     for i, s in enumerate(doc["segments"], 1))


def to_vtt(doc):
    from html import escape  # cue text and <v> names must not contain raw & < >

    cues = (f"{_ts(s['start'], '.')} --> {_ts(s['end'], '.')}\n"
            f"<v {escape(s['speaker_name'], quote=False)}>{escape(s['text'], quote=False)}\n"
            for s in doc["segments"])
    return "WEBVTT\n\n" + "\n".join(cues)


EXPORTERS = {"json": to_json, "markdown": to_markdown, "txt": to_txt, "srt": to_srt, "vtt": to_vtt, "webvtt": to_vtt}

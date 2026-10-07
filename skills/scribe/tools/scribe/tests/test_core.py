import os
import tempfile
from unittest.mock import patch

import pytest

os.environ["SCRIBE_HOME"] = tempfile.mkdtemp()

import numpy as np  # noqa: E402

from scribe import job as J  # noqa: E402
from scribe import audio, cli, pipeline  # noqa: E402
from scribe.audio import SR, pick_samples  # noqa: E402
from scribe.backends import normalize  # noqa: E402
from scribe.export import EXPORTERS, build_doc  # noqa: E402
from scribe.merge import merge  # noqa: E402

DIAR = [{"speaker_id": "SPEAKER_00", "start": 0.0, "end": 10.0},
        {"speaker_id": "SPEAKER_01", "start": 9.0, "end": 20.0}]


def test_merge_splits_segment_at_speaker_change_by_words():
    tr = [{"start": 8.0, "end": 12.0, "text": "はいそうです",
           "words": [{"start": 8.0, "end": 8.8, "word": "はい"}, {"start": 10.5, "end": 12.0, "word": "そうです"}]}]
    m = merge(tr, DIAR)
    assert [(x["speaker_id"], x["text"]) for x in m] == [("SPEAKER_00", "はい"), ("SPEAKER_01", "そうです")]


def test_merge_far_from_any_turn_is_unknown_near_is_assigned():
    m = merge([{"start": 30.0, "end": 31.0, "text": "遠い", "words": []},
               {"start": 20.2, "end": 20.6, "text": "近い", "words": []}], DIAR)
    assert [x["speaker_id"] for x in m] == [None, "SPEAKER_01"]


def test_normalize_relabels_and_drops_bad_spans():
    rows = [{"speaker_id": 7, "start": 5, "end": 6}, {"speaker_id": 3, "start": 1, "end": 2},
            {"speaker_id": 3, "start": 4, "end": 4}]
    assert normalize("diarize", rows) == [{"speaker_id": "SPEAKER_00", "start": 1.0, "end": 2.0},
                                          {"speaker_id": "SPEAKER_01", "start": 5.0, "end": 6.0}]


def test_pick_samples_avoids_overlap_and_silence():
    x = np.zeros(SR * 20, dtype=np.float32)
    x[: SR * 9] = 0.1 * np.sin(np.arange(SR * 9) / 5)  # SPEAKER_00 loud only before overlap
    x[SR * 10:] = 0.1 * np.sin(np.arange(SR * 10) / 5)
    spans = pick_samples(x, DIAR)
    assert spans["SPEAKER_00"] == (0.0, 9.0)
    assert spans["SPEAKER_01"] == (10.0, 20.0)


def test_exporters():
    doc = build_doc("j1", 20.0, [{"speaker_id": "SPEAKER_00", "start": 120.4, "end": 127.8, "text": "始めます。"},
                                 {"speaker_id": None, "start": 128.0, "end": 129.0, "text": "はい"}],
                    {"SPEAKER_00": "田中"})
    assert "## 00:02:00 — 田中\n\n始めます。" in EXPORTERS["markdown"](doc)
    assert "00:02:00,400 --> 00:02:07,800\n田中: 始めます。" in EXPORTERS["srt"](doc)
    assert "[00:02:08] UNKNOWN: はい" in EXPORTERS["txt"](doc)
    vtt = EXPORTERS["vtt"](build_doc("j1", 1.0, [{"speaker_id": "SPEAKER_00", "start": 1.5, "end": 2.0,
                                                  "text": "A<B & C"}], {"SPEAKER_00": "田中"}))
    assert vtt == "WEBVTT\n\n00:00:01.500 --> 00:00:02.000\n<v 田中>A&lt;B &amp; C\n"


def test_status_derivation_and_lock():
    job = J.create(__file__, "t1")
    assert J.status(job) == "created"
    d = J.job_dir("t1")
    J.write_json(d / "diarization.json", DIAR)
    job["stages"] = {s: {"status": "completed"} for s in J.STAGES}
    assert J.status(job) == "speaker_identification_required"
    J.write_json(d / "speakers.json", {"SPEAKER_00": "田中", "SPEAKER_01": "佐藤"})
    assert J.status(job) == "ready"
    job["exports"] = {"markdown": {}}
    assert J.status(job) == "exported"
    job["stages"]["merge"]["status"] = "running"  # crashed process: no lock holder
    assert J.status(job) == "failed"
    with J.lock("t1"):
        assert J.is_locked("t1")
    assert not J.is_locked("t1")
    from scribe.cli import cmd_jobs
    assert [j["job_id"] for j in cmd_jobs(None)[0]["jobs"]] == ["t1"]


def test_rename_refreshes_exported_files():
    import argparse

    from scribe.cli import cmd_export, cmd_speaker_rename

    job = J.create(__file__, "t2")
    d = J.job_dir("t2")
    J.write_json(d / "diarization.json", DIAR)
    J.write_json(d / "merged.json", [{"speaker_id": "SPEAKER_00", "start": 0.0, "end": 1.0, "text": "こんにちは"}])
    job["stages"] = {s: {"status": "completed"} for s in J.STAGES}
    J.save(job)
    out = d / "out.vtt"
    cmd_export(argparse.Namespace(job="t2", format="vtt", output=str(out)))
    assert "<v SPEAKER_00>" in out.read_text(encoding="utf-8")
    res, code = cmd_speaker_rename(argparse.Namespace(job="t2", pairs=["SPEAKER_00=田中", "SPEAKER_01=佐藤"]))
    assert code == 0 and res["status"] == "exported" and res["refreshed"] == [str(out.resolve())]
    assert "<v 田中>こんにちは" in out.read_text(encoding="utf-8")
    with pytest.raises(J.ScribeError):
        cmd_speaker_rename(argparse.Namespace(job="t2", pairs=["SPEAKER_00"]))


@pytest.mark.parametrize("missing,rerun", [("audio.wav", ["diarize", "transcribe"]),
                                          ("diarization.json", ["diarize"]),
                                          ("transcript.json", ["transcribe"]),
                                          ("merged.json", []), ("samples/SPEAKER_00.wav", [])])
def test_pipeline_invalidates_only_dependencies_and_honors_device(missing, rerun):
    job = J.create(__file__)
    d = J.job_dir(job["job_id"])
    source = d / "source.wav"
    audio.write_wav(source, np.full(SR * 20, 0.1, np.float32))
    job["input"] = str(source)
    opts = pipeline.resolve_opts(job, {"device": "cpu"})
    calls, changed = [], False

    def backend(kind, chain, wav, out, options, timeout):
        calls.append(kind)
        boundary = 12.0 if changed else 10.0
        rows = ([{"speaker_id": "SPEAKER_00", "start": 0.0, "end": boundary},
                 {"speaker_id": "SPEAKER_01", "start": boundary, "end": 20.0}] if kind == "diarize" else
                [{"start": 10.5, "end": 11.5, "text": "new" if changed else "old", "words": []}])
        J.write_json(out, rows)
        return {"backend": f"fake/{options['device']}"}

    with patch.object(pipeline, "_run_chain", backend), J.lock(job["job_id"]):
        pipeline.run(job, opts)
        names = {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}
        J.write_json(d / "speakers.json", names)
        job["exports"] = {"txt": [{"path": "old.txt"}]}
        J.save(job)
        old_samples = J.read_json(d / "samples/samples.json")
        calls.clear()
        changed = True
        (d / missing).unlink()
        pipeline.run(job, opts)
        assert calls == rerun
        assert (d / missing).exists()
        assert J.read_json(d / "merged.json") == merge(J.read_json(d / "transcript.json"),
                                                        J.read_json(d / "diarization.json"))
        assert bool(job.get("exports")) == missing.startswith("samples/")
        if "diarize" in rerun:
            assert J.speaker_names(job["job_id"]) == {}
            assert J.read_json(d / "samples/samples.json") != old_samples
        else:
            assert J.speaker_names(job["job_id"]) == names
            assert J.read_json(d / "samples/samples.json") == old_samples

        calls.clear()
        opts = pipeline.resolve_opts(job, {"device": "cuda"})
        pipeline.run(job, opts)
        assert calls == ["diarize", "transcribe"]
        assert all(job["stages"][s]["backend"] == "fake/cuda" for s in calls)
        calls.clear()
        pipeline.run(job, opts)
        assert calls == []


@pytest.mark.parametrize("stage", ["audio", "diarize", "transcribe"])
def test_failed_reprocessing_cannot_export_old_results(stage):
    job = J.create(__file__)
    d = J.job_dir(job["job_id"])
    job["stages"] = {s: {"status": "completed", "key": "old"} for s in J.STAGES}
    job["exports"] = {"markdown": [{"path": "old.md"}]}
    J.write_json(d / "merged.json", [])
    J.write_json(d / "speakers.json", {"SPEAKER_00": "Alice"})
    (d / "samples").mkdir()
    J.write_json(d / "samples/samples.json", {"SPEAKER_00": [0, 5]})
    J.save(job)

    def fail():
        # Invalidation must already be on disk if the worker crashes or is interrupted.
        saved = J.load(job["job_id"])
        assert "merge" not in saved["stages"] and not saved.get("exports")
        assert not (d / "merged.json").exists()
        if stage != "transcribe":
            assert not (d / "samples").exists() and not (d / "speakers.json").exists()
        raise RuntimeError("backend crashed")

    with J.lock(job["job_id"]), pytest.raises(RuntimeError, match="backend crashed"):
        pipeline._stage(job, stage, "new", [], fail)
    assert J.status(J.load(job["job_id"])) == "failed"
    with pytest.raises(J.ScribeError, match="no merged transcript"):
        cli.cmd_export(cli._parser().parse_args(["export", job["job_id"]]))
    if stage != "transcribe":
        # Files can survive an interruption between saving invalidation and deleting them.
        J.write_json(d / "diarization.json", DIAR)
        J.write_json(d / "speakers.json", {"SPEAKER_00": "Old person"})
        assert cli._speakers(J.load(job["job_id"])) == []
        with pytest.raises(J.ScribeError, match="no completed diarization"):
            cli._apply_names(job["job_id"], {"SPEAKER_00": "Wrong person"})


def test_export_paths_are_all_refreshed_with_legacy_jobs_and_write_failures():
    job = J.create(__file__)
    jid, d = job["job_id"], J.job_dir(job["job_id"])
    job["stages"] = {s: {"status": "completed"} for s in J.STAGES}
    J.write_json(d / "diarization.json", DIAR)
    J.write_json(d / "merged.json", [{"speaker_id": "SPEAKER_00", "start": 0, "end": 1, "text": "hello"}])
    first, second, third = (d / n for n in ("first.md", "second.md", "third.md"))
    first.write_text("legacy output", encoding="utf-8")
    job["exports"] = {"markdown": {"path": str(first.resolve()), "at": "old"}}
    J.save(job)
    for path in (second, second, third):
        cli.cmd_export(cli._parser().parse_args(["export", jid, "-o", str(path)]))
    assert len(J.load(jid)["exports"]["markdown"]) == 3  # repeated path is registered once
    third.unlink()
    third.mkdir()  # one unwritable target must not prevent updating the other two
    result = cli._apply_names(jid, {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"})
    assert result["refreshed"] == [str(first.resolve()), str(second.resolve())]
    assert all("Alice" in path.read_text(encoding="utf-8") for path in (first, second))
    assert len(J.load(jid)["exports"]["markdown"]) == 2
    cli.cmd_export(cli._parser().parse_args(["export", jid, "--format", "txt", "-o", str(second)]))
    assert len(J.load(jid)["exports"]["markdown"]) == 1  # the latest format owns this path


def test_commands_load_validate_and_render_after_lock():
    job = J.create(__file__)
    jid, d = job["job_id"], J.job_dir(job["job_id"])
    job["stages"] = {s: {"status": "completed"} for s in J.STAGES}
    J.write_json(d / "diarization.json", DIAR)
    J.write_json(d / "merged.json", [{"speaker_id": "SPEAKER_00", "start": 0, "end": 1, "text": "hello"}])
    J.write_json(d / "speakers.json", {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"})
    J.save(job)
    original_lock = J.lock

    def concurrent_update(job_id):
        current = J.load(job_id)
        current["options"] = {"language": "en"}
        current["exports"] = {"txt": [{"path": None}]}
        J.save(current)
        J.write_json(d / "speakers.json", {"SPEAKER_00": "Carol", "SPEAKER_01": "Bob"})
        return original_lock(job_id)

    out = d / "out.md"
    with patch.object(J, "lock", concurrent_update):
        cli.cmd_export(cli._parser().parse_args(["export", jid, "-o", str(out)]))
        assert "Carol" in out.read_text(encoding="utf-8")
        with patch.object(pipeline, "run") as run:
            cli.cmd_process(cli._parser().parse_args(["process", "--job", jid]))
            assert run.call_args.args[1]["language"] == "en"
            assert J.load(jid)["exports"] == {"txt": [{"path": None}]}

    def concurrent_rediarization(job_id):
        J.write_json(d / "diarization.json", [{"speaker_id": "SPEAKER_01", "start": 0, "end": 1}])
        return original_lock(job_id)

    with patch.object(J, "lock", concurrent_rediarization), pytest.raises(J.ScribeError, match="not in"):
        cli._apply_names(jid, {"SPEAKER_00": "Wrong person"})
    with pytest.raises(J.ScribeError, match="job not found"):
        cli.cmd_export(cli._parser().parse_args(["export", "missing-job"]))

import http.client
import json
import os
import tempfile
import threading
from pathlib import Path

os.environ.setdefault("SCRIBE_HOME", tempfile.mkdtemp())

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from scribe import audio, cli, progress, server  # noqa: E402
from scribe import job as J  # noqa: E402

DIAR = [{"speaker_id": "SPEAKER_00", "start": 0.0, "end": 1.0}, {"speaker_id": "SPEAKER_01", "start": 1.0, "end": 2.0}]
MERGED = [{"speaker_id": "SPEAKER_00", "start": 0.1, "end": 0.9, "text": "始めます"},
          {"speaker_id": "SPEAKER_01", "start": 1.1, "end": 1.9, "text": "はい"}]


def test_progress_pct_weights():
    job = {"stages": {"audio": {"status": "completed"}, "diarize": {"status": "running"}}}
    assert server.progress_pct(job, None) == 5.0
    assert server.progress_pct(job, {"stage": "diarize", "n": 5, "total": 10}) == 22.5
    assert server.progress_pct(job, {"stage": "diarize", "n": 0, "total": None}) == 5.0  # heartbeat: unknown
    job["stages"] = {s: {"status": "completed"} for s in J.STAGES}
    assert server.progress_pct(job, None) == 100.0


def test_bar_mirrors_progress_to_job_file():
    f = Path(tempfile.mkdtemp()) / "progress.json"
    progress.context(f, stage="transcribe", attempt="faster-whisper/cpu")
    try:
        with progress.bar(total=10, desc="asr") as b:
            b.n = 4
            b.update(0)
        data = json.loads(f.read_text(encoding="utf-8"))
        assert (data["stage"], data["attempt"], data["n"], data["total"]) == ("transcribe", "faster-whisper/cpu", 4, 10)
    finally:
        progress.clear()


@pytest.fixture
def srv():
    jid = "web" + os.urandom(3).hex()
    job = J.create(__file__, jid)
    d = J.job_dir(jid)
    J.write_json(d / "diarization.json", DIAR)
    J.write_json(d / "merged.json", MERGED)
    audio.write_wav(d / "audio.wav", np.zeros(audio.SR * 2, dtype=np.float32))
    job["duration"] = 2.0
    job["stages"] = {s: {"status": "completed", "key": {"s": s}} for s in J.STAGES}
    J.save(job)
    out = Path(tempfile.mkdtemp()) / "t.txt"
    assert cli.main(["export", jid, "--format", "txt", "-o", str(out)]) == 0
    s = server.make_server("127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield jid, s.server_address[1], out
    s.shutdown()
    s.server_close()


def _req(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body=json.dumps(body) if body is not None else None,
              headers={"Content-Type": "application/json", **(headers or {})})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, r.getheaders(), data


def test_ctrl_s_saves_names_and_rewrites_exports(srv):
    jid, port, out = srv
    code, _, body = _req(port, "GET", f"/api/jobs/{jid}")
    view = json.loads(body)
    assert code == 200 and view["status"] == "speaker_identification_required" and view["pct"] == 100.0
    tag = view["diarize_tag"]

    code, _, _ = _req(port, "PUT", f"/api/jobs/{jid}/speakers", {"names": {"SPEAKER_00": "田中"}}, {"If-Match": "old"})
    assert code == 409  # re-diarized since the editor loaded
    code, _, _ = _req(port, "PUT", f"/api/jobs/{jid}/speakers", {"names": {"SPEAKER_00": "田中"}},
                      {"If-Match": tag, "Origin": "http://evil.example"})
    assert code == 403
    code, _, _ = _req(port, "GET", f"/api/jobs/{jid}", headers={"Host": "evil.example"})
    assert code == 403  # DNS rebinding

    code, _, body = _req(port, "PUT", f"/api/jobs/{jid}/speakers",
                         {"names": {"SPEAKER_00": "田中", "SPEAKER_01": "佐藤"}},
                         {"If-Match": tag, "Origin": f"http://127.0.0.1:{port}"})
    assert code == 200 and json.loads(body)["refreshed"] == [str(out.resolve())]
    assert "田中: 始めます" in out.read_text(encoding="utf-8")
    assert J.speaker_names(jid) == {"SPEAKER_00": "田中", "SPEAKER_01": "佐藤"}
    code, _, body = _req(port, "GET", f"/api/jobs/{jid}/export?format=srt")
    assert code == 200 and "佐藤: はい" in body.decode()


def test_audio_supports_range(srv):
    jid, port, _ = srv
    raw = (J.job_dir(jid) / "audio.wav").read_bytes()
    code, headers, body = _req(port, "GET", f"/api/jobs/{jid}/audio", headers={"Range": "bytes=10-19"})
    assert code == 206 and body == raw[10:20]
    assert dict(headers)["Content-Range"] == f"bytes 10-19/{len(raw)}"
    code, _, body = _req(port, "GET", f"/api/jobs/{jid}/audio")
    assert code == 200 and body == raw
    code, _, _ = _req(port, "GET", "/api/jobs/nope/audio")
    assert code == 404
    assert _req(port, "GET", "/")[0] == 200 and Path(server.WEB / "app.js").exists()


def _waiting(jid, state="queued"):
    from datetime import datetime, timezone
    J.write_json(J.job_dir(jid) / "progress.json", {"state": state, "updated_at": datetime.now(timezone.utc).isoformat()})


def test_queue_priority_and_control_endpoint(srv):
    jid, port, _ = srv
    a, b = (J.create(__file__)["job_id"] for _ in range(2))
    try:
        J.set_control(a, order=1.0)
        J.set_control(b, order=2.0)
        _waiting(a), _waiting(b)
        assert [w["job_id"] for w in J.waiting() if w["job_id"] in (a, b)] == [a, b]
        assert J.queue_state(b) == {"state": "queued", "position": 2}

        hdr = {"Origin": f"http://127.0.0.1:{port}"}
        code, _, body = _req(port, "PUT", f"/api/jobs/{b}/control", {"first": True}, hdr)
        assert code == 200 and json.loads(body)["queue"] == {"state": "queued", "position": 1}
        assert _req(port, "PUT", f"/api/jobs/{a}/control", {"paused": True}, hdr)[0] == 200
        assert J.control(a)["paused"] is True
        assert _req(port, "PUT", f"/api/jobs/{a}/control", {"paused": "yes"}, hdr)[0] == 400
        # no scribe process alive for this job -> nothing to pause
        assert _req(port, "PUT", f"/api/jobs/{jid}/control", {"paused": True}, hdr)[0] == 409
    finally:
        for j in (a, b):
            (J.job_dir(j) / "progress.json").unlink()


def test_pause_kills_running_backend_and_the_stage_reruns_on_resume():
    from unittest.mock import patch

    from scribe import pipeline

    job = J.create(__file__)
    jid, d = job["job_id"], J.job_dir(job["job_id"])
    J.set_control(jid, paused=True)

    class Child:
        killed = False

        def __init__(self, *a, **k):
            pass

        def poll(self):
            return None  # still decoding

        def kill(self):
            Child.killed = True

        def wait(self):
            return -9

    with patch.object(pipeline.subprocess, "Popen", Child), pytest.raises(pipeline.Paused):
        pipeline._run_chain("transcribe", ["faster-whisper"], d / "audio.wav", d / "transcript.json",
                            {"device": "cpu"}, 60)
    assert Child.killed

    class Turns:
        n = 0

        def turn(self):
            Turns.n += 1

    calls = []

    def work():
        calls.append(1)
        if len(calls) == 1:
            raise pipeline.Paused()
        return {"backend": "fake/cpu"}

    with J.lock(jid):
        assert pipeline._stage(job, "transcribe", "k", [], work, Turns())
    assert Turns.n == 2 and len(calls) == 2  # waited again after the pause, then redid the stage
    assert job["stages"]["transcribe"]["status"] == "completed"


def test_runner_yields_to_a_prioritized_or_paused_job():
    from scribe import pipeline

    me, other = (J.create(__file__)["job_id"] for _ in range(2))
    J.set_control(me, order=5.0)
    r = pipeline.Runner(me)
    try:
        r.slot = object()  # pretend we hold the runner
        assert not r._must_yield()
        J.set_control(other, order=1.0)
        _waiting(other)
        assert r._must_yield()  # someone was put ahead of us
        (J.job_dir(other) / "progress.json").unlink()
        J.set_control(me, paused=True)
        assert r._must_yield()
    finally:
        r.slot = None


def test_text_corrections_reach_exports_and_survive_only_while_the_line_exists(srv):
    jid, port, out = srv
    hdr = {"Origin": f"http://127.0.0.1:{port}"}
    k0, k1 = J.seg_key(MERGED[0]), J.seg_key(MERGED[1])
    code, _, body = _req(port, "PUT", f"/api/jobs/{jid}/speakers", {"texts": {k0: "始めましょう", k1: ""}}, hdr)
    assert code == 200 and json.loads(body)["texts"] == {k0: "始めましょう", k1: ""}
    text = out.read_text(encoding="utf-8")  # the earlier export was rewritten
    assert "始めましょう" in text and "はい" not in text  # "" drops the line
    _, _, body = _req(port, "GET", f"/api/jobs/{jid}/timeline")
    assert json.loads(body)["segments"][0]["text"] == "始めます"  # the ASR original stays for "revert"

    code, _, body = _req(port, "PUT", f"/api/jobs/{jid}/speakers", {"texts": {k0: "始めます"}}, hdr)
    assert json.loads(body)["texts"] == {k1: ""}  # typed back to the original -> no longer an edit
    assert _req(port, "PUT", f"/api/jobs/{jid}/speakers", {"texts": {"1-2": "x"}}, hdr)[0] == 409
    assert _req(port, "PUT", f"/api/jobs/{jid}/speakers", {"texts": {k0: 5}}, hdr)[0] == 400

    J.write_json(J.job_dir(jid) / "merged.json", MERGED[:1])  # reprocessed: the deleted line is gone
    w = cli.summary(J.load(jid))["warnings"]
    assert w[0]["code"] == "text_edits_lost" and w[0]["count"] == 1


def test_delete_removes_only_the_job_folder_and_never_a_busy_job(srv):
    jid, port, out = srv
    hdr = {"Origin": f"http://127.0.0.1:{port}"}
    assert _req(port, "DELETE", f"/api/jobs/{jid}", headers={"Origin": "http://evil.example"})[0] == 403
    with J.lock(jid):  # a scribe process works on it
        assert _req(port, "DELETE", f"/api/jobs/{jid}", headers=hdr)[0] == 409
    assert J.job_dir(jid).exists()
    code, _, _ = _req(port, "DELETE", f"/api/jobs/{jid}", headers=hdr)
    assert code == 200 and not J.job_dir(jid).exists()
    assert out.exists()  # the exported file is the user's, not the job's
    assert _req(port, "DELETE", f"/api/jobs/{jid}", headers=hdr)[0] == 404
    assert jid not in [j["job_id"] for j in json.loads(_req(port, "GET", "/api/jobs")[2])["jobs"]]

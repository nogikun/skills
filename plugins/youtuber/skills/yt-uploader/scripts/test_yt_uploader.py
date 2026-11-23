# /// script
# requires-python = ">=3.11"
# dependencies = ["google-api-python-client>=2.100", "google-auth-oauthlib>=1.2", "pytest>=8"]
# ///
"""実 API に触れずに、private 固定・事前拒否・再実行で重複しないことを確かめる。
実行: uv run skills/yt-uploader/scripts/test_yt_uploader.py
"""
import argparse
import sys
from unittest.mock import MagicMock

import pytest

import yt_uploader as m


def fake_yt(playlist_privacy="private", playlist_owner="UC1", video_privacy="private"):
    yt = MagicMock()
    yt.channels.return_value.list.return_value.execute.return_value = {"items": [{"id": "UC1", "snippet": {"title": "me"}}]}
    yt.playlists.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "PL1", "snippet": {"channelId": playlist_owner}, "status": {"privacyStatus": playlist_privacy}}]
    }
    yt.videos.return_value.insert.return_value.next_chunk.return_value = (None, {"id": "V1"})
    yt.videos.return_value.list.return_value.execute.return_value = {"items": [{"status": {"privacyStatus": video_privacy}}]}
    yt.playlistItems.return_value.list.return_value.execute.return_value = {"items": []}
    return yt


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "LEDGER", tmp_path / "ledger.json")
    video = tmp_path / "a.mp4"
    video.write_bytes(b"fake video")
    return video


def upload(yt, monkeypatch, video, **kw):
    monkeypatch.setattr(m, "service", lambda: yt)
    args = dict(file=str(video), title="t", channel="UC1", description="", playlist="PL1") | kw
    return m.cmd_upload(argparse.Namespace(**args))


def inserted_bodies(yt):
    return [c.kwargs["body"] for c in yt.videos.return_value.insert.call_args_list]


def test_upload_is_private_and_added_to_playlist(env, monkeypatch):
    yt = fake_yt()
    r = upload(yt, monkeypatch, env)
    assert inserted_bodies(yt) == [{"snippet": {"title": "t", "description": ""}, "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}]
    assert r["playlists"] == ["PL1"] and r["duplicate"] is False


def test_rerun_does_not_upload_twice(env, monkeypatch):
    yt = fake_yt()
    upload(yt, monkeypatch, env, playlist=None)
    r = upload(yt, monkeypatch, env)  # 2 回目でプレイリスト追加だけ行う（失敗後の再開と同じ経路）
    assert len(inserted_bodies(yt)) == 1
    assert r["duplicate"] is True and r["playlists"] == ["PL1"]


def test_reuploads_when_recorded_video_was_deleted(env, monkeypatch):
    yt = fake_yt()
    upload(yt, monkeypatch, env, playlist=None)
    yt.videos.return_value.list.return_value.execute.side_effect = [{"items": []}, {"items": [{"status": {"privacyStatus": "private"}}]}]
    r = upload(yt, monkeypatch, env, playlist=None)
    assert len(inserted_bodies(yt)) == 2 and r["duplicate"] is False


def test_playlist_add_skipped_if_already_there(env, monkeypatch):
    yt = fake_yt()
    yt.playlistItems.return_value.list.return_value.execute.return_value = {"items": [{"id": "x"}]}
    r = upload(yt, monkeypatch, env)
    yt.playlistItems.return_value.insert.assert_not_called()
    assert r["playlists"] == ["PL1"]


def test_unexpected_error_still_returns_json(env, monkeypatch, capsys):
    monkeypatch.setattr(m, "service", lambda: (_ for _ in ()).throw(ConnectionResetError("secret-ish detail")))
    assert m.main(["upload", str(env), "--title", "t", "--channel", "UC1"]) == 1
    out = capsys.readouterr().out
    assert '"UNEXPECTED"' in out and "secret-ish" not in out


@pytest.mark.parametrize("kw", [dict(playlist_privacy="public"), dict(playlist_privacy="unlisted"), dict(playlist_owner="UC_OTHER")])
def test_bad_playlist_rejected_before_upload(env, monkeypatch, kw):
    yt = fake_yt(**kw)
    with pytest.raises(m.Fail, match="PL1"):
        upload(yt, monkeypatch, env)
    assert inserted_bodies(yt) == []


def test_channel_mismatch_rejected_before_upload(env, monkeypatch):
    yt = fake_yt()
    with pytest.raises(m.Fail) as e:
        upload(yt, monkeypatch, env, channel="UC_OTHER")
    assert e.value.code == "CHANNEL_MISMATCH" and inserted_bodies(yt) == []


def test_not_private_after_upload_is_failure(env, monkeypatch):
    yt = fake_yt(video_privacy="public")
    with pytest.raises(m.Fail) as e:
        upload(yt, monkeypatch, env)
    assert e.value.code == "PRIVACY_VERIFICATION_FAILED"
    yt.playlistItems.return_value.insert.assert_not_called()


def test_tampered_body_is_never_sent():
    body = m.video_body("t", "")
    body["status"]["publishAt"] = "2030-01-01T00:00:00Z"
    with pytest.raises(m.Fail, match="private"):
        m.insert_video(MagicMock(), "unused", body)


@pytest.mark.parametrize("title,desc", [("", ""), ("x" * 101, ""), ("a<b", ""), ("t", "x" * 5001)])
def test_invalid_metadata_rejected(title, desc):
    with pytest.raises(m.Fail):
        m.video_body(title, desc)


def test_cli_has_no_privacy_option(capsys):
    with pytest.raises(SystemExit):
        m.main(["upload", "a.mp4", "--title", "t", "--channel", "UC1", "--privacy", "public"])
    assert "unrecognized arguments" in capsys.readouterr().err


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

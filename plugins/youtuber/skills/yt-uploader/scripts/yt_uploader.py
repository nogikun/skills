#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["google-api-python-client>=2.100", "google-auth-oauthlib>=1.2"]
# ///
"""ローカル動画を YouTube に「非公開」でアップロードし、自分の非公開プレイリストに追加する CLI。

公開範囲は private 固定。引数・環境変数・JSON のどれからも変更できない。
出力は常に JSON（stdout）。進捗は stderr。失敗時は exit 1。
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

HOME = Path(os.environ.get("YT_UPLOADER_HOME", Path.home() / ".config" / "yt-uploader"))
SECRET = HOME / "client_secret.json"
TOKEN = HOME / "token.json"
LEDGER = HOME / "ledger.json"
# youtube.upload だけでは playlistItems.insert ができないため youtube スコープを使う
SCOPES = ["https://www.googleapis.com/auth/youtube"]
PRIVACY = "private"
# 送る status はこれだけ。子ども向けは常に「いいえ」（変えたいときは YouTube Studio で）
STATUS = {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False}


class Fail(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def write_private(path, text):
    """ディレクトリ 0700・ファイル 0600 で原子的に書く（途中で落ちても既存ファイルを壊さない）。"""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent)  # 0600、同時実行でも一時ファイルが衝突しない
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def load_ledger():
    return json.loads(LEDGER.read_text()) if LEDGER.exists() else {}


def record(key, rec):
    # アップロード中に別プロセスが書いた分を消さないよう、保存直前に読み直して 1 件だけ差し替える
    # shortcut: 読み直しと保存の間の競合は残る。同時アップロードが常態化したら fcntl でロックする
    ledger = load_ledger()
    ledger[key] = rec
    write_private(LEDGER, json.dumps(ledger, ensure_ascii=False, indent=2))


def service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not TOKEN.exists():
        raise Fail("AUTH_REQUIRED", "未認証です。ユーザー本人が `auth` を実行してください")
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except Exception as e:  # 例外本文にトークンが混ざる可能性を避けて型名だけ出す
            raise Fail("AUTH_REQUIRED", f"トークン更新に失敗（{type(e).__name__}）。`auth` をやり直してください")
        write_private(TOKEN, creds.to_json())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def my_channel(yt):
    items = yt.channels().list(part="id,snippet", mine=True).execute().get("items", [])
    if len(items) != 1:
        raise Fail("CHANNEL_UNKNOWN", f"認証アカウントのチャンネルを 1 つに特定できません（{len(items)} 件）")
    return {"channel_id": items[0]["id"], "channel_title": items[0]["snippet"]["title"]}


def private_playlists(yt):
    result, page = [], None
    while True:
        r = yt.playlists().list(
            part="id,snippet,status,contentDetails", mine=True, maxResults=50, pageToken=page
        ).execute()
        result += [
            {
                "id": p["id"],
                "title": p["snippet"]["title"],
                "description": p["snippet"].get("description", ""),
                "item_count": p.get("contentDetails", {}).get("itemCount"),
            }
            for p in r.get("items", [])
            if p.get("status", {}).get("privacyStatus") == PRIVACY
        ]
        page = r.get("nextPageToken")
        if not page:
            return result


def check_playlist(yt, playlist_id, channel_id):
    items = yt.playlists().list(part="snippet,status", id=playlist_id).execute().get("items", [])
    p = items[0] if items else None
    if not p or p["snippet"].get("channelId") != channel_id or p.get("status", {}).get("privacyStatus") != PRIVACY:
        raise Fail("PLAYLIST_REJECTED", f"{playlist_id} は自分の非公開プレイリストとして確認できません")


def video_body(title, description):
    if not title.strip() or len(title) > 100:
        raise Fail("INVALID_INPUT", "title は 1〜100 文字にしてください")
    if len(description.encode()) > 5000:
        raise Fail("INVALID_INPUT", "description は 5000 バイト以内にしてください")
    if any(c in title + description for c in "<>"):
        raise Fail("INVALID_INPUT", "title / description に < > は使えません")
    return {"snippet": {"title": title, "description": description}, "status": dict(STATUS)}


def insert_video(yt, path, body):
    from googleapiclient.http import MediaFileUpload

    # 送信直前の最終確認。status に private 以外の何か（publishAt 等）が入っていたら送らない
    if body.get("status") != STATUS:
        raise Fail("REJECTED_POLICY", "private 以外のリクエストは送信しません")
    media = MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, notifySubscribers=False, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk(num_retries=5)  # 5xx/429 は指数バックオフで再試行される
        if status:
            print(f"uploading {int(status.progress() * 100)}%", file=sys.stderr)
    return resp["id"]


def video_privacy(yt, video_id):
    """動画の公開範囲。見つからなければ None。"""
    items = yt.videos().list(part="status", id=video_id).execute().get("items", [])
    return items[0].get("status", {}).get("privacyStatus") if items else None


def verify_private(yt, video_id):
    if video_privacy(yt, video_id) != PRIVACY:
        raise Fail(
            "PRIVACY_VERIFICATION_FAILED",
            f"動画 {video_id} が非公開であることを確認できません。YouTube Studio で直ちに確認してください",
        )


def cmd_auth(_):
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not SECRET.exists():
        raise Fail("SETUP_REQUIRED", f"OAuth クライアント JSON（デスクトップアプリ）を {SECRET} に置いてください")
    creds = InstalledAppFlow.from_client_secrets_file(str(SECRET), SCOPES).run_local_server(port=0)
    write_private(TOKEN, creds.to_json())
    return my_channel(service())


def cmd_whoami(_):
    return my_channel(service())


def cmd_playlists(_):
    yt = service()
    return {**my_channel(yt), "playlists": private_playlists(yt)}


def cmd_upload(a):
    path = Path(a.file).expanduser().resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise Fail("INVALID_INPUT", f"ファイルが存在しないか空です: {path}")
    body = video_body(a.title, a.description)
    yt = service()
    channel = my_channel(yt)
    if channel["channel_id"] != a.channel:
        raise Fail("CHANNEL_MISMATCH", f"認証中のチャンネルは {channel['channel_id']} で、指定の {a.channel} と違います")
    if a.playlist:
        check_playlist(yt, a.playlist, a.channel)

    with path.open("rb") as f:
        key = f"{a.channel}/{hashlib.file_digest(f, 'sha256').hexdigest()}"
    rec = load_ledger().get(key)
    if rec and video_privacy(yt, rec["video_id"]) is None:
        rec = None  # 記録はあるが YouTube 側で削除済み。上げ直す
    duplicate = rec is not None
    if not duplicate:
        # shortcut: insert 応答の受信前に落ちると ledger に残らず再実行で重複しうる。頻発したら upload session URI を保存して再開する
        rec = {"file": str(path), "video_id": insert_video(yt, path, body), "playlists": []}
        record(key, rec)
    verify_private(yt, rec["video_id"])

    if a.playlist and a.playlist not in rec["playlists"]:
        check_playlist(yt, a.playlist, a.channel)  # 追加直前に再確認
        # 前回の追加が応答だけ失われていた場合に二重登録しない
        already = yt.playlistItems().list(part="id", playlistId=a.playlist, videoId=rec["video_id"]).execute()
        if not already.get("items"):
            yt.playlistItems().insert(
                part="snippet",
                body={"snippet": {"playlistId": a.playlist, "resourceId": {"kind": "youtube#video", "videoId": rec["video_id"]}}},
            ).execute()
        rec["playlists"].append(a.playlist)
        record(key, rec)

    return {
        **channel,
        "video_id": rec["video_id"],
        "url": f"https://youtu.be/{rec['video_id']}",
        "privacy_status": PRIVACY,
        "playlists": rec["playlists"],
        "duplicate": duplicate,
        "state": "VERIFIED_COMPLETE",
    }


def main(argv=None):
    p = argparse.ArgumentParser(prog="yt_uploader", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="OAuth 認証（ブラウザが開く。ユーザー本人が実行）").set_defaults(fn=cmd_auth)
    sub.add_parser("whoami", help="認証中のチャンネル").set_defaults(fn=cmd_whoami)
    sub.add_parser("playlists", help="自分の非公開プレイリスト一覧").set_defaults(fn=cmd_playlists)
    up = sub.add_parser("upload", help="非公開でアップロード（同じファイルの再実行は再開扱い）")
    up.add_argument("file")
    up.add_argument("--title", required=True)
    up.add_argument("--channel", required=True, help="ユーザーが確認したチャンネル ID")
    up.add_argument("--description", default="")
    up.add_argument("--playlist", help="追加先の非公開プレイリスト ID")
    up.set_defaults(fn=cmd_upload)
    a = p.parse_args(argv)

    from googleapiclient.errors import HttpError

    try:
        result = {"ok": True, **a.fn(a)}
    except Fail as e:
        result = {"ok": False, "error": e.code, "message": e.message}
    except HttpError as e:
        result = {"ok": False, "error": "API_ERROR", "status": e.status_code, "message": e.reason}
    except Exception as e:  # 通信断・壊れた ledger 等でも JSON を返す。本文はトークン混入を避けて出さない
        result = {"ok": False, "error": "UNEXPECTED", "message": type(e).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

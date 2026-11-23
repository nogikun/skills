---
name: yt-uploader-setup
description: yt-uploader（YouTube 非公開アップロード）を使える状態にする。Google Cloud の準備、OAuth クライアント JSON の配置、ログイン、動作確認までをユーザーと一緒に進める。「yt-uploader をセットアップして」「YouTube にログインしたい」「認証が切れた」「AUTH_REQUIRED / SETUP_REQUIRED が出た」「別のチャンネルでログインし直したい」と言われたら使う。アップロード自体は yt-uploader スキルが担当する。
---

# yt-uploader-setup

yt-uploader を使えるようにする。Google Cloud Console での作業とブラウザでのログインは **ユーザー本人** が行う。AI は状態を確認し、次にやることを 1 つずつ案内する。

コマンドはリポジトリのルートで実行する。

## 守ること

- `~/.config/yt-uploader/client_secret.json` と `token.json` の **中身を読まない・表示しない**。存在確認と下の検査コマンドだけにする。
- `auth` は AI が実行しない（ブラウザで同意するのはユーザー本人）。ユーザーに `!` 付きで打ってもらう。
- 手順は 1 つずつ示し、ユーザーが「できた」と言ってから次へ進む。

## 1. 現状を確認する

```bash
test -f ~/.config/yt-uploader/client_secret.json && echo secret:yes || echo secret:no
uv run skills/yt-uploader/scripts/yt_uploader.py whoami
```

- `whoami` が `"ok": true` → セットアップ済み。チャンネル名と ID を見せ、正しいチャンネルか確認して **5** へ。違うなら **4** をやり直す。
- `secret:no` → **2** から。
- `secret:yes` で `AUTH_REQUIRED` → **4** から。

## 2. Google Cloud の準備（ユーザーが行う）

次をユーザーに案内する。

1. https://console.cloud.google.com/ でプロジェクトを新規作成する（名前は自由）。
2. 「API とサービス」→「ライブラリ」で **YouTube Data API v3** を有効にする。
3. 「Google Auth Platform」（または「OAuth 同意画面」）で、対象を **外部（External）** にし、アプリ名とメールを入れ、**テストユーザー** に動画を上げるアカウントを追加する。
4. 「クライアント」→ 種類 **デスクトップアプリ** で作成し、JSON をダウンロードする。

画面の名前やメニューの位置は変わることがある。見つからないと言われたら、近い名前の項目を一緒に探す。

## 3. JSON を置く

ダウンロード済みのファイルを探す。

```bash
ls -t ~/Downloads/client_secret_*.json
```

候補をユーザーに見せ、どれを使うか確認してから移す。

```bash
mkdir -p ~/.config/yt-uploader
mv "<選んだファイル>" ~/.config/yt-uploader/client_secret.json
chmod 600 ~/.config/yt-uploader/client_secret.json
```

デスクトップアプリ用か、中身を表示せずに確認する（`installed` と出れば正しい。`web` ならデスクトップアプリで作り直してもらう）。

```bash
python3 -c 'import json,pathlib;print(*json.loads(pathlib.Path("~/.config/yt-uploader/client_secret.json").expanduser().read_text()).keys())'
```

## 4. ログイン（ユーザーが行う）

ユーザーに次を入力欄にそのまま打ってもらう。

```
! uv run skills/yt-uploader/scripts/yt_uploader.py auth
```

ブラウザでの流れを先に伝えておく。

1. テストユーザーに入れたアカウントを選ぶ。チャンネルが複数あれば、動画を上げるチャンネルを選ぶ。
2. 「Google はこのアプリを確認していません」→「詳細」→「（アプリ名）に移動」。自分で作ったアプリなので問題ない。
3. 「許可」を押す。

出力が `"ok": true` ならチャンネル名と ID を見せて、正しいか確認する。

## 5. 動作確認

```bash
uv run skills/yt-uploader/scripts/yt_uploader.py playlists
```

非公開プレイリストが一覧に出れば完了。0 件なら、YouTube で非公開プレイリストを作るか、プレイリストなしで使えることを伝える。

最後に、同意画面が「テスト中」のままだと **約 7 日でログインが切れる** ことがあると伝える。切れたら 4 をやり直す。避けたいなら同意画面で「アプリを公開」（本番）に切り替える（未確認アプリの警告は出続ける）。

## つまずいたとき

| 症状 | 原因と対応 |
|---|---|
| ブラウザで `access_denied` / 「アクセスをブロック」 | ログインしたアカウントがテストユーザーにない。2-3 で追加してから 4 |
| `API_ERROR` で `status: 403`、`accessNotConfigured` などが出る | YouTube Data API v3 が有効になっていない、または別プロジェクトの JSON を置いた。2-2 を確認 |
| `redirect_uri_mismatch` | クライアントの種類が「ウェブ」。デスクトップアプリで作り直して 3 から |
| `CHANNEL_UNKNOWN` | そのアカウントに YouTube チャンネルがない。YouTube でチャンネルを作るか、別アカウントで 4 |
| 数日後に `AUTH_REQUIRED` | テスト中のログイン期限切れ。4 をやり直す |
| 別のチャンネルに切り替えたい | 4 をやり直し、ブラウザで目的のチャンネルを選ぶ（token.json は上書きされる） |

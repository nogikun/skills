# セットアップと仕組み

## 構成

```
skills/planner/
├── SKILL.md
├── references/          この説明
└── tools/               画面（Vite + React）と CLI
    ├── cli.ts           AI が使う CLI（Node でそのまま実行）
    ├── server/          データファイルの読み書き（storeFile.ts）と開発サーバーの API（vitePlugin.ts）
    └── src/             画面と、画面・CLI で共通の計算（core.ts）
```

## データの流れ

- データは1つの JSON ファイル（`{ rev, store }`）。場所は `PLANNER_DATA`、なければ `~/.planner/store.json`。スキルのフォルダの外に置くので、スキルを移しても予定は残る。
- CLI は直接このファイルを読み書きする。画面は開発サーバーの `/api/store` 経由で読み書きする。
- 保存のたびに `rev` が 1 増える。古い `rev` からの保存は拒否される（画面には「最新の内容を表示しました」と出る）。
- 開発サーバーはファイルの変更を見張り、変わったら画面に知らせて最新を読み込ませる。AI が CLI で入れた予定が、画面を開いたまますぐに見える。
- 画面を開発サーバーなしで開いたとき（ビルドしたファイルを直接開くなど）は、ブラウザの localStorage だけで動く。
- 初めてファイルを使うとき、ファイルが空でブラウザに以前の予定があれば、それをファイルへ移す。

## 必要なもの

- Node 22.18 以降（CLI とフックは TypeScript をそのまま実行する）
- 画面を使うときだけ、初回に `npm --prefix tools install`

## フック

このリポジトリでは `.claude/settings.json` に次の2つを登録してある。どちらも `cli.ts hook …` を呼び、予定があるときだけ文脈を足す。

| イベント | 足すもの |
|---|---|
| SessionStart（起動・再開・クリア・要約のあと） | 今の時刻、今日の予定、空き時間、持ち越し候補 |
| UserPromptSubmit | 入力文が予定・計画・振り返りなどの話のときだけ、同じ内容 |

ほかのプロジェクトでも使うときは、スキルを `~/.claude/skills/planner` に置く（またはリンクする）。フックが要るときは `~/.claude/settings.json` に、パスをスキルの場所に合わせて同じ設定を書く。

```json
{
  "hooks": {
    "SessionStart": [{ "matcher": "startup|resume|clear|compact", "hooks": [{ "type": "command", "command": "node \"$HOME/.claude/skills/planner/tools/cli.ts\" hook session-start 2>/dev/null || true", "timeout": 10 }] }],
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "node \"$HOME/.claude/skills/planner/tools/cli.ts\" hook prompt 2>/dev/null || true", "timeout": 10 }] }]
  }
}
```

設定を足したあとは、新しいセッションから効く。

## うまく動かないとき

| 症状 | 確かめること |
|---|---|
| CLI が `Unknown file extension ".ts"` で止まる | Node が古い。`node -v` が 22.18 以上か |
| CLI で入れた予定が画面に出ない | 画面と CLI が同じファイルを見ているか（`cli.ts where` と、開発サーバーを起動したときの `PLANNER_DATA`）。開発サーバーが動いているか |
| 画面に「最新の内容を表示しました」と出る | 画面とほかの書き込みがぶつかった。画面の直前の操作をやり直す |
| フックの文脈が出ない | 新しいセッションか。データに予定があるか（空のときは何も出さない）。`echo '{"prompt":"今日の予定"}' \| node tools/cli.ts hook prompt` で出るか |
| データファイルが壊れた | `cli.ts` が「予定データの形式が正しくありません」と出す。画面の「JSONバックアップ」やブラウザの localStorage の控えから戻す |

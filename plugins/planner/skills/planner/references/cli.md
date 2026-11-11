# CLI リファレンス

目次: 共通 / 読む（brief・show・rules・csv・where） / 書く（add・update・remove・repeat・actual・range・status） / まとめて書く（plan） / フック用（hook） / エラー

## 共通

```bash
node ${CLAUDE_SKILL_DIR}/tools/cli.ts <command> [引数]
```

- Node 22.18 以降（TypeScript をそのまま実行する）。ビルドや `npm install` は不要。
- `<date>`: `today` `tomorrow` `yesterday` `+N` `-N` `YYYY-MM-DD`。AM 6:00 までは前日が「今日」。
- 時刻: 24時間の `HH:MM`。`00:00`〜`05:59` はその日の深夜（翌朝）。範囲は AM 6:00〜翌 AM 6:00。
- `<id>`: 予定の id。先頭4文字以上で、その日の中で一意なら省略できる。繰り返し予定のまだ確定していない日の id は `<ルールid>:<日付>` の形。
- データファイル: 環境変数 `PLANNER_DATA`、なければ `~/.planner/store.json`。
- 書き込むと版（rev）が 1 増える。画面と同時に書いてぶつかったときは読み直して最大3回やり直す。

## 読む

### brief [date]

今の時刻、その日の予定（id の先頭8文字つき）、空き時間、明日の予定、持ち越し候補（直近7日で完了していない予定）、繰り返しを文章で出す。計画の前に必ず見る。

### show <date> [--to <date>]

その日（`--to` があれば期間の各日）を JSON で出す。

```json
{
  "date": "2026-11-11", "weekday": "水", "status": "draft",
  "range": { "start": "09:00", "end": "18:00" },
  "activities": [
    {
      "id": "46050222-…", "title": "レビュー", "memo": "",
      "start": "10:30", "end": "11:30", "label": "AM 10:30〜11:30", "minutes": 60,
      "actual": { "start": "11:00", "end": "12:00", "minutes": 60, "diff": 0 },
      "status": "done", "repeat": true
    }
  ]
}
```

`actual` は実績がなければ `null`。`diff` は実績時間 − 予定時間（分）。`repeat` は繰り返し予定のときだけ出る。`status` は日の状態（`draft` 下書き / `planned` 計画確定 / `reviewed` 振り返り済）。

### rules

繰り返しの一覧（`weekdays` は `月水` のような文字）。

### csv [--from <date>] [--to <date>]

予定・実績・差分の CSV。

### where

データファイルの場所と画面の URL。

## 書く

どれも、書いたあとのその日を `show` と同じ形で返す。

| コマンド | 説明 |
|---|---|
| `add <date> --title T --start HH:MM --end HH:MM [--memo M] [--repeat 曜日]` | 1件足す。`{ "added": id, "day": … }` |
| `update <date> <id> [--title] [--memo] [--start] [--end]` | 直す。繰り返し予定なら、まだ確定していない日の繰り返しにも反映 |
| `remove <date> <id> [--series]` | 消す。繰り返し予定は既定でこの日だけ、`--series` で繰り返しごと |
| `repeat <date> <id> <曜日>` | 繰り返しを設定。`none` でやめる（この日の予定は単発で残る） |
| `actual <date> <id> --start --end [--status done\|partial\|skipped] [--shift]` | 実績を記録。`--status` の既定は `done`。`--shift` で、終了がずれた分だけ後続の実績をずらす（`{ "shiftedFollowing": 件数, "day": … }`） |
| `range <date> --start --end` | その日の時間帯（空き時間の計算と画面の目安に使う） |
| `status <date> draft\|planned\|reviewed` | 日の状態 |

曜日の書き方: `月水` / `1,3`（0=日〜6=土） / `daily` `毎日` / `weekdays` `平日` / `none` `なし`。

## まとめて書く（plan）

```bash
node cli.ts plan <date> --file <path|-> [--dry-run] [--replace]
```

計画の JSON:

```json
{
  "range": { "start": "09:00", "end": "18:00" },
  "replace": false,
  "activities": [
    { "title": "設計レビュー", "start": "09:30", "end": "10:30", "memo": "任意", "repeat": "任意（曜日）" }
  ]
}
```

- `range` と `replace` は省略できる。`replace` は `--replace` と同じ。
- 置き換え（replace）で消えるのは、実績を記録していない単発の予定だけ。
- 返す値:

```json
{
  "date": "2026-11-11", "dryRun": true,
  "remove": ["AM 9:00〜10:00 朝の作業"],
  "add": ["AM 9:30〜10:30 設計レビュー"],
  "warnings": ["時間が重なっています: 「A」と「B」（AM 10:00）"]
}
```

`--dry-run` でないときは `day`（書いたあとのその日）も付く。`warnings` は止めずに知らせるだけ（重なりは画面で横に並べて表示される）。

## フック用（hook）

`hook session-start` と `hook prompt` は Claude Code のフックから呼ぶ。標準入力のフックの JSON を読み、予定があるときだけ `brief` と同じ内容を出す。`prompt` は入力文が予定・計画・振り返りなどの話のときだけ出す。失敗しても何も出さずに終了コード 0 で終わる（ユーザーの入力を止めないため）。

## エラー

誤りは標準エラーに `エラー: …` と出して終了コード 1。id が見つからないときは、その日の予定の一覧も出すので、そこから正しい id を選ぶ。計画の誤りは件ごとにまとめて出し、何も書かない。

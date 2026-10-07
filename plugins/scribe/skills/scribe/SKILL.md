---
name: scribe
license: Apache-2.0
description: 会議の動画・音声ファイル (mp4/mov/mkv/webm/mp3/m4a/wav/flac/ogg など、パスを渡すだけ) を話者分離つきで日本語文字起こしし、話者に名前を付けて議事録 (Markdown) や字幕 (WebVTT / SRT)、テキスト、JSON に書き出すスキル。同梱の Scribe CLI (NVIDIA Nemotron 3 Diarization + faster-whisper) を uv で実行する。「会議を文字起こしして」「この mp4/m4a/wav を書き起こして」「誰が何を話したか分けて」「議事録の元データを作って」「字幕 (vtt/srt) を作って」「話者の名前を付け直して」「さっきの議事録の話者名が違うので直して」「前回のジョブを書き出して」といった依頼では、ファイル形式や『Scribe』という名前が出ていなくても必ずこのスキルを使うこと。Zoom/Teams/Meet の録画、インタビュー、打ち合わせ音声の話者識別・話者分離の確認や、文字起こしジョブの再開・状態確認・一覧にも使う。
---

# Scribe: 会議の話者分離つき文字起こし

本スキルと同梱CLIは [Apache License 2.0](LICENSE) で提供する。モデルと依存ライブラリの条件は [第三者ライセンス](THIRD_PARTY_NOTICES.md) を参照。

同梱の CLI が重い処理 (音声抽出 → 話者分離 → 文字起こし → 統合 → 話者サンプル抽出) を全部やる。
このスキルの役目は **CLI を正しく呼び、話者の名前付けだけ人間に確認する** こと。誰の声かを AI が推測で確定させないのが設計思想 (Human-in-the-loop)。間違った名前が議事録に残ると、後から気づくのが難しいため。

## CLI の呼び方

CLI はこのスキルのディレクトリにある `tools/scribe` (uv プロジェクト)。どこからでも次の形で呼ぶ:

```bash
uv run --project <このスキルのディレクトリ>/tools/scribe scribe <command> ...
```

以下 `scribe ...` はこの形の省略。

- **stdout は結果のみ、stderr はログと進捗バー**。必ず `--json` を付けて stdout を JSON として読む。
- 終了コード: `0` 成功 / `1` 一般エラー (`job_busy` 等) / `2` 引数エラー / `3` 処理失敗 / `4` **話者の命名待ち (エラーではない)**。
- エラー時も stdout に `{"error": "<code>", "message": "..."}` が出る。
- PowerShell では `<job>` のような山括弧をそのまま打つと構文エラーになる。必ず実際の値に置き換える。

## 初回セットアップ

`scribe` が動かない、または初めて使う環境なら、まず依存を入れる。GPU の有無で選ぶ (`nvidia-smi` が通れば GPU あり):

```bash
uv sync --project <skill>/tools/scribe --extra nemotron --extra cu128   # Windows/Linux + NVIDIA GPU
uv sync --project <skill>/tools/scribe --extra nemotron --extra cpu     # Windows/Linux, GPU なし
uv sync --project <skill>/tools/scribe --extra nemotron                 # macOS
```

`cpu` と `cu128` は同時に指定できない。動画・音声のデコードは同梱の PyAV で行うので ffmpeg のインストールは不要 (PATH にあれば、壊れ気味のファイルの予備デコーダとして使われる)。モデルは初回に Hugging Face キャッシュ (`~/.cache/huggingface`) へ自動ダウンロードされ、端末内で共有される。

## 基本の流れ

### 1. 処理する

```bash
scribe process "<入力ファイル>" --job <分かりやすいID> --json
```

- 入力はパスを渡すだけ。拡張子を見て 16kHz モノラル WAV に変換してから推論する。音声 (wav/mp3/m4a/aac/flac/ogg/opus/wma …) も動画 (mp4/mov/mkv/webm/avi/wmv/ts …) も可。動画は音声トラックだけを使う。未知の拡張子でも中身がメディアなら読める。URL や YouTube などは非対応なので、先にファイルとして保存してもらう。
- `--job` で ID を付けておくと後の操作が楽 (例: `weekly-1008`)。省略すると自動採番され、出力の `job_id` に入る。
- 49 分の会議で GPU なら約 3 分、CPU なら 10 分前後かかる。**長くなりうるのでバックグラウンド実行し、stderr の進捗を見て待つ**。途中で止まっても `scribe process --job <ID>` で続きから再開できる (完了済みステージはキャッシュされる)。
- 参加人数が分かっていても、既定の話者分離 (Nemotron) は人数を自動で決める。最大 8 人。

### 2. 結果の分岐

| 終了コード / status | 意味 | 次にやること |
|---|---|---|
| `4` / `speaker_identification_required` | 処理は成功。話者に名前が無い | 手順 3 |
| `0` / `ready` | 全員に名前がある | 手順 4 |
| `3` / `failed` | 処理失敗 | 下の「トラブル対応」 |

### 3. 話者に名前を付ける (人間に確認する)

`process` の JSON (または `scribe speakers <ID> --json`) の `speakers` に、話者ごとの情報がある:

- `speech_seconds`: 発話時間 (多い順に聞くと効率がよい)
- `sample_audio`: 5〜10 秒の代表音声 WAV のパス (人が再生して聞く用)
- `sample_text`: その代表音声で話している内容

ユーザーにはこう提示する: 話者ごとに `sample_text` と `sample_audio` のパスと発話時間を並べ、「この声は誰ですか？」と聞く。会話の流れから推測できる手がかり (例: 「中村君、説明お願い」の直後に答えた人) があれば **候補として** 添えてよいが、確定はユーザーの返答で行う。発話時間が数秒しかない話者は相づちや誤検出のこともあるので、そう伝えて「不明のままでよいか」も選べるようにする。

返答をもらったらまとめて登録する:

```bash
scribe speaker rename --job <ID> SPEAKER_00=田中 SPEAKER_01=佐藤 --json
```

(1 人だけなら `scribe speaker set --job <ID> --speaker SPEAKER_00 --name 田中 --json` でもよい。空の名前 `SPEAKER_00=` は名前の取り消し。)

ユーザーが自分で音声を聞きながら付けたい場合は、ユーザーの端末で次を実行してもらう。代表音声を順に再生し、名前を入力していく対話ツール (AI からは端末入力ができないので実行しない):

```bash
scribe speaker edit --job <ID>
```

全員に名前が付くと status が `ready` になる。同じ人が複数の SPEAKER に分かれていたら (sherpa で起きやすい)、同じ名前を付ければ書き出し時に 1 人として扱われる。名前を付けずに書き出すこともできる (その場合 `SPEAKER_00` 表記)。ユーザーが急いでいる・名前は不要と言う場合は、確認を省いてそのまま手順 4 へ進んでよい。

自動実行 (ユーザーに質問できない状況) では、名前は付けずに書き出し、話者一覧 (`sample_text` と `sample_audio`) を報告に含めて、後から `speaker set` で名前を入れられることを伝える。

### 4. 書き出す

```bash
scribe export <ID> --format markdown -o "<出力パス>.md" --json
```

| `--format` | 用途 |
|---|---|
| `markdown` | 読む用の議事録素材。同じ人の連続発言をまとめ、`## 00:02:00 — 田中` の見出し付き |
| `vtt` / `webvtt` | WebVTT 字幕。話者は `<v 田中>` タグ |
| `srt` | SRT 字幕 |
| `txt` | `[00:02:00] 田中: ...` の 1 行 1 発言 |
| `json` | 機械処理用 (話者一覧 + 区間ごとの speaker_id / speaker_name / start / end / text) |

`-o` を省くと stdout に本文が出る。

### 5. 後から話者名を直す

「さっきの議事録、SPEAKER_01 は佐藤さんじゃなくて鈴木さんだった」のような修正は、ジョブに名前を入れ直すだけでよい。`speaker rename` / `set` / `edit` は **以前 `-o` で書き出したファイルを新しい名前で自動的に書き直す** (結果 JSON の `refreshed` に書き直したパスが入る)。ジョブ ID が分からなければ `scribe jobs --json` の `input` (元ファイル) と `created_at` から探す。

```bash
scribe jobs --json
scribe speaker rename --job <ID> SPEAKER_01=鈴木 --json   # refreshed のファイルが更新済み
```

`refreshed` が空なら、まだファイルに書き出していない (または stdout に出しただけ) なので、手順 4 で書き出す。

注意: 直せるのは **話者の名前** (SPEAKER 番号 → 名前の対応)。「この 1 発言だけ別の人」という発言単位の付け替えはできない。話者分離そのものが合っていない場合は `--diarizer` を変えて `process --job <ID>` で再実行する。

書き出したファイルを渡すときは、話者数・各人の発話時間・未命名の話者の有無を一言添える。会議の録音や文字起こしは機密になりやすいので、git 管理下に出力するならコミット対象外の場所 (例: `.gitignore` 済みのフォルダ) を選ぶ。

## その他のコマンド

```bash
scribe jobs --json                 # ジョブ一覧 (新しい順)。ID を忘れたとき
scribe status <ID> --json          # 状態とステージごとの詳細 (どこで失敗したか)
scribe speakers <ID> --json        # 話者一覧 (名前、発話時間、sample_audio, sample_text)
```

`process` の主なオプション (再開時に省略すれば前回の値を引き継ぐ):

| オプション | 既定 | 説明 |
|---|---|---|
| `--diarizer` | `nemotron,pyannote,sherpa` | 話者分離のフォールバック順。使えないものは自動で飛ばす |
| `--asr` | `faster-whisper` | 文字起こし |
| `--model` | `small` | Whisper のサイズ。誤認識が多いときは `medium` (遅くなる) |
| `--language` | `ja` | 言語 |
| `--device` | `auto` | `auto` は GPU → CPU の順に試す |
| `--num-speakers` | なし | 人数が分かっている場合 (sherpa / pyannote のみ有効) |
| `--cluster-threshold` | `1.0` | sherpa の話者のまとまりやすさ。大きいほど人数が減る |

オプションを変えて `process --job <ID>` を再実行すると、影響を受けるステージだけやり直す。例えば `--model medium` なら文字起こし以降だけ再計算され、話者分離はそのまま使われる。話者分離をやり直した場合は SPEAKER 番号が振り直されるため、付けた名前は自動で消える (別人の名前が付くのを防ぐため)。改めて名前を聞くこと。

## トラブル対応

`error` コードで判断する:

| error | 原因と対処 |
|---|---|
| `input_not_found` | パス違い。引用符で囲んでいるか、存在するか確認 |
| `invalid_input` | 音声トラックが無いファイル (画面録画で音声なし等)。別のファイルか確認 |
| `decode_failed` | 壊れたファイル等で PyAV も ffmpeg も読めない。ffmpeg を入れる (Windows: `winget install ffmpeg`) と通ることがある |
| `not_interactive` | `speaker edit` を AI から実行した。`speaker rename` を使う |
| `job_busy` | 同じジョブを別プロセスが処理中。終わるのを待つ |
| `model_not_cached` | `HF_HUB_OFFLINE=1` でモデル未取得。一度オンラインで実行する |
| `backend_failed` | 全 Backend が失敗。`message` に各 Backend の理由が並ぶ。`not installed` なら上の `uv sync` をやり直す |
| `job_not_found` | ID 違い。`scribe jobs` で確認 |

話者分離の結果が怪しいとき (人数が多すぎる・少なすぎる) は、`status` の `stages.diarize.backend` を見る。`sherpa/cpu` なら Nemotron が使えていない (extra 未導入など) ので、セットアップをやり直して `process --job <ID> --diarizer nemotron` で再実行するのが一番効く。

## 環境変数

| 変数 | 用途 |
|---|---|
| `SCRIBE_HOME` | ジョブの保存先 (既定 `~/.scribe`)。ジョブは全コピー共通 |
| `HF_HOME` | モデルキャッシュの場所 |
| `HF_HUB_OFFLINE=1` | ダウンロードせずキャッシュのみ使う |
| `HF_TOKEN` | pyannote を使う場合のみ |

設計の詳細はリポジトリの `docs/spec/Scribe_redesign.md`、ツール単体の説明は `tools/scribe/README.md` にある。

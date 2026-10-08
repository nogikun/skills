# scribe

会議の動画/音声 → 話者分離 + 日本語文字起こし CLI。設計は [docs/spec/Scribe_redesign.md](../../../../docs/spec/Scribe_redesign.md)。

## インストール

話者分離は NVIDIA Nemotron 3 Diarization (推奨) → pyannote → sherpa-onnx の順に自動フォールバックする。

```bash
uv sync --extra nemotron --extra cu128   # Windows/Linux + NVIDIA GPU (推奨)
uv sync --extra nemotron --extra cpu     # Windows/Linux, GPU なし (49 分の音声で約 1 分, 最大 ~3.4GB)
uv sync --extra nemotron                 # macOS
uv sync                                  # 最小構成 (torch なし, sherpa-onnx のみ)
```

`cpu` と `cu128` は同時に指定できない。pyannote を足す場合は `--extra pyannote` (要 `HF_TOKEN`)。

## 使い方

```bash
uv run scribe process meeting.mp4 --json          # exit 4 = 話者の命名待ち
uv run scribe speakers <job> --json               # sample_audio を人に聞かせる
uv run scribe speaker edit --job <job>            # 代表音声を聞きながら対話で名前付け
uv run scribe speaker rename --job <job> SPEAKER_00=田中 SPEAKER_01=佐藤
uv run scribe export <job> --format markdown      # json|markdown|txt|srt|vtt(webvtt), -o FILE
uv run scribe jobs                                # ジョブ一覧 (新しい順)
uv run scribe process --job <job>                 # 中断したジョブを再開
uv run scribe serve                               # 進捗モニタと話者エディタ (http://127.0.0.1:8765/)
uv run pytest
```

入力はパスだけ渡せばよい。音声 (wav/mp3/m4a/flac/ogg/opus …) も動画 (mp4/mov/mkv/webm/avi …) も PyAV で 16kHz モノラル WAV に変換してから推論する。

話者名を変えると、以前 `-o` で書き出したファイルも自動で書き直される。

進捗は stderr に常に表示される (stdout は結果のみ)。

| 環境変数 | 用途 |
|---|---|
| `HF_HOME` | モデルキャッシュ (既定 `~/.cache/huggingface`)。端末内の全コピーで共有 |
| `HF_HUB_OFFLINE=1` | ダウンロードせずキャッシュのみ使用 |
| `HF_TOKEN` | pyannote 用 (無ければスキップ) |
| `SCRIBE_HOME` | ジョブ保存先 (既定 `~/.scribe`) |
| `SCRIBE_SHERPA_EMBEDDING` | sherpa の話者埋め込みモデル名 (csukuangfj/speaker-embedding-models 内) |

ffmpeg は不要 (PATH にあれば読めないファイルの予備デコーダとして使う)。

スキルとしての使い方は [../../SKILL.md](../../SKILL.md)。

## ライセンス

Copyright 2026 nogikun

本CLIは [Apache License 2.0](LICENSE) で提供します。利用するモデルと依存ライブラリには、それぞれのライセンスが適用されます。詳細は [第三者ライセンス](../../THIRD_PARTY_NOTICES.md) を参照してください。

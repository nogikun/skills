# skills

nogikun が作った [Agent Skills](https://code.claude.com/docs/en/skills) を 1 コマンドで入るようにまとめた repo。
Claude Code / Cursor など SKILL.md を読むエージェントで使える。

**ここは配布用のミラー。** skill の中身は下の表の「配布元」が正で、この repo の `skills/` は
[週次の workflow](.github/workflows/sync-upstream-skills.yml) がそこから同期している。
配布元は決め打ちしていない。`nogikun` の public repo で `skills/<name>/SKILL.md` を
持つものを自動で拾うので、skill repo を新しく作れば次の同期から勝手に載る。
**repo 1 つが plugin 1 つ**で、1 つの repo に skill が複数あればまとめて 1 plugin になる。

不具合の報告・修正の PR は配布元の repo へ。ここに出しても次の同期で消える。

## 入れる

```console
npx skills add nogikun/skills
```

個別に入れるなら skill 名を足す。

```console
npx skills add nogikun/skills -s html-deck
```

プラグインとして入れることもできる。`.claude-plugin/marketplace.json` は
Claude Code と Codex の両方が読む形式なので、マニフェストは 1 つで足りる。
plugin は配布元の repo ごとに分かれている (`claat` / `html-deck` / `usable-xlsm`)。

```console
codex plugin marketplace add https://github.com/nogikun/skills
codex plugin add html-deck@nogikun
```

```console
/plugin marketplace add nogikun/skills
/plugin install html-deck@nogikun
```

## 中身

| skill | 何をするか | 配布元 | 追加で要るもの |
| --- | --- | --- | --- |
| [html-deck](plugins/html-deck/skills/html-deck) | メモ・記事・議事録から HTML スライドを作り、1600x900 の実測検査と批評エージェントの差し戻しループで読めるところまで仕上げる。PDF / PPTX 書き出しと、ブラウザ上で指摘を返すレビューモードつき | [nogikun/html-deck](https://github.com/nogikun/html-deck) | [uv](https://docs.astral.sh/uv/), Chrome (PPTX は Node.js) |
| [claat-creator](plugins/claat/skills/claat-creator) | 下の 2 つを束ねる入口。素材から手順書を書き、lint を通し、HTML にするまでを進行させる | [nogikun/claat](https://github.com/nogikun/claat) | 下の 2 つと同じ |
| [claat-writer](plugins/claat/skills/claat-writer) | プロジェクトの中身から [Google Codelabs](https://github.com/googlecodelabs/tools) 形式の `manual.md` を日本語で書き起こす | [nogikun/claat](https://github.com/nogikun/claat) | — |
| [usable-xlsm](plugins/usable-xlsm/skills/usable-xlsm) | マクロ付きブック (`.xlsm` / `.xlsb` / `.xltm`) の VBA を解析・編集し、専用の Windows Excel ワーカー上で実際に走らせて回帰テストしてから反映する | [nogikun/usable-xlsm](https://github.com/nogikun/usable-xlsm) | Windows, Excel, [uv](https://docs.astral.sh/uv/) |
| [claat-build](plugins/claat/skills/claat-build) | `manual.md` を lint して Codelabs の HTML を生成する | [nogikun/claat](https://github.com/nogikun/claat) | [Go](https://go.dev/), [claat](https://github.com/googlecodelabs/tools/tree/main/claat) |

## ライセンス

MIT。ただし各 skill のライセンスは開発元の repo に従う。`plugins/html-deck/skills/html-deck/vendor/draw-io/` だけは
[little-hands/claude-drawio-skill](https://github.com/little-hands/claude-drawio-skill) を
無改変で同梱したもので、同じく MIT (Copyright (c) 2025 little-hands)。
経緯は [VENDOR.md](plugins/html-deck/skills/html-deck/vendor/draw-io/VENDOR.md) にある。

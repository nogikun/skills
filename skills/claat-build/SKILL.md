---
name: claat-build
description: claat 形式の Markdown（manual.md など）を入力として、Go CLI を `go run github.com/nogikun/claat/cmd/claat-tools@v0.1.0` で実行し、lint を通してから Google Codelabs 形式の HTML を生成する。lint 診断（META001〜ASSET001）を読み解いて、機械的に直せるものは直し、内容の判断が要るものは書き手に返す。ユーザーが「この manual.md をビルドして」「HTML にして」「codelab を生成して」「claat export を回して」「lint を通して」「手順書を変換して」と言ったとき、あるいは claat 形式の .md ファイルを渡して変換・検証・公開準備を求めたときは必ず使うこと。`claat` が PATH に無い、`Duration` が不正、画像が見つからないといったビルド失敗の原因調査にも使う。手順書そのものを書き起こす工程は claat-writer skill が担当する。
---

# claat 手順書ビルダー

`manual.md` を lint し、通ったら HTML を生成する。lint が通らないまま HTML を作ることはできない設計なので、この skill の仕事の大半は**診断を読んで直すこと**になる。

## 前提を確認する

このツールは 2 段構えで動く。`claat-tools` が入力を検査し、検査に通ったら PATH 上の `claat` に HTML 生成を委譲する。したがって必要なものは 2 つある。

```console
go version
claat version
```

`claat` が無ければ、検証済みバージョンを入れる。

```console
go install github.com/googlecodelabs/tools/claat@v0.0.0-20240220115335-873fe39d02dc
```

## 実行する

CLI は公開タグから直接実行する。**作業ディレクトリはどこでもよい。** 初回だけ Go がモジュールを取りに行く。
claat 本体を開発していて手元の変更を試したいときだけ、このリポジトリのルートで `./cmd/claat-tools` に読み替える。

```console
go run github.com/nogikun/claat/cmd/claat-tools@v0.1.0 lint path/to/manual.md
go run github.com/nogikun/claat/cmd/claat-tools@v0.1.0 build -output output path/to/manual.md
```

`build` は内部で同じ lint を先に回す。だが最初は `lint` を単独で回すこと。`build` は lint に落ちた時点で止まるので、診断を先に全部見てまとめて直したほうが往復が減る。

## 終了コードの意味

| コード | 意味 | 次にやること |
| --- | --- | --- |
| 0 | 成功 | 生成物を確認して報告する |
| 1 | 入力の lint エラー | 診断を読んで直す（下記） |
| 2 | 実行環境の問題 | `claat` が PATH に無い、引数が不正、ファイルが読めない、UTF-8 でない |
| その他 | `claat` 自身の失敗 | `claat` の標準エラー出力をそのまま読む |

1 と 2 を混同しない。1 は文書を直す話で、2 は環境を直す話になる。

## 診断を読んで直す

診断は `path:line:column: error CODE message` の形で標準エラーに出る。

```text
dev/manual.md:12:1: error STEP002 expected `Duration: H:M:SS` immediately after the step heading
dev/manual.md:48:5: error ASSET001 local image not found: images/setup.png
```

**直し方の原則: lint を黙らせるために内容を消さない。** `ASSET001` を消すために `![...]()` の行を削るのが最も安易で最も損な直し方になる。読者はその画像を必要としていたから、そこに置かれていた。エラーは「画像を用意しろ」という指示であって「参照をやめろ」ではない。

| コード | 意味 | 判断 |
| --- | --- | --- |
| `META001` | 必須メタデータが無い | 値を決められるものだけ埋める（`status` は `Draft`）。`summary` `feedback link` `analytics account` は勝手に決めず聞く |
| `META002` | 未知キー、または重複キー | 直す。7 キー（`summary` `id` `categories` `environments` `status` `feedback link` `analytics account`）以外は使えない。重複は片方を消す前に、どちらの値を残すか確認する |
| `META003` | 値が不正 | 直す。`id` は `[A-Za-z0-9][A-Za-z0-9_-]*` に正規化、`environments` は `Web` / `Kiosk`、`status` は `Draft` / `Published` / `Deprecated` / `Hidden` |
| `DOC001` | `#` タイトルが無い / 複数ある / 位置が不正 | 直す。2 つ目以降の `#` はたいてい `##` の書き間違いなので降格させる。ただし降格すると手順が 1 つ増えるので、直した内容を報告する |
| `STEP001` | `##` が無い、または見出しレベルが不正 | 直す。`#####` `######` は `####` までに繰り上げる |
| `STEP002` | `Duration` が無い / 位置が不正 / 形式が不正 | 直す。`H:M:SS` の 3 区切り必須で `5:00` は通らない。`##` の直後の最初の非空行に置く。値が無い手順は、内容から所要時間を見積もって提案し、確認をとる |
| `STEP003` | 手順の本文が空 | **書き手に返す。** ただし本文がコードブロックだけの場合は lint の仕様であり（フェンス内は本文と数えない）、コードの前に地の文を 1 行足せば直る |
| `MD001` | コードフェンスが閉じていない | 直す。どこで閉じるべきかは前後の文脈で判断する。開始行が診断に出るので、そこから読む |
| `ASSET001` | 相対画像が存在しない | **参照を消さない。** パスの綴り違いなら直す。ファイル自体が無ければ、どの画面を撮って `images/` のどこに置いてほしいかを添えてユーザーに依頼する |

本文を大きく書き足す必要が出たら、それは変換ではなく執筆の仕事になる。`claat-writer` skill に戻して、日本語チェックまで含めて回す。

## 生成物を確認する

`build` が成功すると `claat` が `ok<TAB><id>` を出し、出力はメタデータの `id` を使った名前で作られる。

```text
output/
└── <id>/
    ├── index.html
    └── codelab.json
```

出力先の既定は `output`。`-output`（短縮 `-o`）で変えられる。**既存ファイルは消されない**ので、`id` を変えて作り直すと古いディレクトリが残る。不要なら消すのは実行した側の責任になる。

生成後は `index.html` が実際にできていることを確認してから報告する。lint が通っても `claat` 側で失敗すれば終了コードは `claat` のものになる。

ローカルで見た目を確認したい場合は `claat serve` が使える。

## 報告する

ユーザーには次を伝える。

- 生成された `index.html` のパス
- lint で直した箇所（何を、なぜ直したか）
- 直さずに残した箇所と、ユーザーにお願いしたいこと（画像の配置、`summary` の文言など）

頼まれていなければ、生成物のコミットも公開もしない。

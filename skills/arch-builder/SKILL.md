---
name: arch-builder
description: AWS の構成図 (ブロック図) を、編集できる draw.io ファイル (.drawio) として設計・作図し、AWS 公式アイコンとグループ規約 (AWS Cloud ⊃ Region ⊃ VPC ⊃ AZ ⊃ Subnet) と構成の定石 (DB を private に置く、Multi-AZ、NAT の置き場所など) をスクリプトで検証し、PNG を別モデルに批評させる差し戻しループで仕上げる。「AWS の構成図を作って」「アーキテクチャ図」「インフラ構成図」「システム構成図」「この構成を図にして」「〜なアーキテクチャを設計して図にして」「drawio で AWS」と言われたら、形式が指定されていなくてもこのスキルを使う。既存の .drawio の AWS 図の手直し・規約チェック・レビュー (「この構成図おかしくない?」「AWS のお作法に沿ってる?」) や、AWS アイコンが読めない・足りないという相談にも使う。AWS 以外の汎用フローチャートや、AWS が出てこないネットワーク図は drawio スキルに回す。
---

# Arch Builder — AWS 構成図

**正本は `arch.yaml`**。.drawio はそこから生成するもので、draw.io で手直ししたら
`arch import` で正本に戻す。正本を持つのは、差分が読めて、スクリプトで編集・検証でき、
何度作り直しても同じ図が出るようにするため。

コードは `tools/` の uv プロジェクトにある。**呼び出しは常にこの形にする**
(`<SKILL>` はこの SKILL.md のあるディレクトリ。空白を含むことがあるので引用符で囲む)。

```bash
uv run --project "<SKILL>/tools" arch <サブコマンド> ...
```

素の `python3` は使わない。Nix の環境では `python3` が自分自身を起動し続けて止まることがある。
依存は `tools/uv.lock` で固定してあり、初回の実行時に `tools/.venv` へ入る。

## 入口

| 言われたこと | 行き先 |
| --- | --- |
| 「〜の構成図を作って」「こういうアーキテクチャを設計して」 | 手順0から |
| 既存の `.drawio` を渡されて「直して」「規約に沿ってるか見て」 | 手順0 → `arch import` → 手順4から |
| 「アイコンが出ない」「新しいアイコン ZIP を入れて」 | 手順0だけ |
| 「この部分だけ変えて」(直前に作った図) | `arch edit` → 手順4から |

## 0. アイコンが読めるか確かめる (毎回、最初に1回)

```bash
uv run --project "<SKILL>/tools" arch doctor
```

アイコンは **AWS 公式 Asset Package の SVG を正本**とする。draw.io 標準の `mxgraph.aws4` は使わない
(更新が遅れて古い意匠が混ざり、規約チェックで公式かどうかを判定できないため)。
ライブラリは `<SKILL>/icons/current/` に置く (git 管理外。`$ARCH_ICON_LIB` か `--lib` で差し替えられる)。

doctor が `NG` を出したら、**作図に進まない**。

- ZIP の候補が表示されたら、それを取り込んでよいかをユーザーに1行で確認してから、
  `arch icons build '<zip>'` を実行する (vendor の aws-drawio-import が変換する)
- 候補が無ければ、ユーザーに次のように頼んで止まる:
  「AWS 公式アイコン (Asset Package の ZIP) が必要です。https://aws.amazon.com/architecture/icons/ から
  ダウンロードして、ZIP のパスを教えてください」

draw.io Desktop が無くても作図はできる (PNG の代わりに近似 SVG で確認する)。その場合は、そのことを報告に書く。

doctor は取り込んだ **Asset Package の日付**も出す。報告に書く。Asset Package は四半期ごとに更新されるので、
古いという注意が出たら、新しい版が出ていないかをユーザーに1行で伝える (作図は止めない)。

## 1. 要件を固める (確認は1回だけ)

依頼文から次の表を自分で埋める。**埋まらないものだけ** `AskUserQuestion` で、**1回にまとめて**聞く (最大4問)。
選択肢は依頼から作った具体的な案にして、推奨を先頭に置く。

| 項目 | 例 | 聞かずに決めてよい既定 |
| --- | --- | --- |
| 何の図か・誰が見るか | 設計レビュー用 / 提案資料用 / 運用手順用 | 設計レビュー |
| 環境と可用性 | 本番 (2 AZ) / 検証 (1 AZ) | 本番 = 2 AZ |
| どこまで描くか | subnet まで / VPC まで / サービス同士の関係だけ | subnet まで |
| 必須のサービス・制約 | 「ECS on Fargate」「オンプレと VPN」 | 依頼にあるものだけ |
| リージョン | ap-northeast-1 | ap-northeast-1 |
| 利用者の入り方 | インターネット公開 / 社内限定 (VPN・Direct Connect) | **決めない** (下を参照) |
| 仕上げの厳しさ | 標準 / 厳しめ | 標準 |

**依頼に無い前提を黙って確定しない。** 特に利用者の入り方 (インターネット公開か社内限定か) は、
セキュリティの形がまるごと変わる。聞けないとき (聞く回数を使い切った、ユーザーがいない) は、
もっともらしい方で描いたうえで、**図の中に前提として書く** (`notes:`。spec.md) し、報告の「前提」にも書く。
注記は図全体表示でも本文ラベルに近い大きさで読める必要がある。`notes:` はページ幅に合わせて出力されるため、前提ごとに短く分ける。

「厳しめ」は、ユーザーが「厳しめに」「レビューに出す」「読み違いが無いように」と言ったときに選ぶ。
批評の `should` も解消するか、解消できないものを未解決として報告に並べる (手順5)。

設計の判断 (なぜ ALB か、なぜ Aurora か) は、図ではなく報告に書く。図には、構成と流れと前提だけを載せる。

## 1.5 最新の AWS 情報を確かめる (不確かなところだけ)

手元の知識と lint の規則は、ある時点のもの。次のどれかに当たるときだけ、**公式の情報源で確かめてから**設計に入る。

- 新しいサービス・機能、最近変わった仕様 (名前の変更、上限、統合の可否)
- 指定リージョンで使えるか (新しいサービスは東京に来ていないことがある)
- 構成の成否を左右する仕様 (例: その組み合わせで VPC 接続できるか、Multi-AZ に対応するか)
- **依頼で指定された構成の選択** (「VPC は使わない」「1 AZ でよい」など) の影響と、見直すべき条件。
  図は指定どおりに描き、影響は報告の設計判断に書く

確かめ方と記録の仕方は `references/aws-knowledge.md`。AWS Knowledge MCP が使えればそれを、無ければ公式ドキュメント
(docs.aws.amazon.com / aws.amazon.com) に絞った Web 検索を使う。**確認した日付と出典 URL を報告に残す。**
よく知られた安定した事実 (NAT は public subnet に置く、など) は確かめない。時間を使うのは不確かなところだけ。

## 2. arch.yaml を書く

書式は `references/spec.md`、見本は `assets/example.arch.yaml`。**書く前に両方読む。**
出力先は、ユーザーの指定が無ければ `docs/architecture/<slug>/` に `<slug>.arch.yaml` として置く。

- アイコンは名前で書く。分からなければ検索する: `arch icons search <語>` (例: `arch icons search firewall`)
  - `NAT Gateway` `ALB` `S3` のような通称・略称でも公式名に解決される
- グループは `group:` で種類を指定する (`aws-cloud` / `region` / `vpc` / `az` / `public-subnet` / `private-subnet` /
  `security-group` / `auto-scaling` / `account` / `corporate-dc` / `generic` など。一覧は spec.md)
- 子を持つ `generic` はまとまりの枠、ラベル付きで線につながる空の `generic` は外部端点としてコンパクトな実線箱になる。未接続の空 group は lint 警告になる
- 並べ方を整えるための箱は `group: layout` にする。枠は描かれず、入れ子の規約チェックでも無いものとして扱われる
- 図は **Web ページのヘッダー / ボディ / フッター**で組む。ヘッダーの上 (VPC 上辺) に IGW、ヘッダーに ALB、
  ボディに AZ を横並び (各 AZ の中は public → app → data を上から下)、フッター (VPC 下辺) に外へ出す口。
  各アイコンは入口と出口が向かい合い (VPC の中は上→下、外は左→右)、ALB からの線は AZ の間を下りて T 字に分かれる。
  接続口は build が自動で決める。詳しくは spec.md の「流れと接続口」 (見本がこの形)
- **ゲートウェイ類は置き場所が決まっている**。IGW / VGW は VPC の枠線の上 (VPC 直下に置けば自動で乗る)、NAT は public subnet の中、
  Gateway 型エンドポイントは VPC の枠線上 (`border:` で辺を指定)。一覧は spec.md の「ゲートウェイ類の置き場所」

## 3. 生成する

```bash
uv run --project "<SKILL>/tools" arch build <slug>.arch.yaml -o <slug>.drawio
```

build は UTF-8 (BOM 付きも可) の arch.yaml を読み、生成した一時 `.drawio` を再読込して lint する。`flow` と明示した `exit` / `entry` は `.drawio` に保持する。build 時と保存後の lint 結果が一致しない場合、または保存後に error がある場合は失敗し、既存出力を置き換えない。

## 4. 決定的検査 (lint) — error を 0 件にする

```bash
uv run --project "<SKILL>/tools" arch lint <slug>.drawio        # 実物の .drawio を検査する
```

- `error` (N-*: 作図規約) は **全部直す**。1件でも残っていたら、批評にも納品にも進まない
- `warn` (A-*: 構成の定石) は直すのが基本。意図があって残すなら、その理由を報告に書く (例: 検証環境なので 1 AZ)
- 各ルールの意味と根拠は `references/aws-conventions.md`。直し方は出力の `→` の行にある
- **納品する `.drawio` 自体を、最後の編集の後に lint する**。build 時の lint 結果を流用しない。`N-PORT-FACE` / `N-PORT-SLOT`、`N-EDGE-*`、`N-NODE-UNCONNECTED` は、最終図で残っていないことを確認する。残す警告は報告に理由を書く

直すときは、**yaml を手で書き換えるより `arch edit` を優先する**。id の整合 (削除時に接続線も消えるなど) をスクリプトが保証するため。

draw.io Desktop で最終図を手直しした場合は、保存後に `arch import <slug>.drawio -o <slug>.arch.yaml` で正本へ戻し、そこから `.drawio` を再生成する。再生成後の `.drawio` に対して lint し、その同じファイルから PNG を書き出す。手直し前の lint や PNG を最終版の検査結果として使わない。孤立したサービスノードは、関係する線を追加するか、図から取り除く。

接続線の経路は build が自動で探索する。下の接続口はラベルの下端にあり、アイコン・ラベル・見出しを避けた経路が選ばれる。
避けきれない線は `N-EDGE-THROUGH-NODE` (error) になるので、並び順か `layout` を変えるか、接続口を固定して直す。

zsh では変数に入れたコマンドが空白で分割されないので、関数にしてから呼ぶ。

```bash
A() { uv run --project "<SKILL>/tools" arch edit <slug>.arch.yaml "$@"; }
A add-node waf --icon "AWS WAF" --label WAF --parent cloud --after cf
A add-group az-d --type az --label "AZ ap-northeast-1d" --parent azs
A move rds-a --parent db-a          # 所属を変える (手動座標は外れて自動配置に戻る)
A set alb label="ALB\n(internet-facing)"
A connect cf waf --label HTTPS ; A disconnect cf alb
A set-edge alb ecs-a label=HTTP exit=right entry=left   # 線のラベル・線種・接続口 (exit= で探索に戻す)
A remove az-c                       # 子と、それにつながる接続線も消える
A relayout                          # draw.io から取り込んだ手動座標を消して自動配置に戻す
```

編集したら、`build` → `lint` をもう一度通す。

## 5. PNG を見て、批評に出す (差し戻しループ)

```bash
uv run --project "<SKILL>/tools" arch render <slug>.drawio -o .arch-loop/<slug>/round-N/<slug>.png
```

**まず自分で PNG を Read して見る。** lint が拾えない見た目の破綻がある。
特に多いのは、接続線がアイコンやラベルの上を横切るもの、線の交差、流れの向きの逆転、要素の詰まりすぎ・空きすぎ。
自分で分かるものは先に直す。線のラベル同士や別の線との重なり、AZ 間の同じ役割の線の高さ・曲がり方、幹を共有する分岐も確認する。

そのうえで、**実装と別系列のモデル**に批評させる。既定は Sonnet。同じモデルに採点させると、自分の癖ごと見落とすため。
その系列が使えない環境なら、使える別の系列で回し、「批評は <モデル> で回した」と1行伝える。

**批評担当はラウンドごとに新しく起動し、返事を待つ** (`run_in_background: false`)。前のラウンドの批評担当に
SendMessage で続きを頼むと、返事が届く前にこちらの作業が終わってしまうことがある。前回の経緯は `accepted` で渡す。

```text
Agent: subagent_type "general-purpose", model "sonnet", run_in_background false
prompt:
  よく考えてから採点してほしい。まず <SKILL>/agents/diagram-critic.md を読み、その指示に完全に従うこと。
  - png: <.arch-loop/<slug>/round-N/<slug>.png>   ← 必ず Read で画像を見る
  - spec: <slug>.arch.yaml のパス
  - requirements: <手順1で固めた要件を箇条書きで>
  - strictness: <標準 / 厳しめ>
  - lint: <arch lint の出力をそのまま>
  - accepted: <これまでに確定した判断 (.arch-loop/<slug>/accepted.md の中身。無ければ「なし」)>
  返答は指定の JSON のみ。
```

- 返答は `.arch-loop/<slug>/round-N/critique.json` に**加工せずに**保存する
- 直すのは findings の分だけにする。ついでに他の箇所を整えない。直したら build → lint → render を回す
- 確定した判断 (「S3 はリージョン直下に置く」など) は `.arch-loop/<slug>/accepted.md` に1行ずつ足す。
  足しておかないと、次のラウンドで批評担当が逆向きの指摘をしてきて往復する
- `user_conflict` が空でなければ、実装側では直さずにユーザーに判断を仰ぐ

**終了条件**: lint の error が 0 件、かつ**納品する PNG そのもの**に対する批評が `pass` なら完成。

- 批評のあとに直したら、その図はまだ誰にも見られていない。**直した最終版は必ずもう一度批評に通す。**
  2ラウンド目のあとに直した場合は、直さない前提の**確認の批評**を1回だけ回し、その結果を報告に書く
- 批評担当は、読み違いを招く指摘 (線の端点の取り違え、流れと逆向きの矢印、無関係な要素に線が触れて見える) を
  `must` にする。`must` が残る間は pass にならない
- 「厳しめ」のときは、`should` も 0 件で pass とする。残ったものは直さない理由を添えて報告の「未解決」に並べる
- **2ラウンドで打ち切る** (確認の批評は数えない)。pass していなくても、現物と残っている指摘を持ってユーザーに返す。
  3周目は、ユーザーが「続けて」と言ったときだけ回す

## 6. 納品と報告

納品物は `<slug>.arch.yaml` (正本)、`<slug>.drawio` (編集用)、`<slug>.png` (確認用) の3つ。
`.arch-loop/` は作業用なので、報告にはパスだけを書く。報告には次を短く書く:

- 3つの成果物のパス
- 構成の要点と設計判断 (なぜその構成か) を3〜6行
- **前提** (図の `notes:` と同じもの。依頼に無く、自分で置いた前提)
- **確認した AWS 情報** (手順1.5 で確かめたもの。確認日と出典 URL。確かめなかったなら「なし」)
- 使ったアイコン: Asset Package の日付 (doctor の出力)
- lint の結果 (error 0 / 残した warn とその理由)
- 批評のラウンド数、**最終版に対する批評の結果**、**未解決**の指摘 (直さなかった `should` とその理由)
- draw.io で手直ししたときの戻し方: 「保存したら `arch import <slug>.drawio -o <slug>.arch.yaml` で正本に戻す」

## draw.io で手直しされた図を受け取ったとき

```bash
uv run --project "<SKILL>/tools" arch import <slug>.drawio -o <slug>.arch.yaml
```

- import は draw.io 上の座標を `pos` / `size` として yaml に書き戻す。**正本を上書きする前に**、
  git の管理下にあるか確かめるか、コピーを取る
- draw.io の Custom Library から置かれた公式アイコンは、画像の中身から公式名を逆引きする。
  逆引きできない画像や図形は `N-UNMANAGED` の error になる
- 見た目の位置と所属がずれている場合 (枠の上に載っているだけで中に入っていない、など) は
  `N-VISUAL-PARENT` になる。draw.io で枠の中へドラッグし直すか、`arch edit move` で所属を直す

draw.io でアイコンを足したいユーザーには、`<SKILL>/icons/current/*.xml` を
「ファイル → ライブラリを開く」(VS Code の拡張機能なら Custom Libraries) で開いてもらうよう案内する。

## 同梱物

| パス | 中身 | 読むタイミング |
| --- | --- | --- |
| `references/spec.md` | arch.yaml の書式・グループの一覧・配置のコツ | arch.yaml を書く前 |
| `references/aws-conventions.md` | lint の各ルールの意味と根拠 | lint の結果を解釈するとき |
| `.mcp.json` | AWS Knowledge MCP Server の接続先 (リポジトリのルートにも登録済み。無くても Web 検索で進める) | 手順1.5 |
| `references/aws-knowledge.md` | 最新の AWS 情報の確かめ方 (AWS Knowledge MCP / 公式ドキュメント) と記録の仕方 | 手順1.5 |
| `agents/diagram-critic.md` | 批評サブエージェントへの指示 | サブエージェントが読む |
| `assets/example.arch.yaml` | 3層 Web (2 AZ) の見本 | 最初に書くとき |
| `tools/src/arch_builder/cli.py` | `arch` コマンド本体 (レイアウト・生成・取り込み・編集) | 挙動を直すとき |
| `tools/src/arch_builder/route.py` | 接続線の経路探索 (接続口の選択・迂回・減点)。lint も同じ判定を使う | 線の引き方を直すとき |
| `tools/src/arch_builder/rules.py` | 規約チェック。ルールの追加はここ | ルールを足すとき |
| `tools/src/arch_builder/render.mjs` | PNG 書き出し (vendor/drawio の Desktop 連携を使う) | — |
| `tools/tests/test_arch.py` | 自己チェック: `uv run --project <SKILL>/tools pytest <SKILL>/tools/tests -q` | **コードを触ったら必ず** |
| `vendor/drawio/` | drawio スキル (MIT)。XML・スタイルの仕様 (`references/official/`) と書き出しの実装 | 生の XML を直接いじるとき |
| `vendor/aws-drawio-import/` | 公式アイコン ZIP → draw.io ライブラリ変換 | `arch icons build` が呼ぶ |
| `icons/current/` | 取り込んだ公式アイコン (git 管理外) | — |

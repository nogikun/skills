# arch-builder の評価の回し方

`evals.json` のプロンプトを、スキルあり (`with_skill`) とスキルなし (`without_skill`) で実行し、
成果物を採点して比べる。結果はリポジトリ直下の `arch-builder-workspace/iteration-N/` に残す。run単位の実行成果物は `.gitignore` 対象で、評価手順・レビュー・比較用MarkdownはGitで管理する。

## 実行

- **1 プロンプト × 1 条件あたり 3 回**実行し、`eval-<id>-<name>/<条件>/run-1` 〜 `run-3` に分けて保存する。
  1 回ずつだと、ケースの違いと偶然のばらつきを区別できない (2026-09-27 のレビューの指摘)
- 各 run に `timing.json` (tokens / 所要時間) と `run.json` を置く:

  ```json
  {"executor_model": "claude-opus-5-5", "critic_model": "claude-sonnet-5", "grader_model": "claude-opus-5-5",
   "skill_commit": "<git rev-parse --short HEAD>", "icon_package": "2026-01-30"}
  ```

- スキルなしの結果は、プロンプトが変わらない限り前の iteration から流用してよい。流用したら `benchmark.md` にそう書く

## 採点 (3 種類を混ぜない)

`evals.json` の各アサーションは `type` を持つ。種類ごとに採点の仕方と採点者を分ける。

| type | 何を見るか | 採点の仕方 |
| --- | --- | --- |
| `machine` | 成果物の有無、`arch lint --json` の error / warn の件数、特定のルールコード | スクリプトで判定する。人やモデルの判断を入れない |
| `design` | 要件を満たす構成か、前提を図と報告に書いているか、報告が直した点を説明しているか | `.arch.yaml` と `report.md` を読んで採点する (grader) |
| `legibility` | 線の誤読、ラベルの重なり、流れの追いやすさ | **PNG だけ**を見せて採点する。yaml や報告を見せない (読み手と同じ条件にする) |

スキルなしの `.drawio` は draw.io 標準の aws4 図形で描かれることが多く、`arch lint` は公式 ZIP アイコン以外を
`N-UNMANAGED` にする。そのため `machine` の比較はスキルありに有利になる。スキルなしとの差は `design` と
`legibility` で見る。

## 集計

skill-creator の `aggregate_benchmark` で `benchmark.json` を作る。`±` は同じケースを 3 回回したばらつきを表す。
run が 1 回しかない iteration では `runs_per_configuration` を 1 にし、「±はケース間の差であり、再現性の指標ではない」と
`benchmark.md` に書く。

批評のループの結果 (最終版に対する批評が pass か、未解決の `should`) も run ごとに記録する。
**最終版に対する批評が無い run は、図の完成条件を満たしていないものとして数える。**

# vendor — 取り込み元と改変の有無

| ディレクトリ | 取り込み元 | 版 | ライセンス | 改変 |
| --- | --- | --- | --- | --- |
| `drawio/` | github.com/bahayonghang/drawio-skills `skills/drawio` (このリポジトリの `skills-lock.json` 経由で導入) | 2.8.0 (computedHash `7136e8ae…`) | MIT | **なし**。`evals/` と `reports/` は実行に不要なので取り込んでいない |
| `aws-drawio-import/` | github.com/nogikun/skills `skills/aws-drawio-import` | computedHash `1fe3f917…` | 取り込み元に準ずる | **なし** |

## arch-builder が使っているところ

- `drawio/scripts/runtime/desktop.js`: draw.io Desktop を探して PNG に書き出す (`tools/src/arch_builder/render.mjs` から呼ぶ)
- `drawio/scripts/svg/drawio-to-svg.js`: Desktop が無いときの近似 SVG 書き出し
- `drawio/references/official/`: mxGraph の XML・スタイルの仕様。.drawio を直接読むときの資料
- `aws-drawio-import/scripts/build_aws_drawio_libraries.py`: 公式アイコン ZIP → draw.io ライブラリ (`arch icons build` から呼ぶ)

drawio スキルの YAML 形式は正本に使っていない。その形式の `modules` は平たいので、
AWS に必須のグループの入れ子 (AWS Cloud ⊃ Region ⊃ VPC ⊃ AZ ⊃ Subnet) を表せないため。

## 更新するとき

上流の同じパスを丸ごと置き換え、この表の版を書き換えてから、次の自己チェックを通す。
`uv run --project ../tools pytest ../tools/tests -q` と、`arch render` で PNG が出ること。

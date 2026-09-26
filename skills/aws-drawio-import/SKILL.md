---
name: aws-drawio-import
description: AWSアイコンパッケージZIP（Icon-package_*.zip）を受け取り、draw.io用SVGライブラリ（mxlibrary形式のXML）に変換して保存する。ユーザーが「新しいAWSアイコンZIPを取り込んで」「icon-package…zipをdrawio用にして」「AWSアイコンをdraw.ioで使えるようにしたい」などと言ったとき、またはIcon-package_*.zipのパスを渡されたときは必ずこのスキルを使うこと。
---

# AWSアイコンZIP → draw.ioライブラリ

変換はこのスキルに同梱した `scripts/build_aws_drawio_libraries.py` がすべて行う（標準ライブラリのみ、Python 3.9以上）。以下の`<SKILL_DIR>`は、このSKILL.mdがあるディレクトリを指す。

## 手順

1. **入力を確認する。** 渡されたZIPが存在し、`.zip`であることを確かめる。どちらかを満たさない場合は、そこで止めてユーザーに確認する。プロジェクトに`icons/`があれば、ZIPをファイル名のまま`icons/`へコピーしておく。

2. **出力先を決める。** このリポジトリでは`build/drawio_libs/`。他のプロジェクトでは既存の出力先があればそれに合わせ、なければ`drawio_libs/`を使う。

3. **ビルドする。** このリポジトリで実行するときは、Taskfileのコマンドを使う。

   ```sh
   task drawio:build ICON_PACKAGE=icons/Icon-package.zip
   ```

   他のリポジトリでは以下のコマンドを使う。標準の`python3`が起動しない環境では`mise exec -- python3`または`uv run --no-project --python 3.11`で実行する。

   ```sh
   python3 <SKILL_DIR>/scripts/build_aws_drawio_libraries.py <ZIPのパス> --output-dir drawio_libs
   ```

   通常は1秒以内に終わる。10秒以上応答しない場合はプロセスを止め、上記のmiseまたはuv経由でやり直す。環境によってはNixの`xcrun`シムが`python3`を再帰的に起動し続けることがある。
   ライブラリごとに`<ライブラリ名>: <数> SVG icons`が出力される。

4. **結果を確認する。** `AWS-*-<YYYY-MM-DD>.xml`が4つ生成され、`<出力先>/current/`の固定名ファイルも更新されていればよい（日付はZIP内のパスから決まる）。日付が`undated`になった場合や生成数が少ない場合は、ZIPの構成が想定と違う可能性があるので、ユーザーに伝える。

5. **後片付け。** 同じ出力先に別の日付のXMLが残っていたら、削除してよいかユーザーに確認する。勝手に消さないこと。ユーザーが古い版を並行して使っている場合があるため。READMEにライブラリの一覧（SVG数やファイル名）があれば、新しい版に合わせて更新する。

## 完了報告

ライブラリごとのSVG数と生成したファイルのパス、古い版をどう扱ったかを短く伝える。VS Codeの`hediet.vscode-drawio`では、リポジトリの`.vscode/settings.json`が`build/drawio_libs/current/`を登録する。図形パネルの **その他の図形 → Custom Libraries** で使うライブラリを選び **Apply** する。通常の **ファイル → インポート** やXMLのキャンバスへのドラッグは、ライブラリ読み込みではなく図面への挿入になる。draw.io単体では **ファイル → ライブラリを開く → デバイスから** を使う。

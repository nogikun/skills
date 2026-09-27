# 最新の AWS 情報の確かめ方

手元の知識、lint の規則、アイコンライブラリは、どれもある時点のもの。
設計の成否を左右するのに自信が持てないところだけを、公式の情報源で確かめる。

## 何を確かめるか

| 確かめる | 確かめない |
| --- | --- |
| 新しいサービス・機能、最近の仕様変更 (名前の変更、統合、廃止予定) | 定番の配置 (NAT は public subnet、DB は private など) |
| 指定リージョンでの提供状況 (東京・大阪に来ているか) | 主要サービスが東京にあるか (EC2 / RDS / S3 など) |
| 構成の可否 (その組み合わせで VPC 接続できるか、Multi-AZ に対応するか、エンドポイントがあるか) | 図の描き方 (それは spec.md と aws-conventions.md) |
| **依頼で指定された構成の選択の影響** (「VPC は使わない」「1 AZ でよい」など)。その選択で何が成り立たなくなるか、いつ見直すべきか | — |

依頼で構成の選択を指定されたときは、図はその選択どおりに描き、影響は報告の「設計判断」に書く。
例: 「VPC を使わない」サーバーレスなら、「DynamoDB / SQS / SNS はインターネット側のエンドポイントで使える。
RDS や ElastiCache を足す、社内ネットワークから閉じた経路で呼ぶ、といった要件が出たら Lambda の VPC 接続と
VPC エンドポイントが要る」のように、見直しの条件まで書く。

迷ったら確かめる。ただし1つの図で確かめるのは数件までにする。時間を使うのは不確かなところだけ。

## 使う道具 (上から順に)

1. **AWS Knowledge MCP Server** (<https://awslabs.github.io/mcp/servers/aws-knowledge-mcp-server>)
   - エンドポイント: `https://knowledge-mcp.global.api.aws` (Streamable HTTP)。AWS アカウントも認証情報も不要
   - このリポジトリはルートの `.mcp.json` に `aws-knowledge-mcp-server` として登録済み。スキルにも同じ設定を
     `<SKILL>/.mcp.json` として同梱している (別のリポジトリへスキルを持っていったとき用)。
     ツールが見えないときは、ユーザーに次で登録してよいか1行で聞く:
     `claude mcp add --transport http --scope project aws-knowledge-mcp-server https://knowledge-mcp.global.api.aws`
   - 使うツール:

     | ツール | 使いどころ |
     | --- | --- |
     | `search_documentation` | 仕様・機能・統合可否を探す |
     | `read_documentation` | 見つけたページを読み、出典 URL を控える |
     | `get_regional_availability` | 指定リージョンでサービス・機能が使えるか |
     | `list_regions` | リージョンコードと名前の対応 (図の Region ラベル用) |
     | `retrieve_skill` | AWS 公式のエージェント向け手順を取り寄せる (必要なときだけ) |

   - レート制限がある。同じことを何度も引かない
2. **Web 検索 / 取得** (MCP が使えないとき)
   - 公式の情報源に絞る: `docs.aws.amazon.com`、`aws.amazon.com` (What's New、リージョン別サービス一覧)
   - ブログや個人の記事は、公式で裏が取れないかぎり根拠にしない
3. どちらも使えないとき
   - 確かめられなかったことを、報告の「確認した AWS 情報」に「未確認」として書く。確かめたふりをしない

## 記録の仕方

報告の「確認した AWS 情報」に、1件1行で書く。

```markdown
## 確認した AWS 情報 (2026-09-27 確認)
- Aurora MySQL の Multi-AZ クラスターは ap-northeast-1 で利用可能 — https://docs.aws.amazon.com/...
- CloudFront の VPC オリジンは ALB (internal) をオリジンにできる — https://docs.aws.amazon.com/...
```

確かめた結果が設計を変えたときは、どう変えたかも1行添える。

## アイコンライブラリの鮮度

`arch doctor` が Asset Package の日付を出す。Asset Package は四半期ごとに更新される
(<https://aws.amazon.com/architecture/icons/>)。古いという注意が出たら、新しい版が出ていないかをユーザーに伝え、
ZIP をもらえたら `arch icons build` で取り込む。使った日付は報告に書く。

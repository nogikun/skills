# lint のルールと根拠

`arch lint` の出力は `[重大度] コード id: 説明` と、直し方を示す `→` の行でできている。

- **error**: 図として誤っているもの。納品前に 0 件にする
- **warn**: 構成の定石から外れているもの。直すのが基本。意図があるなら理由を報告に書いて残してよい
- **info**: 書き足すと親切なもの

根拠にしている資料:

1. AWS Architecture Icons のガイドライン (Asset Package 同梱の PPT と <https://aws.amazon.com/architecture/icons/>)
2. Amazon VPC ユーザーガイド (NAT Gateway、Internet Gateway、サブネット)
3. AWS Well-Architected Framework の信頼性の柱 (REL10: 複数の場所へ分散する) と、セキュリティの柱 (SEC05: ネットワークの保護)

## 作図規約 (N-*)

| コード | 重大度 | 何を見るか | 根拠と理由 |
| --- | --- | --- | --- |
| `E-ICON` | error | アイコンが公式ライブラリに無い | [1] 公式アイコンだけを使う。古い版の意匠や非公式の画像を混ぜない |
| `E-ID` / `E-REF` | error | id の重複・欠落、接続線の端が存在しない | 生成できない |
| `N-UNMANAGED` | error | draw.io で足された、公式ライブラリに無い画像や図形 | [1] 同上。注記はラベルか `generic` グループで表す |
| `N-CATEGORY-ICON` | error | カテゴリアイコンをサービスとして使っている | [1] カテゴリアイコンはサービスを表さない |
| `N-GROUP-ICON` | error | グループアイコンを単体の要素として置いている | [1] グループは枠で表す |
| `N-NESTING` | error | グループの入れ子の順序が誤っている | [1][2] AWS Cloud ⊃ Region ⊃ VPC ⊃ AZ ⊃ Subnet。VPC はリージョンに、subnet は AZ に属する |
| `N-GROUP-TYPE` | error | 未知のグループの種類 | 枠の色と種類の規約を当てられない |
| `N-LABEL` | error | アイコンにラベルが無い | [1] アイコンには必ずサービス名を添える |
| `N-ICON-DISTORT` | error | アイコンの縦横比が崩れている | [1] アイコンを変形しない |
| `N-ESCAPE` | error | 子が親の枠からはみ出している | 所属が読めなくなる |
| `N-OVERLAP` | error | 兄弟要素どうしが重なっている (ラベルの領域を含む) | 読めない |
| `N-VISUAL-PARENT` | error | 見た目で入っている枠と、論理上の所属が一致しない | draw.io で枠の上に載せただけの状態。図と正本が別のことを言っている |
| `N-EDGE-THROUGH-NODE` | error | 接続線がアイコンかラベルを横切っている (自分自身も含む) | 線の下の文字が読めない。経路は build が探索するので、残ったら並び順か `exit`/`entry` で直す |
| `N-ICON-SIZE` | warn | アイコンが 48px 以外 | [1] 図の中のアイコンの大きさはそろえる |
| `N-LABEL-LONG` / `N-EDGE-LABEL-LONG` | warn | ラベルが長い | 他の要素と重なりやすい |
| `N-EMPTY-GROUP` | warn | 空のグループ | 描き残し |
| `N-SUBNET-OUTSIDE-AZ` | warn | AZ を描いた VPC で、AZ の外に subnet がある | [2] subnet は必ず1つの AZ に属する |
| `N-NODE-IN-AZ` | warn | AZ の直下にリソースがある | [2] リソースは subnet に属する |
| `N-NODE-IN-VPC` | warn | 境界要素以外のリソースが VPC の直下にある | [2] 同上 |
| `N-ASPECT-PORTRAIT` | error | 図全体が縦長 (1:1 未満) | スライドや画面に貼れない。直し方は spec.md「キャンバスの縦横比」 |
| `N-ASPECT` | info | 横長だが 16:9 の目安 (1.4〜2.2:1) から外れている | 目安。線の素直さを優先し、無理に合わせない |
| `N-PORT-FACE` | error | 線の出口・入口の面が決まりと違う (spec.md「流れと接続口」の 2) | 入口と出口が向かい合わないと、流れの向きが読めない |
| `N-PORT-SLOT` | error | 面の中の位置が決まりと違う (内容の違う線が同じ位置、同じ内容の線が別の位置、2n+1 等分の偶数番目でない) | 内容の違う通信が 1 本に見える / 同じ通信がばらばらに見える |
| `N-EDGE-OVERLAP` | warn | 内容が違う線、または端点を共有しない線どうしが重なって走っている | どの線がどこへ行くか追えない |
| `N-EDGE-BENDS` | warn | 実線が 3 回以上曲がっている | 並びが流れの向きと合っていないことが多い |
| `N-SPARSE` | warn / error | 枠の面積が、中身に必要な面積の 1.5 倍を超える (2 倍以上は error) | 広い枠の中にアイコンが 1 つ 2 つだけだと、何の枠なのか、どこが重要なのかが読めない |
| `N-NODE-UNCONNECTED` | error | どの線ともつながっていないノードがある (ゲートウェイに限らず、すべてのノード) | 置いたのに、何とやり取りするのか読めない。補助の関係 (監視・認可など) なら破線でつなぐ。不要なら消す |
| `N-EDGE-LABEL-CROSSED` | warn | 線のラベルの上を別の線が通る (共有した幹は除く。ラベルの周り 4px を含む) | 文字が線で消される |
| `N-EDGE-ON-FRAME` | warn | 線がグループの枠線に沿って (8px 以内で) 走っている | 枠線と見分けがつかない |
| `N-EDGE-NEAR-NODE` | warn | 接続線が、端点ではない要素のすぐ脇 (22px 以内) を通っている | その要素に出入りしているように誤読される。経路探索でも減点している |
| `N-EDGE-THROUGH-HEADER` | warn | 接続線がグループの見出し (アイコン + 名前) を横切っている | 見出しが読めない |
| `N-EDGE-LABEL-OVERLAP` | warn | 線のラベル (経路の中点) がアイコンかラベルに重なっている | 読めない |
| `N-EDGE-UNCHECKED` | info | 経路が draw.io 任せ (waypoint が無い、または要素を動かして斜めになった) の線がある | 横切りを検査できない。`arch import` → `arch build` で引き直すと検査できる |
| `N-EDGE-DUP` / `N-EDGE-SELF` | warn | 重複した接続線、自分自身への接続線 | 描き誤り |
| `N-REGION-LABEL` | info | Region のラベルにリージョン名が無い | どこのリージョンか分からない |

## 構成の定石 (A-*)

| コード | 重大度 | 何を見るか | 根拠と理由 |
| --- | --- | --- | --- |
| `A-NAT-PLACEMENT` | error | NAT Gateway が public subnet の外にある | [2] public NAT Gateway は public subnet に作る。構成として動かない |
| `A-DB-PUBLIC` | warn | データストアが public subnet にある | [3] SEC05。データ層はインターネットから到達できない場所に置く |
| `A-COMPUTE-PUBLIC` | warn | 踏み台以外の計算資源が public subnet にある | [3] 入口は ALB / NAT に絞る。ラベルに「踏み台 / bastion」とあれば対象外 |
| `A-LAMBDA-PUBLIC` | warn | VPC Lambda が public subnet にある | [2] VPC Lambda にはパブリック IP が付かないので、外に出られない |
| `A-GATEWAY-BORDER` | warn | IGW / VPN Gateway / Carrier Gateway が VPC の枠線の上に無い | [1][2] VPC の出入口なので境界をまたいで描く。VPC に付くもので、subnet には属さない |
| `N-BORDER-ON-LAYOUT` | warn | 描かれない `layout` 箱の枠線の上に置いている | 境界が見えないので、境界に置いた意味が伝わらない |
| `A-REGIONAL-IN-VPC` | warn | S3 / DynamoDB / SQS などを VPC の中に描いている | [2] これらは VPC の外のリージョンサービス。私設経路はエンドポイントとして描く |
| `A-GLOBAL-IN-REGION` | warn | CloudFront / Route 53 / IAM を Region の中に描いている | グローバルサービスはリージョンに属さない |
| `A-ELB-IN-AZ` | warn | ELB を1つの AZ の中に描いている | [3] REL10。ELB は複数の AZ にまたがる。1つの AZ に描くと単一障害点に見える |
| `A-SINGLE-AZ` | warn | VPC のワークロードが 2 AZ 未満 | [3] REL10。検証環境など意図的なら理由を報告に書く |
| `A-NO-IGW` | warn | public subnet があるのに IGW が無い | [2] IGW への経路が無い subnet は public にならない |
| `A-DB-SINGLE` | info | 2 AZ 構成なのに DB が1つの AZ にしか無い | Multi-AZ なら、スタンバイを描くかラベルに書く |

## ルールを足すとき

`tools/src/arch_builder/rules.py` の `lint()` に足し、この表に1行と、`tools/tests/test_arch.py` にそのルールで引っかかる最小の例を1つ足す。
サービスの判定はアイコン名の部分一致 (`DATA_STORE` / `COMPUTE` / `OUTSIDE_VPC` / `GLOBAL` / `VPC_EDGE`) で行っている。
新しいサービスが出たら、ここに名前を足す。

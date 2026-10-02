# arch.yaml の書式

ファイルは UTF-8 で保存する (BOM があっても読める)。

```yaml
title: 図の名前                  # draw.io のページ名になる
page_aspect: 1.33               # 省略可。用紙の幅/高さ (1.0〜2.2)。未指定は16:9
items:                           # キャンバス直下の要素。入れ子は children で表す
  - id: users                    # 図全体で一意。接続線と arch edit はこれで指す
    icon: Users                  # node: 公式アイコンの名前 (略称でも可)
    label: 利用者                # 表示するラベル。"\n" で改行
    border: top                  # 省略可。親グループの枠線の上に置く辺: top / right / bottom / left (none で打ち消す)
  - id: cloud
    group: aws-cloud             # group: グループの種類 (下の表)
    label: ""                    # 省略すると種類ごとの既定名 (AWS Cloud など)
    layout: row                  # 子の並べ方: row (横, 既定) / column (縦) / grid
    cols: 2                      # grid のときの列数
    children: [...]
edges:
  - from: users
    to: cf
    label: HTTPS                 # 省略可。12〜16字まで
    dashed: true                 # 非同期・レプリケーション・管理系の流れ
    arrow: end                   # end (既定) / both / none
    exit: bottom                 # 省略可。出口の辺を固定: top / right / bottom / left
    entry: left                  # 省略可。入口の辺を固定
```

```yaml
notes:                           # 省略可。図の下に「※」付きで並ぶ前提・注記
  - 前提: 社員はインターネット経由で ALB に入る (社内限定なら VPN + internal ALB に変える)
  - 各 AZ の ECS から Writer への線は、代表の 1 本だけを描いている
```

依頼に無いのに自分で置いた前提 (アクセス経路、省略した線など) は `notes:` に書く。報告だけに書くと、図が単独で回覧されたときに前提が落ちる。
注記はページ幅に合わせて図の下に表示される。長文を詰め込まず、1つの前提を1行に分けて、全体表示でも読める量にする。

接続線の経路は `arch build` が探索して決め、中継点 (waypoint) として .drawio に書く。
接続口は上下左右の 4 つで、**下の接続口はアイコンではなくラベルの下端**にある (ラベルを線が貫かないように)。
他のアイコン・ラベル・グループの見出しを横切る経路、線どうしの交差、曲がりの多い経路を避けて選ぶ。
`exit` / `entry` は、探索の結果が意図と違うときにだけ指定する。

### 流れと接続口 (いちばん大事な決まり)

図は **Web ページのヘッダー / ボディ / フッター** にたとえて組む。

| 部分 | 置くもの |
| --- | --- |
| ヘッダーの上 (VPC の上辺の線) | 入口のゲートウェイ (IGW)。VPC の直下に置けば自動で乗る |
| ヘッダー (VPC の上部) | ALB など、AZ をまたぐ入口。VPC を `layout: column` にして、先頭に置く |
| ボディ | AZ を横に並べる。各 AZ の中は public → app → data を上から下へ |
| フッター (VPC の下辺の線) | 外へ出す口 (VPN Gateway、Transit Gateway Attachment) |

接続口 (線がアイコンのどの面の、どの位置から出入りするか) は build が次の定義で決め、lint が同じ定義で検査する
(`N-PORT-FACE` / `N-PORT-SLOT` は error)。実装は `tools/src/arch_builder/cli.py` の `plan_ports`。

**1. 流れ** — VPC の中は上 → 下 (入口の面 = 上、出口の面 = 下)、VPC の外は左 → 右 (入口 = 左、出口 = 右)。
入口と出口は向かい合う面なので、線はアイコンを一直線に通り抜ける。グループに `flow: down` / `flow: right` を書けば変えられる。

**2. 面 — 通信の向きで決める** (「受け手の入口だから」では決めない。線の両端それぞれ、その端の要素の流れで判定する)

| 通信の向き (相手の位置) | 使う面 |
| --- | --- |
| **順方向** = 外 → 中 (相手が下流。VPC の中なら、相手のアイコンの上端が自分のアイコンの下端より下) | 流れの面 (出口 = 下、入口 = 上) |
| **逆方向** = 中 → 外 (相手が上流。例: ECS → NAT、NAT → IGW) | **点対称に入れ替える**。出口 = 自分の入口の面、入口 = 相手の出口の面 (NAT → IGW は IGW の下の面に入る) |
| 真横 (流れの方向に重なりがある) | 流れと直交する面を向かい合わせる。VPC の中なら左右、外なら上下 |
| 枠線上のゲートウェイ | 枠線に直交する面のうち、相手のいる側 (上辺の IGW なら、外の相手は上、中の相手は下) |

逆方向の線は、行きの線と同じ面に乗ることが多い (IGW の下の面: ALB へ出る線と NAT から戻る線)。
向きが違えば「内容」が違うので、下のスロットで別の位置に分かれ、重ならない。`exit` / `entry` を書けばその面に固定できる。

**3. スロット — 面の中の位置**

- 1 つの面を使う線の「内容」が n 種類なら、面を **2n+1 等分し、2, 4, …, 2n 番目の区間の中央**から出入りする。
  n = 1 は中央 (50%)、n = 2 は 30% と 70%、n = 3 は約 21% / 50% / 79%
- **内容 = ラベル (プロトコルなど) + 線種 (実線・破線) + 向き (出る・入る)**。同じ内容の線は同じスロットを使う
- スロットの並びは、相手の位置の順 (上下の面なら相手の x の順、左右の面なら y の順)。線どうしが交差しないように

**4. 幹の共有** — **同じ内容で、同じ出口から分かれる (または同じ入口へ集まる) 線だけ**が幹を重ねてよい。
ALB → 各 AZ の ECS は、1 つのスロットから出た幹が AZ の間を下りて T 字に分かれる。
内容が違う線は、別のスロットから出て別の経路を通る (重なれば `N-EDGE-OVERLAP`)。

探索には、ほかに次の 2 つの決まりが入っている。

- **枠線上の要素 (IGW など) は、枠線に直交する口だけを使う。** 上辺・下辺の線に乗るものは上下の口、
  左辺・右辺に乗るものは左右の口。平行な口から出すと、線が枠線をなぞってしまうため
- **対になる線はそろえて引く。** 同じ要素から AZ ごとの同じ役割の相手へ向かう線 (ALB → 各 AZ の ECS) や、
  AZ ごとに同じ組み合わせの線 (各 AZ の ECS → 同じ AZ の DB) は、同じ口の組み合わせ・同じ曲がり方で引く
  (相手が反対側にいれば左右を反転する)。「同じ役割」は、アイコンと、祖先のグループの並び (AZ → subnet …) が同じことで判定する。
  つなぎ方が AZ ごとにばらばらだと、構成が違うように見えてしまう

`pos: [x, y]` (親の左上からの相対座標) と `size: [w, h]` は、`arch import` が draw.io 上の手直しを残すために書くもの。
手で書く必要はない。`pos` のある要素は自動配置の対象外になる。`arch edit relayout [id]` で消せる。

## グループの種類

| group | 枠の見た目 | 置いてよい親 |
| --- | --- | --- |
| `aws-cloud` | 濃紺・実線・AWS ロゴ | キャンバス直下 |
| `account` | ピンク・実線 | キャンバス直下 / aws-cloud |
| `region` | 青緑・破線・旗 | aws-cloud / account |
| `vpc` | 紫・実線 | region |
| `az` | 青緑・破線・見出しは中央 | vpc |
| `public-subnet` | 緑・塗り | az / vpc |
| `private-subnet` | 青緑・塗り | az / vpc |
| `security-group` | 赤・実線 | subnet / az / vpc |
| `auto-scaling` | 橙・破線 | subnet / az / vpc / security-group |
| `ec2-contents` / `spot-fleet` | 橙 | subnet など |
| `corporate-dc` / `server-contents` | 灰 | キャンバス直下 (オンプレ側) |
| `generic` | 灰・破線 | どこでも (論理的なまとまり: 「管理系」「バッチ」など。外部システムなどの端点はラベルを付け、線で接続すれば単独でも置ける。接続済みの空の端点はコンパクトな実線箱で描き、子を持つ group は従来どおり破線の枠で描く) |
| `layout` | **描かない** | どこでも。並べ方を整えるためだけに使い、規約チェックでは無いものとして扱う |

入れ子の順序は **AWS Cloud ⊃ (Account ⊃) Region ⊃ VPC ⊃ AZ ⊃ Subnet ⊃ (Security group / Auto Scaling group) ⊃ リソース**。

## 置き場所の原則

| 置くもの | 置き場所 |
| --- | --- |
| 利用者・外部システム・オンプレ | キャンバス直下 (AWS Cloud の外)。`Users` `Client` `Mobile client` `Internet` `Corporate data center` |
| グローバルサービス (CloudFront, Route 53, IAM, Global Accelerator) | AWS Cloud の中、Region の外 |
| リージョンサービス (S3, DynamoDB, SQS, SNS, API Gateway, Cognito, CloudWatch, Lambda※) | Region の中、VPC の外 |
| VPC の境界 (Internet Gateway, ELB, VPC エンドポイント, VPN/Transit Gateway) | VPC の直下 (または `layout` 箱の中) |
| NAT Gateway | public subnet |
| アプリ (EC2, ECS/Fargate, EKS) | private subnet (踏み台だけは public 可) |
| データ (RDS, Aurora, ElastiCache, OpenSearch...) | private subnet (データ用に subnet を分けるとなお良い) |

※ Lambda を VPC に接続する構成なら、private subnet に置く。

## 配置のコツ

自動配置は「子を順に並べる」だけなので、**読みやすさは並び順と `layout` で決まる**。

- `aws-cloud` / `region` は `layout: row` にして、入口 (CloudFront など) を左、VPC を中央、リージョンサービスを右に置く
- **VPC の既定の型** (`assets/example.arch.yaml`): 上の「流れと接続口」の表のとおり。
  VPC は `layout: column` で [ALB, AZ を横に並べた `layout` 箱]、各 AZ は `layout: column` で public → app → data
- 各 AZ の subnet は同じ順・同じ数にする。同じ層が同じ高さにそろい、冗長構成であることが一目で分かる

## 枠の大きさ (スカスカの枠を作らない)

枠 (グループ) の面積は、**中身に必要な面積の 1.5 倍まで**。必要な面積は、中身の外接矩形に規定の余白 (見出し 48px、左右 24px、下 24px)
と見出しの幅 (見出しの 2 倍の幅) を足したもの。1.5 倍を超えると `N-SPARSE` (warn)、**2 倍以上は禁止** (error)。

自動配置は、並んだ枠の幅や高さをそろえるとき、1.5 倍を超える引き伸ばしをしない。中身の少ない枠は、そろえずに中身の大きさのまま置く。
draw.io で枠を手で広げた図や、`size:` を書いた枠は lint で検出されるので、`arch edit relayout` で中身に合わせて縮める。

## キャンバスの縦横比

- **縦長 (1:1 未満) は禁止** (`N-ASPECT-PORTRAIT`、error)。0.7:1 や 1:2 の図は作らない
- 横長の中での **16:9 (1.78:1) は目安**。1.2:1 などでもよい。1.4〜2.2:1 の外は `N-ASPECT` (info) で知らせるだけ。
  **線の素直さ (流れと接続口の決まり) を崩してまで合わせない**
- `.drawio` の用紙は図全体を含む。既定は16:9で、スライドや画面へ貼りやすくする。
- 写真・帳票など別の比率へ合わせるときは、トップレベルの `page_aspect` に幅/高さを指定する (1.0〜2.2)。図の内容が指定幅より広い場合、用紙は内容を切らない比率まで広がる。図形の位置や大きさは変えない。

- 縦長になったら: ALB を AZ 列の上ではなく左に置く (VPC を `layout: row` にする。見本の形)、
  リージョンサービス (S3 / DynamoDB / SQS など) を VPC の右に縦に並べる、利用者やオンプレを左に置く
- 横長になったら: VPC の外の要素を `layout: column` の箱に入れて縦に積む
- AZ を縦に積まない (AZ は横に並べる)

## ゲートウェイ類の置き場所

境界をまたいで出入りするものは、**境界 (枠線) の上**に描く。中に置くものと取り違えない。

| もの | 置き場所 | arch.yaml |
| --- | --- | --- |
| Internet Gateway | VPC の**上辺の線の上** (インターネット側) | VPC の直下に置くだけで `border: top` になる |
| VPN Gateway (VGW) | VPC の**下辺 (フッター) の線の上** (外へ出す口) | VPC の直下に置くだけで `border: bottom` になる |
| Transit Gateway Attachment | VPC の**下辺の線の上** | VPC の直下に置くだけで `border: bottom` になる |
| Gateway 型 VPC エンドポイント (S3 / DynamoDB) | VPC の**サービス側の辺の線の上** | `icon: Endpoints` + `border: right` など、辺を明示する |
| NAT Gateway | 各 AZ の **public subnet の中** | subnet の子にする |
| Interface 型 VPC エンドポイント | **private subnet の中** | subnet の子にする |
| Transit Gateway 本体 | **VPC の外**、Region の中 | Region の子にする |
| Customer Gateway | **オンプレ側** (`corporate-dc` の中) | AWS Cloud の外に置く |
| API Gateway | VPC の外のリージョンサービス | Region の子にする |

辺を変えたいとき (オンプレが右側にある図など) は `border: right` のように書く。
同じ辺に複数あるときは、等間隔に並ぶ。
- 接続線は交差やアイコンの横切りが少なくなるように、**線でつながる相手どうしを隣に置く**。
  横切りが消えないときは、要素の並び順を入れ替えるか、並べ方 (row / column) を変える
- 同じ種類の線がたくさんあるとき (全 AZ の ECS から DB へ、など) は、代表の1本にして「各 AZ から」とラベルを付けるほうが読みやすい
- 要素が 25 個を超えたら、図を分ける (全体図と、VPC の詳細図など) ことを検討する

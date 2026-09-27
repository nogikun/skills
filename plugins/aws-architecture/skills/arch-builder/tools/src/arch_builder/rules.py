"""AWS 構成図の規約チェック。

- N-*: 作図規約 (AWS Architecture Icons のグループ・アイコン規約、図としての破綻)。原則 error
- A-*: 構成の妥当性 (Well-Architected 寄りの定石)。warn / info
error が 1 件でも残る図は納品しない。warn は理由があれば残してよい (図の注記か報告で理由を言う)。
各ルールの根拠は references/aws-conventions.md。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from arch_builder.cli import (GROUPS, ICON, LAYOUT_ONLY, STRETCH_MAX, SUBNETS, Model, edge_class, group_need, on_border,
                              plan_ports, group_headers, label_size, layout, route_box)
from arch_builder.route import (_near_collinear, blocked, label_hits, label_rect, near_obstacles, obstacles_of,
                                crosses_label, passes_near, path_hits, rects_overlap, shares_trunk)

SEV_ORDER = {"error": 0, "warn": 1, "info": 2}


@dataclass
class Finding:
    severity: str
    code: str
    id: str | None
    msg: str
    fix: str = ""


# アイコン名 (正規化後) の部分一致で役割を判定する
def _has(name: str, *words: str) -> bool:
    n = name.lower()
    return any(w in n for w in words)


DATA_STORE = ("rds", "aurora", "elasticache", "redshift", "documentdb", "neptune", "memorydb", "opensearch")
COMPUTE = ("ec2", "instance", "elastic container service", "ecs", "fargate", "eks", "elastic kubernetes")
OUTSIDE_VPC = ("simple storage service", "s3", "dynamodb", "simple queue service", "sqs", "simple notification",
               "sns", "api gateway", "cognito", "eventbridge", "step functions", "kinesis", "secrets manager",
               "key management", "cloudwatch", "cloudtrail", "elastic container registry", "simple email",
               "athena", "glue", "bedrock", "codepipeline", "codebuild", "systems manager")
GLOBAL = ("cloudfront", "route 53", "identity and access management", "iam", "global accelerator", "organizations")
VPC_EDGE = ("internet gateway", "load balanc", "vpn gateway", "virtual private gateway", "endpoint",
            "transit gateway", "network firewall", "privatelink", "customer gateway")
GATEWAYS = ("internet gateway", "nat gateway", "vpn gateway", "carrier gateway", "customer gateway", "transit gateway",
            "direct connect", "endpoint")
BASTION = re.compile(r"bastion|踏み台|jump", re.I)


def _word(name: str, words) -> bool:
    n = " " + re.sub(r"[^a-z0-9]+", " ", name.lower()) + " "
    return any(f" {w} " in n for w in words)


def lint(m: Model, layout_first: bool = False) -> list[Finding]:
    if layout_first and not m.from_drawio:
        layout(m)
    out: list[Finding] = []
    add = lambda sev, code, iid, msg, fix="": out.append(Finding(sev, code, iid, msg, fix))  # noqa: E731

    for code, msg, iid in m.problems:
        add("error", code, iid, msg)

    ids = set(m.items)
    nodes = [i for i in m.items.values() if i.kind == "node"]
    groups = [i for i in m.items.values() if i.kind == "group"]

    def real_ancestors(iid):  # layout 箱を飛ばした祖先
        return [a for a in m.ancestors(iid) if a.group != LAYOUT_ONLY]

    def real_parent(it):
        anc = real_ancestors(it.id)
        return anc[0] if anc else None

    def real_children(g):  # layout 箱を展開した子
        for c in g.children:
            ci = m.items[c]
            if ci.group == LAYOUT_ONLY:
                yield from real_children(ci)
            else:
                yield ci

    def gtypes(it):
        return [a.group for a in real_ancestors(it.id)]

    # ---------------- 作図規約 ----------------
    for it in nodes:
        if it.unmanaged:
            add("error", "N-UNMANAGED", it.id, f"{it.unmanaged}。公式アイコン以外の図形・画像は使わない",
                "公式ライブラリのアイコンに置き換えるか、注記ならグループ/接続線のラベルに移す")
            continue
        if not it.icon:
            add("error", "E-ICON", it.id, "icon が無い", "arch icons search <語> で探して指定する")
        elif not it.raw_icon:
            sug = m.lib.suggest(it.icon) if m.lib else []
            add("error", "E-ICON", it.id, f"アイコン '{it.icon}' は公式ライブラリに無い",
                f"候補: {', '.join(sug) or 'arch icons search で探す'}")
        elif it.raw_icon.kind == "category":
            add("error", "N-CATEGORY-ICON", it.id,
                f"カテゴリアイコン '{it.icon}' をサービスとして使っている (カテゴリアイコンはサービスを表さない)",
                "具体的なサービスアイコンにする")
        elif it.raw_icon.kind == "group":
            add("error", "N-GROUP-ICON", it.id, f"グループアイコン '{it.icon}' を単体で置いている",
                "group として表現する (group: vpc など)")
        if not it.label.strip():
            add("error", "N-LABEL", it.id, "ラベルが無い。アイコンにはサービス名 (と役割) を必ず付ける")
        elif max(len(x) for x in it.label.split("\n")) > 28:
            add("warn", "N-LABEL-LONG", it.id, f"ラベルの1行が長い ({it.label!r})", "改行 (\\n) で2行に分ける")
        w, h = it.icon_wh
        if abs(w - h) > 0.5:
            add("error", "N-ICON-DISTORT", it.id, f"アイコンの縦横比が崩れている ({w:.0f}x{h:.0f})", "正方形に戻す")
        elif abs(w - ICON) > 0.5:
            add("warn", "N-ICON-SIZE", it.id, f"アイコンが {w:.0f}px。図の中のサービスアイコンは 48px に揃える")

    for it in groups:
        if it.group not in GROUPS:
            add("error", "N-GROUP-TYPE", it.id, f"未知のグループ種類 '{it.group}'", f"使えるのは {', '.join(GROUPS)}")
            continue
        if it.group == LAYOUT_ONLY:
            continue
        rp = real_parent(it)
        parent = rp.group if rp else None
        allowed = GROUPS[it.group].parents
        if "*" not in allowed and parent not in allowed:
            want = " / ".join("キャンバス直下" if p is None else p for p in allowed)
            add("error", "N-NESTING", it.id,
                f"{it.group} が {parent or 'キャンバス直下'} の中にある。入れ子の順序は AWS Cloud ⊃ Region ⊃ VPC ⊃ AZ ⊃ Subnet",
                f"{it.group} の親は {want}")
        if not it.children and it.group != LAYOUT_ONLY:
            add("warn", "N-EMPTY-GROUP", it.id, f"{it.group} が空", "不要なら消す")
        if it.group == "region" and not re.search(r"[a-z]{2}-[a-z]+-\d|東京|大阪|バージニア|オレゴン", it.label or ""):
            add("info", "N-REGION-LABEL", it.id, "Region のラベルにリージョン名が無い", "例: 'ap-northeast-1 (東京)'")
        if it.group == "vpc":
            azs = [c.id for c in real_children(it) if c.group == "az"]
            loose = [c.id for c in real_children(it) if c.group in SUBNETS]
            if azs and loose:
                add("warn", "N-SUBNET-OUTSIDE-AZ", it.id, f"AZ を描いた VPC で、AZ の外に subnet がある: {loose}",
                    "subnet は必ずどれか1つの AZ に属する。該当 AZ の中へ移す")
        if it.group == "az":
            bare = [c.id for c in real_children(it) if c.kind == "node"]
            if bare:
                add("warn", "N-NODE-IN-AZ", it.id, f"AZ の直下にリソースがある: {bare}", "リソースは subnet の中に置く")

    for it in nodes:
        rp = real_parent(it)
        if rp and rp.group == "vpc" and it.raw_icon and not _has(it.raw_icon.name, *VPC_EDGE):
            add("warn", "N-NODE-IN-VPC", it.id, f"'{it.raw_icon.name}' が VPC 直下にある",
                "VPC 内のリソースは subnet に置く (VPC 直下に置いてよいのは IGW / ELB / エンドポイント等の境界要素)")

    # 接続線
    seen = Counter()
    for e in m.edges:
        for end in (e.src, e.dst):
            if end not in ids:
                add("error", "E-REF", None, f"接続線 {e.src} -> {e.dst} の '{end}' が存在しない")
        if e.src == e.dst:
            add("warn", "N-EDGE-SELF", e.src, "自分自身への接続線")
        seen[(e.src, e.dst)] += 1
        if len(e.label) > 24:
            add("warn", "N-EDGE-LABEL-LONG", e.src, f"接続線ラベルが長い ({e.label!r})", "12〜16字程度に")
    for (s, d), n in seen.items():
        if n > 1:
            add("warn", "N-EDGE-DUP", s, f"{s} -> {d} の接続線が {n} 本ある")

    # ---------------- 幾何 (見た目の破綻) ----------------
    def inside(a, b, tol=1.0):
        return a[0] >= b[0] - tol and a[1] >= b[1] - tol and a[0] + a[2] <= b[0] + b[2] + tol and a[1] + a[3] <= b[1] + b[3] + tol

    def overlap(a, b):
        return a[0] < b[0] + b[2] - 1 and b[0] < a[0] + a[2] - 1 and a[1] < b[1] + b[3] - 1 and b[1] < a[1] + a[3] - 1

    for it in m.items.values():
        if it.parent and not on_border(it) and not inside(it.box, m.items[it.parent].box):
            add("error", "N-ESCAPE", it.id, f"{it.id} が親 {it.parent} の枠からはみ出している",
                "親グループを広げるか arch edit relayout で自動配置に戻す")
    for parent_children in [m.roots] + [g.children for g in groups]:
        kids = [m.items[c] for c in parent_children]
        for i, a in enumerate(kids):
            for b in kids[i + 1:]:
                if overlap(a.box, b.box):
                    add("error", "N-OVERLAP", a.id, f"{a.id} と {b.id} が重なっている (ラベル領域を含む)")
    for it in nodes:  # 見た目の所属 (どの枠の中に描かれているか) と論理上の所属が一致するか
        cx, cy = it.box[0] + it.box[2] / 2, it.box[1] + ICON / 2
        drawn_in = {g.id for g in groups if g.box[0] <= cx <= g.box[0] + g.box[2] and g.box[1] <= cy <= g.box[1] + g.box[3]}
        logical = {a.id for a in m.ancestors(it.id)}
        if drawn_in != logical:
            add("error", "N-VISUAL-PARENT", it.id,
                f"見た目では {sorted(drawn_in) or 'どの枠にも入っていない'} に描かれているが、所属は {sorted(logical) or 'キャンバス直下'}",
                "draw.io で枠の中へドラッグして親子にするか、arch edit move で所属を直す")

    # 接続線: 経路探索と同じ判定で、線がアイコン・ラベル・見出しを横切っていないかを見る
    obstacles = obstacles_of(route_box(n) for n in nodes)
    near_obs = near_obstacles(route_box(n) for n in nodes)
    headers = group_headers(m)
    unchecked = 0
    for e in m.edges:
        if e.src not in ids or e.dst not in ids:
            continue
        if not e.path:
            unchecked += 1
            continue
        name = f"{e.src} -> {e.dst}"
        hit = blocked(e.path, e.src, e.dst, obstacles)
        if hit:
            add("error", "N-EDGE-THROUGH-NODE", e.src, f"線 {name} が {hit} のアイコンかラベルを横切っている",
                "並び順か layout を変えて近づける。探索で避けきれないときは edges に exit/entry (top/right/bottom/left) を指定する")
        near = [i for i in passes_near(e.path, e.src, e.dst, near_obs) if i not in hit]
        if near:
            add("warn", "N-EDGE-NEAR-NODE", e.src, f"線 {name} が {near} のすぐ脇を通り、そこへつながっているように見える",
                "線でつながる相手どうしを近くに並べ替えるか、exit/entry を変えて離す")
        if any(path_hits(e.path, r) for r in headers):
            add("warn", "N-EDGE-THROUGH-HEADER", e.src, f"線 {name} がグループの見出しを横切っている")
        lh = label_hits(e.path, e.label, label_size, obstacles)
        if lh:
            add("warn", "N-EDGE-LABEL-OVERLAP", e.src, f"線 {name} のラベル '{e.label}' が {lh} に重なっている",
                "ラベルを短くするか、線を長くとれる並びにする")
    # 線のラベル: 別の線が上を通る / ラベルどうし・見出しと重なる
    labels = [(e, label_rect(e.path, e.label, label_size)) for e in m.edges if e.path and e.label]
    for n_i, (e, lr) in enumerate(labels):
        name = f"{e.src} -> {e.dst}"
        crossing = [f"{o.src} -> {o.dst}" for o in m.edges if o.path and o is not e and crosses_label(lr, o.path, e.path)]
        if crossing:
            add("warn", "N-EDGE-LABEL-CROSSED", e.src, f"線 {name} のラベル '{e.label}' の上を {crossing} が通っている",
                "ラベルの文字が線で消される。並び順を変えるか、ラベルを短くして線の長い区間に来るようにする")
        for o, olr in labels[n_i + 1:]:
            # The edge may share a wire trunk, but its label is rendered
            # independently, so coincident text must still be reported.
            if rects_overlap(lr, olr):
                add("warn", "N-EDGE-LABEL-OVERLAP", e.src, f"線 {name} と {o.src} -> {o.dst} のラベルが重なっている")
        if any(rects_overlap(lr, h) for h in headers):
            add("warn", "N-EDGE-LABEL-OVERLAP", e.src, f"線 {name} のラベルがグループの見出しに重なっている")
    # 枠線に沿って走る線 (枠線と見分けがつかない)
    frames = []
    for g in groups:
        if g.group != LAYOUT_ONLY:
            x, y, w, h = g.box
            frames += [((x, y), (x + w, y)), ((x, y + h), (x + w, y + h)), ((x, y), (x, y + h)), ((x + w, y), (x + w, y + h))]
    for e in m.edges:
        if e.path and any(_near_collinear(sg, f) for sg in zip(e.path, e.path[1:]) for f in frames):
            add("warn", "N-EDGE-ON-FRAME", e.src, f"線 {e.src} -> {e.dst} がグループの枠線に沿って走っている",
                "枠線と見分けがつかない。枠と枠の隙間の中央を通るよう、並び順や接続口を見直す")

    # スカスカの枠: 枠の面積が、中身に必要な面積の何倍か (1.5 倍まで。2 倍以上は禁止)
    for g in groups:
        need = group_need(m, g) if g.group != LAYOUT_ONLY else None
        if not need:
            continue
        factor = g.box[2] * g.box[3] / (need[0] * need[1])
        if factor > STRETCH_MAX:
            inside = [c for c in g.children if m.items[c].kind == "node"]
            add("error" if factor >= 2 else "warn", "N-SPARSE", g.id,
                f"{g.group} {g.id} の面積が中身に必要な面積の {factor:.1f} 倍 (中身: {inside or g.children})。上限 {STRETCH_MAX} 倍、2 倍以上は禁止",
                "枠を中身に合わせて縮める (arch edit relayout)。幅や高さをそろえたいなら、並ぶ枠の中身をそろえる")

    # 接続口: 面と面の中の位置 (スロット) が決まりどおりか (決まりは cli.plan_ports。spec.md「流れと接続口」)
    plan = plan_ports(m)
    for i, e in enumerate(m.edges):
        if i not in plan or e.sides == (None, None):
            continue
        (want_sides, want_fracs), name = plan[i], f"{e.src} -> {e.dst}"
        for k, end in ((0, "出口"), (1, "入口")):
            if e.sides[k] != want_sides[k]:
                add("error", "N-PORT-FACE", e.src, f"線 {name} の{end}が {e.sides[k]} の面 (決まりでは {want_sides[k]})",
                    "流れの下流の相手へは 出口=流れの面 / 入口=流れの面。真横・上流の相手へは流れと直交する面を向かい合わせる。"
                    "arch build で引き直す")
            elif abs(e.fracs[k] - want_fracs[k]) > 0.02:
                add("error", "N-PORT-SLOT", e.src,
                    f"線 {name} の{end}が面の {e.fracs[k]:.0%} の位置 (決まりでは {want_fracs[k]:.0%})",
                    "内容 (ラベル・線種) が違う線は面を 2n+1 等分した偶数番目の中央から別々に、同じ内容の線は同じ位置から出す")
    # 重なり: 幹を共有してよいのは、同じ内容で端点を共有する線だけ
    routed = [e for e in m.edges if e.path]
    for a_i, a in enumerate(routed):
        for b in routed[a_i + 1:]:
            if shares_trunk(a.src, a.dst, edge_class(a), b.src, b.dst, edge_class(b)):
                continue
            if any(_near_collinear(s1, s2) for s1 in zip(a.path, a.path[1:]) for s2 in zip(b.path, b.path[1:])):
                add("warn", "N-EDGE-OVERLAP", a.src, f"線 {a.src} -> {a.dst} と {b.src} -> {b.dst} が重なって走っている",
                    "内容が違う線は重ねない。並び順を変えるか、ラベルをそろえて同じ内容として束ねる")
        if not a.dashed and len(a.path) - 2 > 2:
            add("warn", "N-EDGE-BENDS", a.src, f"線 {a.src} -> {a.dst} が {len(a.path) - 2} 回曲がっている (2 回まで)",
                "線でつながる相手どうしを、流れの向き (VPC の中は上→下、外は左→右) に並べ直す")
    if unchecked:
        add("info", "N-EDGE-UNCHECKED", None, f"経路が draw.io 任せの線が {unchecked} 本あり、横切りを検査できない",
            "arch import → arch build で経路を引き直すと検査できる")

    # キャンバスの縦横比: スライドや画面にそのまま貼れるよう 16:9 を目指す
    pts = [(x, y) for i in m.items.values() for x, y in ((i.box[0], i.box[1]), (i.box[0] + i.box[2], i.box[1] + i.box[3]))]
    pts += [p for e in m.edges if e.path for p in e.path]
    if pts:
        w = max(p[0] for p in pts) - min(p[0] for p in pts)
        h = max(p[1] for p in pts) - min(p[1] for p in pts)
        ratio = w / h if h else 0
        # 縦長は禁止。横長の中での 16:9 は目安 (線の素直さを優先する。用紙は to_drawio が 16:9 にする)
        if ratio < 1.0:
            add("error", "N-ASPECT-PORTRAIT", None, f"図全体が縦長 ({ratio:.2f}:1、{w:.0f}x{h:.0f}px)。縦長の図は禁止",
                "VPC の左右にリージョンサービスや利用者を並べる、AZ を横に並べる、など横に広げる (spec.md「キャンバスの縦横比」)")
        elif not 1.4 <= ratio <= 2.2:
            add("info", "N-ASPECT", None, f"図全体の縦横比が {ratio:.2f}:1 (目安は 16:9 = 1.78:1。{w:.0f}x{h:.0f}px)",
                "目安なので、線の素直さを崩してまで合わせない")

    # すべてのノードが、1 本以上の線で何かとつながっているか (図に置いたのに、何とやり取りするのかが読めない)
    connected = {e.src for e in m.edges} | {e.dst for e in m.edges}
    for it in nodes:
        if it.id in connected:
            continue
        name = it.raw_icon.name if it.raw_icon else it.id
        gateway = bool(it.raw_icon and _has(name, *GATEWAYS))
        add("error", "N-NODE-UNCONNECTED", it.id, f"'{name}' ({it.id}) がどの線ともつながっていない",
            "出入りする通信の線を引く (例: 利用者 → IGW → ALB、ECS → NAT → IGW)" if gateway else
            "やり取りする相手への線を引く (監視・認可などの補助の関係なら破線で)。図に不要なら消す")

    # ---------------- 構成の妥当性 ----------------
    vpcs = [g for g in groups if g.group == "vpc"]
    for it in nodes:
        if not it.raw_icon:
            continue
        name, anc = it.raw_icon.name, gtypes(it)
        in_public = "public-subnet" in anc
        in_vpc = "vpc" in anc
        bastion = bool(BASTION.search(it.label))
        if in_public and _has(name, *DATA_STORE):
            add("warn", "A-DB-PUBLIC", it.id, f"データストア '{name}' が public subnet にある", "private subnet に置く")
        if in_public and _word(name, COMPUTE) and not bastion:
            add("warn", "A-COMPUTE-PUBLIC", it.id, f"'{name}' が public subnet にある (踏み台以外)",
                "アプリ層は private subnet に置き、入口は ALB / NAT を通す")
        if in_public and _has(name, "lambda"):
            add("warn", "A-LAMBDA-PUBLIC", it.id, "VPC Lambda を public subnet に置いてもパブリック IP は付かない",
                "private subnet + NAT Gateway (または VPC エンドポイント) にする")
        if _has(name, "nat gateway") and not in_public:
            add("error", "A-NAT-PLACEMENT", it.id, "NAT Gateway は public subnet に置く", "public subnet の中へ移す")
        if _has(name, "internet gateway", "vpn gateway", "carrier gateway") and not (
                it.parent and m.items[it.parent].group == "vpc" and on_border(it)):
            add("warn", "A-GATEWAY-BORDER", it.id, f"'{name}' は VPC の枠線の上に置く (VPC の出入口なので境界をまたいで描く)",
                "VPC の直下に置けば自動で枠線上に乗る (IGW は上辺、VPN Gateway は左辺)。辺は border: で変えられる")
        if it.border in ("top", "right", "bottom", "left") and it.parent and m.items[it.parent].group == LAYOUT_ONLY:
            add("warn", "N-BORDER-ON-LAYOUT", it.id, "枠を描かない layout 箱の枠線上に置いている", "VPC などの見える枠の直下に置く")
        if in_vpc and _word(name, OUTSIDE_VPC) and not _has(name, "endpoint"):
            add("warn", "A-REGIONAL-IN-VPC", it.id, f"'{name}' は VPC の外のリージョンサービス",
                "Region 直下 (VPC の外) に置く。VPC から私設経路で使うなら VPC エンドポイントを描く")
        if "region" in anc and _word(name, GLOBAL):
            add("warn", "A-GLOBAL-IN-REGION", it.id, f"'{name}' はグローバルサービス", "Region の外 (AWS Cloud 直下) に置く")
        if _has(name, "load balanc") and "az" in anc:
            add("warn", "A-ELB-IN-AZ", it.id, "ELB は複数の AZ にまたがる。1つの AZ の中に描くと単一 AZ に見える",
                "VPC 直下 (AZ をまたぐ位置) に置く")

    for v in vpcs:
        sub = [m.items[i] for i in {x.id for x in m.walk(v.children)}]
        azs = [g for g in sub if g.group == "az"]
        has_workload = any(n.kind == "node" and n.raw_icon and (_word(n.raw_icon.name, COMPUTE) or _has(n.raw_icon.name, *DATA_STORE))
                           for n in sub)
        if has_workload and len(azs) < 2:
            add("warn", "A-SINGLE-AZ", v.id, f"VPC {v.id} のワークロードが {len(azs)} AZ にしか無い",
                "本番想定なら 2 AZ 以上に分散して描く。検証環境など意図的なら報告で理由を言う")
        if len(azs) >= 2:
            for n in sub:
                if n.kind == "node" and n.raw_icon and _has(n.raw_icon.name, "rds", "aurora"):
                    others = [x for x in sub if x.kind == "node" and x.raw_icon and x.raw_icon.name == n.raw_icon.name]
                    if len(others) == 1:
                        add("info", "A-DB-SINGLE", n.id, "DB が1つの AZ にしか描かれていない",
                            "Multi-AZ ならスタンバイ (もう一方の AZ) も描くか、ラベルに Multi-AZ と書く")
                    break
        has_public = any(g.group == "public-subnet" for g in sub)
        has_igw = any(n.kind == "node" and n.raw_icon and _has(n.raw_icon.name, "internet gateway") for n in sub)
        if has_public and not has_igw:
            add("warn", "A-NO-IGW", v.id, "public subnet があるのに Internet Gateway が描かれていない")

    out.sort(key=lambda f: (SEV_ORDER[f.severity], f.code, f.id or ""))
    return out


def report(findings: list[Finding]) -> str:
    c = Counter(f.severity for f in findings)
    lines = [f"lint: error {c['error']} / warn {c['warn']} / info {c['info']}"]
    for f in findings:
        lines.append(f"  [{f.severity}] {f.code} {f.id or '-'}: {f.msg}" + (f"\n      → {f.fix}" if f.fix else ""))
    return "\n".join(lines)

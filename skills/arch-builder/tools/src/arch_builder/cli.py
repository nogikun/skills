"""arch-builder: arch.yaml (正本) <-> .drawio の生成・取り込み・編集・検証。

使い方は `uv run --project <skill>/tools arch -h`。規約チェックの中身は rules.py。
"""

from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml

SKILL_DIR = Path(__file__).resolve().parents[3]  # tools/src/arch_builder/cli.py -> <skill>
VENDOR = SKILL_DIR / "vendor"
DEFAULT_LIB = SKILL_DIR / "icons" / "current"
LIB_FILES = {
    "service": "AWS-Architecture-Services.xml",
    "resource": "AWS-Resource-Icons.xml",
    "category": "AWS-Category-Icons.xml",
    "group": "AWS-Architecture-Groups.xml",
}
ICON_DOWNLOAD_URL = "https://aws.amazon.com/architecture/icons/"

# ---------------------------------------------------------------------------
# AWS グループ (Architecture Icons のグループ規約)
# parents: 置いてよい親グループの種類。None はキャンバス直下。
# ---------------------------------------------------------------------------
SUBNETS = ("public-subnet", "private-subnet")
ANY = "*"


@dataclass(frozen=True)
class GroupSpec:
    label: str
    stroke: str
    fill: str = "none"
    dashed: bool = False
    icon: str | None = None  # AWS-Architecture-Groups 内の名前
    parents: tuple = (None,)
    align: str = "left"


GROUPS: dict[str, GroupSpec] = {
    "aws-cloud": GroupSpec("AWS Cloud", "#232F3E", icon="AWS Cloud logo", parents=(None,)),
    "account": GroupSpec("AWS Account", "#E7157B", icon="AWS Account", parents=(None, "aws-cloud")),
    "region": GroupSpec("Region", "#00A4A6", dashed=True, icon="Region", parents=("aws-cloud", "account")),
    "vpc": GroupSpec("VPC", "#8C4FFF", icon="Virtual private cloud VPC", parents=("region",)),
    "az": GroupSpec("Availability Zone", "#00A4A6", dashed=True, parents=("vpc",), align="center"),
    "public-subnet": GroupSpec("Public subnet", "#7AA116", "#F2F6E8", icon="Public subnet", parents=("az", "vpc")),
    "private-subnet": GroupSpec("Private subnet", "#00A4A6", "#E6F6F7", icon="Private subnet", parents=("az", "vpc")),
    "security-group": GroupSpec("Security group", "#DD3522", parents=SUBNETS + ("az", "vpc")),
    "auto-scaling": GroupSpec("Auto Scaling group", "#ED7100", dashed=True, icon="Auto Scaling group",
                              parents=SUBNETS + ("az", "vpc", "security-group")),
    "ec2-contents": GroupSpec("EC2 instance contents", "#ED7100", icon="EC2 instance contents",
                              parents=SUBNETS + ("security-group", "auto-scaling")),
    "spot-fleet": GroupSpec("Spot Fleet", "#ED7100", icon="Spot Fleet", parents=SUBNETS + ("az", "vpc")),
    "corporate-dc": GroupSpec("Corporate data center", "#7D8998", icon="Corporate data center", parents=(None,)),
    "server-contents": GroupSpec("Server contents", "#7D8998", icon="Server contents", parents=(None, "corporate-dc")),
    "generic": GroupSpec("", "#7D8998", dashed=True, parents=(ANY,)),
    # 枠を描かない並べ替え専用の箱。規約チェックでは存在しないものとして扱う
    "layout": GroupSpec("", "none", parents=(ANY,)),
}
LAYOUT_ONLY = "layout"
# VPC の枠線上に置くゲートウェイ類の既定の辺 (親が VPC のときだけ当てる)。境界をまたぐものは境界の上に描く
BORDER_DEFAULTS = {
    "Amazon VPC Internet Gateway": "top",     # インターネット側
    "Amazon VPC Carrier Gateway": "top",
    "Amazon VPC VPN Gateway": "bottom",        # 外 (オンプレ) へ出す口はフッター側
    "AWS Transit Gateway Attachment": "bottom",
}
SIDES = ("top", "right", "bottom", "left")
# 「Amazon VPC NAT Gateway」を「NAT Gateway」でも引けるようにする接頭辞
ALIAS_PREFIXES = re.compile(r"^(amazon vpc|elastic load balancing|amazon ec2|amazon route 53|amazon simple storage "
                            r"service|aws identity access management|amazon cloudwatch|aws lambda|amazon dynamodb) ",
                            re.I)
# 略称 -> 公式名 (略称で書かれがちなものだけ)
ABBREV = {
    "s3": "Amazon Simple Storage Service", "sqs": "Amazon Simple Queue Service",
    "sns": "Amazon Simple Notification Service", "ses": "Amazon Simple Email Service",
    "iam": "AWS Identity and Access Management", "kms": "AWS Key Management Service",
    "ecs": "Amazon Elastic Container Service", "eks": "Amazon Elastic Kubernetes Service",
    "ecr": "Amazon Elastic Container Registry", "ebs": "Amazon Elastic Block Store",
    "elastic file system": "Amazon EFS", "alb": "Elastic Load Balancing Application Load Balancer",
    "nlb": "Elastic Load Balancing Network Load Balancer", "elb": "Elastic Load Balancing",
    "igw": "Amazon VPC Internet Gateway", "nat": "Amazon VPC NAT Gateway", "apigw": "Amazon API Gateway",
}

# レイアウト定数 (px)
ICON = 48
NODE_W = 120          # アイコン + ラベルに確保する枠の幅
LABEL_LINE = 16
PAD_TOP = 48          # グループ見出し (32px アイコン + 余白)
PAD_SIDE = 24
PAD_BOTTOM = 24
GAP = 32
GROUP_ICON = 32
STRETCH_MAX = 1.5     # 枠を中身に必要な大きさの何倍まで広げてよいか (面積比。2 倍以上は error)


# ---------------------------------------------------------------------------
# アイコンライブラリ
# ---------------------------------------------------------------------------
@dataclass
class Icon:
    name: str        # "Amazon RDS"
    title: str       # "Services / Databases / Amazon RDS (48)"
    kind: str        # service | resource | category | group
    category: str    # "Databases"
    data: str        # data:image/svg+xml,<base64>
    w: float
    h: float
    variant: str = ""  # "48" / "48 Light" / "32" など


def lib_dir(arg: str | None = None) -> Path:
    return Path(arg or os.environ.get("ARCH_ICON_LIB") or DEFAULT_LIB)


class Library:
    def __init__(self, path: Path):
        self.path = path
        self.icons: list[Icon] = []
        for kind, fname in LIB_FILES.items():
            f = path / fname
            if not f.exists():
                raise FileNotFoundError(f)
            for e in json.loads(ET.parse(f).getroot().text):
                m = re.fullmatch(r"(.*) \(([^)]*)\)", e["title"])
                full, variant = (m.group(1), m.group(2)) if m else (e["title"], "")
                parts = full.split(" / ")
                self.icons.append(Icon(parts[-1], e["title"], kind, parts[1] if len(parts) > 2 else "",
                                       e["data"], float(e["w"]), float(e["h"]), variant))
        self._by_key: dict[str, Icon] = {}
        self._by_hash: dict[str, Icon] = {}
        # 同名は service > resource > category > group、サイズは 48 / 48 Light / 32 を優先
        rank = {"service": 0, "resource": 1, "category": 2, "group": 3}
        pref = {"48": 0, "48 Light": 1, "32": 2}
        for ic in sorted(self.icons, key=lambda i: (rank[i.kind], pref.get(i.variant, 9))):
            self._by_hash.setdefault(_digest(ic.data), ic)
            if ic.variant not in pref:
                continue
            full = ic.title.rsplit(" (", 1)[0]
            for k in {full, ic.name, _strip_vendor(ic.name), ALIAS_PREFIXES.sub("", ic.name)}:
                self._by_key.setdefault(_norm(k), ic)

    def resolve(self, name: str) -> Icon | None:
        short = _norm(_strip_vendor(name))
        return (self._by_key.get(_norm(name)) or self._by_key.get(short)
                or (self._by_key.get(_norm(ABBREV[short])) if short in ABBREV else None))

    def by_data(self, data: str) -> Icon | None:
        return self._by_hash.get(_digest(data))

    def group_icon(self, name: str) -> Icon | None:
        return next((i for i in self.icons if i.kind == "group" and i.name == name and i.variant == "32"), None)

    def suggest(self, name: str, n: int = 5) -> list[str]:
        keys = {_norm(i.name): i.name for i in self.icons if i.kind in ("service", "resource")}
        return [keys[k] for k in difflib.get_close_matches(_norm(name), keys, n=n, cutoff=0.5)]

    def search(self, word: str) -> list[Icon]:
        w = _norm(ABBREV.get(_norm(word), word))
        seen, out = set(), []
        for ic in self.icons:
            if ic.kind in ("service", "resource") and w in _norm(ic.title) and ic.name not in seen:
                if ic.variant in ("48", "48 Light"):
                    seen.add(ic.name)
                    out.append(ic)
        return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _strip_vendor(s: str) -> str:
    return re.sub(r"^(amazon|aws)\s+", "", s.strip(), flags=re.I)


def _digest(data: str) -> str:
    return hashlib.sha1(data.split(",", 1)[-1].encode()).hexdigest()


# ---------------------------------------------------------------------------
# モデル
# ---------------------------------------------------------------------------
@dataclass
class Item:
    id: str
    kind: str                    # "node" | "group"
    parent: str | None
    label: str = ""
    icon: str | None = None      # node のみ
    group: str | None = None     # group の種類
    layout: str = "row"          # row | column | grid
    cols: int = 2
    pos: list | None = None      # 親からの相対座標 [x, y] (手動配置)
    size: list | None = None     # [w, h] (グループの手動サイズ)
    children: list = field(default_factory=list)
    box: tuple = (0, 0, 0, 0)    # 絶対座標 (x, y, w, h)。レイアウト後に入る
    raw_icon: Icon | None = None
    icon_wh: tuple = (ICON, ICON)  # 実際のアイコン寸法 (.drawio で縮められていないか見る)
    unmanaged: str | None = None  # draw.io 上で追加された未知要素の説明
    flow: str | None = None       # グループの流れの向き: down (上から入って下へ出る) | right (左から入って右へ出る)
    border: str | None = None     # 親の枠線上に置く辺: top | right | bottom | left ("none" で既定を打ち消す)


@dataclass
class Edge:
    src: str
    dst: str
    label: str = ""
    dashed: bool = False
    arrow: str = "end"           # end | both | none
    exit: str | None = None      # 出口の辺を固定する: top | right | bottom | left (既定は経路探索に任せる)
    entry: str | None = None     # 入口の辺を固定する
    path: list | None = None     # 絶対座標の折れ線 (経路探索の結果 / .drawio の waypoint)
    sides: tuple = (None, None)  # 実際に使った (出口, 入口) の面
    fracs: tuple = (0.5, 0.5)    # 面の中の位置 (0〜1。上下の面なら左から、左右の面なら上から)


@dataclass
class Model:
    title: str
    items: dict[str, Item]
    roots: list[str]
    edges: list[Edge]
    lib: Library | None = None
    problems: list = field(default_factory=list)  # 読み込み時点の問題 (code, msg, id)
    from_drawio: bool = False  # True なら box は draw.io 上の実座標 (自動配置し直さない)
    notes: list = field(default_factory=list)  # 図の下に書く前提・注記 (「社員はインターネット経由で入る」など)

    def ancestors(self, iid: str):
        p = self.items[iid].parent
        while p:
            yield self.items[p]
            p = self.items[p].parent

    def walk(self, ids=None):
        for i in (self.roots if ids is None else ids):
            yield self.items[i]
            yield from self.walk(self.items[i].children)


def load_yaml(path: Path, lib: Library | None) -> Model:
    doc = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    m = Model(doc.get("title", path.stem), {}, [], [], lib)

    def add(raw: dict, parent: str | None):
        iid = str(raw.get("id") or "")
        if not iid:
            m.problems.append(("E-ID", f"id が無い要素がある: {raw}", None))
            return
        if iid in m.items:
            m.problems.append(("E-ID", f"id '{iid}' が重複している", iid))
            return
        is_group = "group" in raw or "children" in raw
        it = Item(iid, "group" if is_group else "node", parent, str(raw.get("label", "")),
                  raw.get("icon"), raw.get("group", "generic") if is_group else None,
                  raw.get("layout", "row"), int(raw.get("cols", 2)), raw.get("pos"), raw.get("size"))
        it.border = raw.get("border")
        it.flow = raw.get("flow")
        m.items[iid] = it
        (m.items[parent].children if parent else m.roots).append(iid)
        for c in raw.get("children") or []:
            add(c, iid)

    for raw in doc.get("items") or []:
        add(raw, None)
    for e in doc.get("edges") or []:
        m.edges.append(Edge(str(e["from"]), str(e["to"]), str(e.get("label", "")),
                            bool(e.get("dashed", False)), e.get("arrow", "end"), e.get("exit"), e.get("entry")))
    # 「- 前提: ...」は YAML では辞書になる。書いたとおりの 1 行に戻す
    m.notes = [n if isinstance(n, str) else ", ".join(f"{k}: {v}" for k, v in n.items()) for n in doc.get("notes") or []]
    _attach_icons(m)
    for it in m.items.values():  # ゲートウェイ類は、書かなくても VPC の枠線上に置く
        if (it.kind == "node" and it.border is None and it.raw_icon and it.parent
                and m.items[it.parent].group == "vpc"):
            it.border = BORDER_DEFAULTS.get(it.raw_icon.name)
    return m


def _attach_icons(m: Model):
    if not m.lib:
        return
    for it in m.items.values():
        if it.kind == "node" and it.icon and not it.raw_icon:
            it.raw_icon = m.lib.resolve(it.icon)


def on_border(it: Item) -> bool:
    return it.kind == "node" and it.border in SIDES


def dump_yaml(m: Model) -> str:
    def ser(iid):
        it = m.items[iid]
        d = {"id": it.id}
        if it.kind == "group":
            d["group"] = it.group
            if it.label:
                d["label"] = it.label
            if it.layout != "row":
                d["layout"] = it.layout
            if it.layout == "grid":
                d["cols"] = it.cols
            if it.flow:
                d["flow"] = it.flow
        else:
            d["icon"] = it.icon
            d["label"] = it.label
            if it.border:
                d["border"] = it.border
        if it.pos:
            d["pos"] = [round(v) for v in it.pos]
        if it.size:
            d["size"] = [round(v) for v in it.size]
        if it.kind == "group":
            d["children"] = [ser(c) for c in it.children]
        return d

    edges = []
    for e in m.edges:
        d = {"from": e.src, "to": e.dst}
        if e.label:
            d["label"] = e.label
        if e.dashed:
            d["dashed"] = True
        if e.arrow != "end":
            d["arrow"] = e.arrow
        for k in ("exit", "entry"):
            if getattr(e, k):
                d[k] = getattr(e, k)
        edges.append(d)
    doc = {"title": m.title, "items": [ser(r) for r in m.roots], "edges": edges}
    if m.notes:
        doc["notes"] = m.notes
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120, default_flow_style=None)


# ---------------------------------------------------------------------------
# レイアウト (入れ子の箱詰め。pos/size があればそれを優先)
# ---------------------------------------------------------------------------
def label_size(text: str) -> tuple[float, float]:
    lines = text.split("\n") if text else []
    width = max((sum(14 if ord(c) > 0x2E7F else 7.5 for c in ln) for ln in lines), default=0)
    return width, len(lines) * LABEL_LINE


def node_box_size(it: Item) -> tuple[float, float]:
    lw, lh = label_size(it.label)
    return max(NODE_W, lw + 8), ICON + 8 + lh


def measure(m: Model, iid: str) -> tuple[float, float]:
    """箱の (w, h) を返し、子の相対位置を it._rel に入れる。"""
    it = m.items[iid]
    if it.kind == "node":
        return node_box_size(it)
    if (it.group == "generic" and not it.children
            and any(it.id in (e.src, e.dst) for e in m.edges)):
        # 接続済み generic leaf はコンテナではなく、ラベル付きの外部端点として小さく描く。
        lw, lh = label_size(it.label)
        return max(140, lw + 24), max(64, lh + 24)
    sizes = [(c, *measure(m, c)) for c in it.children]
    edge_nodes = [s for s in sizes if on_border(m.items[s[0]]) and not m.items[s[0]].pos]
    sizes = [s for s in sizes if s not in edge_nodes]
    auto = [s for s in sizes if not m.items[s[0]].pos]
    manual = [s for s in sizes if m.items[s[0]].pos]
    # 同じ行 (row) の子グループは高さ、列 (column) の子グループは幅を揃える
    grp = [s for s in auto if m.items[s[0]].kind == "group"]
    # ただし引き伸ばすのは 1.5 倍まで。それ以上は中身が少ないのに枠だけ広い「スカスカの枠」になる (lint の N-SPARSE)
    if it.layout == "row" and grp:
        hmax = max(s[2] for s in grp)
        auto = [(c, w, hmax if m.items[c].kind == "group" and hmax <= h * STRETCH_MAX else h) for c, w, h in auto]
    if it.layout == "column" and grp:
        wmax = max(s[1] for s in grp)
        auto = [(c, wmax if m.items[c].kind == "group" and wmax <= w * STRETCH_MAX else w, h) for c, w, h in auto]

    rel: dict[str, tuple] = {}
    right = bottom = 0.0
    for c, w, h in manual:
        x, y = m.items[c].pos
        if m.items[c].kind == "node":  # pos はアイコン自体の位置。確保枠はラベル分だけ左右に広い
            x -= (w - ICON) / 2
        rel[c] = (x, y, w, h)
        right, bottom = max(right, x + w), max(bottom, y + h)
    top, side, bottom_pad = (0, 0, 0) if it.group == LAYOUT_ONLY else (PAD_TOP, PAD_SIDE, PAD_BOTTOM)
    # 枠線上のアイコンは半分が内側に入り、上辺ならその下にラベルも付く。その分だけ内側の余白を広げる
    by_side = {sd: [s for s in edge_nodes if m.items[s[0]].border == sd] for sd in SIDES}
    if by_side["top"]:
        top = max(top, ICON / 2 + max(s[2] for s in by_side["top"]) - ICON + GAP)
    if by_side["bottom"]:
        bottom_pad = max(bottom_pad, ICON / 2 + GAP)
    left_pad = max([side] + [s[1] / 2 + GAP / 2 for s in by_side["left"]])
    side = max([side] + [s[1] / 2 + GAP / 2 for s in by_side["right"]])
    y0 = bottom + GAP if manual else top
    x, y = left_pad, y0
    if it.layout == "row":
        for c, w, h in auto:
            rel[c] = (x, y, w, h)
            x += w + GAP
    elif it.layout == "column":
        colw = max((s[1] for s in auto), default=0)
        for c, w, h in auto:  # 縦に並べるときは中央でそろえる (アイコンの中心が一直線になり、線が段差なくつながる)
            rel[c] = (x + (colw - w) / 2, y, w, h)
            y += h + GAP
    else:  # grid
        cols = max(1, it.cols)
        cw = max((s[1] for s in auto), default=0)
        ch = max((s[2] for s in auto), default=0)
        for i, (c, w, h) in enumerate(auto):
            rel[c] = (left_pad + (i % cols) * (cw + GAP), y0 + (i // cols) * (ch + GAP), w, h)
    for bx, by, bw, bh in rel.values():
        right, bottom = max(right, bx + bw), max(bottom, by + bh)
    it._rel = rel
    w = max(right + side, 200 if not it.children else 0)
    h = max(bottom + bottom_pad, 120 if not it.children else 0)
    it._natural = (w, h)  # 中身そのものの大きさ。これより広げた分は _place で中央寄せに使う
    if it.group != LAYOUT_ONLY:  # 中身を中央に置いたとき、上から入る線が見出しを横切らない幅をとる
        spec = GROUPS.get(it.group, GROUPS["generic"])
        w = max(w, 2 * ((40 if spec.icon else 8) + label_size(it.label or spec.label)[0] + 8))
    if it.size:
        w, h = max(w, it.size[0]), max(h, it.size[1])
    # 枠線上のアイコン: 中心を辺の線に乗せ、同じ辺に複数あれば等間隔に並べる
    for sd, group in by_side.items():
        for i, (c, bw, bh) in enumerate(group):
            f = (i + 1) / (len(group) + 1)
            if sd in ("top", "bottom"):
                rel[c] = (w * f - bw / 2, (0 if sd == "top" else h) - ICON / 2, bw, bh)
            else:
                rel[c] = ((0 if sd == "left" else w) - bw / 2, h * f - ICON / 2, bw, bh)
    return w, h


def layout(m: Model):
    x = 40.0
    for r in m.roots:
        it = m.items[r]
        w, h = measure(m, r)
        px, py = it.pos if it.pos else (x, 40.0)
        if it.pos and it.kind == "node":  # pos はアイコン自体の位置 (measure と同じ扱い)
            px -= (w - ICON) / 2
        _place(m, r, px, py, w, h)
        x = max(x, px + w + GAP * 2)
    route(m)


def _place(m: Model, iid: str, x, y, w, h):
    it = m.items[iid]
    it.box = (x, y, w, h)
    rel = getattr(it, "_rel", {})
    # 揃えるために引き伸ばされた枠では、アイコンだけの中身を中央に寄せる (片側に空きが偏らないように)
    dx = dy = 0.0
    inner = [c for c in rel if not on_border(m.items[c])]
    if inner and all(m.items[c].kind == "node" and not m.items[c].pos for c in inner):
        nw, nh = it._natural
        dx, dy = (w - nw) / 2, (h - nh) / 2
    for c, (rx, ry, rw, rh) in rel.items():
        shift = (0, 0) if on_border(m.items[c]) else (dx, dy)
        _place(m, c, x + rx + shift[0], y + ry + shift[1], rw, rh)


def group_need(m: Model, g: Item) -> tuple[float, float] | None:
    """枠が中身を収めるのに必要な (幅, 高さ)。中身の外接矩形 + 規定の余白 + 見出しの幅。枠線上の要素は数えない。"""
    kids = [m.items[c] for c in g.children if not on_border(m.items[c])]
    if not kids:
        return None
    x0 = min(k.box[0] for k in kids)
    y0 = min(k.box[1] for k in kids)
    x1 = max(k.box[0] + k.box[2] for k in kids)
    y1 = max(k.box[1] + k.box[3] for k in kids)
    spec = GROUPS.get(g.group, GROUPS["generic"])
    header = (40 if spec.icon else 8) + label_size(g.label or spec.label)[0] + 8
    top = PAD_TOP + (ICON / 2 + LABEL_LINE * 2 + GAP if any(on_border(m.items[c]) and m.items[c].border == "top"
                                                         for c in g.children) else 0)
    return max(x1 - x0 + 2 * PAD_SIDE, 2 * header), y1 - y0 + top + PAD_BOTTOM


def icon_box(it: Item) -> tuple:
    x, y, w, _ = it.box
    iw, ih = it.icon_wh
    return (x + (w - iw) / 2, y, iw, ih)


# ---------------------------------------------------------------------------
# 接続線の経路 (探索は route.py)
# ---------------------------------------------------------------------------
def route_box(it: Item):
    from arch_builder.route import Box
    if it.kind == "group":
        return Box(it.id, it.box, None, obstacle=False)
    ib = icon_box(it)
    lb = None
    if it.label:
        lw, lh = label_size(it.label)
        lb = (ib[0] + ib[2] / 2 - lw / 2 - 2, ib[1] + ib[3] + 2, lw + 4, lh + 2)
    return Box(it.id, ib, lb)


def group_headers(m: Model) -> list[tuple]:
    """グループの見出し (アイコン + 名前) の矩形。線が横切ると名前が読めなくなる。"""
    out = []
    for it in m.items.values():
        if it.kind != "group" or it.group == LAYOUT_ONLY:
            continue
        spec = GROUPS.get(it.group, GROUPS["generic"])
        x, y, w, _ = it.box
        lw, _ = label_size(it.label or spec.label)
        if spec.align == "center":
            out.append((x + w / 2 - lw / 2 - 4, y, lw + 8, 28))
        else:
            out.append((x, y, min(w, (40 if spec.icon else 8) + lw + 8), GROUP_ICON))
    return out


FACES = {"down": ("top", "bottom"), "right": ("left", "right")}


def faces(m: Model, iid: str) -> tuple[str, str]:
    """要素の (入口の面, 出口の面)。入口と出口は向かい合わせにして、線がアイコンを一直線に通り抜けるようにする。
    流れは、いちばん近い祖先の flow: で決まる。書かなければ VPC の中は down (ヘッダー → ボディ → フッター)、外は right。"""
    for a in m.ancestors(iid):
        if a.flow in FACES:
            return FACES[a.flow]
        if a.group == "vpc":
            return FACES["down"]
    return FACES["right"]


def edge_class(e: Edge) -> tuple:
    """線の「内容」。ラベル (プロトコルなど) と線種が同じ線だけが、同じ口と幹を共有する。"""
    return (e.label, e.dashed)


def _center(r):
    return r[0] + r[2] / 2, r[1] + r[3] / 2


def _face(m: Model, iid: str, other: str, role: str) -> str:
    """線の端の面。role は "out" (線の出口側) か "in" (入口側)。面は「受け手の入口だから」ではなく通信の向きで決める。

    - 順方向 (外 → 中。相手が流れの下流): 流れの面 (VPC の中なら 出口 = 下 / 入口 = 上、外なら 出口 = 右 / 入口 = 左)
    - 逆方向 (中 → 外。相手が流れの上流。例: ECS → NAT、NAT → IGW): 点対称に入れ替える。出口は自分の入口の面、
      入口は相手の出口の面 (NAT → IGW は IGW の下の面に入る)。行きの線と同じ面に乗る分は、スロットで別の位置に分かれる
    - 真横 (流れの方向に重なりがある): 流れと直交する面を向かい合わせる (VPC の中なら左右、外なら上下)
    - 枠線上のゲートウェイ: 枠線に直交する面のうち、相手のいる側"""
    it = m.items[iid]
    b, o = route_box(it).icon, route_box(m.items[other]).icon
    (bx, by), (ox, oy) = _center(b), _center(o)
    if on_border(it):
        if it.border in ("top", "bottom"):
            return "bottom" if oy > by else "top"
        return "right" if ox > bx else "left"
    if it.kind == "group":  # グループに直接つなぐ線は、相手の方を向いた面
        if abs(ox - bx) * b[3] >= abs(oy - by) * b[2]:
            return "right" if ox >= bx else "left"
        return "bottom" if oy >= by else "top"
    f_in, f_out = faces(m, iid)
    down = f_in == "top"
    lo, hi = (1, 3) if down else (0, 2)  # 流れの軸 (VPC の中は y、外は x)
    other_after = o[lo] >= b[lo] + b[hi]    # 相手が自分より下流
    other_before = o[lo] + o[hi] <= b[lo]   # 相手が自分より上流
    forward = other_after if role == "out" else other_before
    reverse = other_before if role == "out" else other_after
    if forward:
        return f_out if role == "out" else f_in
    if reverse:
        return f_in if role == "out" else f_out
    if down:
        return "right" if ox >= bx else "left"
    return "bottom" if oy >= by else "top"


def plan_ports(m: Model) -> dict[int, tuple]:
    """線ごとの (出口の面, 入口の面), (出口の位置, 入口の位置)。

    面の中の位置 (スロット): 1 つの面を内容の違う n 種類の線が使うなら、面を 2n+1 等分し、
    2, 4, ..., 2n 番目の区間の中央に置く (n=1 は中央、n=2 は 30% と 70%)。同じ内容の線は同じスロットを共有する。
    スロットの並びは相手の位置の順 (線どうしが交差しないように)。"""
    plan: dict[int, list] = {}
    use: dict[tuple, list] = {}
    for i, e in enumerate(m.edges):
        if e.src not in m.items or e.dst not in m.items or e.src == e.dst:
            continue
        es = e.exit or _face(m, e.src, e.dst, "out")
        ts = e.entry or _face(m, e.dst, e.src, "in")
        plan[i] = [(es, ts), [0.5, 0.5]]
        for node, side, other, role, k in ((e.src, es, e.dst, "out", 0), (e.dst, ts, e.src, "in", 1)):
            ox, oy = _center(route_box(m.items[other]).icon)
            use.setdefault((node, side), []).append((i, k, (role, *edge_class(e)), ox if side in ("top", "bottom") else oy))
    for entries in use.values():
        classes: dict = {}
        for _, _, cls, coord in entries:
            classes.setdefault(cls, []).append(coord)
        order = sorted(classes, key=lambda c: sum(classes[c]) / len(classes[c]))
        n = len(order)
        for i, k, cls, _ in entries:
            plan[i][1][k] = (2 * (order.index(cls) + 1) - 0.5) / (2 * n + 1)
    return {i: (sides, tuple(fr)) for i, (sides, fr) in plan.items()}


def route(m: Model):
    from arch_builder.route import route_all
    boxes = {it.id: route_box(it) for it in m.items.values() if it.kind == "node" or
             any(it.id in (e.src, e.dst) for e in m.edges)}
    plan = plan_ports(m)
    todo = [i for i in plan if m.edges[i].src in boxes and m.edges[i].dst in boxes]
    # 短い線から引く: 近い要素どうしの素直な線を先に確定させ、長い線に迂回させる
    todo.sort(key=lambda i: abs(boxes[m.edges[i].src].icon[0] - boxes[m.edges[i].dst].icon[0])
              + abs(boxes[m.edges[i].src].icon[1] - boxes[m.edges[i].dst].icon[1]))
    channels = [g.box for g in m.items.values() if g.kind == "group" and g.group != LAYOUT_ONLY]

    # AZ ごとに同じ役割の要素 (同じアイコンで、祖先グループの並びが同じ) へ向かう線は、まとめて対称に引く
    def peer(iid):
        it = m.items[iid]
        return (it.raw_icon.name if it.raw_icon else iid, tuple(a.group for a in m.ancestors(iid) if a.group != LAYOUT_ONLY))

    groups: dict = {}
    for k, i in enumerate(todo):
        e = m.edges[i]
        key = (peer(e.src), peer(e.dst), edge_class(e)) if not (e.exit or e.entry) else ("solo", i)
        groups.setdefault(key, []).append(k)
    edges = []
    for i in todo:
        e, ((es, ts), (xf, ef)) = m.edges[i], plan[i]
        edges.append((e.src, e.dst, e.label, es, ts, xf, ef, edge_class(e)))
    results = route_all(boxes, edges, group_headers(m), label_size, channels, list(groups.values()))
    for i, (path, es, ts) in zip(todo, results):
        m.edges[i].path, m.edges[i].sides, m.edges[i].fracs = path, (es, ts), plan[i][1]


# ---------------------------------------------------------------------------
# .drawio 書き出し
# ---------------------------------------------------------------------------
def group_style(spec: GroupSpec, has_icon: bool, endpoint: bool = False) -> str:
    if endpoint:
        return ("rounded=1;whiteSpace=wrap;html=1;container=0;fillColor=#FFFFFF;strokeColor=#7D8998;dashed=0;"
                "fontColor=#232F3E;fontSize=12;align=center;verticalAlign=middle;spacing=8;")
    if spec.stroke == "none":
        return "rounded=0;html=1;container=1;collapsible=0;recursiveResize=0;fillColor=none;strokeColor=none;pointerEvents=0;"
    return ";".join([
        "rounded=0", "whiteSpace=wrap", "html=1", "container=1", "collapsible=0", "recursiveResize=0",
        f"fillColor={spec.fill}", f"strokeColor={spec.stroke}", f"dashed={int(spec.dashed)}",
        f"fontColor={spec.stroke if spec.stroke != '#7AA116' else '#248814'}", "fontSize=12", "fontStyle=1",
        "verticalAlign=top", f"align={spec.align}", f"spacingLeft={40 if has_icon else 8}", "spacingTop=4",
        "pointerEvents=0",
    ]) + ";"


def node_style(icon: Icon) -> str:
    return ("shape=image;html=1;aspect=fixed;imageAspect=0;verticalLabelPosition=bottom;verticalAlign=top;"
            f"labelBackgroundColor=none;fontSize=12;fontColor=#232F3E;image={icon.data};")


def edge_style(e: Edge) -> str:
    s = ("edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;jettySize=auto;orthogonalLoop=1;"
         "strokeColor=#545B64;strokeWidth=2;fontSize=11;fontColor=#232F3E;labelBackgroundColor=#FFFFFF;")
    s += {"end": "endArrow=block;endFill=1;startArrow=none;", "both": "endArrow=block;endFill=1;startArrow=block;startFill=1;",
          "none": "endArrow=none;startArrow=none;"}.get(e.arrow, "endArrow=block;endFill=1;")
    return s + ("dashed=1;" if e.dashed else "")


def to_drawio(m: Model) -> str:
    if not m.lib:
        raise SystemExit("アイコンライブラリが必要 (doctor を実行)")
    layout(m)
    mxfile = ET.Element("mxfile", host="arch-builder")
    diagram = ET.SubElement(mxfile, "diagram", id="arch", name=m.title)
    # 用紙は図全体を含む 16:9 にする (スライドや画面にそのまま貼れるように)
    right = max((m.items[r].box[0] + m.items[r].box[2] for r in m.roots), default=0) + 40
    bottom = max((m.items[r].box[1] + m.items[r].box[3] for r in m.roots), default=0) + 40 + 24 * bool(m.notes)
    pw = max(right, bottom * 16 / 9)
    model = ET.SubElement(diagram, "mxGraphModel", dx="1400", dy="900", grid="1", gridSize="10", guides="1",
                          tooltips="1", connect="1", arrows="1", fold="1", page="1", pageScale="1",
                          pageWidth=_f(pw), pageHeight=_f(pw * 9 / 16),
                          background="#FFFFFF", math="0", shadow="0", darkMode="0")
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")

    def vertex(iid, label, style, parent, geo, **meta):
        obj = ET.SubElement(root, "object", id=iid, label=_html_label(label), **meta)
        cell = ET.SubElement(obj, "mxCell", style=style, vertex="1", parent=parent)
        ET.SubElement(cell, "mxGeometry", x=_f(geo[0]), y=_f(geo[1]), width=_f(geo[2]), height=_f(geo[3]),
                      **{"as": "geometry"})

    for it in m.walk():
        parent = it.parent or "1"
        px, py = (m.items[it.parent].box[:2] if it.parent else (0, 0))
        if it.kind == "group":
            spec = GROUPS.get(it.group, GROUPS["generic"])
            gi = m.lib.group_icon(spec.icon) if spec.icon else None
            x, y, w, h = it.box
            endpoint = (it.group == "generic" and not it.children
                        and any(it.id in (e.src, e.dst) for e in m.edges))
            vertex(it.id, it.label or spec.label, group_style(spec, bool(gi), endpoint), parent, (x - px, y - py, w, h),
                   arch_kind="group", arch_group=it.group, arch_flow=it.flow or "")
            if gi:
                vertex(f"{it.id}__icon", "", node_style(gi) + "movable=0;resizable=0;deletable=0;editable=0;",
                       it.id, (0, 0, GROUP_ICON, GROUP_ICON), arch_kind="group-icon")
        else:
            if not it.raw_icon:
                raise SystemExit(f"アイコンを解決できない: {it.id} icon={it.icon!r} (lint で候補を確認)")
            x, y, w, h = icon_box(it)
            vertex(it.id, it.label, node_style(it.raw_icon), parent, (x - px, y - py, w, h),
                   arch_kind="node", arch_icon=it.raw_icon.name)
    if m.notes:  # 前提・注記は図の下に並べる。図の要素ではないので lint や経路探索の対象にしない
        bottom = max((m.items[r].box[1] + m.items[r].box[3] for r in m.roots), default=0)
        lines = "<br>".join(f"※ {n}" for n in m.notes)
        vertex("__notes", lines, "text;html=1;align=left;verticalAlign=top;fontSize=14;fontColor=#545B64;"
               "whiteSpace=wrap;", "1", (40, bottom + 24, pw - 80, 20 * len(m.notes) + 8), arch_kind="note")
    from arch_builder.route import port_style
    for i, e in enumerate(m.edges):
        style = edge_style(e)
        if e.path:  # 経路探索の結果を、接続口と waypoint として固定する
            style += (port_style(route_box(m.items[e.src]), e.sides[0], "exit", e.fracs[0])
                      + port_style(route_box(m.items[e.dst]), e.sides[1], "entry", e.fracs[1]))
        obj = ET.SubElement(root, "object", id=f"e{i}__{e.src}__{e.dst}", label=_html_label(e.label),
                            arch_exit=e.exit or "", arch_entry=e.entry or "")
        cell = ET.SubElement(obj, "mxCell", style=style, edge="1", parent="1", source=e.src, target=e.dst)
        geo = ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
        if e.path and len(e.path) > 2:
            arr = ET.SubElement(geo, "Array", **{"as": "points"})
            for x, y in e.path[1:-1]:
                ET.SubElement(arr, "mxPoint", x=_f(x), y=_f(y))
    ET.indent(mxfile)
    return ET.tostring(mxfile, encoding="unicode")


def _f(v: float) -> str:
    return str(round(v, 1)).removesuffix(".0")


def _html_label(s: str) -> str:
    return s.replace("\n", "<br>")


# ---------------------------------------------------------------------------
# .drawio 取り込み (draw.io で手直しした図を正本へ戻す)
# ---------------------------------------------------------------------------
def read_graph(path: Path) -> ET.Element:
    root = ET.parse(path).getroot()
    if root.tag == "mxGraphModel":
        return root
    diagram = root.find("diagram")
    if diagram is None:
        raise SystemExit(f"{path}: <diagram> が無い")
    g = diagram.find("mxGraphModel")
    if g is not None:
        return g
    raw = zlib.decompress(base64.b64decode(diagram.text.strip()), -15)  # 圧縮保存された .drawio
    return ET.fromstring(urllib.parse.unquote(raw.decode()))


def _style_map(style: str) -> dict[str, str]:
    out = {}
    for part in (style or "").split(";"):
        if part:
            k, _, v = part.partition("=")
            out[k] = v
    return out


def _plain(label: str) -> str:
    s = re.sub(r"<br\s*/?>|</div>\s*<div>", "\n", label or "")
    s = re.sub(r"<[^>]+>", "", s)
    return s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()


def load_drawio(path: Path, lib: Library | None) -> Model:
    g = read_graph(path)
    diagram = ET.parse(path).getroot().find("diagram")
    m = Model(diagram.get("name", path.stem) if diagram is not None else path.stem, {}, [], [], lib)
    m.from_drawio = True  # 座標は draw.io 上の実物。lint で自動配置し直さない
    cells = []  # (id, attrs, mxCell)
    for el in g.find("root"):
        if el.tag == "mxCell":
            cells.append((el.get("id"), {"label": el.get("value", "")}, el))
        else:  # object / UserObject
            c = el.find("mxCell")
            cells.append((el.get("id"), dict(el.attrib), c))
    known = {cid for cid, _, c in cells if c is not None and c.get("vertex") == "1"}
    for cid, attrs, c in cells:
        if attrs.get("arch_kind") == "note":
            m.notes = [re.sub(r"^※\s*", "", ln) for ln in _plain(attrs.get("label", "")).split("\n") if ln.strip()]
            continue
        if c is None or c.get("vertex") != "1" or attrs.get("arch_kind") == "group-icon":
            continue
        st = _style_map(c.get("style", ""))
        geo = c.find("mxGeometry")
        gx = [float(geo.get(k, 0)) for k in ("x", "y", "width", "height")] if geo is not None else [0, 0, 0, 0]
        parent = c.get("parent")
        parent = parent if parent in known else None
        label = _plain(attrs.get("label", ""))
        kind = attrs.get("arch_kind")
        if kind == "group" or (not kind and st.get("container") == "1"):
            it = Item(cid, "group", parent, label, group=attrs.get("arch_group", "generic"), pos=gx[:2], size=gx[2:])
            it.flow = attrs.get("arch_flow") or None
            if it.label == GROUPS.get(it.group, GROUPS["generic"]).label:
                it.label = ""
        elif st.get("image", "").startswith("data:"):
            ic = lib.by_data(st["image"]) if lib else None
            it = Item(cid, "node", parent, label, icon=attrs.get("arch_icon") or (ic.name if ic else None))
            it.raw_icon = ic
            if ic is None:
                it.unmanaged = "公式ライブラリに無い画像"
        else:
            it = Item(cid, "node", parent, label)
            it.unmanaged = f"管理外の図形 (style={c.get('style', '')[:40]})"
        it._geo = gx
        m.items[cid] = it
    # 親子を張り、絶対座標を出す
    order = [cid for cid, _, c in cells if cid in m.items]
    for cid in order:
        it = m.items[cid]
        (m.items[it.parent].children if it.parent else m.roots).append(cid)
    for it in m.walk():
        px, py = m.items[it.parent].box[:2] if it.parent else (0, 0)
        x, y, w, h = it._geo
        it.box = (px + x, py + y, w, h)
    # node の box を「アイコン + ラベル」の確保枠に揃える (yaml から作ったときと同じ意味にする)
    for it in m.items.values():
        if it.kind == "node":
            x, y, w, h = it.box
            it.icon_wh = (w, h)
            bw, bh = node_box_size(it)
            it.box = (x + w / 2 - bw / 2, y, bw, h + bh - ICON)
            if it.parent:  # アイコンの中心が親の枠線の上にあれば、枠線上に置いたものとして扱う
                px, py, pw, ph = m.items[it.parent].box
                cx, cy = x + w / 2, y + h / 2
                near = {"top": abs(cy - py), "bottom": abs(cy - py - ph), "left": abs(cx - px), "right": abs(cx - px - pw)}
                sd = min(near, key=near.get)
                if near[sd] <= 4:
                    it.border = sd
    for cid, attrs, c in cells:
        if c is not None and c.get("edge") == "1":
            s, t = c.get("source"), c.get("target")
            if not s or not t:
                m.problems.append(("E-EDGE", f"接続線 {cid} の端が図形に繋がっていない", cid))
                continue
            st = _style_map(c.get("style", ""))
            arrow = "none" if st.get("endArrow") == "none" and st.get("startArrow") in (None, "none") else (
                "both" if st.get("startArrow") not in (None, "none") else "end")
            label = _plain(attrs.get("label", "") or c.get("value", ""))
            e = Edge(s, t, label, st.get("dashed") == "1", arrow,
                     attrs.get("arch_exit") or None, attrs.get("arch_entry") or None)
            if s in m.items and t in m.items:
                e.path = _drawio_path(m.items[s], m.items[t], st, c.find("mxGeometry"))
                if "exitX" in st and "entryX" in st:
                    ports = [_side_frac(st, "exit"), _side_frac(st, "entry")]
                    e.sides, e.fracs = tuple(p[0] for p in ports), tuple(p[1] for p in ports)
            m.edges.append(e)
    return m


def _side_frac(st: dict, prefix: str) -> tuple[str, float]:
    """draw.io の exitX/exitY から (面, 面の中の位置) を読む。"""
    x, y = float(st[f"{prefix}X"]), float(st[f"{prefix}Y"])
    if y in (0.0, 1.0) and 0 < x < 1:
        return ("top" if y == 0 else "bottom"), x
    return ("left" if x == 0 else "right"), y


def _drawio_path(src: Item, dst: Item, st: dict, geo) -> list | None:
    """接続口 + waypoint から実際の折れ線を復元する。draw.io 任せの経路 (waypoint 無し) は復元できないので None。"""
    pts = geo.findall("Array/mxPoint") if geo is not None else []
    if "exitX" not in st or "entryX" not in st:
        return None

    def port(it, prefix):
        x, y, w, h = route_box(it).icon
        return (x + float(st[f"{prefix}X"]) * w + float(st.get(f"{prefix}Dx", 0)),
                y + float(st[f"{prefix}Y"]) * h + float(st.get(f"{prefix}Dy", 0)))

    path = [port(src, "exit")] + [(float(p.get("x", 0)), float(p.get("y", 0))) for p in pts] + [port(dst, "entry")]
    # draw.io で要素を動かすと waypoint が取り残されて斜めになる。そのときは検査しない
    if any(abs(a[0] - b[0]) > 0.5 and abs(a[1] - b[1]) > 0.5 for a, b in zip(path, path[1:])):
        return None
    return path


# ---------------------------------------------------------------------------
# コマンド
# ---------------------------------------------------------------------------
def open_lib(args, required=True) -> Library | None:
    try:
        return Library(lib_dir(getattr(args, "lib", None)))
    except (FileNotFoundError, json.JSONDecodeError, ET.ParseError) as e:
        if required:
            print(f"アイコンライブラリを読めない: {e}\n→ `arch doctor` を実行して指示に従う", file=sys.stderr)
            raise SystemExit(2)
        return None


def load_any(path: Path, lib) -> Model:
    if path.suffix == ".drawio" or path.name.endswith(".drawio.xml"):
        return load_drawio(path, lib)
    m = load_yaml(path, lib)
    layout(m)
    return m


def find_zips() -> list[Path]:
    cands = list(Path.cwd().glob("icons/Icon-package*.zip")) + list(Path.cwd().glob("Icon-package*.zip"))
    cands += list((Path.home() / "Downloads").glob("Icon-package*.zip"))
    return sorted(cands, key=lambda p: p.stat().st_mtime, reverse=True)


def cmd_doctor(args) -> int:
    ok = True
    path = lib_dir(args.lib)
    print(f"[icons] ライブラリ: {path}")
    try:
        lib = Library(path)
        counts = {k: sum(i.kind == k for i in lib.icons) for k in LIB_FILES}
        print(f"  読み込みOK: {counts}")
        date = package_date(path)
        if date:
            from datetime import date as _d
            age = (_d.today() - _d.fromisoformat(date)).days
            print(f"  Asset Package の日付: {date} ({age} 日前)。報告に書く")
            if age > 120:
                print(f"  注意: Asset Package は四半期ごとに更新される。新しい版が出ていないか {ICON_DOWNLOAD_URL} を確認し、"
                      "あればユーザーに取り込みを提案する")
        probe = [lib.resolve(n) for n in ("Amazon EC2", "Amazon RDS", "Elastic Load Balancing Application Load Balancer")]
        missing_groups = [g.icon for g in GROUPS.values() if g.icon and not lib.group_icon(g.icon)]
        if not all(probe) or counts["service"] < 100 or missing_groups:
            print(f"  NG: 代表アイコン/グループアイコンが欠けている {missing_groups}")
            ok = False
        else:
            ET.fromstring(base64.b64decode(probe[0].data.split(",", 1)[1]))  # SVG として解釈できるか
            print("  代表アイコン (EC2 / RDS / ALB) と全グループアイコンを解決・SVGデコードできた")
    except (FileNotFoundError, json.JSONDecodeError, ET.ParseError, ValueError) as e:
        ok = False
        print(f"  NG: 読み込めない ({e})")
        zips = find_zips()
        if zips:
            print("  次の ZIP が見つかった。ユーザーに取り込みの了承を得てから実行する:")
            for z in zips[:3]:
                print(f"    uv run --project '{SKILL_DIR / 'tools'}' arch icons build '{z}'")
        else:
            print("  ACTION: ユーザーに AWS 公式アイコン (Asset Package の ZIP) の用意を依頼する。")
            print(f"    ダウンロード: {ICON_DOWNLOAD_URL}")
            print("    ZIP のパスを受け取ったら `arch icons build <zip>` で取り込む。")
    try:
        node = subprocess.run(["node", "--version"], capture_output=True, text=True) if _which("node") else None
    except OSError:
        node = None
    print(f"[node] {node.stdout.strip() if node and node.returncode == 0 else 'NG: 無い (PNG 書き出しに必要)'}")
    desktop = os.environ.get("DRAWIO_CMD")
    if desktop and not Path(desktop).is_file():
        desktop = None
    if not desktop:
        windows_candidates = (Path(os.environ.get("ProgramFiles", "")) / "draw.io" / "draw.io.exe",
                             Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "draw.io" / "draw.io.exe")
        desktop = next((str(p) for p in windows_candidates if p.is_file()), None)
    desktop = desktop or next((p for p in ("/Applications/draw.io.app/Contents/MacOS/draw.io",
                                           "/usr/bin/drawio", "/snap/bin/drawio") if Path(p).exists()), None)
    desktop = desktop or _which("draw.io") or _which("drawio")
    print(f"[draw.io Desktop] {desktop or '無い (PNG の代わりに SVG で確認する)'}")
    print("OK" if ok else "NG: アイコンが揃うまで作図に進まない")
    return 0 if ok else 2


def package_date(lib_path: Path) -> str | None:
    """取り込んだ Asset Package の日付 (aws-drawio-import が付ける AWS-*-YYYY-MM-DD.xml の日付)。"""
    dates = sorted(re.findall(r"(\d{4}-\d{2}-\d{2})\.xml$", f.name)[0]
                   for f in lib_path.parent.glob("AWS-*-????-??-??.xml"))
    return dates[-1] if dates else None


def _which(cmd):
    from shutil import which
    return which(cmd)


def cmd_icons(args) -> int:
    if args.action == "build":
        out = lib_dir(args.lib).parent
        script = VENDOR / "aws-drawio-import" / "scripts" / "build_aws_drawio_libraries.py"
        return subprocess.run([sys.executable, str(script), args.query, "--output-dir", str(out)]).returncode
    lib = open_lib(args)
    hits = lib.search(args.query)
    for ic in hits[:args.limit]:
        print(f"{ic.name}\t[{ic.kind}] {ic.category}")
    if not hits:
        print("見つからない。候補:", ", ".join(lib.suggest(args.query)) or "なし")
        return 1
    return 0


def cmd_build(args) -> int:
    lib = open_lib(args)
    src = Path(args.spec)
    m = load_yaml(src, lib)
    from arch_builder.rules import lint, report
    findings = lint(m, layout_first=True)
    out = Path(args.out or src.with_suffix("").with_suffix(".drawio"))
    if any(f.severity == "error" and f.code in ("E-ICON", "E-ID", "E-REF") for f in findings):
        print(report(findings))
        print("生成できない error がある。arch.yaml を直す", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".drawio", dir=out.parent, delete=False) as f:
        temp = Path(f.name)
        f.write(to_drawio(m))
    try:
        saved_findings = lint(load_drawio(temp, lib), layout_first=False)
        signature = lambda fs: sorted((x.severity, x.code, x.id or "") for x in fs)  # noqa: E731
        if signature(findings) != signature(saved_findings):
            print("build時と保存後のlint結果が一致しないため、出力を更新しない", file=sys.stderr)
            print(report(saved_findings))
            return 1
        if any(f.severity == "error" for f in saved_findings):
            print(report(saved_findings))
            print("保存後lintに error があるため、出力を更新しない", file=sys.stderr)
            return 1
        temp.replace(out)
    finally:
        temp.unlink(missing_ok=True)
    print(f"wrote {out}")
    print(report(saved_findings))
    return 0


def cmd_lint(args) -> int:
    lib = open_lib(args)
    m = load_any(Path(args.target), lib)
    from arch_builder.rules import lint, report
    findings = lint(m, layout_first=False)
    if args.json:
        print(json.dumps([f.__dict__ for f in findings], ensure_ascii=False, indent=2))
    else:
        print(report(findings))
    return 1 if any(f.severity == "error" for f in findings) else 0


def cmd_import(args) -> int:
    lib = open_lib(args)
    m = load_drawio(Path(args.drawio), lib)
    bad = [it for it in m.items.values() if it.unmanaged]
    for it in bad:
        print(f"warn: {it.id}: {it.unmanaged} — arch.yaml では icon: null のまま残す", file=sys.stderr)
    # 取り込み時は相対座標を pos/size として残す (draw.io 上の手直しを保つ)
    for it in m.items.values():
        it.pos = list(it._geo[:2])
        if it.kind == "group":
            it.size = list(it._geo[2:])
    out = Path(args.out)
    out.write_text(dump_yaml(m), encoding="utf-8")
    print(f"wrote {out} ({len(m.items)} items, {len(m.edges)} edges)")
    return 0


def cmd_edit(args) -> int:
    lib = open_lib(args)
    path = Path(args.spec)
    m = load_yaml(path, lib)
    op = args.op

    def need(iid):
        if iid not in m.items:
            raise SystemExit(f"id '{iid}' が無い")
        return m.items[iid]

    def check_icon(name):
        if not lib.resolve(name):
            raise SystemExit(f"アイコン '{name}' が無い。候補: {', '.join(lib.suggest(name))}")

    def detach(iid):
        it = m.items[iid]
        (m.items[it.parent].children if it.parent else m.roots).remove(iid)

    def attach(iid, parent, after=None):
        sib = m.items[parent].children if parent else m.roots
        sib.insert(sib.index(after) + 1 if after in sib else len(sib), iid)
        m.items[iid].parent = parent

    if op in ("add-node", "add-group"):
        if args.id in m.items:
            raise SystemExit(f"id '{args.id}' は既にある")
        if args.parent:
            need(args.parent)
        if op == "add-node":
            check_icon(args.icon)
            it = Item(args.id, "node", None, args.label or lib.resolve(args.icon).name, icon=args.icon)
        else:
            if args.type not in GROUPS:
                raise SystemExit(f"グループ種類は {', '.join(GROUPS)} のいずれか")
            it = Item(args.id, "group", None, args.label or "", group=args.type, layout=args.layout or "row")
        m.items[it.id] = it
        attach(it.id, args.parent, args.after)
    elif op == "remove":
        need(args.id)
        gone = {i.id for i in m.walk([args.id])}
        detach(args.id)
        for i in gone:
            del m.items[i]
        m.edges = [e for e in m.edges if e.src not in gone and e.dst not in gone]
    elif op == "move":
        it = need(args.id)
        if args.parent:
            need(args.parent)
            if args.parent in {i.id for i in m.walk([args.id])}:
                raise SystemExit("自分の子孫の中へは移動できない")
        detach(args.id)
        attach(args.id, args.parent, args.after)
        it.pos = None
    elif op == "set":
        it = need(args.id)
        for kv in args.pairs:
            k, _, v = kv.partition("=")
            if k == "icon":
                check_icon(v)
            if k == "group" and v not in GROUPS:
                raise SystemExit(f"グループ種類は {', '.join(GROUPS)} のいずれか")
            if k not in ("label", "icon", "group", "layout", "cols"):
                raise SystemExit(f"set できるのは label/icon/group/layout/cols: {k}")
            setattr(it, k, int(v) if k == "cols" else v.replace("\\n", "\n"))
    elif op == "connect":
        need(args.src), need(args.dst)
        m.edges.append(Edge(args.src, args.dst, args.label or "", args.dashed, args.arrow, args.exit, args.entry))
    elif op == "set-edge":
        hits = [e for e in m.edges if e.src == args.src and e.dst == args.dst]
        if not hits:
            raise SystemExit(f"{args.src} -> {args.dst} の接続線は無い")
        for kv in args.pairs:
            k, _, v = kv.partition("=")
            if k not in ("label", "dashed", "arrow", "exit", "entry"):
                raise SystemExit(f"set-edge できるのは label/dashed/arrow/exit/entry: {k}")
            if k in ("exit", "entry") and v not in SIDES + ("",):
                raise SystemExit(f"{k} は {'/'.join(SIDES)} のいずれか (空で探索に戻す)")
            if k == "dashed":
                val = v.lower() == "true"
            elif k in ("exit", "entry"):
                val = v or None
            else:
                val = v.replace("\\n", "\n")
            for e in hits:
                setattr(e, k, val)
    elif op == "disconnect":
        before = len(m.edges)
        m.edges = [e for e in m.edges if not (e.src == args.src and e.dst == args.dst)]
        if len(m.edges) == before:
            raise SystemExit(f"{args.src} -> {args.dst} の接続線は無い")
    elif op == "relayout":
        for it in (m.walk([args.id]) if args.id else m.items.values()):
            it.pos = it.size = None
    path.write_text(dump_yaml(m), encoding="utf-8")
    print(f"updated {path}")
    return 0


def cmd_render(args) -> int:
    script = Path(__file__).with_name("render.mjs")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    cmd = ["node", str(script), args.drawio] + ([args.out] if args.out else [])
    return subprocess.run(cmd).returncode


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="arch", description="AWS 構成図 (arch.yaml <-> .drawio)")
    p.add_argument("--lib", help="アイコンライブラリのディレクトリ (既定: <skill>/icons/current または $ARCH_ICON_LIB)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="アイコン・node・draw.io Desktop が使えるか確認する")
    s = sub.add_parser("icons", help="アイコンを探す / ZIP から取り込む")
    s.add_argument("action", choices=["search", "build"])
    s.add_argument("query", help="search: 検索語 / build: Icon-package ZIP のパス")
    s.add_argument("--limit", type=int, default=30)
    s = sub.add_parser("build", help="arch.yaml -> .drawio (生成後に lint も走る)")
    s.add_argument("spec")
    s.add_argument("-o", "--out")
    s = sub.add_parser("lint", help="arch.yaml / .drawio を AWS 規約で検証する")
    s.add_argument("target")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("import", help=".drawio -> arch.yaml (draw.io での手直しを正本に戻す)")
    s.add_argument("drawio")
    s.add_argument("-o", "--out", required=True)
    s = sub.add_parser("render", help=".drawio -> PNG (draw.io Desktop。無ければ SVG)")
    s.add_argument("drawio")
    s.add_argument("-o", "--out")

    s = sub.add_parser("edit", help="arch.yaml をコマンドで編集する")
    s.add_argument("spec")
    ops = s.add_subparsers(dest="op", required=True)
    o = ops.add_parser("add-node")
    o.add_argument("id"); o.add_argument("--icon", required=True); o.add_argument("--label")
    o.add_argument("--parent"); o.add_argument("--after")
    o = ops.add_parser("add-group")
    o.add_argument("id"); o.add_argument("--type", required=True); o.add_argument("--label")
    o.add_argument("--parent"); o.add_argument("--after"); o.add_argument("--layout", choices=["row", "column", "grid"])
    o = ops.add_parser("remove"); o.add_argument("id")
    o = ops.add_parser("move"); o.add_argument("id"); o.add_argument("--parent"); o.add_argument("--after")
    o = ops.add_parser("set"); o.add_argument("id"); o.add_argument("pairs", nargs="+", metavar="key=value")
    o = ops.add_parser("connect")
    o.add_argument("src"); o.add_argument("dst"); o.add_argument("--label")
    o.add_argument("--dashed", action="store_true"); o.add_argument("--arrow", choices=["end", "both", "none"], default="end")
    o.add_argument("--exit", choices=SIDES, help="出口の辺を固定する (既定は経路探索)")
    o.add_argument("--entry", choices=SIDES, help="入口の辺を固定する")
    o = ops.add_parser("set-edge", help="接続線の label/dashed/arrow/exit/entry を変える")
    o.add_argument("src"); o.add_argument("dst"); o.add_argument("pairs", nargs="+", metavar="key=value")
    o = ops.add_parser("disconnect"); o.add_argument("src"); o.add_argument("dst")
    o = ops.add_parser("relayout", help="pos/size を消して自動配置に戻す"); o.add_argument("id", nargs="?")

    args = p.parse_args(argv)
    return {"doctor": cmd_doctor, "icons": cmd_icons, "build": cmd_build, "lint": cmd_lint,
            "import": cmd_import, "edit": cmd_edit, "render": cmd_render}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())

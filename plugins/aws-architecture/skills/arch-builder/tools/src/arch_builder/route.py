"""接続線の経路探索。

アイコンのラベルは下に付くので、下の接続口はアイコンではなく「ラベルの下端」に置く。
出口 4 辺 × 入口 4 辺 × 曲がり方の候補を総当たりし、次を減点して最も安い経路を選ぶ。

- 他のアイコン・ラベルを横切る (最重)。自分自身のアイコン・ラベルも同じ扱い
- グループの見出し (アイコン + 名前) を横切る
- 先に引いた線との交差・重なり
- 線のラベルがアイコン・ラベルに重なる
- 曲がりの数と長さ (同点のときに短く素直な経路を選ぶため)

選んだ経路は waypoint として .drawio に書く。draw.io に見える線と lint が検査する線を一致させるため。
"""

from __future__ import annotations

from dataclasses import dataclass

SIDES = ("right", "left", "bottom", "top")
NORMAL = {"right": (1, 0), "left": (-1, 0), "bottom": (0, 1), "top": (0, -1)}
STUB = 18           # 接続口からまっすぐ出す長さ
MARGIN = 6          # 障害物のまわりに取る余白
W_NODE, W_HEADER, W_LABEL, W_CROSS, W_OVERLAP, W_BEND = 1000, 300, 400, 60, 250, 12
W_ON_FRAME = 200    # グループの枠線に重ねて走る (枠線と見分けがつかない)
NEAR = 22           # 無関係なアイコンにこれより近いところを通ると、その要素につながっているように見える
W_NEAR = 60
W_BACKWARD = 150   # 最後の区間が、始点から終点へ向かう向きと逆 (矢印が戻ってくる)


@dataclass
class Box:
    """経路探索から見た要素。icon は接続口の基準、label はアイコン下のラベル領域。"""
    id: str
    icon: tuple            # (x, y, w, h)
    label: tuple | None    # (x, y, w, h) 無ければ None
    obstacle: bool = True  # グループを線の端点にするときは False (枠は線が通ってよい)


def port(b: Box, side: str, frac: float = 0.5) -> tuple[float, float]:
    """接続口の座標。frac は面の中の位置 (上下の面なら左から、左右の面なら上から 0〜1)。"""
    x, y, w, h = b.icon
    if side == "right":
        return x + w, y + h * frac
    if side == "left":
        return x, y + h * frac
    if side == "top":
        return x + w * frac, y
    bottom = b.label[1] + b.label[3] + 2 if b.label else y + h  # ラベルの下から出す
    return x + w * frac, bottom


def port_style(b: Box, side: str, prefix: str, frac: float = 0.5) -> str:
    """draw.io の接続口指定 (exitX/exitY/exitDy など)。ラベル下の口は Dy で下へずらす。"""
    _, y, _, h = b.icon
    fx, fy = {"right": (1, frac), "left": (0, frac), "top": (frac, 0), "bottom": (frac, 1)}[side]
    dy = port(b, side, frac)[1] - (y + h) if side == "bottom" else 0
    return (f"{prefix}X={round(fx, 4)};{prefix}Y={round(fy, 4)};{prefix}Dx=0;{prefix}Dy={round(dy, 1)};"
            f"{prefix}Perimeter=0;")


def _inflate(r, d):
    return (r[0] - d, r[1] - d, r[2] + 2 * d, r[3] + 2 * d)


def _horizontal(a, b) -> bool:
    return abs(a[1] - b[1]) < 0.5  # .drawio は 0.1 単位に丸めるので、ぴったり一致は期待しない


def seg_hits(a, b, r) -> bool:
    """軸平行な線分 a-b が矩形 r の内部を通るか (辺に触れるだけは通らない扱い)。"""
    x, y, w, h = r
    if _horizontal(a, b):
        lo, hi = sorted((a[0], b[0]))
        return y < a[1] < y + h and lo < x + w and hi > x
    lo, hi = sorted((a[1], b[1]))
    return x < a[0] < x + w and lo < y + h and hi > y


def path_hits(path, r) -> bool:
    return any(seg_hits(path[i], path[i + 1], r) for i in range(len(path) - 1))


def obstacles_of(boxes) -> list[tuple[str, tuple]]:
    obs = []
    for b in boxes:
        obs.append((b.id, _inflate(b.icon, MARGIN)))
        if b.label:
            obs.append((b.id, _inflate(b.label, 2)))
    return obs


def blocked(path, src, dst, obstacles) -> list[str]:
    """経路が横切る要素の id。出入り口の直後の区間では、自分自身との当たりを見ない。"""
    segs = [(path[i], path[i + 1]) for i in range(len(path) - 1)]
    # 自分自身とは、出入り口から STUB の長さだけ当たりを免除する (それより先で自分を貫いたら横切りとして数える)
    own_segs = list(segs)
    if own_segs:
        a, b = own_segs[0]
        own_segs[0] = (_toward(a, b, STUB), b)
        a, b = own_segs[-1]
        own_segs[-1] = (a, _toward(b, a, STUB))
    out = []
    for oid, r in obstacles:
        check = own_segs if oid in (src, dst) else segs
        if any(s and seg_hits(s[0], s[1], r) for s in check if s[0] and s[1]) and oid not in out:
            out.append(oid)
    return out


def near_obstacles(boxes) -> list[tuple[str, tuple]]:
    return [(b.id, _inflate(b.icon, NEAR)) for b in boxes]


def passes_near(path, src, dst, near_obs) -> list[str]:
    """端点ではない要素のすぐ脇 (NEAR 以内) を通る要素の id。線がその要素に出入りしているように誤読される。"""
    return [oid for oid, r in near_obs if oid not in (src, dst) and path_hits(path, r)]


def _toward(a, b, d):
    """a から b へ d だけ進んだ点。届かなければ None (その区間は丸ごと免除)。"""
    ln = abs(a[0] - b[0]) + abs(a[1] - b[1])
    if ln <= d:
        return None
    t = d / ln
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _cross(s, t) -> tuple[int, int]:
    """線分どうしの (交差数, 重なり数)。"""
    (a, b), (c, d) = s, t
    sh, th = _horizontal(a, b), _horizontal(c, d)
    if sh == th:  # 平行: 同じ線上で重なっていれば重なり
        if sh and abs(a[1] - c[1]) < 0.5 and min(a[0], b[0]) < max(c[0], d[0]) and min(c[0], d[0]) < max(a[0], b[0]):
            return 0, 1
        if not sh and abs(a[0] - c[0]) < 0.5 and min(a[1], b[1]) < max(c[1], d[1]) and min(c[1], d[1]) < max(a[1], b[1]):
            return 0, 1
        return 0, 0
    h, v = (s, t) if sh else (t, s)
    hx = sorted((h[0][0], h[1][0]))
    vy = sorted((v[0][1], v[1][1]))
    return (1 if hx[0] < v[0][0] < hx[1] and vy[0] < h[0][1] < vy[1] else 0), 0


def _near_collinear(s, f, d=8) -> bool:
    """線分 s が枠線 f に沿って (d px 以内で) 重なって走るか。"""
    (a, b), (c, e) = s, f
    if _horizontal(a, b) and _horizontal(c, e) and abs(a[1] - c[1]) < d:
        return min(a[0], b[0]) < max(c[0], e[0]) - d and min(c[0], e[0]) + d < max(a[0], b[0])
    if not _horizontal(a, b) and not _horizontal(c, e) and abs(a[0] - c[0]) < d:
        return min(a[1], b[1]) < max(c[1], e[1]) - d and min(c[1], e[1]) + d < max(a[1], b[1])
    return False


def simplify(path):
    out = [path[0]]
    for p in path[1:]:
        if p == out[-1]:
            continue
        if len(out) >= 2 and (out[-2][0] == out[-1][0] == p[0] or out[-2][1] == out[-1][1] == p[1]):
            out[-1] = p
        else:
            out.append(p)
    return out


def midpoint(path) -> tuple[float, float]:
    segs = [(path[i], path[i + 1]) for i in range(len(path) - 1)]
    total = sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in segs)
    run = total / 2
    for a, b in segs:
        ln = abs(a[0] - b[0]) + abs(a[1] - b[1])
        if run <= ln and ln:
            t = run / ln
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        run -= ln
    return path[0]


def _candidates(p0, s_side, q0, t_side, xs, ys, cxs=(), cys=()):
    nx, ny = NORMAL[s_side]
    mx, my = NORMAL[t_side]
    p1 = (p0[0] + nx * STUB, p0[1] + ny * STUB)
    q1 = (q0[0] + mx * STUB, q0[1] + my * STUB)
    yield [p0, p1, (q1[0], p1[1]), q1, q0]
    yield [p0, p1, (p1[0], q1[1]), q1, q0]
    lo, hi = sorted((p1[0], q1[0]))
    for xm in {(p1[0] + q1[0]) / 2, *[x for x in xs if lo - 200 <= x <= hi + 200]}:
        yield [p0, p1, (xm, p1[1]), (xm, q1[1]), q1, q0]
    lo, hi = sorted((p1[1], q1[1]))
    for ym in {(p1[1] + q1[1]) / 2, *[y for y in ys if lo - 200 <= y <= hi + 200]}:
        yield [p0, p1, (p1[0], ym), (q1[0], ym), q1, q0]
    # 曲がり 3 回: グループ枠の隙間 (横の通り道 → 縦の通り道) を伝って回り込む。候補が多いので枠の隙間だけに絞る
    for ym in cys:
        for xm in cxs:
            yield [p0, p1, (p1[0], ym), (xm, ym), (xm, q1[1]), q1, q0]
            yield [p0, p1, (xm, p1[1]), (xm, ym), (q1[0], ym), q1, q0]


def route_all(boxes: dict[str, Box], edges, headers, label_size, channels=(), groups=None):
    """接続口 (面と面の中の位置) は呼び出し側が決めて渡す。ここでは、その口どうしをどの形の線でつなぐかだけを選ぶ。

    edges: [(src, dst, label, exit_side, entry_side, exit_frac, entry_frac, cls)] -> [(path, exit_side, entry_side)]
    cls: 線の内容 (ラベルと線種)。同じ内容で同じ端点を持つ線だけが、幹を重ねてよい
    groups: まとめて対称に引く線の添字のリスト (単独の線は長さ 1)"""
    obstacles = obstacles_of(b for b in boxes.values() if b.obstacle)
    near_obs = near_obstacles(b for b in boxes.values() if b.obstacle)
    # 迂回路の候補にする縦横の通り道 (障害物の外側)
    rects = [r for _, r in obstacles]
    xs = sorted({r[0] - 14 for r in rects} | {r[0] + r[2] + 14 for r in rects}
                | {c[0] - 16 for c in channels} | {c[0] + c[2] + 16 for c in channels})
    ys = sorted({r[1] - 14 for r in rects} | {r[1] + r[3] + 14 for r in rects}
                | {c[1] - 16 for c in channels} | {c[1] + c[3] + 16 for c in channels})
    # 枠と枠の隙間の中央も通り道の候補にする (隙間のど真ん中を通る線がいちばん枠線と紛れない)
    gx = {(a[0] + a[2] + b[0]) / 2 for a in channels for b in channels if 0 < b[0] - (a[0] + a[2]) < 120}
    gy = {(a[1] + a[3] + b[1]) / 2 for a in channels for b in channels if 0 < b[1] - (a[1] + a[3]) < 120}
    xs, ys = sorted(set(xs) | gx), sorted(set(ys) | gy)
    cxs = sorted({c[0] - 16 for c in channels} | {c[0] + c[2] + 16 for c in channels} | gx)
    cys = sorted({c[1] - 16 for c in channels} | {c[1] + c[3] + 16 for c in channels} | gy)
    frames = []  # グループの枠線。横切るのはよいが、重ねて走らせない
    for x, y, w, h in channels:
        frames += [((x, y), (x + w, y)), ((x, y + h), (x + w, y + h)), ((x, y), (x, y + h)), ((x + w, y), (x + w, y + h))]

    def best_path(edge, segs, deep=False, bias=None):
        src, dst, label, es, ts, xf, ef, cls = edge
        p0, q0 = port(boxes[src], es, xf), port(boxes[dst], ts, ef)
        best = None
        for cand in _candidates(p0, es, q0, ts, xs, ys, cxs if deep else (), cys if deep else ()):
            path = simplify(cand)
            cost = score(path, edge, obstacles, headers, segs, label_size, frames, done_labels)
            cost += W_NEAR * len(passes_near(path, src, dst, near_obs))
            if bias:
                cost += bias(path)
            if best is None or cost < best[0]:
                best = (cost, path, es, ts)
        # 曲がり 2 回までで減点が残るときだけ、曲がり 3 回の迂回も試す (候補が多く遅いので)
        if not deep and best[0] >= W_NEAR:
            best = min(best, best_path(edge, segs, True, bias), key=lambda b: b[0])
        return best

    done_segs, done_labels = [], []   # 引いた線の区間 / ラベル (矩形, 線の経路, src, dst, 内容)
    out: dict[int, tuple] = {}
    for grp in groups or [[i] for i in range(len(edges))]:
        members = [edges[i] for i in grp]
        chosen = [best_path(members[0], done_segs)] if len(grp) == 1 else \
            _route_group(members, boxes, best_path, done_segs)
        for i, (_, path, es, ts) in zip(grp, chosen):
            done_segs += [(path[k], path[k + 1], edges[i][0], edges[i][1], edges[i][7]) for k in range(len(path) - 1)]
            lr = label_rect(path, edges[i][2], label_size)
            if lr:
                done_labels.append((lr, path, edges[i][0], edges[i][1], edges[i][7]))
            out[i] = (path, es, ts)
    return [out[i] for i in range(len(edges))]


W_ASYM = 150  # 対になる線 (AZ ごとに同じ役割の相手へ向かう線) の曲がり方が揃っていない


def _directions(path, flip=False):
    out = []
    for a, b in zip(path, path[1:]):
        if _horizontal(a, b):
            d = "R" if b[0] > a[0] else "L"
        else:
            d = "D" if b[1] > a[1] else "U"
        out.append({"R": "L", "L": "R"}.get(d, d) if flip else d)
    return out


def _route_group(members, boxes, best_path, done_segs):
    """対になる線をまとめて引く。曲がる向き (相手が反対側なら左右反転) と曲がる位置がそろう組のうち、合計が最も安いものを選ぶ。"""
    def cx(n):
        b = boxes[n].icon
        return b[0] + b[2] / 2
    ref = cx(members[0][1]) - cx(members[0][0])
    flips = [(cx(d) - cx(s)) * ref < 0 for s, d, *_ in members]
    # 相手が横に並んでいるなら「横に走る区間の高さ」、縦に並んでいるなら「縦に走る区間の位置」をそろえる
    spread_x = len({round(cx(d)) for _, d, *_ in members}) > 1

    def levels(path):
        segs = zip(path, path[1:])
        return [round(a[1]) for a, b in segs if _horizontal(a, b)] if spread_x else \
               [round(a[0]) for a, b in segs if not _horizontal(a, b)]
    best = None
    # 先に引いた線が基準になるので、どの線を基準にするかも入れ替えて試す (基準の線だけが得をしないように)
    for start in range(len(members)):
        order = list(range(start, len(members))) + list(range(start))
        segs, total, res, ref_dirs, ref_lv = list(done_segs), 0.0, {}, None, None
        for i in order:
            m, f = members[i], flips[i]
            # 2 本目以降は、基準の線と曲がり方・曲がる位置がそろう経路を、候補を選ぶ段階で優先する
            bias = None if ref_dirs is None else (
                lambda p, f=f, rd=ref_dirs, rl=ref_lv: W_ASYM * (_directions(p, f) != rd) + W_ASYM * (levels(p) != rl))
            r = best_path(m, segs, bias=bias)
            if ref_dirs is None:
                ref_dirs, ref_lv = _directions(r[1], f), levels(r[1])
            total += r[0]
            res[i] = r
            segs += [(r[1][k], r[1][k + 1], m[0], m[1], m[7]) for k in range(len(r[1]) - 1)]
        if best is None or total < best[0]:
            best = (total, [res[i] for i in range(len(members))])
    return best[1]


def label_rect(path, label, label_size):
    """線のラベルの矩形 (draw.io は経路の中点に置く)。ラベルが無ければ None。"""
    if not label:
        return None
    lw, lh = label_size(label)
    mx, my = midpoint(path)
    # 4px の余白を含める。文字のすぐ脇をかすめる線も、描画では文字にかかって見える
    return (mx - lw / 2 - 4, my - lh / 2 - 4, lw + 8, lh + 8)


def rects_overlap(a, b) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def crosses_label(lr, other_path, own_path) -> bool:
    """別の線が、ラベル lr の上を横切るか。自分の線と重なっている区間 (共有した幹) は数えない。"""
    own = list(zip(own_path, own_path[1:]))
    return any(seg_hits(a, b, lr) and not any(_near_collinear((a, b), o, 1) for o in own)
               for a, b in zip(other_path, other_path[1:]))


def label_hits(path, label, label_size, obstacles) -> list[str]:
    """線のラベルと重なる要素の id。"""
    lr = label_rect(path, label, label_size)
    return [oid for oid, r in obstacles if lr and rects_overlap(lr, r)]


def shares_trunk(e_src, e_dst, e_cls, t_src, t_dst, t_cls) -> bool:
    """幹を重ねてよい 2 本か: 同じ内容 (ラベルと線種) で、同じ出口から分かれるか同じ入口へ集まる線。"""
    return e_cls == t_cls and (e_src == t_src or e_dst == t_dst)


def score(path, edge, obstacles, headers, done_segs, label_size, frames=(), done_labels=()) -> float:
    src, dst, label, *_, cls = edge
    # ラベルの上を別の線が通る / ラベルどうしが重なる / ラベルが見出しに乗ると、文字が読めない
    own = label_rect(path, label, label_size)
    cost_l = 0
    for r, t_path, t_src, t_dst, t_cls in done_labels:
        # Shared wire segments are allowed for a fan-out, but each mxCell still
        # draws its own label. Overlapping labels are never a valid shared trunk.
        if crosses_label(r, path, t_path) or (own and rects_overlap(own, r)):
            cost_l += W_LABEL
    if own:
        cost_l += W_LABEL * any(seg_hits(a, b, own) and not any(_near_collinear((a, b), o, 1) for o in zip(path, path[1:]))
                                for a, b, *_ in done_segs)
        cost_l += W_LABEL * sum(1 for h in headers if rects_overlap(own, h))
    cost = cost_l + W_NODE * len(blocked(path, src, dst, obstacles))
    for r in headers:
        if path_hits(path, r):
            cost += W_HEADER
    segs = [(path[i], path[i + 1]) for i in range(len(path) - 1)]
    for s in segs:
        for a, b, t_src, t_dst, t_cls in done_segs:
            c, _ = _cross(s, (a, b))
            cost += c * W_CROSS
            # 数 px 離れて並走するのも重なって見える。幹を共有してよいのは、同じ内容で端点を共有する線だけ
            if not shares_trunk(src, dst, cls, t_src, t_dst, t_cls):
                cost += _near_collinear(s, (a, b)) * W_OVERLAP
        for f in frames:
            if _near_collinear(s, f):
                cost += W_ON_FRAME
            elif _near_collinear(s, f, 14):  # 枠のすぐ内側・外側を並走するのも枠線に沿って見える (枠の隙間 32px の中央は 16px 離れるので対象外)
                cost += W_ON_FRAME / 8
    # 後戻り (出口から出てすぐ逆向きに折り返す) は読みにくい
    for i in range(1, len(segs)):
        (a, b), (c, d) = segs[i - 1], segs[i]
        if (b[0] - a[0]) * (d[0] - c[0]) < 0 or (b[1] - a[1]) * (d[1] - c[1]) < 0:
            cost += 40
    cost += W_LABEL * len(label_hits(path, label, label_size, obstacles))
    length = sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in segs)
    # 矢印が流れと逆向きに入る (下にある相手へ下から上向きに入る、など) と、データの向きを読み違える
    (a0, a1), (b0, b1) = path[-2], path[-1]
    dx, dy = path[-1][0] - path[0][0], path[-1][1] - path[0][1]
    if (b1 - a1) * dy < 0 or (b0 - a0) * dx < 0:
        cost += W_BACKWARD
    return cost + (len(path) - 2) * W_BEND + length * 0.06

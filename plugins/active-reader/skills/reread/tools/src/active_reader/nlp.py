"""日本語処理（janome / IPADIC）: 本文テキスト化、文節境界、専門用語の検出。"""
import re
from bisect import bisect_right
from collections import Counter, defaultdict
from functools import lru_cache
from html.parser import HTMLParser

from janome.tokenizer import Tokenizer

_tokenizer = None
CODE_TAGS = {'pre', 'code'}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.code, self.pos, self.depth = [], [], 0, 0

    def handle_starttag(self, tag, attrs):
        self.depth += tag in CODE_TAGS

    def handle_endtag(self, tag):
        self.depth -= tag in CODE_TAGS and self.depth > 0

    def handle_data(self, data):
        if self.depth:
            self.code.append((self.pos, self.pos + len(data)))
        self.parts.append(data)
        self.pos += len(data)


def parse(markup):
    """(ブラウザの #reader.textContent と同じ文字列, コード部分の範囲)。offset を UI と共有する。"""
    p = _Text()
    p.feed(markup)
    p.close()
    return ''.join(p.parts), tuple(p.code)


def plain_text(markup):
    return parse(markup)[0]


@lru_cache(maxsize=16)
def _tokens(text):
    """(開始位置, 表層形, 品詞タプル) の列。"""
    # ponytail: 純 Python の janome なので長い本は初回数秒かかる。遅ければ fugashi に替える
    global _tokenizer
    _tokenizer = _tokenizer or Tokenizer()
    out, pos = [], 0
    for tok in _tokenizer.tokenize(text):
        i = text.find(tok.surface, pos)
        if i >= 0:
            out.append((i, tok.surface, tuple(tok.part_of_speech.split(','))))
            pos = i + len(tok.surface)
    return tuple(out)


# --- 文節境界: ドラッグ選択を吸着させる位置 ---------------------------------------

def _starts(pos, prev):
    """この形態素から新しい文節が始まるか（IPADIC の品詞で判定する簡易ルール）。"""
    if pos[0] == '記号' or prev is None or prev[0] == '記号':
        return True
    if pos[0] in ('助詞', '助動詞') or pos[1] in ('非自立', '接尾'):
        return False  # 前にくっつく: 意味「が」/ 近けれ「ば」/ 速「さ」/ 文書「長」
    if prev[0] == '接頭詞':
        return False
    if pos[0] == '名詞' and prev[0] == '名詞':
        return False  # 複合名詞: 正規 + 化 / ベクトル + 検索
    if pos[0] == '動詞' and prev[:2] == ('名詞', 'サ変接続'):
        return False  # 一致 + し / 飽和 + させ
    return True


@lru_cache(maxsize=16)
def boundaries(text):
    """文節の開始位置と末尾の offset（昇順）。"""
    out, end, prev = [], 0, None
    for i, surface, p in _tokens(text):
        if p[1] == '空白':  # 「TF の」の空白では切らない。改行は段落の切れ目なので必ず切る
            end, prev = i + len(surface), (None if '\n' in surface else prev)
            continue
        if i > end or _starts(p, prev):  # 読み飛ばした文字の後も区切る
            out.append(i)
        end, prev = i + len(surface), p
    return out + [len(text)]


# --- 専門用語の検出 ----------------------------------------------------------------
#
# 1. 候補: 名詞の連続（複合名詞）。英字は空白をはさんでもつなぐ（Reciprocal Rank Fusion）。コード部分は除く
# 2. 点数: FLR = 出現頻度 × LR（中川・森「出現頻度と連接頻度に基づく専門用語抽出」の 1 文書版）
#    LR = (Π (左に連接する名詞の種類数+1)(右に連接する名詞の種類数+1)) ^ (1/2L)
#    いろいろな複合語に入る名詞を含む語ほど専門用語らしい、という考え方
# 3. 字種で補正: 英字の固有名・略語（AWS, CloudFront, BM25）とギリシャ文字（Σ）は必ず採る、カタカナは加点
# 4. 一般語（こと・場合・結果…）、2 文字の一般的な漢語（検索・精度…）、数量表現は除く

GREEK = re.compile(r'[Ͱ-Ͽ]')
LATIN = re.compile(r'[A-Za-z]')
KATAKANA = re.compile(r'^[゠-ヿー・]+$')
HIRAGANA = re.compile(r'^[぀-ゟ]+$')
WORD = re.compile(r'\w')
STOP = set('''こと もの ため 場合 方法 結果 文書 本文 以下 以上 前後 通常 程度 一般 全体 必要 上位 下位
対象 一方 意味 次 例 図 章 節 値 点 際 時 等 方 中 上 下 他 今回 最初 最後 部分 内容 説明 理由 目的 状態'''.split())
BOOST = {'symbol': 3.0, 'latin': 2.0, 'katakana': 1.5, 'mixed': 1.2, 'kanji': 1.0}
TAIL = {'等', '側', '時', '後', '前', '中', '内', '上', '用', 'そのもの'}  # 末尾から落とす一般的な語
HEAD = {'各', '全', '本', '同', '当', '両', '毎', '諸'}  # 先頭から落とす接頭詞


def _unit(surface, p):
    if GREEK.search(surface):
        return True
    return bool(WORD.search(surface)) and (
        (p[0] == '名詞' and p[1] not in ('非自立', '代名詞', '副詞可能')) or p[0] == '接頭詞')


def _trim(units):
    """先頭の数・接尾・単独の接頭詞と、末尾の数量表現（数十件）を落とす。英字に続く数字（BM25, k1）は残す。"""
    while units and (units[-1][1] in TAIL or units[-1][2][2] == '助数詞' or (
            units[-1][2][1] == '数' and not (len(units) > 1 and LATIN.search(units[-2][1])
                                            and units[-2][0] + len(units[-2][1]) == units[-1][0]))):
        units.pop()
    while units and (units[0][2][1] in ('数', '接尾') or units[0][1] in HEAD
                     or units[0][2][0] == '接頭詞' and len(units) == 1):
        units.pop(0)
    return units


def _runs(text, code):
    starts = [a for a, _ in code]

    def in_code(i):
        k = bisect_right(starts, i) - 1
        return k >= 0 and i < code[k][1]

    runs, cur = [], []

    def flush():
        units = _trim(cur[:])
        if units:
            runs.append((units[0][0], units[-1][0] + len(units[-1][1]), tuple(u[1] for u in units)))
        cur.clear()

    for tok in _tokens(text):
        i, surface, p = tok
        if surface in (' ', '-') and cur and LATIN.search(cur[-1][1]):
            continue  # 英字の語の間の空白・ハイフンはつなぐ候補として保留（Re-ranking）
        if in_code(i) or not _unit(surface, p):
            flush()
            continue
        if cur:
            end = cur[-1][0] + len(cur[-1][1])
            joined = i == end or (text[end:i] in (' ', '-') and LATIN.search(surface) and LATIN.search(cur[-1][1]))
            if not joined:
                flush()
        cur.append(tok)
    flush()
    return runs


def _kind(term):
    if GREEK.search(term):
        return 'symbol'
    if LATIN.search(term):
        return 'latin' if term.isascii() else 'mixed'  # AI処理 / JSON保存 は mixed
    return 'katakana' if KATAKANA.match(term) else 'kanji'


def _latin_ok(term, start, text):
    """英文の普通の単語は拾わない。略語・途中の大文字・数字入り・文中の大文字始まりだけ。"""
    if re.search(r'[A-Z].*[A-Z]', term) or (re.search(r'\d', term) and len(term) >= 3):
        return True  # 略語・途中の大文字（CloudFront）・数字入り（BM25。Q1 や v0 は除く）
    before = text[:start].rstrip()
    return term[0].isupper() and bool(before) and before[-1] not in '.!?。\n'


@lru_cache(maxsize=16)
def terms(text, code=()):
    """専門用語の候補を、最初に出てくる位置順に返す: [{term, start, end, kind, score}]"""
    runs = _runs(text, code)
    freq = Counter(text[s:e] for s, e, _ in runs)
    left, right = defaultdict(set), defaultdict(set)
    for _, _, units in runs:
        for a, b in zip(units, units[1:]):
            right[a].add(b)
            left[b].add(a)
    first = {}
    for s, e, units in runs:
        first.setdefault(text[s:e], (s, e, units))

    picked, ranked = [], []  # picked: 英字の固有名・記号（優先）/ ranked: それ以外
    for term, (s, e, units) in first.items():
        kind = _kind(term)
        if term in STOP or HIRAGANA.match(term) or term.isdigit() or term.endswith('的'):
            continue
        if kind == 'latin' and not (len(term) >= 2 and _latin_ok(term, s, text)):
            continue
        if kind in ('kanji', 'mixed') and len(term) < 3 and len(units) == 1:
            continue  # 2 文字の一般的な漢語（検索・精度…）
        lr = 1.0
        for u in units:
            lr *= (len(left[u]) + 1) * (len(right[u]) + 1)
        score = freq[term] * lr ** (1 / (2 * len(units))) * BOOST[kind]
        row = {'term': term, 'start': s, 'end': e, 'kind': kind, 'score': round(score, 2)}
        (picked if kind in ('symbol', 'latin') else ranked).append(row)
    # ponytail: 件数は文書の長さで決め打ち（短い文書 12 件〜長い文書 60 件）。多すぎ / 少なすぎなら調整
    limit = min(60, 12 + len(text) // 800)
    picked.sort(key=lambda r: -r['score'])
    ranked.sort(key=lambda r: -r['score'])
    return sorted(picked[:limit] + ranked[:limit], key=lambda r: r['start'])

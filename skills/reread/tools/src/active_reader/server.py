"""Reader UI 用のローカル HTTP サーバー（127.0.0.1 限定）。AI は扱わない（スキル側の役割）。"""
import html
import json
import mimetypes
import re
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from xml.etree.ElementTree import tostring

from latex2mathml.converter import Converter
from markdown_it import MarkdownIt
from markdown_it.rules_block.table import headerLineRe
from markdown_it.rules_inline.backticks import backtick
from markdown_it.rules_inline.state_inline import StateInline
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.dollarmath.index import math_inline_dollar

from . import nlp, store

STATIC = {
    'index.html': 'text/html; charset=utf-8',
    'app.js': 'text/javascript; charset=utf-8',
    'app.css': 'text/css; charset=utf-8',
}
DOC = re.compile(r'^/api/docs/(doc_[0-9a-f]{12})$')
ANN = re.compile(r'^/api/docs/(doc_[0-9a-f]{12})/annotations/(ann_[\w-]{1,64})$')
FILE = re.compile(r'^/api/docs/(doc_[0-9a-f]{12})/file$')
ASK = re.compile(r'^/api/docs/(doc_[0-9a-f]{12})/ask$')
GLOSSARY = re.compile(r'^/api/docs/(doc_[0-9a-f]{12})/(glossary|annotations)$')
IMAGES = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.avif', '.bmp', '.ico'}
EXTERNAL = re.compile(r'^([a-z][a-z0-9+.-]*:|/|#)', re.I)  # https: / data: / 絶対パス / アンカー
HOSTS = ('127.0.0.1', 'localhost')
# CTFP の category.tex で使われる圏論の略記。
MATH_ALIASES = {
    **{rf'\{name}': rf'\mathbf{{{name}}}' for name in ('Set', 'Rel', 'Cat', 'Hask', 'id', 'Lim', 'Ran', 'Lan')},
    r'\cat': r'\mathbf', r'\Fop': r'\mathbf{F}^{\mathit{op}}', r'\Nat': r'\mathit{Nat}',
}


def _image(self, tokens, idx, options, env):
    # 相対パスの画像は文書の場所を基準にサーバーから配信する。
    # ブラウザが URL 中の ../ を畳んでしまうので、パスはクエリで渡す
    tok = tokens[idx]
    src = tok.attrGet('src') or ''
    if env.get('files') and not EXTERNAL.match(src):
        tok.attrSet('src', f"{env['files']}?p={urllib.parse.quote(src, safe='%/')}")
    # image の代替テキスト描画は math トークンを無視するため、元の表記を保つ。
    for child in tok.children or []:
        if child.type.startswith('math_inline'):
            child.type = 'text'
            child.content = child.markup + child.content + child.markup
    return self.image(tokens, idx, options, env)


def _math(content, options):
    display = options['display_mode']
    try:
        # latex2mathml の複数行整列は align* で扱う。
        tex = content.replace(r'\begin{aligned}', r'\begin{align*}').replace(r'\end{aligned}', r'\end{align*}')
        tex = re.sub(r'\\idarrow\b(?:\s*\[([^\]]*)\])?',
                     lambda m: r'\mathbf{id}' + ('_{' + m[1] + '}' if m[1] else ''), tex)
        tex = re.sub(r'\\Lim\s*\[([^\]]*)\]', lambda m: r'\Lim{' + m[1] + '}', tex)
        tex = re.sub(r'\\[A-Za-z]+', lambda m: MATH_ALIASES.get(m[0], m[0]), tex)
        tree = Converter(display='block' if display else 'inline').convert_to_element(tex)
    except Exception:  # 不正な TeX があっても本文全体を読めなくしない。
        dollars = options.get('markup', '$$' if display else '$')
        return f'<code title="数式を変換できませんでした">{html.escape(dollars + content + dollars)}</code>'
    # 記号の文字参照だけデコードし、TeX 内の HTML は XML として安全にエスケープする。
    for element in tree.iter():
        if element.text:
            element.text = html.unescape(element.text)
    return tostring(tree, encoding='unicode', short_empty_elements=False)


def _math_inline_double(self, tokens, idx, options, env):
    # 独立した行の式だけ別行にし、段落内では本文と同じ行に表示する。
    breaks = {'softbreak', 'hardbreak'}
    display = (idx == 0 or tokens[idx - 1].type in breaks) and (
        idx + 1 == len(tokens) or tokens[idx + 1].type in breaks)
    kind = 'block' if display else 'inline'
    return f'<span class="math {kind}">' + _math(
        tokens[idx].content.strip(), {'display_mode': display, 'markup': tokens[idx].markup}) + '</span>'


def _table_pipes(state):
    # Pandoc の表では数式・コード中の | を列区切りにしない。
    if '|' not in state.src:
        return
    tokens = []
    state.md.block.parse(state.src, state.md, {}, tokens)
    lines = state.src.split('\n')
    math_rule = math_inline_dollar(allow_space=False, allow_digits=False, allow_double=True)
    tables = [token.map for token in tokens if token.type == 'table_open']
    # 見出しに未エスケープの | がある表は、最初の解析では段落になる。
    for token in tokens:
        if token.type != 'inline' or not token.map:
            continue
        for i in range(token.map[0], token.map[1] - 1):
            columns = lines[i + 1].strip().strip('|').split('|')
            if '|' in lines[i] and all(headerLineRe.fullmatch(c.strip()) for c in columns):
                tables.append([i, token.map[1]])
    for start_line, end_line in tables:
        for i in range(start_line, end_line):
            line = lines[i]
            if '|' not in line:
                break
            inline = StateInline(line, state.md, state.env, [])
            parts, cursor = [], 0
            while inline.pos < inline.posMax:
                start = inline.pos
                if not (math_rule(inline, True) or backtick(inline, True)):
                    state.md.inline.skipToken(inline)
                    continue
                parts.extend((line[cursor:start], line[start:inline.pos].replace('|', r'\|')))
                cursor = inline.pos
            lines[i] = ''.join(parts) + line[cursor:]
    state.src = '\n'.join(lines)


def render(kind, text, doc_id=None):
    if kind == 'markdown':
        # html: False で文書中の生 HTML を無効化（XSS 対策）
        md = MarkdownIt('commonmark', {'html': False}).enable(['table', 'strikethrough'])
        md.use(dollarmath_plugin, allow_labels=False, allow_digits=False, allow_space=False,
               double_inline=True, renderer=_math)
        md.add_render_rule('math_inline_double', _math_inline_double)
        md.core.ruler.before('block', 'table_pipes', _table_pipes)
        md.add_render_rule('image', _image)
        return md.render(text, {'files': doc_id and f'/api/docs/{doc_id}/file'})
    return f'<pre>{html.escape(text)}</pre>'


def make_server(data_dir, port=0):
    """開いたことのある文書（= 読書記録のある文書）をすべて扱うサーバー。"""

    def load(doc_id):
        rec = store.read(data_dir, doc_id)
        if not rec['document']:
            raise LookupError(f'文書がありません: {doc_id}')
        doc, text = store.meta(rec['document']['source_path'])  # 毎回最新の本文を読む
        return doc, render(doc['source_type'], text, doc_id), rec

    def doc_file(doc_id, rel):
        """文書から相対パスで参照される画像。画像の拡張子だけ返す。"""
        rec = store.read(data_dir, doc_id)
        if not rec['document']:
            raise LookupError(f'文書がありません: {doc_id}')
        path = (Path(rec['document']['source_path']).parent / rel).resolve()
        if path.suffix.lower() not in IMAGES or not path.is_file():
            raise LookupError(f'画像がありません: {rel}')
        return path.read_bytes(), mimetypes.guess_type(path.name)[0] or 'application/octet-stream'

    def records(clean, body):
        rows = store.list_records(data_dir)
        if clean:
            ids = {r['doc_id'] for r in rows if r['missing']}
        elif body.get('path'):
            if not Path(body['path']).expanduser().is_absolute():
                raise ValueError('フォルダは絶対パスで指定してください')
            ids = set(store.match(rows, body['path']))
        else:
            ids = set(body.get('doc_ids') or [])
        if body.get('dry_run'):
            return [r for r in rows if r['doc_id'] in ids]
        return store.clean(data_dir) if clean else store.delete_records(data_dir, sorted(ids))

    def add(body):
        path = Path(str(body.get('path', ''))).expanduser()
        if not path.is_absolute():
            raise ValueError('ファイルは絶対パスで指定してください')
        doc = store.register(data_dir, path)
        return next(r for r in store.list_records(data_dir) if r['doc_id'] == doc['id'])

    # UI → エージェント（スキル）への依頼キュー。`active-reader wait` が長ポーリングで受け取る
    queue, cond, waiting = [], threading.Condition(), [0]
    stopping = threading.Event()

    def ask(doc_id, body):
        """用語 {term} か、マーカー {ann_ids: [...]} への対応をエージェントに頼む。"""
        doc, markup, rec = load(doc_id)
        req = {'doc_id': doc_id, 'title': doc['title'], 'source_path': doc['source_path']}
        if 'ann_ids' in body:
            ids = {str(i) for i in body['ann_ids']}
            anns = [a for a in rec['annotations'] if a['id'] in ids]
            if not anns:
                raise ValueError('マーカーがありません')
            req |= {'kind': 'marks', 'annotations': [
                {k: a.get(k) for k in ('id', 'type', 'selected_text', 'note', 'status', 'ai')}
                | {'heading': a['locator'].get('heading')} for a in anns]}
        else:
            term = str(body.get('term', ''))[:200]
            text, code = nlp.parse(markup)
            hit = next((t for t in nlp.terms(text, code) if t['term'] == term), None)
            if not term or not hit:
                raise ValueError(f'用語が見つかりません: {term!r}')
            req |= {'kind': 'term', 'term': term,
                    'context': text[max(0, hit['start'] - 200):hit['end'] + 200]}

        def key(q):  # 同じ依頼は重ねない
            return q['doc_id'], q.get('term'), tuple(sorted(a['id'] for a in q.get('annotations', [])))
        with cond:
            if not any(key(q) == key(req) for q in queue):
                queue.append(req)
                cond.notify()
            return {'queued': True, 'agent_waiting': waiting[0] > 0}

    def next_request(body):
        with cond:
            waiting[0] += 1
            try:
                cond.wait_for(lambda: queue or stopping.is_set(), timeout=min(float(body.get('timeout', 50)), 120))
            finally:
                waiting[0] -= 1
            return None if stopping.is_set() else (queue.pop(0) if queue else {})

    def route(method, path, body):
        if method == 'POST' and path == '/api/shutdown':
            with cond:
                stopping.set()
                cond.notify_all()
            return 200, {'stopped': True}
        if method == 'POST' and path == '/api/requests/next':
            req = next_request(body)
            return (503, {'error': '読書を終了しました'}) if req is None else (200, req)
        m = ASK.match(path)
        if method == 'POST' and m:
            return 200, ask(m[1], body)
        if method == 'POST' and path == '/api/docs':
            return 200, add(body)
        if method == 'POST' and path in ('/api/records/delete', '/api/records/clean'):
            return 200, records(path.endswith('clean'), body)
        m = ANN.match(path)
        if not m:
            return 404, {'error': 'not found'}
        doc_id, ann_id = m.groups()
        if method == 'PUT':
            return 200, store.upsert(data_dir, load(doc_id)[0], ann_id, body)
        if method == 'DELETE':
            store.remove(data_dir, load(doc_id)[0], ann_id)
            return 200, {}
        return 404, {'error': 'not found'}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, ctype='application/json; charset=utf-8', sandbox=False):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('X-Content-Type-Options', 'nosniff')
            if sandbox:  # 文書側のファイル（SVG など）を直接開いてもスクリプトを動かさない
                self.send_header('Content-Security-Policy', 'sandbox')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def host_ok(self):
            # DNS rebinding 対策
            return self.headers.get('Host', '').rsplit(':', 1)[0] in HOSTS

        def do_GET(self):
            if not self.host_ok():
                return self.send(403, {'error': 'forbidden host'})
            url = urllib.parse.urlsplit(self.path)
            path = url.path
            if path == '/':
                path = '/static/index.html'
            name = path[len('/static/'):] if path.startswith('/static/') else None
            if name in STATIC:
                data = (files('active_reader') / 'static' / name).read_bytes()
                return self.send(200, data, STATIC[name])
            if path == '/api/docs':
                return self.send(200, store.list_records(data_dir))
            m = GLOSSARY.match(path)  # UI がエージェントの書き戻しを待つ間のポーリング用（軽い）
            if m:
                try:
                    return self.send(200, store.read(data_dir, m[1]).get(m[2], {}))
                except ValueError as e:
                    return self.send(404, {'error': str(e)})
            m = FILE.match(path)
            if m:
                try:
                    rel = urllib.parse.parse_qs(url.query).get('p', [''])[0]
                    data, ctype = doc_file(m[1], rel)
                except (LookupError, OSError, ValueError) as e:
                    return self.send(404, {'error': str(e)})
                return self.send(200, data, ctype, sandbox=True)
            m = DOC.match(path)
            if m:
                try:
                    doc, markup, rec = load(m[1])
                except (LookupError, OSError) as e:
                    return self.send(404, {'error': str(e)})
                text, code = nlp.parse(markup)
                # 数学用の太字なども含め、UI の文字位置は JavaScript の UTF-16 単位で返す。
                offsets = [0]
                for char in text:
                    offsets.append(offsets[-1] + (2 if ord(char) > 0xffff else 1))
                return self.send(200, {
                    'document': doc, 'html': markup, 'annotations': rec['annotations'],
                    'boundaries': [offsets[i] for i in nlp.boundaries(text)],
                    'terms': [{**t, 'start': offsets[t['start']], 'end': offsets[t['end']]}
                              for t in nlp.terms(text, code)],
                    'glossary': rec.get('glossary', {}),
                })
            self.send(404, {'error': 'not found'})

        def write(self, method):
            if not self.host_ok():
                return self.send(403, {'error': 'forbidden host'})
            # JSON 必須にして、他サイトからの単純リクエスト (CSRF) を通さない
            if self.headers.get_content_type() != 'application/json':
                return self.send(415, {'error': 'application/json only'})
            try:
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n) or b'{}')
                if not isinstance(body, dict):
                    raise ValueError('JSON object expected')
                self.send(*route(method, self.path, body))
                if method == 'POST' and self.path == '/api/shutdown':
                    # 成功応答を返してから、serve_forever と別のスレッドで止める。
                    self.wfile.flush()
                    threading.Thread(target=srv.shutdown, daemon=True).start()
            except (LookupError, OSError) as e:
                self.send(404, {'error': str(e)})
            except (ValueError, TypeError) as e:
                self.send(400, {'error': str(e)})

        def do_PUT(self):
            self.write('PUT')

        def do_POST(self):
            self.write('POST')

        def do_DELETE(self):
            self.write('DELETE')

    try:
        srv = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except OSError:
        # ponytail: ポート使用中ならランダムポート。origin が変わるのでスクロール位置は引き継がれない
        srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    return srv


def next_request(port, timeout=50):
    """UI からの依頼を 1 件待って返す。サーバーが無ければ OSError。"""
    req = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/requests/next', method='POST',
        data=json.dumps({'timeout': timeout}).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout + 10) as r:
        return json.load(r)


def attach(port, path):
    """port で動いている既存サーバーに文書を追加し、その URL を返す。無ければ None。"""
    req = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/docs', method='POST',
        data=json.dumps({'path': str(path)}).encode(),
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return f'http://127.0.0.1:{port}/#{json.load(r)["doc_id"]}'
    except (OSError, ValueError, KeyError):
        return None

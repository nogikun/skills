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

from markdown_it import MarkdownIt

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


def _image(self, tokens, idx, options, env):
    # 相対パスの画像は文書の場所を基準にサーバーから配信する。
    # ブラウザが URL 中の ../ を畳んでしまうので、パスはクエリで渡す
    tok = tokens[idx]
    src = tok.attrGet('src') or ''
    if env.get('files') and not EXTERNAL.match(src):
        tok.attrSet('src', f"{env['files']}?p={urllib.parse.quote(src, safe='%/')}")
    return self.image(tokens, idx, options, env)


def render(kind, text, doc_id=None):
    if kind == 'markdown':
        # html: False で文書中の生 HTML を無効化（XSS 対策）
        md = MarkdownIt('commonmark', {'html': False}).enable(['table', 'strikethrough'])
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
                cond.wait_for(lambda: queue, timeout=min(float(body.get('timeout', 50)), 120))
            finally:
                waiting[0] -= 1
            return queue.pop(0) if queue else {}

    def route(method, path, body):
        if method == 'POST' and path == '/api/requests/next':
            return 200, next_request(body)
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
                return self.send(200, {
                    'document': doc, 'html': markup, 'annotations': rec['annotations'],
                    'boundaries': nlp.boundaries(text),
                    'terms': nlp.terms(text, code),
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
        return ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except OSError:
        # ponytail: ポート使用中ならランダムポート。origin が変わるのでスクロール位置は引き継がれない
        return ThreadingHTTPServer(('127.0.0.1', 0), Handler)


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

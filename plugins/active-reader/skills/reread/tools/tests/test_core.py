import http.client
import json
import threading

import pytest

from active_reader import server, store

ANN = {'type': 'unknown', 'selected_text': 'RRF', 'context_before': '',
       'context_after': '', 'locator': {'heading': '', 'start_offset': 0,
                                        'end_offset': 3}, 'note': 'なぜ?'}


def make_doc(path, text='# Title\n\nRRF は順位の逆数を足す。\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return store.meta(path)[0]


def test_upsert_keeps_ai_fields(tmp_path):
    doc = make_doc(tmp_path / 'a.md')
    store.upsert(tmp_path, doc, 'ann_1', {**ANN, 'ai': {'explanation': 'x'}})
    assert store.read(tmp_path, doc['id'])['annotations'][0]['ai']['explanation'] is None
    store.edit(tmp_path, doc, 'ann_1', lambda a: a['ai'].update(explanation='AI'))
    ann = store.upsert(tmp_path, doc, 'ann_1', {'note': '変更'})
    assert ann['note'] == '変更' and ann['ai']['explanation'] == 'AI'
    with pytest.raises(ValueError):
        store.upsert(tmp_path, doc, 'ann_2', {**ANN, 'type': 'bogus'})


def test_match_by_file_and_folder():
    rows = [{'doc_id': 'a', 'source_path': '/p/b/x.md'},
            {'doc_id': 'b', 'source_path': '/p/bc/y.md'},
            {'doc_id': 'c', 'source_path': '/p/b/sub/z.md'}]
    assert store.match(rows, '/p/b') == ['a', 'c']
    assert store.match(rows, '/p/b/x.md') == ['a']


def test_clean_removes_only_missing(tmp_path):
    data = tmp_path / 'data'
    keep, gone = make_doc(tmp_path / 'keep.md'), make_doc(tmp_path / 'gone.md')
    for d in (keep, gone):
        store.upsert(data, d, 'ann_1', ANN)
    (tmp_path / 'gone.md').unlink()
    assert store.clean(data) == [gone['id']]
    assert [r['doc_id'] for r in store.list_records(data)] == [keep['id']]
    assert (tmp_path / 'keep.md').exists()
    with pytest.raises(ValueError):
        store.delete_records(data, ['../keep'])


def test_server_guards_and_docs(tmp_path):
    data = tmp_path / 'data'
    doc = make_doc(tmp_path / 'a.md')
    other = make_doc(tmp_path / 'b.md', '# Math\n\n' + r'$\cat{C}$ の AWS と CloudFront。' + '\n')
    store.register(data, tmp_path / 'a.md')
    srv = server.make_server(data)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def req(method, path, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', port)
        h = {'Content-Type': 'application/json', **(headers or {})}
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b'null')

    ann = f"/api/docs/{doc['id']}/annotations/ann_1"
    assert req('GET', '/api/docs', headers={'Host': 'evil.example'})[0] == 403
    assert req('PUT', ann, ANN, headers={'Content-Type': 'text/plain'})[0] == 415
    assert req('PUT', ann, ANN)[0] == 200
    assert req('POST', ann + '/ai/explain', {})[0] == 404  # AI は UI サーバーでは扱わない
    assert req('GET', '/api/docs/doc_000000000000')[0] == 404
    assert req('POST', '/api/docs', {'path': 'relative.md'})[0] == 400
    assert req('POST', '/api/records/delete', {'doc_ids': ['../x']})[0] == 400
    assert req('POST', '/api/records/delete', {'path': 'relative'})[0] == 400

    # 起動中のサーバーに別の文書を追加できる（cli open の 2 回目）
    assert server.attach(port, tmp_path / 'b.md').endswith('#' + other['id'])
    status, rows = req('GET', '/api/docs')
    assert status == 200 and {r['doc_id'] for r in rows} == {doc['id'], other['id']}
    status, d = req('GET', f"/api/docs/{doc['id']}")
    assert d['annotations'][0]['id'] == 'ann_1' and '<h1>' in d['html']
    from active_reader import nlp
    status, d = req('GET', f"/api/docs/{other['id']}")
    text = nlp.plain_text(d['html'])
    utf16 = text.encode('utf-16-le')
    assert len(utf16) // 2 > len(text)  # 太字 C はサロゲートペア。
    assert d['boundaries'][-1] == len(utf16) // 2
    for term in d['terms']:
        assert utf16[term['start'] * 2:term['end'] * 2].decode('utf-16-le') == term['term']
    assert 'AWS' in [t['term'] for t in d['terms']]

    # 文書からの相対パスの画像だけを配信する
    (tmp_path / 'img').mkdir()
    (tmp_path / 'img' / '図 1.png').write_bytes(b'\x89PNG fake')
    (tmp_path / 'secret.txt').write_text('x')
    base = f"/api/docs/{doc['id']}/file?p="
    assert 'src="' + base + 'img/%E5%9B%B3%201.png"' in server.render('markdown', '![a](<img/図 1.png>)', doc['id'])
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'sub' / 'c.md').write_text('![u](../img/x.png)')
    assert base.replace(doc['id'], 'doc_x') + '../img/x.png' in server.render('markdown', '![u](../img/x.png)', 'doc_x')
    assert 'src="https://e.com/a.png"' in server.render('markdown', '![a](https://e.com/a.png)', doc['id'])
    c = http.client.HTTPConnection('127.0.0.1', port)
    c.request('GET', base + 'img/%E5%9B%B3%201.png')
    r = c.getresponse()
    assert r.status == 200 and r.read() == b'\x89PNG fake'
    assert r.getheader('Content-Type') == 'image/png' and r.getheader('Content-Security-Policy') == 'sandbox'
    assert req('GET', base + 'secret.txt')[0] == 404
    assert req('GET', base + '../../../etc/passwd')[0] == 404
    srv.shutdown()


def test_markdown_math():
    from active_reader import nlp

    markup = server.render('markdown', r'前 $\alpha^2 + \frac{1}{2} < 3$ 後の AWS。' + '\n\n$$\n' + r'\sum_{i=1}^{n} x_i' + '\n$$')
    assert 'display="inline"' in markup and 'display="block"' in markup
    assert '<msup>' in markup and '<mfrac>' in markup and '<munderover>' in markup
    assert '<mi>α</mi>' in markup and '<mo>&lt;</mo>' in markup
    text, code = nlp.parse(markup)
    assert 'α' in text and '\\alpha' not in text
    aws = next(t for t in nlp.terms(text, code) if t['term'] == 'AWS')
    assert text[aws['start']:aws['end']] == 'AWS'
    assert '<mtable' in server.render('markdown', r'$$\begin{aligned}a &= b \\ c &= d\end{aligned}$$')
    assert '<mtable' in server.render('markdown', r'$$\begin{pmatrix}a & b \\ c & d\end{pmatrix}$$')
    assert 'alt="$A$ から $B$"' in server.render('markdown', '![$A$ から $B$](diagram.png)')


@pytest.mark.parametrize('source', [
    r'`$x^2$`', '```tex\n$$x^2$$\n```', r'\$x\$',
    '$5 and $10', '閉じていない $x',
])
def test_math_leaves_code_escaped_dollars_and_currency(source):
    assert '<math' not in server.render('markdown', source)
    assert '<math' not in server.render('text', '$x$')


def test_math_errors_and_html_are_safe():
    markup = server.render('markdown', r'前 $\left(x$ 後 $x^2$')
    assert r'$\left(x$' in markup and 'display="inline"' in markup
    assert '前' in markup and '後' in markup
    for source in (
        r'$\text{<img src=x onerror=alert(1)>}$',
        r'$\text{</mtext><script>alert(1)</script>}$',
        r'$\text{&lt;img src=x onerror=alert(1)&gt;}$',
        r'$\color{red" onmouseover="alert(1)}{x}$',
    ):
        markup = server.render('markdown', source)
        assert '<img' not in markup and '<script' not in markup
        assert ' onmouseover="' not in markup


def test_display_math_without_blank_lines_and_inside_paragraphs():
    from active_reader import nlp

    markup = server.render('markdown', '前の $a$。\n$$a + b$$\n後の $b$。')
    assert markup.count('<math ') == 3 and 'display="block"' in markup
    assert '<span class="math block">' in markup  # 段落内に div を入れない。
    assert nlp.plain_text(markup) == '前の a。\na+b\n後の b。\n'
    markup = server.render('markdown', '$$a+b$$ は $c$。')
    assert markup.count('<math ') == 2
    assert nlp.plain_text(markup) == 'a+b は c。\n'


def test_math_and_code_pipes_in_tables():
    from active_reader import nlp

    table = '| Logic | Types |\n|---|---|\n' + r'| $a\,||\,b$ | `Left a | Right b` |' + '\n'
    markup = server.render('markdown', table)
    assert markup.count('<td>') == 2 and '<math ' in markup
    assert 'a||b' in nlp.plain_text(markup)
    assert '<code>Left a | Right b</code>' in markup
    assert '<math ' not in server.render('markdown', '```md\n' + table + '```')
    markup = server.render('markdown', r'| $\|x\|$ | `$a||b$` |' + '\n|---|---|\n')
    assert markup.count('<th>') == 2 and markup.count('<math ') == 1
    assert '<code>$a||b$</code>' in markup
    assert '\u2016' in nlp.plain_text(markup)


@pytest.mark.parametrize(('tex', 'text'), [
    (r'\cat{C}', 'C'), (r'\Set', 'Set'), (r'\Rel', 'Rel'), (r'\Cat', 'Cat'),
    (r'\Hask', 'Hask'), (r'\id', 'id'), (r'\idarrow', 'id'),
    (r'\idarrow[A]', 'idA'), (r'\idarrow[F a]', 'idFa'),
    (r'\Lim{D}', 'LimD'), (r'\Lim[D]', 'LimD'), (r'\Ran_K D', 'RanKD'), (r'\Lan_K D', 'LanKD'),
    (r'\Fop', 'Fop'), (r'\Nat', 'Nat'),
])
def test_category_theory_math_aliases(tex, text):
    import unicodedata
    from xml.etree.ElementTree import fromstring

    tree = fromstring(server._math(tex, {'display_mode': False}))
    rendered = ''.join(tree.itertext())
    assert unicodedata.normalize('NFKC', rendered) == text
    assert rendered != text or any(el.get('mathvariant') in ('bold', 'italic') for el in tree.iter())


def test_cli_set_and_noninteractive_delete(tmp_path, monkeypatch, capsys):
    import io

    from active_reader import cli
    md = tmp_path / 'a.md'
    doc = make_doc(md)
    data = str(tmp_path / 'data')
    store.upsert(data, doc, 'ann_1', ANN)

    def run(*args, stdin=''):
        monkeypatch.setattr('sys.stdin', io.StringIO(stdin))
        cli.main([*args, '--data-dir', data])

    run('set', str(md), 'ann_1', 'explanation', stdin='{"summary": "s"}')
    run('set', str(md), 'ann_1', 'questions', stdin='["なぜ?"]')
    run('set', str(md), 'ann_1', 'status', stdin='understood\n')
    ann = store.read(data, doc['id'])['annotations'][0]
    assert ann['ai']['explanation'] == {'summary': 's'}
    assert ann['ai']['questions'] == [{'question': 'なぜ?', 'answer': None, 'grading': None}]
    assert ann['status'] == 'understood'
    with pytest.raises(SystemExit):
        run('set', str(md), 'ann_1', 'status', stdin='bogus')

    run('delete', str(md))  # stdin が EOF → 削除しない
    assert len(store.list_records(data)) == 1
    assert '中止しました' in capsys.readouterr().out


def test_bunsetsu_boundaries():
    from active_reader import nlp as bunsetsu
    s = '語が一致しなくても意味が近ければヒットする。TF の速さを決める'
    b = bunsetsu.boundaries(s)
    parts = [s[x:y] for x, y in zip([0] + b, b)][1:] if b[0] else [s[x:y] for x, y in zip(b, b[1:])]
    for p in ('一致しなくても', '意味が', '近ければ', 'TF の', '速さを'):
        assert p in parts, parts
    assert bunsetsu.plain_text(server.render('markdown', '# T\n\nA **b** &amp; <c>\n')) == 'T\nA b & <c>\n'


def test_terms_detection_and_glossary(tmp_path, monkeypatch, capsys):
    import io

    from active_reader import cli
    from active_reader import nlp
    md = '''# 章

Amazon Bedrock と CloudFront を AWS で使う。Σ は総和を表す。上位数十件に絞ることが重要である。
ハイブリッド検索では BM25 とベクトル検索を Reciprocal Rank Fusion で統合する。The ranking is good.

```python
scores = rrf(rankings)
```
'''
    text, code = nlp.parse(server.render('markdown', md))
    found = [t['term'] for t in nlp.terms(text, code)]
    for t in ('Amazon Bedrock', 'CloudFront', 'AWS', 'Σ', 'BM25', 'Reciprocal Rank Fusion', 'ハイブリッド検索'):
        assert t in found, found
    for t in ('こと', '上位数十件', 'The', 'ranking', 'scores', 'rrf'):  # 一般語・数量・英文の単語・コードは拾わない
        assert t not in found, found
    first = next(t for t in nlp.terms(text, code) if t['term'] == 'AWS')
    assert text[first['start']:first['end']] == 'AWS'

    path = tmp_path / 'a.md'
    path.write_text(md, encoding='utf-8')
    data = str(tmp_path / 'data')
    monkeypatch.setattr('sys.stdin', io.StringIO('{"AWS": {"summary": "クラウド", "in_this_text": "基盤"}}'))
    cli.main(['set-terms', str(path), '--data-dir', data])
    doc = store.meta(path)[0]
    assert store.read(data, doc['id'])['glossary']['AWS']['summary'] == 'クラウド'
    capsys.readouterr()
    cli.main(['terms', str(path), '--data-dir', data])
    assert 'AWS' not in [r['term'] for r in json.loads(capsys.readouterr().out)]  # 既定は未解説のみ


def test_ask_agent_queue(tmp_path, capsys):
    from active_reader import cli

    data = tmp_path / 'data'
    (tmp_path / 'a.md').write_text('# T\n\nAWS の CloudFront を使う。\n', encoding='utf-8')
    doc = store.register(data, tmp_path / 'a.md')
    srv = server.make_server(data)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def post(path, body):
        c = http.client.HTTPConnection('127.0.0.1', port)
        c.request('POST', path, json.dumps(body), {'Content-Type': 'application/json'})
        r = c.getresponse()
        return r.status, json.loads(r.read())

    ask = f"/api/docs/{doc['id']}/ask"
    status, res = post(ask, {'term': 'CloudFront'})
    assert status == 200 and res == {'queued': True, 'agent_waiting': False}
    post(ask, {'term': 'CloudFront'})  # 同じ依頼は重ねない
    assert post(ask, {'term': 'ない語'})[0] == 400
    req = server.next_request(port, timeout=1)
    assert req['term'] == 'CloudFront' and 'CloudFront を使う' in req['context']
    assert server.next_request(port, timeout=0.2) == {}  # 重複は 1 件だけ

    # マーカーもまとめて送れる
    store.upsert(data, doc, 'ann_1', ANN)
    store.upsert(data, doc, 'ann_2', {**ANN, 'type': 'quiz'})
    assert post(ask, {'ann_ids': ['ann_1', 'ann_2']})[0] == 200
    assert post(ask, {'ann_ids': ['nope']})[0] == 400
    try:
        # ChatGPT/Codex が会話再開時に、届いている依頼を即時取得する
        cli.main(['wait', '--port', str(port), '--timeout', '0'])
        req = json.loads(capsys.readouterr().out)
        assert req['kind'] == 'marks' and [a['id'] for a in req['annotations']] == ['ann_1', 'ann_2']
        assert req['annotations'][0]['note'] == 'なぜ?' and 'ai' in req['annotations'][0]
        cli.main(['wait', '--port', str(port), '--timeout', '0'])
        assert json.loads(capsys.readouterr().out) == {}

        # 実行中の会話で待っている間に来た UI 操作も受け取る
        timer = threading.Timer(0.05, lambda: post(ask, {'term': 'AWS'}))
        timer.start()
        cli.main(['wait', '--port', str(port), '--timeout', '1'])
        assert json.loads(capsys.readouterr().out)['term'] == 'AWS'
        timer.join()
        cli.main(['wait', '--port', str(port), '--timeout', '1'])
        assert json.loads(capsys.readouterr().out) == {}
    finally:
        srv.shutdown()
        srv.server_close()


def test_cli_wait_default_deadline_and_errors(monkeypatch, capsys):
    from active_reader import cli

    requests = iter([{}, {'kind': 'term', 'term': 'AWS'}])
    timeouts = []

    def next_request(port, timeout):
        timeouts.append(timeout)
        return next(requests)

    monkeypatch.setattr(server, 'next_request', next_request)
    cli.main(['wait'])  # Claude Code の無期限待ち受けは空の応答で終了しない
    assert json.loads(capsys.readouterr().out)['term'] == 'AWS'
    assert timeouts == [50, 50]

    requests = iter([{}, {}])
    timeouts.clear()
    ticks = iter([0, 0, 50, 50, 60])
    monkeypatch.setattr(cli.time, 'monotonic', lambda: next(ticks))
    cli.main(['wait', '--timeout', '60'])
    assert json.loads(capsys.readouterr().out) == {}
    assert timeouts == [50, 10]  # 各 HTTP 待機ではなくコマンド全体の上限

    with pytest.raises(SystemExit, match='timeout は 0 以上'):
        cli.main(['wait', '--timeout', '-1'])

    def disconnected(port, timeout):
        raise OSError('connection refused')

    monkeypatch.setattr(server, 'next_request', disconnected)
    with pytest.raises(SystemExit, match='接続できません'):
        cli.main(['wait'])


def test_ui_shutdown_stops_cli_and_only_its_server(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import re
    import subprocess
    import sys
    import urllib.error
    import urllib.request

    md = tmp_path / 'a.md'
    doc = make_doc(md)
    data = tmp_path / 'records'
    store.upsert(data, doc, 'ann_1', ANN)
    other = server.make_server(data)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    process = subprocess.Popen([
        sys.executable, '-c', 'from active_reader.cli import main; main()',
        'open', str(md), '--data-dir', str(data), '--port', '0', '--no-browser',
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        port = int(re.search(r'127\.0\.0\.1:(\d+)', process.stdout.readline()).group(1))
        url = f'http://127.0.0.1:{port}/api/shutdown'
        # 通常の GET や他サイトからの単純リクエストでは終了しない。
        for request, status in (
            (urllib.request.Request(url), 404),
            (urllib.request.Request(url, data=b'{}', headers={'Content-Type': 'text/plain'}), 415),
            (urllib.request.Request(url, data=b'{}', headers={'Content-Type': 'application/json', 'Host': 'evil.example'}), 403),
        ):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            assert error.value.code == status

        with ThreadPoolExecutor() as pool:
            pending = pool.submit(server.next_request, port, 50)
            request = urllib.request.Request(url, data=b'{}', headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request) as response:
                assert json.load(response) == {'stopped': True}
            with pytest.raises(OSError):
                pending.result(timeout=3)
        assert process.wait(timeout=3) == 0
        with pytest.raises(OSError):
            server.next_request(port, timeout=0)
        assert server.attach(other.server_address[1], md)
        assert store.read(data, doc['id'])['annotations'][0]['note'] == ANN['note']
        assert md.exists()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
        other.shutdown()
        other.server_close()

import json
from pathlib import Path
import subprocess
import sys
import threading
import urllib.request

import pytest

from active_reader import cli, hooks, server, store


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks.Path, 'home', lambda: tmp_path)
    return tmp_path


def test_install_preserves_existing_hooks_and_is_idempotent(home):
    path = home / '.codex' / 'hooks.json'
    path.parent.mkdir()
    original = {'description': 'existing', 'hooks': {
        'Stop': [{'hooks': [{'type': 'command', 'command': 'notify'}]}],
        'PostToolUse': [{'matcher': 'Bash', 'hooks': []}]}}
    before = json.dumps(original).encode()
    path.write_bytes(before)
    claude = home / '.claude' / 'settings.json'
    claude.parent.mkdir()
    claude.write_bytes(before)

    hooks.install(path)
    installed = json.loads(path.read_text())
    assert installed['description'] == original['description']
    assert installed['hooks']['Stop'][0] == original['hooks']['Stop'][0]
    assert installed['hooks']['PostToolUse'] == original['hooks']['PostToolUse']
    assert all('async' not in group['hooks'][0] for group in installed['hooks']['Stop'])
    assert path.with_name('hooks.json.reread-backup').read_bytes() == before
    first = path.read_bytes()
    hooks.install(path)
    assert path.read_bytes() == first
    assert claude.read_bytes() == before


def test_listen_requires_loaded_hook_and_is_scoped_to_session(home, monkeypatch):
    with pytest.raises(RuntimeError, match='未起動'):
        hooks.listen('reading', 8765)
    event = {'session_id': 'reading', 'hook_event_name': 'SessionStart'}
    assert hooks.handle(event)['hookSpecificOutput']['hookEventName'] == 'SessionStart'
    monkeypatch.setenv('CODEX_THREAD_ID', 'reading')
    cli.main(['listen', '--port', '8766'])
    hooks.handle({**event, 'source': 'compact'})
    assert json.loads(hooks.session_file('reading').read_text()) == {'port': 8766}
    assert hooks.handle({'session_id': 'other-chat', 'hook_event_name': 'Stop'}) == {}
    cli.main(['listen', '--stop'])
    assert hooks.handle({**event, 'hook_event_name': 'Stop'}) == {}
    for session_id in ('../escape', '', None):
        with pytest.raises(ValueError):
            hooks.session_file(session_id)
    with pytest.raises(ValueError):
        hooks.listen('reading', 0)


def test_stop_hook_receives_ui_request_and_can_continue_repeatedly(home, monkeypatch):
    md = home / 'a.md'
    md.write_text('# T\n\nAWS を使う。', encoding='utf-8')
    data = home / 'records'
    doc = store.register(data, md)
    srv = server.make_server(data)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    event = {'session_id': 'reading', 'hook_event_name': 'SessionStart'}
    hooks.handle(event)
    hooks.listen('reading', port)
    stop = {**event, 'hook_event_name': 'Stop', 'stop_hook_active': True}

    def ask(body):
        req = urllib.request.Request(f'http://127.0.0.1:{port}/api/docs/{doc["id"]}/ask',
                                     data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as response:
            return json.load(response)

    try:
        # Stop hook の待機中に用語が押されても、自動継続用 JSON に届く。
        timer = threading.Timer(0.1, lambda: ask({'term': 'AWS'}))
        timer.start()
        result = hooks.handle(stop)
        timer.join()
        assert result['decision'] == 'block'
        request = json.loads(result['reason'].split('\n', 1)[1])
        assert request['kind'] == 'term' and request['term'] == 'AWS'
        assert request['source_path'] == str(md)

        # 2 回目以降もマーカーを受け取る（stop_hook_active で打ち切らない）。
        store.upsert(data, doc, 'ann_1', {
            'type': 'unknown', 'selected_text': 'AWS', 'locator': {'heading': 'T'}})
        ask({'ann_ids': ['ann_1']})
        result = hooks.handle(stop)
        assert result['decision'] == 'block' and 'ann_1' in result['reason']
        assert server.next_request(port, timeout=0) == {}

        # 時間切れでも次の Stop hook を継続させる。
        real_open = hooks.urllib.request.urlopen
        def empty_queue(req, timeout):
            return real_open(urllib.request.Request(req.full_url, data=b'{"timeout": 0}',
                                                   headers={'Content-Type': 'application/json'}))
        with monkeypatch.context() as patch:
            patch.setattr(hooks.urllib.request, 'urlopen', empty_queue)
            assert hooks.handle(stop)['decision'] == 'block'

        hooks.handle({**event, 'hook_event_name': 'Interrupt'})
        assert hooks.handle(stop) == {}
        hooks.listen('reading', port)
        hooks.handle({**event, 'hook_event_name': 'SessionEnd'})
        assert not hooks.session_file('reading').exists()
    finally:
        srv.shutdown()
        srv.server_close()


def test_disconnected_server_disarms_listener(home, monkeypatch):
    event = {'session_id': 'reading', 'hook_event_name': 'SessionStart'}
    hooks.handle(event)
    hooks.listen('reading', 8765)
    def disconnected(*args, **kwargs):
        raise OSError('connection refused')
    monkeypatch.setattr(hooks.urllib.request, 'urlopen', disconnected)
    stop = {**event, 'hook_event_name': 'Stop'}
    assert 'systemMessage' in hooks.handle(stop)
    assert hooks.handle(stop) == {}


def test_ui_shutdown_disarms_stop_hook(home):
    from concurrent.futures import ThreadPoolExecutor

    srv = server.make_server(home)
    def serve():
        try:
            srv.serve_forever()
        finally:
            srv.server_close()
    threading.Thread(target=serve, daemon=True).start()
    port = srv.server_address[1]
    event = {'session_id': 'reading', 'hook_event_name': 'SessionStart'}
    hooks.handle(event)
    hooks.listen('reading', port)
    try:
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(hooks.handle, {**event, 'hook_event_name': 'Stop'})
            request = urllib.request.Request(f'http://127.0.0.1:{port}/api/shutdown',
                                             data=b'{}', headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request) as response:
                assert json.load(response)['stopped']
            assert 'decision' not in pending.result(timeout=3)
        assert json.loads(hooks.session_file('reading').read_text()) == {}
    finally:
        srv.shutdown()
        srv.server_close()


def test_hook_runs_without_package_dependencies():
    result = subprocess.run([sys.executable, '-S', str(Path(hooks.__file__))],
                            input=json.dumps({'session_id': 'unrelated', 'hook_event_name': 'Stop'}),
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {}

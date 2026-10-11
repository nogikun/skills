import argparse
import json
import os
import sys
import time
import webbrowser

from . import hooks, nlp, server, store


def confirm(title, rows, yes):
    if not rows:
        print('対象はありません')
        return False
    print(f'以下 {len(rows)} 件の{title}（元文書は削除しません）:')
    for r in rows:
        missing = ' (missing)' if r['missing'] else ''
        print(f"  {r['title'][:24]:24} {r['annotations']:>4} annotations  "
              f"{r['source_path']}{missing}")
    if yes:
        return True
    try:
        return input('よろしいですか? [y/N] ').strip().lower() == 'y'
    except EOFError:  # 非対話実行では削除しない（対象表示のみ）
        print('\n中止しました（-y で確定）')
        return False


def cmd_open(a):
    doc, _ = store.meta(a.file)
    # 既にサーバーが動いていれば、その画面に文書を追加するだけ
    # ponytail: その場合 --data-dir は既存サーバーのものが使われる
    url = server.attach(a.port, doc['source_path'])
    if url:
        print(f'起動中のサーバーに追加しました: {url}', flush=True)
        if not a.no_browser:
            webbrowser.open(url)
        return
    store.register(a.data_dir, a.file)
    srv = server.make_server(a.data_dir, a.port)
    url = f'http://127.0.0.1:{srv.server_address[1]}/#{doc["id"]}'
    print(f'{url} で開いています（Ctrl+C で終了）', flush=True)
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def cmd_export(a):
    doc, _ = store.meta(a.file)
    rec = {**store.read(a.data_dir, doc['id']), 'document': doc}
    out = json.dumps(rec, ensure_ascii=False, indent=2) + '\n'
    if a.output:
        with open(a.output, 'w', encoding='utf-8') as f:
            f.write(out)
    else:
        sys.stdout.write(out)


def cmd_set(a):
    """stdin の値をアノテーションに書き込む（スキル / 外部 AI からの書き戻し用）。"""
    raw = sys.stdin.read()
    if a.key in ('note', 'status'):
        value = raw.strip()
        if a.key == 'status' and value not in store.STATUSES:
            raise ValueError(f'status は {", ".join(store.STATUSES)} のいずれか')
    else:
        value = json.loads(raw)
        if a.key == 'questions':
            if not isinstance(value, list):
                raise ValueError('questions は配列で指定してください')
            value = [{'question': q, 'answer': None, 'grading': None} if isinstance(q, str)
                     else {'question': q['question'], 'answer': q.get('answer'),
                           'grading': q.get('grading')} for q in value]
        elif not isinstance(value, dict):
            raise ValueError(f'{a.key} は JSON オブジェクトで指定してください')
    doc, _ = store.meta(a.file)

    def fn(ann):
        if a.key in ('note', 'status'):
            ann[a.key] = value
        else:
            ann['ai'][a.key] = value
    store.edit(a.data_dir, doc, a.ann_id, fn)
    print(f'{a.ann_id} の {a.key} を更新しました')


def cmd_terms(a):
    """検出した専門用語を、最初に出てくる箇所の前後の文脈と一緒に出す（解説を書くための材料）。"""
    doc, source = store.meta(a.file)
    text, code = nlp.parse(server.render(doc['source_type'], source))
    glossary = store.read(a.data_dir, doc['id']).get('glossary', {})
    rows = [{'term': t['term'], 'kind': t['kind'], 'defined': t['term'] in glossary,
             'context': text[max(0, t['start'] - 80):t['end'] + 80]}
            for t in nlp.terms(text, code) if a.all or t['term'] not in glossary]
    sys.stdout.write(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')


def cmd_set_terms(a):
    """stdin の {用語: {summary, in_this_text, ...}} を用語集に書き込む。"""
    entries = json.loads(sys.stdin.read())
    if not isinstance(entries, dict) or not all(isinstance(v, dict) for v in entries.values()):
        raise ValueError('{"用語": {"summary": ..., "in_this_text": ...}} の形で指定してください')
    doc, _ = store.meta(a.file)
    store.set_glossary(a.data_dir, doc, entries)
    print(f'{len(entries)} 件の用語を書き込みました')


def cmd_wait(a):
    """UI からの依頼を 1 件待つ。時間切れなら空の JSON を返す。"""
    if a.timeout is not None and a.timeout < 0:
        raise ValueError('timeout は 0 以上の秒数で指定してください')
    deadline = None if a.timeout is None else time.monotonic() + a.timeout
    while True:
        timeout = 50 if deadline is None else min(50, max(0, deadline - time.monotonic()))
        try:
            req = server.next_request(a.port, timeout=timeout)
        except OSError as e:
            raise RuntimeError(f'UI サーバー (port {a.port}) に接続できません: {e}') from e
        if req or (deadline is not None and time.monotonic() >= deadline):
            print(json.dumps(req, ensure_ascii=False, indent=2), flush=True)
            return


def cmd_list(a):
    for r in store.list_records(a.data_dir):
        missing = ' (missing)' if r['missing'] else ''
        print(f"{r['doc_id']}  {r['annotations']:>4}  {r['updated_at'] or '-':25}  "
              f"{r['title'][:24]:24}  {r['source_path']}{missing}")


def cmd_listen(a):
    hooks.listen(a.session_id, a.port, a.stop)
    print('自動待ち受けを終了しました' if a.stop else 'この会話で UI の依頼に自動対応します')


def cmd_install_hooks(a):
    path = hooks.install()
    print(f'{path} に reread 用の hooks を追加しました（既存の hooks は保持）')
    print('/hooks で追加した定義を確認して信頼し、新しい会話で読書を開始してください')


def cmd_delete(a):
    rows = store.list_records(a.data_dir)
    ids = {i for p in a.paths for i in store.match(rows, p)}
    rows = [r for r in rows if r['doc_id'] in ids]
    if confirm('読書記録を削除します', rows, a.yes):
        store.delete_records(a.data_dir, [r['doc_id'] for r in rows])
        print('削除しました')


def cmd_clean(a):
    rows = [r for r in store.list_records(a.data_dir) if r['missing']]
    if confirm('元文書が見つからない読書記録を削除します', rows, a.yes):
        store.clean(a.data_dir)
        print('削除しました')


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--data-dir', default='~/.active-reader',
                        help='読書記録の保存先 (default: ~/.active-reader)')
    p = argparse.ArgumentParser(prog='active-reader')
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('open', parents=[common], help='文書を Web UI で開く')
    s.add_argument('file')
    s.add_argument('--port', type=int, default=8765)
    s.add_argument('--no-browser', action='store_true')
    s.set_defaults(fn=cmd_open)

    s = sub.add_parser('export', parents=[common], help='読書記録を JSON で出力')
    s.add_argument('file')
    s.add_argument('-o', '--output')
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser('set', parents=[common],
                       help='アノテーションに値を書き込む（値は stdin）')
    s.add_argument('file')
    s.add_argument('ann_id')
    s.add_argument('key', choices=['explanation', 'evaluation', 'questions', 'note', 'status'],
                   help='explanation/evaluation/questions は JSON、note/status はテキスト')
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser('terms', parents=[common], help='専門用語の候補と文脈を JSON で出力（既定は未解説のみ）')
    s.add_argument('file')
    s.add_argument('--all', action='store_true', help='解説済みも含める')
    s.set_defaults(fn=cmd_terms)

    s = sub.add_parser('set-terms', parents=[common], help='用語の解説を書き込む（stdin に JSON）')
    s.add_argument('file')
    s.set_defaults(fn=cmd_set_terms)

    s = sub.add_parser('wait', parents=[common],
                       help='UI からの依頼を 1 件待って JSON で出力')
    s.add_argument('--port', type=int, default=8765)
    s.add_argument('--timeout', type=int,
                   help='待つ秒数（省略時は無期限、0 は即時取得。時間切れは {}）')
    s.set_defaults(fn=cmd_wait)

    s = sub.add_parser('listen', help='ChatGPT/Codex の同期 Stop hook に待ち受けを任せる')
    s.add_argument('--session-id', default=os.environ.get('CODEX_THREAD_ID'))
    s.add_argument('--port', type=int, default=8765)
    s.add_argument('--stop', action='store_true', help='この会話の自動待ち受けを終了する')
    s.set_defaults(fn=cmd_listen)

    s = sub.add_parser('install-hooks', help='既存の設定を保持して ChatGPT/Codex 用 hooks を登録する')
    s.set_defaults(fn=cmd_install_hooks)

    s = sub.add_parser('list', parents=[common], help='読書記録の一覧')
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser('delete', parents=[common],
                       help='文書（ファイル）またはプロジェクト（フォルダ配下）の記録を削除')
    s.add_argument('paths', nargs='+')
    s.add_argument('-y', '--yes', action='store_true')
    s.set_defaults(fn=cmd_delete)

    s = sub.add_parser('clean', parents=[common], help='元文書が無い記録を削除')
    s.add_argument('-y', '--yes', action='store_true')
    s.set_defaults(fn=cmd_clean)

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except (OSError, ValueError, RuntimeError) as e:
        sys.exit(f'error: {e}')

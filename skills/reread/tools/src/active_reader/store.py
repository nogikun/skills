"""読書記録 JSON（1文書1ファイル）の読み書き。元文書には書き込まない。"""
import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# ponytail: 全文書で1つのロック。多数の文書を同時編集するならファイル単位に
LOCK = threading.Lock()
DOC_ID = re.compile(r'^doc_[0-9a-f]{12}$')
SOURCE_TYPES = {'.md': 'markdown', '.markdown': 'markdown', '.txt': 'text'}
KINDS = ('unknown', 'explain', 'quiz')
STATUSES = ('new', 'learning', 'understood', 'review_required', 'mastered')
USER_FIELDS = ('type', 'selected_text', 'context_before', 'context_after',
               'locator', 'note', 'status')


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def meta(source):
    """元文書を読み、document メタデータと本文を返す。"""
    source = Path(source).expanduser().resolve()
    kind = SOURCE_TYPES.get(source.suffix.lower())
    if not kind:
        raise ValueError(f'未対応の形式です: {source.name}（.md / .txt のみ）')
    text = source.read_text(encoding='utf-8')
    title = source.stem
    if kind == 'markdown':
        title = next((ln[2:].strip() for ln in text.splitlines()
                      if ln.startswith('# ')), title)
    digest = hashlib.sha256(str(source).encode()).hexdigest()[:12]
    return {
        'id': f'doc_{digest}',
        'title': title,
        'source_path': str(source),
        'source_type': kind,
        'content_hash': 'sha256:' + hashlib.sha256(text.encode()).hexdigest(),
    }, text


def record_path(data_dir, doc_id):
    if not isinstance(doc_id, str) or not DOC_ID.match(doc_id):
        raise ValueError(f'不正な doc_id です: {doc_id!r}')
    d = Path(data_dir).expanduser() / 'docs'
    d.mkdir(parents=True, exist_ok=True)
    return d / f'{doc_id}.json'


def read(data_dir, doc_id):
    p = record_path(data_dir, doc_id)
    if not p.exists():
        return {'schema_version': '1.0', 'document': None, 'annotations': [], 'glossary': {}}
    return json.loads(p.read_text(encoding='utf-8'))


@contextmanager
def locked(data_dir):
    """スレッド間 + プロセス間（UI サーバーと CLI）の排他。"""
    lock = Path(data_dir).expanduser() / 'docs' / '.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with LOCK, open(lock, 'a') as f:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
        yield  # close でロック解放


def update(data_dir, doc, fn):
    """read → fn(rec) → アトミックに write。fn の戻り値を返す。"""
    with locked(data_dir):
        rec = read(data_dir, doc['id'])
        rec['document'] = doc
        out = fn(rec)
        p = record_path(data_dir, doc['id'])
        tmp = p.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                       encoding='utf-8')
        os.replace(tmp, p)
        return out


def find(rec, ann_id):
    for a in rec['annotations']:
        if a['id'] == ann_id:
            return a
    raise ValueError(f'アノテーションがありません: {ann_id}')


def upsert(data_dir, doc, ann_id, fields):
    """ユーザー入力フィールドだけを反映する。ai はサーバー側でしか書かない。"""
    def fn(rec):
        try:
            ann = find(rec, ann_id)
        except ValueError:
            ann = {'id': ann_id, 'status': 'new', 'created_at': now(),
                   'ai': {'explanation': None, 'evaluation': None, 'questions': []}}
            rec['annotations'].append(ann)
        ann.update({k: fields[k] for k in USER_FIELDS if k in fields})
        if ann.get('type') not in KINDS or ann['status'] not in STATUSES:
            raise ValueError(f'不正な type / status です: {ann.get("type")}, {ann["status"]}')
        ann['updated_at'] = now()
        return ann
    return update(data_dir, doc, fn)


def register(data_dir, source):
    """文書を一覧に載せる（読書記録を作る / 更新日時を新しくする）。"""
    doc, _ = meta(source)
    update(data_dir, doc, lambda rec: None)
    return doc


def set_glossary(data_dir, doc, entries):
    """用語の解説（AI が書く）をマージする。{用語: {summary, in_this_text, ...}}"""
    def fn(rec):
        glossary = rec.setdefault('glossary', {})
        for term, entry in entries.items():
            glossary[term] = {**entry, 'updated_at': now()}
    update(data_dir, doc, fn)


def edit(data_dir, doc, ann_id, fn):
    def wrap(rec):
        ann = find(rec, ann_id)
        fn(ann)
        ann['updated_at'] = now()
        return ann
    return update(data_dir, doc, wrap)


def remove(data_dir, doc, ann_id):
    def fn(rec):
        rec['annotations'] = [a for a in rec['annotations'] if a['id'] != ann_id]
    update(data_dir, doc, fn)


# --- 記録の管理 -------------------------------------------------------------

def list_records(data_dir):
    rows = []
    for p in sorted((Path(data_dir).expanduser() / 'docs').glob('doc_*.json')):
        rec = json.loads(p.read_text(encoding='utf-8'))
        d, anns = rec.get('document') or {}, rec.get('annotations', [])
        src = d.get('source_path', '')
        rows.append({
            'doc_id': p.stem,
            'title': d.get('title', p.stem),
            'source_path': src,
            'annotations': len(anns),
            'updated_at': max((a.get('updated_at', '') for a in anns), default=''),
            'missing': not src or not Path(src).exists(),
            'modified': datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec='seconds'),
        })
    return rows


def match(rows, path):
    """path がファイルならその文書、ディレクトリなら配下すべての doc_id。"""
    target = Path(path).expanduser().resolve()
    return [r['doc_id'] for r in rows
            if r['source_path'] and Path(r['source_path']).is_relative_to(target)]


def delete_records(data_dir, doc_ids):
    paths = [record_path(data_dir, i) for i in doc_ids]  # 先に全件検証
    deleted = []
    with locked(data_dir):
        for i, p in zip(doc_ids, paths):
            if p.exists():
                p.unlink()
                deleted.append(i)
    return deleted


def clean(data_dir):
    """元文書が無い記録と、古い書き込み残骸 (*.tmp) を削除する。"""
    for t in (Path(data_dir).expanduser() / 'docs').glob('*.tmp'):
        if time.time() - t.stat().st_mtime > 60:  # 書き込み中の tmp は残す
            t.unlink(missing_ok=True)
    return delete_records(data_dir, [r['doc_id'] for r in list_records(data_dir)
                                     if r['missing']])

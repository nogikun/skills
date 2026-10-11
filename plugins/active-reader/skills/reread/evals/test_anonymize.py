import importlib.util
import json
import re
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name('anonymize.py')
spec = importlib.util.spec_from_file_location('eval_anonymize', SCRIPT)
anonymize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(anonymize)


def test_export_removes_personal_paths_without_changing_source(tmp_path):
    source = tmp_path / 'logs'
    source.mkdir()
    record = {'source_path': '/Users/private-person/books/book.md',
              'windows': r'C:\Users\Private Person\books\book.md',
              'unix': '/home/other-person/book.md', 'score': 3}
    original = json.dumps(record)
    (source / 'record.json').write_text(original, encoding='utf-8')
    (source / 'command.txt').write_text(
        str(anonymize.REPOSITORY / 'skills/reread/tools') + '\n'
        + r'C:\\Users\\Private Person\\books\\book.md' + '\n'
        + r'\/Users\/private-person\/book.md', encoding='utf-8')
    (source / 'screenshot.png').write_bytes(b'image data')
    (source / 'linked.txt').symlink_to(source / 'record.json')
    output = tmp_path / 'shared.zip'
    assert anonymize.export_logs(source, output) == (2, 2)
    assert (source / 'record.json').read_text(encoding='utf-8') == original
    with zipfile.ZipFile(output) as bundle:
        assert set(bundle.namelist()) == {'record.json', 'command.txt'}
        exported = json.loads(bundle.read('record.json'))
        assert exported['source_path'] == '/workspace/home/books/book.md'
        assert exported['score'] == 3
        text = '\n'.join(bundle.read(name).decode() for name in bundle.namelist())
        for private in ('private-person', 'Private Person', 'other-person',
                        str(anonymize.REPOSITORY), '/Users/'):
            assert private not in text
        assert not re.search(r'(?<!\w)/home/', text)
        assert '/workspace/re_read/skills/reread/tools' in text


def test_invalid_logs_do_not_replace_previous_export(tmp_path):
    source = tmp_path / 'logs'
    source.mkdir()
    (source / 'record.json').write_text('{broken', encoding='utf-8')
    output = tmp_path / 'shared.zip'
    output.write_bytes(b'previous export')
    with pytest.raises(ValueError):
        anonymize.export_logs(source, output)
    assert output.read_bytes() == b'previous export'
    with pytest.raises(ValueError):
        anonymize.export_logs(source, source / 'output.zip')


def test_repository_prefix_matches_only_the_directory():
    actual = '/Users/owner/re_read'
    result = anonymize.anonymize_text(
        actual + '/log.txt\n' + actual + '-other/log.txt',
        repository=actual, home='/Users/owner')
    assert result == '/workspace/re_read/log.txt\n/workspace/home/re_read-other/log.txt'
    assert anonymize.anonymize_text(result) == result
    temporary = '/private/var/folders/ab/private-hash/T/pytest-of-owner/run/log.txt'
    assert anonymize.anonymize_text(temporary) == '/workspace/tmp/pytest/run/log.txt'

"""評価のテキストログを匿名化して共有用 ZIP にまとめる。"""
import argparse
import json
import re
import tempfile
import zipfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
TEXT_TYPES = {'.json', '.txt', '.md', '.log'}
UNIX_HOME = re.compile(r'(?<!\w)/(?:Users|home)/[^/\r\n\"\'<>]+')
WINDOWS_HOME = re.compile(r'[A-Za-z]:\\{1,2}Users\\{1,2}[^\\\r\n\"\'<>]+', re.I)
TEMP_PATH = re.compile(r'(?<!\w)/(?:private/)?(?:var/folders/[^/]+/[^/]+/[TC]|tmp)(?=/)')


def anonymize_text(text, repository=REPOSITORY, home=None):
    home = Path.home() if home is None else home
    for prefix, replacement in ((repository, '/workspace/re_read'),
                                (home, '/workspace/home')):
        # JSON 形式でエスケープされたパスも、コマンド履歴に残ることがある。
        raw = str(prefix)
        for variant in {raw, raw.replace('/', r'\/'), json.dumps(raw)[1:-1]}:
            text = re.sub(re.escape(variant) + r'(?=$|[/\\\s\"\'<>])',
                          lambda _: replacement, text)
    text = text.replace(r'\/Users\/', '/Users/').replace(r'\/home\/', '/home/')
    text = UNIX_HOME.sub('/workspace/home', text)
    text = WINDOWS_HOME.sub('/workspace/home', text)
    text = TEMP_PATH.sub('/workspace/tmp', text)
    return re.sub(r'(?<=/)pytest-of-[^/\r\n\"\'<>]+', 'pytest', text)


def anonymize_json(value):
    if isinstance(value, str):
        return anonymize_text(value)
    if isinstance(value, list):
        return [anonymize_json(item) for item in value]
    if isinstance(value, dict):
        result = {anonymize_text(key): anonymize_json(item) for key, item in value.items()}
        if len(result) != len(value):
            raise ValueError('匿名化後に JSON のキーが重複します')
        return result
    return value


def export_logs(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_dir():
        raise ValueError('評価ログのフォルダがありません')
    if output.is_relative_to(source) or output.suffix.lower() != '.zip':
        raise ValueError('出力先には評価フォルダの外の ZIP ファイルを指定してください')
    output.parent.mkdir(parents=True, exist_ok=True)
    count = skipped = 0
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        archive = Path(tmp) / 'logs.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(source.rglob('*')):
                if path.is_symlink():
                    skipped += 1
                    continue
                if not path.is_file():
                    continue
                if path.suffix.lower() not in TEXT_TYPES:
                    skipped += 1
                    continue
                text = path.read_text(encoding='utf-8')
                if path.suffix.lower() == '.json':
                    text = json.dumps(anonymize_json(json.loads(text)),
                                      ensure_ascii=False, indent=2) + '\n'
                else:
                    text = anonymize_text(text)
                bundle.writestr(path.relative_to(source).as_posix(), text)
                count += 1
        if not count:
            raise ValueError('共有できるテキストログがありません')
        archive.replace(output)
    return count, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', nargs='?', type=Path,
                        default=REPOSITORY / 'skills/reread-workspace')
    parser.add_argument('output', nargs='?', type=Path,
                        default=REPOSITORY / 'dist/eval-logs.zip')
    args = parser.parse_args()
    try:
        count, skipped = export_logs(args.source, args.output)
    except (OSError, ValueError, UnicodeError):
        # エラーの例外文には元のパスやログ内容が含まれうるので表示しない。
        parser.exit(1, '匿名化できませんでした。入力の形式・出力先・権限を確認してください。\n')
    print(f'匿名化したテキストログ {count} 件を ZIP に保存しました（対象外 {skipped} 件）。')


if __name__ == '__main__':
    main()

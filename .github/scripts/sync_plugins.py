#!/usr/bin/env python3
"""配布元を自動発見して plugins/ と marketplace.json を作り直す。

配布元は決め打ちしない。OWNER の public repo のうち skills/<name>/SKILL.md を
持つものを配布元とみなし、repo 1 つを plugin 1 つに対応させる。
1 つの repo に skill が複数あれば、その plugin にまとまって入る。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OWNER = os.environ.get("OWNER", "nogikun")
SELF = os.environ.get("GITHUB_REPOSITORY", "")
ROOT = Path(__file__).resolve().parents[2]


def sh(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def write_json(path: Path, data: dict) -> None:
    """改行は必ず LF。Windows で生成すると CRLF になって全行差分になる。"""
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_bytes(text.encode("utf-8"))


def discover() -> list[dict]:
    """配布元と、そこにある skill 名を集める。"""
    repos = json.loads(sh(
        "gh", "repo", "list", OWNER, "--no-archived", "--visibility", "public",
        "--limit", "200", "--json", "nameWithOwner,description",
    ))
    found = []
    for r in repos:
        name = r["nameWithOwner"]
        if name == SELF:
            continue
        try:
            tree = json.loads(sh("gh", "api", f"repos/{name}/git/trees/HEAD?recursive=1"))
        except subprocess.CalledProcessError:
            continue
        skills = sorted(
            p.split("/")[1]
            for e in tree.get("tree", [])
            if e.get("type") == "blob"
            for p in [e["path"]]
            if p.startswith("skills/") and p.endswith("/SKILL.md") and p.count("/") == 2
        )
        if skills:
            found.append({"repo": name, "description": r.get("description") or "", "skills": skills})
    return sorted(found, key=lambda f: f["repo"])


def main() -> int:
    found = discover()
    if not found:
        print("::error::配布元が 1 つも見つからない。走査条件か権限を疑う", file=sys.stderr)
        return 1

    # 同名 skill は後勝ちで黙って上書きされるので、ここで止める
    seen: dict[str, str] = {}
    for f in found:
        for s in f["skills"]:
            if s in seen:
                print(f"::error::skill 名 {s} が {seen[s]} と {f['repo']} で重複している", file=sys.stderr)
                return 1
            seen[s] = f["repo"]

    work = Path(tempfile.mkdtemp())
    staged = work / "staged"
    for f in found:
        # repo ごとに別のディレクトリへ入れて、どの repo 由来かを保つ
        into = work / f["repo"].replace("/", "_")
        into.mkdir(parents=True)
        subprocess.run(
            ["npx", "-y", "skills@latest", "add", f["repo"], "-y", "-s", "*", "-a", "claude-code"],
            cwd=into, check=True, stdin=subprocess.DEVNULL, shell=os.name == "nt",
            # stdout は最後のサマリ専用。npx の TUI 出力を混ぜない
            stdout=sys.stderr,
        )
        src = into / ".claude" / "skills"
        got = sorted(p.name for p in src.iterdir() if p.is_dir()) if src.is_dir() else []
        if got != f["skills"]:
            print(f"::error::{f['repo']}: 期待 {f['skills']} に対し取得できたのは {got}", file=sys.stderr)
            return 1

        if not f["description"]:
            print(f"::warning::{f['repo']} に description が無い。marketplace の説明が空になる",
                  file=sys.stderr)
        plugin = f["repo"].split("/")[1]
        dest = staged / "plugins" / plugin
        shutil.copytree(src, dest / "skills")
        (dest / ".claude-plugin").mkdir(parents=True)
        write_json(dest / ".claude-plugin" / "plugin.json", {
            "name": plugin,
            "description": f["description"],
            "author": {"name": OWNER, "url": f"https://github.com/{OWNER}"},
            "repository": f"https://github.com/{f['repo']}",
            "skills": "./skills/",
        })

    marketplace = {
        "name": OWNER,
        "owner": {"name": OWNER, "url": f"https://github.com/{OWNER}"},
        "interface": {"displayName": f"{OWNER} skills"},
        "plugins": [
            {
                "name": f["repo"].split("/")[1],
                "source": f"./plugins/{f['repo'].split('/')[1]}",
                "description": f["description"],
            }
            for f in found
        ],
    }

    shutil.rmtree(ROOT / "plugins", ignore_errors=True)
    shutil.copytree(staged / "plugins", ROOT / "plugins")
    (ROOT / ".claude-plugin").mkdir(exist_ok=True)
    write_json(ROOT / ".claude-plugin" / "marketplace.json", marketplace)

    for f in found:
        print(f"{f['repo']}\t{f['repo'].split('/')[1]}\t{' '.join(f['skills'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

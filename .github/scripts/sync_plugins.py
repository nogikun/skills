#!/usr/bin/env python3
"""配布元を自動発見して skills/ と plugins/ を作り直す。

配布元は決め打ちしない。OWNER の public repo のうち skills/<name>/SKILL.md を
持つものを配布元とみなし、repo 1 つを plugin 1 つに対応させる。
1 つの repo に skill が複数あれば、その plugin にまとまって入る。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OWNER = os.environ.get("OWNER", "nogikun")
SELF = os.environ.get("GITHUB_REPOSITORY", f"{OWNER}/skills")
ROOT = Path(__file__).resolve().parents[2]


def sh(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def write_json(path: Path, data: dict) -> None:
    """改行は必ず LF。Windows で生成すると CRLF になって全行差分になる。"""
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_bytes(text.encode("utf-8"))


def same_skills(previous: Path, current: Path) -> bool:
    old = {p.relative_to(previous) for p in previous.rglob("*") if p.is_file()}
    new = {p.relative_to(current) for p in current.rglob("*") if p.is_file()}
    if old != new:
        return False
    for p in old:
        if (previous / p).read_bytes() == (current / p).read_bytes():
            continue
        # mirror の .gitattributes に従い、改行だけの差で毎週 version を上げない。
        git_path = (previous / p).relative_to(ROOT).as_posix()
        args = ("git", "-C", str(ROOT), "hash-object", f"--path={git_path}")
        if sh(*args, str(previous / p)) != sh(*args, str(current / p)):
            return False
    return True


def write_manifests(dest: Path, previous: Path, name: str, description: str, repo: str) -> str:
    legacy = previous / ".claude-plugin" / "plugin.json"
    portable = previous / "plugin.json"
    old = json.loads(legacy.read_text(encoding="utf-8")) if legacy.is_file() else {}
    old_portable = json.loads(portable.read_text(encoding="utf-8")) if portable.is_file() else {}
    version = old_portable.get("version", old.get("version", "1.0.0"))
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"{previous}: version must be X.Y.Z, got {version!r}")
    # 同じ内容での週次実行はバージョンを変えず、変更時だけキャッシュを更新させる。
    if (old or old_portable) and (
        old.get("name") != name or old.get("description") != description
        or not same_skills(previous / "skills", dest / "skills")
    ):
        major, minor, patch = map(int, version.split("."))
        version = f"{major}.{minor}.{patch + 1}"
    (dest / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    write_json(dest / ".claude-plugin" / "plugin.json", {
        "name": name, "version": version, "description": description,
        "author": {"name": OWNER, "url": f"https://github.com/{OWNER}"},
        "repository": f"https://github.com/{repo}", "skills": "./skills/",
    })
    write_json(dest / "plugin.json", {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": name, "version": version, "description": description,
    })
    return version


def discover() -> list[dict]:
    """配布元と、そこにある skill 名を集める。"""
    repos = json.loads(sh(
        "gh", "repo", "list", OWNER, "--no-archived", "--visibility", "public",
        "--limit", "200", "--json", "nameWithOwner,description,isEmpty",
    ))
    found = []
    for r in repos:
        name = r["nameWithOwner"]
        if name == SELF or r.get("isEmpty"):
            continue
        # API 失敗を「skill が無い」と扱うと、既存 plugin を消してしまう。
        tree = json.loads(sh("gh", "api", f"repos/{name}/git/trees/HEAD?recursive=1"))
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
    versions = {}
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
        # Hermes の custom tap は repo 直下の skills/<name>/SKILL.md を読む。
        # Claude/Codex の plugin 用コピーとは別に、同じ内容を flat な
        # skills/ にも置いて、1 つの repo を両方の配布形式にする。
        hermes_skills = staged / "skills"
        hermes_skills.mkdir(parents=True, exist_ok=True)
        for skill in f["skills"]:
            shutil.copytree(src / skill, hermes_skills / skill)
        versions[plugin] = write_manifests(dest, ROOT / "plugins" / plugin, plugin, f["description"], f["repo"])

    versions[OWNER] = write_manifests(staged, ROOT, OWNER, f"{OWNER} が作った Agent Skills の配布用ミラー", SELF)

    marketplace = {
        "name": OWNER,
        "owner": {"name": OWNER, "url": f"https://github.com/{OWNER}"},
        "interface": {"displayName": f"{OWNER} skills"},
        "plugins": [
            {
                "name": OWNER,
                "source": "./",
                "version": versions[OWNER],
                "description": f"{OWNER} が作った Agent Skills をまとめて導入する",
            },
            *[
                {
                    "name": f["repo"].split("/")[1],
                    "source": f"./plugins/{f['repo'].split('/')[1]}",
                    "version": versions[f["repo"].split("/")[1]],
                    "description": f["description"],
                }
                for f in found
            ]
        ],
    }

    shutil.rmtree(ROOT / "skills", ignore_errors=True)
    shutil.copytree(staged / "skills", ROOT / "skills")
    shutil.rmtree(ROOT / "plugins", ignore_errors=True)
    shutil.copytree(staged / "plugins", ROOT / "plugins")
    (ROOT / ".claude-plugin").mkdir(exist_ok=True)

    # coji/natural-japanese と同じく、repo 直下自体も 1 つの plugin として
    # 追加できるようにする。個別 plugin (`plugins/<repo>/`) も残すので、
    # 利用者は全 skill 一括・配布元ごとのどちらでも選べる。
    shutil.copyfile(staged / ".claude-plugin" / "plugin.json", ROOT / ".claude-plugin" / "plugin.json")
    shutil.copyfile(staged / "plugin.json", ROOT / "plugin.json")
    write_json(ROOT / ".claude-plugin" / "marketplace.json", marketplace)
    codex_catalog = {
        "name": OWNER,
        "interface": marketplace["interface"],
        "plugins": [
            {
                **p,
                "source": {"source": "local", "path": p["source"]},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Productivity",
            }
            for p in marketplace["plugins"]
        ],
    }
    (ROOT / ".agents" / "plugins").mkdir(parents=True, exist_ok=True)
    write_json(ROOT / ".agents" / "plugins" / "marketplace.json", codex_catalog)

    for f in found:
        print(f"{f['repo']}\t{f['repo'].split('/')[1]}\t{' '.join(f['skills'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

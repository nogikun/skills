import importlib.util
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("sync_plugins", Path(__file__).with_name("sync_plugins.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class SyncPluginsTests(unittest.TestCase):
    def setUp(self):
        environment = patch.multiple(sync, OWNER="nogikun", SELF="nogikun/skills")
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        (self.root / ".gitattributes").write_text("* text=auto eol=lf\n")
        self.upstream = self.base / "upstream" / "demo"
        self.upstream.mkdir(parents=True)
        (self.upstream / "SKILL.md").write_text("---\nname: demo\ndescription: Demo\n---\nFirst version\n")
        self.found = [{"repo": "nogikun/example", "description": "Example", "skills": ["demo"]}]
        self.work_count = 0

    def sync(self):
        real_run = subprocess.run
        def workdir():
            self.work_count += 1
            work = self.base / f"work-{self.work_count}"
            work.mkdir()
            return str(work)

        def install(args, **kwargs):
            if args[0] != "npx":
                return real_run(args, **kwargs)
            shutil.copytree(self.upstream, Path(kwargs["cwd"]) / ".claude" / "skills" / "demo")
            return subprocess.CompletedProcess(args, 0)

        with patch.object(sync, "ROOT", self.root), patch.object(sync, "discover", return_value=self.found), \
                patch.object(sync.tempfile, "mkdtemp", side_effect=workdir), \
                patch.object(sync.subprocess, "run", side_effect=install), redirect_stdout(io.StringIO()):
            return sync.main()

    def manifest(self, plugin=None, legacy=False):
        root = self.root / "plugins" / plugin if plugin else self.root
        file = root / ".claude-plugin" / "plugin.json" if legacy else root / "plugin.json"
        return json.loads(file.read_text())

    def test_catalog_and_manifests_keep_the_same_names_and_versions(self):
        self.assertEqual(self.sync(), 0)
        catalog = json.loads((self.root / ".claude-plugin" / "marketplace.json").read_text())
        codex = json.loads((self.root / ".agents" / "plugins" / "marketplace.json").read_text())
        self.assertEqual([(p["name"], p["version"]) for p in catalog["plugins"]],
                         [(p["name"], p["version"]) for p in codex["plugins"]])
        self.assertEqual([p["name"] for p in catalog["plugins"]], ["nogikun", "example"])
        for entry in catalog["plugins"]:
            plugin = None if entry["source"] == "./" else entry["name"]
            for legacy in (True, False):
                manifest = self.manifest(plugin, legacy)
                self.assertEqual((manifest["name"], manifest["version"]), (entry["name"], entry["version"]))
        self.assertEqual((self.root / "skills" / "demo" / "SKILL.md").read_bytes(),
                         (self.root / "plugins" / "example" / "skills" / "demo" / "SKILL.md").read_bytes())

    def test_unchanged_sync_is_idempotent(self):
        self.sync()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.sync()
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_content_change_bumps_only_patch(self):
        self.sync()
        (self.upstream / "SKILL.md").write_text("Second version\n")
        self.sync()
        self.assertEqual(self.manifest("example")["version"], "1.0.1")
        self.assertEqual(self.manifest()["version"], "1.0.1")
        self.sync()
        self.assertEqual(self.manifest("example")["version"], "1.0.1")

    def test_added_and_removed_files_bump_version(self):
        self.sync()
        asset = self.upstream / "asset.txt"
        asset.write_text("asset")
        self.sync()
        self.assertEqual(self.manifest("example")["version"], "1.0.1")
        asset.unlink()
        self.sync()
        self.assertEqual(self.manifest("example")["version"], "1.0.2")

    def test_line_endings_do_not_bump_version(self):
        self.sync()
        file = self.upstream / "SKILL.md"
        file.write_bytes(file.read_bytes().replace(b"\n", b"\r\n"))
        self.sync()
        self.assertEqual(self.manifest("example")["version"], "1.0.0")
        self.assertEqual(self.manifest()["version"], "1.0.0")

    def test_binary_changes_are_not_normalized(self):
        file = self.upstream / "asset.bin"
        file.write_bytes(b"\x00line\r\n")
        self.sync()
        file.write_bytes(b"\x00line\n")
        self.sync()
        self.assertEqual(self.manifest("example")["version"], "1.0.1")

    def test_existing_version_is_preserved_and_incremented(self):
        self.sync()
        file = self.root / "plugins" / "example" / "plugin.json"
        data = json.loads(file.read_text())
        data["version"] = "2.3.9"
        file.write_text(json.dumps(data))
        self.sync()
        self.assertEqual(self.manifest("example")["version"], "2.3.9")
        self.found[0]["description"] = "New description"
        self.sync()
        self.assertEqual(self.manifest("example")["version"], "2.3.10")

    def test_invalid_version_fails_before_published_skills_are_replaced(self):
        self.sync()
        original = (self.root / "skills" / "demo" / "SKILL.md").read_bytes()
        file = self.root / "plugins" / "example" / "plugin.json"
        data = json.loads(file.read_text())
        data["version"] = "invalid"
        file.write_text(json.dumps(data))
        (self.upstream / "SKILL.md").write_text("Changed")
        with self.assertRaises(ValueError):
            self.sync()
        self.assertEqual((self.root / "skills" / "demo" / "SKILL.md").read_bytes(), original)

    def test_discovery_excludes_the_mirror_itself(self):
        repos = [{"nameWithOwner": "nogikun/skills"}, {"nameWithOwner": "nogikun/example"}]
        tree = {"tree": [{"type": "blob", "path": "skills/demo/SKILL.md"}]}
        with patch.object(sync, "sh", side_effect=[json.dumps(repos), json.dumps(tree)]) as sh:
            found = sync.discover()
        self.assertEqual([f["repo"] for f in found], ["nogikun/example"])
        self.assertEqual(sh.call_count, 2)

    def test_discovery_skips_empty_repositories(self):
        repos = [{"nameWithOwner": "nogikun/empty", "isEmpty": True}]
        with patch.object(sync, "sh", return_value=json.dumps(repos)) as sh:
            self.assertEqual(sync.discover(), [])
        self.assertEqual(sh.call_count, 1)

    def test_discovery_does_not_silently_drop_repositories_on_api_failure(self):
        repos = [{"nameWithOwner": "nogikun/example", "isEmpty": False}]
        with patch.object(sync, "sh", side_effect=[json.dumps(repos), subprocess.CalledProcessError(1, "gh")]):
            with self.assertRaises(subprocess.CalledProcessError):
                sync.discover()


if __name__ == "__main__":
    unittest.main()

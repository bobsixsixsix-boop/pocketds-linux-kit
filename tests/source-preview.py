#!/usr/bin/env python3
"""Exercise source-only exports against disposable Git repositories."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("source_preview_test", ROOT / "scripts/export-source-preview.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@unittest.skipUnless(shutil.which("git"), "Git is required")
class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="source-preview-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.output = self.base / "preview"
        self.git("init", "-q")
        self.git("config", "user.name", "Source Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.write("README.md", "source preview fixture\n")
        self.write("scripts/run.sh", "#!/bin/sh\nexit 0\n", 0o755)
        self.write(".gitignore", "ignored/\n")
        self.commit()

    def git(self, *args):
        env = dict(os.environ, GIT_AUTHOR_DATE="2000-01-01T00:00:00+0000",
                   GIT_COMMITTER_DATE="2000-01-01T00:00:00+0000")
        return subprocess.check_output(["git", "-C", str(self.repo), *args],
                                       env=env, stderr=subprocess.PIPE)

    def write(self, name, content, mode=0o644):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        path.chmod(mode)
        return path

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture source")

    def reject(self, pattern, output=None):
        target = output or self.output
        with self.assertRaisesRegex(MODULE.ExportError, pattern):
            MODULE.export(self.repo, target)
        self.assertFalse(target.exists())

    def test_clean_tree_archive_modes_and_exact_asset_manifest(self):
        expected_commit = self.git("rev-parse", "HEAD").decode().strip()
        before = self.git("status", "--porcelain")
        result = MODULE.export(self.repo, self.output)
        source = self.output / "source"
        self.assertEqual(before, self.git("status", "--porcelain"))
        self.assertEqual(result["source_commit"], expected_commit)
        self.assertIs(result["draft_only"], True)
        self.assertIs(result["publication_authorized"], False)
        self.assertEqual(json.loads((source / "components/assets/ASSETS.json").read_text()),
                         {"schema": 1, "profile": "asset-free", "assets": []})
        self.assertEqual((source / "README.md").read_bytes(), (self.repo / "README.md").read_bytes())
        self.assertEqual(stat.S_IMODE((source / "scripts/run.sh").stat().st_mode), 0o755)
        self.assertFalse((source / ".git").exists())
        for record in result["files"]:
            content = (source / record["path"]).read_bytes()
            self.assertEqual(record["sha256"], hashlib.sha256(content).hexdigest())
        with tarfile.open(self.output / "source.tar.gz") as archive:
            for member in archive.getmembers():
                self.assertTrue(member.isfile())
                self.assertEqual((member.uid, member.gid, member.uname, member.gname), (0, 0, "", ""))
                self.assertEqual(member.mtime, 946684800)
                relative = member.name.removeprefix("source/")
                self.assertEqual(archive.extractfile(member).read(), (source / relative).read_bytes())
                self.assertEqual(member.mode, stat.S_IMODE((source / relative).stat().st_mode))
        archive_bytes = (self.output / "source.tar.gz").read_bytes()
        self.assertEqual(archive_bytes[4:8], b"\0" * 4)
        self.assertEqual(result["archive"]["sha256"], hashlib.sha256(archive_bytes).hexdigest())
        for line in (self.output / "SHA256SUMS").read_text().splitlines():
            expected, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((self.output / name).read_bytes()).hexdigest(), expected)

    def test_repeat_export_is_byte_identical(self):
        MODULE.export(self.repo, self.output)
        second = self.base / "preview-again"
        MODULE.export(self.repo, second)
        first_files = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob("*") if p.is_file()}
        second_files = {p.relative_to(second): p.read_bytes() for p in second.rglob("*") if p.is_file()}
        self.assertEqual(first_files, second_files)

    def test_old_secret_history_ignored_cache_and_excluded_paths_are_absent(self):
        secret = "vb-" + "0123456789abcdef"
        old = self.write("old-private.txt", secret)
        self.commit()
        old.unlink()
        self.write("assets/wallpapers/private.png", secret)
        self.write("work/private-state.md", secret)
        self.write(".github/workflows/import-reviewed-source.yml", secret)
        self.write(".github/source-import-complete.json", secret)
        self.write("docs/release/source-preview-manifest.json", secret)
        self.write("components/keyboard/private-personal-preset.json", secret)
        self.write("components/keyboard/asr-api/config.json", secret)
        self.write("components/keyboard/asr-api-config.json", secret)
        self.write("components/game-runtime/pocketds-gamescope-observer", b"\x7fELF" + secret.encode())
        self.write("components/assets/ASSETS.json", '{"schema":0,"private":"' + secret + '"}')
        self.write("components/keyboard/licenses/C2PA.txt", "source receipt retained\n")
        self.commit()
        self.write("ignored/cache.txt", secret)
        result = MODULE.export(self.repo, self.output)
        paths = {record["path"] for record in result["files"]}
        self.assertIn("components/keyboard/licenses/C2PA.txt", paths)
        self.assertNotIn("old-private.txt", paths)
        self.assertFalse(any(path.startswith(("assets/", "work/", "ignored/", ".git/", ".github/")) for path in paths))
        for path in MODULE.EXCLUDED_FILES:
            self.assertNotIn(path, paths)
        for path in self.output.rglob("*"):
            if path.is_file() and path.suffix != ".gz":
                self.assertNotIn(secret.encode(), path.read_bytes())

    def test_dirty_tracked_staged_and_untracked_trees_are_refused(self):
        for kind in ("tracked", "staged", "untracked"):
            with self.subTest(kind=kind):
                if kind == "untracked":
                    target = self.write("extra.txt", "new")
                else:
                    target = self.write("README.md", "changed")
                    if kind == "staged":
                        self.git("add", "README.md")
                self.reject("must be clean")
                self.git("reset", "--hard", "-q", "HEAD")
                if kind == "untracked":
                    target.unlink()

    def test_arbitrary_provider_key_in_unexpected_json_is_refused_without_value(self):
        secret = "synthetic-unknown-provider-credential"
        self.write("unexpected-settings.json", json.dumps({"api_key": secret}))
        self.commit()
        with self.assertRaises(MODULE.ExportError) as error:
            MODULE.export(self.repo, self.output)
        self.assertIn("configured-api-key", str(error.exception))
        self.assertNotIn(secret, str(error.exception))
        self.assertFalse(self.output.exists())

    def test_synthetic_secret_patterns_are_refused_without_echoing_values(self):
        # Construct markers so this test's own source can pass the export scan.
        examples = {
            "asr-access-code": "vb-" + "abcdef0123456789",
            "private-key": "-----BEG" + "IN " + "RSA PRIVATE KEY-----",
            "github-token": "ghp_" + "A" * 36,
            "api-token": "sk-" + "B" * 40,
            "slack-token": "xoxb-" + "1234567890-" * 3,
            "aws-access-key": "AKIA" + "C" * 16,
            "google-api-key": "AIza" + "D" * 35,
            "macos-home": "/" + "Users/" + "private-fixture/Documents/project",
        }
        for category, value in examples.items():
            with self.subTest(category=category):
                self.write("suspect.txt", value)
                self.commit()
                with self.assertRaises(MODULE.ExportError) as caught:
                    MODULE.export(self.repo, self.output)
                self.assertIn(category, str(caught.exception))
                self.assertIn("suspect.txt", str(caught.exception))
                self.assertNotIn(value, str(caught.exception))
                self.assertFalse(self.output.exists())

    def test_placeholder_home_is_allowed(self):
        self.write("example.md", "/" + "Users/<user>/Documents/project\n")
        self.commit()
        MODULE.export(self.repo, self.output)

    def test_symlink_is_refused_without_dereferencing(self):
        external = self.base / "external.txt"
        external.write_text("not repository content")
        (self.repo / "linked.txt").symlink_to(external)
        self.commit()
        self.reject("unsupported tracked mode")
        self.assertEqual(external.read_text(), "not repository content")

    def test_gitlink_is_refused(self):
        child = self.repo / "nested"
        child.mkdir()
        subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(self.repo), str(child)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child_commit = subprocess.check_output(["git", "-C", str(child), "rev-parse", "HEAD"]).decode().strip()
        self.git("update-index", "--add", "--cacheinfo", "160000," + child_commit + ",nested")
        self.git("commit", "-qm", "fixture gitlink")
        self.reject("unsupported tracked mode")

    def test_existing_output_and_symlink_are_never_overwritten(self):
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("retained")
        with self.assertRaisesRegex(MODULE.ExportError, "already exist"):
            MODULE.export(self.repo, self.output)
        self.assertEqual(marker.read_text(), "retained")
        link = self.base / "output-link"
        link.symlink_to(self.output)
        with self.assertRaisesRegex(MODULE.ExportError, "already exist"):
            MODULE.export(self.repo, link)
        self.assertTrue(link.is_symlink())

    def test_output_inside_source_repo_is_refused(self):
        self.reject("outside", self.repo / "preview")
        # A parent link pointing inside is checked after canonical resolution.
        alias = self.base / "repo-alias"
        alias.symlink_to(self.repo, target_is_directory=True)
        self.reject("outside", alias / "preview")

    def test_exporter_and_its_fixture_can_export_themselves(self):
        self.write("scripts/export-source-preview.py", Path(MODULE.__file__).read_bytes())
        self.write("tests/source-preview.py", Path(__file__).read_bytes())
        self.commit()
        MODULE.export(self.repo, self.output)

    def test_cli_failure_has_no_partial_output(self):
        self.write("untracked.txt", "new")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = MODULE.main(["--repo", str(self.repo), "--output", str(self.output)])
        self.assertEqual(result, 1)
        self.assertIn("must be clean", output.getvalue())
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()

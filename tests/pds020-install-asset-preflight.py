#!/usr/bin/env python3
"""Tests for explicit personal and asset-free installer profiles."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pds020-install-asset-preflight.py"
INSTALLER = ROOT / "scripts/install.sh"
SPEC = importlib.util.spec_from_file_location("pds020_install_asset_preflight", SOURCE)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preflight
SPEC.loader.exec_module(preflight)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self, root: Path, *, personal: bool) -> None:
        self.root = root
        self.assets = root / "assets"
        self.metadata = root / "components" / "assets"
        self.assets.mkdir(parents=True)
        self.metadata.mkdir(parents=True)
        if personal:
            wallpaper = b"synthetic personal wallpaper\n"
            path = self.assets / "wallpaper.png"
            path.write_bytes(wallpaper)
            provenance = self.metadata / "provenance"
            provenance.mkdir()
            receipt = {
                "schema": 1,
                "tool": {"fixture": True},
                "trust": {"fixture": True},
                "rights": preflight.PERSONAL_RIGHTS_BOUNDARY,
                "assets": [
                    {
                        "path": "assets/wallpaper.png",
                        "size": len(wallpaper),
                        "sha256": digest(wallpaper),
                    }
                ],
            }
            (provenance / "c2pa-receipt.json").write_text(
                json.dumps(receipt), encoding="utf-8"
            )
        else:
            (self.metadata / "ASSETS.json").write_text(
                json.dumps(preflight.ASSET_FREE_MANIFEST), encoding="utf-8"
            )


class ProfileTests(unittest.TestCase):
    def test_recorded_repository_profile_preserves_its_rights_boundary(self):
        asset_free = (ROOT / preflight.ASSET_MANIFEST).is_file()
        report = preflight.preflight(ROOT, "asset-free" if asset_free else "personal-assets")
        self.assertTrue(report["asset_profile_ready"])
        self.assertEqual(report["install_wallpapers"], not asset_free)
        self.assertEqual(report["public_asset_layer_ready"], asset_free)
        self.assertFalse(report["release_ready"])
        self.assertEqual(report["asset_count"], 0 if asset_free else 2)

    def test_exact_personal_fixture_passes(self):
        with tempfile.TemporaryDirectory(prefix="pds020-assets-") as name:
            fixture = Fixture(Path(name), personal=True)
            report = preflight.preflight(fixture.root, "personal-assets")
            self.assertTrue(report["install_wallpapers"])
            self.assertEqual(report["asset_count"], 1)

    def test_personal_tamper_missing_extra_or_symlink_fails(self):
        with tempfile.TemporaryDirectory(prefix="pds020-assets-") as name:
            fixture = Fixture(Path(name), personal=True)
            wallpaper = fixture.assets / "wallpaper.png"
            wallpaper.write_bytes(b"tampered\n")
            with self.assertRaisesRegex(preflight.PreflightError, "identity"):
                preflight.preflight(fixture.root, "personal-assets")
            wallpaper.write_bytes(b"synthetic personal wallpaper\n")
            extra = fixture.assets / "extra.png"
            extra.write_bytes(b"unlisted\n")
            with self.assertRaisesRegex(preflight.PreflightError, "exactly cover"):
                preflight.preflight(fixture.root, "personal-assets")
            extra.unlink()
            target = fixture.root / "outside.png"
            target.write_bytes(wallpaper.read_bytes())
            wallpaper.unlink()
            os.symlink(target, wallpaper)
            with self.assertRaisesRegex(preflight.PreflightError, "unsafe"):
                preflight.preflight(fixture.root, "personal-assets")

    def test_asset_free_fixture_passes_but_never_claims_full_release(self):
        with tempfile.TemporaryDirectory(prefix="pds020-assets-") as name:
            fixture = Fixture(Path(name), personal=False)
            report = preflight.preflight(fixture.root, "asset-free")
            self.assertFalse(report["install_wallpapers"])
            self.assertTrue(report["public_asset_layer_ready"])
            self.assertFalse(report["release_ready"])
            self.assertEqual(report["asset_count"], 0)

    def test_asset_free_git_clone_without_empty_directory_passes_read_only(self):
        with tempfile.TemporaryDirectory(prefix="pds020-assets-git-") as name:
            fixture = Fixture(Path(name) / "source", personal=False)
            environment = {
                **os.environ,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
            }

            def git(*arguments, cwd=fixture.root):
                return subprocess.run(
                    ["git", *arguments], cwd=cwd, env=environment,
                    capture_output=True, text=True, check=True, timeout=10,
                )

            git("init", "-q")
            git("add", "components")
            git("-c", "user.name=Asset fixture", "-c", "user.email=fixture@example.invalid",
                "-c", "commit.gpgsign=false", "commit", "-qm", "Synthetic asset-free tree")
            checkout = Path(name) / "checkout"
            git("clone", "-q", "--no-local", str(fixture.root), str(checkout))
            self.assertFalse((checkout / "assets").exists())
            report = preflight.preflight(checkout, "asset-free")
            self.assertTrue(report["asset_profile_ready"])
            self.assertFalse(report["release_ready"])
            self.assertFalse((checkout / "assets").exists())
            self.assertEqual(git("status", "--porcelain", cwd=checkout).stdout, "")
            with self.assertRaisesRegex(preflight.PreflightError, "missing or unsafe"):
                preflight.preflight(checkout, "personal-assets")

    def test_missing_directory_requires_exact_asset_free_manifest(self):
        with tempfile.TemporaryDirectory(prefix="pds020-assets-missing-") as name:
            fixture = Fixture(Path(name), personal=False)
            fixture.assets.rmdir()
            manifest = fixture.metadata / "ASSETS.json"
            for value in (
                {"schema": 1, "profile": "personal-assets", "assets": []},
                {"schema": True, "profile": "asset-free", "assets": []},
                {**preflight.ASSET_FREE_MANIFEST, "allow_missing": True},
                {"schema": 1, "profile": "asset-free", "assets": ["assets/missing"]},
            ):
                with self.subTest(manifest=value):
                    manifest.write_text(json.dumps(value), encoding="utf-8")
                    with self.assertRaisesRegex(preflight.PreflightError, "not exactly empty"):
                        preflight.preflight(fixture.root, "asset-free")
            manifest.write_text('{"schema":1,"schema":1,"profile":"asset-free","assets":[]}',
                                encoding="utf-8")
            with self.assertRaisesRegex(preflight.PreflightError, "duplicate key"):
                preflight.preflight(fixture.root, "asset-free")
            manifest.unlink()
            with self.assertRaises(preflight.PreflightError):
                preflight.preflight(fixture.root, "asset-free")
            self.assertFalse(fixture.assets.exists())

    def test_asset_free_rejects_non_directory_and_dangling_or_directory_links(self):
        with tempfile.TemporaryDirectory(prefix="pds020-assets-unsafe-") as name:
            fixture = Fixture(Path(name), personal=False)
            fixture.assets.rmdir()
            fixture.assets.write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(preflight.PreflightError, "unsafe"):
                preflight.preflight(fixture.root, "asset-free")
            fixture.assets.unlink()
            target = fixture.root / "other"
            for exists in (False, True):
                if exists:
                    target.mkdir()
                fixture.assets.symlink_to(target)
                with self.assertRaisesRegex(preflight.PreflightError, "unsafe"):
                    preflight.preflight(fixture.root, "asset-free")
                fixture.assets.unlink()

    def test_asset_free_hidden_file_or_manifest_drift_fails(self):
        with tempfile.TemporaryDirectory(prefix="pds020-assets-") as name:
            fixture = Fixture(Path(name), personal=False)
            hidden = fixture.assets / ".hidden"
            hidden.write_bytes(b"not empty\n")
            with self.assertRaisesRegex(preflight.PreflightError, "not exactly empty"):
                preflight.preflight(fixture.root, "asset-free")
            hidden.unlink()
            manifest_path = fixture.metadata / "ASSETS.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["allow_unlisted"] = True
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(preflight.PreflightError, "not exactly empty"):
                preflight.preflight(fixture.root, "asset-free")
            manifest_path.write_text(
                '{"schema":1,"profile":"personal-assets",'
                '"profile":"asset-free","assets":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(preflight.PreflightError, "duplicate key"):
                preflight.preflight(fixture.root, "asset-free")

    def test_profile_metadata_requires_strict_json_and_exact_types(self):
        with tempfile.TemporaryDirectory(prefix="pds020-assets-") as name:
            fixture = Fixture(Path(name), personal=False)
            manifest = fixture.metadata / "ASSETS.json"
            value = json.loads(manifest.read_text(encoding="utf-8"))
            manifest.write_bytes(json.dumps(value).encode("utf-16"))
            with self.assertRaisesRegex(preflight.PreflightError, "UTF-8 JSON"):
                preflight.preflight(fixture.root, "asset-free")

        with tempfile.TemporaryDirectory(prefix="pds020-assets-") as name:
            fixture = Fixture(Path(name), personal=False)
            manifest = fixture.metadata / "ASSETS.json"
            manifest.write_text(
                '{"schema":1,"ignored":NaN,"profile":"asset-free",'
                '"assets":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(preflight.PreflightError, "non-finite"):
                preflight.preflight(fixture.root, "asset-free")

        for personal, error in ((False, "not exactly empty"), (True, "rights")):
            with self.subTest(personal=personal):
                with tempfile.TemporaryDirectory(prefix="pds020-assets-") as name:
                    fixture = Fixture(Path(name), personal=personal)
                    if personal:
                        receipt = fixture.metadata / "provenance/c2pa-receipt.json"
                        value = json.loads(receipt.read_text(encoding="utf-8"))
                        value["rights"] = {
                            key: int(item) for key, item in value["rights"].items()
                        }
                        receipt.write_text(json.dumps(value), encoding="utf-8")
                        profile = "personal-assets"
                    else:
                        manifest = fixture.metadata / "ASSETS.json"
                        value = json.loads(manifest.read_text(encoding="utf-8"))
                        value["schema"] = True
                        manifest.write_text(json.dumps(value), encoding="utf-8")
                        profile = "asset-free"
                    with self.assertRaisesRegex(preflight.PreflightError, error):
                        preflight.preflight(fixture.root, profile)

    def test_profiles_cannot_be_cross_selected(self):
        with tempfile.TemporaryDirectory(prefix="pds020-assets-") as name:
            personal = Fixture(Path(name) / "personal", personal=True)
            empty = Fixture(Path(name) / "empty", personal=False)
            with self.assertRaises(preflight.PreflightError):
                preflight.preflight(personal.root, "asset-free")
            with self.assertRaises(preflight.PreflightError):
                preflight.preflight(empty.root, "personal-assets")
            with self.assertRaisesRegex(preflight.PreflightError, "unsupported"):
                preflight.preflight(empty.root, "auto")
            with mock.patch.object(
                preflight,
                "repository_assets",
                side_effect=[set(), {"assets/appeared-during-check"}],
            ):
                with self.assertRaisesRegex(preflight.PreflightError, "changed"):
                    preflight.preflight(empty.root, "asset-free")

    def test_cli_rejects_unknown_profile_before_repository_access(self):
        result = subprocess.run(
            [sys.executable, str(SOURCE), "--profile", "auto"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("invalid choice", result.stderr)

    def test_preflight_has_no_delete_install_network_or_write_path(self):
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "urllib.request",
            "requests",
            "socket",
            "curl ",
            "wget ",
            "sudo ",
            "subprocess",
            "write_bytes",
            "write_text",
            "os.remove",
            "os.unlink",
            "shutil",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('"read_only": True', source)
        self.assertIn('"release_ready": False', source)
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("asset_profile=personal-assets", installer)
        self.assertIn("--personal-assets|--asset-free", installer)
        self.assertIn('pds020-install-asset-preflight.py" \\', installer)
        self.assertIn("personal wallpapers are not installed or removed", installer)
        self.assertLess(
            installer.index("pds020-install-asset-preflight.py"),
            installer.index("stamp=$(date"),
        )
        for arguments, message in (
            (["--apps", "--all"], "Install scope may be selected only once"),
            (
                ["--personal-assets", "--asset-free"],
                "Asset profile may be selected only once",
            ),
        ):
            result = subprocess.run(
                ["bash", str(INSTALLER), *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

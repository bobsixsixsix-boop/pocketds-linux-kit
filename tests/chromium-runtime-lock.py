#!/usr/bin/env python3
"""Mock and static tests for the fail-closed Chromium runtime lock."""

from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scripts/pocketds-chromium-runtime-verify.py"
LOCK_PATH = ROOT / "components/chromium/runtime-lock.json"
spec = importlib.util.spec_from_file_location("pocketds_chromium_verify", SOURCE)
assert spec and spec.loader
verify = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verify
spec.loader.exec_module(verify)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RuntimeFixture:
    def __init__(self, base: Path) -> None:
        self.root = base / "mock-chromium-runtime"
        self.root.mkdir()
        self.wrapper = base / "wrapper"
        self.pkginfo = (
            b"pkgname = chromium\n"
            b"pkgver = 1.2.3-1\n"
            b"arch = aarch64\n"
            b"packager = Arch Linux ARM Build System <builder@example.invalid>\n"
            b"builddate = 1234567890\n"
        )
        self.buildinfo = (
            b"pkgname = chromium\n"
            b"pkgver = 1.2.3-1\n"
            b"pkgarch = aarch64\n"
            b"packager = Arch Linux ARM Build System <builder@example.invalid>\n"
            b"builddate = 1234567890\n"
            + f"pkgbuild_sha256sum = {'b' * 64}\n".encode()
        )
        self.main = b"synthetic chromium executable\n"
        (self.root / ".PKGINFO").write_bytes(self.pkginfo)
        (self.root / ".BUILDINFO").write_bytes(self.buildinfo)
        main_path = self.root / "usr/lib/chromium/chromium"
        main_path.parent.mkdir(parents=True)
        main_path.write_bytes(self.main)

        compat = self.root / "compat/usr/lib"
        compat.mkdir(parents=True)
        self.compat_data = b"synthetic compatibility library\n"
        (compat / "libmock.so.1.0").write_bytes(self.compat_data)
        (compat / "libmock.so.1").symlink_to("libmock.so.1.0")

        mtree_text = (
            "#mtree\n"
            f"./.PKGINFO size={len(self.pkginfo)} sha256digest={digest(self.pkginfo)}\n"
            f"./.BUILDINFO size={len(self.buildinfo)} sha256digest={digest(self.buildinfo)}\n"
            "./usr/lib/chromium/chromium "
            f"size={len(self.main)} sha256digest={digest(self.main)}\n"
        ).encode()
        (self.root / ".MTREE").write_bytes(gzip.compress(mtree_text, mtime=0))
        wrapper_text = (
            "#!/usr/bin/bash\n"
            f"readonly chromium_root={self.root}\n"
            'export LD_LIBRARY_PATH="${chromium_root}/compat/usr/lib'
            '${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"\n'
            "export CHROME_DESKTOP=chromium-browser.desktop\n"
        )
        self.wrapper.write_text(wrapper_text, encoding="utf-8")

        source = {
            "version": "1-1",
            "package_page": "https://archlinuxarm.org/packages/aarch64/mock",
            "source_files": "https://archlinuxarm.org/packages/aarch64/mock/files",
            "download": "https://mirror.archlinuxarm.org/aarch64/core/mock.pkg.tar.xz",
            "signature": "https://mirror.archlinuxarm.org/aarch64/core/mock.pkg.tar.xz.sig",
            "archive_sha256": "a" * 64,
            "signature_verified": True,
            "status": verify.VERIFIED_STATUS,
            "gap": None,
        }
        package_source = copy.deepcopy(source)
        package_source.update(
            {
                "name": "chromium",
                "version": "1.2.3-1",
                "arch": "aarch64",
                "builddate": 1234567890,
                "packager": "Arch Linux ARM Build System <builder@example.invalid>",
                "root_basename": self.root.name,
                "main_binary": "usr/lib/chromium/chromium",
                "signing_policy": "https://archlinuxarm.org/about/package-signing",
                "signing_key_fingerprint": "A" * 40,
                "pkgbuild_sha256": "b" * 64,
            }
        )
        self.lock = {
            "schema": 1,
            "release_ready": True,
            "package": package_source,
            "observed": {
                "pkginfo": {
                    "path": ".PKGINFO",
                    "size": len(self.pkginfo),
                    "sha256": digest(self.pkginfo),
                },
                "buildinfo": {
                    "path": ".BUILDINFO",
                    "size": len(self.buildinfo),
                    "sha256": digest(self.buildinfo),
                },
                "mtree": {
                    "path": ".MTREE",
                    "size": (self.root / ".MTREE").stat().st_size,
                    "sha256": verify.sha256_file(self.root / ".MTREE"),
                },
                "main_binary": {
                    "path": "usr/lib/chromium/chromium",
                    "size": len(self.main),
                    "sha256": digest(self.main),
                },
            },
            "wrapper": {
                "path": "components/chromium/mock-wrapper",
                "sha256": verify.sha256_file(self.wrapper),
            },
            "compat_sources": {"mocklib": source},
            "compat_files": [
                {
                    "path": "usr/lib/libmock.so.1",
                    "source_package": "mocklib",
                    "runtime_required": True,
                    "type": "symlink",
                    "target": "libmock.so.1.0",
                },
                {
                    "path": "usr/lib/libmock.so.1.0",
                    "source_package": "mocklib",
                    "runtime_required": True,
                    "type": "file",
                    "size": len(self.compat_data),
                    "sha256": digest(self.compat_data),
                },
            ],
        }
        certificate = b"synthetic OpenPGP certificate\n"
        certificate_path = base / "builder.asc"
        certificate_path.write_bytes(certificate)
        signature = b"synthetic detached OpenPGP signature" * 3
        signature_receipt = {
            "signature_size": len(signature),
            "signature_sha256": digest(signature),
            "signature_base64": base64.b64encode(signature).decode("ascii"),
            "signer_fingerprint": "A" * 40,
            "signature_valid": True,
        }
        chromium_metadata = {
            ".PKGINFO": {
                "size": len(self.pkginfo),
                "sha256": digest(self.pkginfo),
            },
            ".BUILDINFO": {
                "size": len(self.buildinfo),
                "sha256": digest(self.buildinfo),
            },
            ".MTREE": {
                "size": (self.root / ".MTREE").stat().st_size,
                "sha256": verify.sha256_file(self.root / ".MTREE"),
            },
        }
        synthetic_metadata = {
            name: {"size": 1, "sha256": digest(name.encode("ascii"))}
            for name in (".PKGINFO", ".BUILDINFO", ".MTREE")
        }

        def receipt_package(
            source_record: dict[str, object],
            metadata: dict[str, dict[str, object]],
            entry_count: int,
        ) -> dict[str, object]:
            return {
                "version": source_record["version"],
                "arch": "aarch64",
                "archive_url": source_record["download"],
                "archive_size": 123,
                "archive_sha256": source_record["archive_sha256"],
                "signature_url": source_record["signature"],
                **signature_receipt,
                "locked_entries_verified": entry_count,
                "metadata": metadata,
            }

        receipt = {
            "schema": 1,
            "verified_at": "2026-08-28T00:00:00Z",
            "signing_policy": package_source["signing_policy"],
            "mirror_policy": "https://archlinuxarm.org/about/mirrors",
            "key": {
                "fingerprint": "A" * 40,
                "certificate_path": certificate_path.name,
                "certificate_sha256": digest(certificate),
                "source_repository": verify.KEYRING_REPOSITORY,
                "source_commit": "c" * 40,
                "source_path": "packager/builder.asc",
            },
            "verifier": {
                "name": "sequoia-sqv",
                "version": "1.0.0",
                "openpgp_version": "1.0.0",
                "backend": "fixture",
                "command_contract": "fixture",
            },
            "packages": {
                "chromium": receipt_package(package_source, chromium_metadata, 4),
                "mocklib": receipt_package(source, synthetic_metadata, 2),
            },
        }
        receipt_path = base / "receipt.json"
        receipt_path.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
        self.receipt_root = base
        self.lock["provenance_receipt"] = {
            "path": receipt_path.name,
            "sha256": verify.sha256_file(receipt_path),
        }


class RuntimeLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="pds020-chromium-")
        self.fixture = RuntimeFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_complete_mock_runtime_is_release_ready(self) -> None:
        report = verify.verify_runtime(
            self.fixture.lock,
            self.fixture.root,
            self.fixture.wrapper,
            self.fixture.receipt_root,
        )
        self.assertEqual(report["integrity"], "pass")
        self.assertEqual(report["provenance"], "complete")
        self.assertTrue(report["release_ready"])
        self.assertEqual(report["errors"], [])

    def test_hash_mismatch_and_unlocked_extra_fail_integrity(self) -> None:
        compat = self.fixture.root / "compat/usr/lib"
        (compat / "libmock.so.1.0").write_bytes(b"changed")
        (compat / "unexpected.so").write_bytes(b"extra")
        report = verify.verify_runtime(
            self.fixture.lock,
            self.fixture.root,
            self.fixture.wrapper,
            self.fixture.receipt_root,
        )
        self.assertEqual(report["integrity"], "fail")
        self.assertIn("compat file mismatch: usr/lib/libmock.so.1.0", report["errors"])
        self.assertIn("unlocked compat entry: usr/lib/unexpected.so", report["errors"])

    def test_wrong_desktop_identity_fails_integration(self) -> None:
        self.fixture.wrapper.write_text(
            self.fixture.wrapper.read_text(encoding="utf-8").replace(
                "CHROME_DESKTOP=chromium-browser.desktop",
                "CHROME_DESKTOP=missing.desktop",
            ),
            encoding="utf-8",
        )
        self.fixture.lock["wrapper"]["sha256"] = verify.sha256_file(
            self.fixture.wrapper
        )
        report = verify.verify_runtime(
            self.fixture.lock,
            self.fixture.root,
            self.fixture.wrapper,
            self.fixture.receipt_root,
        )
        self.assertIn(
            "wrapper does not identify the installed desktop launcher",
            report["errors"],
        )

    def test_matching_files_still_fail_closed_when_provenance_is_incomplete(self) -> None:
        lock = copy.deepcopy(self.fixture.lock)
        source = lock["compat_sources"]["mocklib"]
        source.update(
            {
                "archive_sha256": None,
                "signature_verified": False,
                "status": "observed-unverified",
                "gap": "synthetic package receipt intentionally absent",
            }
        )
        lock["release_ready"] = False
        report = verify.verify_runtime(
            lock,
            self.fixture.root,
            self.fixture.wrapper,
            self.fixture.receipt_root,
        )
        self.assertEqual(report["integrity"], "pass")
        self.assertEqual(report["provenance"], "incomplete")
        self.assertFalse(report["release_ready"])
        self.assertEqual(len(report["provenance_gaps"]), 1)

    def test_verification_claim_without_receipt_is_rejected(self) -> None:
        lock = copy.deepcopy(self.fixture.lock)
        lock["compat_sources"]["mocklib"]["archive_sha256"] = None
        with self.assertRaisesRegex(verify.LockError, "claims verification"):
            verify.validate_lock(lock)

    def test_cli_reserves_exit_three_for_matching_but_incomplete_receipts(self) -> None:
        lock = copy.deepcopy(self.fixture.lock)
        source = lock["compat_sources"]["mocklib"]
        source.update(
            {
                "archive_sha256": None,
                "signature_verified": False,
                "status": "observed-unverified",
                "gap": "synthetic receipt absent",
            }
        )
        lock["release_ready"] = False
        lock_path = Path(self.temporary.name) / "lock.json"
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
        arguments = [
            str(SOURCE),
            "--lock",
            str(lock_path),
            "--root",
            str(self.fixture.root),
            "--wrapper",
            str(self.fixture.wrapper),
            "--receipt-root",
            str(self.fixture.receipt_root),
        ]
        with mock.patch.object(sys, "argv", arguments), mock.patch("builtins.print"):
            self.assertEqual(verify.main(), 3)


class RecordedLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))

    def test_recorded_runtime_identity_and_hashes_are_fixed(self) -> None:
        package = self.lock["package"]
        self.assertEqual(package["version"], "151.0.7922.137-1")
        self.assertEqual(package["arch"], "aarch64")
        self.assertEqual(
            self.lock["observed"]["main_binary"]["sha256"],
            "2f0002bce6c72188c9426f083e6e802c690eacbbc4fee03b2c08343833fcd1b0",
        )
        self.assertEqual(
            self.lock["observed"]["mtree"]["sha256"],
            "03a8c2507dce08dc70c400b68ae58153cbc7c351b216bf51a3b120d68915a0f8",
        )
        self.assertEqual(
            self.lock["observed"]["icon_256"],
            {
                "path": "usr/share/icons/hicolor/256x256/apps/chromium.png",
                "size": 9614,
                "sha256": "e14120fdefb8eb455f44eac572f34bda75c32c9404e5c3745d44793dae217331",
            },
        )
        self.assertEqual(
            self.lock["wrapper"]["sha256"],
            "273ea337eedbf3af2da56c4283811197a9322ccf59df16615bdd6ea77324b1c0",
        )
        self.assertEqual(
            verify.sha256_file(ROOT / self.lock["wrapper"]["path"]),
            self.lock["wrapper"]["sha256"],
        )

    def test_default_runtime_check_targets_the_live_wrapper(self) -> None:
        self.assertEqual(
            verify.DEFAULT_WRAPPER,
            Path("/usr/local/bin/pocketds-chromium-v4l2"),
        )

    def test_every_compat_entry_has_a_signed_archive_receipt(self) -> None:
        complete, gaps = verify.validate_lock(self.lock)
        self.assertTrue(complete)
        self.assertTrue(self.lock["release_ready"])
        self.assertEqual(gaps, [])
        self.assertEqual(
            verify.validate_provenance_receipt(self.lock, ROOT),
            {"packages": 5, "signatures": 5},
        )
        files = self.lock["compat_files"]
        self.assertEqual(len(files), 54)
        self.assertEqual(len({item["path"] for item in files}), 54)
        self.assertEqual(sum(item["runtime_required"] for item in files), 10)
        for item in files:
            source = self.lock["compat_sources"][item["source_package"]]
            self.assertEqual(source["status"], verify.VERIFIED_STATUS)
            self.assertRegex(source["archive_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(source["signature_verified"])

    def test_recorded_receipt_hash_tamper_fails_closed(self) -> None:
        lock = copy.deepcopy(self.lock)
        lock["provenance_receipt"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(verify.LockError, "receipt SHA-256 mismatch"):
            verify.validate_provenance_receipt(lock, ROOT)

    def test_tool_has_no_network_download_install_or_launch_path(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        for token in (
            "subprocess",
            "urlopen",
            "requests",
            "socket",
            "curl ",
            "wget ",
            "dnf ",
            "pacman ",
            "Popen",
        ):
            self.assertNotIn(token, source)
        self.assertNotIn(
            SOURCE.name, (ROOT / "scripts/test.sh").read_text(encoding="utf-8")
        )
        self.assertNotIn(
            SOURCE.name, (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        )
        self.assertNotIn(
            SOURCE.name, (ROOT / "scripts/import-live.sh").read_text(encoding="utf-8")
        )


if __name__ == "__main__":
    unittest.main()

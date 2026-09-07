#!/usr/bin/env python3
"""Offline tests for the Chromium signed-archive provenance verifier."""

from __future__ import annotations

import base64
from io import BytesIO
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pocketds-chromium-provenance-verify.py"
SPEC = importlib.util.spec_from_file_location("chromium_provenance_verify", SOURCE)
assert SPEC and SPEC.loader
provenance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = provenance
SPEC.loader.exec_module(provenance)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ArchiveFixture:
    def __init__(self, base: Path, *, duplicate: bool = False) -> None:
        self.archive = base / "mock-1.0-1-aarch64.pkg.tar.xz"
        self.pkginfo = b"pkgname = mock\npkgver = 1.0-1\narch = aarch64\n"
        self.buildinfo = b"pkgname = mock\npkgver = 1.0-1\npkgarch = aarch64\n"
        self.mtree = b"synthetic mtree"
        self.library = b"synthetic library"
        with tarfile.open(self.archive, "w:xz") as archive:
            self._regular(archive, ".PKGINFO", self.pkginfo)
            self._regular(archive, ".BUILDINFO", self.buildinfo)
            self._regular(archive, ".MTREE", self.mtree)
            self._regular(archive, "usr/lib/libmock.so.1.0", self.library)
            link = tarfile.TarInfo("usr/lib/libmock.so.1")
            link.type = tarfile.SYMTYPE
            link.linkname = "libmock.so.1.0"
            archive.addfile(link)
            if duplicate:
                self._regular(archive, ".PKGINFO", self.pkginfo)
        signature = b"synthetic detached signature" * 4
        self.receipt = {
            "version": "1.0-1",
            "arch": "aarch64",
            "archive_url": "https://ca.us.mirror.archlinuxarm.org/aarch64/core/mock-1.0-1-aarch64.pkg.tar.xz",
            "archive_size": self.archive.stat().st_size,
            "archive_sha256": provenance.sha256_file(self.archive),
            "signature_url": "https://ca.us.mirror.archlinuxarm.org/aarch64/core/mock-1.0-1-aarch64.pkg.tar.xz.sig",
            "signature_size": len(signature),
            "signature_sha256": digest(signature),
            "signature_base64": base64.b64encode(signature).decode("ascii"),
            "signer_fingerprint": "A" * 40,
            "signature_valid": True,
            "locked_entries_verified": 2,
            "metadata": {
                ".PKGINFO": {"size": len(self.pkginfo), "sha256": digest(self.pkginfo)},
                ".BUILDINFO": {
                    "size": len(self.buildinfo),
                    "sha256": digest(self.buildinfo),
                },
                ".MTREE": {"size": len(self.mtree), "sha256": digest(self.mtree)},
            },
        }
        self.locked = [
            {
                "path": "usr/lib/libmock.so.1.0",
                "type": "file",
                "size": len(self.library),
                "sha256": digest(self.library),
            },
            {
                "path": "usr/lib/libmock.so.1",
                "type": "symlink",
                "target": "libmock.so.1.0",
            },
        ]

    @staticmethod
    def _regular(archive: tarfile.TarFile, name: str, data: bytes) -> None:
        member = tarfile.TarInfo(name)
        member.size = len(data)
        archive.addfile(member, BytesIO(data))


class ArchiveTests(unittest.TestCase):
    def test_archive_metadata_files_and_locked_entries_match(self):
        with tempfile.TemporaryDirectory(prefix="pds020-provenance-") as name:
            fixture = ArchiveFixture(Path(name))
            self.assertEqual(
                provenance.verify_package_archive(
                    "mock", fixture.archive, fixture.receipt, fixture.locked
                ),
                {"metadata": 3, "locked_entries": 2},
            )

    def test_locked_payload_hash_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-provenance-") as name:
            fixture = ArchiveFixture(Path(name))
            fixture.locked[0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(
                provenance.ProvenanceError, "member SHA-256 mismatch"
            ):
                provenance.verify_package_archive(
                    "mock", fixture.archive, fixture.receipt, fixture.locked
                )

    def test_duplicate_archive_member_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-provenance-") as name:
            fixture = ArchiveFixture(Path(name), duplicate=True)
            with self.assertRaisesRegex(
                provenance.ProvenanceError, "duplicate member"
            ):
                provenance.verify_package_archive(
                    "mock", fixture.archive, fixture.receipt, fixture.locked
                )

    def test_percent_encoded_epoch_filename_is_bounded(self):
        self.assertEqual(
            provenance.archive_filename(
                "https://ca.us.mirror.archlinuxarm.org/aarch64/core/minizip-1%3A1.3.2-3-aarch64.pkg.tar.xz"
            ),
            "minizip-1:1.3.2-3-aarch64.pkg.tar.xz",
        )
        with self.assertRaises(provenance.ProvenanceError):
            provenance.archive_filename("https://example.invalid/not-a-package")


class SignatureAndStaticTests(unittest.TestCase):
    def test_signature_verifier_requires_exact_fingerprint_and_success_text(self):
        signature = b"synthetic detached signature" * 4
        receipt = {
            "signature_base64": base64.b64encode(signature).decode("ascii"),
            "signer_fingerprint": "A" * 40,
        }
        completed = subprocess.CompletedProcess(
            [],
            0,
            stdout=f"{'A' * 40}\n1 of 1 signatures are valid (threshold is: 1).\n",
            stderr="",
        )
        with mock.patch.object(provenance.subprocess, "run", return_value=completed):
            provenance.verify_signature(
                Path("/fixture/sqv"),
                Path("/fixture/builder.asc"),
                Path("/fixture/archive.pkg.tar.xz"),
                receipt,
            )

    def test_tool_has_no_network_download_install_or_shell_path(self):
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "urlopen",
            "requests",
            "socket",
            "curl ",
            "wget ",
            "brew ",
            "dnf ",
            "pacman ",
            "shell=True",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('"network": False', source)
        self.assertIn('"read_only": True', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

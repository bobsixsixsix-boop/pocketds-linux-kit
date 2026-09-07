#!/usr/bin/env python3
"""Offline tests for the immutable pds2 dependency RPM archive verifier."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pds020-userspace-rpm-archive-verify.py"
LOCK = ROOT / "packaging/pocketds-userspace/dependency-archives-lock.pds2.json"
ROOTFS_LOCK = (
    ROOT / "packaging/pocketds-userspace/rootfs-package-archives-lock.pds2.json"
)
SPEC = importlib.util.spec_from_file_location("pds020_archive_verify_test", SOURCE)
assert SPEC and SPEC.loader
archive = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = archive
SPEC.loader.exec_module(archive)


class ArchiveVerifyTests(unittest.TestCase):
    def test_real_lock_is_exact_unsigned_pds2_archive_contract(self) -> None:
        lock = archive.load_lock(LOCK)
        self.assertEqual(lock["package_count"], 136)
        self.assertEqual(lock["fedora_package_count"], 135)
        self.assertEqual(lock["fedora_release"], 44)
        self.assertFalse(lock["signature_verified"])
        project = [item for item in lock["packages"] if item["origin"] == "project-pre-sign"]
        self.assertEqual(len(project), 1)
        self.assertFalse(project[0]["signature_verified"])

    def test_only_recorded_dependency_or_rootfs_boundaries_are_supported(self) -> None:
        original = json.loads(LOCK.read_text(encoding="utf-8"))
        original["package_count"] = 137
        original["fedora_package_count"] = 136
        with tempfile.TemporaryDirectory(prefix="pds020-archive-boundary-") as name:
            path = Path(name) / "lock.json"
            path.write_text(json.dumps(original), encoding="utf-8")
            with self.assertRaisesRegex(archive.ArchiveError, "boundary"):
                archive.load_lock(path)

    def test_real_rootfs_lock_extends_archive_to_exact_322_package_set(self) -> None:
        lock = archive.load_lock(ROOTFS_LOCK)
        self.assertEqual(lock["package_count"], 322)
        self.assertEqual(lock["fedora_package_count"], 321)
        self.assertEqual(lock["total_bytes"], 151701195)
        self.assertEqual(
            lock["records_sha256"],
            "fcfd45ca652707f4ad5fb16ccd49c258898e2bed0e1a3bf3203b0ce190104d89",
        )

    def test_lock_rejects_tampered_aggregate_and_project_signature_claim(self) -> None:
        original = json.loads(LOCK.read_text(encoding="utf-8"))
        for mutation, error in (
            (lambda value: value.update(total_bytes=value["total_bytes"] + 1), "aggregate"),
            (
                lambda value: next(
                    item for item in value["packages"] if item["origin"] == "project-pre-sign"
                ).update(signature_verified=True),
                "pre-sign",
            ),
        ):
            with self.subTest(error=error):
                with tempfile.TemporaryDirectory(prefix="pds020-archive-lock-") as name:
                    candidate = copy.deepcopy(original)
                    mutation(candidate)
                    path = Path(name) / "lock.json"
                    path.write_text(json.dumps(candidate), encoding="utf-8")
                    with self.assertRaisesRegex(archive.ArchiveError, error):
                        archive.load_lock(path)

    def test_lock_strict_json_rejects_duplicate_key(self) -> None:
        raw = LOCK.read_text(encoding="utf-8")
        duplicated = raw.replace('"schema": 1,', '"schema": 1, "schema": 1,', 1)
        with tempfile.TemporaryDirectory(prefix="pds020-archive-json-") as name:
            path = Path(name) / "lock.json"
            path.write_text(duplicated, encoding="utf-8")
            with self.assertRaisesRegex(archive.repro.ReproError, "duplicate key"):
                archive.load_lock(path)

    def test_archive_directory_rejects_missing_extra_and_symlink(self) -> None:
        with self.assertRaisesRegex(archive.ArchiveError, "absolute"):
            archive.safe_archive_directory(Path("relative"), {"a.rpm"})
        with tempfile.TemporaryDirectory(prefix="pds020-archive-dir-") as name:
            directory = Path(name)
            directory.chmod(0o700)
            first = directory / "a.rpm"
            second = directory / "key"
            first.write_bytes(b"a")
            second.write_bytes(b"k")
            archive.safe_archive_directory(directory, {"a.rpm", "key"})
            extra = directory / "extra"
            extra.write_bytes(b"x")
            with self.assertRaisesRegex(archive.ArchiveError, "extra"):
                archive.safe_archive_directory(directory, {"a.rpm", "key"})
            extra.unlink()
            second.unlink()
            with self.assertRaisesRegex(archive.ArchiveError, "missing"):
                archive.safe_archive_directory(directory, {"a.rpm", "key"})
            os.symlink(first, second)
            with self.assertRaisesRegex(archive.ArchiveError, "metadata"):
                archive.safe_archive_directory(directory, {"a.rpm", "key"})

    def test_locked_reader_rejects_content_tamper(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-archive-read-") as name:
            path = Path(name) / "artifact.rpm"
            path.write_bytes(b"rpm")
            with self.assertRaisesRegex(archive.ArchiveError, "content differs"):
                archive.read_locked(path, 3, "0" * 64, 32, "RPM")

    def test_verify_rechecks_every_archive_entry_after_external_tools(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("safe_archive_directory(archive_dir"), 2)
        self.assertGreaterEqual(source.count("for record in lock[\"packages\"]"), 4)

    def test_rpm_query_locks_identity_and_payload(self) -> None:
        expected = {
            "name": "package",
            "epoch": 0,
            "version": "1",
            "release": "2.fc44",
            "arch": "aarch64",
            "payload_sha256": "a" * 64,
        }
        good = b"package\n0\n1\n2.fc44\naarch64\n" + b"a" * 64 + b"\n"
        archive.parse_query(good, expected)
        with self.assertRaisesRegex(archive.ArchiveError, "identity"):
            archive.parse_query(good.replace(b"a" * 64, b"b" * 64), expected)

    def test_signature_parser_rejects_wrong_legacy_or_extra_signatures(self) -> None:
        artifact = Path("/archive/package.rpm")
        fingerprint = "1" * 40
        good = (
            f"{artifact}:\n"
            f"    Header OpenPGP V4 RSA/SHA256 signature, key fingerprint: {fingerprint}: OK\n"
            "    Header SHA256 digest: OK\n"
            "    Payload SHA256 digest: OK\n"
        ).encode()
        archive.parse_signature_output(good, artifact, fingerprint)
        for bad in (
            good.replace(b"1" * 40, b"2" * 40),
            good.replace(b"Header OpenPGP V4", b"Header V4 RSA/SHA256"),
            good + good.splitlines(keepends=True)[1],
        ):
            with self.subTest(bad=bad[-80:]):
                with self.assertRaises(archive.ArchiveError):
                    archive.parse_signature_output(bad, artifact, fingerprint)

    def test_verify_audits_unsigned_project_after_all_fedora_signatures(self) -> None:
        lock = archive.load_lock(LOCK)
        records = {item["filename"]: item for item in lock["packages"]}
        build = {
            "package": {
                "binary_result": {
                    key: records[
                        "pocketds-userspace-20260507-20260730174706.pds2.fc44.noarch.rpm"
                    ][key]
                    for key in ("filename", "size", "sha256", "payload_sha256")
                }
            }
        }

        def command_output(command: list[str], _label: str, _home: Path) -> bytes:
            if "--import" in command:
                return b""
            if "--list" in command:
                return f"{lock['fedora_key']['fingerprint']} Fedora public key\n".encode()
            filename = Path(command[-1]).name
            record = records[filename]
            if "--qf" in command:
                return (
                    f"{record['name']}\n{record['epoch']}\n{record['version']}\n"
                    f"{record['release']}\n{record['arch']}\n{record['payload_sha256']}\n"
                ).encode()
            return (
                f"{command[-1]}:\n"
                "    Header OpenPGP V4 RSA/SHA256 signature, key fingerprint: "
                f"{lock['fedora_key']['fingerprint']}: OK\n"
                "    Header SHA256 digest: OK\n"
                "    Payload SHA256 digest: OK\n"
            ).encode()

        with (
            mock.patch.object(archive, "safe_archive_directory"),
            mock.patch.object(archive, "read_locked", return_value=b"locked"),
            mock.patch.object(archive, "run_bounded", side_effect=command_output),
            mock.patch.object(archive.repro, "load_lock", return_value=build),
            mock.patch.object(
                archive.auditor, "audit", return_value={"payload_safe": True}
            ) as audited,
        ):
            report = archive.verify(
                lock,
                Path("/archive"),
                Path("/source-lock"),
                Path("/build-lock"),
                Path("/usr/bin/rpm"),
                Path("/usr/bin/rpmkeys"),
                Path("/usr/bin/rpm2archive"),
            )
        audited.assert_called_once()
        self.assertTrue(report["fedora_signatures_verified"])
        self.assertTrue(report["project_payload_audited"])
        self.assertFalse(report["project_signature_verified"])
        self.assertFalse(report["release_ready"])

    def test_verifier_has_no_network_download_install_or_shell_path(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "urlopen",
            "requests.",
            "curl ",
            "wget ",
            "dnf ",
            '"--install"',
            "shell=True",
        ):
            self.assertNotIn(forbidden, source)
        for required in (
            '"--import"',
            '"--checksig"',
            'auditor.audit(',
            '"offline": True',
            '"installed": False',
            '"release_ready": False',
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

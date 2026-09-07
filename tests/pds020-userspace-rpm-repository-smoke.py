#!/usr/bin/env python3
"""Offline tests for the future DNF5 signed-repository smoke harness."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pds020-userspace-rpm-repository-smoke.py"
SPEC = importlib.util.spec_from_file_location("pds020_repository_smoke_test", SOURCE)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def packages() -> list[dict[str, object]]:
    return [
        {
            "name": f"package-{index:03d}",
            "epoch": 0,
            "version": "1",
            "release": "1.fc44",
            "arch": "aarch64" if index % 2 else "noarch",
            "filename": f"package-{index:03d}-1-1.fc44.rpm",
            "size": 1,
            "sha256": f"{index + 1:064x}",
        }
        for index in range(322)
    ]


class RepositorySmokeTests(unittest.TestCase):
    def test_default_plan_is_read_only_and_fail_closed(self) -> None:
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            self.assertEqual(smoke.main([]), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["planned"])
        self.assertTrue(report["repo_gpgcheck_required"])
        self.assertTrue(report["package_gpgcheck_required"])
        self.assertFalse(report["skip_if_unavailable"])
        self.assertEqual(report["exact_available_package_count"], 322)
        self.assertFalse(report["release_ready"])

    def test_execution_requires_every_input_and_exact_confirmation(self) -> None:
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            self.assertEqual(smoke.main(["--execute"]), 2)
        self.assertIn("execution gate is incomplete", output.getvalue())
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(smoke.main(["--work-dir", "/tmp"]), 1)

    def test_dnf_command_enforces_one_local_repo_and_both_signature_checks(self) -> None:
        base = smoke.dnf_base(
            Path("/usr/bin/sudo"),
            Path("/usr/bin/dnf5"),
            Path("/private/installroot"),
            Path("/private/repository"),
            Path("/private/fedora-key"),
            Path("/private/project-key"),
        )
        self.assertIn("--repo=pds2", base)
        self.assertNotIn("--disable-repo=*", base)
        self.assertIn("--no-plugins", base)
        self.assertIn("--setopt=pds2.repo_gpgcheck=1", base)
        self.assertIn("--setopt=pds2.gpgcheck=1", base)
        self.assertIn("--setopt=pds2.skip_if_unavailable=0", base)
        self.assertIn("--forcearch=aarch64", base)
        repo = next(value for value in base if value.startswith("--repofrompath="))
        keys = next(value for value in base if value.startswith("--setopt=pds2.gpgkey="))
        self.assertTrue(repo.startswith("--repofrompath=pds2,file:///"))
        self.assertEqual(keys.count("file:///"), 2)

    def test_query_manifest_requires_all_322_exact_identities(self) -> None:
        expected = packages()
        rows = smoke.expected_query_rows(expected)
        data = "".join(f"{row}\n" for row in rows).encode()
        report = smoke.parse_query_output(data, expected)
        self.assertEqual(report["package_count"], 322)
        self.assertEqual(report["package_manifest_sha256"], digest(data))
        for changed in (data.splitlines(keepends=True)[:-1], list(reversed(data.splitlines(keepends=True)))):
            with self.subTest(row_count=len(changed)):
                with self.assertRaisesRegex(smoke.RepositorySmokeError, "manifest"):
                    smoke.parse_query_output(b"".join(changed), expected)

    def test_signature_or_skipped_repository_text_fails_even_with_zero_exit(self) -> None:
        for token in smoke.FORBIDDEN_DNF_TEXT:
            with self.subTest(token=token):
                with self.assertRaisesRegex(
                    smoke.RepositorySmokeError, "signature failure"
                ):
                    smoke.ensure_clean_dnf_output(b"", token, "fixture")

    def test_materialized_repository_is_content_bound_and_exact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-repository-materialize-") as name:
            base = Path(name)
            archive = base / "archive"
            result = base / "result"
            workspace = base / "workspace"
            for directory in (archive, result, workspace):
                directory.mkdir(mode=0o700)
            package_data = b"rpm"
            package = {
                "filename": "package.rpm",
                "size": len(package_data),
                "sha256": digest(package_data),
            }
            (archive / package["filename"]).write_bytes(package_data)
            records = []
            for filename, data in (
                ("repomd.xml", b"repomd"),
                ("1-filelists.xml.xz", b"filelists"),
                ("2-other.xml.xz", b"other"),
                ("3-primary.xml.xz", b"primary"),
            ):
                record = {
                    "filename": filename,
                    "size": len(data),
                    "sha256": digest(data),
                }
                (result / filename).write_bytes(data)
                records.append(record)
            signature_data = b"signature"
            signature = {
                "filename": "repomd.xml.asc",
                "size": len(signature_data),
                "sha256": digest(signature_data),
            }
            (result / signature["filename"]).write_bytes(signature_data)
            receipt = {
                "repository": {
                    "repomd": records[0],
                    "metadata": records[1:],
                },
                "repository_signature": {"artifact": signature},
            }
            report = smoke.materialize_repository(
                workspace / "repository",
                archive,
                result,
                {"packages": [package]},
                receipt,
            )
            self.assertEqual(report["file_count"], 6)
            self.assertEqual(
                {path.name for path in (workspace / "repository").iterdir()},
                {"package.rpm", "repodata"},
            )
            self.assertEqual(
                {path.name for path in (workspace / "repository/repodata").iterdir()},
                {record["filename"] for record in records} | {"repomd.xml.asc"},
            )

    def test_copy_bound_rejects_changed_or_unsafe_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-repository-copy-") as name:
            base = Path(name)
            source = base / "source"
            destination = base / "destination"
            source.write_bytes(b"content")
            size, value = smoke.copy_bound(source, destination, 1024)
            self.assertEqual(size, 7)
            self.assertEqual(value, digest(b"content"))
            linked = base / "linked"
            linked.symlink_to(source)
            with self.assertRaises(smoke.repro.ReproError):
                smoke.copy_bound(linked, base / "other", 1024)

    def test_execute_queries_installs_checks_and_cleans(self) -> None:
        expected_packages = packages()
        rows = smoke.expected_query_rows(expected_packages)
        query = "".join(f"{row}\n" for row in rows).encode()
        rootfs_lock = {
            "installed_stage": {
                "package_count": 322,
                "package_manifest_sha256": "a" * 64,
            }
        }
        with tempfile.TemporaryDirectory(prefix="pds020-repository-execute-") as name:
            work = Path(name)
            work.chmod(0o700)
            commands: list[list[str]] = []

            def run(command: list[str], _label: str) -> bytes:
                commands.append(command)
                return query if "repoquery" in command else b""

            with (
                mock.patch.object(
                    smoke,
                    "materialize_repository",
                    return_value={"file_count": 327, "copied_bytes": 1},
                ),
                mock.patch.object(smoke, "run_dnf", side_effect=run),
                mock.patch.object(
                    smoke.rootfs.full,
                    "package_manifest",
                    return_value=(322, "a" * 64),
                ),
                mock.patch.object(smoke.rootfs.full, "validate_installed"),
                mock.patch.object(smoke, "cleanup_workspace") as cleanup,
            ):
                report = smoke.execute(
                    {"repository": {}, "repository_signature": {}},
                    {
                        "packages": expected_packages,
                        "fedora_key": {"filename": "fedora-key"},
                    },
                    Path("/private/archive"),
                    Path("/private/result"),
                    Path("/private/unsigned"),
                    Path("/private/project-key"),
                    rootfs_lock,
                    {},
                    work,
                    Path("/usr/bin/sudo"),
                    Path("/usr/bin/dnf5"),
                    Path("/usr/bin/rpm"),
                    Path("/usr/bin/rm"),
                )
            self.assertEqual(report["installed_package_count"], 322)
            self.assertEqual(len(commands), 3)
            self.assertIn("repoquery", commands[0])
            self.assertIn("install", commands[1])
            self.assertIn("check", commands[2])
            cleanup.assert_called_once()

    def test_execute_failure_still_attempts_cleanup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-repository-execute-") as name:
            work = Path(name)
            work.chmod(0o700)
            with (
                mock.patch.object(
                    smoke,
                    "materialize_repository",
                    side_effect=smoke.RepositorySmokeError("synthetic failure"),
                ),
                mock.patch.object(smoke, "cleanup_workspace") as cleanup,
            ):
                with self.assertRaisesRegex(smoke.RepositorySmokeError, "synthetic"):
                    smoke.execute(
                        {"repository": {}, "repository_signature": {}},
                        {"packages": [], "fedora_key": {"filename": "key"}},
                        Path("/private/archive"),
                        Path("/private/result"),
                        Path("/private/unsigned"),
                        Path("/private/project-key"),
                        {"installed_stage": {}},
                        {},
                        work,
                        Path("/usr/bin/sudo"),
                        Path("/usr/bin/dnf5"),
                        Path("/usr/bin/rpm"),
                        Path("/usr/bin/rm"),
                    )
            cleanup.assert_called_once()

    def test_source_has_no_signature_network_or_package_check_bypass(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "shell=True",
            "urlopen",
            "requests.",
            "curl ",
            "wget ",
            "--no-gpgchecks",
            "--cacheonly",
            "--nodeps",
            "--noscripts",
            "--notriggers",
            "systemctl",
            "reboot",
        ):
            self.assertNotIn(forbidden, source)
        for required in (
            '"--setopt=pds2.repo_gpgcheck=1"',
            '"--setopt=pds2.gpgcheck=1"',
            '"--setopt=pds2.skip_if_unavailable=0"',
            '"--repo=pds2"',
            '"repository_receipt_sha256"',
            '"repository_repomd_sha256"',
            '"repository_signature_sha256"',
            '"project_rpm_sha256"',
            '"final_archive_records_sha256"',
            '"release_ready": False',
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

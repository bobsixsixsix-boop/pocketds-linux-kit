#!/usr/bin/env python3
"""Offline tests for the two-result pocketds-userspace RPM reproduction gate."""

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
SOURCE = ROOT / "scripts/pds020-userspace-rpm-repro.py"
SPEC = importlib.util.spec_from_file_location("pds020_userspace_repro_test", SOURCE)
assert SPEC and SPEC.loader
repro = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = repro
SPEC.loader.exec_module(repro)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self, base: Path) -> None:
        self.source_name = "pocketds-userspace-1-1.pds1.fc44.src.rpm"
        self.binary_name = "pocketds-userspace-1-1.pds1.fc44.noarch.rpm"
        self.source = b"reproducible source rpm\n"
        self.binary = b"reproducible binary rpm\n"
        self.packages = b"rpm-1-1.fc44.aarch64 1 1 installed\n"
        self.source_payload = "1" * 64
        self.binary_payload = "2" * 64
        self.manifest = "3" * 64
        self.scriptlets = "4" * 64
        self.fan = "5" * 64
        self.lock = {
            "schema": 1,
            "package": {
                "name": "pocketds-userspace",
                "version": "1",
                "release": "1.pds1.fc44",
                "arch": "noarch",
                "source_result": {
                    "filename": self.source_name,
                    "size": len(self.source),
                    "sha256": digest(self.source),
                    "payload_sha256": self.source_payload,
                },
                "binary_result": {
                    "filename": self.binary_name,
                    "size": len(self.binary),
                    "sha256": digest(self.binary),
                    "payload_sha256": self.binary_payload,
                },
            },
            "builder": {
                "distro": "fedora-44",
                "architecture": "aarch64",
                "mock_config": "fedora-44-aarch64",
                "mock_nvr": "mock-1-1.fc44.noarch",
                "offline_rebuild": True,
                "independent_result_count": 2,
                "macros": {
                    "_buildhost": "pocketds-build.invalid",
                    "use_source_date_epoch_as_buildtime": 1,
                },
                "buildtime": 100,
                "installed_packages": {
                    "filename": "installed_pkgs.log",
                    "size": len(self.packages),
                    "sha256": digest(self.packages),
                },
            },
            "payload_audit": {
                "file_count": 2,
                "manifest_sha256": self.manifest,
                "scriptlet_count": 1,
                "scriptlets_sha256": self.scriptlets,
                "fan_controller_sha256": self.fan,
            },
        }
        self.lock_path = base / "build-lock.json"
        self.write_lock()
        self.result_a = self.write_result(base / "a")
        self.result_b = self.write_result(base / "b")
        self.rpm = base / "rpm"
        self.rpm2archive = base / "rpm2archive"
        for tool in (self.rpm, self.rpm2archive):
            tool.write_bytes(b"fixture tool\n")
            tool.chmod(0o755)

    def write_lock(self) -> None:
        self.lock_path.write_text(json.dumps(self.lock), encoding="utf-8")

    def write_result(self, path: Path) -> Path:
        path.mkdir(mode=0o700)
        (path / self.source_name).write_bytes(self.source)
        (path / self.binary_name).write_bytes(self.binary)
        (path / "installed_pkgs.log").write_bytes(self.packages)
        return path

    def header(self, _rpm: Path, artifact: Path) -> dict[str, object]:
        source = artifact.name == self.source_name
        return {
            "name": "pocketds-userspace",
            "version": "1",
            "release": "1.pds1.fc44",
            "arch": "noarch",
            "buildhost": "pocketds-build.invalid",
            "buildtime": 100,
            "payload_sha256": self.source_payload if source else self.binary_payload,
        }

    def audit(self) -> dict[str, object]:
        return {
            "payload_safe": True,
            "global_nopasswd_absent": True,
            "fan_controller_locked": True,
            "payload_file_count": 2,
            "payload_manifest_sha256": self.manifest,
            "scriptlet_count": 1,
            "scriptlets_sha256": self.scriptlets,
            "fan_controller_sha256": self.fan,
        }

    def evaluate(self) -> dict[str, object]:
        with (
            mock.patch.object(repro, "query_header", side_effect=self.header),
            mock.patch.object(repro.auditor, "audit", return_value=self.audit()),
        ):
            return repro.evaluate(
                self.lock_path,
                ROOT / "packaging/pocketds-userspace/source-lock.json",
                self.result_a,
                self.result_b,
                self.rpm,
                self.rpm2archive,
            )


class ReproductionTests(unittest.TestCase):
    def test_header_query_uses_independent_json_lines(self):
        self.assertEqual(
            repro.HEADER_FORMAT,
            "\\n".join(
                f"%{{{tag}:json}}" for _, tag in repro.HEADER_FIELDS
            )
            + "\\n",
        )
        self.assertNotIn("{\"", repro.HEADER_FORMAT)
        self.assertIn("%{BUILDTIME:json}", repro.HEADER_FORMAT)

    def test_exact_two_result_fixture_passes_without_release_authorization(self):
        with tempfile.TemporaryDirectory(prefix="pds020-repro-") as name:
            report = Fixture(Path(name)).evaluate()
            self.assertTrue(report["source_rpm_byte_identical"])
            self.assertTrue(report["binary_rpm_byte_identical"])
            self.assertTrue(report["buildroot_package_manifest_identical"])
            self.assertTrue(report["global_nopasswd_absent"])
            self.assertFalse(report["signature_verified"])
            self.assertFalse(report["release_ready"])

    def test_each_locked_result_artifact_drift_fails_closed(self):
        for filename, content in (
            ("source", b"drifted source\n"),
            ("binary", b"drifted binary\n"),
            ("installed_pkgs.log", b"drifted packages\n"),
        ):
            with self.subTest(filename=filename):
                with tempfile.TemporaryDirectory(prefix="pds020-repro-") as name:
                    fixture = Fixture(Path(name))
                    target = {
                        "source": fixture.source_name,
                        "binary": fixture.binary_name,
                    }.get(filename, filename)
                    (fixture.result_b / target).write_bytes(content)
                    with self.assertRaisesRegex(repro.ReproError, "build lock"):
                        fixture.evaluate()

    def test_two_distinct_private_result_directories_are_required(self):
        with tempfile.TemporaryDirectory(prefix="pds020-repro-") as name:
            fixture = Fixture(Path(name))
            fixture.result_b = fixture.result_a
            with self.assertRaisesRegex(repro.ReproError, "distinct"):
                fixture.evaluate()
        with tempfile.TemporaryDirectory(prefix="pds020-repro-") as name:
            fixture = Fixture(Path(name))
            fixture.result_b.chmod(0o755)
            with self.assertRaisesRegex(repro.ReproError, "mode-0700"):
                fixture.evaluate()

    def test_header_and_payload_audit_drift_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-repro-") as name:
            fixture = Fixture(Path(name))
            wrong = fixture.header(fixture.rpm, fixture.result_a / fixture.binary_name)
            wrong["buildtime"] = 101
            with (
                mock.patch.object(repro, "query_header", return_value=wrong),
                mock.patch.object(repro.auditor, "audit", return_value=fixture.audit()),
            ):
                with self.assertRaisesRegex(repro.ReproError, "header"):
                    repro.evaluate(
                        fixture.lock_path,
                        ROOT / "packaging/pocketds-userspace/source-lock.json",
                        fixture.result_a,
                        fixture.result_b,
                        fixture.rpm,
                        fixture.rpm2archive,
                    )
        with tempfile.TemporaryDirectory(prefix="pds020-repro-") as name:
            fixture = Fixture(Path(name))
            audit = fixture.audit()
            audit["global_nopasswd_absent"] = False
            with (
                mock.patch.object(repro, "query_header", side_effect=fixture.header),
                mock.patch.object(repro.auditor, "audit", return_value=audit),
            ):
                with self.assertRaisesRegex(repro.ReproError, "payload audit"):
                    repro.evaluate(
                        fixture.lock_path,
                        ROOT / "packaging/pocketds-userspace/source-lock.json",
                        fixture.result_a,
                        fixture.result_b,
                        fixture.rpm,
                        fixture.rpm2archive,
                    )

    def test_build_lock_is_strict_typed_and_duplicate_safe(self):
        with tempfile.TemporaryDirectory(prefix="pds020-repro-") as name:
            fixture = Fixture(Path(name))
            fixture.lock["builder"]["offline_rebuild"] = 1
            fixture.write_lock()
            with self.assertRaisesRegex(repro.ReproError, "builder lock"):
                fixture.evaluate()
        with self.assertRaisesRegex(repro.ReproError, "duplicate key"):
            repro.strict_json(b'{"schema":1,"schema":2}', "fixture")
        with self.assertRaisesRegex(repro.ReproError, "non-finite"):
            repro.strict_json(b'{"schema":NaN}', "fixture")

    def test_recorded_build_lock_matches_measured_mock_result(self):
        lock = repro.load_lock(
            ROOT / "packaging/pocketds-userspace/build-lock.json"
        )
        self.assertEqual(lock["builder"]["independent_result_count"], 2)
        self.assertTrue(lock["builder"]["offline_rebuild"])
        self.assertEqual(
            lock["package"]["binary_result"]["sha256"],
            "cd60625fca2f668d828d6cee119afc6a370925ce77302362f373d4373e4944d4",
        )
        self.assertFalse("signature" in lock)

    def test_tool_is_read_only_offline_and_has_no_build_or_install_path(self):
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "--rebuild",
            "rpmbuild",
            "sudo",
            "dnf ",
            "rpm -i",
            "mock --",
            "urllib.request",
            "requests",
            "socket",
            "shell=True",
            "write_bytes",
            "write_text",
        ):
            self.assertNotIn(forbidden, source)
        completed = subprocess.run(
            [sys.executable, os.fspath(SOURCE), "--help"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.assertNotIn("--output", completed.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

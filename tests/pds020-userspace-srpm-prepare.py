#!/usr/bin/env python3
"""Offline tests for the pinned pocketds-userspace SRPM hardening step."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pds020-userspace-srpm-prepare.py"
LOCK = ROOT / "packaging/pocketds-userspace/source-lock.json"
PDS2_LOCK = ROOT / "packaging/pocketds-userspace/source-lock.pds2.json"
SPEC = importlib.util.spec_from_file_location("pds020_userspace_prepare", SOURCE)
assert SPEC and SPEC.loader
prepare = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prepare
SPEC.loader.exec_module(prepare)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self, base: Path) -> None:
        self.source_rpm = base / "pocketds-userspace-1-1.fc44.src.rpm"
        self.source_rpm.write_bytes(b"synthetic signed source RPM\n")
        self.extracted = base / "extracted"
        self.extracted.mkdir()
        self.spec = (
            "Name:           pocketds-userspace\n"
            "Release:        1%{?dist}\n"
            "Source70:       pocketds-fancontrol\n"
            "Source22:       10-wheel-nopasswd\n"
            "description mentions wheel nopasswd\n"
            "%install\n"
            "install 10-wheel-nopasswd\n"
            "%files\n"
            "%{_sysconfdir}/sudoers.d/10-wheel-nopasswd\n"
            "%changelog\n"
            "* old fixture\n"
        ).encode()
        (self.extracted / "pocketds-userspace.spec").write_bytes(self.spec)
        self.fan_source = b"#!/usr/bin/python3\nprint('old fan controller')\n"
        (self.extracted / "pocketds-fancontrol").write_bytes(self.fan_source)
        self.fan_override = b"#!/usr/bin/python3\nprint('smooth fan controller')\n"
        (base / "pocketds-fancontrol.pds1").write_bytes(self.fan_override)
        self.forbidden = b"%wheel ALL=(ALL) NOPASSWD: ALL\n"
        (self.extracted / "10-wheel-nopasswd").write_bytes(self.forbidden)
        self.other_source = b"locked auxiliary source\n"
        (self.extracted / "other-source").write_bytes(self.other_source)
        self.rpmkeys = base / "rpmkeys"
        self.rpmkeys.write_text("fixture\n", encoding="utf-8")
        self.rpmkeys.chmod(0o755)

        transformed = (
            "Name:           pocketds-userspace\n"
            "Release:        1.pds1%{?dist}\n"
            "Source70:       pocketds-fancontrol.pds1\n"
            "%install\n"
            "%files\n"
            "%changelog\n"
            "* fixture hardening\n"
            "- remove global sudo\n"
            "\n"
            "* old fixture\n"
        ).encode()
        self.lock = {
            "schema": 1,
            "package": {
                "name": "pocketds-userspace",
                "source_version": "1-1.fc44",
                "hardened_version": "1-1.pds1.fc44",
                "source_rpm": {
                    "filename": "pocketds-userspace-1-1.fc44.src.rpm",
                    "url": "https://download.copr.fedorainfracloud.org/results/pocketds-userspace-1-1.fc44.src.rpm",
                    "size": len(self.source_rpm.read_bytes()),
                    "sha256": digest(self.source_rpm.read_bytes()),
                    "signing_key_fingerprint": "a" * 40,
                },
                "source_tree": prepare.source_tree_identity(self.extracted),
                "spec": {
                    "path": "pocketds-userspace.spec",
                    "size": len(self.spec),
                    "sha256": digest(self.spec),
                },
                "fan_controller_source": {
                    "path": "pocketds-fancontrol",
                    "size": len(self.fan_source),
                    "sha256": digest(self.fan_source),
                },
                "forbidden_source": {
                    "path": "10-wheel-nopasswd",
                    "size": len(self.forbidden),
                    "sha256": digest(self.forbidden),
                },
            },
            "transform": {
                "replace_exact_lines": [
                    {
                        "old": "Release:        1%{?dist}",
                        "new": "Release:        1.pds1%{?dist}",
                    },
                    {
                        "old": "Source70:       pocketds-fancontrol",
                        "new": "Source70:       pocketds-fancontrol.pds1",
                    }
                ],
                "remove_exact_lines": [
                    "Source22:       10-wheel-nopasswd",
                    "description mentions wheel nopasswd",
                    "install 10-wheel-nopasswd",
                    "%{_sysconfdir}/sudoers.d/10-wheel-nopasswd",
                ],
                "insert_after_exact_line": {
                    "line": "%changelog",
                    "text": "* fixture hardening\n- remove global sudo\n",
                },
                "forbidden_tokens": [
                    "10-wheel-nopasswd",
                    "NOPASSWD: ALL",
                    "wheel nopasswd",
                ],
                "fan_controller_override": {
                    "path": "pocketds-fancontrol.pds1",
                    "size": len(self.fan_override),
                    "sha256": digest(self.fan_override),
                    "payload_path": "/usr/bin/pocketds-fancontrol",
                    "payload_mode": "0755",
                },
                "hardened_spec_size": len(transformed),
                "hardened_spec_sha256": digest(transformed),
            },
        }
        self.lock_path = base / "lock.json"
        self.write_lock()
        self.transformed = transformed

    def write_lock(self) -> None:
        self.lock_path.write_text(json.dumps(self.lock), encoding="utf-8")

    def refresh_tree_lock(self) -> None:
        self.lock["package"]["source_tree"] = prepare.source_tree_identity(
            self.extracted
        )


class PrepareTests(unittest.TestCase):
    def test_exact_fixture_verifies_without_output(self):
        with tempfile.TemporaryDirectory(prefix="pds020-userspace-") as name:
            fixture = Fixture(Path(name))
            with mock.patch.object(prepare, "verify_source_signature") as signature:
                report = prepare.prepare(
                    fixture.lock_path,
                    fixture.source_rpm,
                    fixture.extracted,
                    fixture.rpmkeys,
                    None,
                )
            signature.assert_called_once()
            self.assertTrue(report["read_only"])
            self.assertTrue(report["global_nopasswd_removed"])
            self.assertFalse(report["release_ready"])
            self.assertEqual(
                report["fan_controller_override_sha256"],
                digest(fixture.fan_override),
            )
            self.assertEqual(report["hardened_spec_sha256"], digest(fixture.transformed))

    def test_archive_tamper_fails_before_signature(self):
        with tempfile.TemporaryDirectory(prefix="pds020-userspace-") as name:
            fixture = Fixture(Path(name))
            fixture.source_rpm.write_bytes(b"tampered\n")
            with mock.patch.object(prepare, "verify_source_signature") as signature:
                with self.assertRaisesRegex(prepare.PrepareError, "size mismatch"):
                    prepare.prepare(
                        fixture.lock_path,
                        fixture.source_rpm,
                        fixture.extracted,
                        fixture.rpmkeys,
                        None,
                    )
            signature.assert_not_called()

    def test_archive_swap_during_signature_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-userspace-") as name:
            fixture = Fixture(Path(name))

            def replace_archive(*_args):
                fixture.source_rpm.write_bytes(b"different signed archive\n")

            with mock.patch.object(
                prepare, "verify_source_signature", side_effect=replace_archive
            ):
                with self.assertRaisesRegex(
                    prepare.PrepareError, "after signature verification size mismatch"
                ):
                    prepare.prepare(
                        fixture.lock_path,
                        fixture.source_rpm,
                        fixture.extracted,
                        fixture.rpmkeys,
                        None,
                    )

    def test_upstream_spec_drift_fails_exact_line_contract(self):
        with tempfile.TemporaryDirectory(prefix="pds020-userspace-") as name:
            fixture = Fixture(Path(name))
            changed = fixture.spec.replace(
                b"Source22:       10-wheel-nopasswd",
                b"Source99:       10-wheel-nopasswd",
            )
            (fixture.extracted / "pocketds-userspace.spec").write_bytes(changed)
            fixture.lock["package"]["spec"]["size"] = len(changed)
            fixture.lock["package"]["spec"]["sha256"] = digest(changed)
            fixture.refresh_tree_lock()
            fixture.write_lock()
            with mock.patch.object(prepare, "verify_source_signature"):
                with self.assertRaisesRegex(prepare.PrepareError, "absent or ambiguous"):
                    prepare.prepare(
                        fixture.lock_path,
                        fixture.source_rpm,
                        fixture.extracted,
                        fixture.rpmkeys,
                        None,
                    )

    def test_recorded_versions_reject_unversioned_downstream_release(self):
        with tempfile.TemporaryDirectory(prefix="pds020-userspace-") as name:
            fixture = Fixture(Path(name))
            fixture.lock["package"]["hardened_version"] = "1-1.local.fc44"
            fixture.write_lock()
            with mock.patch.object(prepare, "verify_source_signature") as signature:
                with self.assertRaisesRegex(
                    prepare.PrepareError, "pinned downstream release"
                ):
                    prepare.prepare(
                        fixture.lock_path,
                        fixture.source_rpm,
                        fixture.extracted,
                        fixture.rpmkeys,
                        None,
                    )
            signature.assert_not_called()

    def test_numbered_downstream_generation_is_content_bound(self):
        with tempfile.TemporaryDirectory(prefix="pds020-userspace-") as name:
            fixture = Fixture(Path(name))
            fixture.lock["package"]["hardened_version"] = "1-1.pds2.fc44"
            fixture.lock["transform"]["replace_exact_lines"][0]["new"] = (
                "Release:        1.pds2%{?dist}"
            )
            transformed = fixture.transformed.replace(
                b"Release:        1.pds1%{?dist}",
                b"Release:        1.pds2%{?dist}",
            )
            fixture.lock["transform"]["hardened_spec_size"] = len(transformed)
            fixture.lock["transform"]["hardened_spec_sha256"] = digest(transformed)
            fixture.write_lock()
            with mock.patch.object(prepare, "verify_source_signature"):
                report = prepare.prepare(
                    fixture.lock_path,
                    fixture.source_rpm,
                    fixture.extracted,
                    fixture.rpmkeys,
                    None,
                )
            self.assertEqual(report["hardened_spec_sha256"], digest(transformed))
            self.assertFalse(report["release_ready"])

    def test_complete_extracted_source_tree_is_content_bound(self):
        with tempfile.TemporaryDirectory(prefix="pds020-userspace-") as name:
            fixture = Fixture(Path(name))
            (fixture.extracted / "other-source").write_bytes(b"drifted\n")
            with mock.patch.object(prepare, "verify_source_signature"):
                with self.assertRaisesRegex(
                    prepare.PrepareError, "source tree identity mismatch"
                ):
                    prepare.prepare(
                        fixture.lock_path,
                        fixture.source_rpm,
                        fixture.extracted,
                        fixture.rpmkeys,
                        None,
                    )
            (fixture.extracted / "other-source").write_bytes(fixture.other_source)
            (fixture.extracted / "unexpected-source").write_bytes(b"extra\n")
            with mock.patch.object(prepare, "verify_source_signature"):
                with self.assertRaisesRegex(
                    prepare.PrepareError, "source tree identity mismatch"
                ):
                    prepare.prepare(
                        fixture.lock_path,
                        fixture.source_rpm,
                        fixture.extracted,
                        fixture.rpmkeys,
                        None,
                    )

    def test_source_lock_json_is_strict_and_unambiguous(self):
        with tempfile.TemporaryDirectory(prefix="pds020-userspace-") as name:
            fixture = Fixture(Path(name))
            original = fixture.lock_path.read_text(encoding="utf-8")
            fixture.lock_path.write_text(
                '{"schema":999,' + original[1:],
                encoding="utf-8",
            )
            with mock.patch.object(prepare, "verify_source_signature") as signature:
                with self.assertRaisesRegex(prepare.PrepareError, "duplicate key"):
                    prepare.prepare(
                        fixture.lock_path,
                        fixture.source_rpm,
                        fixture.extracted,
                        fixture.rpmkeys,
                        None,
                    )
            signature.assert_not_called()

        for value in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(prepare.PrepareError, "non-finite"):
                    prepare.strict_json(b'{"value":' + value + b"}", "fixture")
        with self.assertRaisesRegex(prepare.PrepareError, "UTF-8 JSON"):
            prepare.strict_json('{"schema":1}'.encode("utf-16"), "fixture")

    def test_source_lock_numeric_and_url_types_are_exact(self):
        for field, value, error in (
            ("schema", True, "schema"),
            ("archive.size", True, "size is invalid"),
            ("archive.url", [1], "URL or filename"),
            ("source_tree.file_count", True, "count or size is invalid"),
            ("hardened_spec_size", None, "hardened spec size mismatch"),
            ("unknown", 1, "fields are incomplete"),
        ):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory(prefix="pds020-userspace-") as name:
                    fixture = Fixture(Path(name))
                    if field == "schema":
                        fixture.lock["schema"] = value
                    elif field == "archive.size":
                        fixture.lock["package"]["source_rpm"]["size"] = value
                    elif field == "archive.url":
                        fixture.lock["package"]["source_rpm"]["url"] = value
                    elif field == "source_tree.file_count":
                        fixture.lock["package"]["source_tree"]["file_count"] = value
                    elif field == "hardened_spec_size":
                        fixture.lock["transform"][field] = float(
                            fixture.lock["transform"][field]
                        )
                    else:
                        fixture.lock[field] = value
                    fixture.write_lock()
                    with mock.patch.object(
                        prepare, "verify_source_signature"
                    ) as signature:
                        with self.assertRaisesRegex(prepare.PrepareError, error):
                            prepare.prepare(
                                fixture.lock_path,
                                fixture.source_rpm,
                                fixture.extracted,
                                fixture.rpmkeys,
                                None,
                            )
                    if field != "hardened_spec_size":
                        signature.assert_not_called()

    def test_output_is_new_private_exact_content(self):
        with tempfile.TemporaryDirectory(prefix="pds020-userspace-") as name:
            fixture = Fixture(Path(name))
            output = Path(name) / "hardened.spec"
            fan_output = Path(name) / "pocketds-fancontrol.pds1.output"
            with mock.patch.object(prepare, "verify_source_signature"):
                report = prepare.prepare(
                    fixture.lock_path,
                    fixture.source_rpm,
                    fixture.extracted,
                    fixture.rpmkeys,
                    output,
                    fan_output,
                )
                self.assertEqual(output.read_bytes(), fixture.transformed)
                self.assertEqual(fan_output.read_bytes(), fixture.fan_override)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.assertEqual(fan_output.stat().st_mode & 0o777, 0o600)
                self.assertTrue(report["output_written"])
                with self.assertRaisesRegex(prepare.PrepareError, "already exists"):
                    prepare.prepare(
                        fixture.lock_path,
                        fixture.source_rpm,
                        fixture.extracted,
                        fixture.rpmkeys,
                        output,
                        fan_output,
                    )

    def test_outputs_are_required_as_a_pair(self):
        with tempfile.TemporaryDirectory(prefix="pds020-userspace-") as name:
            fixture = Fixture(Path(name))
            with mock.patch.object(prepare, "verify_source_signature"):
                with self.assertRaisesRegex(prepare.PrepareError, "requested together"):
                    prepare.prepare(
                        fixture.lock_path,
                        fixture.source_rpm,
                        fixture.extracted,
                        fixture.rpmkeys,
                        Path(name) / "hardened.spec",
                    )


class SignatureAndRecordedLockTests(unittest.TestCase):
    def test_rpmkeys_requires_two_exact_signatures_and_payload_digests(self):
        with tempfile.TemporaryDirectory(prefix="pds020-userspace-") as name:
            base = Path(name)
            rpmkeys = base / "rpmkeys"
            rpmkeys.write_text("fixture\n", encoding="utf-8")
            rpmkeys.chmod(0o755)
            fingerprint = "a" * 40
            completed = subprocess.CompletedProcess(
                [],
                0,
                stdout=(
                    f"Header OpenPGP signature, key fingerprint: {fingerprint}: OK\n"
                    "Header SHA256 digest: OK\n"
                    "Payload SHA256 digest: OK\n"
                    f"Legacy OpenPGP signature, key fingerprint: {fingerprint}: OK\n"
                ),
                stderr="",
            )
            with mock.patch.object(prepare.subprocess, "run", return_value=completed):
                prepare.verify_source_signature(
                    rpmkeys, base / "source.src.rpm", fingerprint
                )
            completed.stdout = completed.stdout.replace(
                "Payload SHA256 digest: OK", "Payload SHA256 digest: BAD"
            )
            with mock.patch.object(prepare.subprocess, "run", return_value=completed):
                with self.assertRaises(prepare.PrepareError):
                    prepare.verify_source_signature(
                        rpmkeys, base / "source.src.rpm", fingerprint
                    )

    def test_recorded_lock_pins_distinct_hardened_release(self):
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        package = lock["package"]
        archive = package["source_rpm"]
        self.assertNotEqual(package["source_version"], package["hardened_version"])
        self.assertIn(".pds1.", package["hardened_version"])
        self.assertEqual(archive["size"], 94753)
        self.assertRegex(archive["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(archive["signing_key_fingerprint"], r"^[0-9a-f]{40}$")
        source_tree = package["source_tree"]
        self.assertEqual(source_tree["file_count"], 67)
        self.assertEqual(source_tree["total_size"], 148381)
        self.assertRegex(source_tree["manifest_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(archive["url"].startswith("https://download.copr.fedorainfracloud.org/"))
        self.assertIn("10-wheel-nopasswd", lock["transform"]["forbidden_tokens"])
        fan = lock["transform"]["fan_controller_override"]
        self.assertEqual(fan["sha256"], digest((LOCK.parent / fan["path"]).read_bytes()))
        self.assertEqual(fan["payload_path"], "/usr/bin/pocketds-fancontrol")

    def test_pds2_candidate_removes_retired_osk_contract(self):
        lock = json.loads(PDS2_LOCK.read_text(encoding="utf-8"))
        self.assertEqual(
            lock["package"]["hardened_version"],
            "20260507-20260730174706.pds2.fc44",
        )
        transform = lock["transform"]
        self.assertEqual(transform["hardened_spec_size"], 28270)
        self.assertEqual(
            transform["hardened_spec_sha256"],
            "bdd2045335d5d63386efcc004ca39e7b86afedfba0f9314b26ffe614180297b9",
        )
        removals = transform["remove_exact_lines"]
        for token in (
            "Requires:       onboard",
            "Requires:       onboard-data",
            "Source100:      pocketds-onboard",
            "%{_bindir}/pocketds-osk-listener",
            "/usr/local/share/dbus-1/services/org.onboard.Onboard.service",
        ):
            self.assertIn(token, removals)
        self.assertIn("Onboard", transform["forbidden_tokens"])
        self.assertIn("onboard", transform["forbidden_tokens"])

    def test_tool_has_no_network_download_install_or_build_path(self):
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "urllib.request",
            "requests",
            "socket",
            "curl ",
            "wget ",
            "dnf ",
            "rpm -i",
            "rpmbuild",
            "shell=True",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('"network": False', source)
        self.assertIn('"build": False', source)
        self.assertIn('"--checksig", "--verbose"', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

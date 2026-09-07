#!/usr/bin/env python3
"""Offline tests for the hardened userspace RPM post-sign receipt gate."""

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
SOURCE = ROOT / "scripts/pds020-userspace-rpm-postsign.py"
SPEC = importlib.util.spec_from_file_location("pds020_userspace_postsign_test", SOURCE)
assert SPEC and SPEC.loader
postsign = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = postsign
SPEC.loader.exec_module(postsign)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self, base: Path, candidate: str = "pds1") -> None:
        suffix = "" if candidate == "pds1" else ".pds2"
        self.build_lock_path = (
            ROOT / f"packaging/pocketds-userspace/build-lock{suffix}.json"
        )
        self.source_lock_path = (
            ROOT / f"packaging/pocketds-userspace/source-lock{suffix}.json"
        )
        self.build_lock_bytes = self.build_lock_path.read_bytes()
        self.build_lock = json.loads(self.build_lock_bytes)
        package = self.build_lock["package"]
        payload = self.build_lock["payload_audit"]
        builder = self.build_lock["builder"]
        self.artifact = base / package["binary_result"]["filename"]
        self.artifact_bytes = b"signed fixture rpm differs from pre-sign bytes\n"
        self.artifact.write_bytes(self.artifact_bytes)
        self.public_key = base / "release-key.asc"
        self.key_bytes = b"fixture public key only\n"
        self.public_key.write_bytes(self.key_bytes)
        self.receipt_path = base / "post-sign-receipt.json"
        self.receipt = {
            "schema": 1,
            "artifact": {
                "filename": self.artifact.name,
                "size": len(self.artifact_bytes),
                "sha256": digest(self.artifact_bytes),
            },
            "canonical_build": {
                "build_lock_sha256": digest(self.build_lock_bytes),
                "pre_sign_binary_sha256": package["binary_result"]["sha256"],
                "payload_sha256": package["binary_result"]["payload_sha256"],
                "payload_manifest_sha256": payload["manifest_sha256"],
            },
            "signing": {
                "public_key": {
                    "filename": self.public_key.name,
                    "size": len(self.key_bytes),
                    "sha256": digest(self.key_bytes),
                },
                "fingerprint": "a" * 40,
                "signature_scope": "Header",
                "signature_version": 4,
                "signature_algorithm": "RSA/SHA256",
                "signature_count": 1,
            },
            "policy": {
                "isolated_temporary_keyring": True,
                "legacy_signature_forbidden": True,
                "post_sign_payload_audit": True,
            },
        }
        self.write_receipt()
        self.rpmkeys = base / "rpmkeys"
        self.rpm = base / "rpm"
        self.rpm2archive = base / "rpm2archive"
        for tool in (self.rpmkeys, self.rpm, self.rpm2archive):
            tool.write_bytes(b"fixture executable\n")
            tool.chmod(0o755)
        self.header = {
            "name": package["name"],
            "version": package["version"],
            "release": package["release"],
            "arch": package["arch"],
            "buildhost": builder["macros"]["_buildhost"],
            "buildtime": builder["buildtime"],
            "payload_sha256": package["binary_result"]["payload_sha256"],
        }
        self.audit = {
            "payload_safe": True,
            "global_nopasswd_absent": True,
            "fan_controller_locked": True,
            "payload_file_count": payload["file_count"],
            "payload_manifest_sha256": payload["manifest_sha256"],
            "scriptlet_count": payload["scriptlet_count"],
            "scriptlets_sha256": payload["scriptlets_sha256"],
            "fan_controller_sha256": payload["fan_controller_sha256"],
        }

    def write_receipt(self) -> None:
        self.receipt_path.write_text(json.dumps(self.receipt), encoding="utf-8")

    def signature(self) -> dict[str, object]:
        return {
            "scope": "Header",
            "version": 4,
            "algorithm": "RSA/SHA256",
            "fingerprint": "a" * 40,
            "count": 1,
            "legacy_signature_absent": True,
            "header_sha256_digest": "pass",
            "payload_sha256_digest": "pass",
        }

    def evaluate(self) -> dict[str, object]:
        with (
            mock.patch.object(postsign, "verify_signature", return_value=self.signature()),
            mock.patch.object(postsign.repro, "query_header", return_value=self.header),
            mock.patch.object(postsign.auditor, "audit", return_value=self.audit),
        ):
            return postsign.evaluate(
                self.receipt_path,
                self.artifact,
                self.public_key,
                self.build_lock_path,
                self.source_lock_path,
                self.rpmkeys,
                self.rpm,
                self.rpm2archive,
            )


class PostSignTests(unittest.TestCase):
    def test_exact_signed_fixture_passes_artifact_gate_not_release_gate(self):
        with tempfile.TemporaryDirectory(prefix="pds020-postsign-") as name:
            report = Fixture(Path(name)).evaluate()
            self.assertTrue(report["signature_verified"])
            self.assertTrue(report["canonical_payload_verified"])
            self.assertTrue(report["artifact_ready_for_repository_staging"])
            self.assertFalse(report["release_ready"])

    def test_pds2_uses_the_same_gate_with_its_own_locks_and_payload(self):
        with tempfile.TemporaryDirectory(prefix="pds020-postsign-pds2-") as name:
            fixture = Fixture(Path(name), candidate="pds2")
            report = fixture.evaluate()
            self.assertTrue(report["signature_verified"])
            self.assertTrue(report["canonical_payload_verified"])
            self.assertEqual(fixture.build_lock["payload_audit"]["file_count"], 60)
            self.assertIn(".pds2.fc44.", fixture.artifact.name)
            self.assertFalse(report["release_ready"])

    def test_artifact_key_and_canonical_build_drift_fail_closed(self):
        for field in ("artifact", "public_key", "canonical"):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory(prefix="pds020-postsign-") as name:
                    fixture = Fixture(Path(name))
                    if field == "artifact":
                        fixture.artifact.write_bytes(b"changed signed rpm\n")
                    elif field == "public_key":
                        fixture.public_key.write_bytes(b"changed public key\n")
                    else:
                        fixture.receipt["canonical_build"]["payload_sha256"] = "0" * 64
                        fixture.write_receipt()
                    with self.assertRaisesRegex(
                        (postsign.PostSignError, postsign.repro.ReproError),
                        "receipt|build lock",
                    ):
                        fixture.evaluate()

    def test_receipt_schema_types_policy_and_json_are_strict(self):
        with tempfile.TemporaryDirectory(prefix="pds020-postsign-") as name:
            fixture = Fixture(Path(name))
            fixture.receipt["signing"]["signature_count"] = True
            fixture.write_receipt()
            with self.assertRaisesRegex(postsign.PostSignError, "signing receipt"):
                fixture.evaluate()
        with tempfile.TemporaryDirectory(prefix="pds020-postsign-") as name:
            fixture = Fixture(Path(name))
            fixture.receipt["policy"]["legacy_signature_forbidden"] = False
            fixture.write_receipt()
            with self.assertRaisesRegex(postsign.PostSignError, "policy"):
                fixture.evaluate()
        with self.assertRaisesRegex(postsign.PostSignError, "duplicate key"):
            postsign.strict_json(b'{"schema":1,"schema":2}', "fixture")
        with self.assertRaisesRegex(postsign.PostSignError, "non-finite"):
            postsign.strict_json(b'{"schema":NaN}', "fixture")

    def test_exact_rpmkeys_signature_output_is_parsed(self):
        artifact = Path("/private/signed.rpm")
        fingerprint = "a" * 40
        output = (
            f"{artifact}:\n"
            f"    Header OpenPGP V4 RSA/SHA256 signature, key fingerprint: "
            f"{fingerprint}: OK\n"
            "    Header SHA256 digest: OK\n"
            "    Payload SHA256 digest: OK\n"
        ).encode()
        report = postsign.parse_signature_output(
            output, artifact, fingerprint, 4, "RSA/SHA256"
        )
        self.assertEqual(report["count"], 1)
        self.assertTrue(report["legacy_signature_absent"])

    def test_unsigned_legacy_multiple_or_wrong_signature_fails_closed(self):
        artifact = Path("/private/signed.rpm")
        fingerprint = "a" * 40
        valid = [
            f"{artifact}:",
            f"    Header OpenPGP V4 RSA/SHA256 signature, key fingerprint: {fingerprint}: OK",
            "    Header SHA256 digest: OK",
            "    Payload SHA256 digest: OK",
        ]
        variants = (
            [valid[0], *valid[2:]],
            [*valid, f"    Legacy OpenPGP V4 RSA/SHA256 signature, key fingerprint: {fingerprint}: OK"],
            [valid[0], valid[1], valid[1], *valid[2:]],
            [valid[0], valid[1].replace(fingerprint, "b" * 40), *valid[2:]],
        )
        for lines in variants:
            with self.subTest(lines=lines):
                with self.assertRaises(postsign.PostSignError):
                    postsign.parse_signature_output(
                        ("\n".join(lines) + "\n").encode(),
                        artifact,
                        fingerprint,
                        4,
                        "RSA/SHA256",
                    )

    def test_header_and_post_sign_payload_audit_drift_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-postsign-") as name:
            fixture = Fixture(Path(name))
            fixture.header["buildtime"] += 1
            with self.assertRaisesRegex(postsign.PostSignError, "header"):
                fixture.evaluate()
        with tempfile.TemporaryDirectory(prefix="pds020-postsign-") as name:
            fixture = Fixture(Path(name))
            fixture.audit["global_nopasswd_absent"] = False
            with self.assertRaisesRegex(postsign.PostSignError, "payload audit"):
                fixture.evaluate()

    def test_unsafe_artifact_or_verifier_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="pds020-postsign-") as name:
            fixture = Fixture(Path(name))
            fixture.artifact.chmod(0o666)
            with self.assertRaisesRegex(postsign.repro.ReproError, "unsafe"):
                fixture.evaluate()
        with tempfile.TemporaryDirectory(prefix="pds020-postsign-") as name:
            base = Path(name)
            tool = base / "rpmkeys"
            tool.write_bytes(b"fixture\n")
            tool.chmod(0o777)
            with self.assertRaisesRegex(postsign.PostSignError, "unsafe"):
                postsign.safe_executable(tool)

    def test_artifact_change_during_verification_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="pds020-postsign-") as name:
            fixture = Fixture(Path(name))

            def drifting_audit(*_args: object) -> dict[str, object]:
                fixture.artifact.write_bytes(b"changed during verification\n")
                return fixture.audit

            with (
                mock.patch.object(
                    postsign, "verify_signature", return_value=fixture.signature()
                ),
                mock.patch.object(
                    postsign.repro, "query_header", return_value=fixture.header
                ),
                mock.patch.object(postsign.auditor, "audit", side_effect=drifting_audit),
            ):
                with self.assertRaisesRegex(postsign.PostSignError, "changed during"):
                    postsign.evaluate(
                        fixture.receipt_path,
                        fixture.artifact,
                        fixture.public_key,
                        fixture.build_lock_path,
                        fixture.source_lock_path,
                        fixture.rpmkeys,
                        fixture.rpm,
                        fixture.rpm2archive,
                    )

    def test_tool_cannot_sign_build_install_download_or_generate_keys(self):
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "rpmsign",
            "--addsign",
            "--resign",
            "rpm -i",
            "rpmbuild",
            "mock --",
            "gpg --gen",
            "sq key generate",
            "urllib.request",
            "requests",
            "socket",
            "shell=True",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('"--dbpath"', source)
        self.assertIn('"--import"', source)
        completed = subprocess.run(
            [sys.executable, os.fspath(SOURCE), "--help"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.assertNotIn("--sign", completed.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

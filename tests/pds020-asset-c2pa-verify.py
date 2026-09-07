#!/usr/bin/env python3
"""Offline tests for the wallpaper C2PA receipt verifier."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pds020-asset-c2pa-verify.py"
RECEIPT = ROOT / "components/assets/provenance/c2pa-receipt.json"
SPEC = importlib.util.spec_from_file_location("pds020_asset_c2pa_verify", SOURCE)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self, base: Path) -> None:
        self.root = base / "repository"
        self.asset_dir = self.root / "assets"
        self.provenance_dir = self.root / "components" / "assets" / "provenance"
        self.asset_dir.mkdir(parents=True)
        self.provenance_dir.mkdir(parents=True)
        self.asset = self.asset_dir / "fixture.png"
        self.asset_bytes = (
            verifier.PNG_SIGNATURE
            + struct.pack(">I", 13)
            + b"IHDR"
            + struct.pack(">II", 64, 48)
            + b"fixture payload\n"
        )
        self.asset.write_bytes(self.asset_bytes)
        self.settings = self.provenance_dir / "c2patool-settings.json"
        self.settings_bytes = (
            b'{"verify":{"ocsp_fetch":false,"remote_manifest_fetch":false}}\n'
        )
        self.settings.write_bytes(self.settings_bytes)
        self.signer_list = base / "C2PA-TRUST-LIST.pem"
        self.tsa_list = base / "C2PA-TSA-TRUST-LIST.pem"
        self.signer_list.write_bytes(b"synthetic signer trust\n")
        self.tsa_list.write_bytes(b"synthetic TSA trust\n")
        self.active_manifest = "urn:c2pa:fixture"
        self.report_path = base / "tool-report.json"
        self.report = self.valid_report()
        self.write_report()
        self.tool = base / "c2patool"
        self.tool.write_text(
            "#!/usr/bin/python3\n"
            "import pathlib, sys\n"
            "if '-V' in sys.argv:\n"
            "    print('c2patool 0.26.60')\n"
            "else:\n"
            "    print((pathlib.Path(__file__).parent / 'tool-report.json').read_text())\n",
            encoding="utf-8",
        )
        self.tool.chmod(0o700)
        self.receipt_path = self.provenance_dir / "c2pa-receipt.json"
        self.receipt = self.valid_receipt()
        self.write_receipt()

    def valid_report(self) -> dict:
        success_codes = [
            "timeStamp.validated",
            "signingCredential.trusted",
            "claimSignature.insideValidity",
            "claimSignature.validated",
            "assertion.hashedURI.match",
            "assertion.hashedURI.match",
            "assertion.hashedURI.match",
            "assertion.dataHash.match",
        ]
        return {
            "active_manifest": self.active_manifest,
            "manifests": {
                self.active_manifest: {
                    "claim_version": 2,
                    "claim_generator_info": [
                        {"name": "OpenAI Media Service API", "specVersion": "2.2.0"}
                    ],
                    "assertions": [
                        {
                            "label": "c2pa.actions.v2",
                            "data": {
                                "actions": [
                                    {
                                        "action": "c2pa.created",
                                        "softwareAgent": {
                                            "name": "gpt-image",
                                            "version": "2.0",
                                        },
                                        "digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia",
                                        "when": "2026-08-26T00:00:00Z",
                                    },
                                    {"action": "c2pa.converted"},
                                    {"action": "c2pa.watermarked.unbound"},
                                ]
                            },
                        }
                    ],
                    "signature_info": {
                        "alg": "Es256",
                        "issuer": "OpenAI OpCo, LLC",
                        "common_name": "OpenAI Media Service",
                        "time": "2026-08-26T00:00:00+00:00",
                    },
                }
            },
            "validation_state": "Trusted",
            "validation_status": None,
            "validation_results": {
                "activeManifest": {
                    "success": [{"code": code} for code in success_codes],
                    "failure": [],
                    "informational": [{"code": "timeStamp.untrusted"}],
                }
            },
        }

    def valid_receipt(self) -> dict:
        tool_bytes = self.tool.read_bytes()
        signer_bytes = self.signer_list.read_bytes()
        tsa_bytes = self.tsa_list.read_bytes()
        self.expected_tool_size = len(tool_bytes)
        self.expected_tool_sha256 = digest(tool_bytes)
        self.expected_signer_size = len(signer_bytes)
        self.expected_signer_sha256 = digest(signer_bytes)
        self.expected_tsa_size = len(tsa_bytes)
        self.expected_tsa_sha256 = digest(tsa_bytes)
        return {
            "schema": 1,
            "tool": {
                "name": "c2patool",
                "version": "0.26.60",
                "release": "https://github.com/contentauth/c2pa-rs/releases/tag/c2patool-v0.26.60",
                "archive": {
                    "filename": "c2patool-v0.26.60-universal-apple-darwin.zip",
                    "size": 19955218,
                    "sha256": "4ca9a8e0937ca28b9090c35e5af1a41c252023799b9bca07f27d974e9ea57beb",
                },
                "binary": {"size": len(tool_bytes), "sha256": digest(tool_bytes)},
                "settings": {
                    "path": "components/assets/provenance/c2patool-settings.json",
                    "size": len(self.settings_bytes),
                    "sha256": digest(self.settings_bytes),
                },
            },
            "trust": {
                "repository": "https://github.com/c2pa-org/conformance-public",
                "commit": "2466172859fad1215f7aaf7e3768b41a0ac29abc",
                "signer_list": {
                    "filename": self.signer_list.name,
                    "size": len(signer_bytes),
                    "sha256": digest(signer_bytes),
                },
                "tsa_list": {
                    "filename": self.tsa_list.name,
                    "size": len(tsa_bytes),
                    "sha256": digest(tsa_bytes),
                },
                "timestamp_trust_verified": False,
                "timestamp_note": "timeStamp.untrusted with separate TSA list unsupported",
            },
            "rights": {
                "c2pa_proves_content_integrity": True,
                "c2pa_proves_license": False,
                "human_author_identified": False,
                "license_identified": False,
                "redistribution_permission": False,
                "release_ready": False,
            },
            "assets": [
                {
                    "path": "assets/fixture.png",
                    "size": len(self.asset_bytes),
                    "sha256": digest(self.asset_bytes),
                    "width": 64,
                    "height": 48,
                    "active_manifest": self.active_manifest,
                    "claim_version": 2,
                    "claim_generator": "OpenAI Media Service API",
                    "software_agent": "gpt-image",
                    "software_version": "2.0",
                    "digital_source_type": "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia",
                    "created_time": "2026-08-26T00:00:00Z",
                    "signature_algorithm": "Es256",
                    "signature_issuer": "OpenAI OpCo, LLC",
                    "signature_common_name": "OpenAI Media Service",
                    "signature_time": "2026-08-26T00:00:00+00:00",
                    "validation_state": "Trusted",
                    "success_code_counts": {
                        "timeStamp.validated": 1,
                        "signingCredential.trusted": 1,
                        "claimSignature.insideValidity": 1,
                        "claimSignature.validated": 1,
                        "assertion.hashedURI.match": 3,
                        "assertion.dataHash.match": 1,
                    },
                    "failure_codes": [],
                    "informational_codes": ["timeStamp.untrusted"],
                }
            ],
        }

    def write_report(self) -> None:
        self.report_path.write_text(json.dumps(self.report), encoding="utf-8")

    def write_receipt(self) -> None:
        self.receipt_path.write_text(json.dumps(self.receipt), encoding="utf-8")

    def verify(self):
        with mock.patch.multiple(
            verifier,
            C2PATOOL_BINARY_SIZE=self.expected_tool_size,
            C2PATOOL_BINARY_SHA256=self.expected_tool_sha256,
            SIGNER_TRUST_LIST_SIZE=self.expected_signer_size,
            SIGNER_TRUST_LIST_SHA256=self.expected_signer_sha256,
            TSA_TRUST_LIST_SIZE=self.expected_tsa_size,
            TSA_TRUST_LIST_SHA256=self.expected_tsa_sha256,
        ):
            return verifier.verify(
                self.receipt_path,
                self.root,
                self.tool,
                self.signer_list,
                self.tsa_list,
            )


class VerifyTests(unittest.TestCase):
    def test_exact_fixture_verifies_without_rights_inference(self):
        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            asset = fixture.receipt["assets"][0]
            data = (fixture.root / asset["path"]).read_bytes()
            self.assertEqual(data, fixture.asset_bytes)
            self.assertEqual(len(data), asset["size"])
            self.assertEqual(digest(data), asset["sha256"])
            report = fixture.verify()
            self.assertTrue(report["provenance_verified"])
            self.assertFalse(report["timestamp_trust_verified"])
            self.assertFalse(report["redistribution_ready"])
            self.assertFalse(report["release_ready"])

    def test_asset_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            fixture.asset.write_bytes(fixture.asset_bytes + b"tamper")
            with self.assertRaisesRegex(verifier.VerifyError, "size mismatch"):
                fixture.verify()

    def test_same_size_asset_tamper_fails_hash_validation(self):
        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            fixture.asset.write_bytes(fixture.asset_bytes[:-1] + b"!")
            with self.assertRaisesRegex(verifier.VerifyError, "SHA-256 mismatch"):
                fixture.verify()

    def test_malformed_or_untrusted_tool_report_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            fixture.report["validation_state"] = "Valid"
            fixture.report["validation_status"] = [
                {"code": "signingCredential.untrusted"}
            ]
            fixture.write_report()
            with self.assertRaisesRegex(verifier.VerifyError, "trust state"):
                fixture.verify()
            fixture.report = fixture.valid_report()
            actions = fixture.report["manifests"][fixture.active_manifest]["assertions"][0]["data"]["actions"]
            actions[1] = "malformed"
            fixture.write_report()
            with self.assertRaisesRegex(verifier.VerifyError, "action chain"):
                fixture.verify()

    def test_duplicate_receipt_settings_and_report_keys_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            original = fixture.receipt_path.read_text(encoding="utf-8")
            fixture.receipt_path.write_text(
                '{"schema":999,' + original[1:],
                encoding="utf-8",
            )
            with self.assertRaisesRegex(verifier.VerifyError, "duplicate key"):
                fixture.verify()

        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            data = (
                b'{"verify":{"ocsp_fetch":true,"ocsp_fetch":false,'
                b'"remote_manifest_fetch":false}}\n'
            )
            fixture.settings.write_bytes(data)
            fixture.receipt["tool"]["settings"].update(
                {"size": len(data), "sha256": digest(data)}
            )
            fixture.write_receipt()
            with self.assertRaisesRegex(verifier.VerifyError, "duplicate key"):
                fixture.verify()

        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            original = fixture.report_path.read_text(encoding="utf-8")
            fixture.report_path.write_text(
                '{"validation_state":"Untrusted",' + original[1:],
                encoding="utf-8",
            )
            with self.assertRaisesRegex(verifier.VerifyError, "duplicate key"):
                fixture.verify()

    def test_json_must_be_utf8_finite_and_bounded_by_types(self):
        for value in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(verifier.VerifyError, "non-finite"):
                    verifier.strict_json(b'{"value":' + value + b"}", "fixture")
        with self.assertRaisesRegex(verifier.VerifyError, "UTF-8 JSON"):
            verifier.strict_json('{"schema":1}'.encode("utf-16"), "fixture")

        for field, value, error in (
            ("schema", True, "schema"),
            ("rights", 0, "overstates"),
            ("claim_version", True, "numeric fields"),
            ("width", 64.0, "numeric fields"),
        ):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
                    fixture = Fixture(Path(name))
                    if field == "schema":
                        fixture.receipt["schema"] = value
                    elif field == "rights":
                        for key, original in fixture.receipt["rights"].items():
                            fixture.receipt["rights"][key] = int(original)
                    else:
                        fixture.receipt["assets"][0][field] = value
                    fixture.write_receipt()
                    with self.assertRaisesRegex(verifier.VerifyError, error):
                        fixture.verify()

        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            data = b'{"verify":{"ocsp_fetch":0,"remote_manifest_fetch":0}}\n'
            fixture.settings.write_bytes(data)
            fixture.receipt["tool"]["settings"].update(
                {"size": len(data), "sha256": digest(data)}
            )
            fixture.write_receipt()
            with self.assertRaisesRegex(verifier.VerifyError, "offline validation"):
                fixture.verify()

    def test_trust_policy_cannot_be_redefined_by_receipt(self):
        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            fixture.report["validation_state"] = "Valid"
            fixture.receipt["assets"][0]["validation_state"] = "Valid"
            fixture.write_report()
            fixture.write_receipt()
            with self.assertRaisesRegex(verifier.VerifyError, "trust or numeric"):
                fixture.verify()

        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            failure = {"code": "claimSignature.invalid"}
            active = fixture.report["validation_results"]["activeManifest"]
            active["failure"].append(failure)
            fixture.receipt["assets"][0]["failure_codes"].append(failure["code"])
            fixture.write_report()
            fixture.write_receipt()
            with self.assertRaisesRegex(verifier.VerifyError, "validation codes"):
                fixture.verify()

        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            fixture.report["active_manifest"] = []
            fixture.write_report()
            with self.assertRaisesRegex(verifier.VerifyError, "active C2PA"):
                fixture.verify()

    def test_non_utf8_tool_output_is_a_controlled_failure(self):
        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            emitter = Path(name) / "emitter"
            emitter.write_text(
                "#!/usr/bin/python3\nimport sys\nsys.stdout.buffer.write(b'\\xff')\n",
                encoding="utf-8",
            )
            emitter.chmod(0o700)
            with self.assertRaisesRegex(verifier.VerifyError, "UTF-8 JSON"):
                verifier.run_json([str(emitter)])

    def test_tool_output_is_bounded_while_it_is_collected(self):
        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            emitter = Path(name) / "emitter"
            emitter.write_text(
                "#!/usr/bin/python3\nimport sys\nsys.stdout.write('x' * 2048)\n",
                encoding="utf-8",
            )
            emitter.chmod(0o700)
            with self.assertRaisesRegex(verifier.VerifyError, "output bound"):
                verifier.run_bounded(
                    [str(emitter)],
                    1024,
                    5,
                    "bounded fixture",
                )

    def test_creation_agent_drift_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            actions = fixture.report["manifests"][fixture.active_manifest]["assertions"][0]["data"]["actions"]
            actions[0]["softwareAgent"]["name"] = "unknown-generator"
            fixture.write_report()
            with self.assertRaisesRegex(verifier.VerifyError, "creation assertion"):
                fixture.verify()

    def test_unlisted_asset_and_rights_overstatement_fail(self):
        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            (fixture.asset_dir / "unlisted.png").write_bytes(fixture.asset_bytes)
            with self.assertRaisesRegex(verifier.VerifyError, "exactly cover"):
                fixture.verify()
            (fixture.asset_dir / "unlisted.png").unlink()
            fixture.receipt["rights"]["redistribution_permission"] = True
            fixture.write_receipt()
            with self.assertRaisesRegex(verifier.VerifyError, "overstates"):
                fixture.verify()

    def test_symlinked_trust_list_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            target = Path(name) / "real-signer.pem"
            fixture.signer_list.rename(target)
            os.symlink(target, fixture.signer_list)
            with self.assertRaisesRegex(verifier.VerifyError, "missing or unsafe"):
                fixture.verify()

    def test_receipt_cannot_reauthorize_tool_or_trust_material(self):
        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            fixture.tool.write_bytes(fixture.tool.read_bytes() + b"# replacement\n")
            tool_data = fixture.tool.read_bytes()
            fixture.receipt["tool"]["binary"].update(
                {"size": len(tool_data), "sha256": digest(tool_data)}
            )
            fixture.write_receipt()
            with self.assertRaisesRegex(verifier.VerifyError, "release identity"):
                fixture.verify()

        with tempfile.TemporaryDirectory(prefix="pds020-c2pa-") as name:
            fixture = Fixture(Path(name))
            fixture.signer_list.write_bytes(b"replacement signer trust\n")
            signer_data = fixture.signer_list.read_bytes()
            fixture.receipt["trust"]["signer_list"].update(
                {"size": len(signer_data), "sha256": digest(signer_data)}
            )
            fixture.write_receipt()
            with self.assertRaisesRegex(verifier.VerifyError, "trust-list record"):
                fixture.verify()

    def test_historical_receipt_identity_and_rights_remain_blocked(self):
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        self.assertEqual(
            receipt["tool"]["binary"],
            {
                "size": verifier.C2PATOOL_BINARY_SIZE,
                "sha256": verifier.C2PATOOL_BINARY_SHA256,
            },
        )
        self.assertEqual(
            receipt["trust"]["signer_list"]["size"],
            verifier.SIGNER_TRUST_LIST_SIZE,
        )
        self.assertEqual(
            receipt["trust"]["signer_list"]["sha256"],
            verifier.SIGNER_TRUST_LIST_SHA256,
        )
        self.assertEqual(
            receipt["trust"]["tsa_list"]["size"],
            verifier.TSA_TRUST_LIST_SIZE,
        )
        self.assertEqual(
            receipt["trust"]["tsa_list"]["sha256"],
            verifier.TSA_TRUST_LIST_SHA256,
        )
        self.assertFalse(receipt["rights"]["release_ready"])
        self.assertFalse(receipt["rights"]["c2pa_proves_license"])
        self.assertFalse(receipt["rights"]["redistribution_permission"])
        # Source exports intentionally omit these private assets. Pin the
        # historical record without claiming to verify absent image bytes.
        # Actual byte validation and tamper rejection use Fixture above.
        self.assertEqual(
            [{key: asset[key] for key in ("path", "size", "sha256")}
             for asset in receipt["assets"]],
            [
                {
                    "path": "assets/wallpapers/neko-bass-upper.png",
                    "size": 2259093,
                    "sha256": "e95ac3d4098a675b4029bbd004eebccae6fb10b36a3a9bbf4c01380063b4fa2c",
                },
                {
                    "path": "assets/wallpapers/tendou-kei-lower.png",
                    "size": 1787826,
                    "sha256": "892a6cb49a8c870288a387af3bf0de8c21b75b660d354800349d20cf843d4be2",
                },
            ],
        )

    def test_tool_has_no_download_install_or_write_path(self):
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "urllib.request",
            "requests",
            "socket",
            "curl ",
            "wget ",
            "dnf ",
            "brew ",
            "shell=True",
            "os.remove",
            "os.unlink",
            "shutil",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('"network": False', source)
        self.assertIn('"redistribution_ready": False', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

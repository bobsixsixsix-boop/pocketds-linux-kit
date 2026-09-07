#!/usr/bin/env python3
"""Offline tests for the future signed pds2 repository receipt gate."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pds020-userspace-rpm-repository-postsign.py"
ARCHIVE_LOCK = ROOT / "packaging/pocketds-userspace/rootfs-package-archives-lock.pds2.json"
REPOSITORY_LOCK = ROOT / "packaging/pocketds-userspace/repository-metadata-lock.pds2.json"
BUILD_LOCK = ROOT / "packaging/pocketds-userspace/build-lock.pds2.json"
SOURCE_LOCK = ROOT / "packaging/pocketds-userspace/source-lock.pds2.json"
SPEC = importlib.util.spec_from_file_location("pds020_repository_postsign_test", SOURCE)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self, base: Path) -> None:
        self.base = base
        for name in ("unsigned", "signed", "repo-a", "repo-b"):
            (base / name).mkdir(mode=0o700)
        self.unsigned_archive_dir = base / "unsigned"
        self.signed_archive_dir = base / "signed"
        self.result_a = base / "repo-a"
        self.result_b = base / "repo-b"
        self.build = json.loads(BUILD_LOCK.read_text(encoding="utf-8"))
        self.archive = gate.archive.load_lock(ARCHIVE_LOCK)
        self.unsigned_repository = gate.repository.load_lock(REPOSITORY_LOCK)
        project = next(
            record
            for record in self.archive["packages"]
            if record["origin"] == "project-pre-sign"
        )
        self.signed_rpm = base / project["filename"]
        self.signed_rpm_bytes = b"synthetic signed pds2 rpm\n"
        self.signed_rpm.write_bytes(self.signed_rpm_bytes)
        self.public_key = base / "project-release-key.asc"
        self.key_bytes = b"synthetic public certificate\n"
        self.public_key.write_bytes(self.key_bytes)
        self.signature = self.result_a / "repomd.xml.asc"
        self.signature_bytes = b"synthetic detached signature\n"
        self.signature.write_bytes(self.signature_bytes)
        (self.result_b / self.signature.name).write_bytes(self.signature_bytes)
        self.primary_fingerprint = "a" * 40
        self.signing_fingerprint = "b" * 40
        self.rpm_receipt_path = base / "pds2-post-sign-receipt.json"
        package = self.build["package"]
        payload = self.build["payload_audit"]
        self.rpm_receipt = {
            "schema": 1,
            "artifact": self.artifact(self.signed_rpm.name, self.signed_rpm_bytes),
            "canonical_build": {
                "build_lock_sha256": digest(BUILD_LOCK.read_bytes()),
                "pre_sign_binary_sha256": package["binary_result"]["sha256"],
                "payload_sha256": package["binary_result"]["payload_sha256"],
                "payload_manifest_sha256": payload["manifest_sha256"],
            },
            "signing": {
                "public_key": self.artifact(self.public_key.name, self.key_bytes),
                "fingerprint": self.primary_fingerprint,
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
        self.write_json(self.rpm_receipt_path, self.rpm_receipt)
        self.repository = copy.deepcopy(self.unsigned_repository["repository"])
        self.repository["repomd"] = {
            "filename": "repomd.xml",
            "size": 1500,
            "sha256": "1" * 64,
        }
        replacements = {
            "filelists": ("2", "5", 190000, 2500000),
            "other": ("3", "6", 67000, 640000),
            "primary": ("4", "7", 104000, 900000),
        }
        metadata = []
        for kind in ("filelists", "other", "primary"):
            compressed, opened, size, open_size = replacements[kind]
            sha = compressed * 64
            metadata.append(
                {
                    "type": kind,
                    "filename": f"{sha}-{kind}.xml.xz",
                    "size": size,
                    "sha256": sha,
                    "open_size": open_size,
                    "open_sha256": opened * 64,
                }
            )
        self.repository["metadata"] = metadata
        self.repository["primary_location_checksum_sha256"] = "8" * 64
        self.project_binding = {
            "receipt": self.artifact(
                self.rpm_receipt_path.name, self.rpm_receipt_path.read_bytes()
            ),
            "signed_rpm": self.rpm_receipt["artifact"],
        }
        _, final_aggregate = gate.derive_final_packages(
            self.archive,
            self.project_binding["signed_rpm"],
            self.primary_fingerprint,
        )
        self.receipt_path = base / "repository-post-sign-receipt.json"
        self.receipt = {
            "schema": 1,
            "unsigned_inputs": {
                "archive_lock_sha256": digest(ARCHIVE_LOCK.read_bytes()),
                "repository_lock_sha256": digest(REPOSITORY_LOCK.read_bytes()),
                "build_lock_sha256": digest(BUILD_LOCK.read_bytes()),
            },
            "project_post_sign": self.project_binding,
            "final_archive": final_aggregate,
            "generator": self.unsigned_repository["generator"],
            "repository": self.repository,
            "repository_signature": {
                "artifact": self.artifact(self.signature.name, self.signature_bytes),
                "public_key": self.rpm_receipt["signing"]["public_key"],
                "certificate_primary_fingerprint": self.primary_fingerprint,
                "signing_fingerprint": self.signing_fingerprint,
                "signature_version": 4,
                "hash_algorithm": "SHA256",
                "signature_count": 1,
            },
            "sequence": copy.deepcopy(gate.SEQUENCE),
            "policy": copy.deepcopy(gate.POLICY),
        }
        self.write_receipt()

    @staticmethod
    def artifact(filename: str, data: bytes) -> dict[str, object]:
        return {"filename": filename, "size": len(data), "sha256": digest(data)}

    @staticmethod
    def write_json(path: Path, value: object) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    def write_receipt(self) -> None:
        self.write_json(self.receipt_path, self.receipt)

    def evaluate(self) -> dict[str, object]:
        rpm_report = {
            "signature_verified": True,
            "canonical_payload_verified": True,
            "artifact_ready_for_repository_staging": True,
            "release_ready": False,
        }
        metadata_report = {
            "repomd_sha256": self.repository["repomd"]["sha256"],
            "metadata_file_count": 3,
            "package_count": 322,
            "primary_location_checksum_sha256": self.repository[
                "primary_location_checksum_sha256"
            ],
        }
        signature_report = {
            "signature_count": 1,
            "signature_version": 4,
            "hash_algorithm": "SHA256",
            "signing_fingerprint": self.signing_fingerprint,
            "certificate_primary_fingerprint": self.primary_fingerprint,
        }
        with (
            mock.patch.object(gate.postsign, "evaluate", return_value=rpm_report),
            mock.patch.object(gate.archive, "verify", return_value={"verified": True}),
            mock.patch.object(gate, "verify_final_archive"),
            mock.patch.object(gate.repository, "verify_generator"),
            mock.patch.object(
                gate.repository, "verify_result", return_value=metadata_report
            ),
            mock.patch.object(
                gate, "verify_detached_signature", return_value=signature_report
            ),
        ):
            return gate.evaluate(
                self.receipt_path,
                self.rpm_receipt_path,
                self.signed_rpm,
                self.public_key,
                self.signature,
                self.unsigned_archive_dir,
                self.signed_archive_dir,
                self.result_a,
                self.result_b,
                ARCHIVE_LOCK,
                REPOSITORY_LOCK,
                BUILD_LOCK,
                SOURCE_LOCK,
                Path("/usr/bin/createrepo_c"),
                Path("/usr/bin/gpg"),
                Path("/usr/bin/rpmkeys"),
                Path("/usr/bin/rpm"),
                Path("/usr/bin/rpm2archive"),
            )


class RepositoryPostSignTests(unittest.TestCase):
    def test_future_chain_passes_artifact_gate_without_release_overclaim(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-repository-postsign-") as name:
            fixture = Fixture(Path(name))
            report = fixture.evaluate()
            self.assertTrue(report["project_rpm_signature_verified"])
            self.assertTrue(report["metadata_reproducible"])
            self.assertTrue(report["detached_repomd_signature_verified"])
            self.assertTrue(report["repository_ready_for_composition"])
            self.assertFalse(report["dnf5_repo_gpgcheck_smoke"])
            self.assertFalse(report["release_ready"])
            outside = fixture.base / "outside-repomd.xml.asc"
            outside.write_bytes(fixture.signature_bytes)
            fixture.signature = outside
            with self.assertRaisesRegex(
                gate.RepositoryPostSignError, "inside the first repodata"
            ):
                fixture.evaluate()

    def test_receipt_sequence_policy_types_and_json_are_strict(self) -> None:
        for mutation, error in (
            (lambda value: value["sequence"].reverse(), "sequence"),
            (
                lambda value: value["policy"].update(release_ready=True),
                "policy",
            ),
            (
                lambda value: value["final_archive"].update(package_count=True),
                "final archive",
            ),
        ):
            with self.subTest(error=error):
                with tempfile.TemporaryDirectory(
                    prefix="pds020-repository-postsign-"
                ) as name:
                    fixture = Fixture(Path(name))
                    mutation(fixture.receipt)
                    fixture.write_receipt()
                    with self.assertRaisesRegex(gate.RepositoryPostSignError, error):
                        gate.load_receipt(fixture.receipt_path)
        with self.assertRaisesRegex(gate.repro.ReproError, "duplicate key"):
            with tempfile.TemporaryDirectory(
                prefix="pds020-repository-postsign-json-"
            ) as name:
                path = Path(name) / "receipt.json"
                path.write_bytes(b'{"schema":1,"schema":2}')
                gate.load_receipt(path)

    def test_final_package_set_replaces_only_the_project_container(self) -> None:
        unsigned = {
            "fedora_package_count": 1,
            "packages": [
                {
                    "filename": "a.rpm",
                    "origin": "fedora",
                    "size": 10,
                    "sha256": "1" * 64,
                    "payload_sha256": "2" * 64,
                    "signature_verified": True,
                    "signing_key_fingerprint": "c" * 40,
                },
                {
                    "filename": "project.rpm",
                    "origin": "project-pre-sign",
                    "size": 20,
                    "sha256": "3" * 64,
                    "payload_sha256": "4" * 64,
                    "signature_verified": False,
                    "signing_key_fingerprint": None,
                },
            ],
        }
        final, aggregate = gate.derive_final_packages(
            unsigned,
            {"filename": "project.rpm", "size": 24, "sha256": "5" * 64},
            "d" * 40,
        )
        self.assertEqual(unsigned["packages"][1]["origin"], "project-pre-sign")
        self.assertEqual(final["packages"][0], unsigned["packages"][0])
        self.assertEqual(final["packages"][1]["origin"], "project-post-sign")
        self.assertEqual(final["packages"][1]["payload_sha256"], "4" * 64)
        self.assertTrue(final["packages"][1]["signature_verified"])
        self.assertEqual(aggregate["total_bytes"], 34)

    def test_final_archive_is_exact_private_and_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-final-archive-") as name:
            directory = Path(name)
            directory.chmod(0o700)
            data = b"rpm"
            (directory / "one.rpm").write_bytes(data)
            lock = {
                "packages": [
                    {
                        "filename": "one.rpm",
                        "size": len(data),
                        "sha256": digest(data),
                    }
                ]
            }
            gate.verify_final_archive(directory, lock)
            (directory / "extra.rpm").write_bytes(b"x")
            with self.assertRaisesRegex(gate.archive.ArchiveError, "extra"):
                gate.verify_final_archive(directory, lock)

    def test_public_certificate_parser_rejects_secret_or_multiple_keys(self) -> None:
        valid = f"pub:u:1:1:key::::::\nfpr:::::::::{'A' * 40}:\n".encode()
        self.assertEqual(gate.primary_fingerprint(valid), "a" * 40)
        for data in (
            valid + b"pub:u:1:1:other::::::\n",
            valid + b"sec:u:1:1:secret::::::\n",
            b"pub:u:1:1:key::::::\n",
        ):
            with self.subTest(data=data):
                with self.assertRaises(gate.RepositoryPostSignError):
                    gate.primary_fingerprint(data)

    def test_detached_signature_status_binds_signer_primary_version_and_hash(self) -> None:
        signature = {
            "signing_fingerprint": "b" * 40,
            "certificate_primary_fingerprint": "a" * 40,
            "signature_version": 4,
            "hash_algorithm": "SHA256",
        }
        status = (
            "[GNUPG:] NEWSIG\n"
            "[GNUPG:] GOODSIG 0000000000000000 release\n"
            f"[GNUPG:] VALIDSIG {'b' * 40} 2026-08-28 1787875200 0 4 0 1 8 00 {'a' * 40}\n"
        ).encode()
        report = gate.parse_signature_status(status, signature)
        self.assertEqual(report["signature_count"], 1)
        self.assertEqual(report["hash_algorithm"], "SHA256")

    def test_bad_multiple_expired_or_wrong_signature_status_fails(self) -> None:
        signature = {
            "signing_fingerprint": "b" * 40,
            "certificate_primary_fingerprint": "a" * 40,
            "signature_version": 4,
            "hash_algorithm": "SHA256",
        }
        valid = (
            "[GNUPG:] NEWSIG\n"
            "[GNUPG:] GOODSIG 0000000000000000 release\n"
            f"[GNUPG:] VALIDSIG {'b' * 40} 2026-08-28 1787875200 0 4 0 1 8 00 {'a' * 40}\n"
        )
        variants = (
            valid + "[GNUPG:] BADSIG bad\n",
            valid + valid,
            valid.replace(" 0 4 0 1 8 00 ", " 0 4 0 1 2 00 "),
            valid.replace("a" * 40, "c" * 40),
        )
        for status in variants:
            with self.subTest(status=status):
                with self.assertRaises(gate.RepositoryPostSignError):
                    gate.parse_signature_status(status.encode(), signature)

    def test_rpm_and_repository_must_use_the_same_public_certificate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-repository-postsign-") as name:
            fixture = Fixture(Path(name))
            fixture.receipt["repository_signature"][
                "certificate_primary_fingerprint"
            ] = "c" * 40
            fixture.write_receipt()
            with self.assertRaisesRegex(
                gate.RepositoryPostSignError, "signing identities"
            ):
                fixture.evaluate()

    def test_unsigned_repodata_cannot_be_relabelled_as_final(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-repository-postsign-") as name:
            fixture = Fixture(Path(name))
            fixture.receipt["repository"] = fixture.unsigned_repository["repository"]
            fixture.write_receipt()
            with self.assertRaisesRegex(
                gate.RepositoryPostSignError, "cannot be release-signed"
            ):
                fixture.evaluate()

    def test_verifier_cannot_sign_generate_keys_download_install_or_build(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "shell=True",
            "urlopen",
            "requests.",
            "curl ",
            "wget ",
            '"--sign"',
            '"--quick-generate-key"',
            '"--generate-key"',
            '"install",',
            "dnf ",
            "write_text(",
            "write_bytes(",
            "os.O_WRONLY",
        ):
            self.assertNotIn(forbidden, source)
        for required in (
            '"rpm_signed_before_metadata": True',
            '"--show-keys"',
            '"import-minimal"',
            '"--no-auto-key-retrieve"',
            '"detached_repomd_signature_verified": True',
            '"dnf5_repo_gpgcheck_smoke": False',
            '"release_ready": False',
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

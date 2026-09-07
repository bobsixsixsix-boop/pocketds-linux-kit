#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "kernel-ab" / "source-evidence.py"
MODULE_SPEC = importlib.util.spec_from_file_location("pocketds_kernel_source_evidence", SOURCE)
assert MODULE_SPEC and MODULE_SPEC.loader
source_evidence = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = source_evidence
MODULE_SPEC.loader.exec_module(source_evidence)


SRPM_ROOT = "linux-pocketds-v7.1-rc2"
COMMIT_ROOT = "linux-" + ("a" * 40)
FINGERPRINT = "A" * 40


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(65_536):
            digest.update(block)
    return digest.hexdigest()


def write_tree(path: Path, root: str, *, changed: bool = False, unsafe: str | None = None) -> None:
    with tarfile.open(path, "w:gz") as archive:
        directory = tarfile.TarInfo(root + "/")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        archive.addfile(directory)

        readme = b"different\n" if changed else b"fixture\n"
        item = tarfile.TarInfo(root + "/README.md")
        item.mode = 0o644
        item.size = len(readme)
        archive.addfile(item, io.BytesIO(readme))

        script = b"#!/bin/sh\nexit 0\n"
        item = tarfile.TarInfo(root + "/scripts/check.sh")
        item.mode = 0o755
        item.size = len(script)
        archive.addfile(item, io.BytesIO(script))

        link = tarfile.TarInfo(root + "/README.link")
        link.type = tarfile.SYMTYPE
        link.mode = 0o777
        link.linkname = unsafe if unsafe is not None else "README.md"
        archive.addfile(link)


def rpm_header(entries: list[tuple[int, int, int, int]], store: bytes) -> bytes:
    content = bytearray(source_evidence.RPM_HEADER_MAGIC + b"\x01" + (b"\0" * 4))
    content.extend(len(entries).to_bytes(4, "big"))
    content.extend(len(store).to_bytes(4, "big"))
    for entry in entries:
        for value in entry:
            content.extend(value.to_bytes(4, "big"))
    content.extend(store)
    return bytes(content)


def write_rpm(path: Path, *, signature_tag: int = source_evidence.RPM_SIGNATURE_TAG_RSA) -> None:
    lead = bytearray(source_evidence.RPM_LEAD_BYTES)
    lead[:4] = source_evidence.RPM_LEAD_MAGIC
    lead[4] = 3
    signature = b"synthetic-openpgp-signature"
    signature_header = rpm_header(
        [(signature_tag, source_evidence.RPM_BIN_TYPE, 0, len(signature))],
        signature,
    )
    content = bytes(lead) + signature_header
    content += b"\0" * ((-len(content)) % 8)
    content += rpm_header([], b"") + b"synthetic-payload"
    path.write_bytes(content)


def verified_signatures(
    _paths: dict[str, Path], fingerprint: str
) -> dict[str, dict[str, object]]:
    return {
        name: {"verified": True, "signing_fingerprint": fingerprint}
        for name in ("binary_rpm", "source_rpm")
    }


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.paths: dict[str, Path] = {
            "repomd": root / "repomd.xml",
            "primary": root / "primary.xml.gz",
            "binary_rpm": root / "kernel.rpm",
            "source_rpm": root / "kernel.src.rpm",
            "srpm_source_archive": root / "srpm.tar.gz",
            "commit_archive": root / "commit.tar.gz",
            "lid_source_patch": root / "native-lid.patch",
            "copr_keyring": root / "pubkey.gpg",
            "sqv": root / "sqv",
            "rebuilt_dtb": root / "native-lid.dtb",
        }
        for name in (
            "repomd",
            "primary",
            "lid_source_patch",
            "copr_keyring",
            "sqv",
            "rebuilt_dtb",
        ):
            self.paths[name].write_bytes((name + "\n").encode())
        self.paths["sqv"].chmod(0o755)
        write_rpm(self.paths["binary_rpm"])
        write_rpm(self.paths["source_rpm"])
        write_tree(self.paths["srpm_source_archive"], SRPM_ROOT)
        write_tree(self.paths["commit_archive"], COMMIT_ROOT)
        _tree, tree_sha = source_evidence._scan_tree(
            self.paths["srpm_source_archive"], SRPM_ROOT
        )
        self.spec: dict[str, object] = {
            "schema_version": 1,
            "expected_source_commit": "a" * 40,
            "expected_source_tree_sha256": tree_sha,
            "expected_signing_fingerprint": FINGERPRINT,
            "copr_build_id": 42,
            "package_nevra": "kernel-fixture.aarch64",
            "srpm_archive_root": SRPM_ROOT,
            "commit_archive_root": COMMIT_ROOT,
            "artifacts": {},
        }
        for name, path in self.paths.items():
            self.spec["artifacts"][name] = {
                "filename": path.name,
                "sha256": sha256(path),
                "size": path.stat().st_size,
            }

    def relock(self, name: str) -> None:
        path = self.paths[name]
        self.spec["artifacts"][name].update(
            {"sha256": sha256(path), "size": path.stat().st_size}
        )

    def collect(self, raw: dict[str, object] | None = None) -> dict[str, object]:
        return source_evidence.collect(
            self.spec if raw is None else raw,
            self.paths,
            signature_verifier=verified_signatures,
        )


class SourceEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_matching_snapshot_is_measured_without_overclaiming_provenance(self) -> None:
        report = self.fixture.collect()
        self.assertTrue(report["source_tree"]["matches_expected_commit"])
        self.assertEqual(report["source_tree"]["entry_count"], 3)
        self.assertTrue(report["gates"]["rpm_archive_signature_verified"])
        self.assertFalse(report["gates"]["source_to_binary_reproducible_build_proven"])
        self.assertTrue(report["gates"]["custom_dtb_rebuild_reproduced"])
        self.assertTrue(report["gates"]["custom_dtb_source_patch_locked"])
        self.assertFalse(report["source_to_binary_provenance_complete"])
        self.assertTrue(report["read_only"])
        self.assertFalse(report["network"])

    def test_archive_tamper_is_rejected_before_tree_comparison(self) -> None:
        self.fixture.paths["binary_rpm"].write_bytes(b"tampered\n")
        with self.assertRaisesRegex(source_evidence.SourceEvidenceError, "size, type, or mode"):
            self.fixture.collect()

    def test_source_tree_difference_is_rejected(self) -> None:
        write_tree(self.fixture.paths["commit_archive"], COMMIT_ROOT, changed=True)
        self.fixture.relock("commit_archive")
        with self.assertRaisesRegex(source_evidence.SourceEvidenceError, "source tree differs"):
            self.fixture.collect()

    def test_symlink_escape_is_rejected(self) -> None:
        write_tree(self.fixture.paths["commit_archive"], COMMIT_ROOT, unsafe="../../outside")
        self.fixture.relock("commit_archive")
        with self.assertRaisesRegex(source_evidence.SourceEvidenceError, "symlink escapes"):
            self.fixture.collect()

    def test_unknown_spec_field_is_rejected(self) -> None:
        raw = copy.deepcopy(self.fixture.spec)
        raw["helpful"] = True
        with self.assertRaisesRegex(source_evidence.SourceEvidenceError, "spec fields differ"):
            self.fixture.collect(raw)

    def test_rpm_parser_extracts_tag_268_and_exact_main_header(self) -> None:
        signature, signed_header, metadata = source_evidence.rpm_signature_material(
            self.fixture.paths["binary_rpm"]
        )
        self.assertEqual(signature, b"synthetic-openpgp-signature")
        self.assertEqual(signed_header, rpm_header([], b""))
        self.assertEqual(metadata["signature_tag"], 268)

    def test_rpm_parser_rejects_missing_header_signature(self) -> None:
        write_rpm(self.fixture.paths["binary_rpm"], signature_tag=1000)
        with self.assertRaisesRegex(source_evidence.SourceEvidenceError, "no unique RSA"):
            source_evidence.rpm_signature_material(self.fixture.paths["binary_rpm"])

    def test_spec_parser_rejects_duplicate_json_keys(self) -> None:
        path = Path(self.temporary.name) / "duplicate.json"
        path.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")
        with self.assertRaisesRegex(source_evidence.SourceEvidenceError, "duplicate JSON key"):
            source_evidence.load_spec(path)

    def test_cli_exposes_no_output_network_or_mutation_switch(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SOURCE), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        for forbidden in ("--output", "--download", "--install", "--build", "--root"):
            self.assertNotIn(forbidden, result.stdout)
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in ("requests", "urllib", "os.system", "shell=True"):
            self.assertNotIn(forbidden, source)
        self.assertIn('"read_only": True', source)
        self.assertIn('"network": False', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

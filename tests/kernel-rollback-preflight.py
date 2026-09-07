#!/usr/bin/env python3

from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "kernel-ab" / "rollback" / "preflight.py"
MODULE_SPEC = importlib.util.spec_from_file_location("pocketds_rollback_preflight", SOURCE)
assert MODULE_SPEC and MODULE_SPEC.loader
preflight = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = preflight
MODULE_SPEC.loader.exec_module(preflight)


def record(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {"filename": path.name, "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}


def make_boot(raw: bytes, dtb: bytes, image_id: bytes) -> tuple[bytes, bytes]:
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    kernel = compressed + dtb
    page = 2048
    header = bytearray(page)
    header[:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, len(kernel))
    struct.pack_into("<I", header, 36, page)
    header[576:608] = image_id
    return bytes(header) + kernel + bytes((-len(kernel)) % page), compressed


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.rollback_dir = root / "rollback"
        self.baseline_dir = root / "packages"
        self.reference_root = root / "reference"
        self.rebuilt_root = root / "rebuilt"
        for directory in (self.rollback_dir, self.baseline_dir, self.reference_root, self.rebuilt_root):
            directory.mkdir()
        self.release = "fixture.aarch64"
        self.raw = bytes(range(128))
        self.package_dtb = b"package-dtb" * 8
        self.dtb = b"custom-dtb" * 9
        self.image_id = bytes(range(32))
        self.boot, compressed = make_boot(self.raw, self.dtb, self.image_id)
        self.package_boot, _package_compressed = make_boot(
            self.raw, self.package_dtb, bytes(reversed(range(32)))
        )
        boot_path = f"boot/Image-{self.release}"
        raw_path = f"lib/modules/{self.release}/Image"
        for tree in (self.reference_root, self.rebuilt_root):
            (tree / boot_path).parent.mkdir(parents=True)
            (tree / boot_path).write_bytes(self.package_boot)
            (tree / raw_path).parent.mkdir(parents=True)
            (tree / raw_path).write_bytes(self.raw)
            (tree / "boot/config-fixture").write_bytes(b"config\n")
        self.records, summary = preflight.payload.scan_tree(self.reference_root)
        rebuilt_records, rebuilt_summary = preflight.payload.scan_tree(self.rebuilt_root)
        assert self.records == rebuilt_records and summary == rebuilt_summary

        rollback_path = self.rollback_dir / "rollback.img"
        rollback_path.write_bytes(self.boot)
        custom_dtb_path = self.rollback_dir / "custom.dtb"
        custom_dtb_path.write_bytes(self.dtb)
        signed_path = self.baseline_dir / "reference.rpm"
        rebuilt_path = self.baseline_dir / "rebuilt.rpm"
        signed_path.write_bytes(b"signed-rpm\n")
        rebuilt_path.write_bytes(b"rebuilt-rpm\n")
        source_commit = "a" * 40
        nevra = "kernel-" + self.release
        key_files = {
            "boot_image": {"path": boot_path, "sha256": hashlib.sha256(self.package_boot).hexdigest(), "size": len(self.package_boot)},
            "raw_image": {"path": raw_path, "sha256": hashlib.sha256(self.raw).hexdigest(), "size": len(self.raw)},
        }
        report_payload = {**summary, "key_files": key_files}
        baseline_report = {
            "schema": preflight.payload.REPORT_SCHEMA,
            "read_only": True,
            "network": False,
            "package": {"nevra": nevra, "source_commit": source_commit},
            "reference_payload": report_payload,
            "rebuilt_payload": report_payload,
            "gates": {
                "signed_reference_hash_bound": True,
                "source_and_toolchain_inputs_locked": True,
                "attested_rebuild_rpm_locked": True,
                "complete_kernel_payload_reproduced": True,
                "source_to_binary_reproducible_build_proven": True,
            },
        }
        candidate_report = {
            "schema": preflight.ifpc.REPORT_SCHEMA,
            "read_only": True,
            "network": False,
            "experiment": {"source_commit": source_commit, "package_nevra": nevra},
            "gates": {
                "candidate_built": True,
                "source_inputs_except_revert_identical": True,
                "single_variable_payload_delta_proven": True,
                "candidate_independently_reproduced": True,
                "rollback_artifact_prevalidated": False,
                "candidate_install_authorized": False,
            },
        }
        runtime_report = {
            "schema": "pocketds.kernel-ab-runtime-evidence.v3",
            "device_id": "fixture-device",
            "expected_source_commit": source_commit,
            "runtime": {"kernel_release": self.release},
            "artifacts": {
                "boot_images": [
                    {"sha256": hashlib.sha256(self.boot).hexdigest(), "size": len(self.boot)}
                    for _index in range(3)
                ],
                "boot_container": {
                    "format": "android-bootimg-v0",
                    "page_size": 2048,
                    "kernel_blob_size": len(compressed) + len(self.dtb),
                    "decompressed_kernel_sha256": hashlib.sha256(self.raw).hexdigest(),
                    "decompressed_kernel_size": len(self.raw),
                    "appended_dtb_sha256": hashlib.sha256(self.dtb).hexdigest(),
                    "appended_dtb_size": len(self.dtb),
                },
                "boot_dtb": {"sha256": hashlib.sha256(self.dtb).hexdigest(), "size": len(self.dtb)},
                "kernel_image": {"sha256": hashlib.sha256(self.raw).hexdigest(), "size": len(self.raw)},
            },
            "gates": {
                "boot_container_appended_dtb_valid": True,
                "boot_container_dtb_mirror_matches": True,
                "boot_container_matches_raw_kernel": True,
                "boot_image_aliases_match": True,
                "config_mirror_matches": True,
                "firmware_set_measured": True,
                "machine_identity_matches": True,
                "runtime_kernel_release_matches": True,
                "prevalidated_rollback_artifact": False,
                "runtime_source_commit_cryptographically_bound": False,
            },
            "production_plan_ready": False,
        }
        source_report = {
            "schema": "pocketds.kernel-source-evidence.v1",
            "read_only": True,
            "network": False,
            "expected_source_commit": source_commit,
            "package": {"nevra": nevra},
            "artifacts": {
                "rebuilt_dtb": {"sha256": hashlib.sha256(self.dtb).hexdigest(), "size": len(self.dtb)}
            },
            "gates": {
                "commit_archive_locked": True,
                "copr_metadata_artifacts_locked": True,
                "custom_dtb_rebuild_reproduced": True,
                "custom_dtb_source_patch_locked": True,
                "published_binary_rpm_locked": True,
                "published_source_rpm_locked": True,
                "rpm_archive_signature_verified": True,
                "source_snapshot_matches_expected_commit_tree": True,
                "srpm_source_member_locked": True,
                "source_to_binary_reproducible_build_proven": False,
            },
            "source_to_binary_provenance_complete": False,
        }
        self.baseline_report_path = self.rollback_dir / "baseline.json"
        self.candidate_report_path = self.rollback_dir / "candidate.json"
        self.runtime_report_path = self.rollback_dir / "runtime.json"
        self.source_report_path = self.rollback_dir / "source.json"
        self.baseline_report_path.write_text(json.dumps(baseline_report, sort_keys=True), encoding="utf-8")
        self.candidate_report_path.write_text(json.dumps(candidate_report, sort_keys=True), encoding="utf-8")
        self.runtime_report_path.write_text(json.dumps(runtime_report, sort_keys=True), encoding="utf-8")
        self.source_report_path.write_text(json.dumps(source_report, sort_keys=True), encoding="utf-8")
        self.lock = {
            "schema_version": 1,
            "artifact_id": "fixture-rollback",
            "device_id": "fixture-device",
            "kernel_release": self.release,
            "source_commit": source_commit,
            "artifacts": {
                "rollback_bootimg": record(rollback_path),
                "runtime_report": record(self.runtime_report_path),
                "source_report": record(self.source_report_path),
                "custom_dtb": record(custom_dtb_path),
                "baseline_payload_report": record(self.baseline_report_path),
                "candidate_report": record(self.candidate_report_path),
            },
            "baseline_packages": {
                "signed_reference_rpm": record(signed_path),
                "attested_rebuilt_rpm": record(rebuilt_path),
            },
            "payload": {
                **summary,
                "rpm_entry_count": summary["entry_count"],
                "implicit_directories": [],
                "package_boot_image_path": boot_path,
                "package_boot_image_sha256": hashlib.sha256(self.package_boot).hexdigest(),
                "raw_image_path": raw_path,
                "raw_image_sha256": hashlib.sha256(self.raw).hexdigest(),
                "raw_image_size": len(self.raw),
                "page_size": 2048,
                "kernel_size": len(compressed) + len(self.dtb),
                "gzip_size": len(compressed),
                "gzip_sha256": hashlib.sha256(compressed).hexdigest(),
                "dtb_size": len(self.dtb),
                "dtb_sha256": hashlib.sha256(self.dtb).hexdigest(),
                "image_id_hex": self.image_id.hex(),
            },
            "recovery_validation": {
                "status": "not_run",
                "successful_boots": 0,
                "successful_recovery_tests": 0,
                "independent_recovery_path": False,
            },
        }

    def reader(self, path: Path, _expected: dict[str, object]) -> list[dict[str, object]]:
        return copy.deepcopy(self.records)

    def collect(self, raw: dict[str, object] | None = None) -> dict[str, object]:
        return preflight.collect(
            preflight.validate_lock(self.lock if raw is None else raw),
            rollback_artifact_dir=self.rollback_dir,
            baseline_artifact_dir=self.baseline_dir,
            reference_root=self.reference_root,
            rebuilt_root=self.rebuilt_root,
            rpm_manifest_reader=self.reader,
        )


class RollbackPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_static_preflight_passes_without_claiming_recovery(self) -> None:
        report = self.fixture.collect()
        self.assertTrue(report["gates"]["static_rollback_preflight_passed"])
        self.assertFalse(report["gates"]["rollback_artifact_prevalidated"])
        self.assertFalse(report["gates"]["candidate_install_authorized"])
        self.assertEqual(report["recovery_validation"]["successful_boots"], 0)
        self.assertEqual(report["recovery_validation"]["successful_recovery_tests"], 0)
        self.assertFalse(report["recovery_validation"]["independent_recovery_path"])

    def test_rollback_artifact_tamper_is_rejected(self) -> None:
        path = self.fixture.rollback_dir / "rollback.img"
        changed = bytearray(path.read_bytes())
        changed[-1] ^= 1
        path.write_bytes(changed)
        with self.assertRaisesRegex(preflight.PreflightError, "locked file differs"):
            self.fixture.collect()

    def test_rebuilt_payload_drift_is_rejected(self) -> None:
        (self.fixture.rebuilt_root / "boot/config-fixture").write_bytes(b"changed\n")
        with self.assertRaisesRegex(preflight.PreflightError, "payloads differ"):
            self.fixture.collect()

    def test_candidate_report_cannot_overclaim_install_readiness(self) -> None:
        report = json.loads(self.fixture.candidate_report_path.read_text(encoding="utf-8"))
        report["gates"]["candidate_install_authorized"] = True
        self.fixture.candidate_report_path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
        changed = copy.deepcopy(self.fixture.lock)
        changed["artifacts"]["candidate_report"] = record(self.fixture.candidate_report_path)
        lock = preflight.validate_lock(changed)
        with self.assertRaisesRegex(preflight.PreflightError, "overclaims installation"):
            preflight._verify_reports(lock, self.fixture.rollback_dir)

    def test_static_lock_cannot_claim_boot_or_recovery_success(self) -> None:
        for field, value in (
            ("successful_boots", 1),
            ("successful_recovery_tests", 1),
            ("independent_recovery_path", True),
            ("status", "passed"),
        ):
            changed = copy.deepcopy(self.fixture.lock)
            changed["recovery_validation"][field] = value
            with self.assertRaisesRegex(preflight.PreflightError, "must not claim recovery"):
                preflight.validate_lock(changed)

    def test_rpm_header_manifest_drift_is_rejected(self) -> None:
        changed_records = copy.deepcopy(self.fixture.records)
        changed_records[0]["mode"] = 0o600

        def reader(path: Path, _expected: dict[str, object]) -> list[dict[str, object]]:
            return changed_records if path.name == "rebuilt.rpm" else copy.deepcopy(self.fixture.records)

        lock = preflight.validate_lock(self.fixture.lock)
        with self.assertRaisesRegex(preflight.PreflightError, "RPM header manifests differ"):
            preflight.collect(
                lock,
                rollback_artifact_dir=self.fixture.rollback_dir,
                baseline_artifact_dir=self.fixture.baseline_dir,
                reference_root=self.fixture.reference_root,
                rebuilt_root=self.fixture.rebuilt_root,
                rpm_manifest_reader=reader,
            )

    def test_unknown_and_duplicate_lock_fields_are_rejected(self) -> None:
        changed = copy.deepcopy(self.fixture.lock)
        changed["helpful"] = True
        with self.assertRaisesRegex(preflight.PreflightError, "rollback lock fields differ"):
            preflight.validate_lock(changed)
        duplicate = self.fixture.root / "duplicate.json"
        duplicate.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")
        with self.assertRaisesRegex(preflight.PreflightError, "duplicate JSON key"):
            preflight.payload.load_lock(duplicate)

    def test_cli_has_no_build_install_flash_output_or_lock_override(self) -> None:
        result = subprocess.run([sys.executable, str(SOURCE), "--help"], check=True, capture_output=True, text=True)
        for forbidden in ("--build", "--install", "--flash", "--output", "--lock"):
            self.assertNotIn(forbidden, result.stdout)
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in ("requests", "urllib", "os.system", "shell=True"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

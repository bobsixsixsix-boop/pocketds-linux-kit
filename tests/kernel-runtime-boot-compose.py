#!/usr/bin/env python3

from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "kernel-ab" / "runtime-boot" / "compose.py"
MODULE_SPEC = importlib.util.spec_from_file_location("pocketds_runtime_boot_compose", SOURCE)
assert MODULE_SPEC and MODULE_SPEC.loader
compose = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = compose
MODULE_SPEC.loader.exec_module(compose)


def record(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "filename": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
    }


def image_id(kernel: bytes) -> bytes:
    return hashlib.sha1(
        kernel + struct.pack("<I", len(kernel)) + struct.pack("<I", 0) * 2
    ).digest() + bytes(12)


def boot_image(raw: bytes, dtb: bytes) -> tuple[bytes, bytes, bytes]:
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    kernel = compressed + dtb
    page_size = 2048
    header = bytearray(page_size)
    header[:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, len(kernel))
    struct.pack_into("<I", header, 36, page_size)
    identifier = image_id(kernel)
    header[576:608] = identifier
    return bytes(header) + kernel + bytes((-len(kernel)) % page_size), compressed, identifier


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.artifacts = root / "artifacts"
        self.artifacts.mkdir(mode=0o700)
        self.output_dir = root / "output"
        self.output_dir.mkdir(mode=0o700)
        self.raw = bytes(range(256)) * 4
        self.package_dtb = b"package-dtb" * 13
        self.custom_dtb = b"source-bound-native-lid-dtb" * 11
        self.package_boot, self.compressed, package_id = boot_image(
            self.raw, self.package_dtb
        )
        self.runtime_boot, _unused, runtime_id = boot_image(self.raw, self.custom_dtb)

        paths = {
            "candidate_package_bootimg": self.artifacts / "candidate-package-boot.img",
            "candidate_raw_image": self.artifacts / "candidate-raw-Image",
            "custom_dtb": self.artifacts / "custom.dtb",
            "candidate_report": self.artifacts / "candidate.json",
            "rollback_preflight_report": self.artifacts / "rollback.json",
        }
        paths["candidate_package_bootimg"].write_bytes(self.package_boot)
        paths["candidate_raw_image"].write_bytes(self.raw)
        paths["custom_dtb"].write_bytes(self.custom_dtb)

        self.source_commit = "a" * 40
        self.revert_commit = "b" * 40
        self.release = "fixture.aarch64"
        candidate_report = {
            "schema": compose.ifpc.REPORT_SCHEMA,
            "experiment": {
                "source_commit": self.source_commit,
                "revert_commit": self.revert_commit,
                "package_nevra": "kernel-" + self.release,
            },
            "gates": {
                "candidate_built": True,
                "source_inputs_except_revert_identical": True,
                "single_variable_payload_delta_proven": True,
                "candidate_independently_reproduced": True,
                "rollback_artifact_prevalidated": False,
                "candidate_install_authorized": False,
            },
        }
        rollback_report = {
            "schema": "pocketds.kernel-rollback-static-preflight.v1",
            "artifact": {
                "artifact_id": "pds002-live-baseline-rollback-v2",
                "device_id": "fixture-device",
                "kernel_release": self.release,
                "source_commit": self.source_commit,
            },
            "boot_derivation": {
                "dtb_sha256": hashlib.sha256(self.custom_dtb).hexdigest(),
                "dtb_size": len(self.custom_dtb),
            },
            "recovery_validation": {
                "status": "not_run",
                "successful_boots": 0,
                "successful_recovery_tests": 0,
                "independent_recovery_path": False,
            },
            "gates": {
                "rollback_artifact_content_locked": True,
                "baseline_source_to_binary_evidence_bound": True,
                "signed_and_reproduced_baseline_bound": True,
                "custom_dtb_source_and_rebuild_evidence_bound": True,
                "runtime_boot_composition_bound": True,
                "static_rollback_preflight_passed": True,
                "rollback_artifact_prevalidated": False,
                "candidate_install_authorized": False,
            },
        }
        paths["candidate_report"].write_text(
            json.dumps(candidate_report, sort_keys=True), encoding="utf-8"
        )
        paths["rollback_preflight_report"].write_text(
            json.dumps(rollback_report, sort_keys=True), encoding="utf-8"
        )
        self.paths = paths
        self.lock = {
            "schema_version": 1,
            "artifact_id": "fixture-runtime",
            "device_id": "fixture-device",
            "kernel_release": self.release,
            "source_commit": self.source_commit,
            "revert_commit": self.revert_commit,
            "inputs": {name: record(path) for name, path in paths.items()},
            "package_boot": {
                "page_size": 2048,
                "kernel_size": len(self.compressed) + len(self.package_dtb),
                "gzip_size": len(self.compressed),
                "gzip_sha256": hashlib.sha256(self.compressed).hexdigest(),
                "dtb_size": len(self.package_dtb),
                "dtb_sha256": hashlib.sha256(self.package_dtb).hexdigest(),
                "image_id_hex": package_id.hex(),
            },
            "runtime_boot": {
                "filename": "fixture-runtime.img",
                "sha256": hashlib.sha256(self.runtime_boot).hexdigest(),
                "size": len(self.runtime_boot),
                "page_size": 2048,
                "kernel_size": len(self.compressed) + len(self.custom_dtb),
                "gzip_size": len(self.compressed),
                "gzip_sha256": hashlib.sha256(self.compressed).hexdigest(),
                "dtb_size": len(self.custom_dtb),
                "dtb_sha256": hashlib.sha256(self.custom_dtb).hexdigest(),
                "image_id_hex": runtime_id.hex(),
            },
        }

    @property
    def output(self) -> Path:
        return self.output_dir / str(self.lock["runtime_boot"]["filename"])

    def validated(self, raw: dict[str, object] | None = None) -> dict[str, object]:
        return compose.validate_lock(self.lock if raw is None else raw)

    def run(self, *, verify: bool = False, raw: dict[str, object] | None = None) -> dict[str, object]:
        return compose.run(
            self.validated(raw),
            artifact_dir=self.artifacts,
            output=self.output,
            verify_existing=verify,
        )

    def refresh(self, name: str) -> dict[str, object]:
        changed = copy.deepcopy(self.lock)
        changed["inputs"][name] = record(self.paths[name])
        return changed


class RuntimeBootComposeTests(unittest.TestCase):
    def setUp(self) -> None:
        test_root = ROOT / "build"
        test_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=test_root)
        self.fixture = Fixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_create_and_verify_are_deterministic_and_non_authorizing(self) -> None:
        created = self.fixture.run()
        verified = self.fixture.run(verify=True)
        self.assertEqual(created["action"], "created_new")
        self.assertEqual(verified["action"], "verified_existing")
        self.assertEqual(self.fixture.output.read_bytes(), self.fixture.runtime_boot)
        self.assertEqual(self.fixture.output.stat().st_mode & 0o777, 0o600)
        self.assertTrue(created["composition"]["candidate_gzip_preserved"])
        self.assertTrue(created["composition"]["source_bound_native_lid_dtb_appended"])
        self.assertFalse(created["gates"]["rollback_artifact_prevalidated"])
        self.assertFalse(created["gates"]["candidate_install_authorized"])

    def test_create_refuses_to_overwrite(self) -> None:
        self.fixture.run()
        with self.assertRaisesRegex(compose.ComposeError, "cannot create"):
            self.fixture.run()
        self.assertEqual(self.fixture.output.read_bytes(), self.fixture.runtime_boot)

    def test_verify_rejects_existing_image_drift(self) -> None:
        self.fixture.output.write_bytes(b"not-the-image")
        with self.assertRaisesRegex(compose.ComposeError, "existing runtime boot image differs"):
            self.fixture.run(verify=True)

    def test_locked_input_tamper_is_rejected(self) -> None:
        self.fixture.paths["candidate_raw_image"].write_bytes(self.fixture.raw + b"tamper")
        with self.assertRaisesRegex(compose.ComposeError, "locked input differs"):
            self.fixture.run()

    def test_package_embedded_dtb_drift_is_rejected(self) -> None:
        changed = bytearray(self.fixture.package_boot)
        changed[2048 + len(self.fixture.compressed)] ^= 1
        self.fixture.paths["candidate_package_bootimg"].write_bytes(changed)
        raw = self.fixture.refresh("candidate_package_bootimg")
        with self.assertRaisesRegex(compose.ComposeError, "appended DTB differs"):
            self.fixture.run(raw=raw)

    def test_candidate_report_cannot_overclaim_install(self) -> None:
        path = self.fixture.paths["candidate_report"]
        report_data = json.loads(path.read_text(encoding="utf-8"))
        report_data["gates"]["candidate_install_authorized"] = True
        path.write_text(json.dumps(report_data, sort_keys=True), encoding="utf-8")
        with self.assertRaisesRegex(compose.ComposeError, "overclaims readiness"):
            self.fixture.run(raw=self.fixture.refresh("candidate_report"))

    def test_rollback_report_cannot_claim_unrun_recovery(self) -> None:
        path = self.fixture.paths["rollback_preflight_report"]
        report_data = json.loads(path.read_text(encoding="utf-8"))
        report_data["recovery_validation"]["successful_boots"] = 1
        path.write_text(json.dumps(report_data, sort_keys=True), encoding="utf-8")
        with self.assertRaisesRegex(compose.ComposeError, "overclaims recovery"):
            self.fixture.run(raw=self.fixture.refresh("rollback_preflight_report"))

    def test_wrong_output_basename_is_rejected(self) -> None:
        with self.assertRaisesRegex(compose.ComposeError, "filename differs"):
            compose.run(
                self.fixture.validated(),
                artifact_dir=self.fixture.artifacts,
                output=self.fixture.output_dir / "wrong.img",
                verify_existing=False,
            )

    def test_linked_output_parent_is_rejected(self) -> None:
        linked = self.fixture.root / "linked"
        linked.symlink_to(self.fixture.output_dir, target_is_directory=True)
        with self.assertRaisesRegex(compose.ComposeError, "unavailable or linked"):
            compose.run(
                self.fixture.validated(),
                artifact_dir=self.fixture.artifacts,
                output=linked / self.fixture.output.name,
                verify_existing=False,
            )

    def test_group_writable_output_parent_is_rejected(self) -> None:
        os.chmod(self.fixture.output_dir, 0o770)
        with self.assertRaisesRegex(compose.ComposeError, "parent is linked, writable"):
            self.fixture.run()

    def test_lock_rejects_package_runtime_gzip_mismatch(self) -> None:
        raw = copy.deepcopy(self.fixture.lock)
        raw["runtime_boot"]["gzip_sha256"] = "0" * 64
        with self.assertRaisesRegex(compose.ComposeError, "gzip streams are not locked equal"):
            self.fixture.validated(raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)

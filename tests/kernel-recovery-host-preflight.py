#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "kernel-ab" / "recovery" / "preflight.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "pocketds_recovery_host_preflight", SOURCE
)
assert MODULE_SPEC and MODULE_SPEC.loader
preflight = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = preflight
MODULE_SPEC.loader.exec_module(preflight)


def record(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "filename": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
    }


def boot(path: Path, kernel_size: int, identifier: bytes) -> None:
    page = 2048
    header = bytearray(page)
    header[:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, kernel_size)
    struct.pack_into("<I", header, 36, page)
    header[576:608] = identifier
    path.write_bytes(bytes(header) + b"K" * kernel_size + bytes((-kernel_size) % page))


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.artifact_dir = root / "artifacts"
        self.artifact_dir.mkdir(mode=0o700)
        self.baseline = self.artifact_dir / "baseline.img"
        self.candidate = self.artifact_dir / "candidate.img"
        self.abl = root / "abl.elf"
        self.fastboot = root / "fastboot"
        self.baseline_id = bytes(range(32))
        self.candidate_id = bytes(reversed(range(32)))
        boot(self.baseline, 1000, self.baseline_id)
        boot(self.candidate, 999, self.candidate_id)
        self.abl.write_bytes(b"signed-abl-fixture")
        self.version = "fastboot version fixture-1"
        self.fastboot.write_text(
            "#!/bin/sh\nprintf '%s\\n' 'fastboot version fixture-1'\n",
            encoding="utf-8",
        )
        self.fastboot.chmod(0o700)
        notes_base = b"N" * 128
        notes_candidate = b"C" * 128
        self.lock = {
            "schema_version": 1,
            "device_id": "fixture-device",
            "machine_model": "Fixture Device",
            "kernel_release": "fixture.release",
            "artifacts": {
                "baseline_rollback_boot": record(self.baseline),
                "candidate_runtime_boot": record(self.candidate),
                "rocknix_abl_payload": record(self.abl),
                "host_fastboot": record(self.fastboot),
            },
            "boot_images": {
                "page_size": 2048,
                "container_size": len(self.baseline.read_bytes()),
                "baseline_kernel_size": 1000,
                "baseline_image_id_hex": self.baseline_id.hex(),
                "candidate_kernel_size": 999,
                "candidate_image_id_hex": self.candidate_id.hex(),
            },
            "rocknix_abl": {
                "release": "1.2.3",
                "release_url": "https://github.com/ROCKNIX/abl/releases/tag/v1.2.3",
                "release_asset_sha256": "a" * 64,
                "linuxloader_commit": "b" * 40,
                "partition_prefix_size": len(self.abl.read_bytes()),
                "required_fastboot_string": "locked device",
            },
            "host_fastboot": {
                "version_line": self.version,
                "temporary_boot_subcommand": "boot",
                "requires_unlocked": True,
            },
            "runtime_identity": {
                "notes_size": 128,
                "baseline_notes_sha256": hashlib.sha256(notes_base).hexdigest(),
                "candidate_notes_sha256": hashlib.sha256(notes_candidate).hexdigest(),
                "baseline_build_id_hex": "1" * 40,
                "candidate_build_id_hex": "2" * 40,
            },
            "disk_boot_aliases": ["boot/Image", "boot/boot/Image", "boot/Image-fixture"],
        }

    def validated(self, raw: dict[str, object] | None = None) -> dict[str, object]:
        return preflight.validate_lock(self.lock if raw is None else raw)

    def collect(self, raw: dict[str, object] | None = None) -> dict[str, object]:
        return preflight.collect(
            self.validated(raw),
            artifact_dir=self.artifact_dir,
            abl_payload=self.abl,
            fastboot=self.fastboot,
        )

    def refresh(self, name: str) -> dict[str, object]:
        changed = copy.deepcopy(self.lock)
        path = {
            "baseline_rollback_boot": self.baseline,
            "candidate_runtime_boot": self.candidate,
            "rocknix_abl_payload": self.abl,
            "host_fastboot": self.fastboot,
        }[name]
        changed["artifacts"][name] = record(path)
        return changed


class RecoveryHostPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        test_root = ROOT / "build"
        test_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=test_root)
        self.fixture = Fixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_locked_host_material_passes_without_authorizing_execution(self) -> None:
        report = self.fixture.collect()
        self.assertTrue(report["read_only"])
        self.assertFalse(report["device_access"])
        self.assertTrue(report["gates"]["host_recovery_material_preflight_passed"])
        self.assertFalse(report["gates"]["live_fastboot_unlock_verified"])
        self.assertFalse(report["gates"]["independent_runtime_boot_observed"])
        self.assertFalse(report["gates"]["rollback_artifact_prevalidated"])
        self.assertFalse(report["gates"]["candidate_install_authorized"])
        self.assertFalse(report["recovery_design"]["writes_partition"])

    def test_locked_boot_artifact_tamper_is_rejected(self) -> None:
        self.fixture.baseline.write_bytes(self.fixture.baseline.read_bytes() + b"x")
        with self.assertRaisesRegex(preflight.PreflightError, "locked recovery input differs"):
            self.fixture.collect()

    def test_boot_geometry_drift_is_rejected_after_rehash(self) -> None:
        changed = bytearray(self.fixture.candidate.read_bytes())
        struct.pack_into("<I", changed, 16, 1)
        self.fixture.candidate.write_bytes(changed)
        with self.assertRaisesRegex(preflight.PreflightError, "geometry differs"):
            self.fixture.collect(self.fixture.refresh("candidate_runtime_boot"))

    def test_fastboot_version_drift_is_rejected_after_rehash(self) -> None:
        self.fixture.fastboot.write_text(
            "#!/bin/sh\nprintf '%s\\n' 'fastboot version wrong'\n", encoding="utf-8"
        )
        self.fixture.fastboot.chmod(0o700)
        with self.assertRaisesRegex(preflight.PreflightError, "fastboot version differs"):
            self.fixture.collect(self.fixture.refresh("host_fastboot"))

    def test_symlinked_fastboot_is_rejected(self) -> None:
        linked = self.fixture.root / "linked-fastboot"
        linked.symlink_to(self.fixture.fastboot)
        changed = copy.deepcopy(self.fixture.lock)
        changed["artifacts"]["host_fastboot"]["filename"] = linked.name
        with self.assertRaisesRegex(preflight.PreflightError, "unavailable or linked"):
            preflight.collect(
                self.fixture.validated(changed),
                artifact_dir=self.fixture.artifact_dir,
                abl_payload=self.fixture.abl,
                fastboot=linked,
            )

    def test_non_executable_fastboot_is_rejected(self) -> None:
        self.fixture.fastboot.chmod(0o600)
        with self.assertRaisesRegex(preflight.PreflightError, "not executable"):
            self.fixture.collect(self.fixture.refresh("host_fastboot"))

    def test_lock_rejects_flash_subcommand_and_false_unlock_gate(self) -> None:
        for field, value in (("temporary_boot_subcommand", "flash"), ("requires_unlocked", False)):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.fixture.lock)
                changed["host_fastboot"][field] = value
                with self.assertRaisesRegex(preflight.PreflightError, "recovery policy differs"):
                    self.fixture.validated(changed)

    def test_unknown_lock_field_is_rejected(self) -> None:
        changed = copy.deepcopy(self.fixture.lock)
        changed["unknown"] = True
        with self.assertRaisesRegex(preflight.PreflightError, "fields differ"):
            self.fixture.validated(changed)

    def test_cli_exposes_no_device_execute_or_flash_switch(self) -> None:
        completed = subprocess.run(
            [sys.executable, os.fspath(SOURCE), "--help"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        for forbidden in ("--execute", "--confirm", "--device", "--flash", "--reboot"):
            self.assertNotIn(forbidden, completed.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

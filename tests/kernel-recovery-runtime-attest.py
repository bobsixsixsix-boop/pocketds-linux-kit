#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "kernel-ab" / "recovery" / "runtime-attest.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "pocketds_recovery_runtime_attest", SOURCE
)
assert MODULE_SPEC and MODULE_SPEC.loader
attest = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = attest
MODULE_SPEC.loader.exec_module(attest)


def artifact(name: str, content: bytes) -> dict[str, object]:
    return {"filename": name, "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.release = "fixture.release"
        self.model = "Fixture Device"
        self.baseline_build_id = bytes.fromhex("1" * 40)
        self.candidate_build_id = bytes.fromhex("2" * 40)
        self.baseline_notes = b"H" * 16 + self.baseline_build_id + b"B" * 92
        self.candidate_notes = b"H" * 16 + self.candidate_build_id + b"C" * 92
        assert len(self.baseline_notes) == len(self.candidate_notes) == 128
        self.baseline_boot = b"baseline--boot" * 64
        self.candidate_boot = b"candidate-boot" * 64
        assert len(self.baseline_boot) == len(self.candidate_boot)
        zero = "0" * 64
        self.lock = {
            "schema_version": 1,
            "device_id": "fixture-device",
            "machine_model": self.model,
            "kernel_release": self.release,
            "artifacts": {
                "baseline_rollback_boot": artifact("baseline.img", self.baseline_boot),
                "candidate_runtime_boot": artifact("candidate.img", self.candidate_boot),
                "rocknix_abl_payload": {"filename": "abl.elf", "sha256": zero, "size": 16},
                "host_fastboot": {"filename": "fastboot", "sha256": zero, "size": 16},
            },
            "boot_images": {
                "page_size": 2048,
                "container_size": len(self.baseline_boot),
                "baseline_kernel_size": 128,
                "baseline_image_id_hex": "3" * 64,
                "candidate_kernel_size": 127,
                "candidate_image_id_hex": "4" * 64,
            },
            "rocknix_abl": {
                "release": "1.2.3",
                "release_url": "https://github.com/ROCKNIX/abl/releases/tag/v1.2.3",
                "release_asset_sha256": "5" * 64,
                "linuxloader_commit": "6" * 40,
                "partition_prefix_size": 16,
                "required_fastboot_string": "locked device",
            },
            "host_fastboot": {
                "version_line": "fastboot version fixture",
                "temporary_boot_subcommand": "boot",
                "requires_unlocked": True,
            },
            "runtime_identity": {
                "notes_size": 128,
                "baseline_notes_sha256": hashlib.sha256(self.baseline_notes).hexdigest(),
                "candidate_notes_sha256": hashlib.sha256(self.candidate_notes).hexdigest(),
                "baseline_build_id_hex": self.baseline_build_id.hex(),
                "candidate_build_id_hex": self.candidate_build_id.hex(),
            },
            "disk_boot_aliases": ["boot/Image", "boot/boot/Image", "boot/Image-fixture"],
        }
        for relative in (
            "sys/kernel/notes",
            "sys/firmware/devicetree/base/model",
            "proc/sys/kernel/osrelease",
            "proc/sys/kernel/random/boot_id",
            "proc/cmdline",
            "proc/uptime",
            *self.lock["disk_boot_aliases"],
        ):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / "sys/firmware/devicetree/base/model").write_bytes(self.model.encode() + b"\x00")
        (root / "proc/sys/kernel/osrelease").write_text(self.release + "\n", encoding="utf-8")
        (root / "proc/sys/kernel/random/boot_id").write_text(
            "12345678-1234-5678-9234-567812345678\n", encoding="utf-8"
        )
        (root / "proc/cmdline").write_text(
            "root=PARTLABEL=STORAGE rw boot=LABEL=ROCKNIX quiet\n", encoding="utf-8"
        )
        (root / "proc/uptime").write_text("30.50 100.00\n", encoding="utf-8")
        self.set_state("baseline", "candidate")

    def validated(self) -> dict[str, object]:
        return attest.preflight.validate_lock(self.lock)

    def set_state(self, running: str, disk: str) -> None:
        notes = self.baseline_notes if running == "baseline" else self.candidate_notes
        boot = self.baseline_boot if disk == "baseline" else self.candidate_boot
        (self.root / "sys/kernel/notes").write_bytes(notes)
        for relative in self.lock["disk_boot_aliases"]:
            (self.root / relative).write_bytes(boot)

    def collect(self, running: str = "baseline", disk: str = "candidate", maximum: int = 900) -> dict[str, object]:
        return attest.collect(
            self.validated(),
            root=self.root,
            running=running,
            disk=disk,
            max_uptime_seconds=maximum,
        )


class RecoveryRuntimeAttestationTests(unittest.TestCase):
    def setUp(self) -> None:
        test_root = ROOT / "build"
        test_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=test_root)
        self.fixture = Fixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_baseline_running_over_candidate_disk_proves_independent_path(self) -> None:
        report = self.fixture.collect()
        self.assertEqual(
            report["schema"],
            "pocketds.kernel-fastboot-recovery-runtime-attestation.v2",
        )
        self.assertTrue(report["gates"]["independent_runtime_boot_observed"])
        self.assertTrue(report["gates"]["running_kernel_build_id_matches"])
        self.assertTrue(report["gates"]["disk_boot_aliases_match"])
        self.assertFalse(report["gates"]["rollback_artifact_prevalidated"])
        self.assertFalse(report["gates"]["candidate_install_authorized"])
        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn("12345678-1234-5678-9234-567812345678", encoded)
        self.assertNotIn(os.fspath(self.fixture.root), encoded)
        runtime = report["runtime"]
        self.assertAlmostEqual(
            (runtime["observed_at_unix_ns"] - runtime["boot_started_at_unix_ns"])
            / 1_000_000_000,
            runtime["uptime_seconds"],
            places=6,
        )

    def test_normal_baseline_boot_does_not_prove_independence(self) -> None:
        self.fixture.set_state("baseline", "baseline")
        report = self.fixture.collect(disk="baseline")
        self.assertFalse(report["gates"]["independent_runtime_boot_observed"])

    def test_normal_candidate_boot_is_attested_but_not_recovery(self) -> None:
        self.fixture.set_state("candidate", "candidate")
        report = self.fixture.collect(running="candidate")
        self.assertEqual(report["runtime"]["build_id_hex"], self.fixture.candidate_build_id.hex())
        self.assertFalse(report["gates"]["independent_runtime_boot_observed"])

    def test_stale_boot_window_does_not_prove_independence(self) -> None:
        (self.fixture.root / "proc/uptime").write_text("901.0 1000.0\n", encoding="utf-8")
        report = self.fixture.collect(maximum=900)
        self.assertFalse(report["gates"]["boot_is_within_recovery_window"])
        self.assertFalse(report["gates"]["independent_runtime_boot_observed"])

    def test_running_notes_tamper_is_rejected(self) -> None:
        (self.fixture.root / "sys/kernel/notes").write_bytes(b"x" * 128)
        with self.assertRaisesRegex(attest.AttestationError, "kernel notes identity differs"):
            self.fixture.collect()

    def test_one_disk_alias_drift_is_rejected(self) -> None:
        (self.fixture.root / "boot/boot/Image").write_bytes(b"drift")
        with self.assertRaisesRegex(attest.AttestationError, "boot alias identity differs"):
            self.fixture.collect()

    def test_linked_disk_alias_is_rejected(self) -> None:
        path = self.fixture.root / "boot/boot/Image"
        path.unlink()
        path.symlink_to(self.fixture.root / "boot/Image")
        with self.assertRaisesRegex(attest.AttestationError, "unavailable or linked"):
            self.fixture.collect()

    def test_machine_release_and_root_binding_are_strict(self) -> None:
        targets = (
            ("sys/firmware/devicetree/base/model", b"Wrong\x00"),
            ("proc/sys/kernel/osrelease", b"wrong.release\n"),
            ("proc/cmdline", b"root=PARTLABEL=OTHER boot=LABEL=ROCKNIX\n"),
        )
        for relative, content in targets:
            with self.subTest(relative=relative):
                original = (self.fixture.root / relative).read_bytes()
                (self.fixture.root / relative).write_bytes(content)
                with self.assertRaisesRegex(attest.AttestationError, "binding differs"):
                    self.fixture.collect()
                (self.fixture.root / relative).write_bytes(original)

    def test_uptime_and_selector_bounds_are_strict(self) -> None:
        with self.assertRaisesRegex(attest.AttestationError, "uptime bound"):
            self.fixture.collect(maximum=59)
        with self.assertRaisesRegex(attest.AttestationError, "selector"):
            attest.collect(
                self.fixture.validated(),
                root=self.fixture.root,
                running="unknown",
                disk="candidate",
                max_uptime_seconds=900,
            )

    def test_cli_has_no_write_fastboot_reboot_or_install_switch(self) -> None:
        completed = subprocess.run(
            [sys.executable, os.fspath(SOURCE), "--help"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        for forbidden in ("--execute", "--confirm", "--fastboot", "--flash", "--reboot", "--install"):
            self.assertNotIn(forbidden, completed.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

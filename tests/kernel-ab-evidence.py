#!/usr/bin/env python3

from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "kernel-ab" / "evidence.py"
MODULE_SPEC = importlib.util.spec_from_file_location("pocketds_kernel_ab_evidence", SOURCE)
assert MODULE_SPEC and MODULE_SPEC.loader
evidence = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = evidence
MODULE_SPEC.loader.exec_module(evidence)


RELEASE = "7.1.0-test.pocketds.aarch64"
SOURCE_COMMIT = "a" * 40


def spec() -> dict[str, object]:
    return {
        "schema_version": 3,
        "device_id": "fixture-pocket-ds",
        "expected_compatible": ["ayaneo,pocketds", "qcom,qcs8550", "qcom,sm8550"],
        "expected_kernel_release": RELEASE,
        "expected_source_commit": SOURCE_COMMIT,
        "kernel_image": f"usr/lib/modules/{RELEASE}/Image",
        "kernel_config": f"usr/lib/modules/{RELEASE}/config",
        "kernel_config_mirror": f"boot/config-{RELEASE}",
        "boot_image_versioned": f"boot/Image-{RELEASE}",
        "boot_image_aliases": ["boot/Image", "boot/boot/Image"],
        "boot_dtb": f"boot/dtb-{RELEASE}/qcom/qcs8550-ayaneo-pocketds.dtb",
        "firmware_files": [
            "usr/lib/firmware/qcom/a740_sqe.fw",
            "usr/lib/firmware/qcom/gmu_gen70200.bin",
        ],
    }


def write_fixture(root: Path) -> None:
    raw_kernel = b"fixture-kernel-image"
    dtb = struct.pack(
        ">10I",
        evidence.FDT_MAGIC,
        56,
        56,
        56,
        40,
        17,
        16,
        0,
        0,
        0,
    ) + (b"\0" * 16)
    kernel_blob = gzip.compress(raw_kernel, mtime=0) + dtb
    header = bytearray(evidence.ANDROID_BOOT_V0_HEADER_BYTES)
    header[:8] = evidence.ANDROID_BOOT_MAGIC
    struct.pack_into("<10I", header, 8, len(kernel_blob), 0, 0, 0, 0, 0, 0, 2048, 0, 0)
    boot_image = bytes(header).ljust(2048, b"\0") + kernel_blob
    boot_image = boot_image.ljust(
        2048 + evidence._padded_size(len(kernel_blob), 2048),
        b"\0",
    )
    payloads = {
        "proc/device-tree/compatible": b"ayaneo,pocketds\0qcom,qcs8550\0qcom,sm8550\0",
        f"usr/lib/modules/{RELEASE}/Image": raw_kernel,
        f"usr/lib/modules/{RELEASE}/config": b"CONFIG_DRM_MSM=y\n",
        f"boot/config-{RELEASE}": b"CONFIG_DRM_MSM=y\n",
        "usr/lib/firmware/qcom/a740_sqe.fw": b"fixture-sqe",
        "usr/lib/firmware/qcom/gmu_gen70200.bin": b"fixture-gmu",
        f"boot/Image-{RELEASE}": boot_image,
        "boot/Image": boot_image,
        "boot/boot/Image": boot_image,
        f"boot/dtb-{RELEASE}/qcom/qcs8550-ayaneo-pocketds.dtb": dtb,
    }
    for relative, content in payloads.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o644)


class EvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        write_fixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def collect(self, raw: dict[str, object] | None = None) -> dict[str, object]:
        return evidence.collect(spec() if raw is None else raw, root=self.root, runtime_release=RELEASE)

    def test_valid_fixture_measures_controls_but_refuses_production_readiness(self) -> None:
        report = self.collect()
        controls = report["planner_controls"]
        self.assertEqual(
            controls["kernel_config_sha256"], hashlib.sha256(b"CONFIG_DRM_MSM=y\n").hexdigest()
        )
        self.assertEqual(len(controls["firmware_set_sha256"]), 64)
        self.assertFalse(report["production_plan_ready"])
        self.assertFalse(report["gates"]["runtime_source_commit_cryptographically_bound"])
        self.assertFalse(report["gates"]["prevalidated_rollback_artifact"])
        container = report["artifacts"]["boot_container"]
        self.assertEqual(container["format"], "android-bootimg-v0")
        self.assertEqual(
            container["decompressed_kernel_sha256"],
            report["artifacts"]["kernel_image"]["sha256"],
        )
        self.assertEqual(len(report["artifacts"]["boot_images"]), 3)

    def test_report_is_deterministic(self) -> None:
        self.assertEqual(self.collect(), self.collect())

    def test_runtime_release_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(evidence.EvidenceError, "runtime kernel release differs"):
            evidence.collect(spec(), root=self.root, runtime_release="different")

    def test_machine_identity_mismatch_is_rejected(self) -> None:
        (self.root / "proc/device-tree/compatible").write_bytes(b"qcom,other\0")
        with self.assertRaisesRegex(evidence.EvidenceError, "device-tree compatible differs"):
            self.collect()

    def test_config_mirror_drift_is_rejected(self) -> None:
        (self.root / f"boot/config-{RELEASE}").write_bytes(b"CONFIG_DRM_MSM=n\n")
        with self.assertRaisesRegex(evidence.EvidenceError, "kernel configs differ"):
            self.collect()

    def test_boot_image_alias_drift_is_rejected(self) -> None:
        (self.root / "boot/Image").write_bytes(b"different boot image")
        with self.assertRaisesRegex(evidence.EvidenceError, "boot image alias differs"):
            self.collect()

    def test_boot_image_raw_kernel_drift_is_rejected(self) -> None:
        (self.root / f"usr/lib/modules/{RELEASE}/Image").write_bytes(b"different raw image")
        with self.assertRaisesRegex(evidence.EvidenceError, "gzip kernel differs"):
            self.collect()

    def test_boot_image_header_drift_is_rejected(self) -> None:
        paths = [
            self.root / f"boot/Image-{RELEASE}",
            self.root / "boot/Image",
            self.root / "boot/boot/Image",
        ]
        content = bytearray(paths[0].read_bytes())
        content[:8] = b"BROKEN!!"
        for path in paths:
            path.write_bytes(content)
        with self.assertRaisesRegex(evidence.EvidenceError, "not an Android boot image v0"):
            self.collect()

    def test_boot_image_appended_dtb_drift_is_rejected(self) -> None:
        paths = [
            self.root / f"boot/Image-{RELEASE}",
            self.root / "boot/Image",
            self.root / "boot/boot/Image",
        ]
        content = bytearray(paths[0].read_bytes())
        kernel_size = struct.unpack_from("<I", content, 8)[0]
        kernel_blob = bytes(content[2048 : 2048 + kernel_size])
        decompressor = evidence.zlib.decompressobj(16 + evidence.zlib.MAX_WBITS)
        decompressor.decompress(kernel_blob)
        dtb_offset = 2048 + kernel_size - len(decompressor.unused_data)
        content[dtb_offset : dtb_offset + 4] = b"NOPE"
        for path in paths:
            path.write_bytes(content)
        with self.assertRaisesRegex(evidence.EvidenceError, "DTB magic"):
            self.collect()

    def test_boot_image_dtb_mirror_drift_is_rejected(self) -> None:
        path = self.root / f"boot/dtb-{RELEASE}/qcom/qcs8550-ayaneo-pocketds.dtb"
        content = bytearray(path.read_bytes())
        content[-1] ^= 1
        path.write_bytes(content)
        with self.assertRaisesRegex(evidence.EvidenceError, "differs from its versioned mirror"):
            self.collect()

    def test_missing_firmware_is_rejected(self) -> None:
        (self.root / "usr/lib/firmware/qcom/a740_sqe.fw").unlink()
        with self.assertRaisesRegex(evidence.EvidenceError, "unavailable or linked"):
            self.collect()

    def test_symlinked_firmware_is_rejected(self) -> None:
        firmware = self.root / "usr/lib/firmware/qcom/a740_sqe.fw"
        firmware.unlink()
        firmware.symlink_to(self.root / "usr/lib/firmware/qcom/gmu_gen70200.bin")
        with self.assertRaisesRegex(evidence.EvidenceError, "unavailable or linked"):
            self.collect()

    def test_writable_artifact_is_rejected(self) -> None:
        firmware = self.root / "usr/lib/firmware/qcom/a740_sqe.fw"
        firmware.chmod(0o666)
        with self.assertRaisesRegex(evidence.EvidenceError, "artifact is unsafe"):
            self.collect()

    def test_unsorted_or_out_of_tree_firmware_is_rejected(self) -> None:
        raw = spec()
        raw["firmware_files"] = list(reversed(raw["firmware_files"]))
        with self.assertRaisesRegex(evidence.EvidenceError, "unique and sorted"):
            self.collect(raw)
        raw = spec()
        raw["firmware_files"] = ["etc/passwd"]
        with self.assertRaisesRegex(evidence.EvidenceError, "must stay below"):
            self.collect(raw)

    def test_unknown_spec_field_is_rejected(self) -> None:
        raw = copy.deepcopy(spec())
        raw["helpful"] = True
        with self.assertRaisesRegex(evidence.EvidenceError, "spec fields differ"):
            self.collect(raw)

    def test_cli_exposes_no_root_output_or_mutation_switch(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SOURCE), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        for forbidden in ("--root", "--output", "--install", "--build", "--source-commit"):
            self.assertNotIn(forbidden, result.stdout)
        source_text = SOURCE.read_text(encoding="utf-8")
        for forbidden in ("subprocess", "requests", "urllib", "os.system"):
            self.assertNotIn(forbidden, source_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

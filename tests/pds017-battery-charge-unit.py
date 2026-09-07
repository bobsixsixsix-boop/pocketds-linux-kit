#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "kernel" / "battery-charge-unit"
MANIFEST_PATH = EXPERIMENT / "manifest.json"
PATCH_PATH = EXPERIMENT / "0001-power-supply-qcom-battmgr-initialize-property-unit.patch"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_patch(source: Path) -> bytes:
    with tempfile.TemporaryDirectory(prefix="pds017-unit-") as directory:
        root = Path(directory)
        target = root / "drivers" / "power" / "supply" / "qcom_battmgr.c"
        target.parent.mkdir(parents=True)
        shutil.copyfile(source, target)
        subprocess.run(
            ["git", "-C", str(root), "apply", "--check", str(PATCH_PATH)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(root), "apply", str(PATCH_PATH)],
            check=True,
            capture_output=True,
            text=True,
        )
        return target.read_bytes()


class BatteryChargeUnitCandidateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.patch = PATCH_PATH.read_text(encoding="utf-8")

    def test_manifest_is_source_only_and_separate(self) -> None:
        self.assertEqual(self.manifest["schema"], 1)
        self.assertEqual(self.manifest["status"], "source-only-full-build-verified")
        self.assertFalse(self.manifest["default_install"])
        self.assertEqual(
            self.manifest["must_not_combine_with"], ["pds002-a740-ifpc-revert"]
        )
        validation = self.manifest["validation"]
        self.assertTrue(validation["applies_to_pinned_downstream_source"])
        self.assertTrue(validation["arm64_driver_object_compiled"])
        self.assertTrue(validation["upower_source_contract"])
        self.assertTrue(validation["full_kernel_built"])
        self.assertFalse(validation["runtime_sysfs"])
        self.assertFalse(validation["upower"])
        build = self.manifest["isolated_arm64_build"]
        self.assertEqual(build["architecture"], "aarch64")
        self.assertRegex(build["object_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(build["full_kernel_built"])
        full = build["full_build"]
        self.assertEqual(full["targets"], ["Image", "modules", "dtbs"])
        self.assertEqual(full["kernel_release"], "7.1.0-rc2")
        self.assertEqual(full["module_count"], 319)
        self.assertEqual(full["dtb_count"], 417)
        self.assertFalse(full["release_artifact"])
        for key in (
            "config_sha256",
            "image_sha256",
            "vmlinux_sha256",
            "system_map_sha256",
            "module_symvers_sha256",
            "module_tree_sha256",
            "candidate_module_sha256",
            "pocketds_dtb_sha256",
        ):
            self.assertRegex(full[key], r"^[0-9a-f]{64}$")
        self.assertEqual(
            full["candidate_module_vermagic"],
            "7.1.0-rc2 SMP preempt mod_unload aarch64",
        )
        self.assertIn("CONFIG_INITRAMFS_SOURCE", " ".join(full["config_delta_from_running"]))

    def test_all_provenance_is_pinned(self) -> None:
        for key in ("downstream_source", "upstream_source", "android_protocol_reference"):
            record = self.manifest[key]
            self.assertRegex(record["commit"], r"^[0-9a-f]{40}$")
            self.assertRegex(record["driver_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(record["repository"].startswith("https://"))
        downstream = self.manifest["downstream_source"]
        self.assertEqual(
            downstream["driver_sha256"],
            "9315d81b08488a921348a4bc5abb85b3fc7d6ce506e4a0138e54e51ecf834425",
        )
        self.assertEqual(
            self.manifest["android_protocol_reference"]["contract"],
            [
                "BATT_CHG_FULL_DESIGN maps to POWER_SUPPLY_PROP_CHARGE_FULL_DESIGN",
                "BATT_CHG_FULL maps to POWER_SUPPLY_PROP_CHARGE_FULL",
                "non-special battery properties return the firmware value unchanged",
            ],
        )

    def test_installed_upower_contract_is_pinned(self) -> None:
        reference = self.manifest["upower_reference"]
        self.assertEqual(reference["tag"], "v1.91.3")
        self.assertEqual(
            reference["commit"], "3dc5323bf9f8f84b0a038f5f36c34d49e480ee3c"
        )
        self.assertEqual(reference["installed_package"], "upower-1.91.3-1.fc44.aarch64")
        self.assertEqual(
            reference["files"],
            {
                "src/linux/up-device-supply-battery.c": "1678ee184ef0e8fe491a391617a27fda4588f29589bae09abbb8a62185c45823",
                "src/up-device-battery.c": "9d9a076305049e4352c8140e4d652bf972a68555080378df4836538a7bf4c438",
                "src/up-device-battery.h": "123192935c4d127087e8628381170a6d847282143ebd99dd743b664249af8a3d",
            },
        )
        self.assertEqual(
            reference["contract"],
            [
                "energy_full below 0.01 falls back to charge_full and selects charge units",
                "charge full/current/rate values are converted to energy by multiplying by the selected design voltage",
                "missing voltage_max_design and voltage_min_design falls back to voltage_now",
                "missing charge_now is inferred from energy_full multiplied by capacity percentage",
            ],
        )

    def test_patch_digest_and_single_variable(self) -> None:
        self.assertEqual(sha256(PATCH_PATH), self.manifest["patch"]["sha256"])
        self.assertEqual(
            self.patch.count("diff --git a/drivers/power/supply/qcom_battmgr.c"), 1
        )
        additions = [
            line[1:]
            for line in self.patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        self.assertEqual(
            additions,
            [
                "\t\t/* The SM8350-style property protocol reports capacity as charge. */",
                "\t\tbattmgr->unit = QCOM_BATTMGR_UNIT_mAh;",
                "",
            ],
        )
        self.assertNotIn("POWER_SUPPLY_PROP_ENERGY_", self.patch)
        self.assertNotIn("a6xx", self.patch.lower())

    def test_pinned_downstream_source_applies_when_supplied(self) -> None:
        supplied = os.environ.get("PDS017_DRIVER_SOURCE")
        if not supplied:
            self.skipTest("set PDS017_DRIVER_SOURCE for the pinned full-source apply test")
        source = Path(supplied)
        self.assertEqual(sha256(source), self.manifest["downstream_source"]["driver_sha256"])
        patched = apply_patch(source)
        self.assertEqual(
            hashlib.sha256(patched).hexdigest(),
            self.manifest["patch"]["patched_driver_sha256"],
        )

    def test_patch_applies_to_exact_probe_context(self) -> None:
        context = (
            "\n" * 1688
            + '\t\t\t\t\t     "failed to register wireless charing power supply\\n");\n'
            + "\t} else {\n"
            + "\t\tif (battmgr->variant == QCOM_BATTMGR_SM8550)\n"
            + "\t\t\tpsy_desc = &sm8550_bat_psy_desc;\n"
            + "\t\telse\n"
        )
        with tempfile.TemporaryDirectory(prefix="pds017-fixture-") as directory:
            source = Path(directory) / "qcom_battmgr.c"
            source.write_text(context, encoding="utf-8")
            patched = apply_patch(source).decode("utf-8")
        marker = "\t\tbattmgr->unit = QCOM_BATTMGR_UNIT_mAh;\n"
        self.assertEqual(patched.count(marker), 1)
        self.assertLess(patched.index(marker), patched.index("sm8550_bat_psy_desc"))

    def test_live_observation_does_not_claim_a_capacity(self) -> None:
        observation = self.manifest["live_read_only_observation"]
        self.assertEqual(observation["compatible"], "qcom,sm8550-pmic-glink")
        self.assertEqual(observation["charge_full"], "ENODATA")
        self.assertEqual(observation["charge_full_design"], "ENODATA")
        self.assertEqual(observation["energy_full"], "ABSENT")
        self.assertEqual(observation["charge_now"], "ABSENT")
        self.assertEqual(observation["voltage_min_design"], "ABSENT")
        self.assertEqual(observation["voltage_max_design"], "ABSENT")
        self.assertGreater(observation["voltage_now_uv"], 1_000_000)
        self.assertGreater(observation["charge_counter_uah"], 0)


if __name__ == "__main__":
    unittest.main()

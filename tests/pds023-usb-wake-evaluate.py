#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pds023-usb-wake-evaluate.py"
SPEC = importlib.util.spec_from_file_location("pds023_usb_wake", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

BINDING = {
    "repo_revision": "a" * 40,
    "helper_sha256": "b" * 64,
    "udev_rule_sha256": "c" * 64,
}


def complete_evidence() -> dict[str, object]:
    physical: dict[str, object] = {"observer_confirmed": True}
    for name, minimum in MODULE.PHYSICAL_POLICY.items():
        physical[f"{name}_actions"] = minimum
        physical[f"{name}_failures"] = 0
    return {
        "schema": MODULE.EVIDENCE_SCHEMA,
        "binding": BINDING.copy(),
        "cold_boot": {
            "attempts": 5,
            "passes": 5,
            "failures": 0,
            "cancellations": 0,
            "unique_boot_count": 5,
            "driver_bound_count": 5,
            "wake_disabled_before_count": 5,
            "wake_disabled_after_count": 5,
            "observer_confirmed": True,
        },
        "deep_resume": {
            "attempts": 30,
            "passes": 30,
            "failures": 0,
            "cancellations": 0,
            "rtc_resume_count": 30,
            "wake_disabled_before_count": 30,
            "wake_disabled_after_count": 30,
            "renesas_ebusy_count": 0,
            "xhci_error_count": 0,
            "observer_confirmed": True,
        },
        "physical_io": physical,
    }


def parsed(raw: dict[str, object]) -> tuple[dict[str, str], dict[str, object], dict[str, object], dict[str, object]]:
    return MODULE.parse_evidence(raw)


class EvaluationTests(unittest.TestCase):
    def test_complete_matrix_passes_without_private_data(self) -> None:
        report = MODULE.evaluate(*parsed(complete_evidence()), BINDING)
        self.assertTrue(report["complete"])
        self.assertEqual(report["result"], "PASS")
        serialized = json.dumps(report)
        for private in ("/sys/", "USB-SERIAL", "boot_id", "private note"):
            self.assertNotIn(private, serialized)

    def test_empty_template_is_parseable_but_incomplete(self) -> None:
        report = MODULE.evaluate(
            *parsed(MODULE.empty_template(BINDING.copy())), BINDING
        )
        self.assertFalse(report["complete"])
        self.assertEqual(report["result"], "INCOMPLETE")

    def test_cold_boot_faults_fail_exact_gates(self) -> None:
        raw = complete_evidence()
        raw["cold_boot"].update(
            {"unique_boot_count": 4, "wake_disabled_after_count": 4, "observer_confirmed": False}
        )
        report = MODULE.evaluate(*parsed(raw), BINDING)
        gates = report["cold_boot"]["gates"]
        self.assertFalse(gates["five_distinct_boots"])
        self.assertFalse(gates["wake_disabled_after_every_attempt"])
        self.assertFalse(gates["observer_confirmed"])

    def test_deep_faults_fail_exact_gates(self) -> None:
        raw = complete_evidence()
        raw["deep_resume"].update(
            {"attempts": 29, "passes": 29, "rtc_resume_count": 29, "wake_disabled_before_count": 29, "wake_disabled_after_count": 28, "renesas_ebusy_count": 1}
        )
        report = MODULE.evaluate(*parsed(raw), BINDING)
        gates = report["deep_resume"]["gates"]
        self.assertFalse(gates["attempt_floor"])
        self.assertFalse(gates["wake_disabled_after_every_attempt"])
        self.assertFalse(gates["no_renesas_ebusy"])

    def test_each_physical_path_has_its_own_floor_and_failure_gate(self) -> None:
        raw = complete_evidence()
        raw["physical_io"]["usb_c_usb3_actions"] = 4
        raw["physical_io"]["internal_gamepad_failures"] = 1
        report = MODULE.evaluate(*parsed(raw), BINDING)
        gates = report["physical_io"]["gates"]
        self.assertFalse(gates["usb_c_usb3_action_floor"])
        self.assertFalse(gates["internal_gamepad_zero_failures"])

    def test_binding_drift_never_completes(self) -> None:
        raw = complete_evidence()
        raw["binding"]["helper_sha256"] = "d" * 64
        report = MODULE.evaluate(*parsed(raw), BINDING)
        self.assertFalse(report["binding_matches_current_source"])
        self.assertFalse(report["complete"])

    def test_unknown_types_and_impossible_counts_are_rejected(self) -> None:
        raw = complete_evidence()
        raw["helpful"] = True
        with self.assertRaises(MODULE.UsbWakeError):
            parsed(raw)
        raw = complete_evidence()
        raw["deep_resume"]["rtc_resume_count"] = 31
        with self.assertRaisesRegex(MODULE.UsbWakeError, "exceeds attempts"):
            parsed(raw)
        raw = complete_evidence()
        raw["physical_io"]["upper_touch_failures"] = True
        with self.assertRaisesRegex(MODULE.UsbWakeError, "invalid"):
            parsed(raw)


class FileAndStaticTests(unittest.TestCase):
    def test_private_reader_rejects_public_links_duplicates_and_nonfinite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public.json"
            public.write_text("{}", encoding="utf-8")
            public.chmod(0o644)
            with self.assertRaises(MODULE.UsbWakeError):
                MODULE._read_private(public)
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            target.chmod(0o600)
            link = root / "link.json"
            link.symlink_to(target)
            with self.assertRaises(MODULE.UsbWakeError):
                MODULE._read_private(link)
            with self.assertRaises(MODULE.UsbWakeError):
                MODULE._strict_json(b'{"x":1,"x":2}')
            with self.assertRaises(MODULE.UsbWakeError):
                MODULE._strict_json(b'{"x":NaN}')

    def test_template_and_report_are_private_new_and_never_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            template = Path(temporary) / "template.json"
            report = Path(temporary) / "report.json"
            MODULE.write_new(MODULE.empty_template(BINDING), str(template))
            MODULE.write_new({"complete": False}, str(report))
            self.assertEqual(stat.S_IMODE(template.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                MODULE.write_new({}, str(report))

    def test_source_binding_hashes_owned_regular_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pocketds-usb-wake-policy").write_bytes(b"helper")
            (root / "71-pocketds-usb-wakeup.rules").write_bytes(b"rule")
            binding = MODULE.expected_binding("a" * 40, root)
            self.assertEqual(set(binding), MODULE.BINDING_FIELDS)
            (root / "pocketds-usb-wake-policy").unlink()
            (root / "pocketds-usb-wake-policy").symlink_to(root / "71-pocketds-usb-wakeup.rules")
            with self.assertRaises(MODULE.UsbWakeError):
                MODULE.expected_binding("a" * 40, root)

    def test_evaluator_has_no_live_power_usb_service_or_network_path(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "subprocess",
            "/sys/",
            "systemctl",
            "udevadm",
            "rtcwake",
            "requests",
            "urllib",
            "socket",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Tests for the privacy-minimal PDS-006 battery matrix evaluator."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pds006-battery-matrix-evaluate.py"
SPEC = importlib.util.spec_from_file_location("pds006_battery_matrix", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

BINDING = {
    "repo_revision": "a" * 40,
    "panelctl_sha256": "b" * 64,
    "panel_qml_sha256": "c" * 64,
}


def transition(attempts: int, **extras: int) -> dict[str, object]:
    return {
        "attempts": attempts,
        "passes": attempts,
        "failures": 0,
        "cancellations": 0,
        "panelctl_read_failures": 0,
        "telemetry_complete_count": attempts,
        "plasma_restart_delta": 0,
        "observer_confirmed": True,
        **extras,
    }


def complete_evidence() -> dict[str, object]:
    return {
        "schema": MODULE.EVIDENCE_SCHEMA,
        "binding": BINDING.copy(),
        "transitions": {
            "unplug-discharging": transition(
                5,
                external_power_false_count=5,
                discharging_state_count=5,
            ),
            "plug-charging": transition(
                5,
                external_power_true_count=5,
                charging_state_count=5,
            ),
            "full-on-external": transition(
                2,
                external_power_true_count=2,
                full_state_count=2,
                percent_100_count=2,
            ),
        },
        "telemetry-soak": {
            "duration_seconds": 86_400,
            "sample_count": 2_880,
            "panelctl_read_failures": 0,
            "invalid_sample_count": 0,
            "max_gap_seconds": 31,
            "plasma_restart_delta": 0,
            "observer_confirmed": True,
        },
    }


class EvaluationTests(unittest.TestCase):
    def test_complete_matrix_passes_without_raw_or_private_data(self) -> None:
        binding, transitions, soak = MODULE.parse_evidence(complete_evidence())
        report = MODULE.evaluate(binding, transitions, soak, BINDING)
        self.assertTrue(report["complete"])
        self.assertEqual(report["passed_transition_count"], 3)
        self.assertTrue(report["telemetry_soak"]["pass"])
        serialized = json.dumps(report)
        for private in (
            "/sys/class/power_supply",
            "/home/private",
            "SERIAL-PRIVATE",
            "PRIVATE NOTE",
        ):
            self.assertNotIn(private, serialized)

    def test_missing_transition_or_soak_is_incomplete_not_fabricated(self) -> None:
        raw = complete_evidence()
        del raw["transitions"]["plug-charging"]
        raw["telemetry-soak"] = None
        binding, transitions, soak = MODULE.parse_evidence(raw)
        report = MODULE.evaluate(binding, transitions, soak, BINDING)
        self.assertFalse(report["complete"])
        missing = next(
            item
            for item in report["transition_cases"]
            if item["case_id"] == "plug-charging"
        )
        self.assertFalse(missing["present"])
        self.assertEqual(report["telemetry_soak"]["observed_sample_count"], 0)

    def test_transition_faults_fail_exact_gates(self) -> None:
        raw = complete_evidence()
        record = raw["transitions"]["unplug-discharging"]
        record["telemetry_complete_count"] = 4
        record["discharging_state_count"] = 4
        record["plasma_restart_delta"] = 1
        binding, transitions, soak = MODULE.parse_evidence(raw)
        report = MODULE.evaluate(binding, transitions, soak, BINDING)
        item = next(
            candidate
            for candidate in report["transition_cases"]
            if candidate["case_id"] == "unplug-discharging"
        )
        self.assertFalse(item["pass"])
        self.assertFalse(item["gates"]["telemetry_complete_every_attempt"])
        self.assertFalse(item["gates"]["discharging_state_count_every_attempt"])
        self.assertFalse(item["gates"]["plasma_not_restarted"])

    def test_soak_threshold_and_faults_fail_exact_gates(self) -> None:
        raw = complete_evidence()
        raw["telemetry-soak"].update(
            {
                "duration_seconds": 86_399,
                "sample_count": 2_879,
                "invalid_sample_count": 1,
                "max_gap_seconds": 61,
                "observer_confirmed": False,
            }
        )
        binding, transitions, soak = MODULE.parse_evidence(raw)
        report = MODULE.evaluate(binding, transitions, soak, BINDING)
        gates = report["telemetry_soak"]["gates"]
        for gate in (
            "duration_at_least_24h",
            "sample_floor",
                "samples_valid",
                "gap_within_limit",
                "observer_confirmed",
            ):
            self.assertFalse(gates[gate])

    def test_soak_duration_and_sample_coverage_must_be_consistent(self) -> None:
        raw = complete_evidence()
        raw["telemetry-soak"]["max_gap_seconds"] = 1
        binding, transitions, soak = MODULE.parse_evidence(raw)
        report = MODULE.evaluate(binding, transitions, soak, BINDING)
        self.assertFalse(
            report["telemetry_soak"]["gates"][
                "duration_sample_coverage_consistent"
            ]
        )
        self.assertFalse(report["complete"])

    def test_unknown_extra_types_and_impossible_counts_are_rejected(self) -> None:
        raw = complete_evidence()
        raw["transitions"]["invented"] = {}
        with self.assertRaises(MODULE.MatrixError):
            MODULE.parse_evidence(raw)
        raw = complete_evidence()
        raw["transitions"]["plug-charging"]["notes"] = "private"
        with self.assertRaises(MODULE.MatrixError):
            MODULE.parse_evidence(raw)
        raw = complete_evidence()
        raw["transitions"]["plug-charging"]["attempts"] = True
        with self.assertRaises(MODULE.MatrixError):
            MODULE.parse_evidence(raw)
        raw = complete_evidence()
        raw["transitions"]["plug-charging"]["charging_state_count"] = 6
        with self.assertRaises(MODULE.MatrixError):
            MODULE.parse_evidence(raw)
        raw = complete_evidence()
        raw["telemetry-soak"]["panelctl_read_failures"] = 2_880
        raw["telemetry-soak"]["invalid_sample_count"] = 1
        with self.assertRaises(MODULE.MatrixError):
            MODULE.parse_evidence(raw)

    def test_binding_mismatch_never_completes(self) -> None:
        binding, transitions, soak = MODULE.parse_evidence(complete_evidence())
        expected = {**BINDING, "repo_revision": "d" * 40}
        report = MODULE.evaluate(binding, transitions, soak, expected)
        self.assertFalse(report["binding_matches_current_source"])
        self.assertFalse(report["complete"])

    def test_source_binding_hashes_owned_regular_files(self) -> None:
        binding = MODULE.expected_binding("a" * 40, ROOT / "components/control-panel")
        self.assertEqual(binding["repo_revision"], "a" * 40)
        self.assertRegex(binding["panelctl_sha256"], r"^[0-9a-f]{64}$")
        with tempfile.TemporaryDirectory(prefix="pds006-source-") as temporary:
            root = Path(temporary)
            (root / "plasmoid/contents/ui").mkdir(parents=True)
            target = root / "target"
            target.write_text("source", encoding="utf-8")
            (root / "pocketds-panelctl.cpp").symlink_to(target)
            (root / "plasmoid/contents/ui/main.qml").write_text(
                "source", encoding="utf-8"
            )
            with self.assertRaises(MODULE.MatrixError):
                MODULE.expected_binding("a" * 40, root)


class FileTests(unittest.TestCase):
    def test_strict_json_rejects_duplicates_nonfinite_and_non_utf8(self) -> None:
        for content in (
            b'{"schema":1,"schema":1}',
            b'{"value":NaN}',
            b'{"value":Infinity}',
            b"\xff",
        ):
            with self.assertRaises(MODULE.MatrixError):
                MODULE.strict_json(content)

    def test_private_reader_rejects_public_links_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds006-evidence-") as temporary:
            root = Path(temporary)
            path = root / "evidence.json"
            path.write_text(json.dumps(complete_evidence()), encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaises(MODULE.MatrixError):
                MODULE.read_private(path)
            path.chmod(0o600)
            hardlink = root / "hardlink"
            os.link(path, hardlink)
            with self.assertRaises(MODULE.MatrixError):
                MODULE.read_private(path)
            hardlink.unlink()
            path.unlink()
            target = root / "target"
            target.write_text("{}", encoding="utf-8")
            target.chmod(0o600)
            path.symlink_to(target)
            with self.assertRaises(MODULE.MatrixError):
                MODULE.read_private(path)
            path.unlink()
            with path.open("wb") as stream:
                stream.truncate(MODULE.MAX_EVIDENCE_BYTES + 1)
            path.chmod(0o600)
            with self.assertRaises(MODULE.MatrixError):
                MODULE.read_private(path)

    def test_template_and_report_are_private_new_and_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds006-output-") as temporary:
            root = Path(temporary)
            template = root / "ledger.json"
            MODULE.write_new(MODULE.empty_template(BINDING), str(template))
            self.assertEqual(stat.S_IMODE(template.stat().st_mode), 0o600)
            parsed = MODULE.strict_json(MODULE.read_private(template))
            binding, transitions, soak = MODULE.parse_evidence(parsed)
            self.assertEqual(binding, BINDING)
            self.assertEqual(set(transitions), set(MODULE.CASE_POLICY))
            self.assertIsNotNone(soak)
            with self.assertRaises(FileExistsError):
                MODULE.write_new(MODULE.empty_template(BINDING), str(template))


class StaticTests(unittest.TestCase):
    def test_evaluator_cannot_probe_hardware_services_or_power(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "subprocess",
            "/sys/",
            "panelctl status",
            "systemctl",
            "loginctl",
            "upower",
            "suspend",
            "reboot",
            "poweroff",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

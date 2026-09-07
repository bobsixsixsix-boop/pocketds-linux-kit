#!/usr/bin/env python3
"""Tests for the privacy-minimal PDS-001 deep report series."""

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
SCRIPT = ROOT / "scripts/pds001-deep-series.py"
SPEC = importlib.util.spec_from_file_location("pds001_deep_series", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

BINDING = {
    "repo_revision": "a" * 40,
    "helper_reference_sha256": "b" * 64,
}


def snapshot(state: str, external_power: bool) -> dict[str, object]:
    return {
        "gates": {key: True for key in MODULE.SNAPSHOT_GATES},
        "displays": {"DSI-1": True, "DSI-2": True},
        "backlights": {"top": True, "bottom": True},
        "system_services": {key: True for key in MODULE.SYSTEM_SERVICES},
        "user_services": {key: True for key in MODULE.USER_SERVICES},
        "battery": {"state": state, "external_power": external_power},
        "ready": True,
    }


def completed_report(role: str, *, binding: dict[str, str] | None = None) -> dict[str, object]:
    policy = MODULE.ROLE_POLICY[role]
    snap = snapshot(policy["state"], policy["external_power"])
    cycles = [
        {
            "elapsed_boottime_s": float(policy["rtc_seconds"]),
            "gates": {key: True for key in MODULE.CYCLE_GATES},
            "accepted": True,
            "before": snap,
            "after": snap,
        }
        for _index in range(policy["attempts"])
    ]
    return {
        "schema": MODULE.INPUT_SCHEMA,
        "execute": True,
        "rtc_rescue_seconds": policy["rtc_seconds"],
        "requested_attempts": policy["attempts"],
        "phase": "completed",
        "complete": True,
        "accepted": True,
        "source_binding": binding or BINDING.copy(),
        "source_binding_unchanged": True,
        "boot_token": "c" * 64,
        "cycles": cycles,
        "current_attempt": policy["attempts"],
    }


def parsed_matrix() -> list[dict[str, object]]:
    return [
        MODULE.parse_report(completed_report(role))
        for role in MODULE.ROLE_POLICY
    ]


class EvaluationTests(unittest.TestCase):
    def test_complete_automated_matrix_passes_but_never_closes_pds001(self) -> None:
        report = MODULE.evaluate(parsed_matrix())
        self.assertTrue(report["automated_matrix_pass"])
        self.assertEqual(report["observed_cycle_count"], 28)
        self.assertEqual(report["result"], "AUTOMATED_PASS_PHYSICAL_PENDING")
        self.assertFalse(report["pds001_complete"])
        self.assertEqual(report["long_standby_over_5m"], "NOT_EVALUATED")
        self.assertTrue(
            all(value == "NOT_EVALUATED" for value in report["physical_post_resume"].values())
        )
        serialized = json.dumps(report)
        self.assertNotIn("c" * 64, serialized)
        self.assertNotIn("/home/private", serialized)

    def test_role_mismatch_and_duplicate_role_are_incomplete(self) -> None:
        reports = parsed_matrix()
        reports[-1] = MODULE.parse_report(completed_report("discharging-short"))
        result = MODULE.evaluate(reports)
        self.assertFalse(result["automated_matrix_pass"])
        self.assertEqual(result["result"], "INCOMPLETE")

    def test_binding_drift_is_incomplete_and_not_reported_as_one_binding(self) -> None:
        reports = parsed_matrix()
        reports[-1] = MODULE.parse_report(
            completed_report(
                "discharging-five-minute",
                binding={**BINDING, "repo_revision": "d" * 40},
            )
        )
        result = MODULE.evaluate(reports)
        self.assertFalse(result["source_binding_consistent"])
        self.assertIsNone(result["source_binding"])
        self.assertFalse(result["automated_matrix_pass"])

    def test_failed_gate_snapshot_or_top_level_success_is_rejected(self) -> None:
        raw = completed_report("discharging-short")
        raw["cycles"][0]["gates"]["boot_id_unchanged"] = False
        with self.assertRaises(MODULE.SeriesError):
            MODULE.parse_report(raw)
        raw = completed_report("discharging-short")
        raw["cycles"][0]["before"]["gates"]["audio_card_present"] = False
        parsed = MODULE.parse_report(raw)
        self.assertFalse(
            MODULE.role_matches(parsed, MODULE.ROLE_POLICY["discharging-short"])
        )
        raw = completed_report("discharging-short")
        raw["accepted"] = False
        with self.assertRaises(MODULE.SeriesError):
            MODULE.parse_report(raw)

    def test_unknown_extra_wrong_types_and_counts_are_rejected(self) -> None:
        raw = completed_report("full-short")
        raw["notes"] = "private"
        with self.assertRaises(MODULE.SeriesError):
            MODULE.parse_report(raw)
        raw = completed_report("full-short")
        raw["requested_attempts"] = True
        with self.assertRaises(MODULE.SeriesError):
            MODULE.parse_report(raw)
        raw = completed_report("full-short")
        raw["cycles"].pop()
        with self.assertRaises(MODULE.SeriesError):
            MODULE.parse_report(raw)
        raw = completed_report("full-short")
        raw["cycles"][0]["elapsed_boottime_s"] = 151
        with self.assertRaises(MODULE.SeriesError):
            MODULE.parse_report(raw)


class FileTests(unittest.TestCase):
    def test_strict_json_rejects_duplicates_nonfinite_and_non_utf8(self) -> None:
        for content in (
            b'{"schema":1,"schema":1}',
            b'{"value":NaN}',
            b'{"value":Infinity}',
            b"\xff",
        ):
            with self.assertRaises(MODULE.SeriesError):
                MODULE.strict_json(content)

    def test_private_reader_rejects_public_links_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds001-series-input-") as temporary:
            root = Path(temporary)
            path = root / "report.json"
            path.write_text(json.dumps(completed_report("full-short")), encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaises(MODULE.SeriesError):
                MODULE.read_private(path)
            path.chmod(0o600)
            hardlink = root / "hardlink"
            os.link(path, hardlink)
            with self.assertRaises(MODULE.SeriesError):
                MODULE.read_private(path)
            hardlink.unlink()
            path.unlink()
            target = root / "target"
            target.write_text("{}", encoding="utf-8")
            target.chmod(0o600)
            path.symlink_to(target)
            with self.assertRaises(MODULE.SeriesError):
                MODULE.read_private(path)
            path.unlink()
            with path.open("wb") as stream:
                stream.truncate(MODULE.MAX_REPORT_BYTES + 1)
            path.chmod(0o600)
            with self.assertRaises(MODULE.SeriesError):
                MODULE.read_private(path)

    def test_output_is_private_new_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds001-series-output-") as temporary:
            path = Path(temporary) / "series.json"
            report = MODULE.evaluate(parsed_matrix())
            MODULE.write_report(report, str(path))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                MODULE.write_report(report, str(path))


class StaticTests(unittest.TestCase):
    def test_aggregator_has_no_live_power_process_service_or_network_path(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "subprocess",
            "systemctl",
            "loginctl",
            "rtcwake",
            "/sys/",
            "/proc/",
            "suspend",
            "reboot",
            "nmcli",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

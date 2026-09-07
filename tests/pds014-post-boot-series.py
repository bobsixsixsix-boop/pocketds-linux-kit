#!/usr/bin/env python3
"""Tests for the private five-boot PDS-014 report aggregator."""

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
SCRIPT = ROOT / "scripts/pds014-post-boot-series.py"
SPEC = importlib.util.spec_from_file_location("pds014_post_boot_series", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def healthy_report(index: int, *, revision: str = "a" * 40) -> dict[str, object]:
    return {
        "schema": MODULE.INPUT_SCHEMA,
        "mode": "deliberate-post-boot-readonly-matrix",
        "privacy": {key: False for key in MODULE.EXPECTED_PRIVACY},
        "boot_token": f"{index:064x}",
        "repo_revision": revision,
        "start_uptime_s": 100.0,
        "end_uptime_s": 200.0,
        "max_start_uptime_s": 900,
        "steps": [
            {"name": name, "status": "pass", "returncode": 0, "seconds": 1.0}
            for name in MODULE.EXPECTED_STEPS
        ],
        "gates": {key: True for key in MODULE.EXPECTED_GATES},
        "complete": True,
        "result": "PASS",
    }


class EvaluationTests(unittest.TestCase):
    def test_five_distinct_clean_boots_pass_without_tokens_or_paths(self) -> None:
        report = MODULE.evaluate_series([healthy_report(index) for index in range(1, 6)])
        self.assertTrue(report["accepted"])
        self.assertEqual(report["distinct_boot_count"], 5)
        serialized = json.dumps(report)
        for index in range(1, 6):
            self.assertNotIn(f"{index:064x}", serialized)
        self.assertNotIn("/private", serialized)

    def test_duplicate_boot_revision_drift_and_failed_run_are_incomplete(self) -> None:
        duplicate = [healthy_report(index) for index in range(1, 6)]
        duplicate[-1]["boot_token"] = duplicate[0]["boot_token"]
        self.assertFalse(MODULE.evaluate_series(duplicate)["accepted"])

        drift = [healthy_report(index) for index in range(1, 6)]
        drift[-1]["repo_revision"] = "b" * 40
        self.assertFalse(MODULE.evaluate_series(drift)["accepted"])

        failed = [healthy_report(index) for index in range(1, 6)]
        failed[-1]["gates"]["brightness_targets_restored"] = False
        failed[-1]["complete"] = False
        failed[-1]["result"] = "FAIL"
        self.assertFalse(MODULE.evaluate_series(failed)["accepted"])

    def test_missing_report_is_incomplete_not_fabricated(self) -> None:
        report = MODULE.evaluate_series([healthy_report(index) for index in range(1, 5)])
        self.assertFalse(report["accepted"])
        self.assertEqual(report["report_count"], 4)

    def test_wrong_schema_steps_types_and_inconsistent_result_fail_closed(self) -> None:
        bad = healthy_report(1)
        bad["unexpected"] = True
        with self.assertRaises(MODULE.SeriesError):
            MODULE.validate_report(bad)
        bad = healthy_report(1)
        bad["steps"][0]["name"] = "wrong"
        with self.assertRaises(MODULE.SeriesError):
            MODULE.validate_report(bad)
        bad = healthy_report(1)
        bad["gates"]["boot_unchanged"] = 1
        with self.assertRaises(MODULE.SeriesError):
            MODULE.validate_report(bad)
        bad = healthy_report(1)
        bad["complete"] = False
        with self.assertRaises(MODULE.SeriesError):
            MODULE.validate_report(bad)


class FileAndStaticTests(unittest.TestCase):
    def test_reader_requires_private_unique_regular_input_and_strict_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds014-series-") as temporary:
            root = Path(temporary)
            path = root / "boot.json"
            path.write_text(json.dumps(healthy_report(1)), encoding="utf-8")
            path.chmod(0o600)
            report, identity = MODULE.read_private_report(path)
            self.assertTrue(report["complete"])
            self.assertEqual(identity, (path.stat().st_dev, path.stat().st_ino))
            path.chmod(0o644)
            with self.assertRaises(MODULE.SeriesError):
                MODULE.read_private_report(path)
            path.chmod(0o600)
            hard = root / "hard.json"
            os.link(path, hard)
            with self.assertRaises(MODULE.SeriesError):
                MODULE.read_private_report(path)
            hard.unlink()
            link = root / "link.json"
            link.symlink_to(path)
            with self.assertRaises(MODULE.SeriesError):
                MODULE.read_private_report(link)

        with self.assertRaises(MODULE.SeriesError):
            MODULE.strict_json(b'{"complete":true,"complete":false}')
        with self.assertRaises(MODULE.SeriesError):
            MODULE.strict_json(b'{"value":NaN}')

    def test_output_is_private_new_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds014-output-") as temporary:
            output = Path(temporary) / "series.json"
            MODULE.write_report({"accepted": False}, output)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                MODULE.write_report({"accepted": True}, output)

    def test_cli_requires_exactly_five_reports_and_new_output_argument(self) -> None:
        with self.assertRaises(SystemExit):
            MODULE.parse_args(["--reports", "one", "--output", "out"])
        parsed = MODULE.parse_args(
            ["--reports", "1", "2", "3", "4", "5", "--output", "out"]
        )
        self.assertEqual(len(parsed.reports), 5)

    def test_aggregator_has_no_live_power_service_network_or_process_path(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "subprocess",
            "systemctl",
            "reboot",
            "poweroff",
            "os.kill",
            "/sys/power/state",
            "loginctl",
            "requests",
            "urllib",
            "socket",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

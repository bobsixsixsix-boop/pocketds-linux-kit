#!/usr/bin/env python3
"""Tests for private fan-noise markers and evidence alignment."""

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
SCRIPT = ROOT / "scripts/pds009-fan-correlate.py"
SPEC = importlib.util.spec_from_file_location("pds009_fan_correlate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def sample(timestamp: int, value: int, *, decoder: bool = False) -> dict[str, object]:
    return {
        "schema": 1,
        "type": "sample",
        "read_only": True,
        "sample_unix_ms": timestamp,
        "collector_integrity": "PASS",
        "temperature": {"controller_hotspot_c": 40 + value},
        "fan": {
            "target_pwm": {"value": 51 + value},
            "actual_pwm": {"value": 51 + value},
            "actual_rpm": {"value": 2000 + value * 20},
        },
        "compute": {
            "cpu_percent": 10 + value,
            "gpu": {"gpu_percent": 5 + value},
        },
        "video_decode": {
            "decoder_fd_observed": decoder,
            "open_video_fds": [{"process_comm": "PRIVATE-PROCESS"}],
        },
    }


def evidence() -> list[object]:
    samples = [sample(1000, 0), sample(2500, 1), sample(4000, 4, decoder=True)]
    return samples + [
        {
            "schema": 1,
            "type": "summary",
            "read_only": True,
            "sample_count": len(samples),
            "collector_integrity": "PASS",
            "controller_policy_evaluation": "PASS",
        }
    ]


def marker(timestamp: int) -> dict[str, object]:
    return {
        "schema": MODULE.MARKER_SCHEMA,
        "type": "noise-spike",
        "unix_ms": timestamp,
    }


class CorrelationTests(unittest.TestCase):
    def test_marker_aligns_to_surrounding_samples_without_private_fields(self) -> None:
        samples, summary = MODULE.parse_evidence(evidence())
        report = MODULE.correlate(samples, summary, [3000], max_offset_ms=2000)
        self.assertTrue(report["complete"])
        event = report["events"][0]
        self.assertEqual(event["before_offset_ms"], 500)
        self.assertEqual(event["after_offset_ms"], 1000)
        self.assertEqual(event["target_pwm_delta"], 3)
        self.assertEqual(event["rpm_delta"], 60)
        self.assertFalse(event["decoder_before"])
        self.assertTrue(event["decoder_after"])
        serialized = json.dumps(report)
        self.assertNotIn("PRIVATE-PROCESS", serialized)
        self.assertNotIn("3000", serialized)
        self.assertEqual(report["acoustic_causality"], "NOT_DETERMINED")

    def test_unmatched_marker_and_failed_policy_do_not_complete(self) -> None:
        samples, summary = MODULE.parse_evidence(evidence())
        summary["controller_policy_evaluation"] = "FAIL"
        report = MODULE.correlate(samples, summary, [9000], max_offset_ms=1000)
        self.assertFalse(report["complete"])
        self.assertEqual(report["unmatched_marker_count"], 1)
        self.assertFalse(report["gates"]["collector_and_policy_pass"])

    def test_non_monotonic_samples_and_summary_mismatch_fail_closed(self) -> None:
        records = evidence()
        records[1]["sample_unix_ms"] = 1000
        with self.assertRaises(MODULE.CorrelationError):
            MODULE.parse_evidence(records)
        records = evidence()
        records[-1]["sample_count"] = 99
        with self.assertRaises(MODULE.CorrelationError):
            MODULE.parse_evidence(records)

    def test_non_finite_metrics_fail_closed(self) -> None:
        records = evidence()
        records[0]["compute"]["cpu_percent"] = float("nan")
        with self.assertRaises(MODULE.CorrelationError):
            MODULE.parse_evidence(records)


class MarkerAndFileTests(unittest.TestCase):
    def test_marker_append_is_private_bounded_and_parseable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds009-marker-") as temporary:
            path = Path(temporary) / "markers.jsonl"
            MODULE.append_marker(path, unix_ms=1000)
            MODULE.append_marker(path, unix_ms=2000)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            records = MODULE.parse_jsonl(
                MODULE.read_private_bytes(path, maximum=MODULE.MAX_MARKER_BYTES),
                maximum_records=MODULE.MAX_MARKERS,
            )
            self.assertEqual(MODULE.parse_markers(records), [1000, 2000])

    def test_private_reader_rejects_public_linked_and_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds009-marker-") as temporary:
            root = Path(temporary)
            path = root / "markers.jsonl"
            path.write_text(json.dumps(marker(1000)) + "\n", encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaises(MODULE.CorrelationError):
                MODULE.read_private_bytes(path, maximum=MODULE.MAX_MARKER_BYTES)
            path.chmod(0o600)
            hardlink = root / "hardlink"
            os.link(path, hardlink)
            with self.assertRaises(MODULE.CorrelationError):
                MODULE.read_private_bytes(path, maximum=MODULE.MAX_MARKER_BYTES)
            hardlink.unlink()
            path.unlink()
            target = root / "target"
            target.write_text(json.dumps(marker(1000)), encoding="utf-8")
            target.chmod(0o600)
            path.symlink_to(target)
            with self.assertRaises(MODULE.CorrelationError):
                MODULE.read_private_bytes(path, maximum=MODULE.MAX_MARKER_BYTES)
            path.unlink()
            with path.open("wb") as stream:
                stream.truncate(MODULE.MAX_MARKER_BYTES + 1)
            path.chmod(0o600)
            with self.assertRaises(MODULE.CorrelationError):
                MODULE.read_private_bytes(path, maximum=MODULE.MAX_MARKER_BYTES)

    def test_jsonl_rejects_duplicate_keys_and_non_finite_numbers(self) -> None:
        for content in (
            b'{"schema":1,"schema":1}\n',
            b'{"metric":NaN}\n',
            b'{"metric":Infinity}\n',
        ):
            with self.assertRaises(MODULE.CorrelationError):
                MODULE.parse_jsonl(content, maximum_records=1)

    def test_report_is_private_new_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds009-report-") as temporary:
            output = Path(temporary) / "report.json"
            MODULE.write_report({"complete": False}, str(output))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                MODULE.write_report({"complete": True}, str(output))


class StaticTests(unittest.TestCase):
    def test_tool_never_starts_workloads_or_changes_fan_state(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "subprocess",
            "os.system",
            "fancontrol-set-profile",
            "write_pwm",
            "chromium",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('"acoustic_causality": "NOT_DETERMINED"', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

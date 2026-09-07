#!/usr/bin/env python3
"""Fixture and static safety tests for the PDS-008 telemetry soak."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pds008-telemetry-soak.py"
SPEC = importlib.util.spec_from_file_location("pds008_telemetry_soak", SCRIPT)
assert SPEC and SPEC.loader
soak = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = soak
SPEC.loader.exec_module(soak)


def payload(
    timestamp: int,
    display_timestamp: int,
    *,
    app_fps=None,
    top_refresh: float = 165.0,
):
    return {
        "sample_unix_ms": timestamp,
        "display_sample_unix_ms": display_timestamp,
        "display_status": "ok",
        "display_dsi1_refresh_hz": top_refresh,
        "display_dsi2_refresh_hz": 59.999,
        "display_app_fps": app_fps,
    }


def service(cpu: int, memory: int = 14_000_000):
    return {
        "active_state": "active",
        "sub_state": "running",
        "main_pid": 1234,
        "n_restarts": 0,
        "memory_current": memory,
        "memory_peak": memory,
        "cpu_usage_nsec": cpu,
    }


class AccumulatorTests(unittest.TestCase):
    def test_healthy_report_keeps_physical_refresh_distinct_from_fps(self):
        accumulator = soak.Accumulator(stale_after_ms=5000)
        base = 1_800_000_000_000
        accumulator.observe(payload(base, base), base + 10)
        accumulator.observe(payload(base + 2000, base, app_fps=None), base + 2010)
        accumulator.observe(payload(base + 4000, base + 60_000), base + 4010)
        report = soak.make_report(
            accumulator,
            started_unix_ms=base,
            elapsed_s=6.0,
            requested_duration_s=6.0,
            interval_s=2.0,
            expected_display_interval_s=60.0,
            completion_reason="completed",
            service_before=service(1_000_000_000),
            service_after=service(1_020_000_000, 14_001_000),
            observer_cpu_s=0.001,
            max_gpu_gap_ms=5000,
            max_service_cpu_percent=0.5,
            max_observer_cpu_percent=0.1,
            max_memory_growth_bytes=8 * 1024 * 1024,
        )
        self.assertEqual(report["distinct_gpu_timestamps"], 3)
        self.assertEqual(report["max_gpu_timestamp_gap_ms"], 2000)
        self.assertEqual(report["refresh_pairs_hz"], [[165.0, 59.999]])
        self.assertEqual(report["refresh_pair_transition_count"], 0)
        self.assertEqual(report["refresh_pair_transition_events"], [])
        self.assertFalse(report["refresh_pair_transition_events_truncated"])
        self.assertEqual(report["display_app_fps_nonnull_count"], 0)
        self.assertIsNone(report["terminal_error"])
        self.assertTrue(report["accepted"])

    def test_regression_stale_duplicate_and_fabricated_fps_fail_gates(self):
        accumulator = soak.Accumulator(stale_after_ms=5000)
        base = 1_800_000_000_000
        accumulator.observe(payload(base, base), base + 10)
        accumulator.observe(payload(base, base, app_fps=60), base + 7000)
        accumulator.observe(payload(base - 1, base), base + 8000)
        fields = accumulator.fields()
        self.assertEqual(fields["distinct_gpu_timestamps"], 2)
        self.assertEqual(fields["gpu_timestamp_regressions"], 1)
        self.assertEqual(fields["stale_sample_reads"], 2)
        self.assertEqual(fields["display_app_fps_nonnull_count"], 1)

    def test_refresh_change_records_a_bounded_timestamped_transition(self):
        accumulator = soak.Accumulator(stale_after_ms=5000)
        base = 1_800_000_000_000
        accumulator.observe(payload(base, base), base + 10)
        accumulator.observe(
            payload(base + 2000, base + 60_000, top_refresh=120.0),
            base + 2010,
        )
        fields = accumulator.fields()
        self.assertEqual(
            fields["refresh_pairs_hz"], [[120.0, 59.999], [165.0, 59.999]]
        )
        self.assertEqual(fields["refresh_pair_transition_count"], 1)
        self.assertEqual(
            fields["refresh_pair_transition_events"],
            [
                {
                    "read_index": 2,
                    "display_sample_unix_ms": base + 60_000,
                    "from_hz": [165.0, 59.999],
                    "to_hz": [120.0, 59.999],
                }
            ],
        )
        self.assertFalse(fields["refresh_pair_transition_events_truncated"])

        for index in range(soak.MAX_REFRESH_TRANSITION_EVENTS + 5):
            refresh = 165.0 if index % 2 else 120.0
            accumulator.observe(
                payload(base + 4000 + index, base + 120_000 + index, top_refresh=refresh),
                base + 4010 + index,
            )
        fields = accumulator.fields()
        self.assertGreater(
            fields["refresh_pair_transition_count"],
            soak.MAX_REFRESH_TRANSITION_EVENTS,
        )
        self.assertEqual(
            len(fields["refresh_pair_transition_events"]),
            soak.MAX_REFRESH_TRANSITION_EVENTS,
        )
        self.assertTrue(fields["refresh_pair_transition_events_truncated"])


class FileSafetyTests(unittest.TestCase):
    def test_private_cache_and_atomic_report_modes(self):
        with tempfile.TemporaryDirectory(prefix="pds008-soak-") as name:
            root = Path(name)
            cache = root / "cache.json"
            cache.write_text(json.dumps(payload(1, 1)), encoding="utf-8")
            cache.chmod(0o600)
            self.assertEqual(soak.read_private_json(cache)["sample_unix_ms"], 1)

            cache.chmod(0o644)
            with self.assertRaisesRegex(soak.SoakError, "unsafe-mode"):
                soak.read_private_json(cache)
            cache.unlink()
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "cache.json"
            link.symlink_to(target)
            with self.assertRaises(soak.SoakError):
                soak.read_private_json(link)

            output = root / "result.json"
            soak.atomic_write_json(output, {"accepted": False})
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertFalse(any(root.glob(".*.tmp")))

    def test_service_parser_is_read_only_and_typed(self):
        stdout = "\n".join(
            (
                f"Id={soak.SERVICE}",
                "ActiveState=active",
                "SubState=running",
                "MainPID=1234",
                "NRestarts=0",
                "MemoryCurrent=14000000",
                "MemoryPeak=15000000",
                "CPUUsageNSec=9000000000",
            )
        )

        def runner(argv, **kwargs):
            self.assertEqual(argv[0:4], ["/usr/bin/systemctl", "--user", "show", "--no-pager"])
            self.assertNotIn("restart", argv)
            self.assertNotIn("stop", argv)
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

        snapshot = soak.service_snapshot(runner)
        self.assertEqual(snapshot["main_pid"], 1234)
        self.assertEqual(snapshot["cpu_usage_nsec"], 9_000_000_000)


class StaticContractTests(unittest.TestCase):
    def test_no_mutating_process_or_power_verbs(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "systemctl restart",
            "systemctl stop",
            "systemctl kill",
            "kwin_wayland --replace",
            "sudo ",
            "/sys/",
            "pocketds-panelctl",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('"read_only": True', source)
        self.assertIn('"display_app_fps_nonnull_count"', source)
        self.assertIn("fcntl.LOCK_EX | fcntl.LOCK_NB", source)

    def test_argument_bounds_and_existing_output_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds008-soak-cli-") as name:
            output = Path(name) / "result.json"
            output.write_text("keep\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--duration", "0.2", "--output", str(output)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(output.read_text(encoding="utf-8"), "keep\n")
            self.assertIn("output already exists", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

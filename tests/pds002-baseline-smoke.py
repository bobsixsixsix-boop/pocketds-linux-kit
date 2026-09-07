#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import io
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "scripts" / "pds002-gpu-kwin-baseline.py"
spec = importlib.util.spec_from_file_location("pds002_gpu_kwin_baseline", SOURCE)
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = collector
spec.loader.exec_module(collector)


class JournalClassificationTests(unittest.TestCase):
    def assert_category(self, category: str, message: str, source: str = "kernel") -> None:
        self.assertIn(category, collector.classify_journal(message, source))

    def test_all_required_categories(self) -> None:
        self.assert_category(
            "kwin_atomic_ebusy",
            "atomic commit failed: Device or resource busy",
            "kwin_wayland",
        )
        self.assert_category(
            "kwin_atomic_ebusy",
            "atomic commit failed: 设备或资源忙",
            "kwin_wayland",
        )
        self.assert_category(
            "gmu_oob_timeout", "Timeout waiting for GMU OOB set GPU_SET: 0x0"
        )
        self.assert_category("gpu_lockup", "hangcheck detected gpu lockup rb 0!")
        self.assert_category("gpu_recover", "recover_worker: hangcheck recover!")
        self.assert_category("gpu_offender", "offending task: kwin_wayland")
        self.assert_category("hfi_error", "HFI response timeout while starting GMU")
        self.assert_category("smmu_fault", "arm-smmu: Unhandled context fault")
        self.assert_category(
            "fenced_register_delay", "delay in fenced register write (0x8a1)"
        )
        self.assert_category("dma_fence_log", "blocked in dma_fence_default_wait")
        self.assert_category("hung_task", "task foo blocked for more than 120 seconds")

    def test_classification_avoids_known_false_positives(self) -> None:
        self.assertNotIn(
            "kwin_atomic_ebusy",
            collector.classify_journal(
                "atomic commit failed: Device or resource busy", "test-helper"
            ),
        )
        self.assertNotIn(
            "kwin_atomic_ebusy",
            collector.classify_journal("atomic commit failed: Invalid argument", "kwin_wayland"),
        )
        self.assertNotIn(
            "hfi_error", collector.classify_journal("Loaded GMU-HFI queue table", "kernel")
        )
        self.assertNotIn(
            "gmu_oob_timeout", collector.classify_journal("Loaded GMU firmware", "kernel")
        )

    def test_json_journal_stream_counting(self) -> None:
        entries = [
            {
                "MESSAGE": "atomic commit failed: Device or resource busy",
                "_COMM": "kwin_wayland",
            },
            {"MESSAGE": "hangcheck recover!", "_COMM": "kernel"},
            {"MESSAGE": "unrelated", "_COMM": "kernel"},
        ]
        stream = io.StringIO("\n".join(json.dumps(entry) for entry in entries))
        counts = collector.count_journal_stream(stream)
        self.assertEqual(counts["kwin_atomic_ebusy"], 1)
        self.assertEqual(counts["gpu_recover"], 1)


class ParsingTests(unittest.TestCase):
    def test_pressure(self) -> None:
        parsed = collector.parse_pressure(
            "some avg10=0.57 avg60=0.35 avg300=0.43 total=88281019\n"
            "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
        )
        assert parsed is not None
        self.assertEqual(parsed["some"]["avg10"], 0.57)
        self.assertEqual(parsed["some"]["total"], 88281019)
        self.assertEqual(parsed["full"]["total"], 0)

    def test_existing_gpu_telemetry_and_age(self) -> None:
        payload = {
            "source": "msm-drm-fdinfo",
            "semantics": "observable-drm-client-load",
            "sample_unix_ms": 10_000,
            "gpu_percent": 4.2,
            "gpu_freq_hz": 220_000_000,
            "gpu_temp_c": 45.1,
            "gpu_clients": 6,
        }
        parsed = collector.parse_gpu_status(json.dumps(payload), 12_500)
        assert parsed is not None
        self.assertEqual(parsed["age_ms"], 2500)
        self.assertEqual(parsed["gpu_percent"], 4.2)
        self.assertIsNone(collector.parse_gpu_status("not json", 1))

    def test_kwin_matrix_and_stable_signature(self) -> None:
        support = """Operation Mode: Wayland
LogicalOutput backend
--------------
Name: DRM
Atomic Mode Setting on GPU 0: true
Screens
-------
Number of Screens: 2

Screen 0:
---------
Name: DSI-1
Enabled: 1
Geometry: 0,0,1280x720
Scale: 1.5
Refresh Rate: 165000
Adaptive Sync: incapable
Screen 1:
---------
Name: DSI-2
Enabled: 1
Geometry: 283,720,819x614
Scale: 1.25
Refresh Rate: 59999
Adaptive Sync: incapable

Compositing
-----------
OpenGL renderer string: FD740
Mesa version: 26.1.8
"""
        parsed = collector.parse_kwin_support(support)
        assert parsed is not None
        self.assertEqual(parsed["backend"], "DRM")
        self.assertEqual(parsed["outputs"][0]["refresh_millihz"], 165000)
        self.assertEqual(parsed["outputs"][1]["refresh_millihz"], 59999)
        first = collector.signature_for(parsed)
        second = collector.signature_for(dict(reversed(list(parsed.items()))))
        self.assertEqual(first, second)
        changed = json.loads(json.dumps(parsed))
        changed["outputs"][0]["refresh_millihz"] = 60000
        self.assertNotEqual(first, collector.signature_for(changed))

    def test_proc_status(self) -> None:
        parsed = collector.parse_proc_status(
            "Name:\tkwin_wayland\nState:\tD (disk sleep)\nUid:\t1000\t1000\t1000\t1000\n"
        )
        self.assertTrue(parsed["State"].startswith("D"))
        self.assertEqual(parsed["Name"], "kwin_wayland")
        self.assertTrue(collector.is_fence_wchan("dma_fence_default_wait"))
        self.assertFalse(collector.is_fence_wchan("ep_poll"))

    def test_kernel_notes_identity_is_bounded_hashed_and_link_safe(self) -> None:
        with tempfile.TemporaryDirectory(dir=HERE.parent / "build") as temporary:
            root = Path(temporary)
            notes = root / "notes"
            content = b"fixture-kernel-notes"
            notes.write_bytes(content)
            notes.chmod(0o444)
            measured = collector.kernel_notes_snapshot(notes)
            self.assertEqual(measured["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(measured["size"], len(content))
            self.assertIsNone(measured["error"])
            linked = root / "linked"
            linked.symlink_to(notes)
            self.assertEqual(
                collector.kernel_notes_snapshot(linked)["error"], "OSError"
            )

    def test_fixed_control_identity_is_content_bound_and_write_safe(self) -> None:
        with tempfile.TemporaryDirectory(dir=HERE.parent / "build") as temporary:
            root = Path(temporary)
            kwin = root / "kwin_wayland"
            firmware = root / "a740_sqe.fw"
            kwin.write_bytes(b"fixture-kwin")
            firmware.write_bytes(b"fixture-firmware")
            kwin.chmod(0o555)
            firmware.chmod(0o444)
            measured = collector.fixed_control_snapshot(
                {"kwin": kwin, "firmware": firmware}
            )
            self.assertEqual(
                measured["kwin"]["sha256"], hashlib.sha256(b"fixture-kwin").hexdigest()
            )
            self.assertIsNone(measured["firmware"]["error"])
            firmware.chmod(0o666)
            self.assertEqual(
                collector.regular_artifact_snapshot(firmware)["error"], "unsafe-metadata"
            )


class StatisticsTests(unittest.TestCase):
    def test_bursts_and_gap(self) -> None:
        result = collector.burst_summary([0.0, 1.0, 4.0, 12.0, 13.0], 5.0)
        self.assertEqual(result["burst_count"], 2)
        self.assertEqual(result["max_events"], 3)
        self.assertEqual(result["max_span_s"], 4.0)
        self.assertEqual(result["max_inter_event_gap_s"], 8.0)

    def test_rate(self) -> None:
        stats = collector.EventStats(5.0)
        stats.add("gpu_lockup", 1.0)
        stats.add("gpu_lockup", 2.0)
        self.assertEqual(stats.rates(1800.0)["gpu_lockup"], 4.0)

    def test_d_state_lifecycle(self) -> None:
        tracker = collector.DStateTracker()
        record = {
            42: {
                "pid": 42,
                "name": "kwin_wayland",
                "uid": "1000",
                "state": "D (disk sleep)",
                "wchan": "dma_fence_default_wait",
                "command": "kwin_wayland",
            }
        }
        entered = tracker.update(record, 10.0)
        self.assertEqual(
            [event["category"] for event in entered],
            ["d_state_enter", "dma_fence_wait"],
        )
        exited = tracker.update({}, 16.5)
        self.assertEqual(exited[0]["category"], "d_state_exit")
        self.assertEqual(tracker.intervals[0]["duration_s"], 6.5)

    def test_sustained_interactive_d_state_is_critical_without_wchan(self) -> None:
        intervals = [
            {"name": "kwin_wayland", "duration_s": 10.0, "wchan": "0"},
            {"name": "plasma-plasmas", "duration_s": 8.0, "wchan": "0"},
            {"name": "sugov:3", "duration_s": 30.0, "wchan": "0"},
            {"name": "chromium", "duration_s": 2.0, "wchan": "0"},
        ]
        critical = collector.critical_user_d_state_intervals(intervals, 7.5)
        self.assertEqual(
            [item["name"] for item in critical],
            ["kwin_wayland", "plasma-plasmas"],
        )

    def test_unknown_or_invalid_d_state_records_are_not_critical(self) -> None:
        intervals = [
            {"name": None, "duration_s": 100.0},
            {"name": "kwin_wayland", "duration_s": None},
            {"name": "jbd2/sda", "duration_s": 100.0},
        ]
        self.assertEqual(
            collector.critical_user_d_state_intervals(intervals, 1.0), []
        )


class ReadOnlyContractTests(unittest.TestCase):
    def test_collector_has_no_device_file_write_apis_or_forbidden_commands(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        forbidden = (
            "kscreen-doctor",
            "modetest",
            "tuned-adm",
            "systemctl",
            "tempfile",
            ".mkdir(",
            ".write_text(",
            ".write_bytes(",
            "makedirs(",
            "mkstemp(",
            "NamedTemporaryFile",
            "backlight",
            "devfreq",
            "debugfs",
        )
        for token in forbidden:
            self.assertNotIn(token, source, token)
        self.assertNotRegex(source, r"open\s*\([^\n]*['\"](?:w|a|x|r\+)['\"]")

    def test_stdout_only_end_to_end_smoke(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SOURCE),
                "--duration",
                "0.3",
                "--interval",
                "0.1",
                "--no-boot-snapshot",
                "--no-journal-follow",
                "--repo-revision",
                "test-fixture",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(records[0]["type"], "header")
        self.assertEqual(records[-1]["type"], "summary")
        self.assertTrue(any(record["type"] == "sample" for record in records))
        self.assertEqual(records[0]["read_only"]["device_output"], "stdout-jsonl-only")
        self.assertEqual(records[0]["repo_revision"], "test-fixture")
        self.assertEqual(records[0]["workload_sha256"], "unspecified")
        self.assertIn("notes", records[0]["boot"])
        self.assertIn("kwin_wayland", records[0]["fixed_controls"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

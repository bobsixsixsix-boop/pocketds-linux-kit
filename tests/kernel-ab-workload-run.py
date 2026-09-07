#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "kernel-ab" / "workload" / "run.py"
SPEC = importlib.util.spec_from_file_location("pocketds_kernel_ab_workload_run_test", SOURCE)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


class WorkloadRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest, self.manifest_sha256 = runner.preflight.load_manifest()
        self.lock, _lock_sha256 = runner.evaluate.load_lock()

    def test_default_plan_is_static_and_starts_nothing(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                os.fspath(SOURCE),
                "--variant", "baseline",
                "--profile", "dual-165-60",
                "--round", "1",
            ],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        report = json.loads(completed.stdout)
        self.assertEqual(report["action"], "plan_only")
        self.assertFalse(report["live_preflight_performed"])
        self.assertFalse(report["browser_started"])
        self.assertFalse(report["collector_started"])
        self.assertFalse(report["candidate_install_authorized"])

    def test_live_preflight_cli_runs_every_read_only_gate_and_starts_nothing(self) -> None:
        live = {
            "kernel_notes_sha256": "1" * 64,
            "display_signature": "2" * 64,
            "fixed_controls": {},
            "runtime_controls": {},
            "preexisting_workload_browser_processes": 0,
        }
        output = io.StringIO()
        with (
            mock.patch.object(
                runner.preflight,
                "load_manifest",
                return_value=(self.manifest, self.manifest_sha256),
            ),
            mock.patch.object(
                runner.evaluate,
                "load_lock",
                return_value=(self.lock, "3" * 64),
            ),
            mock.patch.object(runner.preflight, "collect") as collect,
            mock.patch.object(runner, "repository_revision", return_value="4" * 40),
            mock.patch.object(runner, "session_environment", return_value={}) as session,
            mock.patch.object(runner, "live_preflight", return_value=live) as live_check,
            mock.patch("sys.stdout", output),
        ):
            result = runner.main(
                [
                    "--variant", "baseline",
                    "--profile", "dual-165-60",
                    "--round", "1",
                    "--live-preflight",
                ]
            )
        self.assertEqual(result, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["action"], "live_preflight")
        self.assertEqual(report["live_preflight"], live)
        self.assertTrue(report["desktop_session_valid"])
        self.assertFalse(report["browser_started"])
        self.assertFalse(report["collector_started"])
        self.assertFalse(report["display_or_boot_state_changed"])
        self.assertFalse(report["candidate_install_authorized"])
        collect.assert_called_once_with(self.manifest, self.manifest_sha256)
        session.assert_called_once_with(os.getuid())
        live_check.assert_called_once_with("dual-165-60", "baseline", self.lock)

    def test_execution_requires_exact_confirmation_output_and_matrix_bounds(self) -> None:
        for arguments in (
            ["--variant", "baseline", "--profile", "dual-165-60", "--round", "1", "--execute"],
            ["--variant", "baseline", "--profile", "dual-165-60", "--round", "1", "--output", "x"],
            ["--variant", "baseline", "--profile", "dual-165-60", "--round", "1", "--live-preflight", "--output", "x"],
            ["--variant", "baseline", "--profile", "dual-165-60", "--round", "1", "--live-preflight", "--execute"],
            ["--variant", "baseline", "--profile", "dual-165-60", "--round", "4"],
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(SystemExit):
                    runner.parse_args(arguments)

    def test_commands_are_fixed_shell_free_offline_and_content_bound(self) -> None:
        profile = Path("/run/user/1000/pds002-private-profile")
        browser, collector = runner.build_commands(
            self.manifest, self.manifest_sha256, profile, "1" * 40
        )
        self.assertEqual(browser[0], runner.preflight.EXPECTED_BROWSER_PATHS["wrapper"])
        self.assertIn("--disable-background-networking", browser)
        self.assertIn("--host-resolver-rules=MAP * ~NOTFOUND", browser)
        self.assertIn(f"--user-data-dir={profile}", browser)
        self.assertTrue(any(item.startswith("--app=file://") for item in browser))
        self.assertEqual(collector[0], os.fspath(runner.COLLECTOR_PATH))
        self.assertIn(self.manifest_sha256, collector)
        self.assertIn("2700", collector)
        self.assertNotIn("sh", (Path(browser[0]).name, Path(collector[0]).name))

    def test_live_preflight_binds_variant_profile_and_zero_existing_browser(self) -> None:
        display = {
            "kwin": {
                "backend": "DRM",
                "atomic": "true",
                "renderer": "FD740",
                "outputs": [
                    {"name": "DSI-1", "enabled": True, "refresh_millihz": 165000},
                    {"name": "DSI-2", "enabled": True, "refresh_millihz": 59999},
                ],
            },
            "signature": "a" * 64,
        }
        notes = {
            "sha256": self.lock["variants"]["baseline"]["notes_sha256"],
            "size": 128,
            "error": None,
        }
        fixed_controls = {
            name: {"sha256": item["sha256"], "size": item["size"], "error": None}
            for name, item in self.lock["fixed_controls"].items()
        }
        runtime_controls = {
            name: {"sha256": item["sha256"], "size": item["size"], "error": None}
            for name, item in self.lock["runtime_controls"].items()
        }
        with (
            mock.patch.object(runner.collector, "kernel_notes_snapshot", return_value=notes),
            mock.patch.object(runner.collector, "display_snapshot", return_value=display),
            mock.patch.object(runner.collector, "fixed_control_snapshot", return_value=fixed_controls),
            mock.patch.object(runner.collector, "runtime_control_snapshot", return_value=runtime_controls),
            mock.patch.object(runner, "browser_process_count", return_value=0),
        ):
            report = runner.live_preflight("dual-165-60", "baseline", self.lock)
            self.assertEqual(report["preexisting_workload_browser_processes"], 0)
        with (
            mock.patch.object(runner.collector, "kernel_notes_snapshot", return_value=notes),
            mock.patch.object(runner.collector, "display_snapshot", return_value=display),
            mock.patch.object(runner.collector, "fixed_control_snapshot", return_value=fixed_controls),
            mock.patch.object(runner.collector, "runtime_control_snapshot", return_value=runtime_controls),
            mock.patch.object(runner, "browser_process_count", return_value=1),
        ):
            with self.assertRaisesRegex(runner.RunError, "close every"):
                runner.live_preflight("dual-165-60", "baseline", self.lock)
        drifted_runtime = dict(runtime_controls)
        drifted_runtime["fan_profile"] = {
            "sha256": "0" * 64,
            "size": 11,
            "error": None,
        }
        with (
            mock.patch.object(runner.collector, "kernel_notes_snapshot", return_value=notes),
            mock.patch.object(runner.collector, "display_snapshot", return_value=display),
            mock.patch.object(runner.collector, "fixed_control_snapshot", return_value=fixed_controls),
            mock.patch.object(runner.collector, "runtime_control_snapshot", return_value=drifted_runtime),
        ):
            with self.assertRaisesRegex(runner.RunError, "pocketds-performance"):
                runner.live_preflight("dual-165-60", "baseline", self.lock)

    def test_output_is_exact_private_new_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            parent = Path(temporary)
            parent.chmod(0o700)
            expected = runner.expected_basename("candidate", "upper-only-165", 3)
            output = parent / expected
            descriptor, parent_fd = runner.reserve_output(output, expected)
            os.write(descriptor, b"fixture\n")
            os.close(descriptor)
            os.close(parent_fd)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            before = output.read_bytes()
            with self.assertRaisesRegex(runner.RunError, "new regular file"):
                runner.reserve_output(output, expected)
            self.assertEqual(output.read_bytes(), before)
            with self.assertRaisesRegex(runner.RunError, "basename differs"):
                runner.reserve_output(parent / "wrong.jsonl", expected)

    def test_source_has_no_boot_display_service_or_shell_mutation(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "shell=True", "fastboot", "flash", "reboot", "kscreen-doctor",
            "xrandr", "systemctl restart", "systemctl stop", "/boot/",
        ):
            self.assertNotIn(forbidden, source)
        completed = subprocess.run(
            [sys.executable, os.fspath(SOURCE), "--help"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.assertNotIn("--duration", completed.stdout)
        self.assertIn("--live-preflight", completed.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

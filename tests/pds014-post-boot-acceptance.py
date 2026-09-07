#!/usr/bin/env python3
"""Tests for the gated PDS-014 post-boot read-only matrix."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pds014-post-boot-acceptance.py"
SPEC = importlib.util.spec_from_file_location("pds014_post_boot", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def services(units: tuple[str, ...], *, pid_base: int = 100) -> str:
    blocks = []
    for index, unit in enumerate(units):
        blocks.append(
            "\n".join(
                (
                    f"MainPID={pid_base + index}",
                    "NRestarts=0",
                    "SubState=running",
                    "ActiveState=active",
                    f"Id={unit}",
                )
            )
        )
    return "\n\n".join(blocks) + "\n"


def healthy_results() -> list[object]:
    values = {
        "repo-revision-before": "a" * 40 + "\n",
        "repo-status-before": "",
        "system-services-before": services(MODULE.SYSTEM_SERVICES),
        "user-services-before": services(MODULE.USER_SERVICES, pid_base=200),
        "full-tests": "PASS\n",
        "hardware-readonly": "PASS\n",
        "suspend-preflight": "preflight PASS; no suspend was attempted\n",
        "diagnostic-bundle": "Diagnostic bundle: PRIVATE-PATH\nIt remains local\n",
        "repo-revision-after": "a" * 40 + "\n",
        "repo-status-after": "",
        "system-services-after": services(MODULE.SYSTEM_SERVICES),
        "user-services-after": services(MODULE.USER_SERVICES, pid_base=200),
    }
    return [MODULE.Result(step.name, "pass", 0, 1.0, values[step.name], "") for step in MODULE.build_plan(ROOT)]


def observation_arguments(*, brightness: bool = True, renesas: bool = True) -> dict[str, object]:
    observations = {
        "brightness_targets_restored": brightness,
        "renesas_xhci_wake_disabled": renesas,
    }
    return {
        "boot_token": "b" * 64,
        "boot_observations_before": observations,
        "boot_observations_after": dict(observations),
    }


class PlanTests(unittest.TestCase):
    def test_default_plan_is_read_only_and_never_executes_power_actions(self) -> None:
        plan = MODULE.build_plan(ROOT)
        flattened = [token for step in plan for token in step.argv]
        self.assertEqual([step.name for step in plan].count("suspend-preflight"), 1)
        for forbidden in ("reboot", "poweroff", "suspend", "hibernate", "install", "dnf"):
            if forbidden == "suspend":
                self.assertNotIn("--execute", flattened)
            else:
                self.assertNotIn(forbidden, flattened)
        self.assertIn("test-suspend", flattened)

    def test_cli_requires_exact_dual_gate_and_new_output(self) -> None:
        with self.assertRaises(SystemExit):
            MODULE.parse_args(["--execute"])
        with self.assertRaises(SystemExit):
            MODULE.parse_args(["--execute", "--confirm", "wrong", "--output", "x"])
        parsed = MODULE.parse_args(
            ["--execute", "--confirm", MODULE.CONFIRMATION, "--output", "new.json"]
        )
        self.assertTrue(parsed.execute)


class EvaluationTests(unittest.TestCase):
    def test_healthy_matrix_passes_without_raw_command_or_private_path(self) -> None:
        report = MODULE.evaluate(
            healthy_results(),
            **observation_arguments(),
            boot_unchanged=True,
            start_uptime_s=120,
            end_uptime_s=180,
            max_start_uptime_s=900,
        )
        self.assertTrue(report["complete"])
        serialized = json.dumps(report)
        self.assertNotIn("PRIVATE-PATH", serialized)
        self.assertNotIn("no suspend was attempted", serialized)

    def test_old_boot_dirty_repo_and_boot_change_fail(self) -> None:
        results = healthy_results()
        next(item for item in results if item.name == "repo-status-before").stdout = " M private-name\n"
        report = MODULE.evaluate(
            results,
            **observation_arguments(),
            boot_unchanged=False,
            start_uptime_s=901,
            end_uptime_s=902,
            max_start_uptime_s=900,
        )
        self.assertFalse(report["complete"])
        self.assertFalse(report["gates"]["started_in_post_boot_window"])
        self.assertFalse(report["gates"]["boot_unchanged"])
        self.assertFalse(report["gates"]["repo_clean_before_after"])

    def test_step_failure_and_missing_diagnostic_fail(self) -> None:
        results = healthy_results()
        step = next(item for item in results if item.name == "hardware-readonly")
        step.status = "fail"
        step.returncode = 1
        diagnostic = next(item for item in results if item.name == "diagnostic-bundle")
        diagnostic.stdout = "PASS without artifact\n"
        report = MODULE.evaluate(
            results,
            **observation_arguments(),
            boot_unchanged=True,
            start_uptime_s=100,
            end_uptime_s=200,
            max_start_uptime_s=900,
        )
        self.assertFalse(report["gates"]["all_steps_passed"])
        self.assertFalse(report["gates"]["diagnostic_bundle_created"])

    def test_revision_or_post_run_repo_drift_fails(self) -> None:
        results = healthy_results()
        next(item for item in results if item.name == "repo-revision-after").stdout = "b" * 40
        next(item for item in results if item.name == "repo-status-after").stdout = " M changed\n"
        report = MODULE.evaluate(
            results,
            **observation_arguments(),
            boot_unchanged=True,
            start_uptime_s=100,
            end_uptime_s=200,
            max_start_uptime_s=900,
        )
        self.assertFalse(report["gates"]["repo_revision_unchanged"])
        self.assertFalse(report["gates"]["repo_clean_before_after"])

    def test_service_restart_pid_change_and_malformed_snapshot_fail(self) -> None:
        results = healthy_results()
        after = next(item for item in results if item.name == "user-services-after")
        after.stdout = after.stdout.replace("MainPID=200", "MainPID=999", 1)
        report = MODULE.evaluate(
            results,
            **observation_arguments(),
            boot_unchanged=True,
            start_uptime_s=100,
            end_uptime_s=200,
            max_start_uptime_s=900,
        )
        self.assertFalse(report["gates"]["user_services_same_pid_zero_restarts"])
        with self.assertRaises(MODULE.AcceptanceError):
            MODULE.parse_services("Id=unknown\n", MODULE.SYSTEM_SERVICES)

    def test_hardware_boot_observation_failure_is_a_visible_gate(self) -> None:
        report = MODULE.evaluate(
            healthy_results(),
            **observation_arguments(brightness=False),
            boot_unchanged=True,
            start_uptime_s=100,
            end_uptime_s=200,
            max_start_uptime_s=900,
        )
        self.assertFalse(report["complete"])
        self.assertFalse(report["gates"]["brightness_targets_restored"])
        self.assertTrue(report["gates"]["renesas_xhci_wake_disabled"])

    def test_boot_token_is_domain_separated_and_rejects_bad_identity(self) -> None:
        first = "11111111-1111-4111-8111-111111111111"
        second = "22222222-2222-4222-8222-222222222222"
        self.assertRegex(MODULE.boot_token(first), r"^[0-9a-f]{64}$")
        self.assertNotEqual(MODULE.boot_token(first), MODULE.boot_token(second))
        self.assertNotIn(first, MODULE.boot_token(first))
        with self.assertRaises(MODULE.AcceptanceError):
            MODULE.boot_token("not-a-boot-id")


class BootObservationTests(unittest.TestCase):
    def test_sysfs_reader_accepts_bounded_short_content_with_pseudo_size(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds014-sysfs-") as temporary:
            path = Path(temporary) / "attribute"
            path.write_text("disabled\n", encoding="utf-8")
            values = list(path.stat())
            values[6] = 4096
            with mock.patch.object(MODULE.os, "fstat", return_value=os.stat_result(values)):
                self.assertEqual(MODULE._read_small_text(path), "disabled")

    def test_brightness_requires_private_state_and_exact_raw_targets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds014-brightness-") as temporary:
            root = Path(temporary)
            state = root / "brightness.json"
            state.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "top_percent": 56,
                        "bottom_percent": 68,
                        "updated_at": "fixture",
                        "source": "fixture",
                    }
                ),
                encoding="utf-8",
            )
            state.chmod(0o600)
            backlights = {}
            for name, maximum, percent in (("top", 4096, 56), ("bottom", 4080, 68)):
                path = root / name
                path.mkdir()
                (path / "max_brightness").write_text(f"{maximum}\n", encoding="utf-8")
                (path / "brightness").write_text(
                    f"{maximum * percent // 100}\n", encoding="utf-8"
                )
                backlights[name] = path
            self.assertTrue(
                MODULE.brightness_targets_restored(state_path=state, backlights=backlights)
            )
            (backlights["bottom"] / "brightness").write_text("1\n", encoding="utf-8")
            self.assertFalse(
                MODULE.brightness_targets_restored(state_path=state, backlights=backlights)
            )
            state.chmod(0o644)
            self.assertFalse(
                MODULE.brightness_targets_restored(state_path=state, backlights=backlights)
            )

    def test_renesas_probe_requires_one_bound_exact_disabled_controller(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds014-renesas-") as temporary:
            root = Path(temporary)
            devices = root / "devices"
            driver = root / "drivers" / MODULE.RENESAS_DRIVER
            devices.mkdir()
            driver.mkdir(parents=True)

            def add(slot: str, state: str) -> Path:
                path = devices / slot
                (path / "power").mkdir(parents=True)
                (path / "vendor").write_text(MODULE.RENESAS_VENDOR + "\n", encoding="utf-8")
                (path / "device").write_text(MODULE.RENESAS_DEVICE + "\n", encoding="utf-8")
                (path / "power" / "wakeup").write_text(state + "\n", encoding="utf-8")
                os.symlink(driver, path / "driver")
                return path

            first = add("0001:01:00.0", "disabled")
            self.assertTrue(MODULE.renesas_xhci_wake_disabled(devices))
            (first / "power" / "wakeup").write_text("enabled\n", encoding="utf-8")
            self.assertFalse(MODULE.renesas_xhci_wake_disabled(devices))
            (first / "power" / "wakeup").write_text("disabled\n", encoding="utf-8")
            add("0002:01:00.0", "disabled")
            self.assertFalse(MODULE.renesas_xhci_wake_disabled(devices))


class FileAndStaticTests(unittest.TestCase):
    def test_report_is_private_new_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds014-report-") as temporary:
            path = Path(temporary) / "report.json"
            MODULE.write_report({"complete": False}, path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                MODULE.write_report({"complete": True}, path)

    def test_source_has_no_direct_power_network_or_install_command(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "systemctl reboot",
            "systemctl suspend",
            "systemctl poweroff",
            "os.system",
            "dnf install",
            "nmcli",
            "curl ",
            "wget ",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

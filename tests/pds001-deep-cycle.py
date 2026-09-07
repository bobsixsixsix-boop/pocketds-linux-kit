#!/usr/bin/env python3
"""Fixture and static safety tests for the PDS-001 deep-cycle harness."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pds001-deep-cycle.py"
HELPER = ROOT / "components/control-panel/pocketds-panel-root"
GUARD = ROOT / "components/system/pocketds-deep-suspend.py"
SPEC = importlib.util.spec_from_file_location("pds001_deep_cycle", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write(root: Path, relative: str, value: str, mode: int = 0o644) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)
    return path


class Fixture:
    def __init__(self, root: Path) -> None:
        self.sys = root / "sys"
        self.proc = root / "proc"
        self.etc = root / "etc"
        write(self.proc, "device-tree/model", "AYANEO Pocket DS\x00")
        write(self.proc, "asound/cards", " 0 [SoundCard]: Pocket DS\n")
        write(self.proc, "sys/kernel/random/boot_id", "fixture-boot-id\n")
        write(self.sys, "power/state", "freeze mem disk\n")
        write(self.sys, "power/mem_sleep", "s2idle [deep]\n")
        write(self.sys, "power/pm_async", "0\n")
        write(self.sys, "class/rtc/rtc0/device/power/wakeup", "enabled\n")
        write(self.sys, "class/rtc/rtc0/wakealarm", "")
        for connector in ("DSI-1", "DSI-2"):
            write(self.sys, f"class/drm/card0-{connector}/status", "connected\n")
        for backlight, maximum in (
            ("ae94000.dsi.0", 1023),
            ("sy7758-backlight", 255),
        ):
            write(self.sys, f"class/backlight/{backlight}/brightness", "128\n")
            write(self.sys, f"class/backlight/{backlight}/max_brightness", f"{maximum}\n")
        write(self.sys, "class/power_supply/battery/capacity", "80\n")
        write(self.sys, "class/power_supply/battery/status", "Discharging\n")
        write(self.sys, "class/power_supply/usb/type", "USB\n")
        write(self.sys, "class/power_supply/usb/online", "0\n")
        write(
            self.etc,
            "systemd/sleep.conf.d/80-pocketds-sleep.conf",
            "[Sleep]\nAllowSuspend=yes\nSuspendState=mem\nMemorySleepMode=deep\n",
        )
        write(
            self.etc,
            "systemd/logind.conf.d/80-pocketds-lid-safety.conf",
            "[Login]\nHandleLidSwitch=ignore\nHandlePowerKey=ignore\n",
        )
        self.helper = write(root, "pocketds-panel-root", "#!/bin/sh\nexit 0\n", 0o755)
        self.guard = write(root, "pocketds-deep-suspend", "#!/bin/sh\nexit 0\n", 0o755)

    @staticmethod
    def runner(argv, **_kwargs):
        valid_prefix = argv[0] == "/usr/bin/systemctl"
        if not valid_prefix or "is-active" not in argv or "--quiet" not in argv:
            raise AssertionError(f"unexpected command: {argv!r}")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    def snapshot(self):
        return MODULE.collect_snapshot(
            sys_root=self.sys,
            proc_root=self.proc,
            etc_root=self.etc,
            helper=self.helper,
            guard=self.guard,
            helper_reference=self.helper,
            guard_reference=self.guard,
            expected_helper_uid=os.getuid(),
            runner=self.runner,
        )


class PreflightTests(unittest.TestCase):
    def test_exact_healthy_fixture_passes_without_private_identifiers(self):
        with tempfile.TemporaryDirectory(prefix="pds001-deep-") as temporary:
            fixture = Fixture(Path(temporary))
            snapshot = fixture.snapshot()
            self.assertTrue(snapshot["ready"])
            serialized = json.dumps(snapshot)
            self.assertNotIn("fixture-boot-id", serialized)
            self.assertNotIn("AYANEO Pocket DS", serialized)
            self.assertTrue(all(snapshot["gates"].values()))
            self.assertEqual(snapshot["battery"]["state"], "discharging")
            self.assertIs(snapshot["battery"]["external_power"], False)

    def test_battery_context_is_categorical_and_fails_bad_capacity(self):
        with tempfile.TemporaryDirectory(prefix="pds001-battery-") as temporary:
            fixture = Fixture(Path(temporary))
            write(fixture.sys, "class/power_supply/battery/status", "Charging\n")
            write(fixture.sys, "class/power_supply/usb/online", "1\n")
            context = MODULE.battery_context(fixture.sys / "class/power_supply")
            self.assertEqual(
                context,
                {"state": "charging", "external_power": True, "ready": True},
            )
            write(fixture.sys, "class/power_supply/battery/capacity", "101\n")
            self.assertFalse(
                MODULE.battery_context(fixture.sys / "class/power_supply")["ready"]
            )

    def test_wrong_sleep_mode_existing_alarm_and_bad_service_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds001-deep-") as temporary:
            fixture = Fixture(Path(temporary))
            write(fixture.sys, "power/mem_sleep", "[s2idle] deep\n")
            write(fixture.sys, "class/rtc/rtc0/wakealarm", "2000000000\n")

            def failed_runner(argv, **_kwargs):
                return subprocess.CompletedProcess(
                    argv, 1 if argv[-1] == "NetworkManager.service" else 0
                )

            snapshot = MODULE.collect_snapshot(
                sys_root=fixture.sys,
                proc_root=fixture.proc,
                etc_root=fixture.etc,
                helper=fixture.helper,
                guard=fixture.guard,
                helper_reference=fixture.helper,
                guard_reference=fixture.guard,
                expected_helper_uid=os.getuid(),
                runner=failed_runner,
            )
            self.assertFalse(snapshot["ready"])
            self.assertFalse(snapshot["gates"]["deep_selected"])
            self.assertFalse(snapshot["gates"]["rtc_alarm_clear"])
            self.assertFalse(snapshot["gates"]["system_services_active"])

    def test_stale_or_tampered_privileged_helper_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds001-deep-") as temporary:
            fixture = Fixture(Path(temporary))
            reference = write(
                Path(temporary), "reference-helper", "#!/bin/sh\nexit 1\n", 0o755
            )
            snapshot = MODULE.collect_snapshot(
                sys_root=fixture.sys,
                proc_root=fixture.proc,
                etc_root=fixture.etc,
                helper=fixture.helper,
                guard=fixture.guard,
                helper_reference=reference,
                guard_reference=fixture.guard,
                expected_helper_uid=os.getuid(),
                runner=fixture.runner,
            )
            self.assertFalse(snapshot["ready"])
            self.assertFalse(snapshot["gates"]["privileged_helper_current"])
            missing = reference.with_name("missing-reference")
            missing_snapshot = MODULE.collect_snapshot(
                sys_root=fixture.sys,
                proc_root=fixture.proc,
                etc_root=fixture.etc,
                helper=fixture.helper,
                guard=fixture.guard,
                helper_reference=missing,
                guard_reference=fixture.guard,
                expected_helper_uid=os.getuid(),
                runner=fixture.runner,
            )
            self.assertFalse(missing_snapshot["gates"]["privileged_helper_current"])

    def test_stale_or_tampered_guard_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds001-guard-") as temporary:
            fixture = Fixture(Path(temporary))
            reference = write(
                Path(temporary), "reference-guard", "#!/bin/sh\nexit 1\n", 0o755
            )
            snapshot = MODULE.collect_snapshot(
                sys_root=fixture.sys,
                proc_root=fixture.proc,
                etc_root=fixture.etc,
                helper=fixture.helper,
                guard=fixture.guard,
                helper_reference=fixture.helper,
                guard_reference=reference,
                expected_helper_uid=os.getuid(),
                runner=fixture.runner,
            )
            self.assertFalse(snapshot["ready"])
            self.assertFalse(snapshot["gates"]["privileged_guard_current"])

    def test_allow_suspend_no_is_safe_baseline_but_not_execution_ready(self):
        with tempfile.TemporaryDirectory(prefix="pds001-policy-") as temporary:
            fixture = Fixture(Path(temporary))
            write(
                fixture.etc,
                "systemd/sleep.conf.d/80-pocketds-sleep.conf",
                (
                    "[Sleep]\n"
                    "AllowSuspend=no\n"
                    "SuspendState=mem\n"
                    "MemorySleepMode=deep\n"
                ),
            )
            snapshot = fixture.snapshot()
            self.assertFalse(snapshot["ready"])
            self.assertTrue(snapshot["gates"]["deep_only_policy"])
            self.assertFalse(snapshot["gates"]["suspend_explicitly_enabled"])

    def test_commented_or_duplicate_policy_does_not_pass(self):
        with tempfile.TemporaryDirectory(prefix="pds001-deep-") as temporary:
            fixture = Fixture(Path(temporary))
            write(
                fixture.etc,
                "systemd/sleep.conf.d/80-pocketds-sleep.conf",
                "[Sleep]\n# SuspendState=mem\nMemorySleepMode=deep\n",
            )
            self.assertFalse(fixture.snapshot()["gates"]["deep_only_policy"])
            self.assertFalse(
                MODULE.config_has_assignment(
                    "[Sleep]\nSuspendState=mem\nSuspendState=mem\n",
                    "Sleep",
                    "SuspendState",
                    "mem",
                )
            )

    def test_cycle_requires_same_boot_bounded_elapsed_and_both_snapshots(self):
        healthy = {"ready": True}
        accepted = MODULE.evaluate_cycle(
            healthy,
            healthy,
            helper_returncode=0,
            elapsed_boottime_s=30.5,
            rtc_seconds=30,
            boot_id_unchanged=True,
        )
        self.assertTrue(accepted["accepted"])
        failed = MODULE.evaluate_cycle(
            healthy,
            {"ready": False},
            helper_returncode=0,
            elapsed_boottime_s=1.0,
            rtc_seconds=30,
            boot_id_unchanged=False,
        )
        self.assertFalse(failed["accepted"])
        self.assertFalse(failed["gates"]["boot_id_unchanged"])
        self.assertFalse(failed["gates"]["minimum_sleep_elapsed"])
        self.assertFalse(failed["gates"]["post_resume_ready"])
        source_drift = MODULE.evaluate_cycle(
            healthy,
            healthy,
            helper_returncode=0,
            elapsed_boottime_s=30.0,
            rtc_seconds=30,
            boot_id_unchanged=True,
            source_binding_unchanged=False,
        )
        self.assertFalse(source_drift["accepted"])
        self.assertFalse(source_drift["gates"]["source_binding_unchanged"])

    def test_boot_token_is_stable_domain_separated_and_private(self):
        first = MODULE.boot_token("fixture-boot-id")
        second = MODULE.boot_token("fixture-boot-id")
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertNotIn("fixture-boot-id", first)
        self.assertNotEqual(
            first,
            MODULE.hashlib.sha256(b"fixture-boot-id").hexdigest(),
        )
        with self.assertRaises(MODULE.AcceptanceError):
            MODULE.boot_token("")

    def test_repository_identity_requires_exact_clean_revision(self):
        responses = iter(
            (
                subprocess.CompletedProcess([], 0, stdout=b"a" * 40 + b"\n"),
                subprocess.CompletedProcess([], 0, stdout=b""),
            )
        )

        def runner(*_args, **_kwargs):
            return next(responses)

        self.assertEqual(MODULE.repository_identity(runner), ("a" * 40, True))
        dirty = iter(
            (
                subprocess.CompletedProcess([], 0, stdout=b"a" * 40 + b"\n"),
                subprocess.CompletedProcess([], 0, stdout=b" M tracked\n"),
            )
        )
        self.assertEqual(
            MODULE.repository_identity(lambda *_args, **_kwargs: next(dirty)),
            ("a" * 40, False),
        )


class EvidenceTests(unittest.TestCase):
    def test_private_report_is_new_checkpointed_and_link_safe(self):
        with tempfile.TemporaryDirectory(prefix="pds001-report-") as temporary:
            root = Path(temporary)
            output = root / "report.json"
            MODULE.create_report(output, {"phase": "preflight"})
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                MODULE.create_report(output, {"phase": "overwrite"})
            MODULE.checkpoint_report(output, {"phase": "post-resume"})
            self.assertEqual(json.loads(output.read_text())["phase"], "post-resume")
            target = root / "target"
            target.write_text("keep", encoding="utf-8")
            output.unlink()
            output.symlink_to(target)
            with self.assertRaises((MODULE.AcceptanceError, OSError)):
                MODULE.checkpoint_report(output, {"phase": "bad"})
            self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_execute_needs_both_explicit_gates_and_does_not_create_output(self):
        with tempfile.TemporaryDirectory(prefix="pds001-gate-") as temporary:
            output = Path(temporary) / "must-not-exist.json"
            environment = os.environ.copy()
            environment.pop("POCKETDS_ALLOW_DEEP_CYCLE", None)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--execute", "--output", str(output)],
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertFalse(output.exists())
            self.assertIn("confirmation", result.stderr)


class StaticBoundaryTests(unittest.TestCase):
    def test_privileged_helper_has_one_bounded_deep_path(self):
        source = HELPER.read_text(encoding="utf-8")
        self.assertIn("deep-suspend)", source)
        self.assertIn("deep-suspend-check)", source)
        self.assertIn("exec /usr/local/libexec/pocketds-deep-suspend run", source)
        self.assertNotIn("rtcwake", source)
        self.assertNotIn("systemctl suspend", source)
        self.assertNotIn("trap", source)

    def test_guard_owns_login1_and_rtc_lifecycle(self):
        source = GUARD.read_text(encoding="utf-8")
        self.assertIn('"Inhibit"', source)
        self.assertIn('"PrepareForSleep"', source)
        self.assertIn('"SuspendWithFlags"', source)
        self.assertIn("ROOT_CHECK_INHIBITORS = 1", source)
        self.assertIn('policy.get("AllowSuspend", "")', source)

    def test_orchestrator_never_calls_sleep_interfaces_directly(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertIn('"deep-suspend"', source)
        self.assertIn("POCKETDS_ALLOW_DEEP_CYCLE", source)
        self.assertIn('"guard_reference_sha256"', source)
        self.assertNotIn("timeout=arguments.rtc_seconds + 120", source)

    def test_read_only_preflight_cannot_request_suspend(self):
        source = (ROOT / "tests/suspend-preflight.sh").read_text(encoding="utf-8")
        self.assertNotIn("--execute", source)
        self.assertNotIn("POCKETDS_ALLOW_SUSPEND_TEST", source)
        self.assertNotIn("systemctl suspend", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

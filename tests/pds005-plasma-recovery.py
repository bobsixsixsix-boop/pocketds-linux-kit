#!/usr/bin/env python3
"""Mocked state-machine tests for bounded PDS-005 shell recovery."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/pocketds-plasma-recovery.py"
SPEC = importlib.util.spec_from_file_location("pocketds_plasma_recovery", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recovery = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recovery
SPEC.loader.exec_module(recovery)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeWorld:
    def __init__(self, *, active: bool = True) -> None:
        self.shell_active = active
        self.shell_sub = "running" if active else "dead"
        self.shell_pid = 4200 if active else 0
        self.shell_result = "success"
        self.bus_owner = active
        self.quit_mode = "normal"
        self.stop_mode = "normal"
        self.start_failure = False
        self.worker_active = False
        self.change_keyboard_on_start = False
        self.commands: list[tuple[str, ...]] = []
        self.preserved = {
            "pocketds-keyboard.service": ["active", "running", 5100, 0],
            "pocketds-gpu-telemetry.service": ["active", "running", 5200, 0],
        }

    @staticmethod
    def _show(
        unit: str,
        *,
        active: str,
        sub: str,
        pid: int,
        restarts: int = 0,
        result: str = "success",
    ) -> str:
        return "\n".join(
            (
                f"Id={unit}",
                "LoadState=loaded",
                f"ActiveState={active}",
                f"SubState={sub}",
                f"MainPID={pid}",
                f"NRestarts={restarts}",
                f"Result={result}",
                f"ControlGroup=/mock/{unit}",
            )
        )

    def __call__(self, argv):
        command = tuple(argv)
        self.commands.append(command)
        binary = Path(command[0]).name
        if binary == "kquitapp6":
            if self.quit_mode == "fail":
                return recovery.CommandResult(1, "", "mock KDE quit failure")
            if self.quit_mode == "normal":
                self.shell_active = False
                self.shell_sub = "dead"
                self.shell_pid = 0
                self.bus_owner = False
            return recovery.CommandResult(0)
        if binary != "systemctl":
            return recovery.CommandResult(99, "", "unexpected binary")
        verb = command[2]
        if verb == "show":
            unit = command[-1]
            if unit == recovery.UNIT:
                active = "active" if self.shell_active else "inactive"
                return recovery.CommandResult(
                    0,
                    self._show(
                        unit,
                        active=active,
                        sub=self.shell_sub,
                        pid=self.shell_pid,
                        result=self.shell_result,
                    ),
                )
            if unit == recovery.WORKER_UNIT:
                active = "active" if self.worker_active else "inactive"
                sub = "running" if self.worker_active else "dead"
                return recovery.CommandResult(
                    0,
                    self._show(
                        unit,
                        active=active,
                        sub=sub,
                        pid=5300 if self.worker_active else 0,
                    ),
                )
            values = self.preserved[unit]
            return recovery.CommandResult(
                0,
                self._show(
                    unit,
                    active=values[0],
                    sub=values[1],
                    pid=values[2],
                    restarts=values[3],
                ),
            )
        if verb == "stop":
            self.assert_exact_unit(command)
            if self.stop_mode == "normal":
                self.shell_active = False
                self.shell_sub = "dead"
                self.shell_pid = 0
                self.bus_owner = False
            else:
                self.shell_active = True
                self.shell_sub = "stop-sigterm"
            return recovery.CommandResult(0)
        if verb == "kill":
            self.assert_exact_unit(command)
            if "--kill-whom=all" not in command or "--signal=SIGKILL" not in command:
                return recovery.CommandResult(2, "", "wrong kill scope")
            self.shell_active = False
            self.shell_sub = "dead"
            self.shell_pid = 0
            self.bus_owner = False
            return recovery.CommandResult(0)
        if verb == "reset-failed":
            self.assert_exact_unit(command)
            self.shell_result = "success"
            return recovery.CommandResult(0)
        if verb == "start":
            if command[-1] == recovery.WORKER_UNIT:
                return recovery.CommandResult(0)
            self.assert_exact_unit(command)
            if self.start_failure:
                return recovery.CommandResult(1, "", "mock start failure")
            self.shell_active = True
            self.shell_sub = "running"
            self.shell_pid += 4301
            self.bus_owner = True
            if self.change_keyboard_on_start:
                self.preserved["pocketds-keyboard.service"][2] += 1
            return recovery.CommandResult(0)
        return recovery.CommandResult(98, "", f"unexpected verb {verb}")

    @staticmethod
    def assert_exact_unit(command: tuple[str, ...]) -> None:
        if command[-1] != recovery.UNIT:
            raise AssertionError(f"mutation escaped Plasma unit: {command}")


def controller(world: FakeWorld, clock: FakeClock | None = None):
    clock = clock or FakeClock()
    return recovery.PlasmaRecovery(
        world,
        lambda: world.bus_owner,
        clock,
        quit_timeout=0.25,
        term_timeout=0.5,
        kill_timeout=0.25,
        start_timeout=1.0,
        poll_interval=0.1,
    )


SETTINGS = recovery.timeout_settings(0.25, 0.5, 0.25, 1.0, 0.1)


def mutating_verbs(world: FakeWorld) -> list[str]:
    verbs: list[str] = []
    for command in world.commands:
        binary = Path(command[0]).name
        if binary == "kquitapp6":
            verbs.append("kquitapp6")
        elif binary == "systemctl" and command[2] != "show":
            verbs.append(command[2])
    return verbs


class RecoveryStateMachineTests(unittest.TestCase):
    def test_unit_stop_cancels_auto_restart_before_single_start(self):
        world = FakeWorld()
        report = controller(world).recover()
        self.assertTrue(report["ok"])
        self.assertEqual(mutating_verbs(world), ["stop", "reset-failed", "start"])
        self.assertEqual(report["preserve_violations"], [])
        self.assertNotIn("manual_fallback", report)

    def test_timeout_kills_only_shell_unit_cgroup(self):
        world = FakeWorld()
        world.quit_mode = "stuck"
        world.stop_mode = "stuck"
        clock = FakeClock()
        report = controller(world, clock).recover()
        self.assertTrue(report["ok"])
        self.assertEqual(
            mutating_verbs(world),
            ["stop", "kill", "reset-failed", "start"],
        )
        kill = next(
            command
            for command in world.commands
            if len(command) > 2 and command[2] == "kill"
        )
        self.assertEqual(kill[-1], recovery.UNIT)
        self.assertIn("--kill-whom=all", kill)
        self.assertIn("--signal=SIGKILL", kill)
        self.assertGreaterEqual(clock.now, 0.5)
        self.assertLess(clock.now, 1.0)

    def test_auto_restart_policy_cannot_create_an_intermediate_shell(self):
        class AutoRestartWorld(FakeWorld):
            auto_restarts = 0

            def __call__(self, argv):
                if Path(argv[0]).name == "kquitapp6":
                    # The deployed Restart=always policy respawns a clean exit.
                    # This would make the old quit/observe/stop sequence stop
                    # a new shell before explicitly starting yet another one.
                    self.auto_restarts += 1
                    self.shell_pid += 1
                    return recovery.CommandResult(0)
                return super().__call__(argv)

        world = AutoRestartWorld()
        report = controller(world).recover()
        self.assertTrue(report["ok"])
        self.assertEqual(world.auto_restarts, 0)
        self.assertEqual(mutating_verbs(world), ["stop", "reset-failed", "start"])

    def test_already_inactive_skips_all_stop_paths(self):
        world = FakeWorld(active=False)
        report = controller(world).recover()
        self.assertTrue(report["ok"])
        self.assertEqual(mutating_verbs(world), ["reset-failed", "start"])

    def test_start_failure_is_nonzero_report_with_manual_fallback(self):
        world = FakeWorld()
        world.start_failure = True
        report = controller(world).recover()
        self.assertFalse(report["ok"])
        self.assertIn("single Plasma start request failed", report["error"])
        self.assertEqual(mutating_verbs(world).count("start"), 1)
        self.assertIn(
            "systemctl --user start plasma-plasmashell.service",
            report["manual_fallback"],
        )

    def test_protected_service_change_fails_acceptance_without_mutating_it(self):
        world = FakeWorld()
        world.change_keyboard_on_start = True
        report = controller(world).recover()
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["preserve_violations"]), 1)
        for command in world.commands:
            if Path(command[0]).name == "systemctl" and command[2] != "show":
                self.assertNotIn(command[-1], recovery.PRESERVE_UNITS)


class TriggerAndSafetyTests(unittest.TestCase):
    def test_invalid_double_confirmation_fails_before_any_command(self):
        for unit, action in (
            (None, None),
            (recovery.UNIT, None),
            (None, recovery.CONFIRM_ACTION),
            ("kwin.service", recovery.CONFIRM_ACTION),
            (recovery.UNIT, "yes"),
        ):
            with self.assertRaises(recovery.RecoveryError):
                recovery.validate_confirmations(unit, action)
        world = FakeWorld()
        with tempfile.TemporaryDirectory(prefix="pds005-recovery-") as temporary:
            request = Path(temporary) / "request"
            lock = Path(temporary) / "lock"
            with self.assertRaises(recovery.RecoveryError):
                recovery.queue_recovery(
                    world,
                    request,
                    lock,
                    1000.0,
                    SETTINGS,
                    recovery.UNIT,
                    "wrong",
                )
            self.assertEqual(world.commands, [])
            self.assertFalse(request.exists())

    def test_trigger_only_queues_independent_worker_and_request_is_one_shot(self):
        world = FakeWorld()
        with tempfile.TemporaryDirectory(prefix="pds005-recovery-") as temporary:
            request = Path(temporary) / "request"
            lock = Path(temporary) / "lock"
            report = recovery.queue_recovery(
                world,
                request,
                lock,
                1000.0,
                SETTINGS,
                recovery.UNIT,
                recovery.CONFIRM_ACTION,
            )
            self.assertTrue(report["queued"])
            self.assertEqual(mutating_verbs(world), ["start"])
            self.assertEqual(world.commands[-1][-1], recovery.WORKER_UNIT)
            payload = recovery.consume_request(request, 1000.5)
            self.assertEqual(payload["unit"], recovery.UNIT)
            self.assertEqual(payload["request_id"], report["request_id"])
            self.assertFalse(request.exists())
            with self.assertRaises(recovery.RecoveryError):
                recovery.consume_request(request, 1000.6)

    def test_worker_can_acquire_mutex_before_trigger_returns(self):
        world = FakeWorld()
        consumed: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory(prefix="pds005-recovery-") as temporary:
            request = Path(temporary) / "request"
            lock = Path(temporary) / "lock"

            def start_worker_during_systemctl() -> None:
                with recovery.exclusive_lock(lock):
                    consumed.append(recovery.consume_request(request, 1000.1))

            original_call = world.__call__

            def runner(argv):
                result = original_call(argv)
                if tuple(argv)[-1] == recovery.WORKER_UNIT:
                    start_worker_during_systemctl()
                return result

            report = recovery.queue_recovery(
                runner,
                request,
                lock,
                1000.0,
                SETTINGS,
                recovery.UNIT,
                recovery.CONFIRM_ACTION,
            )
            self.assertTrue(report["queued"])
            self.assertEqual(len(consumed), 1)
            self.assertFalse(request.exists())

    def test_failed_with_live_main_pid_is_not_stopped(self):
        snapshot = recovery.UnitSnapshot(
            unit=recovery.UNIT,
            load_state="loaded",
            active_state="failed",
            sub_state="failed",
            main_pid=99,
            n_restarts=0,
            result="timeout",
            control_group="/mock/plasma",
        )
        self.assertFalse(snapshot.stopped)

    def test_worker_exception_atomically_replaces_old_result_with_request_id(self):
        world = FakeWorld()
        original_call = world.__call__

        def broken_runner(argv):
            command = tuple(argv)
            if (
                len(command) > 2
                and Path(command[0]).name == "systemctl"
                and command[2] == "show"
                and command[-1] == recovery.UNIT
            ):
                return recovery.CommandResult(1, "", "mock snapshot failure")
            return original_call(argv)

        with tempfile.TemporaryDirectory(prefix="pds005-recovery-") as temporary:
            request = Path(temporary) / "request"
            result = Path(temporary) / "result.json"
            result.write_text('{"ok":true,"request_id":"old"}\n', encoding="utf-8")
            result.chmod(0o600)
            request_id = recovery.write_request(request, 1000.0, SETTINGS)
            report = recovery.run_worker(broken_runner, request, result, 1000.1)
            stored = json.loads(result.read_text(encoding="utf-8"))
            self.assertFalse(report["ok"])
            self.assertEqual(stored, report)
            self.assertEqual(stored["request_id"], request_id)
            self.assertEqual(stored["worker_started_unix_ms"], 1_000_100)
            self.assertIn("mock snapshot failure", stored["error"])
            self.assertFalse(request.exists())

    def test_invalid_worker_request_replaces_old_result_without_mutation(self):
        world = FakeWorld()
        with tempfile.TemporaryDirectory(prefix="pds005-recovery-") as temporary:
            request = Path(temporary) / "request"
            result = Path(temporary) / "result.json"
            request.write_text("{}\n", encoding="utf-8")
            request.chmod(0o600)
            result.write_text('{"ok":true,"request_id":"old"}\n', encoding="utf-8")
            result.chmod(0o600)
            report = recovery.run_worker(world, request, result, 1000.0)
            stored = json.loads(result.read_text(encoding="utf-8"))
            self.assertFalse(report["ok"])
            self.assertEqual(stored, report)
            self.assertIsNone(stored["request_id"])
            self.assertIn("request contract mismatch", stored["error"])
            self.assertEqual(world.commands, [])
            self.assertTrue(request.exists())

    def test_lock_conflict_is_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds005-recovery-") as temporary:
            lock = Path(temporary) / "lock"
            with recovery.exclusive_lock(lock):
                with self.assertRaises(recovery.LockConflict):
                    with recovery.exclusive_lock(lock):
                        pass

    def test_stale_request_cleanup_requires_age_and_inactive_worker(self):
        world = FakeWorld()
        with tempfile.TemporaryDirectory(prefix="pds005-recovery-") as temporary:
            request = Path(temporary) / "request"
            request.write_text("malformed but private\n", encoding="utf-8")
            request.chmod(0o600)
            os.utime(request, (900.0, 900.0))
            report = recovery.clear_stale_request(world, request, 1000.0)
            self.assertTrue(report["ok"])
            self.assertTrue(report["removed"])
            self.assertFalse(report["plasma_mutations_executed"])
            self.assertFalse(request.exists())
            self.assertEqual(mutating_verbs(world), [])

    def test_fresh_or_active_worker_request_is_never_removed(self):
        for fresh, worker_active in ((True, False), (False, True)):
            world = FakeWorld()
            world.worker_active = worker_active
            with tempfile.TemporaryDirectory(prefix="pds005-recovery-") as temporary:
                request = Path(temporary) / "request"
                request.write_text("private\n", encoding="utf-8")
                request.chmod(0o600)
                age = 10.0 if fresh else 100.0
                os.utime(request, (1000.0 - age, 1000.0 - age))
                with self.assertRaises(recovery.RecoveryError):
                    recovery.clear_stale_request(world, request, 1000.0)
                self.assertTrue(request.exists())
                self.assertEqual(mutating_verbs(world), [])

    def test_stale_cleanup_is_idempotent_when_request_is_absent(self):
        world = FakeWorld()
        with tempfile.TemporaryDirectory(prefix="pds005-recovery-") as temporary:
            request = Path(temporary) / "request"
            report = recovery.clear_stale_request(world, request, 1000.0)
            self.assertTrue(report["ok"])
            self.assertFalse(report["removed"])
            self.assertEqual(world.commands, [])

    def test_status_and_dry_run_have_no_mutating_commands(self):
        for action in ("status", "dry-run"):
            world = FakeWorld()
            report = controller(world).status(action)
            self.assertTrue(report["ok"])
            self.assertFalse(report["mutations_executed"])
            self.assertEqual(mutating_verbs(world), [])
            if action == "dry-run":
                self.assertIn("plan", report)

    def test_worker_unit_is_an_independent_non_restarting_cgroup(self):
        unit = (
            ROOT / "components/control-panel/pocketds-plasma-recovery.service"
        ).read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", unit)
        self.assertIn("Slice=app.slice", unit)
        self.assertIn(" --worker", unit)
        self.assertIn("Restart=no", unit)
        for forbidden in ("PartOf=", "BindsTo=", "Requires=", "kwin"):
            self.assertNotIn(forbidden, unit)

    def test_install_and_import_are_symmetric_but_never_enable_worker(self):
        installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        importer = (ROOT / "scripts/import-live.sh").read_text(encoding="utf-8")
        self.assertIn(
            "/usr/local/bin/pocketds-plasma-recovery 0755",
            installer,
        )
        self.assertIn(
            '"$HOME/.config/systemd/user/pocketds-plasma-recovery.service"',
            installer,
        )
        self.assertNotIn(
            "enable pocketds-plasma-recovery.service",
            installer,
        )
        self.assertNotIn(
            "start pocketds-plasma-recovery.service",
            installer,
        )
        for target in (
            "/usr/local/bin/pocketds-plasma-recovery",
            "$HOME/.config/systemd/user/pocketds-plasma-recovery.service",
        ):
            self.assertIn(target, importer)


if __name__ == "__main__":
    unittest.main()

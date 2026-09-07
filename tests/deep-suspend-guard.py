#!/usr/bin/env python3
"""Pure and static tests for the fail-closed deep suspend guard."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "components/system/pocketds-deep-suspend.py"
POLKIT_RULE = ROOT / "components/system/90-pocketds-deep-suspend.rules"
SPEC = importlib.util.spec_from_file_location("pocketds_deep_suspend", GUARD)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class PolicyTests(unittest.TestCase):
    def test_effective_sleep_policy_uses_last_assignment(self) -> None:
        parsed = MODULE.parse_sleep_policy(
            """
[Sleep]
AllowSuspend=yes
SuspendState=freeze mem
MemorySleepMode=s2idle
[Other]
AllowSuspend=yes
[Sleep]
AllowSuspend=no
SuspendState=mem
MemorySleepMode=deep
"""
        )
        self.assertEqual(
            parsed,
            {
                "AllowSuspend": "no",
                "SuspendState": "mem",
                "MemorySleepMode": "deep",
            },
        )

    def test_comments_and_other_sections_cannot_enable_suspend(self) -> None:
        parsed = MODULE.parse_sleep_policy(
            """
[Sleep]
# AllowSuspend=yes
; SuspendState=freeze
MemorySleepMode=deep
[Login]
AllowSuspend=yes
"""
        )
        self.assertNotIn("AllowSuspend", parsed)
        self.assertNotIn("SuspendState", parsed)


class LifecycleTests(unittest.TestCase):
    def test_only_requested_true_then_false_authorizes_cleanup(self) -> None:
        lifecycle = MODULE.Lifecycle()
        self.assertEqual(lifecycle.prepare_for_sleep(False), "ignore")
        lifecycle.mark_request_sent()
        self.assertEqual(
            lifecycle.prepare_for_sleep(True), "release-delay-inhibitor"
        )
        self.assertEqual(lifecycle.prepare_for_sleep(True), "ignore")
        self.assertEqual(lifecycle.prepare_for_sleep(False), "cleanup-rtc")
        self.assertEqual(lifecycle.prepare_for_sleep(False), "ignore")
        self.assertTrue(lifecycle.saw_prepare)
        self.assertTrue(lifecycle.saw_resume)

    def test_false_without_true_never_authorizes_cleanup(self) -> None:
        lifecycle = MODULE.Lifecycle()
        lifecycle.mark_request_sent()
        self.assertEqual(lifecycle.prepare_for_sleep(False), "ignore")
        self.assertFalse(lifecycle.saw_resume)

    def test_method_success_or_failure_never_authorizes_cleanup(self) -> None:
        for succeeded in (True, False):
            with self.subTest(succeeded=succeeded):
                lifecycle = MODULE.Lifecycle()
                lifecycle.mark_request_sent()
                self.assertEqual(
                    lifecycle.mark_method_reply(succeeded),
                    "wait-for-prepare-false",
                )
                self.assertFalse(lifecycle.saw_prepare)
                self.assertFalse(lifecycle.saw_resume)

    def test_request_cannot_be_dispatched_twice(self) -> None:
        lifecycle = MODULE.Lifecycle()
        lifecycle.mark_request_sent()
        with self.assertRaises(MODULE.GuardError):
            lifecycle.mark_request_sent()


class FakeParameters:
    def __init__(self, active: bool) -> None:
        self.active = active

    def unpack(self) -> tuple[bool]:
        return (self.active,)


class FakeLoop:
    def __init__(self) -> None:
        self.on_run = None
        self.quit_count = 0

    def run(self) -> None:
        if self.on_run is not None:
            self.on_run()

    def quit(self) -> None:
        self.quit_count += 1


class FakeAdapter:
    def __init__(
        self,
        *,
        arm_error: Exception | None = None,
        request_error: Exception | None = None,
        async_error: bool = False,
    ) -> None:
        self.arm_error = arm_error
        self.request_error = request_error
        self.async_error = async_error
        self.actions: list[object] = []
        self.clear_count = 0
        self.prepare_callback = None
        self.request_callback = None

    def take_delay_inhibitor(self) -> int:
        self.actions.append("take-inhibitor")
        return 1234

    def subscribe(self, callback) -> int:
        self.actions.append("subscribe")
        self.prepare_callback = callback
        return 17

    def unsubscribe(self, subscription: int) -> None:
        self.actions.append(("unsubscribe", subscription))

    def preparing_for_sleep(self) -> bool:
        self.actions.append("preparing-for-sleep")
        return False

    def alarm(self) -> None:
        self.actions.append("alarm")
        return None

    def arm_rtc(self, seconds: int) -> int:
        self.actions.append(("arm-rtc", seconds))
        if self.arm_error is not None:
            raise self.arm_error
        return 1060

    def rtc_epoch(self) -> int:
        self.actions.append("rtc-epoch")
        return 1000

    def request_suspend(self, callback) -> None:
        self.actions.append("request-suspend")
        self.request_callback = callback
        if self.request_error is not None:
            raise self.request_error

    def finish_request(self, _result) -> None:
        self.actions.append("finish-request")
        if self.async_error:
            raise MODULE.GLib.Error("mock asynchronous D-Bus failure")

    def clear_rtc(self) -> bool:
        self.actions.append("clear-rtc")
        self.clear_count += 1
        return True


class SuspendSessionTests(unittest.TestCase):
    def exercise(self, adapter: FakeAdapter, on_run=None):
        session = MODULE.SuspendSession(adapter, 60)
        loop = FakeLoop()
        loop.on_run = None if on_run is None else lambda: on_run(session)
        session.loop = loop
        with (
            mock.patch.object(
                MODULE.os,
                "sync",
                side_effect=lambda: adapter.actions.append("sync"),
            ) as sync,
            mock.patch.object(
                MODULE.os,
                "close",
                side_effect=lambda descriptor: adapter.actions.append(
                    ("close", descriptor)
                ),
            ) as close,
            mock.patch.object(
                MODULE.GLib, "timeout_add_seconds", return_value=101
            ) as add_timeout,
            mock.patch.object(MODULE.GLib, "source_remove") as remove_source,
            mock.patch.object(MODULE, "emit"),
        ):
            result = session.run()
        return {
            "result": result,
            "session": session,
            "loop": loop,
            "sync": sync,
            "close": close,
            "add_timeout": add_timeout,
            "remove_source": remove_source,
        }

    def test_arm_readback_error_gets_pre_request_cleanup(self) -> None:
        adapter = FakeAdapter(arm_error=MODULE.GuardError("mock readback failure"))
        observed = self.exercise(adapter)
        self.assertEqual(observed["result"], 2)
        self.assertEqual(adapter.clear_count, 1)
        self.assertFalse(observed["session"].lifecycle.request_sent)
        self.assertNotIn("request-suspend", adapter.actions)
        self.assertLess(adapter.actions.index("sync"), adapter.actions.index("clear-rtc"))

    def test_synchronous_request_error_gets_pre_request_cleanup(self) -> None:
        adapter = FakeAdapter(
            request_error=MODULE.GuardError("mock synchronous D-Bus failure")
        )
        observed = self.exercise(adapter)
        self.assertEqual(observed["result"], 2)
        self.assertEqual(adapter.clear_count, 1)
        self.assertFalse(observed["session"].lifecycle.request_sent)
        self.assertIn("request-suspend", adapter.actions)
        self.assertLess(
            adapter.actions.index("request-suspend"),
            adapter.actions.index("clear-rtc"),
        )

    def test_async_request_error_then_timeout_never_cleans_rtc(self) -> None:
        adapter = FakeAdapter(async_error=True)

        def on_run(session) -> None:
            session._on_request_done(None, object(), None)
            session._on_timeout()

        observed = self.exercise(adapter, on_run)
        self.assertEqual(observed["result"], 6)
        self.assertEqual(adapter.clear_count, 0)
        self.assertTrue(observed["session"].lifecycle.request_sent)
        self.assertIs(observed["session"].lifecycle.method_succeeded, False)
        self.assertEqual(observed["close"].call_count, 1)

    def test_false_without_true_then_timeout_never_cleans_rtc(self) -> None:
        adapter = FakeAdapter()

        def on_run(session) -> None:
            session._on_prepare(None, "", "", "", "", FakeParameters(False))
            session._on_timeout()

        observed = self.exercise(adapter, on_run)
        self.assertEqual(observed["result"], 6)
        self.assertEqual(adapter.clear_count, 0)
        self.assertFalse(observed["session"].lifecycle.saw_prepare)
        self.assertFalse(observed["session"].lifecycle.saw_resume)

    def test_true_then_false_releases_inhibitor_and_cleans_once(self) -> None:
        adapter = FakeAdapter()

        def on_run(session) -> None:
            session._on_prepare(None, "", "", "", "", FakeParameters(True))
            session._on_prepare(None, "", "", "", "", FakeParameters(False))
            session._on_prepare(None, "", "", "", "", FakeParameters(False))

        observed = self.exercise(adapter, on_run)
        self.assertEqual(observed["result"], 0)
        self.assertEqual(adapter.clear_count, 1)
        self.assertEqual(observed["close"].call_count, 1)
        self.assertEqual(observed["loop"].quit_count, 1)
        self.assertLess(
            adapter.actions.index(("close", 1234)),
            adapter.actions.index("clear-rtc"),
        )

    def test_execution_not_ready_refuses_before_every_mutating_boundary(self) -> None:
        adapter = FakeAdapter()
        snapshot = {"execution_ready": False, "safely_blocked": True}
        with (
            mock.patch.object(MODULE, "LiveAdapter", return_value=adapter),
            mock.patch.object(MODULE, "collect_preflight", return_value=snapshot),
            mock.patch.object(MODULE, "acquire_lock") as acquire_lock,
            mock.patch.object(MODULE, "SuspendSession") as suspend_session,
            mock.patch.object(MODULE.os, "sync") as sync,
            mock.patch.object(MODULE, "emit"),
        ):
            result = MODULE.main(["run", "60"])
        self.assertEqual(result, 1)
        acquire_lock.assert_not_called()
        suspend_session.assert_not_called()
        sync.assert_not_called()
        self.assertEqual(adapter.actions, [])
        self.assertEqual(adapter.clear_count, 0)


class ArgumentTests(unittest.TestCase):
    def test_guard_has_a_sixty_to_three_hundred_second_window(self) -> None:
        self.assertEqual(MODULE.parse_args(["run", "60"]).seconds, 60)
        self.assertEqual(MODULE.parse_args(["run", "300"]).seconds, 300)
        for value in ("59", "301"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                MODULE.parse_args(["run", value])

    def test_check_has_no_seconds_or_execution_flag(self) -> None:
        arguments = MODULE.parse_args(["check"])
        self.assertEqual(arguments.command, "check")
        with self.assertRaises(SystemExit):
            MODULE.parse_args(["check", "60"])


class StaticBoundaryTests(unittest.TestCase):
    def test_guard_uses_delay_inhibitor_and_exact_logind_method(self) -> None:
        source = GUARD.read_text(encoding="utf-8")
        self.assertIn('"Inhibit"', source)
        self.assertIn('"delay"', source)
        self.assertIn('"PrepareForSleep"', source)
        self.assertIn('"SuspendWithFlags"', source)
        self.assertIn("ROOT_CHECK_INHIBITORS = 1", source)
        self.assertNotIn("SD_LOGIND_SKIP_INHIBITORS", source)
        self.assertNotIn("systemctl suspend", source)
        self.assertNotIn('"Suspend",', source)

    def test_polkit_denies_every_non_root_login1_suspend_action(self) -> None:
        source = POLKIT_RULE.read_text(encoding="utf-8")
        for action in (
            "org.freedesktop.login1.suspend",
            "org.freedesktop.login1.suspend-ignore-inhibit",
            "org.freedesktop.login1.suspend-multiple-sessions",
        ):
            self.assertEqual(source.count(f'"{action}"'), 1)
        self.assertIn('subject.user !== "root"', source)
        self.assertIn("polkit.Result.NO", source)
        self.assertNotIn("polkit.Result.YES", source)

    def test_rtc_cleanup_call_sites_are_strictly_bounded(self) -> None:
        tree = ast.parse(GUARD.read_text(encoding="utf-8"))
        call_sites: list[str] = []

        class Visitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.functions: list[str] = []

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self.functions.append(node.name)
                self.generic_visit(node)
                self.functions.pop()

            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Attribute) and node.func.attr == "clear_rtc":
                    call_sites.append(self.functions[-1] if self.functions else "")
                self.generic_visit(node)

        Visitor().visit(tree)
        self.assertEqual(sorted(call_sites), ["_clear_before_request", "_on_prepare"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

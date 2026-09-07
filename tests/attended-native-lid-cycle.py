#!/usr/bin/env python3
"""Pure native-supervisor fixtures: no GI, installed helper, RTC or sleep access."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools/kernel-ab/attended-native-lid-cycle.py"
spec = importlib.util.spec_from_file_location("native_trial", SOURCE)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
common_spec = importlib.util.spec_from_file_location("native_common_test", SOURCE.with_name("attended-lid-cycle.py"))
common_app = importlib.util.module_from_spec(common_spec)
common_spec.loader.exec_module(common_app)

# Exercise the actual guard lifecycle without importing GI on the development Mac.
guard_tree = ast.parse((ROOT / "components/system/pocketds-deep-suspend.py").read_text())
namespace = {}
exec(compile(ast.Module(body=[node for node in guard_tree.body if isinstance(node, ast.ClassDef)
                             and node.name in ("GuardError", "Lifecycle")], type_ignores=[]),
             "actual_guard_lifecycle", "exec"), namespace)
guard_constants = {node.targets[0].id: ast.literal_eval(node.value) for node in guard_tree.body
                   if isinstance(node, ast.Assign) and len(node.targets) == 1
                   and isinstance(node.targets[0], ast.Name)
                   and node.targets[0].id in ("MIN_RTC_SECONDS", "MAX_RTC_SECONDS")}


def arguments():
    return SimpleNamespace(command="run", boot_id="6847be2d-cd54-4df4-8a9e-86e33ae7172a",
        expected_success=0, receipt=Path("/run/pds-lid-cycle-native-fixture"),
        **{key + "_sha256": "a" * 64 for key in
           ("common", "verifier", "light", "lid_mode", "kwin", "powerdevil", "observer")})


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class FakeLoop:
    def __init__(self):
        self.work = lambda: None
        self.quit_count = 0
    def run(self):
        self.work()
    def quit(self):
        self.quit_count += 1


class Adapter:
    def __init__(self):
        self.events = []
        self.current_alarm = None
        self.preparing = False
        self.clear_ok = True
        self.epoch = 1000
        self.callback = None
        self.fd = -1
    def take_delay_inhibitor(self):
        self.events.append("inhibitor")
        self.fd = os.open(os.devnull, os.O_RDONLY)
        return self.fd
    def subscribe(self, callback):
        self.events.append("subscribe")
        self.callback = callback
        return 1
    def unsubscribe(self, subscription):
        self.events.append("unsubscribe")
    def preparing_for_sleep(self):
        return self.preparing
    def alarm(self):
        return self.current_alarm
    def rtc_epoch(self):
        return self.epoch
    def arm_rtc(self, seconds):
        self.events.append(("arm", seconds))
        self.current_alarm = 1300
        return self.current_alarm
    def clear_rtc(self):
        self.events.append("clear")
        if self.clear_ok:
            self.current_alarm = None
        return self.clear_ok
    def request_suspend(self, *_args):
        raise AssertionError("a passive native test may not request suspend")


class NativeSessionTests(unittest.TestCase):
    def setUp(self):
        self.adapter = Adapter()
        self.loop = FakeLoop()
        self.now, self.closed = 1000, False
        self.messages = []
        self.glib = SimpleNamespace(MainLoop=lambda: self.loop,
            timeout_add=Mock(return_value=7), source_remove=Mock(),
            SOURCE_REMOVE=False, SOURCE_CONTINUE=True)
        self.guard = SimpleNamespace(Lifecycle=namespace["Lifecycle"], GLib=self.glib, **guard_constants)
        self.block_return = Mock(side_effect=lambda: self.adapter.events.append("block"))
        self.session = app.NativeSession(self.guard, self.adapter,
            lambda event, **fields: self.messages.append((event, fields)),
            lambda: self.closed, clock=lambda: self.now, block_return=self.block_return)
        release = self.session.release_inhibitor
        def observed_release():
            if self.session.inhibitor >= 0:
                self.adapter.events.append("release")
            release()
        self.session.release_inhibitor = observed_release

    def assert_blocked_before_exit_without_clear(self):
        self.block_return.assert_called_once()
        self.assertLess(self.adapter.events.index("block"), self.adapter.events.index("release"))
        self.assertLess(self.adapter.events.index("block"), self.adapter.events.index("unsubscribe"))
        self.assertNotIn("clear", self.adapter.events)
        self.assertEqual(self.adapter.current_alarm, 1300)

    def signal(self, active):
        self.closed = active
        self.adapter.callback(None, None, None, None, None,
                              SimpleNamespace(unpack=lambda: (active,)))

    def test_prearms_once_and_only_native_pair_clears_owned_alarm(self):
        def cycle():
            self.assertEqual(self.adapter.events[:3], ["inhibitor", "subscribe", ("arm", 300)])
            self.assertEqual(self.messages[-1][0], "armed-waiting-for-native-close")
            self.assertFalse(self.session.lifecycle.request_sent)
            self.signal(True)
            self.assertTrue(self.session.lifecycle.request_sent)
            self.assertEqual(self.session.inhibitor, -1)
            self.assertNotIn("clear", self.adapter.events)
            self.signal(False)
        self.loop.work = cycle
        result = self.session.run()
        self.assertEqual(result["native_lifecycle"], "returned")
        self.assertEqual(result["prepare_values"], [True, False])
        self.assertEqual(self.adapter.events.count("clear"), 1)
        self.assertIsNone(self.adapter.current_alarm)
        self.block_return.assert_not_called()

    def test_closed_return_blocks_daily_before_clearing_owned_rescue(self):
        def cycle():
            self.signal(True)
            self.adapter.callback(None, None, None, None, None,
                                  SimpleNamespace(unpack=lambda: (False,)))
        self.loop.work = cycle
        with self.assertRaisesRegex(RuntimeError, "daily sleep blocked"):
            self.session.run()
        self.assertLess(self.adapter.events.index("block"), self.adapter.events.index("clear"))
        self.assertEqual(self.session.result["native_lifecycle"], "returned-but-blocked")
        self.assertIsNone(self.adapter.current_alarm)
        self.block_return.assert_called_once()  # Finally must not repeat the successful latch.

    def test_unknown_return_also_blocks_before_cleanup(self):
        def cycle():
            self.signal(True)
            self.session.lid_closed = Mock(side_effect=OSError("lid unavailable"))
            self.signal(False)
        self.loop.work = cycle
        with self.assertRaisesRegex(RuntimeError, "daily sleep blocked"):
            self.session.run()
        self.assertLess(self.adapter.events.index("block"), self.adapter.events.index("clear"))

    def test_failed_or_unavailable_block_preserves_rescue(self):
        for blocker in (None, Mock(side_effect=OSError("cannot persist runtime block"))):
            with self.subTest(blocker=blocker):
                self.setUp()
                self.session.block_return = blocker
                def cycle():
                    self.signal(True)
                    self.adapter.callback(None, None, None, None, None,
                                          SimpleNamespace(unpack=lambda: (False,)))
                self.loop.work = cycle
                with self.assertRaises((RuntimeError, OSError)):
                    self.session.run()
                self.assertNotIn("clear", self.adapter.events)
                self.assertEqual(self.adapter.current_alarm, 1300)

    def test_closed_return_block_does_not_authorize_clearing_foreign_alarm(self):
        def cycle():
            self.signal(True)
            self.adapter.current_alarm = 9000
            self.adapter.callback(None, None, None, None, None,
                                  SimpleNamespace(unpack=lambda: (False,)))
        self.loop.work = cycle
        with self.assertRaisesRegex(RuntimeError, "ownership changed"):
            self.session.run()
        self.block_return.assert_called_once()
        self.assertNotIn("clear", self.adapter.events)
        self.assertEqual(self.adapter.current_alarm, 9000)

    def test_fixed_rescue_remains_inside_existing_guard_range(self):
        self.assertEqual(app.RTC_SECONDS, 300)
        self.assertLessEqual(app.RTC_SECONDS, guard_constants["MAX_RTC_SECONDS"])
        self.assertGreaterEqual(app.RTC_SECONDS, guard_constants["MIN_RTC_SECONDS"])
        self.guard.MAX_RTC_SECONDS = 299
        with self.assertRaisesRegex(RuntimeError, "unchanged guard range"):
            self.session.run()
        self.assertEqual(self.adapter.events, [])
        self.block_return.assert_not_called()

    def test_latest_close_and_prepare_leave_at_least_sixty_seconds_for_rescue(self):
        # Use the minimum accepted actual alarm readback, not just nominal 300.
        self.adapter.epoch = 1030
        def cycle():
            self.assertEqual(self.messages[-1][1]["rtc_remaining_seconds"], 270)
            self.now += app.CLOSE_WINDOW_SECONDS
            self.closed = True
            self.assertTrue(self.session.tick())
            self.now += app.PREPARE_AFTER_CLOSE_SECONDS
            self.signal(True)
            self.now += app.OPEN_AFTER_CLOSE_SECONDS - app.PREPARE_AFTER_CLOSE_SECONDS
            margin = self.adapter.current_alarm - (self.adapter.epoch + self.now - 1000)
            self.assertGreaterEqual(margin, 60)
            self.signal(False)
        self.loop.work = cycle
        self.assertEqual(self.session.run()["native_lifecycle"], "returned")

    def test_short_actual_rescue_margin_refuses_before_ready_and_preserves_alarm(self):
        self.adapter.epoch = 1031
        with self.assertRaisesRegex(RuntimeError, "margin is too short"):
            self.session.run()
        self.assertFalse(any(event == "armed-waiting-for-native-close" for event, _ in self.messages))
        self.assertEqual(self.adapter.current_alarm, 1300)
        self.assertNotIn("clear", self.adapter.events)
        self.assert_blocked_before_exit_without_clear()

    def test_late_close_is_not_accepted_even_if_no_timeout_tick_ran_yet(self):
        def cycle():
            self.now += 181
            self.signal(True)
            self.signal(False)
        self.loop.work = cycle
        with self.assertRaisesRegex(RuntimeError, "missed the physical-close window"):
            self.session.run()
        self.assert_blocked_before_exit_without_clear()

    def test_prepare_later_than_fifteen_seconds_after_close_is_rejected(self):
        def cycle():
            self.closed = True
            self.session.tick()
            self.now += 16
            self.signal(True)
            self.signal(False)
        self.loop.work = cycle
        with self.assertRaisesRegex(RuntimeError, "missed the physical-close window"):
            self.session.run()
        self.assert_blocked_before_exit_without_clear()

    def test_reopen_before_native_prepare_preserves_alarm(self):
        def cycle():
            self.closed = True
            self.session.tick()
            self.closed = False
            self.session.tick()
        self.loop.work = cycle
        with self.assertRaisesRegex(RuntimeError, "reopened before native prepare"):
            self.session.run()
        self.assertNotIn("clear", self.adapter.events)

    def test_foreign_existing_alarm_refuses_before_arm(self):
        self.adapter.current_alarm = 9000
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            self.session.run()
        self.assertNotIn(("arm", 300), self.adapter.events)
        self.assertNotIn("clear", self.adapter.events)
        self.assertEqual(self.session.inhibitor, -1)
        self.block_return.assert_not_called()

    def test_closed_or_preparing_start_refuses_without_rtc_write(self):
        self.closed = True
        with self.assertRaisesRegex(RuntimeError, "must arm while open"):
            self.session.run()
        self.assertNotIn(("arm", 300), self.adapter.events)
        self.assertNotIn("clear", self.adapter.events)

    def test_false_without_true_never_clears(self):
        self.loop.work = lambda: self.signal(False)
        with self.assertRaisesRegex(RuntimeError, "did not complete"):
            self.session.run()
        self.assertEqual(self.adapter.current_alarm, 1300)
        self.assertNotIn("clear", self.adapter.events)
        self.assert_blocked_before_exit_without_clear()

    def test_duplicate_prepare_is_not_a_clean_owned_cycle(self):
        def cycle():
            self.signal(True)
            self.signal(True)
            self.signal(False)
        self.loop.work = cycle
        with self.assertRaisesRegex(RuntimeError, "not exactly one"):
            self.session.run()
        self.assertNotIn("clear", self.adapter.events)
        self.block_return.assert_called_once()

    def test_alarm_replaced_by_another_owner_is_never_cleared(self):
        def cycle():
            self.signal(True)
            self.adapter.current_alarm = 9000
            self.signal(False)
        self.loop.work = cycle
        with self.assertRaisesRegex(RuntimeError, "ownership changed"):
            self.session.run()
        self.assertEqual(self.adapter.current_alarm, 9000)
        self.assertNotIn("clear", self.adapter.events)

    def test_expired_empty_alarm_after_real_pair_needs_no_write(self):
        def cycle():
            self.signal(True)
            self.adapter.current_alarm = None
            self.signal(False)
        self.loop.work = cycle
        self.assertEqual(self.session.run()["rtc_cleanup"], "already-empty-after-resume")
        self.assertNotIn("clear", self.adapter.events)

    def test_no_native_request_timeout_preserves_rescue(self):
        def timeout():
            self.now += 181
            self.assertFalse(self.session.tick())
        self.loop.work = timeout
        with self.assertRaisesRegex(RuntimeError, "window expired"):
            self.session.run()
        self.assertNotIn("clear", self.adapter.events)
        self.assertEqual(self.adapter.current_alarm, 1300)
        self.assert_blocked_before_exit_without_clear()

    def test_missing_resume_timeout_preserves_rescue(self):
        def timeout():
            self.signal(True)
            self.now += 336
            self.session.tick()
        self.loop.work = timeout
        with self.assertRaisesRegex(RuntimeError, "lifecycle timed out"):
            self.session.run()
        self.assertNotIn("clear", self.adapter.events)

    def test_interruption_preserves_alarm_and_releases_inhibitor_subscription(self):
        def abort():
            raise RuntimeError("interrupted")
        self.loop.work = abort
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.session.run()
        self.assertEqual(self.adapter.current_alarm, 1300)
        self.assertEqual(self.session.inhibitor, -1)
        self.assertIn("unsubscribe", self.adapter.events)
        self.assert_blocked_before_exit_without_clear()

    def test_base_exception_interrupt_blocks_before_releasing_supervision(self):
        def abort():
            raise KeyboardInterrupt("attended test stopped")
        self.loop.work = abort
        with self.assertRaises(KeyboardInterrupt):
            self.session.run()
        self.assert_blocked_before_exit_without_clear()

    def test_arm_write_then_readback_error_blocks_before_release_and_keeps_alarm(self):
        original = self.adapter.arm_rtc
        def uncertain_arm(seconds):
            original(seconds)
            raise OSError("RTC readback failed after write")
        self.adapter.arm_rtc = uncertain_arm
        with self.assertRaisesRegex(OSError, "after write"):
            self.session.run()
        self.assertTrue(self.session.arm_attempted)
        self.assertIsNone(self.session.alarm)  # The failed arm did not confirm ownership.
        self.assert_blocked_before_exit_without_clear()

    def test_timeout_block_failure_is_explicit_and_does_not_clear_rescue(self):
        def failed_block():
            self.adapter.events.append("block-attempt")
            raise OSError("runtime block write failed")
        self.session.block_return = failed_block
        def timeout():
            self.now += 181
            self.session.tick()
        self.loop.work = timeout
        with self.assertRaisesRegex(OSError, "runtime block write failed"):
            self.session.run()
        self.assertLess(self.adapter.events.index("block-attempt"), self.adapter.events.index("release"))
        self.assertNotIn("clear", self.adapter.events)
        self.assertEqual(self.adapter.current_alarm, 1300)
        self.assertEqual(self.session.result["daily_block"], "write-failed")
        self.assertIn("window expired", self.session.result["supervision_error"])

    def test_failed_clear_does_not_report_returned_lifecycle(self):
        self.adapter.clear_ok = False
        self.loop.work = lambda: (self.signal(True), self.signal(False))
        with self.assertRaisesRegex(RuntimeError, "did not clear"):
            self.session.run()
        self.assertEqual(self.session.result["native_lifecycle"], "incomplete")


class PassiveBoundaryTests(unittest.TestCase):
    def test_check_never_starts_observer_or_allocates_lock_session_or_receipt(self):
        common, guard, adapter, layout, daily = (Mock() for _ in range(5))
        args = arguments()
        args.command = "check"
        with patch.object(app, "preflight", return_value=(daily, guard, adapter, layout, {})), \
             patch.object(app, "NativeSession") as session, \
             patch.object(app.Path, "mkdir") as mkdir:
            self.assertEqual(app.perform(common, args), {"native_preflight": {}, "write_executed": False})
        common.start_observer.assert_not_called()
        guard.acquire_lock.assert_not_called()
        session.assert_not_called()
        mkdir.assert_not_called()

    def test_consumed_receipt_refuses_before_importing_guard_or_daily(self):
        common = Mock()
        common.require.side_effect = require
        with patch.object(app.os, "geteuid", return_value=0), \
             patch.object(app.os.path, "lexists", return_value=True), \
             self.assertRaisesRegex(RuntimeError, "fresh receipt"):
            app.preflight(common, arguments())
        common.load_trusted.assert_not_called()

    def test_parameters_require_new_receipt_exact_boot_and_every_reviewed_hash(self):
        common = SimpleNamespace(require=require)
        app.identity_parameters(common, arguments())
        for key, value in (("boot_id", "wrong"), ("expected_success", True),
                           ("expected_success", -1), ("common_sha256", "a" * 63),
                           ("observer_sha256", "A" * 64),
                           ("receipt", Path("/tmp/pds-lid-cycle-native")),
                           ("receipt", Path("/run/pds-lid-cycle-native/child"))):
            args = arguments()
            setattr(args, key, value)
            with self.subTest(key=key), self.assertRaises((RuntimeError, ValueError)):
                app.identity_parameters(common, args)

    def test_physical_event_timing_overrides_late_logind_observation(self):
        common = SimpleNamespace(require=require, assess=Mock(return_value={
            "physical_display_acceptance": "pending"}), raw_lid_bounds=common_app.raw_lid_bounds)
        def evidence(closed, prepared):
            return [{"event": "native-lid", "closed": True, "boottime_ns": (closed + 1) * 10**9,
                     "input_clock": "CLOCK_BOOTTIME", "input_boottime_ns": closed * 10**9,
                     "input_sec": closed, "input_usec": 0},
                    {"event": "prepare-for-sleep", "active": True, "boottime_ns": prepared * 10**9}]
        self.assertEqual(app.assess_native(common, evidence(1180, 1195), {}, 1000),
                         {"physical_display_acceptance": "pending"})
        for closed, prepared in ((999, 1000), (1181, 1182), (1000, 1016)):
            with self.subTest(closed=closed, prepared=prepared), self.assertRaises(RuntimeError):
                app.assess_native(common, evidence(closed, prepared), {}, 1000)

    def test_closed_return_writes_only_boot_scoped_daily_runtime_latch(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "blocked.json"
            daily = SimpleNamespace(BLOCK=path,
                save_runtime=Mock(side_effect=lambda target, data: target.write_text(json.dumps(data))))
            app.block_native_return(daily, "current-boot", "/run/pds-lid-cycle-new")
            daily.save_runtime.assert_called_once()
            self.assertEqual(daily.save_runtime.call_args.args[0], path)
            self.assertEqual(json.loads(path.read_text()), {
                "boot_id": "current-boot",
                "reason": "attended native supervision did not finish with a verified open return",
                "receipt": "/run/pds-lid-cycle-new"})
            self.assertEqual(list(Path(temporary).iterdir()), [path])

    def test_missing_runtime_readback_refuses_cleanup_authorization(self):
        with tempfile.TemporaryDirectory() as temporary:
            daily = SimpleNamespace(BLOCK=Path(temporary) / "blocked.json", save_runtime=Mock())
            with self.assertRaisesRegex(RuntimeError, "did not persist"):
                app.block_native_return(daily, "current-boot", "/run/pds-lid-cycle-new")

    def test_no_active_sleep_or_policy_display_api_is_called(self):
        tree = ast.parse(SOURCE.read_text())
        forbidden = {"request_suspend", "requestShutdown", "atomic_write", "set_dpms", "wake_display",
                     "clear_rtc", "arm_rtc"}
        calls = [(node.func.attr, node.lineno) for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        # RTC mutation is delegated exclusively to the existing guard adapter.
        for name, _line in calls:
            self.assertNotIn(name, forbidden - {"clear_rtc", "arm_rtc"})
        self.assertEqual(sum(name == "arm_rtc" for name, _line in calls), 1)
        self.assertEqual(sum(name == "clear_rtc" for name, _line in calls), 1)

    def test_daily_gate_failure_prevents_observer_lock_and_arm(self):
        common = Mock()
        common.require.side_effect = require
        daily = Mock()
        common.NOTES = daily.NOTES = "notes"
        common.IMAGES = {"v4": ("image", 1)}
        daily.IMAGE = "image"
        daily.eligible.side_effect = RuntimeError("daily disabled")
        common.load_trusted.return_value = daily
        args = arguments()
        with patch.object(app.os, "geteuid", return_value=0), \
             patch.object(app.os.path, "lexists", return_value=False), \
             self.assertRaisesRegex(RuntimeError, "daily disabled"):
            app.preflight(common, args)
        common.start_observer.assert_not_called()
        self.assertEqual(common.load_trusted.call_count, 1)


class NativeActorTests(unittest.TestCase):
    def setUp(self):
        account = patch.object(app.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1000))
        account.start()
        self.addCleanup(account.stop)
        self.actor = {"owner": ":1.1691", "pid": 37410, "sha256": "a" * 64,
                      "device": (8, 13), "inode": 5898388}
        self.daily = SimpleNamespace(powerdevil_resume_ready=Mock(return_value=self.actor),
                                     process_start=Mock(return_value=595443))
        self.mode = "sleep"
        self.sleep_modes = {profile: "1" for profile in ("AC", "Battery", "LowBattery")}
        self.capability = {"type": "b", "data": [True]}
        self.pkcheck_error = None
        self.calls = []
        def run(*argv, **kwargs):
            self.calls.append((argv, kwargs))
            if argv[-1] == "owner":
                # The owner path deliberately does not assert this SSH/root
                # helper's own CanSuspend. Its default unavailable is harmless.
                return json.dumps({"mode": self.mode, "sleep_available": False,
                                   "lid_open": None, "reason_code": "ready"})
            if "/usr/bin/kreadconfig6" in argv:
                profile = argv[argv.index("--group") + 1]
                return self.sleep_modes[profile]
            if argv[0] == "/usr/bin/pkcheck":
                if self.pkcheck_error:
                    raise self.pkcheck_error
                return "polkit.result=yes"
            if argv[-1] == "CanSuspend":
                return json.dumps(self.capability)
            raise AssertionError("unexpected or mutating command: " + repr(argv))
        self.common = SimpleNamespace(require=require, run=Mock(side_effect=run),
                                      lid_closed=Mock(return_value=False))

    def test_background_supervisor_checks_verified_desktop_actor_without_interaction(self):
        result = app.native_mode(self.common, self.daily)
        self.assertEqual(result, {**self.actor, "start_time_ticks": 595443, "uid": 1000,
                                  "suspend_authorized": True, "can_suspend": True})
        auth = next(call for call in self.calls if call[0][0] == "/usr/bin/pkcheck")
        self.assertEqual(auth, (("/usr/bin/pkcheck", "--action-id", "org.freedesktop.login1.suspend",
                                "--process", "37410,595443,1000"), {"timeout": 4}))
        capability = next(argv for argv, _ in self.calls if argv[-1] == "CanSuspend")
        self.assertEqual(capability[capability.index("call") + 1], ":1.1691")
        self.assertIn("--auto-start=no", capability)
        self.assertNotIn("--allow-user-interaction", [arg for argv, _ in self.calls for arg in argv])
        self.daily.powerdevil_resume_ready.assert_has_calls([unittest.mock.call(), unittest.mock.call()])
        self.assertEqual(self.common.lid_closed.call_count, 2)

    def test_wrong_lid_selection_stops_before_actor_or_polkit_query(self):
        for mode in ("connected", "unknown", None):
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, "lid action"):
                app.native_mode(self.common, self.daily)
        self.daily.powerdevil_resume_ready.assert_not_called()

    def test_each_profile_requires_suspend_to_ram_without_rewriting_it(self):
        for profile in self.sleep_modes:
            self.sleep_modes[profile] = "3"
            with self.subTest(profile=profile), self.assertRaisesRegex(RuntimeError, "SleepMode"):
                app.native_mode(self.common, self.daily)
            self.sleep_modes[profile] = "1"
        self.daily.powerdevil_resume_ready.assert_not_called()

    def test_closed_or_unknown_lid_stops_before_actor_authorization(self):
        for closed in (True, None):
            self.common.lid_closed.return_value = closed
            with self.subTest(closed=closed), self.assertRaisesRegex(RuntimeError, "open lid"):
                app.native_mode(self.common, self.daily)
        self.daily.powerdevil_resume_ready.assert_not_called()

    def test_invalid_actor_artifact_or_uid_is_not_replaced_by_service_pid(self):
        self.daily.powerdevil_resume_ready.side_effect = RuntimeError("verified actor UID or plugin differs")
        with self.assertRaisesRegex(RuntimeError, "UID or plugin"):
            app.native_mode(self.common, self.daily)
        self.assertFalse(any(argv[0] == "/usr/bin/pkcheck" for argv, _ in self.calls))

    def test_polkit_denial_and_timeout_remain_failures(self):
        for error in (subprocess.CalledProcessError(1, "pkcheck"), subprocess.TimeoutExpired("pkcheck", 4)):
            self.pkcheck_error = error
            with self.subTest(error=error), self.assertRaises(type(error)):
                app.native_mode(self.common, self.daily)
        self.assertFalse(any(argv[-1] == "CanSuspend" for argv, _ in self.calls))

    def test_cached_denial_or_malformed_capability_is_not_accepted(self):
        for reply in ({"type": "b", "data": [False]}, {"type": "b", "data": True},
                      {"type": "b", "data": [1]}, {"type": "b", "data": [True, True]},
                      {"type": "s", "data": ["yes"]}, []):
            self.capability = reply
            with self.subTest(reply=reply), self.assertRaisesRegex(RuntimeError, "capability"):
                app.native_mode(self.common, self.daily)

    def test_owner_restart_after_authorization_is_rejected(self):
        self.daily.powerdevil_resume_ready.side_effect = [self.actor, {**self.actor, "owner": ":1.1692"}]
        with self.assertRaisesRegex(RuntimeError, "actor changed"):
            app.native_mode(self.common, self.daily)

    def test_pid_reuse_after_authorization_is_rejected(self):
        self.daily.process_start.side_effect = [595443, 595444]
        with self.assertRaisesRegex(RuntimeError, "actor changed"):
            app.native_mode(self.common, self.daily)

    def test_lid_closure_during_authorization_is_rejected(self):
        self.common.lid_closed.side_effect = [False, True]
        with self.assertRaisesRegex(RuntimeError, "lid closed during"):
            app.native_mode(self.common, self.daily)


if __name__ == "__main__":
    unittest.main()

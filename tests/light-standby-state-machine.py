#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import json
import subprocess
import os
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "components/light-standby/pocketds-light-standby.py"
SPEC = importlib.util.spec_from_file_location("pocketds_light_standby", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
try:
    import gi  # noqa: F401
except ModuleNotFoundError:
    # The state machine fixtures need no desktop session. Linux still runs the
    # real Gio FD ABI test below; only that test is skipped on hosts without GI.
    gi_stub = types.ModuleType('gi')
    gi_stub.require_version = lambda *_args: None
    repository_stub = types.ModuleType('gi.repository')
    repository_stub.Gio = types.SimpleNamespace()
    repository_stub.GLib = types.SimpleNamespace(
        MainLoop=mock.Mock, timeout_add=mock.Mock(return_value=1),
        source_remove=mock.Mock(), SOURCE_REMOVE=False,
    )
    with mock.patch.dict(sys.modules, {'gi': gi_stub, 'gi.repository': repository_stub}):
        SPEC.loader.exec_module(MODULE)
else:
    SPEC.loader.exec_module(MODULE)


class FakeAdapter:
    def __init__(self, profile: str | None = "balanced") -> None:
        self.profile = profile
        self.lid_closed = True
        self.external_power = False
        self.boot_id = "boot-one"
        self.poweroff_requests = 0
        self.actions: list[tuple[str, str]] = []
        self.fail_dpms: set[str] = set()
        self.fail_wakeup = False
        self.fail_profile: set[str] = set()
        self.daily_deep = False
        self.lid_mode = "connected"
        self.dark = False
        self.preparing = False

    def get_lid_mode(self, refresh=False) -> str:
        return self.lid_mode

    def acquire_sleep_delay(self):
        return None

    def get_preparing_for_sleep(self):
        return self.preparing

    def displays_dark(self, strict=False):
        return self.dark

    def blank_displays(self):
        return self.set_dpms("off")

    def request_dpms(self, mode, _timeout, reuse_off=False):
        return self.set_dpms(mode)

    def blank_bottom(self, _timeout):
        return True

    def forget_pending_off(self):
        pass

    def dpms_status(self, _timeout):
        return {'DSI-1': 'off' if self.dark else 'on', 'DSI-2': 'off' if self.dark else 'on'}

    dpms_state_matches = staticmethod(MODULE.LiveAdapter.dpms_state_matches)

    def current_profile(self) -> str | None:
        return self.profile

    def get_lid_closed(self, timeout_seconds=2.0) -> bool:
        return self.lid_closed

    def get_external_power(self) -> bool:
        return self.external_power

    def current_boot_id(self) -> str:
        return self.boot_id

    def power_off(self) -> bool:
        self.poweroff_requests += 1
        return True

    def set_dpms(self, mode: str) -> bool:
        self.actions.append(("dpms", mode))
        okay = mode not in self.fail_dpms
        if okay:
            self.dark = mode == "off"
        return okay

    def wake_display(self) -> bool:
        self.actions.append(("wakeup", "powerdevil"))
        if not self.fail_wakeup:
            self.dark = False
        return not self.fail_wakeup

    def set_profile(self, profile: str) -> bool:
        self.actions.append(("profile", profile))
        if profile in self.fail_profile:
            return False
        self.profile = profile
        return True


class CountingStateStore(MODULE.StateStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.save_count = 0

    def save(self, data) -> None:
        self.save_count += 1
        super().save(data)


class PreparationScheduler:
    """Deterministic GLib source order; no sleep, event devices or real bus."""
    def __init__(self):
        self.now = 100.0
        self.sources = {}
        self.next_id = 0
        self.removed = []

    def add(self, milliseconds, callback, *args):
        self.next_id += 1
        self.sources[self.next_id] = (self.now + milliseconds / 1000, callback, args)
        return self.next_id

    def remove(self, source):
        self.removed.append(source)
        self.sources.pop(source, None)

    def advance(self, seconds):
        until = self.now + seconds
        for _ in range(1000):
            ready = [(entry[0], source) for source, entry in self.sources.items() if entry[0] <= until]
            if not ready:
                self.now = max(self.now, until)
                return
            due, source = min(ready)
            _due, callback, args = self.sources.pop(source)
            self.now = max(self.now, due)
            callback(*args)
        raise AssertionError('preparation did not reach a bounded result')


class PreparingHardware(FakeAdapter):
    """KWin animation, kernel blank, and lower raw zero are separate events."""
    request_dpms = MODULE.LiveAdapter.request_dpms
    forget_pending_off = MODULE.LiveAdapter.forget_pending_off
    _graphical_environment = staticmethod(lambda: {})

    def __init__(self, scheduler):
        super().__init__()
        self.scheduler = scheduler
        self.preparing = True
        self.request_time = None
        self.dpms_delay = 0.250
        self.power_delay = 0.400
        self.raw = 80
        self.blank_attempts = []
        self.query_timeouts = []

    def _run(self, command, timeout, environment=None):
        assert command[:2] == ['/usr/bin/kscreen-doctor', '--dpms']
        mode = command[-1]
        self.actions.append(('dpms', mode))
        self.request_time = self.scheduler.now if mode == 'off' else None
        return True

    def dpms_status(self, timeout):
        self.query_timeouts.append((self.scheduler.now, timeout))
        off = self.request_time is not None and self.scheduler.now >= self.request_time + self.dpms_delay
        return {'DSI-1': 'off' if off else 'on', 'DSI-2': 'off' if off else 'on'}

    def power_is_off(self):
        return self.request_time is not None and self.scheduler.now >= self.request_time + self.power_delay

    def displays_dark(self, strict=False):
        return self.power_is_off() and self.raw == 0

    def blank_bottom(self, timeout):
        # The actual privileged helper refuses to write raw brightness until
        # bl_power has transitioned; an accepted DPMS request is insufficient.
        accepted = self.power_is_off()
        self.blank_attempts.append((self.scheduler.now, accepted))
        if accepted:
            self.raw = 0
        return accepted


class SleepPreparationTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = PreparationScheduler()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.adapter = PreparingHardware(self.scheduler)
        controller = MODULE.LightStandbyController(
            self.adapter, MODULE.StateStore(Path(self.temp.name) / 'state'),
            MODULE.AutoPoweroffSettings(Path(self.temp.name) / 'settings'),
        )
        self.daemon = MODULE.LidDaemon(self.adapter, controller)
        self.fd = os.open(os.devnull, os.O_RDONLY)
        self.daemon.sleep_delay_fd = self.fd
        self.addCleanup(self.daemon._release_sleep_delay)
        for target, attribute, value in [
            (MODULE.time, 'monotonic', lambda: self.scheduler.now),
            (MODULE.GLib, 'timeout_add', self.scheduler.add),
            (MODULE.GLib, 'source_remove', self.scheduler.remove),
        ]:
            patcher = mock.patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.events = mock.Mock()
        patcher = mock.patch.object(MODULE, 'emit', self.events)
        patcher.start()
        self.addCleanup(patcher.stop)

    def result(self):
        return [call.kwargs for call in self.events.call_args_list
                if call.args[0] == 'sleep-display-prepared']

    def test_holds_delay_through_kwin_animation_and_kernel_blank_then_raw_zero(self):
        self.daemon.prepare_sleep(True)
        self.assertEqual(self.adapter.actions, [('dpms', 'off')])
        self.assertEqual(self.adapter.blank_attempts, [])
        self.scheduler.advance(0.32)
        os.fstat(self.fd)
        self.assertEqual(self.result(), [])
        self.assertEqual(self.adapter.raw, 80)
        self.assertTrue(self.adapter.blank_attempts)
        self.assertTrue(all(not accepted for _at, accepted in self.adapter.blank_attempts))
        self.scheduler.advance(0.2)
        self.assertEqual(self.adapter.raw, 0)
        self.assertEqual(self.result(), [{'okay': True, 'reason': 'dark'}])
        self.assertEqual(self.adapter.actions, [('dpms', 'off')])
        with self.assertRaises(OSError):
            os.fstat(self.fd)

    def test_reuses_normal_off_pending_before_prepare_without_early_success(self):
        self.assertFalse(MODULE.LiveAdapter.set_dpms(self.adapter, 'off', 0.5))
        self.scheduler.advance(0.1)
        self.daemon.prepare_sleep(True)
        self.scheduler.advance(0.15)
        os.fstat(self.fd)
        self.assertEqual(self.result(), [])
        self.scheduler.advance(0.35)
        self.assertEqual(self.adapter.actions, [('dpms', 'off')])
        self.assertEqual(self.result(), [{'okay': True, 'reason': 'dark'}])

    def test_queued_normal_debounce_is_cancelled_and_does_not_compete(self):
        parameters = mock.Mock()
        parameters.unpack.return_value = ('manager', {'LidClosed': True}, [])
        self.daemon._on_signal(None, '', '', '', '', parameters)
        old_source = self.daemon.debounce_source
        self.daemon.prepare_sleep(True)
        self.assertIn(old_source, self.scheduler.removed)
        self.assertEqual(self.daemon.debounce_source, 0)
        with mock.patch.object(self.adapter, 'get_preparing_for_sleep', side_effect=AssertionError('normal query')):
            self.daemon._after_debounce()  # even an already-dispatched old callback cannot act
            self.daemon._tick()
        self.scheduler.advance(0.6)
        self.assertEqual(self.adapter.actions, [('dpms', 'off')])

    def test_pfs_false_invalidates_old_callback_before_new_fd_or_wake(self):
        self.daemon.prepare_sleep(True)
        old_generation = self.daemon.prepare_generation
        old_callback = self.scheduler.sources[self.daemon.prepare_source]
        self.adapter.lid_closed = False
        self.adapter.preparing = False
        replacement = os.open(os.devnull, os.O_RDONLY)
        with mock.patch.object(self.adapter, 'acquire_sleep_delay', return_value=replacement):
            self.daemon.prepare_sleep(False)
        actions = list(self.adapter.actions)
        self.assertIn(('wakeup', 'powerdevil'), actions)
        old_callback[1](*old_callback[2])
        self.daemon._prepare_step(old_generation)
        self.scheduler.advance(5)
        self.assertEqual(self.adapter.actions, actions)
        self.assertEqual(self.daemon.sleep_delay_fd, replacement)
        os.fstat(replacement)
        self.assertIsNone(self.adapter._off_requested_at)
        self.assertEqual(self.result(), [])

    def test_physical_open_cancels_without_wakeup_or_further_blank(self):
        self.daemon.prepare_sleep(True)
        self.adapter.lid_closed = False
        self.scheduler.advance(0.6)
        self.assertEqual(self.result(), [{'okay': False, 'reason': 'lid-open'}])
        self.assertEqual(self.adapter.blank_attempts, [])
        self.assertNotIn(('wakeup', 'powerdevil'), self.adapter.actions)
        self.daemon._apply()
        self.assertNotIn(('wakeup', 'powerdevil'), self.adapter.actions)

    def test_open_during_dpms_query_never_blanks_bottom_or_claims_ready(self):
        self.adapter.dpms_delay = 0
        self.adapter.power_delay = 0
        real_query = self.adapter.dpms_status
        def query(timeout):
            self.adapter.lid_closed = False
            return real_query(timeout)
        self.adapter.dpms_status = query
        self.daemon.prepare_sleep(True)
        self.assertEqual(self.adapter.blank_attempts, [])
        self.assertEqual(self.result(), [{'okay': False, 'reason': 'lid-open'}])

    def test_timeout_does_not_confuse_accepted_off_with_completed_off(self):
        self.adapter.dpms_delay = 60
        self.daemon.prepare_sleep(True)
        self.scheduler.advance(3.9)
        os.fstat(self.fd)
        self.assertEqual(self.result(), [])
        self.scheduler.advance(0.1)
        self.assertEqual(self.result(), [{'okay': False, 'reason': 'deadline'}])
        self.assertEqual(self.adapter.actions, [('dpms', 'off')])
        self.assertEqual(self.adapter.blank_attempts, [])
        self.assertLessEqual(self.scheduler.now, 104.001)

    def test_slow_queries_share_the_deadline_instead_of_renewing_timeouts(self):
        def slow_query(timeout):
            self.adapter.query_timeouts.append((self.scheduler.now, timeout))
            self.scheduler.now += timeout
            return None
        self.adapter.dpms_status = slow_query
        self.daemon.prepare_sleep(True)
        self.scheduler.advance(3.6)
        self.assertEqual(self.result(), [{'okay': False, 'reason': 'deadline'}])
        self.assertLessEqual(self.scheduler.now, 104.001)
        for started, timeout in self.adapter.query_timeouts:
            self.assertGreater(timeout, 0)
            self.assertLessEqual(started + timeout, 104.0)
        self.assertLess(self.adapter.query_timeouts[-1][1], 0.4)

    def test_changed_dpms_after_pending_off_fails_closed_instead_of_assuming_timer(self):
        self.daemon.prepare_sleep(True)
        # Another client can cancel KWin's timer. Do not treat our successful
        # request or elapsed time as proof of dark hardware.
        self.adapter.request_time = None
        self.scheduler.advance(4)
        self.assertEqual(self.result(), [{'okay': False, 'reason': 'deadline'}])
        self.assertEqual(self.adapter.actions, [('dpms', 'off')])

    def test_new_cycle_does_not_reuse_previous_cycle_off(self):
        self.daemon.prepare_sleep(True)
        self.adapter.lid_closed = False
        self.adapter.preparing = False
        self.daemon.prepare_sleep(False)
        self.adapter.lid_closed = True
        self.adapter.preparing = True
        self.daemon.prepare_sleep(True)
        self.assertEqual(self.adapter.actions.count(('dpms', 'off')), 2)

    def test_strict_preparation_checks_exact_poweroff_without_upper_actual_read(self):
        for power, ordinary, strict in [(0, False, False), (1, True, False),
                                         (3, True, False), (4, True, True)]:
            reads = []
            def read(path, *_args, **_kwargs):
                reads.append(str(path))
                if path.name == 'bl_power':
                    return str(power)
                if str(path.parent).endswith('/sy7758-backlight'):
                    return '0'
                raise AssertionError(f'unexpected upper read: {path}')
            with mock.patch.object(Path, 'read_text', read):
                self.assertEqual(MODULE.LiveAdapter.displays_dark(), ordinary)
                self.assertEqual(MODULE.LiveAdapter.displays_dark(strict=True), strict)
            self.assertNotIn('/sys/class/backlight/ae94000.dsi.0/actual_brightness', reads)


class LightStandbyTests(unittest.TestCase):
    def setUp(self) -> None:
        if not hasattr(MODULE.time, 'CLOCK_BOOTTIME'):
            # These fixtures never suspend. Production keeps CLOCK_BOOTTIME;
            # the Linux run additionally exercises the real kernel constant.
            patcher = mock.patch.object(MODULE.time, 'CLOCK_BOOTTIME', MODULE.time.CLOCK_MONOTONIC, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temporary.name) / "light-standby.json"
        self.setting_path = Path(self.temporary.name) / "auto-poweroff-minutes"
        self.store = CountingStateStore(self.state_path)
        self.settings = MODULE.AutoPoweroffSettings(self.setting_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def controller(self, adapter: FakeAdapter):
        return MODULE.LightStandbyController(adapter, self.store, self.settings)

    def test_native_deep_lid_close_does_not_compete_with_kde(self) -> None:
        adapter = FakeAdapter()
        adapter.daily_deep = True
        adapter.lid_mode = "sleep"
        daemon = MODULE.LidDaemon(adapter, self.controller(adapter))
        self.assertTrue(daemon._apply())
        self.assertEqual(adapter.actions, [('dpms', 'off')])
        self.assertFalse(self.store.exists())
        self.assertEqual(adapter.poweroff_requests, 0)
        adapter.lid_closed = False
        self.assertTrue(daemon._apply())
        self.assertEqual(adapter.actions, [('dpms', 'off'), ('wakeup', 'powerdevil')])

    def test_rejected_daily_kernel_keeps_old_light_standby(self) -> None:
        adapter = FakeAdapter()
        daemon = MODULE.LidDaemon(adapter, self.controller(adapter))
        self.assertTrue(daemon._apply())
        self.assertEqual(adapter.actions, [('dpms', 'off'), ('profile', 'powersave')])

    def test_connected_mode_still_owns_lid_with_daily_sleep_available(self) -> None:
        adapter = FakeAdapter()
        adapter.daily_deep = True
        adapter.lid_mode = "connected"
        daemon = MODULE.LidDaemon(adapter, self.controller(adapter))
        daemon._apply()
        self.assertEqual(adapter.actions, [('dpms', 'off'), ('profile', 'powersave')])

    def test_guard_failure_preserves_selected_sleep_without_connected_autooff(self) -> None:
        adapter = FakeAdapter()
        adapter.daily_deep = True
        adapter.lid_mode = "sleep"
        self.settings.save(60)
        daemon = MODULE.LidDaemon(adapter, self.controller(adapter))
        daemon._apply()
        adapter.daily_deep = False
        daemon._apply()
        self.assertEqual(adapter.actions, [('dpms', 'off')])
        self.assertFalse(self.store.exists())
        adapter.lid_closed = False
        daemon._apply()
        self.assertEqual(adapter.actions[-1:], [('wakeup', 'powerdevil')])

    def test_lid_opened_during_owner_query_never_turns_off_displays(self) -> None:
        adapter = FakeAdapter()
        daemon = MODULE.LidDaemon(adapter, self.controller(adapter))
        daemon.last_lid = True
        def opened_during_query(refresh=False):
            adapter.lid_closed = False
            return False
        adapter.get_lid_mode = opened_during_query
        daemon._apply()
        self.assertEqual(adapter.actions, [('wakeup', 'powerdevil')])
        self.assertFalse(self.store.exists())

    def test_live_lid_owner_reads_selection_independently_of_guard(self) -> None:
        for payload, expected in [
            ({"mode": "sleep", "sleep_available": True}, "sleep"),
            ({"mode": "connected", "sleep_available": True}, "connected"),
            ({"mode": "sleep", "sleep_available": False}, "sleep"),
            ({"mode": "unknown", "sleep_available": True}, "unknown"),
            ([], "unknown"),
            ({"mode": []}, "unknown"),
        ]:
            reply = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
            with self.subTest(payload=payload), mock.patch.object(MODULE.subprocess, "run", return_value=reply):
                adapter = MODULE.LiveAdapter.__new__(MODULE.LiveAdapter)
                adapter._mode_sample = (-100.0, "unknown")
                self.assertEqual(adapter.get_lid_mode(), expected)

    def test_closed_rtc_return_reblanks_without_wake_or_profile_change(self) -> None:
        adapter = FakeAdapter()
        adapter.lid_mode = "sleep"
        daemon = MODULE.LidDaemon(adapter, self.controller(adapter))
        daemon._apply()
        adapter.preparing = True
        daemon.prepare_sleep(True)
        adapter.dark = False  # driver/KWin relit a still-closed lid on return
        adapter.preparing = False
        daemon.prepare_sleep(False)
        self.assertTrue(adapter.dark)
        self.assertEqual(adapter.actions, [('dpms', 'off')] * 3)
        self.assertEqual(adapter.poweroff_requests, 0)
        self.assertFalse(self.store.exists())

    def test_unrelated_closed_lid_relight_is_repaired_without_an_event(self) -> None:
        for mode in ("connected", "sleep", "unknown"):
            adapter = FakeAdapter()
            adapter.lid_mode = mode
            daemon = MODULE.LidDaemon(adapter, self.controller(adapter))
            daemon._apply()
            adapter.dark = False
            daemon._apply()
            self.assertTrue(adapter.dark)
            self.assertEqual(adapter.actions[-1], ('dpms', 'off'))
            self.store.remove()

    def test_unknown_mode_never_arms_connected_shutdown(self) -> None:
        adapter = FakeAdapter()
        adapter.lid_mode = "unknown"
        self.settings.save(60)
        daemon = MODULE.LidDaemon(adapter, self.controller(adapter))
        daemon._apply()
        daemon._apply()
        self.assertFalse(self.store.exists())
        self.assertEqual(adapter.profile, "balanced")
        self.assertEqual(adapter.poweroff_requests, 0)

    def test_connected_to_sleep_clears_owned_timer_and_restores_profile_without_waking(self) -> None:
        adapter = FakeAdapter()
        self.settings.save(60)
        daemon = MODULE.LidDaemon(adapter, self.controller(adapter))
        daemon._apply()
        self.assertIn('auto_poweroff_minutes', self.store.load())
        adapter.lid_mode = "sleep"
        daemon._apply()
        self.assertFalse(self.store.exists())
        self.assertTrue(adapter.dark)
        self.assertEqual(adapter.profile, "balanced")
        self.assertNotIn(('wakeup', 'powerdevil'), adapter.actions)

    def test_sleep_selection_clears_old_deadline_even_if_blanking_fails(self) -> None:
        adapter = FakeAdapter()
        self.settings.save(60)
        daemon = MODULE.LidDaemon(adapter, self.controller(adapter))
        daemon._apply()
        adapter.lid_mode = "sleep"
        adapter.fail_dpms.add("off")
        daemon._apply()
        self.assertNotIn('auto_poweroff_minutes', self.store.load())
        self.assertEqual(adapter.poweroff_requests, 0)

    def test_prepare_releases_delay_fd_even_on_blank_exception(self) -> None:
        adapter = FakeAdapter()
        daemon = MODULE.LidDaemon(adapter, self.controller(adapter))
        fd = os.open(os.devnull, os.O_RDONLY)
        daemon.sleep_delay_fd = fd
        with mock.patch.object(adapter, 'request_dpms', side_effect=RuntimeError('fixture fail')):
            daemon.prepare_sleep(True)
        self.assertIsNone(daemon.sleep_delay_fd)
        with self.assertRaises(OSError):
            os.fstat(fd)
        self.assertTrue(daemon.preparing)

    def test_prepare_never_wakes_even_if_lid_opens_until_completion(self) -> None:
        adapter = FakeAdapter()
        daemon = MODULE.LidDaemon(adapter, self.controller(adapter))
        daemon._apply()
        adapter.preparing = True
        daemon.prepare_sleep(True)
        adapter.lid_closed = False
        daemon._apply()
        self.assertNotIn(('wakeup', 'powerdevil'), adapter.actions)
        adapter.preparing = False
        daemon.prepare_sleep(False)
        self.assertEqual(adapter.actions[-2:], [('wakeup', 'powerdevil'), ('profile', 'balanced')])

    def test_failed_or_missed_completion_signal_recovers_once_and_reacquires_delay(self) -> None:
        adapter = FakeAdapter()
        adapter.lid_closed = False
        daemon = MODULE.LidDaemon(adapter, self.controller(adapter))
        daemon.prepare_sleep(True)
        fd = os.open(os.devnull, os.O_RDONLY)
        self.addCleanup(daemon._release_sleep_delay)
        with mock.patch.object(adapter, 'acquire_sleep_delay', return_value=fd) as acquire:
            daemon._apply()  # PreparingForSleep is already false in fixture
            daemon._apply()
        self.assertFalse(daemon.preparing)
        self.assertEqual(adapter.actions.count(('wakeup', 'powerdevil')), 1)
        self.assertEqual(acquire.call_count, 1)
        os.fstat(fd)  # one live delay lock, not leaked/replaced on every tick

    def test_ordinary_blank_still_checks_physical_result(self) -> None:
        adapter = MODULE.LiveAdapter.__new__(MODULE.LiveAdapter)
        with mock.patch.object(adapter, 'set_dpms', return_value=True) as dpms, \
                mock.patch.object(adapter, 'displays_dark', side_effect=[False, False]), \
                mock.patch.object(adapter, '_run', return_value=True) as run:
            self.assertFalse(adapter.blank_displays())
        dpms.assert_called_once_with('off', 2.0)
        self.assertEqual(run.call_args.args[0][-2:], ['backlight-blank', 'bottom'])
        self.assertEqual(run.call_args.args[1], 2.0)

    def test_delayed_resume_relight_is_repaired_by_bounded_fast_checks(self) -> None:
        adapter = FakeAdapter()
        adapter.lid_mode = "sleep"
        daemon = MODULE.LidDaemon(adapter, self.controller(adapter))
        now = [10.0]
        with mock.patch.object(MODULE.time, 'monotonic', side_effect=lambda: now[0]):
            daemon.prepare_sleep(False)
            adapter.dark = False
            now[0] = 10.25
            daemon._tick()
            self.assertTrue(adapter.dark)
            self.assertEqual(daemon.next_check_at, 10.5)
            now[0] = 12.0
            daemon._tick()
            self.assertEqual(daemon.next_check_at, 13.0)
        self.assertNotIn(('wakeup', 'powerdevil'), adapter.actions)

    def test_fast_resume_checks_stop_when_lid_opens(self) -> None:
        adapter = FakeAdapter()
        adapter.lid_mode = "sleep"
        daemon = MODULE.LidDaemon(adapter, self.controller(adapter))
        now = [10.0]
        with mock.patch.object(MODULE.time, 'monotonic', side_effect=lambda: now[0]):
            daemon.prepare_sleep(False)
            adapter.lid_closed = False
            now[0] = 10.25
            daemon._tick()
            self.assertEqual(daemon.next_check_at, 11.25)
        self.assertEqual(adapter.actions[-1], ('wakeup', 'powerdevil'))

    @unittest.skipUnless(hasattr(MODULE.Gio, 'UnixFDList'), 'native Gio fixture runs on Linux')
    def test_delay_inhibitor_uses_owned_fd_and_delay_mode(self) -> None:
        original = os.open(os.devnull, os.O_RDONLY)
        self.addCleanup(os.close, original)
        descriptors = MODULE.Gio.UnixFDList.new()
        descriptors.append(original)
        reply = MODULE.GLib.Variant('(h)', (0,))
        bus = mock.Mock()
        bus.call_with_unix_fd_list_sync.return_value = (reply, descriptors)
        adapter = MODULE.LiveAdapter.__new__(MODULE.LiveAdapter)
        adapter.bus = bus
        owned = adapter.acquire_sleep_delay()
        try:
            self.assertNotEqual(original, owned)
            os.fstat(owned)
            args = bus.call_with_unix_fd_list_sync.call_args.args
            self.assertEqual(args[3], 'Inhibit')
            self.assertEqual(args[4].unpack()[0], 'sleep')
            self.assertEqual(args[4].unpack()[3], 'delay')
            self.assertEqual(args[7], 1500)
        finally:
            os.close(owned)
        os.fstat(original)

    def test_balanced_close_and_open_orders_display_before_profile(self) -> None:
        adapter = FakeAdapter("balanced")
        controller = self.controller(adapter)
        self.assertTrue(controller.close())
        self.assertEqual(adapter.actions, [("dpms", "off"), ("profile", "powersave")])
        state = self.store.load()
        self.assertEqual(state["restore_profile"], "balanced")
        self.assertEqual(state["power_phase"], "owned")

        adapter.lid_closed = False
        self.assertTrue(controller.open())
        self.assertEqual(
            adapter.actions,
            [
                ("dpms", "off"),
                ("profile", "powersave"),
                ("wakeup", "powerdevil"),
                ("profile", "balanced"),
            ],
        )
        self.assertFalse(self.state_path.exists())

    def test_existing_powersave_is_not_claimed(self) -> None:
        adapter = FakeAdapter("powersave")
        controller = self.controller(adapter)
        self.assertTrue(controller.close())
        self.assertEqual(adapter.actions, [("dpms", "off")])
        self.assertEqual(self.store.load()["power_phase"], "baseline")
        adapter.lid_closed = False
        self.assertTrue(controller.open())
        self.assertEqual(adapter.actions[-1], ("wakeup", "powerdevil"))
        self.assertEqual(adapter.profile, "powersave")

    def test_unmanaged_profile_only_changes_dpms(self) -> None:
        adapter = FakeAdapter(None)
        controller = self.controller(adapter)
        self.assertTrue(controller.close())
        self.assertEqual(adapter.actions, [("dpms", "off")])
        self.assertEqual(self.store.load()["power_phase"], "unmanaged")

    def test_external_profile_change_is_preserved_on_open(self) -> None:
        adapter = FakeAdapter("balanced")
        controller = self.controller(adapter)
        self.assertTrue(controller.close())
        adapter.profile = "performance"
        adapter.lid_closed = False
        self.assertTrue(controller.open())
        self.assertEqual(adapter.profile, "performance")
        self.assertNotIn(("profile", "balanced"), adapter.actions[2:])

    def test_dpms_failure_never_changes_profile(self) -> None:
        adapter = FakeAdapter("balanced")
        adapter.fail_dpms.add("off")
        controller = self.controller(adapter)
        self.assertFalse(controller.close())
        self.assertEqual(adapter.actions, [("dpms", "off")])
        self.assertEqual(adapter.profile, "balanced")
        self.assertEqual(self.store.load()["power_phase"], "pending")

    def test_profile_failure_is_retried_from_pending(self) -> None:
        adapter = FakeAdapter("balanced")
        adapter.fail_profile.add("powersave")
        controller = self.controller(adapter)
        self.assertFalse(controller.close())
        self.assertEqual(self.store.load()["power_phase"], "pending")
        adapter.fail_profile.clear()
        self.assertTrue(controller.close())
        self.assertEqual(adapter.profile, "powersave")
        self.assertEqual(self.store.load()["power_phase"], "owned")

    def test_crash_after_powersave_is_recovered_from_pending(self) -> None:
        self.store.save(
            {
                "schema": 1,
                "active": True,
                "restore_profile": "balanced",
                "power_phase": "pending",
                "closed_unix_ms": 1,
            }
        )
        adapter = FakeAdapter("powersave")
        adapter.lid_closed = False
        controller = self.controller(adapter)
        self.assertTrue(controller.open())
        self.assertEqual(
            adapter.actions,
            [("wakeup", "powerdevil"), ("profile", "balanced")],
        )
        self.assertFalse(self.state_path.exists())

    def test_bad_state_is_not_overwritten_or_removed(self) -> None:
        self.state_path.write_text("not json", encoding="utf-8")
        self.state_path.chmod(0o600)
        adapter = FakeAdapter("balanced")
        controller = self.controller(adapter)
        self.assertTrue(controller.close())
        self.assertEqual(adapter.actions, [("dpms", "off")])
        adapter.lid_closed = False
        self.assertTrue(controller.open())
        self.assertEqual(adapter.actions[-1], ("wakeup", "powerdevil"))
        self.assertTrue(self.state_path.exists())

    def test_default_off_does_not_rewrite_state_on_poll(self) -> None:
        adapter = FakeAdapter("balanced")
        controller = self.controller(adapter)
        self.assertTrue(controller.close())
        saves_after_close = self.store.save_count
        self.assertTrue(controller.sync_auto_poweroff(lid_closed=True, now_us=1))
        self.assertEqual(self.store.save_count, saves_after_close)
        self.assertNotIn("auto_poweroff_deadline_boottime_us", self.store.load())

    def test_battery_close_arms_once_and_due_requests_poweroff(self) -> None:
        self.settings.save(60)
        adapter = FakeAdapter("balanced")
        controller = self.controller(adapter)
        self.assertTrue(controller.close())
        state = self.store.load()
        deadline = state["auto_poweroff_deadline_boottime_us"]
        self.assertEqual(state["auto_poweroff_minutes"], 60)
        self.assertEqual(state["auto_poweroff_boot_id"], "boot-one")

        saves_after_arm = self.store.save_count
        self.assertTrue(controller.sync_auto_poweroff(lid_closed=True, now_us=deadline - 1))
        self.assertEqual(self.store.save_count, saves_after_arm)
        self.assertEqual(adapter.poweroff_requests, 0)

        self.assertTrue(controller.sync_auto_poweroff(lid_closed=True, now_us=deadline))
        self.assertEqual(adapter.poweroff_requests, 1)

    def test_due_deadline_rechecks_that_lid_is_still_closed(self) -> None:
        self.settings.save(60)
        adapter = FakeAdapter("balanced")
        controller = self.controller(adapter)
        self.assertTrue(controller.close())
        deadline = self.store.load()["auto_poweroff_deadline_boottime_us"]
        adapter.lid_closed = False
        self.assertTrue(controller.sync_auto_poweroff(lid_closed=True, now_us=deadline))
        self.assertEqual(adapter.poweroff_requests, 0)

    def test_due_deadline_rechecks_external_power(self) -> None:
        self.settings.save(60)
        adapter = FakeAdapter("balanced")
        controller = self.controller(adapter)
        self.assertTrue(controller.close())
        deadline = self.store.load()["auto_poweroff_deadline_boottime_us"]
        adapter.external_power = True
        self.assertTrue(controller.sync_auto_poweroff(lid_closed=True, now_us=deadline))
        self.assertEqual(adapter.poweroff_requests, 0)

    def test_due_deadline_rechecks_disabled_setting(self) -> None:
        self.settings.save(60)
        adapter = FakeAdapter("balanced")
        controller = self.controller(adapter)
        self.assertTrue(controller.close())
        deadline = self.store.load()["auto_poweroff_deadline_boottime_us"]
        self.settings.save(0)
        self.assertTrue(controller.sync_auto_poweroff(lid_closed=True, now_us=deadline))
        self.assertEqual(adapter.poweroff_requests, 0)

    def test_same_boot_controller_restart_keeps_original_deadline(self) -> None:
        self.settings.save(60)
        adapter = FakeAdapter("balanced")
        controller = self.controller(adapter)
        self.assertTrue(controller.close())
        original = self.store.load()["auto_poweroff_deadline_boottime_us"]
        restarted = self.controller(adapter)
        self.assertTrue(restarted.sync_auto_poweroff(lid_closed=True, now_us=original - 1))
        self.assertEqual(
            self.store.load()["auto_poweroff_deadline_boottime_us"], original
        )
        self.assertEqual(adapter.poweroff_requests, 0)

    def test_open_clears_deadline_before_display_recovery(self) -> None:
        self.settings.save(60)
        adapter = FakeAdapter("balanced")
        controller = self.controller(adapter)
        self.assertTrue(controller.close())
        adapter.lid_closed = False
        adapter.fail_wakeup = True
        self.assertFalse(controller.open())
        self.assertNotIn("auto_poweroff_deadline_boottime_us", self.store.load())

    def test_external_power_cancels_and_unplug_rearms(self) -> None:
        self.settings.save(60)
        adapter = FakeAdapter("balanced")
        adapter.external_power = True
        controller = self.controller(adapter)
        self.assertTrue(controller.close())
        self.assertNotIn("auto_poweroff_deadline_boottime_us", self.store.load())

        adapter.external_power = False
        self.assertTrue(controller.sync_auto_poweroff(lid_closed=True, now_us=100))
        self.assertIn("auto_poweroff_deadline_boottime_us", self.store.load())
        adapter.external_power = True
        self.assertTrue(controller.sync_auto_poweroff(lid_closed=True, now_us=101))
        self.assertNotIn("auto_poweroff_deadline_boottime_us", self.store.load())

    def test_new_boot_rearms_instead_of_using_old_deadline(self) -> None:
        self.settings.save(60)
        adapter = FakeAdapter("balanced")
        controller = self.controller(adapter)
        self.assertTrue(controller.close())
        old_deadline = self.store.load()["auto_poweroff_deadline_boottime_us"]
        adapter.boot_id = "boot-two"
        self.assertTrue(
            controller.sync_auto_poweroff(lid_closed=True, now_us=old_deadline + 1)
        )
        state = self.store.load()
        self.assertEqual(state["auto_poweroff_boot_id"], "boot-two")
        self.assertGreater(state["auto_poweroff_deadline_boottime_us"], old_deadline)
        self.assertEqual(adapter.poweroff_requests, 0)

    def test_setting_file_is_private_and_rejects_unsupported_value(self) -> None:
        self.settings.save(60)
        self.assertEqual(self.settings.load(), 60)
        self.assertEqual(self.setting_path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            self.settings.save(30)

    def test_source_has_no_system_sleep_executables(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("/usr/bin/systemctl", source)
        self.assertNotIn("/usr/bin/rtcwake", source)
        self.assertNotIn("/usr/bin/loginctl", source)
        self.assertNotIn("ScheduleShutdown", source)
        self.assertNotIn("CancelScheduledShutdown", source)
        self.assertIn('["/usr/bin/kscreen-doctor", "--dpms", mode]', source)
        self.assertIn('"wakeup"', source)
        self.assertIn('"refreshStatus"', source)
        self.assertIn('POWERDEVIL_SERVICE = "org.kde.Solid.PowerManagement"', source)
        self.assertIn('states.get("DSI-2") == "off"', source)
        self.assertIn('states.get("DSI-1", "off") == "off"', source)

    def test_dpms_verification_accepts_physically_absent_upper_only_on_close(self) -> None:
        matches = MODULE.LiveAdapter.dpms_state_matches
        self.assertTrue(matches("off", {"DSI-2": "off"}))
        self.assertTrue(matches("off", {"DSI-1": "off", "DSI-2": "off"}))
        self.assertFalse(matches("off", {"DSI-1": "on", "DSI-2": "off"}))
        self.assertFalse(matches("off", {"DSI-1": "off"}))

    def test_dpms_verification_requires_both_panels_on_open(self) -> None:
        matches = MODULE.LiveAdapter.dpms_state_matches
        self.assertTrue(matches("on", {"DSI-1": "on", "DSI-2": "on"}))
        self.assertFalse(matches("on", {"DSI-2": "on"}))
        self.assertFalse(matches("on", {"DSI-1": "on", "DSI-2": "off"}))

    def test_user_service_has_a_wayland_environment(self) -> None:
        service = (
            ROOT / "components/light-standby/pocketds-light-standby.service"
        ).read_text(encoding="utf-8")
        self.assertIn("After=plasma-powerdevil.service plasma-kwin_wayland.service", service)
        self.assertIn("Environment=QT_QPA_PLATFORM=wayland", service)
        self.assertIn("Environment=QT_ACCESSIBILITY=0", service)
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('environment["QT_LINUX_ACCESSIBILITY_ALWAYS_ON"] = "0"', source)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Offline regression tests: no real evdev, D-Bus, DPMS or sleep operation."""
import ast
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'components/gamepad-activity/pocketds-gamepad-activity.py'
SPEC = importlib.util.spec_from_file_location('gamepad_activity_candidate', SOURCE)
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


def axis_fixture():
    return {0: (0, -1000, 1000, 20), 1: (0, -1000, 1000, 20),
            2: (0, 0, 1000, 10), 16: (0, -1, 1, 0)}


def fake_live():
    live = app.Live.__new__(app.Live)
    live.GLib = SimpleNamespace(IO_IN=1, IO_HUP=16, IO_ERR=8,
                                source_remove=Mock(), io_add_watch=Mock(return_value=99))
    live.device = Mock()
    live.device.path = '/dev/input/event7'
    live.device.fd = 42
    live.device.info = SimpleNamespace(bustype=3, vendor=0x045e, product=0x0b00)
    live.device.active_keys.return_value = []
    live.device.absinfo.side_effect = lambda code: (
        SimpleNamespace(value=0, min=-1000, max=1000, flat=20) if code in app.STICKS
        else SimpleNamespace(value=0, min=0, max=1000, flat=10) if code in app.TRIGGERS
        else SimpleNamespace(value=0, min=-1, max=1, flat=0))
    live.InputDevice = Mock(return_value=live.device)
    live.controls = app.Controls(axis_fixture())
    live.watch = None
    live.session_path = '/org/freedesktop/login1/session/test'
    live.locking = live.sleeping = False
    live.activity_proxy = None
    live.last_gap = 0
    live.last_summary = 0
    live.bridge = app.Bridge(live)
    live.system = Mock()
    live.session = Mock(spec=['get_object', 'add_signal_receiver'])
    live.screen = Mock()
    live.screen.GetActive.return_value = False
    live.bus_proxy = Mock()
    live.bus_proxy.GetNameOwner.return_value = ':1.test'
    live.bus_proxy.GetConnectionUnixProcessID.return_value = 1444
    live.session.get_object.side_effect = lambda service, path, **kwargs: (
        live.bus_proxy if service == 'org.freedesktop.DBus' else live.screen)
    live.properties = {'LidClosed': False, 'PreparingForSleep': False,
                       'Active': True, 'LockedHint': False, 'ProfilePath': app.PROFILE,
                       'Type': 'wayland'}
    live.property = Mock(side_effect=lambda _bus, _service, _path, _interface, name:
                         live.properties[name])
    return live


def gate_text(path, *args, **kwargs):
    value = str(path)
    if value == '/proc/1444/comm':
        return 'kwin_wayland\n'
    if value in [f'/sys/class/backlight/{name}/bl_power' for name in app.BACKLIGHTS]:
        return '0\n'
    raise OSError('unexpected or unavailable fake path')


class ControlsTests(unittest.TestCase):
    def test_initial_pressed_buttons_and_deflected_axes_are_not_activity(self):
        controls = app.Controls({0: (700, -1000, 1000, 20)}, [304])
        self.assertFalse(controls.held)
        self.assertFalse(controls.feed(1, 304, 1))
        self.assertFalse(controls.feed(3, 0, 700))
        self.assertFalse(controls.held)

    def test_initial_deflection_followed_by_tiny_noise_does_not_establish_hold(self):
        controls = app.Controls({0: (700, -1000, 1000, 20)})
        for value in (701, 700, 698, 705):
            self.assertFalse(controls.feed(3, 0, value))
            self.assertFalse(controls.held)

    def test_neutral_stick_and_trigger_drift_is_ignored(self):
        controls = app.Controls(axis_fixture())
        for code, values in ((0, [1, 80, -90, 179, 180, 0]),
                             (2, [1, 20, 79, 80, 0])):
            for value in values:
                with self.subTest(code=code, value=value):
                    self.assertFalse(controls.feed(3, code, value))
                    self.assertFalse(controls.held)

    def test_deadzone_is_at_least_kernel_flat_and_boundary_is_not_active(self):
        controls = app.Controls({0: (0, -1000, 1000, 400), 2: (0, 0, 1000, 200)})
        for code, threshold in ((0, 400), (2, 200)):
            self.assertFalse(controls.feed(3, code, threshold))
            self.assertNotIn((3, code), controls.held)
            self.assertTrue(controls.feed(3, code, threshold + 1))
            self.assertIn((3, code), controls.held)

    def test_real_stick_transition_establishes_hold_tiny_noise_preserves_it(self):
        controls = app.Controls(axis_fixture())
        self.assertTrue(controls.feed(3, 0, 500))
        self.assertIn((3, 0), controls.held)
        self.assertFalse(controls.feed(3, 0, 501))
        self.assertIn((3, 0), controls.held)
        self.assertTrue(controls.feed(3, 0, 600))
        self.assertTrue(controls.feed(3, 0, 0))
        self.assertNotIn((3, 0), controls.held)

    def test_initial_deflection_can_establish_hold_after_significant_new_motion(self):
        controls = app.Controls({0: (500, -1000, 1000, 20)})
        self.assertTrue(controls.feed(3, 0, 800))
        self.assertIn((3, 0), controls.held)

    def test_button_edges_are_distinct_from_repeats_and_duplicate_reports(self):
        controls = app.Controls(axis_fixture())
        self.assertTrue(controls.feed(1, 304, 1))
        self.assertIn((1, 304), controls.held)
        self.assertFalse(controls.feed(1, 304, 1))
        self.assertFalse(controls.feed(1, 304, 2))
        self.assertTrue(controls.feed(1, 304, 0))
        self.assertNotIn((1, 304), controls.held)
        self.assertFalse(controls.feed(1, 304, 0))

    def test_only_explicit_gamepad_button_ranges_are_accepted(self):
        for code in (304, 318, 544, 547, 704, 743):
            with self.subTest(code=code):
                self.assertTrue(app.Controls({}).feed(1, code, 1))
        for code in (0, 30, 116, 303, 319, 543, 548, 703, 744):
            with self.subTest(code=code):
                self.assertFalse(app.Controls({}).feed(1, code, 1))

    def test_hat_press_direction_change_and_release(self):
        controls = app.Controls(axis_fixture())
        for value, held in ((1, True), (-1, True), (0, False)):
            self.assertTrue(controls.feed(3, 16, value))
            self.assertEqual((3, 16) in controls.held, held)
        self.assertFalse(controls.feed(3, 16, 2))

    def test_out_of_range_unsupported_and_non_control_events_do_not_count(self):
        controls = app.Controls(axis_fixture())
        original = controls.axes.copy()
        for event in ((3, 0, 1001), (3, 0, -1001), (3, 99, 500),
                      (4, 4, 1), (21, 0, 100), (0, 0, 0), (1, 304, -1)):
            self.assertFalse(controls.feed(*event))
        self.assertEqual(controls.axes, original)
        self.assertFalse(controls.held)

    def test_syn_dropped_clears_hold_and_ignores_resync_events(self):
        controls = app.Controls(axis_fixture())
        controls.feed(1, 304, 1)
        self.assertFalse(controls.feed(0, 3, 0))
        self.assertTrue(controls.syncing)
        self.assertFalse(controls.held)
        self.assertFalse(controls.feed(1, 305, 1))
        self.assertFalse(controls.feed(3, 0, 900))
        self.assertFalse(controls.feed(0, 0, 0))
        self.assertTrue(controls.syncing)  # Live must resnapshot, not replay.
        self.assertFalse(controls.held)

    def test_clear_intent_does_not_rearm_from_static_or_tiny_axis_noise(self):
        controls = app.Controls(axis_fixture())
        controls.feed(1, 304, 1)
        controls.feed(3, 0, 500)
        controls.clear_intent()
        self.assertFalse(controls.feed(1, 304, 1))
        self.assertFalse(controls.feed(3, 0, 501))
        self.assertFalse(controls.held)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.adapter = Mock()
        self.adapter.allowed.return_value = True
        self.bridge = app.Bridge(self.adapter)

    def test_startup_and_ticks_without_input_do_not_query_or_notify(self):
        for now in (0, 1, 5, 300, 1000):
            self.assertFalse(self.bridge.tick(now))
        self.adapter.allowed.assert_not_called()
        self.adapter.notify_activity.assert_not_called()

    def test_only_fresh_meaningful_edges_create_pending_activity(self):
        for stamp, meaningful in ((98.99, True), (100.01, True), (100, False)):
            self.bridge.event(100, stamp, meaningful)
            self.assertIsNone(self.bridge.pending_at)
        for stamp in (99, 100):
            self.bridge.event(100, stamp, True)
            self.assertEqual(self.bridge.pending_at, 100)

    def test_success_is_throttled_with_coalesced_new_edges(self):
        self.bridge.event(10, 10, True)
        self.assertTrue(self.bridge.tick(10))
        self.bridge.event(10.1, 10.1, True)
        self.assertFalse(self.bridge.tick(11.999))
        self.assertTrue(self.bridge.tick(12))
        self.assertEqual(self.bridge.sent, 2)
        self.assertEqual(self.adapter.allowed.call_count, 2)
        self.assertEqual(self.adapter.notify_activity.call_count, 2)

    def test_pending_edge_expires_without_becoming_periodic_activity(self):
        self.bridge.event(10, 10, True)
        self.assertFalse(self.bridge.tick(13.001))
        self.assertIsNone(self.bridge.pending_at)
        self.adapter.notify_activity.assert_not_called()

    def test_refusal_clears_intent_and_throttles_further_checks(self):
        self.adapter.allowed.return_value = False
        self.bridge.event(10, 10, True)
        self.assertFalse(self.bridge.tick(10, held=True))
        self.assertIsNone(self.bridge.pending_at)
        self.adapter.clear_intent.assert_called_once()
        self.bridge.event(10.5, 10.5, True)
        self.assertFalse(self.bridge.tick(10.5, held=True))
        self.assertEqual(self.adapter.allowed.call_count, 1)
        self.assertFalse(self.bridge.tick(12, held=True))
        self.assertEqual(self.adapter.allowed.call_count, 2)
        self.assertEqual(self.bridge.sent, 0)
        self.adapter.notify_activity.assert_not_called()

    def test_notify_exception_never_counts_success_and_attempts_remain_throttled(self):
        self.adapter.notify_activity.side_effect = RuntimeError('fake bus unavailable')
        self.bridge.event(10, 10, True)
        with self.assertRaises(RuntimeError):
            self.bridge.tick(10)
        self.assertEqual(self.bridge.sent, 0)
        self.assertEqual(self.bridge.last_sent, float('-inf'))
        self.bridge.event(10.5, 10.5, True)
        self.assertFalse(self.bridge.tick(10.5))
        self.assertEqual(self.adapter.notify_activity.call_count, 1)

    def test_allowed_exception_does_not_enable_unbounded_retries(self):
        self.adapter.allowed.side_effect = RuntimeError('fake gate unavailable')
        self.bridge.event(10, 10, True)
        with self.assertRaises(RuntimeError):
            self.bridge.tick(10)
        self.bridge.event(10.5, 10.5, True)
        self.assertFalse(self.bridge.tick(10.5))
        self.assertEqual(self.adapter.allowed.call_count, 1)
        self.adapter.notify_activity.assert_not_called()

    def test_revalidated_hold_counts_but_stops_immediately_when_released(self):
        self.assertTrue(self.bridge.tick(10, held=True))
        self.assertFalse(self.bridge.tick(11, held=True))
        self.assertTrue(self.bridge.tick(12, held=True))
        self.assertFalse(self.bridge.tick(14, held=False))
        self.assertFalse(self.bridge.tick(400, held=False))
        self.assertEqual(self.bridge.sent, 2)


class LiveGateTests(unittest.TestCase):
    def setUp(self):
        self.live = fake_live()
        self.paths = patch.object(app.Path, 'read_text', gate_text)
        self.paths.start()
        self.addCleanup(self.paths.stop)

    def test_open_active_unlocked_dual_lit_managed_profile_is_allowed(self):
        self.assertTrue(self.live.allowed())
        self.live.bus_proxy.GetConnectionUnixProcessID.assert_called_once_with(
            ':1.test', dbus_interface='org.freedesktop.DBus', timeout=1)
        self.live.session.get_object.assert_any_call(':1.test', '/ScreenSaver', introspect=False)
        self.live.screen.SimulateUserActivity.assert_not_called()

    def test_closed_lid_preparing_sleep_inactive_session_and_locked_hint_refuse(self):
        for key, bad in (('LidClosed', True), ('PreparingForSleep', True),
                         ('Active', False), ('LockedHint', True),
                         ('ProfilePath', '/usr/share/inputplumber/profiles/desktop.yaml')):
            with self.subTest(key=key):
                live = fake_live()
                live.properties[key] = bad
                self.assertFalse(live.allowed())
                live.screen.SimulateUserActivity.assert_not_called()

    def test_missing_session_locking_and_sleep_signal_latches_refuse(self):
        for field, value in (('session_path', None), ('locking', True), ('sleeping', True)):
            with self.subTest(field=field):
                setattr(self.live, field, value)
                self.assertFalse(self.live.allowed())
                self.live = fake_live()

    def test_screensaver_locked_or_gate_exception_refuses(self):
        self.live.screen.GetActive.return_value = True
        self.assertFalse(self.live.allowed())
        self.live.screen.GetActive.side_effect = RuntimeError('fake bus failure')
        self.assertFalse(self.live.allowed())
        self.live.property.side_effect = OSError('fake logind missing')
        self.assertFalse(self.live.allowed())

    def test_wrong_screensaver_owner_or_missing_comm_refuses(self):
        for replacement in ('powerdevil\n', 'kwin_x11\n', ''):
            with self.subTest(replacement=replacement):
                with patch.object(app.Path, 'read_text', lambda path, *a, **k:
                                  replacement if str(path).startswith('/proc/') else gate_text(path)):
                    self.assertFalse(self.live.allowed())
        with patch.object(app.Path, 'read_text', side_effect=FileNotFoundError):
            self.assertFalse(self.live.allowed())

    def test_non_unique_or_missing_bus_owner_refuses_without_activity_proxy(self):
        for value in ('', 'org.freedesktop.ScreenSaver', 'kwin_wayland'):
            with self.subTest(value=value):
                self.live.bus_proxy.GetNameOwner.return_value = value
                self.assertFalse(self.live.allowed())
                self.assertIsNone(self.live.activity_proxy)
        self.live.bus_proxy.GetNameOwner.side_effect = RuntimeError('fake vanished owner')
        self.assertFalse(self.live.allowed())
        self.assertIsNone(self.live.activity_proxy)
        self.live.screen.SimulateUserActivity.assert_not_called()

    def test_owner_pid_lookup_failure_refuses_and_discards_prior_verified_proxy(self):
        self.assertTrue(self.live.allowed())
        self.assertIs(self.live.activity_proxy, self.live.screen)
        self.live.bus_proxy.GetConnectionUnixProcessID.side_effect = RuntimeError('fake owner vanished')
        self.assertFalse(self.live.allowed())
        self.assertIsNone(self.live.activity_proxy)
        with self.assertRaises(RuntimeError):
            self.live.notify_activity()
        self.live.screen.SimulateUserActivity.assert_not_called()

    def test_each_backlight_off_invalid_or_missing_refuses(self):
        for name in app.BACKLIGHTS:
            for bad in ('4\n', '1\n', '', 'unknown\n'):
                with self.subTest(name=name, bad=bad):
                    with patch.object(app.Path, 'read_text', lambda path, *a, **k:
                                      bad if str(path) == f'/sys/class/backlight/{name}/bl_power'
                                      else gate_text(path)):
                        self.assertFalse(self.live.allowed())

    def test_slow_gate_is_rejected_before_sending(self):
        with patch.object(app.time, 'monotonic', side_effect=[10, 11]):
            self.assertFalse(self.live.allowed())
        self.live.screen.SimulateUserActivity.assert_not_called()

    def test_lid_sleep_or_backlight_change_during_identity_checks_refuses(self):
        for changed in ('LidClosed', 'PreparingForSleep'):
            with self.subTest(changed=changed):
                live = fake_live()
                counts = {}
                def property_read(_bus, _service, _path, _interface, name):
                    counts[name] = counts.get(name, 0) + 1
                    return True if name == changed and counts[name] > 1 else live.properties[name]
                live.property.side_effect = property_read
                self.assertFalse(live.allowed())
        counts = {}
        def backlight_read(path, *args, **kwargs):
            name = str(path)
            counts[name] = counts.get(name, 0) + 1
            if name.endswith('/bl_power') and counts[name] > 1:
                return '4\n'
            return gate_text(path)
        with patch.object(app.Path, 'read_text', backlight_read):
            self.assertFalse(self.live.allowed())

    def test_only_the_activity_method_is_used_with_bounded_timeout(self):
        self.assertTrue(self.live.allowed())
        self.live.screen.reset_mock()
        self.live.notify_activity()
        self.live.screen.SimulateUserActivity.assert_called_once_with(
            dbus_interface='org.freedesktop.ScreenSaver', timeout=1)
        self.assertEqual([call[0] for call in self.live.screen.method_calls], ['SimulateUserActivity'])

    def test_notification_uses_verified_unique_proxy_once_without_retargeting_bus_name(self):
        self.assertTrue(self.live.allowed())
        replacement = Mock()
        self.live.bus_proxy.GetNameOwner.return_value = ':1.replacement'
        self.live.session.get_object.side_effect = lambda *a, **k: replacement
        lookups_before = self.live.session.get_object.call_count
        self.live.notify_activity()
        self.assertEqual(self.live.session.get_object.call_count, lookups_before)
        self.live.screen.SimulateUserActivity.assert_called_once()
        replacement.SimulateUserActivity.assert_not_called()
        self.assertIsNone(self.live.activity_proxy)
        with self.assertRaises(RuntimeError):
            self.live.notify_activity()
        self.assertEqual(self.live.screen.SimulateUserActivity.call_count, 1)

    def test_notification_without_successful_gate_is_refused(self):
        with self.assertRaises(RuntimeError):
            self.live.notify_activity()
        self.live.screen.SimulateUserActivity.assert_not_called()

    def test_notification_exception_consumes_verified_proxy_instead_of_retrying_it(self):
        self.assertTrue(self.live.allowed())
        self.live.screen.SimulateUserActivity.side_effect = RuntimeError('fake bus disappeared')
        with self.assertRaises(RuntimeError):
            self.live.notify_activity()
        self.assertIsNone(self.live.activity_proxy)
        with self.assertRaises(RuntimeError):
            self.live.notify_activity()
        self.assertEqual(self.live.screen.SimulateUserActivity.call_count, 1)

    def test_lock_sleep_and_unlock_callbacks_clear_pending_and_held_intent(self):
        for method, args, field, expected in (
                ('about_to_lock', (), 'locking', True),
                ('lock_changed', (True,), 'locking', True),
                ('lock_changed', (False,), 'locking', False),
                ('prepare_sleep', (True,), 'sleeping', True),
                ('prepare_sleep', (False,), 'sleeping', False)):
            with self.subTest(method=method, args=args):
                self.live.controls.feed(1, 304, 1)
                self.live.bridge.event(10, 10, True)
                getattr(self.live, method)(*args)
                self.assertEqual(getattr(self.live, field), expected)
                self.assertFalse(self.live.controls.held)
                self.assertIsNone(self.live.bridge.pending_at)
                self.live.controls = app.Controls(axis_fixture())


class LiveObservationTests(unittest.TestCase):
    def test_stale_release_then_fresh_hold_earns_activity(self):
        for kind, code, down in ((1, 304, 1), (3, 0, 700)):
            with self.subTest(kind=kind):
                live = fake_live()
                live.controls.feed(kind, code, down)
                live.tick = Mock()
                def read(value, timestamp, now):
                    live.device.read.return_value = [SimpleNamespace(
                        type=kind, code=code, value=value, timestamp=lambda: timestamp)]
                    with patch.object(app.time, 'monotonic', return_value=now):
                        self.assertTrue(live.readable(42, live.GLib.IO_IN))
                read(0, 8, 10)
                self.assertIsNone(live.bridge.pending_at)
                self.assertFalse(live.controls.held)
                read(down, 11, 11)
                self.assertEqual(live.bridge.pending_at, 11)
                live.device.active_keys.return_value = [304]
                live.device.absinfo.side_effect = None
                live.device.absinfo.return_value = SimpleNamespace(value=down)
                self.assertTrue(live.held_now())
                live.bridge.pending_at = None
                live.allowed = Mock(return_value=True)
                live.notify_activity = Mock()
                self.assertTrue(live.bridge.tick(400, held=live.held_now()))
                live.notify_activity.assert_called_once()

    def test_stale_press_updates_state_without_earning_activity(self):
        live = fake_live()
        live.tick = Mock()
        live.device.read.return_value = [SimpleNamespace(type=1, code=304, value=1,
                                                        timestamp=lambda: 8)]
        with patch.object(app.time, 'monotonic', return_value=10):
            live.readable(42, live.GLib.IO_IN)
        self.assertIn(304, live.controls.keys)
        live.device.active_keys.return_value = [304]
        self.assertFalse(live.held_now())
        self.assertIsNone(live.bridge.pending_at)

    def test_stale_overflow_still_requires_snapshot(self):
        live = fake_live()
        live.tick = Mock()
        live.device.read.return_value = [SimpleNamespace(type=0, code=3, value=0,
                                                        timestamp=lambda: 8)]
        with patch.object(app.time, 'monotonic', return_value=10):
            live.readable(42, live.GLib.IO_IN)
        self.assertTrue(live.controls.syncing)
        self.assertFalse(live.controls.held)

    def test_held_buttons_are_rechecked_from_kernel_not_cached_keys(self):
        live = fake_live()
        live.controls.feed(1, 304, 1)
        live.device.active_keys.return_value = [304]
        self.assertTrue(live.held_now())
        live.device.active_keys.return_value = []
        self.assertFalse(live.held_now())
        self.assertEqual(live.device.active_keys.call_count, 2)
        self.assertFalse(live.controls.held)

    def test_held_axes_are_rechecked_from_kernel_and_neutral_drops_intent(self):
        live = fake_live()
        live.controls.feed(3, 0, 800)
        live.device.absinfo.side_effect = None
        live.device.absinfo.return_value = SimpleNamespace(value=800)
        self.assertTrue(live.held_now())
        live.device.absinfo.return_value = SimpleNamespace(value=0)
        self.assertFalse(live.held_now())
        self.assertEqual(live.device.absinfo.call_count, 2)

    def test_snapshot_of_existing_hold_does_not_notify_or_establish_intent(self):
        live = fake_live()
        live.device.active_keys.return_value = [304]
        live.snapshot()
        self.assertIn(304, live.controls.keys)
        self.assertFalse(live.held_now())
        self.assertEqual(live.bridge.sent, 0)
        live.screen.SimulateUserActivity.assert_not_called()

    def test_syn_report_after_drop_uses_fresh_snapshot_without_counting_it(self):
        live = fake_live()
        live.controls.feed(0, 3, 0)
        live.device.active_keys.return_value = [304]
        live.device.read.return_value = [SimpleNamespace(type=0, code=0, value=0,
                                                        timestamp=lambda: 10)]
        live.tick = Mock()
        with patch.object(app.time, 'monotonic', return_value=10):
            self.assertTrue(live.readable(42, live.GLib.IO_IN))
        self.assertFalse(live.controls.syncing)
        self.assertIn(304, live.controls.keys)
        self.assertFalse(live.controls.held)
        self.assertIsNone(live.bridge.pending_at)

    def test_syn_dropped_immediately_clears_pending_even_without_report_in_batch(self):
        live = fake_live()
        live.bridge.event(10, 10, True)
        live.controls.feed(1, 304, 1)
        live.device.read.return_value = [SimpleNamespace(type=0, code=3, value=0,
                                                        timestamp=lambda: 10)]
        live.tick = Mock()
        with patch.object(app.time, 'monotonic', return_value=10):
            self.assertTrue(live.readable(42, live.GLib.IO_IN))
        self.assertTrue(live.controls.syncing)
        self.assertFalse(live.controls.held)
        self.assertIsNone(live.bridge.pending_at)
        self.assertFalse(live.held_now())

    def test_old_and_future_events_do_not_reestablish_activity(self):
        for timestamp in (1, 11):
            with self.subTest(timestamp=timestamp):
                live = fake_live()
                live.controls.feed(3, 0, 500)
                live.device.read.return_value = [SimpleNamespace(type=1, code=304, value=1,
                                                                 timestamp=lambda: timestamp)]
                live.tick = Mock()
                with patch.object(app.time, 'monotonic', return_value=10):
                    self.assertTrue(live.readable(42, live.GLib.IO_IN))
                self.assertFalse(live.controls.held)
                self.assertIsNone(live.bridge.pending_at)

    def test_device_hangup_detaches_and_clears_intent_without_activity(self):
        live = fake_live()
        device = live.device
        live.watch = 99
        live.controls.feed(1, 304, 1)
        live.bridge.event(10, 10, True)
        self.assertFalse(live.readable(42, live.GLib.IO_HUP))
        self.assertIsNone(live.device)
        self.assertIsNone(live.controls)
        self.assertIsNone(live.bridge.pending_at)
        device.close.assert_called_once()
        live.GLib.source_remove.assert_called_once_with(99)
        live.screen.SimulateUserActivity.assert_not_called()

    def test_boottime_gap_clears_pre_sleep_hold_and_pending_without_notification(self):
        live = fake_live()
        live.controls.feed(1, 304, 1)
        live.bridge.event(10, 10, True)
        live.device.active_keys.return_value = [304]
        with patch.object(app.time, 'CLOCK_BOOTTIME', 7, create=True), \
                patch.object(app.time, 'clock_gettime', return_value=110), \
                patch.object(app.time, 'monotonic', return_value=10):
            self.assertTrue(live.tick())
        self.assertFalse(live.controls.held)
        self.assertIsNone(live.bridge.pending_at)
        live.screen.SimulateUserActivity.assert_not_called()

    def test_live_catches_notification_failure_and_clears_intent(self):
        live = fake_live()
        live.allowed = Mock(return_value=True)
        live.notify_activity = Mock(side_effect=RuntimeError('fake unavailable'))
        live.controls.feed(1, 304, 1)
        live.device.active_keys.return_value = [304]
        with patch.object(app.time, 'CLOCK_BOOTTIME', 7, create=True), \
                patch.object(app.time, 'clock_gettime', return_value=10), \
                patch.object(app.time, 'monotonic', return_value=10), patch.object(app, 'emit'):
            self.assertTrue(live.tick())
        self.assertFalse(live.controls.held)
        self.assertIsNone(live.bridge.pending_at)
        self.assertEqual(live.bridge.sent, 0)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.live = fake_live()
        self.device = self.live.device
        self.live.device = self.live.controls = None
        self.manager = self.live.system.get_object.return_value
        self.manager.ListSessions.return_value = [
            ('test', 1000, 'user', 'seat0', '/org/freedesktop/login1/session/test')]
        self.names = ['/sys/class/input/event7/device/name']
        self.device_name = app.GAMEPAD_NAME
        self.virtual = True
        patches = [patch.object(app.os, 'getuid', return_value=1000),
                   patch.object(app.glob, 'glob', side_effect=lambda pattern: self.names),
                   patch.object(app.Path, 'read_text', lambda path, *a, **k: self.device_name),
                   patch.object(app.Path, 'resolve', lambda path, *a, **k: Path(
                       '/sys/devices/virtual/input/input77/name' if self.virtual
                       else '/sys/devices/platform/physical/input/input77/name')),
                   patch.object(app.fcntl, 'ioctl'), patch.object(app, 'emit')]
        results = [item.start() for item in patches]
        self.ioctl = results[-2]
        for item in patches:
            self.addCleanup(item.stop)

    def test_only_unique_virtual_named_pad_with_expected_id_is_attached(self):
        self.assertTrue(self.live.discover())
        self.live.InputDevice.assert_called_once_with('/dev/input/event7')
        self.assertIs(self.live.device, self.device)
        self.assertFalse(self.live.controls.held)
        self.assertEqual(self.live.session_path, '/org/freedesktop/login1/session/test')
        self.ioctl.assert_called_once_with(42, 0x400445a0,
                                          app.struct.pack('i', app.time.CLOCK_MONOTONIC))
        self.live.screen.SimulateUserActivity.assert_not_called()

    def test_missing_or_multiple_matching_pads_do_not_open_an_arbitrary_device(self):
        for names in ([], ['/sys/class/input/event7/device/name', '/sys/class/input/event8/device/name']):
            with self.subTest(names=names):
                self.names = names
                self.assertTrue(self.live.discover())
                self.assertIsNone(self.live.device)
                self.live.InputDevice.assert_not_called()
                self.ioctl.assert_not_called()

    def test_same_name_physical_device_and_wrong_name_are_not_observed(self):
        self.virtual = False
        self.assertTrue(self.live.discover())
        self.live.InputDevice.assert_not_called()
        self.virtual = True
        self.device_name = 'Another controller'
        self.assertTrue(self.live.discover())
        self.live.InputDevice.assert_not_called()

    def test_bus_vendor_or_product_mismatch_closes_device_without_watch(self):
        for field in ('bustype', 'vendor', 'product'):
            with self.subTest(field=field):
                original = getattr(self.device.info, field)
                setattr(self.device.info, field, original + 1)
                self.assertTrue(self.live.discover())
                self.assertIsNone(self.live.device)
                self.device.close.assert_called_once()
                self.live.GLib.io_add_watch.assert_not_called()
                self.ioctl.assert_not_called()
                setattr(self.device.info, field, original)
                self.device.close.reset_mock()

    def test_other_uid_wrong_seat_non_wayland_or_ambiguous_sessions_have_no_authority(self):
        for sessions, kind in (
                ([('x', 1001, 'other', 'seat0', '/session/x')], 'wayland'),
                ([('x', 1000, 'user', 'seat1', '/session/x')], 'wayland'),
                ([('x', 1000, 'user', 'seat0', '/session/x')], 'x11'),
                ([('x', 1000, 'user', 'seat0', '/session/x'),
                  ('y', 1000, 'user', 'seat0', '/session/y')], 'wayland')):
            with self.subTest(sessions=sessions, kind=kind):
                self.manager.ListSessions.return_value = sessions
                self.live.properties['Type'] = kind
                self.assertTrue(self.live.discover())
                self.assertIsNone(self.live.session_path)
                self.assertFalse(self.live.allowed())
                self.live.screen.SimulateUserActivity.assert_not_called()

    def test_clock_ioctl_failure_closes_new_fd_and_installs_no_watch(self):
        self.ioctl.side_effect = OSError('fake unsupported clock selection')
        self.assertTrue(self.live.discover())
        self.assertIsNone(self.live.device)
        self.device.close.assert_called_once()
        self.live.GLib.io_add_watch.assert_not_called()

    def test_snapshot_failure_closes_new_fd_and_installs_no_watch(self):
        self.device.absinfo.side_effect = OSError('fake device disappeared')
        self.assertTrue(self.live.discover())
        self.assertIsNone(self.live.device)
        self.device.close.assert_called_once()
        self.live.GLib.io_add_watch.assert_not_called()


class ScopeTests(unittest.TestCase):
    def test_no_input_grab_synthesis_dpms_inhibition_or_power_mutation_calls(self):
        tree = ast.parse(SOURCE.read_text())
        calls = {node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        forbidden = {'grab', 'ungrab', 'write', 'write_event', 'syn', 'UInput',
                     'wakeup', 'refreshStatus', 'Inhibit', 'UnInhibit', 'Lock',
                     'SetActive', 'Suspend', 'Hibernate', 'SetBrightness', 'SetProfile',
                     'switchMode', 'requestDpmsState', 'system', 'Popen'}
        self.assertFalse(calls & forbidden, calls & forbidden)
        modules = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                   for alias in node.names}
        self.assertNotIn('subprocess', modules)

    def test_constructor_installs_observers_but_never_sends_activity(self):
        dbus = ModuleType('dbus')
        dbus.SystemBus = Mock(return_value=Mock())
        dbus.SessionBus = Mock(return_value=Mock())
        dbus_mainloop = ModuleType('dbus.mainloop')
        dbus_glib = ModuleType('dbus.mainloop.glib')
        dbus_glib.DBusGMainLoop = Mock()
        repository = ModuleType('gi.repository')
        repository.GLib = Mock()
        evdev = ModuleType('evdev')
        evdev.InputDevice = Mock()
        modules = {'dbus': dbus, 'dbus.mainloop': dbus_mainloop,
                   'dbus.mainloop.glib': dbus_glib, 'gi': ModuleType('gi'),
                   'gi.repository': repository, 'evdev': evdev}
        with patch.dict(sys.modules, modules), \
                patch.object(app.time, 'CLOCK_BOOTTIME', 7, create=True), \
                patch.object(app.time, 'clock_gettime', return_value=10), \
                patch.object(app.time, 'monotonic', return_value=10), \
                patch.object(app.Live, 'discover', return_value=True), \
                patch.object(app.Live, 'notify_activity') as notify:
            live = app.Live()
        notify.assert_not_called()
        self.assertEqual(live.bridge.sent, 0)
        self.assertIsNone(live.bridge.pending_at)
        signal_names = {call.kwargs['signal_name'] for call in
                        live.session.add_signal_receiver.call_args_list}
        self.assertEqual(signal_names, {'AboutToLock', 'ActiveChanged'})
        self.assertEqual(live.system.add_signal_receiver.call_args.kwargs['signal_name'],
                         'PrepareForSleep')

    def test_user_service_does_not_require_privilege_or_change_sleep_policy(self):
        service = SOURCE.with_name('pocketds-gamepad-activity.service').read_text()
        self.assertIn('PartOf=graphical-session.target', service)
        self.assertIn('NoNewPrivileges=yes', service)
        self.assertNotIn('ExecStartPre=', service)
        self.assertNotIn('ExecStartPost=', service)
        self.assertNotIn('sudo', service)
        self.assertNotIn('CAP_SYS_ADMIN', service)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Behavior contract for the raw DSI-2 Type-B touch bridge."""

import errno
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "components" / "touchpad"))

from touchpad_raw import (  # noqa: E402
    ContextMenuTouchGuard,
    ExclusiveTouchGuard,
    PointerButtonLatch,
    RawTouchAction,
    RawTouchRouter,
    TypeBTouchFrame,
    lower_touch_to_screen,
)


def kinds(actions):
    return [action.kind for action in actions]


class RawTouchFrameTests(unittest.TestCase):
    def setUp(self):
        self.frame = TypeBTouchFrame()
        self.region = (0, 80, 1024, 600)

    def begin(self, slot, contact, raw_x, raw_y):
        self.frame.set_slot(slot)
        self.frame.set_tracking_id(contact)
        self.frame.set_x(raw_x)
        self.frame.set_y(raw_y)

    def test_rotated_right_sensor_coordinates_match_landscape_dsi2(self):
        self.assertEqual(lower_touch_to_screen(0, 0), (1023.0, 0.0))
        self.assertEqual(lower_touch_to_screen(767, 1023), (0.0, 767.0))

    def test_contact_inside_track_is_forwarded_once_per_frame(self):
        self.begin(0, 40, 300, 700)
        actions = self.frame.sync(self.region)
        self.assertEqual(kinds(actions), ["begin"])
        self.frame.set_x(320)
        actions = self.frame.sync(self.region)
        self.assertEqual(kinds(actions), ["update"])
        self.frame.set_tracking_id(-1)
        actions = self.frame.sync(self.region)
        self.assertEqual(kinds(actions), ["end"])

    def test_contact_starting_outside_track_stays_ignored(self):
        self.begin(0, 40, 20, 700)
        self.assertEqual(self.frame.sync(self.region), [])
        self.frame.set_x(300)
        self.assertEqual(self.frame.sync(self.region), [])
        self.frame.set_tracking_id(-1)
        self.assertEqual(self.frame.sync(self.region), [])

    def test_three_slots_are_preserved_as_three_contacts(self):
        self.begin(0, 40, 200, 700)
        self.begin(1, 41, 300, 600)
        self.begin(2, 42, 400, 500)
        actions = self.frame.sync(self.region)
        self.assertEqual(kinds(actions), ["begin", "begin", "begin"])
        self.assertEqual([action.contact for action in actions], [40, 41, 42])

    def test_reused_slot_accepts_same_position_without_repeated_axes(self):
        self.begin(0, 40, 300, 700)
        self.frame.sync(self.region)
        self.frame.set_tracking_id(-1)
        self.frame.sync(self.region)
        self.frame.set_tracking_id(41)
        self.assertEqual(
            self.frame.sync(self.region), [RawTouchAction("begin", 41, 323, 300)]
        )
        self.frame.set_tracking_id(-1)
        self.assertEqual(kinds(self.frame.sync(self.region)), ["end"])

    def test_reused_slot_accepts_only_the_axis_that_changed(self):
        for axis, value, expected in (
            ("x", 350, (323, 350)),
            ("y", 600, (423, 300)),
        ):
            with self.subTest(axis=axis):
                self.frame = TypeBTouchFrame()
                self.begin(0, 40, 300, 700)
                self.frame.sync(self.region)
                self.frame.set_tracking_id(-1)
                self.frame.sync(self.region)
                self.frame.set_tracking_id(41)
                getattr(self.frame, "set_" + axis)(value)
                self.assertEqual(
                    self.frame.sync(self.region),
                    [RawTouchAction("begin", 41, *expected)],
                )

    def test_replacement_keeps_axes_but_not_previous_contact_ownership(self):
        self.begin(0, 40, 20, 700)
        self.assertEqual(self.frame.sync(self.region), [])
        self.frame.set_tracking_id(41)
        self.frame.set_x(300)
        self.assertEqual(
            self.frame.sync(self.region), [RawTouchAction("begin", 41, 323, 300)]
        )
        self.frame.set_tracking_id(42)
        self.assertEqual(
            self.frame.sync(self.region),
            [RawTouchAction("end", 41, 323, 300),
             RawTouchAction("begin", 42, 323, 300)],
        )

    def test_new_or_reset_stream_does_not_invent_unknown_axes(self):
        for reset_existing in (False, True):
            with self.subTest(reset_existing=reset_existing):
                self.frame = TypeBTouchFrame()
                if reset_existing:
                    self.begin(0, 40, 300, 700)
                    self.frame.sync(self.region)
                    self.frame.reset()
                self.frame.set_tracking_id(41)
                self.frame.set_x(300)
                self.assertEqual(self.frame.sync(self.region), [])
                self.frame.set_y(700)
                self.assertEqual(
                    self.frame.sync(self.region),
                    [RawTouchAction("begin", 41, 323, 300)],
                )

    def test_reused_axes_stay_with_their_own_slot(self):
        self.begin(0, 40, 300, 700)
        self.begin(1, 41, 400, 500)
        self.frame.sync(self.region)
        self.frame.set_slot(0)
        self.frame.set_tracking_id(42)
        self.frame.set_y(600)
        self.assertEqual(
            self.frame.sync(self.region),
            [RawTouchAction("end", 40, 323, 300),
             RawTouchAction("begin", 42, 423, 300)],
        )

    def test_reset_cancels_only_an_active_forwarded_gesture(self):
        self.assertEqual(self.frame.reset(), [])
        self.begin(0, 40, 300, 700)
        self.frame.sync(self.region)
        self.assertEqual(kinds(self.frame.reset()), ["cancel"])


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeInputDevice:
    def __init__(self, fail_grab=False, fail_ungrab=False):
        self.events = []
        self.fail_grab = fail_grab
        self.fail_ungrab = fail_ungrab

    def grab(self):
        self.events.append("grab")
        if self.fail_grab:
            raise OSError(errno.EBUSY, "busy")

    def ungrab(self):
        self.events.append("ungrab")
        if self.fail_ungrab:
            raise OSError(errno.EBUSY, "busy")


class ContextMenuTouchGuardTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.device = FakeInputDevice()
        self.guard = ContextMenuTouchGuard(clock=self.clock)

    def test_guard_arms_renews_and_releases_once(self):
        self.assertTrue(self.guard.arm(self.device, timeout_s=12.0))
        self.assertEqual(self.device.events, ["grab"])
        self.clock.now = 5.0
        self.assertTrue(self.guard.arm(self.device, timeout_s=12.0))
        self.assertEqual(self.device.events, ["grab"])
        self.assertEqual(self.guard.deadline, 17.0)
        self.guard.release()
        self.guard.release()
        self.assertEqual(self.device.events, ["grab", "ungrab"])

    def test_guard_expires_on_monotonic_deadline(self):
        self.guard.arm(self.device, timeout_s=12.0)
        self.clock.now = 11.9
        self.assertFalse(self.guard.expire())
        self.clock.now = 12.0
        self.assertTrue(self.guard.expire())
        self.assertFalse(self.guard.active)
        self.assertEqual(self.device.events, ["grab", "ungrab"])

    def test_grab_failure_fails_closed(self):
        device = FakeInputDevice(fail_grab=True)
        self.assertFalse(self.guard.arm(device))
        self.assertFalse(self.guard.active)
        self.assertEqual(device.events, ["grab"])

    def test_expiry_waits_for_active_touch_sequence(self):
        self.guard.arm(self.device, timeout_s=12.0)
        self.clock.now = 12.0
        self.assertFalse(self.guard.expire(active_contacts=True))
        self.assertTrue(self.guard.active)
        self.assertEqual(self.device.events, ["grab"])
        self.assertTrue(self.guard.expire(active_contacts=False))
        self.assertEqual(self.device.events, ["grab", "ungrab"])

    def test_unknown_ungrab_failure_remains_retryable_until_close(self):
        device = FakeInputDevice(fail_ungrab=True)
        self.guard.arm(device)
        self.assertFalse(self.guard.release())
        self.assertTrue(self.guard.active)
        device.fail_ungrab = False
        self.assertTrue(self.guard.release())
        self.assertFalse(self.guard.active)
        self.assertEqual(device.events, ["grab", "ungrab", "ungrab"])


class ExclusiveTouchGuardTests(unittest.TestCase):
    def test_acquire_is_idempotent_and_release_restores_touchscreen(self):
        device = FakeInputDevice()
        guard = ExclusiveTouchGuard()

        self.assertTrue(guard.acquire(device))
        self.assertTrue(guard.acquire(device))
        self.assertTrue(guard.active)
        self.assertEqual(device.events, ["grab"])

        self.assertTrue(guard.release())
        self.assertTrue(guard.release())
        self.assertFalse(guard.active)
        self.assertEqual(device.events, ["grab", "ungrab"])

    def test_failed_acquire_never_claims_exclusive_state(self):
        device = FakeInputDevice(fail_grab=True)
        guard = ExclusiveTouchGuard()

        self.assertFalse(guard.acquire(device))
        self.assertFalse(guard.active)
        self.assertEqual(device.events, ["grab"])

    def test_ungrab_failure_remains_retryable(self):
        device = FakeInputDevice(fail_ungrab=True)
        guard = ExclusiveTouchGuard()
        guard.acquire(device)

        self.assertFalse(guard.release())
        self.assertTrue(guard.active)
        device.fail_ungrab = False
        self.assertTrue(guard.release())
        self.assertFalse(guard.active)

    def test_closed_device_clears_guard_without_second_ioctl(self):
        device = FakeInputDevice()
        guard = ExclusiveTouchGuard()
        guard.acquire(device)

        guard.device_closed(device)

        self.assertFalse(guard.active)
        self.assertEqual(device.events, ["grab"])


class PointerButtonLatchTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.latch = PointerButtonLatch(
            lambda: self.events.append("down"),
            lambda: self.events.append("up"),
        )

    def test_second_owner_cannot_release_first_owners_hold(self):
        self.assertTrue(self.latch.acquire("raw-control"))
        self.assertTrue(self.latch.acquire("gesture"))
        self.assertEqual(self.events, ["down"])

        self.assertTrue(self.latch.relinquish("gesture"))
        self.assertTrue(self.latch.held)
        self.assertEqual(self.events, ["down"])

        self.assertTrue(self.latch.relinquish("raw-control"))
        self.assertFalse(self.latch.held)
        self.assertEqual(self.events, ["down", "up"])

    def test_duplicate_owner_transitions_are_idempotent(self):
        self.assertTrue(self.latch.acquire("raw-control"))
        self.assertFalse(self.latch.acquire("raw-control"))
        self.assertTrue(self.latch.relinquish("raw-control"))
        self.assertFalse(self.latch.relinquish("raw-control"))
        self.assertEqual(self.events, ["down", "up"])

    def test_clear_releases_once_and_cannot_leave_button_stuck(self):
        self.latch.acquire("raw-control")
        self.latch.acquire("gesture")

        self.assertTrue(self.latch.clear())
        self.assertFalse(self.latch.clear())
        self.assertFalse(self.latch.held)
        self.assertEqual(self.events, ["down", "up"])


class RawTouchRouterTests(unittest.TestCase):
    def setUp(self):
        self.router = RawTouchRouter()
        self.assertTrue(
            self.router.configure(
                819,
                614,
                [
                    ("keyboard", (650, 8, 75, 52)),
                    ("panel", (735, 8, 72, 52)),
                    ("left", (12, 545, 390, 58)),
                    ("right", (412, 545, 395, 58)),
                    ("track", (12, 80, 795, 455)),
                ],
            )
        )

    def test_physical_coordinates_scale_into_logical_dsi2_geometry(self):
        keyboard = self.router.route(RawTouchAction("begin", 1, 850, 30))
        right = self.router.route(RawTouchAction("begin", 2, 800, 720))
        track = self.router.route(RawTouchAction("begin", 3, 500, 300))

        self.assertEqual(keyboard.target, "keyboard")
        self.assertEqual(right.target, "right")
        self.assertEqual(track.target, "track")

    def test_contact_target_is_anchored_until_release(self):
        self.router.route(RawTouchAction("begin", 1, 500, 300))
        outside = self.router.route(RawTouchAction("update", 1, 1000, 20))
        released = self.router.route(RawTouchAction("end", 1, 500, 300))

        self.assertEqual(outside.target, "track")
        self.assertFalse(outside.inside)
        self.assertEqual(released.target, "track")
        self.assertTrue(released.inside)

    def test_button_release_outside_does_not_look_pressed(self):
        pressed = self.router.route(RawTouchAction("begin", 1, 850, 30))
        released = self.router.route(RawTouchAction("end", 1, 500, 300))

        self.assertTrue(pressed.inside)
        self.assertEqual(released.target, "keyboard")
        self.assertFalse(released.inside)

    def test_unknown_region_is_ignored_for_whole_contact(self):
        self.assertIsNone(
            self.router.route(RawTouchAction("begin", 1, 20, 20))
        )
        self.assertIsNone(
            self.router.route(RawTouchAction("update", 1, 500, 300))
        )
        self.assertIsNone(
            self.router.route(RawTouchAction("end", 1, 500, 300))
        )

    def test_cancel_clears_all_anchored_contacts(self):
        self.router.route(RawTouchAction("begin", 1, 500, 300))
        cancelled = self.router.route(RawTouchAction("cancel"))

        self.assertEqual(cancelled.kind, "cancel")
        self.assertEqual(self.router.contacts, {})
        self.assertIsNone(
            self.router.route(RawTouchAction("update", 1, 510, 310))
        )


class RawTouchSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = (ROOT / "components/touchpad/pocketds-touchpad.py").read_text(
            encoding="utf-8"
        )
        cls.decoder = (ROOT / "components/touchpad/touchpad_raw.py").read_text(
            encoding="utf-8"
        )
        cls.rules = (ROOT / "components/system/61-pocketds-touchscreens.rules").read_text(
            encoding="utf-8"
        )
        cls.installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")

    def test_runtime_reads_only_the_stable_lower_touchscreen_path(self):
        self.assertIn("platform-a88000.i2c-event", self.runtime)
        self.assertIn('device.name != "Goodix Capacitive TouchScreen"', self.runtime)

    def test_touchpad_mode_exclusively_grabs_and_releases_lower_screen(self):
        self.assertIn("device.grab()", self.decoder)
        self.assertIn("device.ungrab()", self.decoder)
        self.assertIn("class ExclusiveTouchGuard:", self.decoder)
        self.assertIn("self.exclusive_guard.acquire(device)", self.runtime)
        self.assertIn("self.exclusive_guard.release()", self.runtime)
        self.assertIn("and self.exclusive_guard.active", self.runtime)
        self.assertIn("FULL_TOUCH_REGION", self.runtime)
        self.assertIn("Descriptor close drops EVIOCGRAB", self.runtime)
        self.assertIn("device.close()", self.runtime)

    def test_context_menu_guard_remains_bounded_as_fallback(self):
        self.assertIn("timeout_s: float = 12.0", self.decoder)
        self.assertEqual(self.runtime.count(".context_guard.arm("), 1)
        self.assertIn("self.context_guard.expire(", self.runtime)
        self.assertIn("self.context_guard.release()", self.runtime)
        self.assertIn("active_contacts=bool(self.tracker.committed)", self.runtime)
        self.assertIn("self.context_guard.device_closed(device)", self.runtime)

    def test_raw_router_preserves_all_touchpad_controls_while_grabbed(self):
        for token in (
            "RawTouchRouter()",
            '("keyboard", self.keyboard_button)',
            '("panel", self.panel_button)',
            '("left", self.left_button)',
            '("right", self.right_button)',
            '("track", self.track)',
            'context.add_class("raw-active")',
            "def activate_raw_control(self, target):",
        ):
            self.assertIn(token, self.runtime)

    def test_left_button_hold_allows_parallel_pointer_contact(self):
        for token in (
            'self.left_button_latch.acquire("raw-control")',
            'self.left_button_latch.relinquish("raw-control")',
            'self.raw_control_target not in {None, "left"}',
            'self.left_button_latch.acquire("gesture")',
            'self.left_button_latch.relinquish("gesture")',
            "self.left_button_latch.clear()",
            "self.raw_left_chord_contacts.add(routed.contact)",
            "self.raw_left_chord_contacts.update(self.gestures.points)",
            'if action.kind != "left_click"',
        ):
            self.assertIn(token, self.runtime)
        left_begin = self.runtime[
            self.runtime.index("    def handle_raw_control(self, routed):") :
            self.runtime.index("    def raw_control_button(self, target):")
        ]
        self.assertIn('if target != "left" and self.gestures.points:', left_begin)
        self.assertIn('if target == "left":', left_begin)

    def test_exclusive_grab_precedes_visible_touchpad_publication(self):
        show = self.runtime[
            self.runtime.index("    def manual_show(self, _button=None):") :
            self.runtime.index("    def retry_manual_show(self):")
        ]
        acquire_at = show.index(
            "self.raw_touch.set_enabled(True, FULL_TOUCH_REGION)"
        )
        publish_at = show.index("publish_touchpad_visibility(True)")
        window_at = show.index("self.window.show_all()")
        self.assertLess(acquire_at, publish_at)
        self.assertLess(publish_at, window_at)

    def test_context_menu_guard_wraps_virtual_mouse_click_order(self):
        right_click = self.runtime.split(
            'elif action.kind == "right_click":', 1
        )[1].split('elif action.kind == "left_down":', 1)[0]
        self.assertLess(
            right_click.index("arm_context_menu_guard()"),
            right_click.index("self.click(ecodes.BTN_RIGHT)"),
        )
        left_click = self.runtime.split(
            'elif action.kind == "left_click":', 1
        )[1].split('elif action.kind == "right_click":', 1)[0]
        self.assertLess(
            left_click.index("self.click(ecodes.BTN_LEFT)"),
            left_click.index("release_context_menu_guard()"),
        )

    def test_lower_touchscreen_access_is_session_scoped(self):
        self.assertIn('TAG+="uaccess"', self.rules.splitlines()[1])
        self.assertNotIn('GROUP="input"', self.rules)

    def test_raw_decoder_is_installed_with_the_touchpad(self):
        self.assertIn("touchpad_raw.py", self.installer)


if __name__ == "__main__":
    unittest.main(verbosity=2)

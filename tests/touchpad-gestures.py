#!/usr/bin/env python3
"""Small behavior contract for Pocket DS touchpad gestures."""

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "components" / "touchpad"))

from touchpad_gestures import TouchpadGestureEngine  # noqa: E402


def kinds(actions):
    return [action.kind for action in actions]


class TouchpadGestureTests(unittest.TestCase):
    def setUp(self):
        self.engine = TouchpadGestureEngine(pointer_gain=1.0)

    def test_single_finger_motion_is_relative_pointer_motion(self):
        self.engine.touch_begin("a", 100, 100, 0)
        actions = self.engine.touch_update("a", 116, 92, 30)
        self.assertEqual([(a.kind, a.x, a.y) for a in actions], [("move", 16, -8)])
        self.assertEqual(self.engine.touch_end("a", 116, 92, 60), [])

    def test_short_single_finger_tap_is_left_click(self):
        self.engine.touch_begin("a", 100, 100, 0)
        actions = self.engine.touch_end("a", 104, 103, 90)
        self.assertEqual(kinds(actions), ["left_click"])

    def test_double_tap_hold_enters_and_releases_drag(self):
        self.engine.touch_begin("a", 100, 100, 0)
        self.engine.touch_end("a", 100, 100, 80)
        actions = self.engine.touch_begin("b", 104, 102, 180)
        self.assertEqual(kinds(actions), ["left_down"])
        self.assertEqual(kinds(self.engine.touch_update("b", 130, 130, 220)), ["move"])
        self.assertEqual(kinds(self.engine.touch_end("b", 130, 130, 260)), ["left_up"])

    def test_two_finger_tap_is_right_click(self):
        self.engine.touch_begin("a", 100, 100, 0)
        self.engine.touch_begin("b", 300, 100, 10)
        self.assertEqual(self.engine.touch_end("a", 102, 101, 100), [])
        self.assertEqual(kinds(self.engine.touch_end("b", 301, 100, 120)), ["right_click"])

    def test_context_menu_move_then_tap_stays_two_distinct_gestures(self):
        self.engine.touch_begin("a", 100, 100, 0)
        self.engine.touch_begin("b", 300, 100, 10)
        self.engine.touch_end("a", 100, 100, 80)
        self.assertEqual(
            kinds(self.engine.touch_end("b", 300, 100, 90)), ["right_click"]
        )

        self.engine.touch_begin("c", 120, 120, 200)
        self.assertEqual(
            kinds(self.engine.touch_update("c", 180, 160, 240)), ["move"]
        )
        self.assertEqual(self.engine.touch_end("c", 180, 160, 260), [])

        self.engine.touch_begin("d", 180, 160, 320)
        self.assertEqual(
            kinds(self.engine.touch_end("d", 182, 161, 380)), ["left_click"]
        )

    def test_two_finger_parallel_motion_scrolls(self):
        self.engine.touch_begin("a", 100, 100, 0)
        self.engine.touch_begin("b", 300, 100, 5)
        self.assertEqual(self.engine.touch_update("a", 100, 140, 20), [])
        actions = self.engine.touch_update("b", 300, 140, 21)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "scroll")
        self.assertEqual(actions[0].y, 2)

    def test_two_finger_pinch_emits_zoom_without_scroll(self):
        self.engine.touch_begin("a", 100, 100, 0)
        self.engine.touch_begin("b", 300, 100, 5)
        self.engine.touch_update("a", 80, 100, 20)
        actions = self.engine.touch_update("b", 320, 100, 21)
        self.assertEqual(kinds(actions), ["zoom"])
        self.assertGreater(actions[0].y, 0)

    def test_three_finger_motion_enters_drag_after_threshold(self):
        self.engine.touch_begin("a", 100, 100, 0)
        self.engine.touch_begin("b", 200, 100, 5)
        self.engine.touch_begin("c", 300, 100, 10)
        self.assertEqual(self.engine.touch_update("a", 108, 100, 20), [])
        self.assertEqual(self.engine.touch_update("b", 208, 100, 21), [])
        actions = self.engine.touch_update("c", 308, 100, 22)
        self.assertEqual(kinds(actions), ["left_down", "move"])
        actions = self.engine.touch_update("a", 130, 115, 30)
        self.assertEqual(kinds(actions), ["move"])
        self.assertGreater(actions[0].x, 0)
        self.assertGreater(actions[0].y, 0)

    def test_three_finger_drag_releases_when_any_finger_lifts(self):
        self.engine.touch_begin("a", 100, 100, 0)
        self.engine.touch_begin("b", 200, 100, 5)
        self.engine.touch_begin("c", 300, 100, 10)
        self.engine.touch_update("a", 130, 100, 20)
        self.engine.touch_update("b", 230, 100, 21)
        self.engine.touch_update("c", 330, 100, 22)
        actions = self.engine.touch_end("b", 230, 100, 30)
        self.assertEqual(kinds(actions), ["left_up"])
        self.assertEqual(self.engine.touch_update("a", 150, 100, 35), [])

    def test_three_finger_tap_does_not_click(self):
        self.engine.touch_begin("a", 100, 100, 0)
        self.engine.touch_begin("b", 200, 100, 5)
        self.engine.touch_begin("c", 300, 100, 10)
        self.assertEqual(self.engine.touch_end("a", 100, 100, 80), [])
        self.assertEqual(self.engine.touch_end("b", 200, 100, 90), [])
        self.assertEqual(self.engine.touch_end("c", 300, 100, 100), [])

    def test_fourth_contact_cancels_active_three_finger_drag(self):
        self.engine.touch_begin("a", 100, 100, 0)
        self.engine.touch_begin("b", 200, 100, 5)
        self.engine.touch_begin("c", 300, 100, 10)
        self.engine.touch_update("a", 130, 100, 20)
        self.engine.touch_update("b", 230, 100, 21)
        self.engine.touch_update("c", 330, 100, 22)
        actions = self.engine.touch_begin("d", 400, 100, 30)
        self.assertEqual(kinds(actions), ["left_up"])
        self.assertEqual(self.engine.touch_update("a", 150, 100, 35), [])

    def test_cancel_releases_active_three_finger_drag(self):
        self.engine.touch_begin("a", 100, 100, 0)
        self.engine.touch_begin("b", 200, 100, 5)
        self.engine.touch_begin("c", 300, 100, 10)
        self.engine.touch_update("a", 130, 100, 20)
        self.engine.touch_update("b", 230, 100, 21)
        self.engine.touch_update("c", 330, 100, 22)
        self.assertEqual(kinds(self.engine.cancel()), ["left_up"])
        self.assertEqual(self.engine.mode, "idle")

    def test_third_contact_cancels_drag_and_ignores_remainder(self):
        self.engine.touch_begin("a", 100, 100, 0)
        self.engine.touch_end("a", 100, 100, 50)
        self.assertEqual(kinds(self.engine.touch_begin("b", 100, 100, 100)), ["left_down"])
        actions = self.engine.touch_begin("c", 200, 100, 110)
        self.assertEqual(kinds(actions), ["left_up"])
        self.assertEqual(self.engine.touch_begin("d", 300, 100, 120), [])
        self.assertEqual(self.engine.touch_update("b", 150, 150, 140), [])

    def test_cancel_releases_active_drag(self):
        self.engine.touch_begin("a", 100, 100, 0)
        self.engine.touch_end("a", 100, 100, 50)
        self.engine.touch_begin("b", 100, 100, 100)
        self.assertEqual(kinds(self.engine.cancel()), ["left_up"])
        self.assertEqual(self.engine.mode, "idle")


class TouchpadSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = (ROOT / "components/touchpad/pocketds-touchpad.py").read_text(
            encoding="utf-8"
        )
        cls.service = (ROOT / "components/touchpad/pocketds-touchpad.service").read_text(
            encoding="utf-8"
        )
        cls.panel = (ROOT / "components/control-panel/pocketds-panelctl.cpp").read_text(
            encoding="utf-8"
        )
        cls.qml = (
            ROOT / "components/control-panel/plasmoid/contents/ui/main.qml"
        ).read_text(encoding="utf-8")

    def test_runtime_is_relative_uinput_and_never_steals_focus(self):
        for token in (
            "REL_X",
            "REL_Y",
            "REL_WHEEL",
            "REL_HWHEEL",
            "KEY_LEFTCTRL",
            "set_accept_focus(False)",
            'connect("touch-event", self.on_touch_event)',
            'connect("motion-notify-event", self.on_pointer_motion)',
            '"pointer-fallback"',
            "GLib.timeout_add(80, self.activate_touch_surface)",
            'set_wmclass("pocketds-touchpad"',
        ):
            self.assertIn(token, self.runtime)

    def test_panel_and_keyboard_switch_overlays_without_restart(self):
        self.assertIn('std::strcmp(argv[1], "touchpad")', self.panel)
        self.assertIn('hide_overlay("pocketds-keyboard.service")', self.panel)
        self.assertIn('hide_overlay("pocketds-touchpad.service")', self.panel)
        self.assertIn("show_overlay", self.panel)
        self.assertIn('"--signal=HUP"', self.panel)
        self.assertNotIn('"restart", "pocketds-touchpad.service"', self.panel)
        self.assertIn('root.exec("touchpad")', self.qml)
        self.assertIn('text: "触摸板"', self.qml)

    def test_touchpad_surface_is_self_explanatory_without_fine_print(self):
        for token in (
            'self.button("左键", self.click_left, "click-button")',
            'self.button("右键", self.click_right, "click-button")',
            "def draw_track_surface",
            "def click_left",
            "def click_right",
            "if self.raw_touch.arm_context_menu_guard():",
        ):
            self.assertIn(token, self.runtime)
        for fine_print in (
            "下屏不抢焦点",
            "在这里滑动",
            "单指移动与轻触",
        ):
            self.assertNotIn(fine_print, self.runtime)

    def test_service_is_preloaded_hidden_and_signal_driven(self):
        self.assertIn("Type=notify", self.service)
        self.assertIn("ExecReload=/bin/kill -USR1 $MAINPID", self.service)
        self.assertIn("RuntimeDirectory=pocketds-touchpad", self.service)
        self.assertIn("RuntimeDirectoryMode=0700", self.service)
        self.assertIn("RuntimeDirectoryPreserve=no", self.service)
        self.assertIn("NoNewPrivileges=true", self.service)
        self.assertNotIn("START_VISIBLE", self.service)

    def test_visible_touchpad_publishes_focus_suppression_state(self):
        self.assertIn('TOUCHPAD_VISIBLE_STATE = TOUCHPAD_RUNTIME / "visible"', self.runtime)
        self.assertIn("def publish_touchpad_visibility(visible):", self.runtime)
        show = self.runtime[
            self.runtime.index("    def manual_show(self, _button=None):") :
            self.runtime.index("    def manual_hide(self, _button=None):")
        ]
        self.assertLess(
            show.index("publish_touchpad_visibility(True)"),
            show.index("self.window.show_all()"),
        )
        self.assertIn("publish_touchpad_visibility(False)", self.runtime)
        self.assertIn("_SIGHUP_GATE.mark_ready(self.queue_show)", self.runtime)


if __name__ == "__main__":
    unittest.main(verbosity=2)

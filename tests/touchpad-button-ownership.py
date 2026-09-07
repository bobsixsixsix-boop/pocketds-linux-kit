#!/usr/bin/env python3
"""Run real overlay callbacks with fake GTK/uinput; never open input devices."""
import ast
from pathlib import Path
from types import SimpleNamespace
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "components/touchpad"))
from touchpad_gestures import GestureAction, TouchpadGestureEngine
from touchpad_raw import PointerButtonLatch, RawTouchAction, RawTouchRouter, TypeBTouchFrame

SOURCE = ROOT / "components/touchpad/pocketds-touchpad.py"
PARSED = ast.parse(SOURCE.read_text())
METHODS = {
    "manual_show", "manual_hide", "on_unmap", "activate_touch_surface",
    "cancel_touch_surface_activation", "on_raw_touch", "handle_raw_control",
    "cancel_raw_control", "activate_raw_control", "press_virtual_left", "release_virtual_left", "emit_actions",
    "_emit_actions_unlocked", "click", "_click_unlocked", "wake_pointer",
    "_wake_pointer_unlocked", "sequence_key", "on_touch_event", "cancel_fallback_input",
    "on_pointer_press", "on_pointer_motion", "on_pointer_release",
}
REGIONS = [("keyboard", (0, 0, 100, 80)), ("panel", (100, 0, 100, 80)),
           ("left", (0, 650, 512, 118)), ("right", (512, 650, 512, 118)),
           ("track", (0, 80, 1024, 570))]


def load_methods(class_name, names, namespace):
    original = next(n for n in PARSED.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    selected = [n for n in original.body if isinstance(n, ast.FunctionDef) and n.name in names]
    if {n.name for n in selected} != names:
        raise AssertionError("production callback missing")
    node = ast.ClassDef(name="Probe", bases=[], keywords=[], decorator_list=[], body=selected)
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["Probe"]


class Scheduler:
    def __init__(self): self.pending = {}; self.next_id = 0; self.removed = []
    def timeout_add(self, ms, callback):
        self.next_id += 1
        self.pending[self.next_id] = (ms, callback)
        return self.next_id
    def source_remove(self, identifier):
        self.removed.append(identifier)
        self.pending.pop(identifier, None)
    def run(self, identifier):
        _, callback = self.pending.pop(identifier)
        callback()


class Device:
    def __init__(self): self.keys = {}; self.events = []
    def write(self, typ, code, value):
        self.events.append((typ, code, value))
        if typ == "EV_KEY": self.keys[code] = value
    def syn(self): pass


class Raw:
    def __init__(self): self.active = True; self.generation = 1
    def is_current(self, generation): return generation == self.generation
    def set_enabled(self, enabled, region=None):
        self.active = enabled; self.generation += 1; return True
    def is_active(self): return self.active
    def arm_context_menu_guard(self): return True
    def release_context_menu_guard(self): pass


class Touch:
    def __init__(self, kind, contact, timestamp, x=100, y=100):
        self.type = kind; self.contact = contact; self.time = timestamp
        self.x = x; self.y = y; self.button = 1
    def get_event_sequence(self): return self.contact


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = Scheduler()
        ecodes = SimpleNamespace(**{x:x for x in ("EV_KEY", "EV_REL", "BTN_LEFT", "BTN_RIGHT",
            "REL_X", "REL_Y", "REL_WHEEL", "REL_HWHEEL", "KEY_LEFTCTRL")})
        eventtypes = SimpleNamespace(**{x:x for x in ("TOUCH_BEGIN", "TOUCH_UPDATE", "TOUCH_END", "TOUCH_CANCEL")})
        self.namespace = dict(GestureAction=GestureAction, RawTouchAction=RawTouchAction, time=time,
            GLib=self.scheduler, ecodes=ecodes, Gdk=SimpleNamespace(EventType=eventtypes),
            RAW_CONTROL_TARGETS=("keyboard", "panel", "left", "right"),
            FULL_TOUCH_REGION=(0.,0.,1024.,768.), publish_touchpad_visibility=lambda visible:True)
        p = load_methods("PocketDSTouchpad", METHODS, self.namespace)()
        self.p = p
        p.visible = True
        p.input_lock = threading.RLock()
        p.gestures = TouchpadGestureEngine()
        p.uinput = Device()
        p.left_button_latch = PointerButtonLatch(p.press_virtual_left, p.release_virtual_left)
        p.raw_touch = Raw()
        p.raw_router = RawTouchRouter()
        p.raw_router.configure(1024, 768, REGIONS)
        p.raw_control_contact = None
        p.raw_control_target = None
        p.raw_left_chord_contacts = set()
        p.show_retry_source = 0
        p.touch_surface_source = 0
        p.raw_surface_ready = True
        p.pointer_fallback_active = False
        p.gtk_fallback_active = False
        p.window = SimpleNamespace(show_all=lambda:None, hide=lambda:None,
            get_allocation=lambda:SimpleNamespace(width=1024, height=768))
        for name, _ in REGIONS:
            setattr(p, name + "_button" if name != "track" else name, name)
        p.widget_touch_region = lambda widget: dict(REGIONS)[widget]
        p.set_raw_control_active = lambda target, active:None

    def assert_released(self):
        self.assertFalse(self.p.left_button_latch.held)
        self.assertEqual(self.p.uinput.keys.get("BTN_LEFT", 0), 0)

    def raw(self, kind, contact="left-contact", x=100, y=700):
        self.p.on_raw_touch(self.p.raw_touch.generation, [RawTouchAction(kind, contact, x, y)])

    def gtk_drag(self):
        self.p.raw_touch.active = False
        for kind, contact, timestamp in (("TOUCH_BEGIN", 1, 0), ("TOUCH_END", 1, 50),
                                          ("TOUCH_BEGIN", 2, 100)):
            self.p.on_touch_event(None, Touch(kind, contact, timestamp))
        self.assertEqual(self.p.uinput.keys["BTN_LEFT"], 1)
        self.assertTrue(self.p.gtk_fallback_active)

    def test_reopen_contact_before_layout_cannot_lose_its_release(self):
        self.p.manual_hide()
        self.p.manual_show()
        self.assertFalse(self.p.raw_surface_ready)
        self.raw("begin")
        self.assert_released()
        self.scheduler.run(self.p.touch_surface_source)
        self.assertTrue(self.p.raw_surface_ready)
        self.raw("end")
        self.assert_released()
        self.assertIsNone(self.p.raw_control_contact)
        self.raw("begin", "next-contact")
        self.assertEqual(self.p.uinput.keys["BTN_LEFT"], 1)
        self.raw("end", "next-contact")
        self.assert_released()

    def test_hide_cancels_old_layout_callback_before_next_show(self):
        self.p.manual_hide()
        self.p.manual_show()
        old_source = self.p.touch_surface_source
        self.p.manual_hide()
        self.assertNotIn(old_source, self.scheduler.pending)
        self.p.manual_show()
        new_source = self.p.touch_surface_source
        self.assertNotEqual(new_source, old_source)
        self.assertEqual(list(self.scheduler.pending), [new_source])
        self.scheduler.run(new_source)
        self.raw("begin")
        self.assertEqual(self.p.uinput.keys["BTN_LEFT"], 1)
        self.raw("end")
        self.assert_released()

    def test_reconfigure_releases_before_forgetting_owned_contact(self):
        self.raw("begin")
        self.assertTrue(self.p.left_button_latch.held)
        self.p.activate_touch_surface()
        self.assert_released()
        self.assertEqual(self.p.raw_router.contacts, {})
        self.raw("end")
        self.assert_released()

    def reopen_with_left_hold(self):
        stale_generation = self.p.raw_touch.generation
        self.p.manual_hide()
        self.p.manual_show()
        self.scheduler.run(self.p.touch_surface_source)
        self.raw("begin")
        self.assertEqual(self.p.left_button_latch.owners, {"raw-control"})
        return stale_generation

    def test_old_show_begin_cannot_join_new_show_contact(self):
        old = self.reopen_with_left_hold()
        self.p.on_raw_touch(old, [RawTouchAction("begin", "stale-track", 100, 100)])
        self.assertEqual(self.p.raw_router.contacts, {"left-contact": "left"})
        self.assertEqual(self.p.gestures.points, {})
        self.assertEqual(self.p.uinput.keys["BTN_LEFT"], 1)
        self.raw("end")
        self.assert_released()

    def test_old_show_end_cannot_release_reused_new_contact_id(self):
        old = self.reopen_with_left_hold()
        self.p.on_raw_touch(old, [RawTouchAction("end", "left-contact", 100, 700)])
        self.assertEqual(self.p.raw_control_contact, "left-contact")
        self.assertEqual(self.p.uinput.keys["BTN_LEFT"], 1)
        self.raw("end")
        self.assert_released()

    def test_old_show_cancel_cannot_release_new_show_owner(self):
        old = self.reopen_with_left_hold()
        before = list(self.p.uinput.events)
        self.p.on_raw_touch(old, [RawTouchAction("cancel")])
        self.assertEqual(self.p.uinput.events, before)
        self.assertEqual(self.p.left_button_latch.owners, {"raw-control"})
        self.raw("end")
        self.assert_released()

    def test_latest_handover_cancel_releases_when_earlier_cancel_is_stale(self):
        self.raw("begin")
        self.p.raw_touch.generation += 1
        disconnected = self.p.raw_touch.generation
        self.p.raw_touch.generation += 1
        reopened = self.p.raw_touch.generation
        self.p.on_raw_touch(disconnected, [RawTouchAction("cancel")])
        self.assertEqual(self.p.uinput.keys["BTN_LEFT"], 1)
        self.p.on_raw_touch(reopened, [RawTouchAction("cancel")])
        self.assert_released()
        self.assertEqual(self.p.raw_router.contacts, {})
        self.assertIsNone(self.p.raw_control_contact)

    def test_control_hide_stops_later_begin_in_the_same_raw_frame(self):
        generation = self.p.raw_touch.generation
        self.p.on_raw_touch(generation, [RawTouchAction("begin", "panel", 150, 30)])
        self.p.on_raw_touch(generation, [
            RawTouchAction("end", "panel", 150, 30),
            RawTouchAction("begin", "left-contact", 100, 700),
        ])
        self.p.on_raw_touch(self.p.raw_touch.generation, [RawTouchAction("cancel")])
        self.assertFalse(self.p.visible)
        self.assertFalse(self.p.raw_touch.active)
        self.assert_released()

    def test_gtk_drag_is_released_when_raw_grab_returns_before_cancel(self):
        self.gtk_drag()
        self.p.raw_touch.active = True
        self.p.on_touch_event(None, Touch("TOUCH_CANCEL", 2, 120))
        self.p.on_touch_event(None, Touch("TOUCH_END", 2, 150))
        self.assert_released()
        self.assertFalse(self.p.gtk_fallback_active)
        self.assertEqual(self.p.gestures.points, {})

    def test_reader_handover_cancels_gtk_drag_without_another_gtk_event(self):
        self.gtk_drag()
        self.p.raw_touch.active = True
        self.raw("cancel", None)
        self.assert_released()
        self.assertEqual(self.p.gestures.mode, "idle")
        self.assertEqual(self.p.raw_router.contacts, {})

    def test_first_raw_contact_releases_fallback_before_new_owner(self):
        self.gtk_drag()
        self.p.raw_touch.active = True
        self.raw("begin")
        self.assertEqual(self.p.left_button_latch.owners, {"raw-control"})
        edges = [value for typ, code, value in self.p.uinput.events if code == "BTN_LEFT"]
        self.assertEqual(edges[-3:], [1, 0, 1])
        self.raw("end")
        self.assert_released()

    def test_delayed_gtk_event_does_not_cancel_real_raw_drag(self):
        self.p.gestures.last_tap = (int(time.monotonic() * 1000), 100, 100)
        self.raw("begin", "track", 100, 100)
        self.assertEqual(self.p.left_button_latch.owners, {"gesture"})
        self.p.on_touch_event(None, Touch("TOUCH_CANCEL", 99, 120))
        self.assertEqual(self.p.uinput.keys["BTN_LEFT"], 1)
        self.raw("end", "track", 100, 100)
        self.assert_released()

    def test_left_hold_with_parallel_pointer_keeps_button_until_owner_ends(self):
        self.raw("begin")
        self.raw("begin", "track", 100, 100)
        self.raw("update", "track", 140, 120)
        self.raw("end", "track", 140, 120)
        self.assertEqual(self.p.uinput.keys["BTN_LEFT"], 1)
        self.assertEqual(self.p.left_button_latch.owners, {"raw-control"})
        self.raw("end")
        self.assert_released()

    def test_normal_gtk_drag_releases_on_end(self):
        self.gtk_drag()
        self.p.on_touch_event(None, Touch("TOUCH_UPDATE", 2, 120, 130, 110))
        self.assertEqual(self.p.uinput.keys["BTN_LEFT"], 1)
        self.p.on_touch_event(None, Touch("TOUCH_END", 2, 150, 130, 110))
        self.assert_released()
        self.assertFalse(self.p.gtk_fallback_active)

    def test_pointer_fallback_also_releases_on_raw_handover(self):
        self.p.raw_touch.active = False
        self.p.gestures.last_tap = (50, 100, 100)
        self.p.on_pointer_press(None, Touch("pointer", None, 100))
        self.assertEqual(self.p.uinput.keys["BTN_LEFT"], 1)
        self.p.raw_touch.active = True
        self.p.on_pointer_release(None, Touch("pointer", None, 150))
        self.assert_released()
        self.assertFalse(self.p.pointer_fallback_active)

    def test_unmap_clears_raw_button_and_pending_geometry(self):
        self.raw("begin")
        self.p.touch_surface_source = self.scheduler.timeout_add(80, self.p.activate_touch_surface)
        self.p.on_unmap()
        self.assert_released()
        self.assertFalse(self.p.visible)
        self.assertFalse(self.p.raw_surface_ready)
        self.assertEqual(self.scheduler.pending, {})

    def test_hidden_fallback_events_cannot_press_again(self):
        self.p.manual_hide()
        self.p.gestures.last_tap = (50, 100, 100)
        self.p.on_touch_event(None, Touch("TOUCH_BEGIN", 1, 100))
        self.p.on_pointer_press(None, Touch("pointer", None, 100))
        self.assert_released()
        self.assertEqual(self.p.gestures.points, {})

    def test_raw_open_publishes_handover_even_without_committed_raw_contacts(self):
        observed = []
        device = SimpleNamespace(name="Goodix Capacitive TouchScreen")
        reader = load_methods("RawTouchscreenReader", {"_open", "_cancel"},
            {"InputDevice":lambda path:device, "RawTouchAction":RawTouchAction,
             "resync_type_b": lambda source, tracker:None})()
        reader.DEVICE = "fixture-only"
        reader.lock = threading.RLock()
        reader.enabled = True
        reader.exclusive_guard = SimpleNamespace(active=True, acquire=lambda source:True)
        reader.tracker = TypeBTouchFrame()
        reader.generation = 0
        reader._acquire_neutral = lambda source: True
        reader.dispatch = lambda generation, actions:observed.extend(actions)
        self.assertIs(reader._open(), device)
        self.assertTrue(reader.ready)
        self.assertEqual(observed, [RawTouchAction("cancel")])
        observed.clear()
        reader.enabled = False
        reader._open()
        self.assertEqual(observed, [RawTouchAction("cancel")])


if __name__ == "__main__":
    unittest.main(verbosity=2)

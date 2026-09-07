#!/usr/bin/env python3
"""Exercise the real evdev readers against cached ioctl/queue fixtures only."""
import ast
from collections import deque
import errno
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'components/touchpad'))
import touchpad_raw as raw

E = SimpleNamespace(EV_SYN=0, EV_ABS=3, SYN_REPORT=0, SYN_DROPPED=3,
                    ABS_MT_SLOT=0x2f, ABS_MT_TRACKING_ID=0x39,
                    ABS_MT_POSITION_X=0x35, ABS_MT_POSITION_Y=0x36)
REGION = (0., 0., 1024., 768.)


def event(code, value=0, typ=E.EV_ABS):
    return SimpleNamespace(type=typ, code=code, value=value)


class Device:
    name = 'Goodix Capacitive TouchScreen'
    fd = 41

    def __init__(self):
        self.slots = [(-1, 300, 700), (-1, 400, 500)]
        self.slot = 0
        self.queue = deque()
        self.reads = 0
        self.ioctls = []
        self.closed = False
        self.grabbed = False
        self.fail_ungrab = False
        self.on_grab = lambda: None
        self.on_ioctl = lambda: None
        self.on_read = lambda: None

    def absinfo(self, code):
        assert code == E.ABS_MT_SLOT
        return SimpleNamespace(min=0, max=len(self.slots)-1, value=self.slot)

    def ioctl(self, fd, request, values, mutate):
        if self.closed:
            raise OSError(errno.EBADF, 'closed fixture')
        assert fd == self.fd and mutate is True
        assert request == 0x80000000 | (len(values) * 4 << 16) | (ord('E') << 8) | 0x0a
        assert values.itemsize == 4
        self.ioctls.append(values[0])
        self.on_ioctl()
        index = {E.ABS_MT_TRACKING_ID:0, E.ABS_MT_POSITION_X:1, E.ABS_MT_POSITION_Y:2}[values[0]]
        for number, slot in enumerate(self.slots, 1):
            values[number] = slot[index]

    def read(self):
        self.reads += 1
        self.on_read()
        if self.closed:
            raise OSError(errno.ENODEV, 'closed fixture')
        if self.queue:
            return iter(self.queue.popleft())
        raise BlockingIOError(errno.EAGAIN, 'no queued input')

    def grab(self):
        self.grabbed = True
        self.on_grab()

    def ungrab(self):
        if self.fail_ungrab:
            raise OSError(errno.EIO, 'ungrab failed')
        self.grabbed = False

    def close(self):
        self.closed = True
        self.grabbed = False


def reader(kind, device):
    path, class_name = (
        ('components/touchpad/pocketds-touchpad.py', 'RawTouchscreenReader')
        if kind == 'touchpad' else
        ('components/keyboard/pocketds-keyboard.py', 'RawKeyboardTouchReader')
    )
    tree = ast.parse((ROOT/path).read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    namespace = dict(threading=threading, TypeBTouchFrame=raw.TypeBTouchFrame,
        RawTouchAction=raw.RawTouchAction, resync_type_b=raw.resync_type_b,
        ExclusiveTouchGuard=raw.ExclusiveTouchGuard, ContextMenuTouchGuard=raw.ContextMenuTouchGuard,
        InputDevice=lambda path: device, ecodes=E, LOWER_TOUCH_WIDTH=1024., LOWER_TOUCH_HEIGHT=768.)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(ROOT/path), 'exec'), namespace)
    r = namespace[class_name].__new__(namespace[class_name])
    r.tracker = raw.TypeBTouchFrame()
    r.lock = threading.RLock()
    r.stop_event = threading.Event()
    r.enabled = False
    r.ready = False
    r.dropping = False
    r.generation = 0
    r.device = device
    r.region = REGION
    r.exclusive_guard = raw.ExclusiveTouchGuard()
    r.context_guard = raw.ContextMenuTouchGuard()
    r.observed = []
    r.dispatch = lambda generation, actions: r.observed.append((generation, list(actions)))
    r.thread = SimpleNamespace(join=lambda timeout:None)
    r.fixture_namespace = namespace
    return r


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.d = Device()
        self.f = raw.TypeBTouchFrame()
        self.mock = patch.object(raw.fcntl, 'ioctl', self.d.ioctl)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def test_snapshot_seeds_unchanged_axes_and_current_slot(self):
        self.d.slot = 1
        raw.resync_type_b(self.d, self.f)
        self.f.set_tracking_id(7)
        self.f.set_x(450)  # Y remains 500 in this slot; no Y event.
        self.assertEqual(self.f.sync(REGION), [raw.RawTouchAction('begin', 7, 523., 450.)])
        self.assertEqual(self.d.ioctls, [0x39, 0x35, 0x36] * 2)

    def test_existing_finger_is_ignored_through_motion_until_lift(self):
        self.d.slots[0] = (6, 300, 700)
        raw.resync_type_b(self.d, self.f)
        self.assertEqual(self.f.sync(REGION), [])
        self.f.set_x(310)
        self.assertEqual(self.f.sync(REGION), [])
        self.f.set_tracking_id(-1)
        self.assertEqual(self.f.sync(REGION), [])
        self.f.set_tracking_id(7)
        self.assertEqual(self.f.sync(REGION), [raw.RawTouchAction('begin', 7, 323., 310.)])

    def test_queued_old_begin_end_are_discarded_not_replayed(self):
        self.d.queue.append([event(E.ABS_MT_TRACKING_ID, 7), event(E.ABS_MT_TRACKING_ID, -1)])
        raw.resync_type_b(self.d, self.f)
        self.assertEqual(self.f.sync(REGION), [])
        self.assertFalse(self.d.queue)

    def test_arrival_during_snapshot_retries_before_seeding(self):
        def arrive():
            if len(self.d.ioctls) == 2:
                self.d.slots[0] = (7, 320, 610)
                self.d.queue.append([event(E.ABS_MT_POSITION_X, 320), event(E.SYN_REPORT, typ=E.EV_SYN)])
        self.d.on_ioctl = arrive
        raw.resync_type_b(self.d, self.f)
        self.assertGreater(len(self.d.ioctls), 6)
        self.assertEqual(self.f.sync(REGION), [])
        self.assertEqual(self.f.slots[0].x, 320)
        self.assertEqual(self.f.slots[0].y, 610)

    def test_snapshot_changing_without_complete_report_is_retried(self):
        def change():
            if len(self.d.ioctls) == 4:
                self.d.slot = 1
                self.d.slots[0] = (7, 320, 610)
        self.d.on_ioctl = change
        raw.resync_type_b(self.d, self.f)
        self.assertGreater(len(self.d.ioctls), 6)
        self.assertEqual(self.f.current_slot, 1)
        self.assertEqual(self.f.sync(REGION), [])

    def test_constant_input_and_invalid_states_fail_bounded(self):
        self.d.on_read = lambda: self.d.queue.append([event(E.ABS_MT_POSITION_X, 300)] * 128)
        with self.assertRaises(OSError):
            raw.resync_type_b(self.d, self.f)
        self.assertLessEqual(self.d.reads, 33)
        self.assertEqual(self.f.slots, {})
        self.d.on_read = lambda: None
        self.d.queue.clear()
        for slots in ([], [(-1, 300, 700)] * 65, [(1, 300, 700), (1, 400, 500)],
                      [(-2, 300, 700)], [(-1, 768, 700)], [(-1, 300, 1024)]):
            with self.subTest(slots=slots[:2]):
                self.d.slots = slots
                with self.assertRaises(OSError): raw.resync_type_b(self.d, self.f)

    def test_continuous_snapshot_race_has_only_three_attempts(self):
        def race():
            if len(self.d.ioctls) % 3 == 1:
                tracking, x, y = self.d.slots[0]
                self.d.slots[0] = (tracking, x + 1, y)
        self.d.on_ioctl = race
        with self.assertRaises(OSError): raw.resync_type_b(self.d, self.f)
        self.assertEqual(len(self.d.ioctls), 18)
        self.assertFalse(self.f.slots)


class ReaderTests(unittest.TestCase):
    def for_readers(self, callback):
        for kind in ('keyboard', 'touchpad'):
            with self.subTest(kind=kind):
                d = Device()
                r = reader(kind, d)
                with patch.object(raw.fcntl, 'ioctl', d.ioctl):
                    callback(r, d, kind)

    def test_enable_restores_hidden_or_other_grab_changes(self):
        def check(r, d, kind):
            r.set_enabled(True)
            r._process(event(E.ABS_MT_TRACKING_ID, 7))
            r._process(event(E.SYN_REPORT, typ=E.EV_SYN))
            self.assertEqual(r.observed[-1][1], [raw.RawTouchAction('begin', 7, 323., 300.)])
            old_generation = r.generation
            r.set_enabled(False)
            d.slots[0] = (-1, 450, 600)  # Missed during hidden/EVIOCGRAB epoch.
            r.set_enabled(True)
            r._process(event(E.ABS_MT_TRACKING_ID, 8))
            r._process(event(E.ABS_MT_POSITION_Y, 500))
            r._process(event(E.SYN_REPORT, typ=E.EV_SYN))
            self.assertEqual(r.observed[-1][1], [raw.RawTouchAction('begin', 8, 523., 450.)])
            self.assertFalse(r.is_current(old_generation))
        self.for_readers(check)

    def test_open_and_reconnect_seed_new_device_state(self):
        def check(r, d, kind):
            r.enabled = True
            r._open()
            old_generation = r.generation
            self.assertEqual(r.observed[-1][1], [raw.RawTouchAction('cancel')])
            r._disconnect()
            self.assertTrue(d.closed)
            self.assertFalse(d.grabbed)
            self.assertFalse(r.is_current(old_generation))
            d.closed = False
            d.slots[0] = (-1, 450, 600)
            r._open()
            r._process(event(E.ABS_MT_TRACKING_ID, 8))
            r._process(event(E.SYN_REPORT, typ=E.EV_SYN))
            self.assertEqual(r.observed[-1][1], [raw.RawTouchAction('begin', 8, 423., 450.)])
        self.for_readers(check)

    def test_drop_cancels_ignores_fragment_and_resyncs_held_finger(self):
        def check(r, d, kind):
            r.set_enabled(True)
            r._process(event(E.ABS_MT_TRACKING_ID, 7))
            r._process(event(E.SYN_REPORT, typ=E.EV_SYN))
            old_generation = r.generation
            r._process(event(E.SYN_DROPPED, typ=E.EV_SYN))
            self.assertFalse(r.is_current(old_generation))
            self.assertEqual(r.observed[-1][1], [raw.RawTouchAction('cancel')])
            r._process(event(E.ABS_MT_TRACKING_ID, 999))
            r._process(event(E.ABS_MT_POSITION_X, 500))
            self.assertFalse(r.tracker.slots)
            d.slots[0] = (8, 320, 600)
            self.assertTrue(r._process(event(E.SYN_REPORT, typ=E.EV_SYN)))
            count = len(r.observed)
            r._process(event(E.ABS_MT_POSITION_X, 330))
            r._process(event(E.SYN_REPORT, typ=E.EV_SYN))
            r._process(event(E.ABS_MT_TRACKING_ID, -1))
            r._process(event(E.SYN_REPORT, typ=E.EV_SYN))
            self.assertEqual(len(r.observed), count)
            r._process(event(E.ABS_MT_TRACKING_ID, 9))
            r._process(event(E.SYN_REPORT, typ=E.EV_SYN))
            self.assertEqual(r.observed[-1][1], [raw.RawTouchAction('begin', 9, 423., 330.)])
        self.for_readers(check)

    def test_real_loop_discards_remaining_loaded_batch_after_resync(self):
        def check(r, d, kind):
            r.set_enabled(True)
            d.queue.append([event(E.SYN_DROPPED, typ=E.EV_SYN), event(E.SYN_REPORT, typ=E.EV_SYN),
                event(E.ABS_MT_TRACKING_ID, 999), event(E.ABS_MT_POSITION_X, 300),
                event(E.ABS_MT_POSITION_Y, 500), event(E.SYN_REPORT, typ=E.EV_SYN)])
            def select(*args):
                r.stop_event.set()
                return ([d.fd], [], [])
            r.fixture_namespace['select'] = SimpleNamespace(select=select)
            (r.run if kind == 'touchpad' else r._read_loop)()
            self.assertTrue(all(action.kind == 'cancel' for _, actions in r.observed for action in actions))
            self.assertTrue(d.closed)
            self.assertFalse(d.grabbed)
        self.for_readers(check)

    def test_open_snapshot_failure_closes_and_invalidates(self):
        def check(r, d, kind):
            r.enabled = True
            before = r.generation
            d.on_ioctl = lambda: (_ for _ in ()).throw(OSError(errno.EIO, 'snapshot failed'))
            with self.assertRaises(OSError): r._open()
            self.assertIsNone(r.device)
            self.assertFalse(r.ready)
            self.assertFalse(d.grabbed)
            self.assertTrue(d.closed)
            self.assertFalse(r.is_current(before))
            self.assertEqual(r.observed[-1][1], [raw.RawTouchAction('cancel')])
        self.for_readers(check)

    def test_enable_snapshot_failure_cannot_leave_active_grab(self):
        def check(r, d, kind):
            d.on_ioctl = lambda: (_ for _ in ()).throw(OSError(errno.EIO, 'snapshot failed'))
            r.set_enabled(True)
            self.assertIsNone(r.device)
            self.assertFalse(r.ready)
            self.assertFalse(d.grabbed)
            self.assertTrue(d.closed)
            if kind == 'touchpad': self.assertFalse(r.enabled)
        self.for_readers(check)

    def test_drop_snapshot_failure_closes_in_real_worker_loop(self):
        def check(r, d, kind):
            r.set_enabled(True)
            old_generation = r.generation
            d.queue.append([event(E.SYN_DROPPED, typ=E.EV_SYN),
                            event(E.SYN_REPORT, typ=E.EV_SYN)])
            d.on_ioctl = lambda: (_ for _ in ()).throw(OSError(errno.EIO, 'drop resync failed'))
            def select(*args):
                r.stop_event.set()
                return ([d.fd], [], [])
            r.fixture_namespace['select'] = SimpleNamespace(select=select)
            (r.run if kind == 'touchpad' else r._read_loop)()
            self.assertTrue(d.closed)
            self.assertFalse(d.grabbed)
            self.assertFalse(r.ready)
            self.assertIsNone(r.device)
            self.assertFalse(r.is_current(old_generation))
            self.assertTrue(all(action.kind == 'cancel' for _, actions in r.observed for action in actions))
        self.for_readers(check)

    def test_close_cancels_current_epoch_and_releases_every_grab(self):
        def check(r, d, kind):
            r.set_enabled(True)
            epoch = r.generation
            r.close()
            self.assertFalse(r.is_current(epoch))
            self.assertFalse(r.enabled)
            self.assertFalse(r.ready)
            self.assertTrue(d.closed)
            self.assertFalse(d.grabbed)
        self.for_readers(check)

    def test_keyboard_enable_ignores_a_finger_already_down(self):
        d = Device(); r = reader('keyboard', d)
        d.slots[0] = (7, 300, 700)
        with patch.object(raw.fcntl, 'ioctl', d.ioctl):
            r.set_enabled(True)
            count = len(r.observed)
            r._process(event(E.ABS_MT_POSITION_X, 330))
            r._process(event(E.SYN_REPORT, typ=E.EV_SYN))
            r._process(event(E.ABS_MT_TRACKING_ID, -1))
            r._process(event(E.SYN_REPORT, typ=E.EV_SYN))
            self.assertEqual(len(r.observed), count)

    def test_touchpad_waits_for_neutral_before_grab_without_background_capture(self):
        d = Device(); r = reader('touchpad', d)
        d.slots[0] = (7, 300, 700)
        with patch.object(raw.fcntl, 'ioctl', d.ioctl):
            self.assertFalse(r.set_enabled(True))
            self.assertFalse(d.grabbed)
            self.assertFalse(r.enabled)
            d.slots[0] = (-1, 300, 700)
            self.assertTrue(r.set_enabled(True))
            self.assertTrue(r.is_active())

    def test_touchpad_held_finger_racing_grab_immediately_releases(self):
        d = Device(); r = reader('touchpad', d)
        d.on_grab = lambda: d.slots.__setitem__(0, (7, 300, 700))
        with patch.object(raw.fcntl, 'ioctl', d.ioctl):
            self.assertFalse(r.set_enabled(True))
            self.assertFalse(d.grabbed)
            self.assertFalse(r.enabled)
            self.assertFalse(r.is_active())

    def test_failed_handover_ungrab_forces_descriptor_close(self):
        d = Device(); r = reader('touchpad', d)
        d.on_grab = lambda: d.slots.__setitem__(0, (7, 300, 700))
        d.fail_ungrab = True
        with patch.object(raw.fcntl, 'ioctl', d.ioctl):
            self.assertFalse(r.set_enabled(True))
            self.assertTrue(d.closed)
            self.assertFalse(d.grabbed)
            self.assertIsNone(r.device)

    def test_hide_with_failed_ungrab_closes_instead_of_retaining_ownership(self):
        d = Device(); r = reader('touchpad', d)
        with patch.object(raw.fcntl, 'ioctl', d.ioctl):
            self.assertTrue(r.set_enabled(True))
            old_generation = r.generation
            d.fail_ungrab = True
            self.assertTrue(r.set_enabled(False))
            self.assertTrue(d.closed)
            self.assertFalse(d.grabbed)
            self.assertFalse(r.enabled)
            self.assertFalse(r.ready)
            self.assertFalse(r.is_current(old_generation))

    def test_enable_without_descriptor_does_not_arm_hidden_background_grab(self):
        d = Device(); r = reader('touchpad', d); r.device = None
        self.assertFalse(r.set_enabled(True))
        self.assertFalse(r.enabled)
        with patch.object(raw.fcntl, 'ioctl', d.ioctl):
            r._open()
            self.assertFalse(d.grabbed)
            self.assertFalse(r.is_active())
            self.assertTrue(r.set_enabled(True))
            self.assertTrue(d.grabbed)


if __name__ == '__main__':
    unittest.main(verbosity=2)

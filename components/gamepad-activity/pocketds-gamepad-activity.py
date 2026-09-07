#!/usr/bin/python3
"""Count managed gamepad interaction as KDE activity, never wake a dark screen.

Non-exclusive evdev observation only: no grab, uinput, remapping, inhibitor,
DPMS, power-profile, lock or suspend mutation. Raw input is never logged.
"""
import glob
import argparse
import fcntl
import json
import os
from pathlib import Path
import time
import struct

INTERVAL = 2.0
GAMEPAD_NAME = 'Microsoft X-Box One Elite 2 pad'
GAMEPAD_ID = (0x03, 0x045e, 0x0b00)
PROFILE = '/usr/share/inputplumber/profiles/pocketds-gamepad.yaml'
COMPOSITE = '/org/shadowblip/InputPlumber/CompositeDevice0'
COMPOSITE_IFACE = 'org.shadowblip.Input.CompositeDevice'
BACKLIGHTS = ('ae94000.dsi.0', 'sy7758-backlight')
STICKS = {0, 1, 3, 4}
TRIGGERS = {2, 5}
HATS = {16, 17}
AXES = STICKS | TRIGGERS | HATS


class Controls:
    """Significant transitions, with neutral drift rejection and held state."""
    def __init__(self, axes, keys=()):
        # axes maps code to (value, min, max, flat). Initial state is NOT input.
        self.axes = dict(axes)
        self.keys = set(keys)
        self.held = set()
        self.syncing = False

    @staticmethod
    def button(code):
        return 304 <= code <= 318 or 544 <= code <= 547 or 704 <= code <= 743

    def axis_active(self, code, value):
        if code in HATS:
            return value in (-1, 1)
        _, low, high, flat = self.axes[code]
        span = high - low
        if span <= 0 or not low <= value <= high:
            return False
        if code in STICKS:
            return abs(value - (low + high) / 2) > max(span * 0.09, flat)
        return value - low > max(span * 0.08, flat)

    def feed(self, kind, code, value):
        if kind == 0:
            if code == 3:  # SYN_DROPPED: caller must resnapshot after SYN_REPORT.
                self.syncing = True
                self.held.clear()
            return False
        if self.syncing:
            return False
        if kind == 1 and self.button(code) and value in (0, 1):
            previous = code in self.keys
            if previous == bool(value):
                return False
            if value:
                self.keys.add(code)
                self.held.add((kind, code))
            else:
                self.keys.discard(code)
                self.held.discard((kind, code))
            return True
        if kind == 3 and code in self.axes:
            old, low, high, flat = self.axes[code]
            if not low <= value <= high or old == value:
                return False
            old_active = self.axis_active(code, old)
            active = self.axis_active(code, value)
            self.axes[code] = (value, low, high, flat)
            meaningful = (active != old_active
                          or (active and abs(value - old) >= max(1, (high - low) * 0.025)))
            if active and meaningful:
                self.held.add((kind, code))
            elif not active:
                self.held.discard((kind, code))
            return meaningful
        return False

    def clear_intent(self):
        self.held.clear()


class Bridge:
    """Only genuine recent edges or revalidated held controls may reset idle."""
    def __init__(self, adapter):
        self.adapter = adapter
        self.last_sent = float('-inf')
        self.last_attempt = float('-inf')
        self.pending_at = None
        self.sent = 0

    def event(self, now, event_time, meaningful):
        if meaningful and 0 <= now - event_time <= 1.0:
            self.pending_at = now

    def tick(self, now, held=False):
        if self.pending_at is not None and now - self.pending_at > INTERVAL + 1:
            self.pending_at = None
        if not held and self.pending_at is None:
            return False
        if now - self.last_attempt < INTERVAL:
            return False
        self.last_attempt = now
        if not self.adapter.allowed():
            self.pending_at = None
            self.adapter.clear_intent()
            return False
        self.pending_at = None
        self.adapter.notify_activity()
        self.last_sent = now
        self.sent += 1
        return True


def emit(event, **fields):
    print(json.dumps({'event': event, **fields}), flush=True)


class Live:
    def __init__(self):
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
        from evdev import InputDevice
        DBusGMainLoop(set_as_default=True)
        self.GLib, self.InputDevice = GLib, InputDevice
        self.system, self.session = dbus.SystemBus(), dbus.SessionBus()
        self.device = self.controls = self.watch = None
        self.session_path = None
        self.locking = False
        self.sleeping = False
        self.bridge = Bridge(self)
        self.last_gap = time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()
        self.last_summary = 0
        self.session.add_signal_receiver(self.about_to_lock, signal_name='AboutToLock',
            dbus_interface='org.kde.screensaver', bus_name='org.freedesktop.ScreenSaver')
        self.session.add_signal_receiver(self.lock_changed, signal_name='ActiveChanged',
            dbus_interface='org.freedesktop.ScreenSaver', bus_name='org.freedesktop.ScreenSaver')
        self.system.add_signal_receiver(self.prepare_sleep, signal_name='PrepareForSleep',
            dbus_interface='org.freedesktop.login1.Manager', bus_name='org.freedesktop.login1')
        self.GLib.timeout_add(500, self.tick)
        self.GLib.timeout_add_seconds(3, self.discover)
        self.discover()

    def property(self, bus, service, path, interface, name):
        return bus.get_object(service, path, introspect=False).Get(interface, name,
            dbus_interface='org.freedesktop.DBus.Properties', timeout=1)

    def clear_intent(self):
        self.bridge.pending_at = None
        if self.controls:
            self.controls.clear_intent()

    def about_to_lock(self):
        self.locking = True
        self.clear_intent()

    def lock_changed(self, active):
        self.locking = bool(active)
        self.clear_intent()

    def prepare_sleep(self, active):
        self.sleeping = bool(active)
        self.clear_intent()

    def allowed(self):
        started = time.monotonic()
        self.activity_proxy = None
        try:
            if self.locking or self.sleeping:
                return False
            manager = ('org.freedesktop.login1', '/org/freedesktop/login1',
                       'org.freedesktop.login1.Manager')
            if self.property(self.system, *manager, 'LidClosed'):
                return False
            if self.property(self.system, *manager, 'PreparingForSleep'):
                return False
            if not self.session_path:
                return False
            session = ('org.freedesktop.login1', self.session_path,
                       'org.freedesktop.login1.Session')
            if not self.property(self.system, *session, 'Active'):
                return False
            if self.property(self.system, *session, 'LockedHint'):
                return False
            # This API is meaningful only in KWin, whose static idle poller works
            # on Wayland. The generic external KIdleTime Wayland poller is a no-op.
            daemon = self.session.get_object('org.freedesktop.DBus',
                '/org/freedesktop/DBus', introspect=False)
            owner = str(daemon.GetNameOwner('org.freedesktop.ScreenSaver',
                dbus_interface='org.freedesktop.DBus', timeout=1))
            if not owner.startswith(':'):
                return False
            owner_pid = daemon.GetConnectionUnixProcessID(owner,
                dbus_interface='org.freedesktop.DBus', timeout=1)
            if Path(f'/proc/{owner_pid}/comm').read_text().strip() != 'kwin_wayland':
                return False
            obj = self.session.get_object(owner, '/ScreenSaver', introspect=False)
            if obj.GetActive(dbus_interface='org.freedesktop.ScreenSaver', timeout=1):
                return False
            # Fail closed on single-screen / manually blanked / unknown state.
            if any((Path('/sys/class/backlight') / name / 'bl_power').read_text().strip() != '0'
                   for name in BACKLIGHTS):
                return False
            if self.property(self.system, 'org.shadowblip.InputPlumber', COMPOSITE,
                             COMPOSITE_IFACE, 'ProfilePath') != PROFILE:
                return False
            # Recheck immediate power/lid state after the slower identity probes.
            # A stalled bus must not turn an old edge into a late activity reset.
            ready = (not self.locking and not self.sleeping
                    and not self.property(self.system, *manager, 'LidClosed')
                    and not self.property(self.system, *manager, 'PreparingForSleep')
                    and all((Path('/sys/class/backlight') / name / 'bl_power').read_text().strip() == '0'
                            for name in BACKLIGHTS)
                    and time.monotonic() - started < 1.0)
            if ready:
                self.activity_proxy = obj
            return ready
        except Exception:
            return False

    def notify_activity(self):
        # No wakeup(), refreshStatus(), inhibit or synthesized keyboard event.
        proxy, self.activity_proxy = self.activity_proxy, None
        if proxy is None:
            raise RuntimeError('no verified KWin activity target')
        proxy.SimulateUserActivity(dbus_interface='org.freedesktop.ScreenSaver', timeout=1)

    def snapshot(self):
        axes = {}
        for code in AXES:
            info = self.device.absinfo(code)
            if info is not None:
                axes[code] = (info.value, info.min, info.max, info.flat)
        self.controls = Controls(axes, self.device.active_keys())

    def detach(self):
        if self.watch:
            self.GLib.source_remove(self.watch)
        self.watch = None
        if self.device:
            self.device.close()
        self.device = self.controls = None
        self.clear_intent()

    def discover(self):
        try:
            manager = self.system.get_object('org.freedesktop.login1', '/org/freedesktop/login1', introspect=False)
            candidates = []
            for _, uid, _, seat, path in manager.ListSessions(
                    dbus_interface='org.freedesktop.login1.Manager', timeout=1):
                if int(uid) == os.getuid() and seat == 'seat0' and self.property(
                        self.system, 'org.freedesktop.login1', path,
                        'org.freedesktop.login1.Session', 'Type') == 'wayland':
                    candidates.append(str(path))
            self.session_path = candidates[0] if len(candidates) == 1 else None
            paths = []
            for name in glob.glob('/sys/class/input/event*/device/name'):
                path = Path(name)
                if (path.read_text().strip() == GAMEPAD_NAME
                        and str(path.resolve()).startswith('/sys/devices/virtual/input/')):
                    paths.append('/dev/input/' + path.parent.parent.name)
            if len(paths) != 1:
                self.detach()
                return True
            if self.device and self.device.path == paths[0]:
                return True
            self.detach()
            device = self.InputDevice(paths[0])
            if (device.info.bustype, device.info.vendor, device.info.product) != GAMEPAD_ID:
                device.close()
                return True
            self.device = device
            # Linux EVIOCSCLOCKID: per-reader timestamp basis, not an input write.
            fcntl.ioctl(device.fd, 0x400445a0, struct.pack('i', time.CLOCK_MONOTONIC))
            self.snapshot()
            self.watch = self.GLib.io_add_watch(device.fd,
                self.GLib.IO_IN | self.GLib.IO_HUP | self.GLib.IO_ERR, self.readable)
            emit('observer-attached', device='managed-virtual-gamepad')
        except Exception as exc:
            self.detach()
            emit('observer-unavailable', error=type(exc).__name__)
        return True

    def readable(self, _fd, condition):
        try:
            if condition & (self.GLib.IO_HUP | self.GLib.IO_ERR):
                raise OSError('device removed')
            now = time.monotonic()
            for event in self.device.read():
                if self.controls.syncing and event.type == 0 and event.code == 0:
                    self.snapshot()
                    self.clear_intent()
                    continue
                if not 0 <= now - event.timestamp() <= 1.0:
                    # Update cached keys/axes even when the event earns no idle
                    # credit. Otherwise a stale release hides the next press.
                    # Feeding SYN_DROPPED also preserves overflow resync rules.
                    self.controls.feed(event.type, event.code, event.value)
                    self.clear_intent()
                    continue
                meaningful = self.controls.feed(event.type, event.code, event.value)
                if self.controls.syncing:
                    self.clear_intent()
                    continue
                self.bridge.event(now, event.timestamp(), meaningful)
            self.tick()
            return True
        except BlockingIOError:
            return True
        except Exception:
            self.detach()
            return False

    def held_now(self):
        if not self.controls or self.controls.syncing or not self.controls.held:
            return False
        # Sustained intentional holds count too, but revalidate kernel state;
        # never keep the screen awake from cached releases or unplugged pads.
        keys = set(self.device.active_keys())
        for kind, code in tuple(self.controls.held):
            active = (code in keys if kind == 1 else
                      self.controls.axis_active(code, self.device.absinfo(code).value))
            if not active:
                self.controls.held.discard((kind, code))
        return bool(self.controls.held)

    def tick(self):
        try:
            gap = time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()
            if abs(gap - self.last_gap) > 0.5:
                self.clear_intent()
            self.last_gap = gap
            self.bridge.tick(time.monotonic(), self.held_now())
            if self.bridge.sent and time.monotonic() - self.last_summary > 60:
                emit('activity-summary', notifications=self.bridge.sent)
                self.last_summary = time.monotonic()
        except Exception as exc:
            self.clear_intent()
            emit('activity-refused', error=type(exc).__name__)
        return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='read-only readiness, no activity notification')
    args = parser.parse_args()
    live = Live()
    if args.check:
        emit('check', observer_ready=live.device is not None, activity_allowed=bool(live.allowed()),
             notifications=live.bridge.sent)
        live.detach()
    else:
        emit('ready', min_notify_interval_seconds=INTERVAL)
        live.GLib.MainLoop().run()

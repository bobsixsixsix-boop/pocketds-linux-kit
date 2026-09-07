#!/usr/bin/python3
"""Read-only, bounded sleep evidence collector; never initiates or repairs sleep.

Run only under the attended harness or a separate root transient service.
It observes only native SW_LID, not keys, and never grabs an input device.
Every run requires a new /run directory. Existing receipts are never modified.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import queue
import signal
import stat
import struct
import subprocess
import threading
import time

EVENT = struct.Struct("llHHi")
EVIDENCE_SCHEMA = "pocketds.lid-observer.v2"
INPUT_CLOCK = "CLOCK_BOOTTIME"
# Linux 7.1.12 UAPI: _IOW('E', 0xa0, int), asm-generic ioctl ABI (arm64/x86).
# The clock is per evdev client, not a setting on the shared input device.
EVIOCSCLOCKID = 0x400445A0
LINUX_CLOCK_BOOTTIME = 7
BACKLIGHTS = ("ae94000.dsi.0", "sy7758-backlight")
# The upper panel's actual_brightness invokes get_brightness and a DSI read.
# Reading it can alter DSI mode flags and interfere with suspend/resume. Only
# cached upper properties are safe to sample; lower actual is GPIO-driver state.
BACKLIGHT_FIELDS = {
    BACKLIGHTS[0]: ("bl_power", "brightness", "max_brightness"),
    BACKLIGHTS[1]: ("bl_power", "brightness", "actual_brightness", "max_brightness"),
}
LOGIN = "org.freedesktop.login1"
MANAGER = LOGIN + ".Manager"
LOGIN_PATH = "/org/freedesktop/login1"


def timestamp():
    return {"unix_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(),
            "boottime_ns": time.clock_gettime_ns(time.CLOCK_BOOTTIME)}


def small(path, limit=65536):
    with open(path, "rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise RuntimeError("oversized observation")
    return raw.decode("utf-8", errors="replace").strip()


def observation(path, limit=65536):
    try:
        return {"value": small(path, limit)}
    except (OSError, RuntimeError) as error:
        return {"error": type(error).__name__}


def lid_events(payload):
    if len(payload) % EVENT.size:
        raise RuntimeError("partial native input event")
    result = []
    for offset in range(0, len(payload), EVENT.size):
        sec, usec, kind, code, value = EVENT.unpack_from(payload, offset)
        if kind == 5 and code == 0:  # EV_SW / SW_LID only
            if sec < 0 or not 0 <= usec < 1_000_000:
                raise RuntimeError("invalid native BOOTTIME timestamp")
            result.append({"event": "native-lid" if value in (0, 1) else "native-lid-invalid",
                           "closed": value == 1 if value in (0, 1) else None,
                           "raw_value": value, "input_sec": sec, "input_usec": usec,
                           "input_clock": INPUT_CLOCK,
                           "input_boottime_ns": sec * 1_000_000_000 + usec * 1000})
        elif kind == 0 and code == 3:  # SYN_DROPPED means evidence is incomplete.
            result.append({"event": "native-input-overrun"})
    return result


def backlights_dark(record):
    """Readback proxy independent of delayed logind lid notification; None is unknown."""
    lights = record.get("backlights", {})
    fields = [(name, "bl_power", "4") for name in BACKLIGHTS]
    fields += [(BACKLIGHTS[1], key, "0") for key in ("brightness", "actual_brightness")]
    values = [lights.get(name, {}).get(key, {}).get("value") for name, key, _ in fields]
    if any(not isinstance(value, str) or not value.isdecimal() for value in values):
        return None
    return all(value == expected for value, (_, _, expected) in zip(values, fields))


def physical_dark(record):
    """Legacy logind-correlated proxy; assessment uses raw-event time boundaries."""
    return record.get("lid_closed") is True and backlights_dark(record) is True


def open_native_device(device):
    descriptor = os.open(device, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if not stat.S_ISCHR(os.fstat(descriptor).st_mode):
            raise RuntimeError("native Hall input is not a character device")
        # Failure must prevent readiness. Never guess the clock of kernel events.
        fcntl.ioctl(descriptor, EVIOCSCLOCKID, struct.pack("i", LINUX_CLOCK_BOOTTIME))
        state = bytearray(32)
        fcntl.ioctl(descriptor, (2 << 30) | (32 << 16) | (ord("E") << 8) | 0x1b, state, True)
        return descriptor, bool(state[0] & 1)
    except BaseException:
        os.close(descriptor)
        raise


class Recorder:
    def __init__(self, directory):
        self.path = directory
        self.lock = threading.Lock()
        self.written = 0
        self.stream = open(directory / "events.jsonl", "x", encoding="utf-8", buffering=1)

    def emit(self, event, **values):
        line = json.dumps({"event": event, **timestamp(), **values}, ensure_ascii=True) + "\n"
        with self.lock:
            self.written += len(line)
            if self.written > 16 * 1024 * 1024:
                raise RuntimeError("bounded evidence limit reached")
            self.stream.write(line)


class Observer:
    def __init__(self, out, duration):
        # Deferred import permits pure fixtures on the development Mac.
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.record = Recorder(out)
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.loop = GLib.MainLoop()
        self.stop = threading.Event()
        self.queue = queue.Queue(maxsize=4)
        self.lid = None
        self.duration = duration
        self.started = time.clock_gettime(time.CLOCK_BOOTTIME)
        self.fd = -1
        self.subscriptions = []

    def query_lid(self):
        reply = self.bus.call_sync(LOGIN, LOGIN_PATH, "org.freedesktop.DBus.Properties",
            "Get", self.GLib.Variant("(ss)", (MANAGER, "LidClosed")),
            self.GLib.VariantType.new("(v)"), self.Gio.DBusCallFlags.NONE, 2000, None)
        value = reply.get_child_value(0).get_variant()
        if not value.is_of_type(self.GLib.VariantType.new("b")):
            raise RuntimeError("nonboolean logind lid state")
        return value.get_boolean()

    def fast(self):
        started = timestamp()
        record = {"lid_closed": self.lid,
                  "pm_wakeup_irq": observation("/sys/power/pm_wakeup_irq", 64),
                  "rtc_alarm": observation("/sys/class/rtc/rtc0/wakealarm", 64),
                  "counters": {key: observation("/sys/power/suspend_stats/" + key, 64)
                               for key in ("success", "fail")},
                  "backlights": {}}
        for name in BACKLIGHTS:
            record["backlights"][name] = {
                key: observation(Path("/sys/class/backlight") / name / key, 64)
                for key in BACKLIGHT_FIELDS[name]}
        finished = timestamp()
        record.update(sample_started_boottime_ns=started["boottime_ns"],
                      sample_finished_boottime_ns=finished["boottime_ns"],
                      sample_started_monotonic_ns=started["monotonic_ns"],
                      sample_finished_monotonic_ns=finished["monotonic_ns"])
        record["backlights_dark_proxy"] = backlights_dark(record)
        record["physical_dark_proxy"] = physical_dark(record)
        self.record.emit("fast-state", **record)

    def detailed(self, reason):
        prefix = ["/usr/bin/runuser", "-u", "pocketds", "--", "/usr/bin/env",
                  "WAYLAND_DISPLAY=wayland-0", "XDG_RUNTIME_DIR=/run/user/1000",
                  "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
                  "QT_QPA_PLATFORM=wayland", "QT_ACCESSIBILITY=0",
                  "QT_LINUX_ACCESSIBILITY_ALWAYS_ON=0", "/usr/bin/kscreen-doctor"]
        for label, args in (("outputs", ["-j"]), ("dpms", ["--dpms", "show"])):
            try:
                result = subprocess.run(prefix + args, stdin=subprocess.DEVNULL,
                                        capture_output=True, text=True, timeout=3)
                self.record.emit(label, reason=reason, code=result.returncode,
                                 stdout=result.stdout[:131072], stderr=result.stderr[:2048],
                                 truncated=len(result.stdout) > 131072)
            except (OSError, subprocess.TimeoutExpired) as error:
                self.record.emit(label, reason=reason, error=type(error).__name__)
        # Preserve CRTC/connector enable state; never write/debug-test the DRM device.
        self.record.emit("drm-state", reason=reason,
                         state=observation("/sys/kernel/debug/dri/0/state", 262144))
        text = observation("/proc/interrupts")
        if "value" in text:
            text["value"] = "\n".join(line for line in text["value"].splitlines()
                                      if any(token in line for token in ("Lid Switch", "rtc_alarm")))
        self.record.emit("wake-interrupts", reason=reason, state=text)

    def request_detail(self, reason):
        try:
            self.queue.put_nowait(reason)
        except queue.Full:
            self.record.emit("detail-coalesced", reason=reason)
        return self.GLib.SOURCE_REMOVE

    def detail_worker(self):
        while not self.stop.is_set():
            try:
                reason = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.detailed(reason)
            except Exception as error:
                self.record.emit("detail-error", error=type(error).__name__)

    def prepare(self, _bus, _sender, _path, _interface, _signal, parameters):
        values = parameters.unpack()
        if len(values) != 1 or type(values[0]) is not bool:
            self.record.emit("invalid-prepare-signal")
            return
        active = values[0]
        self.record.emit("prepare-for-sleep", active=active)
        # Preserve the first post-thaw backlights before a DBus round trip.
        self.fast()
        try:
            self.lid = self.query_lid()
        except Exception as error:
            self.lid = None
            self.record.emit("lid-query-failed", error=type(error).__name__)
        self.record.emit("prepare-lid-readback", active=active, closed=self.lid)
        self.request_detail("prepare-true" if active else "prepare-false")
        if not active:
            for milliseconds in (500, 1000, 3000, 10000, 20000, 30000):
                self.GLib.timeout_add(milliseconds, self.request_detail,
                                      "resume+" + str(milliseconds) + "ms")

    def properties(self, _bus, _sender, _path, _interface, _signal, parameters):
        interface, changed, invalidated = parameters.unpack()
        if interface != MANAGER or ("LidClosed" not in changed and "LidClosed" not in invalidated):
            return
        try:
            self.lid = self.query_lid()
        except Exception as error:
            self.lid = None
            self.record.emit("lid-query-failed", error=type(error).__name__)
        self.record.emit("logind-lid", closed=self.lid)
        self.fast()
        self.request_detail("lid-closed" if self.lid else "lid-open")

    def tick(self):
        try:
            while True:
                try:
                    raw = os.read(self.fd, EVENT.size * 64)
                except BlockingIOError:
                    break
                if not raw:
                    raise RuntimeError("native lid device vanished")
                for event in lid_events(raw):
                    self.record.emit(**event)
            self.fast()
            if time.clock_gettime(time.CLOCK_BOOTTIME) - self.started >= self.duration:
                self.loop.quit()
                return self.GLib.SOURCE_REMOVE
        except Exception as error:
            self.record.emit("observation-failed", error=type(error).__name__)
            self.loop.quit()
            return self.GLib.SOURCE_REMOVE
        return self.GLib.SOURCE_CONTINUE

    def run(self):
        devices = [p for p in Path("/sys/class/input").glob("event*/device/name")
                   if small(p, 64) == "gpio-keys" and "/platform/gpio-keys/" in str(p.resolve())]
        if len(devices) != 1:
            raise RuntimeError("native Hall input identity is ambiguous")
        device = Path("/dev/input") / devices[0].parents[1].name
        self.fd, native_closed = open_native_device(device)
        self.lid = self.query_lid()
        for interface, member, arg0, callback in (
            (MANAGER, "PrepareForSleep", None, self.prepare),
            ("org.freedesktop.DBus.Properties", "PropertiesChanged", MANAGER, self.properties),
        ):
            self.subscriptions.append(self.bus.signal_subscribe(LOGIN, interface, member,
                LOGIN_PATH, arg0, self.Gio.DBusSignalFlags.NONE, callback))
        self.record.emit("ready", boot_id=small("/proc/sys/kernel/random/boot_id", 64),
                         evidence_schema=EVIDENCE_SCHEMA, input_clock=INPUT_CLOCK,
                         native_device=str(device), native_lid_closed=native_closed,
                         logind_lid_closed=self.lid, maximum_boottime_seconds=self.duration)
        worker = threading.Thread(target=self.detail_worker, daemon=True)
        worker.start()
        self.request_detail("before")
        self.GLib.timeout_add(100, self.tick)
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_args: self.loop.quit())
        try:
            self.loop.run()
        finally:
            self.stop.set()
            worker.join(timeout=7)
            for subscription in self.subscriptions:
                self.bus.signal_unsubscribe(subscription)
            os.close(self.fd)
            self.record.emit("observer-stopped")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--duration", type=int, default=360)
    args = parser.parse_args()
    if os.geteuid() != 0 or not 60 <= args.duration <= 600:
        parser.error("root and duration 60..600 seconds required")
    standalone = args.output.parent == Path("/run") and args.output.name.startswith("pds-sleep-observe-")
    nested = (args.output.name == "observations" and args.output.parent.parent == Path("/run")
              and args.output.parent.name.startswith("pds-lid-cycle-")
              and stat.S_ISDIR(args.output.parent.lstat().st_mode)
              and args.output.parent.lstat().st_uid == 0
              and not args.output.parent.lstat().st_mode & 0o077)
    if not (standalone or nested):
        parser.error("use a fresh /run/pds-sleep-observe-* directory")
    os.umask(0o077)
    args.output.mkdir(mode=0o700)  # Existing evidence is never reused or deleted.
    Observer(args.output, args.duration).run()


if __name__ == "__main__":
    main()

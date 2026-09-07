#!/usr/bin/python3
"""Temporary controller inspection. Physical inputs are read with ioctls only."""
from __future__ import annotations

import argparse
import fcntl
import glob
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import signal
import socket
import stat
import struct
import sys
import tempfile
import time

PROTOCOL = "pds-controller-test-v1"
RUNTIME = Path("/run/pocketds-controller-test")
JOURNAL = RUNTIME / "state.json"
SOCKET = RUNTIME / "control.sock"
LOCK = Path("/run/pocketds-input-mode.lock")
SERVICE = "org.shadowblip.InputPlumber"
COMPOSITE = "/org/shadowblip/InputPlumber/CompositeDevice0"
INTERFACE = "org.shadowblip.Input.CompositeDevice"
PROFILES = {"/usr/share/inputplumber/profiles/pocketds-gamepad.yaml",
            "/usr/share/inputplumber/profiles/pocketds-joymouse.yaml"}
DEVICE_IDS = {(0x1c4f, 0x0002), (0x045e, 0x028e), (0x4001, 0x0428)}
TOKEN = re.compile(r"^[0-9a-f]{32}$")
PERIOD = 1 / 60
LEASE_SECONDS = 1.5
NEUTRAL_SECONDS = 0.3
LATCH_SECONDS = 0.12
RECOVER_SECONDS = 8.0
KEY_MAX = 0x2ff
ABS_MAX = 0x3f
# These raw Pocket DS face-button codes follow the physical X/Y labels.
# The generic evdev NORTH/WEST aliases reverse them on this source; the test
# reads raw ioctls and does not receive InputPlumber's normalized events.
BUTTON_CODES = {
    "a": 304, "b": 305, "x": 307, "y": 308, "lb": 310, "rb": 311,
    "lc": 259, "rc": 260, "menu": 315, "view": 314, "l3": 317, "r3": 318,
    "aya": 261, "guide": 262, "brightness_up": 316, "brightness_down": 258,
    "left_paddle": 309, "right_paddle": 306, "aux0": 256, "aux1": 257, "aux7": 263,
}
BUTTON_NAMES = tuple(BUTTON_CODES) + ("dpad_up", "dpad_down", "dpad_left", "dpad_right")
AXIS_NAMES = ("lx", "ly", "rx", "ry", "lt", "rt")


def blank_buttons():
    return dict.fromkeys(BUTTON_NAMES, 0)


def blank_axes():
    return dict.fromkeys(AXIS_NAMES, 0.0)


class CaptureError(Exception):
    pass


class CaptureLost(CaptureError):
    """Our InputPlumber instance or intercept ownership has disappeared."""


class SourceUnavailable(CaptureError):
    """Only verified physical sources may be reopened after unplug/replug."""


class OutputNotNeutral(CaptureError):
    """Interception did not yet release the kernel-visible virtual outputs."""


class JournalError(RuntimeError):
    """Terminate into stop-post recovery if a durable state write fails."""


def ior(number, size):
    return (2 << 30) | (size << 16) | (ord("E") << 8) | number


def read_ioctl(fd, number, size):
    buffer = bytearray(size)
    fcntl.ioctl(fd, ior(number, size), buffer, True)
    return bytes(buffer)


def bits(data):
    return {index * 8 + bit for index, value in enumerate(data)
            for bit in range(8) if value & (1 << bit)}


def normalize(value, low, high, *, trigger=False):
    if high <= low or not low <= value <= high:
        raise CaptureError("手柄轴数据无效")
    result = (value - low) / (high - low)
    return max(0.0, min(1.0, result)) if trigger else max(-1.0, min(1.0, result * 2 - 1))


class PhysicalDevice:
    """EVIOCGKEY/EVIOCGABS work without competing with InputPlumber's grab."""
    def __init__(self, path, *, physical=True):
        if not re.fullmatch(r"/dev/input/event[0-9]+", path):
            raise CaptureError("手柄设备路径无效")
        self.path, self.fd = path, None
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            info = os.fstat(self.fd)
            if not stat.S_ISCHR(info.st_mode):
                raise CaptureError("手柄设备类型无效")
            self.rdev = info.st_rdev
            syspath = Path(f"/sys/dev/char/{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}").resolve()
            is_virtual = str(syspath).startswith("/sys/devices/virtual/")
            if physical == is_virtual or not str(syspath).startswith("/sys/devices/"):
                raise CaptureError("手柄设备来源不匹配")
            bus, vendor, product, version = struct.unpack("HHHH", read_ioctl(self.fd, 0x02, 8))
            self.identity = (bus, vendor, product, version)
            if physical and (vendor, product) not in DEVICE_IDS:
                raise CaptureError("手柄设备身份不匹配")
            self.supported_keys = bits(read_ioctl(self.fd, 0x21, (KEY_MAX + 8) // 8))
            self.supported_axes = bits(read_ioctl(self.fd, 0x23, (ABS_MAX + 8) // 8))
            self.axes_info = {code: struct.unpack("iiiiii", read_ioctl(self.fd, 0x40 + code, 24))
                              for code in self.supported_axes}
        except Exception:
            self.close()
            raise

    def snapshot(self):
        if os.stat(self.path, follow_symlinks=False).st_rdev != self.rdev:
            raise SourceUnavailable("手柄设备已变化")
        keys = bits(read_ioctl(self.fd, 0x18, (KEY_MAX + 8) // 8))
        axes = {code: struct.unpack("iiiiii", read_ioctl(self.fd, 0x40 + code, 24))
                for code in self.supported_axes}
        return keys, axes

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def decode_snapshots(snapshots):
    buttons, axes = blank_buttons(), blank_axes()
    any_key = False
    for keys, values in snapshots:
        any_key = any_key or bool(keys)
        for name, code in BUTTON_CODES.items():
            buttons[name] |= int(code in keys)
        hx, hy = values.get(16), values.get(17)
        if hx:
            buttons["dpad_left"] |= int(hx[0] < 0)
            buttons["dpad_right"] |= int(hx[0] > 0)
        if hy:
            buttons["dpad_up"] |= int(hy[0] < 0)
            buttons["dpad_down"] |= int(hy[0] > 0)
        # The 4001:0428 controller has Z/RZ sticks and BRAKE/GAS triggers.
        # XInput's RX/RY + Z/RZ layout is accepted only when BRAKE/GAS are absent.
        direct = 9 in values and 10 in values
        mapping = {"lx": 0, "ly": 1, "rx": 2 if direct else 3,
                   "ry": 5 if direct else 4, "lt": 10 if direct else 2,
                   "rt": 9 if direct else 5}
        for name, code in mapping.items():
            if code not in values:
                continue
            value, low, high, _fuzz, _flat, _resolution = values[code]
            current = normalize(value, low, high, trigger=name in ("lt", "rt"))
            if abs(current) > abs(axes[name]):
                axes[name] = current
    neutral = (not any_key and not any(buttons.values())
               and all(abs(axes[name]) <= 0.12 for name in ("lx", "ly", "rx", "ry"))
               and all(axes[name] <= 0.08 for name in ("lt", "rt")))
    return {"buttons": buttons, "axes": axes, "neutral": neutral}


def safe_journal(path=JOURNAL):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o022 or meta.st_size > 8192:
            raise CaptureError("独占恢复记录无效")
        data = json.loads(os.read(fd, 8193))
    finally:
        os.close(fd)
    if (not isinstance(data, dict) or data.get("protocol") != PROTOCOL
            or not isinstance(data.get("blocked"), bool)):
        raise CaptureError("独占恢复记录无效")
    if data["blocked"] and (not TOKEN.fullmatch(data.get("token", ""))
                            or data.get("previous_intercept") not in (0, 1)
                            or not isinstance(data.get("owner"), str)):
        raise CaptureError("独占恢复记录无效")
    return data


def publish(data, path=JOURNAL):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    if len(payload) > 8192:
        raise CaptureError("独占状态过大")
    fd, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class LiveBackend:
    def __init__(self):
        import dbus
        self.dbus = dbus
        self.bus = dbus.SystemBus()
        self.lock_fd = None
        self.devices = []
        self.saved = None
        self.source_paths = None
        self.last_identity_check = 0.0
        self.next_source_reload = 0.0

    def owner(self):
        return str(self.bus.get_name_owner(SERVICE))

    def get(self, name):
        proxy = self.bus.get_object(SERVICE, COMPOSITE, introspect=False)
        return proxy.Get(INTERFACE, name, dbus_interface="org.freedesktop.DBus.Properties", timeout=0.5)

    def set_intercept(self, value):
        proxy = self.bus.get_object(SERVICE, COMPOSITE, introspect=False)
        # introspect=False requires an explicit variant for Properties.Set's
        # third argument; an unwrapped UInt32 incorrectly marshals as "ssu".
        proxy.Set(INTERFACE, "InterceptMode", self.dbus.UInt32(value, variant_level=1),
                  dbus_interface="org.freedesktop.DBus.Properties", timeout=0.5)
        if int(self.get("InterceptMode")) != value:
            raise CaptureError("手柄独占状态未确认")

    def acquire(self):
        self.lock_fd = os.open(LOCK, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            meta = os.fstat(self.lock_fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0:
                raise CaptureError("输入模式锁无效")
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            os.close(self.lock_fd)
            self.lock_fd = None
            raise CaptureError("输入模式正在切换")

    def current_source_paths(self):
        paths = sorted(str(path) for path in self.get("SourceDevicePaths")
                       if str(path).startswith("/dev/input/"))
        if not paths or len(paths) > 8 or len(set(paths)) != len(paths):
            raise SourceUnavailable("手柄物理设备不可用")
        return paths

    def load_sources(self):
        paths = self.current_source_paths()
        devices = []
        try:
            for path in paths:
                devices.append(PhysicalDevice(path))
            # Either missing half would hide held controls during recovery.
            keys = set().union(*(device.supported_keys for device in devices))
            supported_axes = set().union(*(device.supported_axes for device in devices))
            identities = {(device.identity[1], device.identity[2]) for device in devices}
            required_axes = ({0, 1, 2, 5, 9, 10, 16, 17} if {9, 10}.issubset(supported_axes)
                             else {0, 1, 2, 3, 4, 5, 16, 17})
            if (not {259, 260, 261, 262, 304, 305, 307, 308, 310, 311, 314, 315, 317, 318}.issubset(keys)
                    or not required_axes.issubset(supported_axes)
                    or (0x1c4f, 0x0002) not in identities
                    or not identities.intersection({(0x4001, 0x0428), (0x045e, 0x028e)})):
                raise SourceUnavailable("手柄物理设备不完整")
        except Exception:
            for device in devices:
                device.close()
            raise
        for device in self.devices:
            device.close()
        self.devices, self.source_paths = devices, paths

    def prepare(self):
        previous = int(self.get("InterceptMode"))
        if previous not in (0, 1):
            raise CaptureError("手柄正由其他界面占用")
        profile = str(self.get("ProfilePath"))
        if profile not in PROFILES:
            raise CaptureError("当前输入模式不受支持")
        owner = self.owner()
        targets = sorted(str(path) for path in self.get("TargetDevices"))
        kinds = []
        for path in targets:
            match = re.fullmatch(r"/org/shadowblip/InputPlumber/devices/target/(dbus|keyboard|mouse|gamepad)[0-9]+", path)
            if not match:
                raise CaptureError("输入输出设备状态异常")
            kinds.append(match.group(1))
        expected = {"dbus", "keyboard", "gamepad" if profile.endswith("gamepad.yaml") else "mouse"}
        if len(kinds) != 3 or set(kinds) != expected:
            raise CaptureError("输入输出设备不完整")
        self.load_sources()
        if owner != self.owner():
            raise CaptureError("输入服务已变化")
        self.saved = {"owner": owner, "previous_intercept": previous, "profile": profile,
                      "targets": targets, "source_paths": self.source_paths}
        return dict(self.saved)

    def check_capture(self, *, configuration=True):
        if self.owner() != self.saved["owner"] or int(self.get("InterceptMode")) != 2:
            raise CaptureLost("手柄独占已中断")
        if configuration and (str(self.get("ProfilePath")) != self.saved["profile"]
                              or sorted(str(path) for path in self.get("TargetDevices")) != self.saved["targets"]):
            raise CaptureError("输入模式已变化，恢复已暂停")

    def refresh_sources(self, now):
        # Check the lease even during the reload backoff. Never adopt a new
        # composite generation's profile/targets or its prior intercept mode.
        self.check_capture()
        if now < self.next_source_reload:
            return False
        self.next_source_reload = now + 0.5
        self.load_sources()
        self.check_capture()
        return True

    def snapshot(self, now):
        self.check_capture(configuration=False)
        if now - self.last_identity_check >= 0.2:
            self.last_identity_check = now
            self.check_capture()
            if self.current_source_paths() != self.source_paths:
                raise SourceUnavailable("手柄设备已变化，等待重新接入")
        if not self.devices:
            raise SourceUnavailable("等待手柄重新接入")
        try:
            return decode_snapshots([device.snapshot() for device in self.devices])
        except OSError as exc:
            raise SourceUnavailable("等待手柄重新接入") from exc

    def capture_confirmed(self):
        if self.owner() != self.saved["owner"] or int(self.get("InterceptMode")) != 2:
            return False
        # The composite setter queues clear_state to its targets. Confirm the
        # kernel-visible output devices are neutral before claiming ACTIVE.
        expected = {kind for kind in ("gamepad", "keyboard", "mouse")
                    if any("/" + kind in path for path in self.saved["targets"])}
        found = set()
        names = {"keyboard": ("InputPlumber Keyboard",), "mouse": ("InputPlumber Mouse",),
                 "gamepad": ("Microsoft X-Box One Elite", "Microsoft Xbox Series")}
        for name_path in glob.glob("/sys/class/input/event*/device/name"):
            entry = Path(name_path)
            if not str(entry.resolve()).startswith("/sys/devices/virtual/input/"):
                continue
            name = entry.read_text().strip()
            kind = next((kind for kind in expected if name.startswith(names[kind])), None)
            if kind is None:
                continue
            device = PhysicalDevice("/dev/input/" + entry.parent.parent.name, physical=False)
            try:
                if not decode_snapshots([device.snapshot()])["neutral"]:
                    return False
                found.add(kind)
            finally:
                device.close()
        return found == expected and bool(expected)

    def restore(self):
        self.check_capture()
        # Older InputPlumber mouse.clear_state implementations reset motion,
        # but leave already-held buttons asserted. A physical release during
        # interception cannot repair that virtual state. Never declare idle
        # until the same output-neutral evidence used for ACTIVE also passes.
        if not self.capture_confirmed():
            raise OutputNotNeutral("虚拟输入尚未释放，恢复已暂停")
        self.check_capture()
        if self.current_source_paths() != self.source_paths:
            raise SourceUnavailable("手柄设备已变化，等待重新接入")
        if not decode_snapshots([device.snapshot() for device in self.devices])["neutral"]:
            raise CaptureError("请松开手柄按键")
        self.set_intercept(self.saved["previous_intercept"])

    def release(self):
        for device in self.devices:
            device.close()
        self.devices = []
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None


class Session:
    def __init__(self, backend, write=publish, clock=time.monotonic):
        self.backend, self.write, self.clock = backend, write, clock
        self.token = None
        self.status = "idle"
        self.error = ""
        self.deadline = 0.0
        self.started_at = 0.0
        self.neutral_since = None
        self.saved = {}
        self.sample = {"buttons": blank_buttons(), "axes": blank_axes(), "neutral": True}
        self.lit_until = {}

    @property
    def blocked(self):
        return self.status in ("starting", "active", "draining")

    def record(self):
        try:
            # The UI may retain a terminal error; gate readers require an
            # explicit idle receipt once the original capture no longer exists.
            self.write({"protocol": PROTOCOL, "token": self.token or "", "status": self.status if self.blocked else "idle",
                        "blocked": self.blocked, "error": self.error,
                        "updated_monotonic": self.clock(), **self.saved})
        except Exception as exc:
            raise JournalError("独占状态无法保存") from exc

    def response(self, token=None, *, status=None, error=None):
        now = self.clock()
        readable = self.blocked and not self.error
        buttons = {name: int(bool(value) or now < self.lit_until.get(name, 0))
                   for name, value in self.sample["buttons"].items()}
        return {"token": token if token is not None else self.token or "",
                "status": status or self.status, "buttons": buttons if readable else blank_buttons(),
                "axes": {name: round(value, 4) for name, value in self.sample["axes"].items()}
                if readable else blank_axes(), "error": self.error if error is None else error}

    def request(self, op, token):
        if op not in ("begin", "poll", "end") or not isinstance(token, str) or not TOKEN.fullmatch(token):
            return self.response("", status="error", error="请求无效")
        now = self.clock()
        if self.blocked and now >= self.deadline and self.status != "draining":
            self.drain()
        if self.blocked and token != self.token:
            return self.response(token, status="busy", error="另一个说明页正在使用手柄")
        if op == "begin":
            if self.blocked:
                if self.status != "draining":
                    self.deadline = now + LEASE_SECONDS
                return self.response()
            self.token, self.error = token, ""
            self.lit_until = {}
            self.sample = {"buttons": blank_buttons(), "axes": blank_axes(), "neutral": True}
            try:
                self.backend.acquire()
                self.saved = self.backend.prepare()
                self.status = "starting"
                self.started_at = self.clock()
                self.deadline = self.started_at + LEASE_SECONDS
                self.record()  # Gate and durable recovery receipt precede interception.
                self.backend.set_intercept(2)
                self.started_at = self.clock()
                self.deadline = self.started_at + LEASE_SECONDS
            except JournalError:
                self.backend.release()
                raise
            except Exception as exc:
                self.error = str(exc) if isinstance(exc, CaptureError) else "无法独占手柄输入"
                if self.status == "starting":
                    self.drain(self.error)
                else:
                    self.backend.release()
                    self.status = "error"
                return self.response(status="error")
        elif op == "poll":
            if token != self.token or not self.blocked:
                if token == self.token and self.status == "error":
                    return self.response()
                return self.response(token, status="idle", error="")
            if self.status != "draining":
                self.deadline = now + LEASE_SECONDS
        elif op == "end":
            if token != self.token or not self.blocked:
                return self.response(token, status="idle", error="")
            self.drain()
        return self.response()

    def drain(self, error=""):
        if not self.blocked:
            return
        changed = self.status != "draining" or bool(error and error != self.error)
        if self.status != "draining":
            self.status, self.neutral_since = "draining", None
        if error:
            self.error = error
        if changed:
            self.record()

    def capture_lost(self, error):
        # Never apply an old lease's intercept value to a replacement owner.
        self.error, self.status = str(error), "error"
        self.record()
        self.backend.release()

    def tick(self):
        if not self.blocked:
            return
        now = self.clock()
        if now >= self.deadline and self.status != "draining":
            self.drain()
        try:
            self.sample = self.backend.snapshot(now)
            for name, pressed in self.sample["buttons"].items():
                if pressed:
                    self.lit_until[name] = now + LATCH_SECONDS
            if self.status == "starting":
                if self.backend.capture_confirmed() and now - self.started_at >= 0.1:
                    self.status = "active"
                    self.record()
                elif now - self.started_at >= 1:
                    self.drain("手柄独占状态未确认")
            if self.status == "draining":
                if not self.sample["neutral"]:
                    self.neutral_since = None
                elif self.neutral_since is None:
                    self.neutral_since = now
                elif now - self.neutral_since >= NEUTRAL_SECONDS:
                    self.backend.restore()
                    self.status, self.error = "idle", ""
                    self.record()
                    self.backend.release()
        except JournalError:
            raise
        except CaptureLost as exc:
            self.capture_lost(exc)
        except Exception as exc:
            self.neutral_since = None
            self.sample = {"buttons": blank_buttons(), "axes": blank_axes(), "neutral": False}
            self.lit_until = {}
            error = str(exc) if isinstance(exc, CaptureError) else "手柄读取失败，恢复已暂停"
            reloaded = False
            if isinstance(exc, (SourceUnavailable, OSError)):
                try:
                    reloaded = self.backend.refresh_sources(now)
                    if reloaded:
                        self.saved["source_paths"] = list(self.backend.source_paths)
                    elif self.error:
                        error = self.error
                except CaptureLost as lost:
                    self.capture_lost(lost)
                    return
                except Exception as failed:
                    error = str(failed) if isinstance(failed, CaptureError) else "等待手柄重新接入"
            self.drain(error)
            if reloaded:
                self.error = ""
                self.record()


def recover(backend=None, *, timeout=RECOVER_SECONDS, clock=time.monotonic, sleep=time.sleep,
            read=safe_journal, write=publish):
    saved = read()
    if not saved or not saved.get("blocked"):
        if saved and saved.get("protocol") == PROTOCOL and saved.get("status") == "error":
            write({**saved, "status": "idle", "updated_monotonic": clock()})
        return True
    backend = backend or LiveBackend()
    backend.acquire()
    try:
        # Stop-post can race a new lease only if someone bypassed the canonical lock.
        current = read()
        if not current or current.get("token") != saved["token"] or not current.get("blocked"):
            return False
        if backend.owner() != saved["owner"] or int(backend.get("InterceptMode")) != 2:
            write({**saved, "status": "idle", "blocked": False,
                   "error": "原独占会话已结束", "updated_monotonic": clock()})
            return True
        backend.saved = saved
        deadline, neutral_since, sources_loaded = clock() + timeout, None, False
        while clock() < deadline:
            try:
                if not sources_loaded:
                    sources_loaded = backend.refresh_sources(clock())
                    if not sources_loaded:
                        sleep(PERIOD)
                        continue
                    backend.saved["source_paths"] = list(backend.source_paths)
                sample = backend.snapshot(clock())
                if not sample["neutral"]:
                    neutral_since = None
                elif neutral_since is None:
                    neutral_since = clock()
                elif clock() - neutral_since >= NEUTRAL_SECONDS:
                    current = read()
                    if not current or current.get("token") != saved["token"] or not current.get("blocked"):
                        return False
                    backend.restore()
                    write({**saved, "status": "idle", "blocked": False,
                           "error": "", "updated_monotonic": clock()})
                    return True
            except CaptureLost:
                current = read()
                if not current or current.get("token") != saved["token"]:
                    return False
                write({**saved, "status": "idle", "blocked": False,
                       "error": "原独占会话已结束", "updated_monotonic": clock()})
                return True
            except (SourceUnavailable, OSError):
                # A source may disappear during stop-post as well. Retry only
                # authoritative, identity-checked devices within the same bound.
                neutral_since = None
                sources_loaded = False
            except OutputNotNeutral:
                # Preserve the blocked receipt and interception on old/broken
                # outputs. Retry within the existing stop-post time bound;
                # never synthesize a physical release or report false idle.
                neutral_since = None
            sleep(PERIOD)
        return False
    finally:
        backend.release()


class Server:
    def __init__(self, session, allowed_uid):
        self.session, self.allowed_uid = session, allowed_uid
        self.selector = selectors.DefaultSelector()
        self.clients = {}
        self.stopping = False
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if SOCKET.exists():
            info = SOCKET.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0:
                raise CaptureError("控制接口路径无效")
            SOCKET.unlink()
        self.socket.bind(str(SOCKET))
        os.chown(SOCKET, 0, pwd.getpwnam("pocketds").pw_gid)
        os.chmod(SOCKET, 0o660)
        self.socket.listen(8)
        self.socket.setblocking(False)
        self.selector.register(self.socket, selectors.EVENT_READ)

    @staticmethod
    def authorized_uid(uid, allowed_uid):
        return uid in (0, allowed_uid)

    @staticmethod
    def notify_ready():
        address = os.environ.get("NOTIFY_SOCKET")
        if not address:
            return
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
            notify.connect(address)
            notify.sendall(b"READY=1")

    def close_client(self, client):
        self.selector.unregister(client)
        self.clients.pop(client, None)
        client.close()

    def event(self, key, mask):
        client = key.fileobj
        if client is self.socket:
            connection, _ = self.socket.accept()
            _pid, uid, _gid = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if not self.authorized_uid(uid, self.allowed_uid) or len(self.clients) >= 8:
                connection.close()
                return
            connection.setblocking(False)
            self.clients[connection] = {"in": bytearray(), "out": bytearray(), "deadline": time.monotonic() + 0.5}
            self.selector.register(connection, selectors.EVENT_READ)
            return
        entry = self.clients[client]
        try:
            if mask & selectors.EVENT_WRITE:
                sent = client.send(entry["out"])
                del entry["out"][:sent]
                if not entry["out"]:
                    self.close_client(client)
                return
            chunk = client.recv(4097)
            if not chunk:
                self.close_client(client)
                return
            entry["in"].extend(chunk)
            if len(entry["in"]) > 4096:
                self.close_client(client)
                return
            if b"\n" not in entry["in"]:
                return
            line, extra = bytes(entry["in"]).split(b"\n", 1)
            try:
                request = json.loads(line)
                if extra or not isinstance(request, dict) or set(request) != {"op", "token"}:
                    raise ValueError()
                response = self.session.request(request["op"], request["token"])
            except (ValueError, TypeError, KeyError):
                response = self.session.response("", status="error", error="请求无效")
            encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
            if len(encoded) > 16384:
                raise CaptureError("控制响应过大")
            entry["out"].extend(encoded)
            # Request assembly and reply delivery have separate finite bounds;
            # a legitimate slow D-Bus begin must still be allowed to reply.
            entry["deadline"] = time.monotonic() + 0.5
            self.selector.modify(client, selectors.EVENT_WRITE)
        except (OSError, CaptureError):
            self.close_client(client)

    def run(self):
        def stop(_signum, _frame):
            self.stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        self.notify_ready()
        while not self.stopping:
            self.session.tick()
            for key, mask in self.selector.select(PERIOD):
                self.event(key, mask)
            now = time.monotonic()
            for client, entry in list(self.clients.items()):
                if now >= entry["deadline"]:
                    self.close_client(client)
        # ExecStopPost owns bounded, held-neutral recovery after every exit,
        # including SIGKILL; keep the journal blocked until it verifies restore.
        self.session.drain()
        self.session.backend.release()
        for client in list(self.clients):
            self.close_client(client)
        self.socket.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise CaptureError("需要系统服务权限")
    info = RUNTIME.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise CaptureError("独占运行目录无效")
    if not recover():
        raise CaptureError("等待手柄释放，独占恢复未完成")
    if args.recover:
        return
    Server(Session(LiveBackend()), pwd.getpwnam("pocketds").pw_uid).run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc) if isinstance(exc, CaptureError) else "手柄独占服务不可用", file=sys.stderr)
        raise SystemExit(1)

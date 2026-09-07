#!/usr/bin/env python3
"""Single-session, passive Wiliwili physical-control acceptance.

The tool attaches to one already-managed Wiliwili session. It never starts,
stops, signals, grabs, reconfigures, or consumes the event queue of an input
device. It samples only current EVIOCGKEY/EVIOCGABS state, asks the person at
the device to confirm the matching Wiliwili action, then waits for that exact
session to exit naturally and verifies restoration before returning.

Evidence is intentionally kept in memory. There is no output-path argument,
self-sealed report, replayable postflight, or installer-generation claim.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import struct
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Protocol, Sequence


SESSION_PROTOCOL = "pds-game-session-v1"
INPUT_PROTOCOL = "pds-input-v1"
APP_ID = "cn.xfangfang.wiliwili"
EXPECTED_EVENT_NAME = "Microsoft X-Box One Elite 2 pad"
EXPECTED_BUS = 0x0003
EXPECTED_VENDOR = 0x045E
EXPECTED_PRODUCT = 0x0B00
EXPECTED_VERSION = 0x0001
GLFW_GUID = "030000005e040000000b000001000000"
CAPTURE_CONFIRMATION = "POCKETDS-OBSERVE-WILIWILI-CONTROLS"
OBSERVER_PASS = "WILIWILI-UI-PASS"
OBSERVER_FAIL = "WILIWILI-UI-FAIL"
EXIT_CONFIRMATION = "WILIWILI-CLOSED"
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
EVENT_RE = re.compile(r"event[0-9]+")
INPUT_RE = re.compile(r"input[0-9]+")
INSTANCE_RE = re.compile(r"[1-9][0-9]*")
INVOCATION_RE = re.compile(r"[0-9a-f]{32}")
MAX_FILE_BYTES = 512 * 1024

EV_KEY = 0x01
EV_ABS = 0x03
BTN_SOUTH = 0x130
BTN_EAST = 0x131
BTN_NORTH = 0x133
BTN_WEST = 0x134
BTN_TL = 0x136
BTN_TR = 0x137
BTN_SELECT = 0x13A
BTN_START = 0x13B
BTN_MODE = 0x13C
BTN_THUMBL = 0x13D
BTN_THUMBR = 0x13E
ABS_X = 0x00
ABS_Y = 0x01
ABS_Z = 0x02
ABS_RX = 0x03
ABS_RY = 0x04
ABS_RZ = 0x05
ABS_HAT0X = 0x10
ABS_HAT0Y = 0x11

EXPECTED_GLFW_BINDINGS = {
    "a": "b0",
    "b": "b1",
    "x": "b2",
    "y": "b3",
    "back": "b6",
    "guide": "b8",
    "start": "b7",
    "leftshoulder": "b4",
    "rightshoulder": "b5",
    "leftstick": "b9",
    "rightstick": "b10",
    "dpup": "h0.1",
    "dpright": "h0.2",
    "dpdown": "h0.4",
    "dpleft": "h0.8",
    "leftx": "a0",
    "lefty": "a1",
    "rightx": "a3",
    "righty": "a4",
    "lefttrigger": "a2",
    "righttrigger": "a5",
    "platform": "Linux",
}


class AcceptanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ControlSpec:
    name: str
    label: str
    kind: str
    code: int
    direction: int = 0
    active_threshold: float = 1.0
    neutral_threshold: float = 0.0


CONTROL_SPECS = {
    "a": ControlSpec("a", "A", "key", BTN_SOUTH),
    "b": ControlSpec("b", "B", "key", BTN_EAST),
    "x": ControlSpec("x", "X", "key", BTN_NORTH),
    "y": ControlSpec("y", "Y", "key", BTN_WEST),
    "lb": ControlSpec("lb", "LB", "key", BTN_TL),
    "rb": ControlSpec("rb", "RB", "key", BTN_TR),
    "back": ControlSpec("back", "Back", "key", BTN_SELECT),
    "start": ControlSpec("start", "Start", "key", BTN_START),
    "l3": ControlSpec("l3", "L3", "key", BTN_THUMBL),
    "r3": ControlSpec("r3", "R3", "key", BTN_THUMBR),
    "dpad-left": ControlSpec(
        "dpad-left", "D-pad Left", "axis", ABS_HAT0X, -1, 0.70, 0.0
    ),
    "dpad-right": ControlSpec(
        "dpad-right", "D-pad Right", "axis", ABS_HAT0X, 1, 0.70, 0.0
    ),
    "dpad-up": ControlSpec(
        "dpad-up", "D-pad Up", "axis", ABS_HAT0Y, -1, 0.70, 0.0
    ),
    "dpad-down": ControlSpec(
        "dpad-down", "D-pad Down", "axis", ABS_HAT0Y, 1, 0.70, 0.0
    ),
    "left-stick-left": ControlSpec(
        "left-stick-left", "Left Stick Left", "axis", ABS_X, -1, 0.70, 0.15
    ),
    "left-stick-right": ControlSpec(
        "left-stick-right", "Left Stick Right", "axis", ABS_X, 1, 0.70, 0.15
    ),
    "left-stick-up": ControlSpec(
        "left-stick-up", "Left Stick Up", "axis", ABS_Y, -1, 0.70, 0.15
    ),
    "left-stick-down": ControlSpec(
        "left-stick-down", "Left Stick Down", "axis", ABS_Y, 1, 0.70, 0.15
    ),
    "right-stick-left": ControlSpec(
        "right-stick-left", "Right Stick Left", "axis", ABS_RX, -1, 0.70, 0.15
    ),
    "right-stick-right": ControlSpec(
        "right-stick-right", "Right Stick Right", "axis", ABS_RX, 1, 0.70, 0.15
    ),
    "right-stick-up": ControlSpec(
        "right-stick-up", "Right Stick Up", "axis", ABS_RY, -1, 0.70, 0.15
    ),
    "right-stick-down": ControlSpec(
        "right-stick-down", "Right Stick Down", "axis", ABS_RY, 1, 0.70, 0.15
    ),
    "lt": ControlSpec("lt", "LT", "axis", ABS_Z, 1, 0.70, 0.05),
    "rt": ControlSpec("rt", "RT", "axis", ABS_RZ, 1, 0.70, 0.05),
}
GUIDE_NAMES = frozenset(("guide", "mode", "home"))
MONITORED_KEYS = frozenset(
    spec.code for spec in CONTROL_SPECS.values() if spec.kind == "key"
)
MONITORED_AXES = frozenset(
    spec.code for spec in CONTROL_SPECS.values() if spec.kind == "axis"
)


@dataclass(frozen=True)
class Paths:
    repo: Path
    home: Path
    proc: Path
    dev_input: Path
    sys_input: Path
    runtime: Path
    input_state: Path
    session_record: Path
    invocation_link: Path
    flatpak_root: Path
    helper_source: Path
    helper_live: Path
    supervisor_source: Path
    supervisor_live: Path
    launcher_source: Path
    launcher_live: Path
    mapping_source: Path
    mapping_installed: Path
    mapping_live: Path


@dataclass(frozen=True)
class InputState:
    token: str
    mode: str


@dataclass(frozen=True)
class InputPlumberSnapshot:
    pid: int
    starttime: str
    invocation_id: str


@dataclass(frozen=True)
class EventFdBinding:
    fd_number: int
    event_name: str
    st_dev: int
    st_ino: int
    st_rdev: int


@dataclass(frozen=True)
class SessionSnapshot:
    owner_pid: int
    owner_starttime: str
    owner_cgroup: str
    wiliwili_pid: int
    wiliwili_starttime: str
    flatpak_instance: str
    input_previous: str
    input_token: str
    session_sha256: str
    event: EventFdBinding


@dataclass(frozen=True)
class RuntimeArtifacts:
    helper_sha256: str
    supervisor_sha256: str
    launcher_sha256: str
    mapping_sha256: str
    live_database_sha256: str


@dataclass(frozen=True)
class Baseline:
    input_state: InputState
    inputplumber: InputPlumberSnapshot
    session: SessionSnapshot
    artifacts: RuntimeArtifacts


@dataclass(frozen=True)
class AxisRange:
    minimum: int
    maximum: int


@dataclass(frozen=True)
class DeviceInfo:
    bustype: int
    vendor: int
    product: int
    version: int


@dataclass(frozen=True)
class AbsState:
    value: int
    min: int
    max: int
    fuzz: int
    flat: int
    resolution: int


@dataclass(frozen=True)
class DeviceState:
    keys: frozenset[int]
    axes: Mapping[int, int]


@dataclass(frozen=True)
class Assessment:
    active: bool
    neutral: bool
    magnitude: float
    unexpected: bool


class StateDevice(Protocol):
    name: str
    info: DeviceInfo

    def capabilities(self, *, absinfo: bool = False) -> Mapping[int, Sequence[int]]:
        ...

    def active_keys(self, *, verbose: bool = False) -> Sequence[int]:
        ...

    def absinfo(self, code: int) -> Any:
        ...

    def close(self) -> None:
        ...


def input_ioctl(direction: int, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("E") << 8) | number


def input_read_ioctl(number: int, size: int) -> int:
    return input_ioctl(2, number, size)


def bitmap_codes(bitmap: bytes) -> list[int]:
    return [
        code
        for code in range(len(bitmap) * 8)
        if bitmap[code // 8] & (1 << (code % 8))
    ]


class IoctlStateDevice:
    """O_RDONLY current-state view; never reads this client's event queue."""

    KEY_BITMAP_BYTES = (0x2FF + 8) // 8
    ABS_BITMAP_BYTES = (0x3F + 8) // 8
    NAME_BYTES = 256

    def __init__(self, path: Path, expected: EventFdBinding) -> None:
        flags = os.O_RDONLY | os.O_NONBLOCK
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise AcceptanceError("virtual gamepad could not be opened read-only") from exc
        self.fd = descriptor
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISCHR(metadata.st_mode) or (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_rdev,
            ) != (expected.st_dev, expected.st_ino, expected.st_rdev):
                raise AcceptanceError(
                    "opened gamepad no longer matches Wiliwili's descriptor"
                )
            identity = bytearray(8)
            fcntl.ioctl(descriptor, input_read_ioctl(0x02, len(identity)), identity, True)
            self.info = DeviceInfo(*struct.unpack("=HHHH", identity))
            name = bytearray(self.NAME_BYTES)
            fcntl.ioctl(descriptor, input_read_ioctl(0x06, len(name)), name, True)
            self.name = bytes(name).split(b"\0", 1)[0].decode("utf-8", errors="strict")
            key_bits = bytearray(self.KEY_BITMAP_BYTES)
            abs_bits = bytearray(self.ABS_BITMAP_BYTES)
            fcntl.ioctl(
                descriptor,
                input_read_ioctl(0x20 + EV_KEY, len(key_bits)),
                key_bits,
                True,
            )
            fcntl.ioctl(
                descriptor,
                input_read_ioctl(0x20 + EV_ABS, len(abs_bits)),
                abs_bits,
                True,
            )
            self._capabilities = {
                EV_KEY: bitmap_codes(bytes(key_bits)),
                EV_ABS: bitmap_codes(bytes(abs_bits)),
            }
        except AcceptanceError:
            self.close()
            raise
        except (OSError, UnicodeError, struct.error) as exc:
            self.close()
            raise AcceptanceError("virtual gamepad ioctl identity is unavailable") from exc

    def capabilities(self, *, absinfo: bool = False) -> Mapping[int, Sequence[int]]:
        del absinfo
        return self._capabilities

    def active_keys(self, *, verbose: bool = False) -> Sequence[int]:
        del verbose
        bitmap = bytearray(self.KEY_BITMAP_BYTES)
        try:
            fcntl.ioctl(self.fd, input_read_ioctl(0x18, len(bitmap)), bitmap, True)
        except OSError as exc:
            raise AcceptanceError("current key state is unavailable") from exc
        return bitmap_codes(bytes(bitmap))

    def absinfo(self, code: int) -> AbsState:
        if code not in MONITORED_AXES:
            raise AcceptanceError("unapproved axis state was requested")
        payload = bytearray(24)
        try:
            fcntl.ioctl(
                self.fd, input_read_ioctl(0x40 + code, len(payload)), payload, True
            )
            values = struct.unpack("=iiiiii", payload)
        except (OSError, struct.error) as exc:
            raise AcceptanceError("current axis state is unavailable") from exc
        return AbsState(*values)

    def close(self) -> None:
        if getattr(self, "fd", -1) >= 0:
            os.close(self.fd)
            self.fd = -1


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def strict_json_loads(text: str) -> object:
    def reject_constant(value: str) -> object:
        raise ValueError(f"non-finite JSON constant: {value}")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(
        text, parse_constant=reject_constant, object_pairs_hook=reject_duplicates
    )


def read_regular(
    path: Path,
    *,
    expected_uid: int | None,
    expected_mode: int | None,
    maximum: int,
    allow_empty: bool = False,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AcceptanceError("required file is unavailable or linked") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (expected_uid is not None and before.st_uid != expected_uid)
            or (
                expected_mode is not None
                and stat.S_IMODE(before.st_mode) != expected_mode
            )
            or before.st_size > maximum
            or (not allow_empty and before.st_size == 0)
        ):
            raise AcceptanceError("required file identity is unsafe")
        remaining = before.st_size
        content = bytearray()
        while remaining:
            block = os.read(descriptor, min(65536, remaining))
            if not block:
                raise AcceptanceError("required file changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise AcceptanceError("required file grew while reading")
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise AcceptanceError("required file changed while reading")
        return bytes(content)
    finally:
        os.close(descriptor)


def read_proc_text(path: Path, maximum: int = 64 * 1024) -> str:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise AcceptanceError("process identity is unavailable") from exc
    try:
        content = os.read(descriptor, maximum + 1)
        if len(content) > maximum:
            raise AcceptanceError("process identity exceeded its bound")
    finally:
        os.close(descriptor)
    try:
        return content.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise AcceptanceError("process identity is not UTF-8") from exc


def read_dynamic_regular(path: Path, *, expected_uid: int, maximum: int) -> bytes:
    """Read a bounded sysfs-style file whose reported st_size is not content size."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AcceptanceError("dynamic evidence file is unavailable or linked") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != expected_uid
        ):
            raise AcceptanceError("dynamic evidence file identity is unsafe")
        content = os.read(descriptor, maximum + 1)
        if len(content) > maximum:
            raise AcceptanceError("dynamic evidence file exceeded its bound")
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_mtime_ns) != (
            before.st_dev,
            before.st_ino,
            before.st_mtime_ns,
        ):
            raise AcceptanceError("dynamic evidence file changed while reading")
        return content
    finally:
        os.close(descriptor)


def validate_private_runtime(path: Path, uid: int) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AcceptanceError("systemd user runtime directory is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != uid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise AcceptanceError("systemd user runtime directory is unsafe")


def default_paths() -> Paths:
    uid = os.getuid()
    account = pwd.getpwuid(uid)
    if uid == 0 or account.pw_name != "pocketds":
        raise AcceptanceError("run attended acceptance as the pocketds desktop user")
    home = Path(account.pw_dir)
    runtime = Path("/run/user") / str(uid)
    supplied_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if supplied_runtime and Path(supplied_runtime) != runtime:
        raise AcceptanceError("XDG_RUNTIME_DIR does not identify this systemd user")
    validate_private_runtime(runtime, uid)
    repo = Path(__file__).resolve().parents[1]
    return Paths(
        repo=repo,
        home=home,
        proc=Path("/proc"),
        dev_input=Path("/dev/input"),
        sys_input=Path("/sys/class/input"),
        runtime=runtime,
        input_state=Path("/run/pocketds-input-mode/state"),
        session_record=runtime / "pocketds-game-session/session.json",
        invocation_link=Path("/run/systemd/units/invocation:inputplumber.service"),
        flatpak_root=runtime / ".flatpak",
        helper_source=repo / "components/emulation/pocketds-input-mode",
        helper_live=Path("/usr/local/libexec/pocketds-input-mode"),
        supervisor_source=repo / "components/emulation/pocketds-game-session.py",
        supervisor_live=Path("/usr/local/libexec/pocketds-game-session"),
        launcher_source=repo / "components/wiliwili/pocketds-wiliwili",
        launcher_live=home / ".local/bin/pocketds-wiliwili",
        mapping_source=repo / "components/wiliwili/gamecontrollerdb.txt",
        mapping_installed=home
        / ".local/share/pocketds-linux-kit/wiliwili/gamecontrollerdb.txt",
        mapping_live=home
        / ".var/app/cn.xfangfang.wiliwili/config/wiliwili/gamecontrollerdb.txt",
    )


def proc_starttime(proc: Path, pid: int) -> str:
    line = read_proc_text(proc / str(pid) / "stat")
    closing = line.rfind(")")
    fields = line[closing + 1 :].split() if closing >= 0 else []
    if len(fields) <= 19 or not fields[19].isdigit():
        raise AcceptanceError("process start time is malformed")
    return fields[19]


def proc_cgroup(proc: Path, pid: int) -> str:
    lines = read_proc_text(proc / str(pid) / "cgroup").splitlines()
    values = [line.split(":", 2)[2] for line in lines if line.startswith("0::")]
    if len(values) != 1 or not values[0].startswith("/") or ".." in values[0].split("/"):
        raise AcceptanceError("process cgroup identity is malformed")
    return values[0].rstrip("/") or "/"


def proc_status(proc: Path, pid: int) -> tuple[int, int]:
    fields: dict[str, str] = {}
    for line in read_proc_text(proc / str(pid) / "status").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key] = value.strip()
    try:
        return int(fields["Uid"].split()[0]), int(fields["PPid"].split()[0])
    except (KeyError, IndexError, ValueError) as exc:
        raise AcceptanceError("process status identity is malformed") from exc


def proc_cmdline(proc: Path, pid: int) -> list[str]:
    raw = read_proc_text(proc / str(pid) / "cmdline")
    values = raw.rstrip("\0").split("\0") if raw else []
    if not values or any(not value or "\n" in value for value in values):
        raise AcceptanceError("process command line is malformed")
    return values


def processes_named(proc: Path, name: str) -> list[int]:
    try:
        entries = list(proc.iterdir())
    except OSError as exc:
        raise AcceptanceError("process table is unavailable") from exc
    result: list[int] = []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            comm = read_proc_text(entry / "comm", maximum=256).strip()
        except AcceptanceError:
            if not entry.exists():
                continue
            raise
        if comm == name:
            result.append(int(entry.name))
    return sorted(result)


def unique_process_named(proc: Path, name: str) -> int:
    pids = processes_named(proc, name)
    if len(pids) != 1:
        raise AcceptanceError(f"exactly one {name} process is required")
    return pids[0]


def read_input_state(path: Path, *, expected_uid: int) -> InputState:
    content = read_regular(
        path, expected_uid=expected_uid, expected_mode=0o640, maximum=128
    )
    try:
        parts = content.decode("ascii", errors="strict").split()
    except UnicodeError as exc:
        raise AcceptanceError("input state is not ASCII") from exc
    if (
        len(parts) != 3
        or parts[0] != INPUT_PROTOCOL
        or UUID_RE.fullmatch(parts[1]) is None
        or parts[2] not in ("gamepad", "joymouse")
    ):
        raise AcceptanceError("input state is malformed")
    return InputState(parts[1], parts[2])


def inputplumber_snapshot(paths: Paths, *, root_uid: int = 0) -> InputPlumberSnapshot:
    pid = unique_process_named(paths.proc, "inputplumber")
    uid, ppid = proc_status(paths.proc, pid)
    if uid != root_uid or ppid != 1:
        raise AcceptanceError("InputPlumber process ownership is invalid")
    if proc_cgroup(paths.proc, pid) != "/system.slice/inputplumber.service":
        raise AcceptanceError("InputPlumber process is outside its service cgroup")
    try:
        metadata = paths.invocation_link.lstat()
        invocation = os.readlink(paths.invocation_link)
    except OSError as exc:
        raise AcceptanceError("InputPlumber invocation identity is unavailable") from exc
    if (
        not stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != root_uid
        or INVOCATION_RE.fullmatch(invocation) is None
    ):
        raise AcceptanceError("InputPlumber invocation identity is invalid")
    return InputPlumberSnapshot(pid, proc_starttime(paths.proc, pid), invocation)


def read_session(paths: Paths, *, user_uid: int) -> tuple[dict[str, object], bytes]:
    content = read_regular(
        paths.session_record,
        expected_uid=user_uid,
        expected_mode=0o600,
        maximum=32 * 1024,
    )
    try:
        record = strict_json_loads(content.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise AcceptanceError("managed Wiliwili session is malformed") from exc
    required = {
        "backend",
        "flatpak_instance",
        "input_previous",
        "input_token",
        "name",
        "owner_cgroup",
        "owner_pid",
        "owner_starttime",
        "phase",
        "protocol",
        "token",
    }
    if type(record) is not dict or set(record) != required:
        raise AcceptanceError("managed Wiliwili session fields are invalid")
    if (
        record["protocol"] != SESSION_PROTOCOL
        or record["backend"] != "flatpak"
        or record["name"] != "wiliwili"
        or record["phase"] != "running"
        or record["input_previous"] != "joymouse"
        or type(record["owner_pid"]) is not int
        or int(record["owner_pid"]) <= 0
        or type(record["owner_starttime"]) is not str
        or not str(record["owner_starttime"]).isdigit()
        or type(record["owner_cgroup"]) is not str
        or UUID_RE.fullmatch(str(record["token"])) is None
        or UUID_RE.fullmatch(str(record["input_token"])) is None
        or INSTANCE_RE.fullmatch(str(record["flatpak_instance"])) is None
    ):
        raise AcceptanceError("managed Wiliwili session identity is invalid")
    owner_pid = int(record["owner_pid"])
    if proc_starttime(paths.proc, owner_pid) != record["owner_starttime"]:
        raise AcceptanceError("managed Wiliwili owner PID was reused")
    if proc_cgroup(paths.proc, owner_pid) != record["owner_cgroup"]:
        raise AcceptanceError("managed Wiliwili owner changed cgroup")
    owner_uid, _owner_ppid = proc_status(paths.proc, owner_pid)
    if owner_uid != user_uid:
        raise AcceptanceError("managed Wiliwili owner UID is invalid")
    argv = proc_cmdline(paths.proc, owner_pid)
    tail = [
        "run",
        "--backend",
        "flatpak",
        "--name",
        "wiliwili",
        "--app-id",
        APP_ID,
        "--",
    ]
    if len(argv) < len(tail) + 1 or argv[-len(tail) :] != tail:
        raise AcceptanceError("managed Wiliwili owner command is invalid")
    if argv[-len(tail) - 1] != str(paths.supervisor_live):
        raise AcceptanceError("managed Wiliwili owner executable is invalid")
    return record, content


def read_flatpak_info(paths: Paths, instance: str, *, user_uid: int) -> None:
    content = read_regular(
        paths.flatpak_root / instance / "info",
        expected_uid=user_uid,
        expected_mode=0o644,
        maximum=64 * 1024,
    )
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise AcceptanceError("Flatpak instance info is not UTF-8") from exc
    names = re.findall(r"(?m)^name=([^\n]+)$", text)
    ids = re.findall(r"(?m)^instance-id=([^\n]+)$", text)
    if names != [APP_ID] or ids != [instance]:
        raise AcceptanceError("Flatpak instance does not identify Wiliwili")


def event_bindings_for_process(
    paths: Paths, pid: int
) -> tuple[str, list[EventFdBinding]]:
    start_before = proc_starttime(paths.proc, pid)
    try:
        entries = list((paths.proc / str(pid) / "fd").iterdir())
    except OSError as exc:
        raise AcceptanceError("Wiliwili descriptors are unavailable") from exc
    bindings: list[EventFdBinding] = []
    for entry in sorted(entries, key=lambda value: value.name):
        if not entry.name.isdigit():
            continue
        try:
            target_before = os.readlink(entry)
        except OSError:
            continue
        candidate = Path(target_before)
        if candidate.parent != paths.dev_input or EVENT_RE.fullmatch(candidate.name) is None:
            continue
        try:
            metadata = os.stat(entry, follow_symlinks=True)
            target_after = os.readlink(entry)
        except OSError as exc:
            raise AcceptanceError("Wiliwili input descriptor changed") from exc
        if target_after != target_before or not stat.S_ISCHR(metadata.st_mode):
            raise AcceptanceError("Wiliwili input descriptor identity changed")
        bindings.append(
            EventFdBinding(
                int(entry.name),
                candidate.name,
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_rdev,
            )
        )
    start_after = proc_starttime(paths.proc, pid)
    if start_after != start_before:
        raise AcceptanceError("Wiliwili PID changed during descriptor inspection")
    return start_before, bindings


def read_sysfs_identity(paths: Paths, event_name: str) -> tuple[str, int, int, int, int]:
    root = paths.sys_input / event_name / "device"

    def text(relative: str) -> str:
        content = read_dynamic_regular(root / relative, expected_uid=0, maximum=256)
        try:
            return content.decode("utf-8", errors="strict").strip()
        except UnicodeError as exc:
            raise AcceptanceError("input sysfs identity is not UTF-8") from exc

    try:
        return (
            text("name"),
            int(text("id/bustype"), 16),
            int(text("id/vendor"), 16),
            int(text("id/product"), 16),
            int(text("id/version"), 16),
        )
    except ValueError as exc:
        raise AcceptanceError("input sysfs identity is malformed") from exc


def virtual_input_device_path(paths: Paths, event_name: str) -> Path:
    """Bind the selected event to an InputPlumber/uinput virtual device.

    VID/PID/name are not unique: a physically attached Elite 2 can expose the
    same tuple.  The managed Pocket DS target observed on-device lives directly
    under /sys/devices/virtual/input/inputN, so reject physical USB/Bluetooth
    ancestry and nested lookalikes before treating the descriptor as built-in.
    """
    event_device = paths.sys_input / event_name / "device"
    virtual_root = paths.sys_input.parents[1] / "devices/virtual/input"
    try:
        resolved_device = event_device.resolve(strict=True)
        resolved_root = virtual_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AcceptanceError("input sysfs ancestry is unavailable") from exc
    if (
        resolved_device.parent != resolved_root
        or INPUT_RE.fullmatch(resolved_device.name) is None
    ):
        raise AcceptanceError("Wiliwili input is not the managed virtual gamepad")
    return resolved_device


def matching_virtual_gamepads(
    paths: Paths, expected: tuple[str, int, int, int, int]
) -> tuple[str, ...]:
    """Enumerate direct-virtual events exposing the expected public tuple.

    The pinned xpad target has no creator-specific phys/uniq value. Requiring
    the Wiliwili event to be the only direct-virtual match rejects stale or
    parallel lookalike uinput devices; the attended no-other-uinput rule covers
    the remaining unique-replacement boundary.
    """
    try:
        entries = sorted(paths.sys_input.iterdir(), key=lambda value: value.name)
    except OSError as exc:
        raise AcceptanceError("input sysfs enumeration is unavailable") from exc
    matches: list[str] = []
    for entry in entries:
        if EVENT_RE.fullmatch(entry.name) is None:
            continue
        try:
            virtual_input_device_path(paths, entry.name)
        except AcceptanceError as exc:
            if str(exc) == "Wiliwili input is not the managed virtual gamepad":
                continue
            raise
        if read_sysfs_identity(paths, entry.name) == expected:
            matches.append(entry.name)
    return tuple(matches)


def select_wiliwili_event(paths: Paths, pid: int) -> tuple[str, EventFdBinding]:
    starttime, bindings = event_bindings_for_process(paths, pid)
    if len(bindings) != 1:
        raise AcceptanceError("Wiliwili must hold exactly one input event descriptor")
    binding = bindings[0]
    expected = (
        EXPECTED_EVENT_NAME,
        EXPECTED_BUS,
        EXPECTED_VENDOR,
        EXPECTED_PRODUCT,
        EXPECTED_VERSION,
    )
    ancestry = virtual_input_device_path(paths, binding.event_name)
    if read_sysfs_identity(paths, binding.event_name) != expected:
        raise AcceptanceError("Wiliwili did not open the expected virtual gamepad")
    if matching_virtual_gamepads(paths, expected) != (binding.event_name,):
        raise AcceptanceError("expected virtual gamepad identity is not unique")
    if virtual_input_device_path(paths, binding.event_name) != ancestry:
        raise AcceptanceError("Wiliwili virtual gamepad ancestry changed")
    node = paths.dev_input / binding.event_name
    try:
        metadata = node.lstat()
    except OSError as exc:
        raise AcceptanceError("Wiliwili virtual gamepad node is unavailable") from exc
    if not stat.S_ISCHR(metadata.st_mode) or (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_rdev,
    ) != (binding.st_dev, binding.st_ino, binding.st_rdev):
        raise AcceptanceError("gamepad node does not match Wiliwili's descriptor")
    if matching_virtual_gamepads(paths, expected) != (binding.event_name,):
        raise AcceptanceError("virtual gamepad identity set changed during inspection")
    return starttime, binding


def managed_session(paths: Paths, *, user_uid: int) -> SessionSnapshot:
    record, content = read_session(paths, user_uid=user_uid)
    instance = str(record["flatpak_instance"])
    read_flatpak_info(paths, instance, user_uid=user_uid)
    pid = unique_process_named(paths.proc, "wiliwili")
    uid, _ppid = proc_status(paths.proc, pid)
    if uid != user_uid:
        raise AcceptanceError("Wiliwili UID is invalid")
    expected_cgroup = f"/app.slice/app-flatpak-{APP_ID}-{instance}.scope"
    if not proc_cgroup(paths.proc, pid).endswith(expected_cgroup):
        raise AcceptanceError("Wiliwili is outside its recorded Flatpak instance")
    starttime, event = select_wiliwili_event(paths, pid)
    return SessionSnapshot(
        owner_pid=int(record["owner_pid"]),
        owner_starttime=str(record["owner_starttime"]),
        owner_cgroup=str(record["owner_cgroup"]),
        wiliwili_pid=pid,
        wiliwili_starttime=starttime,
        flatpak_instance=instance,
        input_previous=str(record["input_previous"]),
        input_token=str(record["input_token"]),
        session_sha256=sha256(content),
        event=event,
    )


def parse_mapping(content: bytes) -> str:
    try:
        rows = content.decode("utf-8", errors="strict").splitlines()
    except UnicodeError as exc:
        raise AcceptanceError("GLFW mapping source is not UTF-8") from exc
    rows = [row for row in rows if row and not row.startswith("#")]
    if len(rows) != 1 or not rows[0].endswith(","):
        raise AcceptanceError("GLFW mapping source is not exactly one row")
    fields = rows[0].split(",")
    if fields[:2] != [GLFW_GUID, "InputPlumber Xbox Elite 2"]:
        raise AcceptanceError("GLFW mapping identity is invalid")
    bindings: dict[str, str] = {}
    for field in fields[2:-1]:
        if field.count(":") != 1:
            raise AcceptanceError("GLFW mapping field is malformed")
        key, value = field.split(":", 1)
        if not key or not value or key in bindings:
            raise AcceptanceError("GLFW mapping field is duplicated")
        bindings[key] = value
    if bindings != EXPECTED_GLFW_BINDINGS:
        raise AcceptanceError("GLFW mapping bindings are not exact")
    return rows[0]


def runtime_artifacts(
    paths: Paths, *, user_uid: int, root_uid: int = 0
) -> RuntimeArtifacts:
    pairs = (
        (paths.helper_source, user_uid, 0o755, paths.helper_live, root_uid, 0o755),
        (
            paths.supervisor_source,
            user_uid,
            0o755,
            paths.supervisor_live,
            root_uid,
            0o755,
        ),
        (
            paths.launcher_source,
            user_uid,
            0o755,
            paths.launcher_live,
            user_uid,
            0o755,
        ),
    )
    digests: list[str] = []
    for source, source_uid, source_mode, live, live_uid, live_mode in pairs:
        source_content = read_regular(
            source,
            expected_uid=source_uid,
            expected_mode=source_mode,
            maximum=MAX_FILE_BYTES,
        )
        live_content = read_regular(
            live,
            expected_uid=live_uid,
            expected_mode=live_mode,
            maximum=MAX_FILE_BYTES,
        )
        if live_content != source_content:
            raise AcceptanceError("installed Wiliwili input runtime differs from source")
        digests.append(sha256(source_content))
    mapping = read_regular(
        paths.mapping_source,
        expected_uid=user_uid,
        expected_mode=0o644,
        maximum=MAX_FILE_BYTES,
    )
    installed = read_regular(
        paths.mapping_installed,
        expected_uid=user_uid,
        expected_mode=0o644,
        maximum=MAX_FILE_BYTES,
    )
    live_database = read_regular(
        paths.mapping_live,
        expected_uid=user_uid,
        expected_mode=0o644,
        maximum=MAX_FILE_BYTES,
    )
    row = parse_mapping(mapping)
    if installed != mapping:
        raise AcceptanceError("installed Wiliwili mapping source has drifted")
    try:
        live_rows = live_database.decode("utf-8", errors="strict").splitlines()
    except UnicodeError as exc:
        raise AcceptanceError("live Wiliwili mapping database is not UTF-8") from exc
    matching = [item for item in live_rows if item.startswith(GLFW_GUID + ",")]
    if matching != [row]:
        raise AcceptanceError("live Wiliwili mapping is stale or duplicated")
    return RuntimeArtifacts(
        helper_sha256=digests[0],
        supervisor_sha256=digests[1],
        launcher_sha256=digests[2],
        mapping_sha256=sha256(mapping),
        live_database_sha256=sha256(live_database),
    )


def helper_wait(paths: Paths, mode: str) -> None:
    if mode not in ("gamepad", "joymouse"):
        raise AcceptanceError("invalid helper wait mode")
    try:
        result = subprocess.run(
            [str(paths.helper_live), "wait", mode],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "LC_ALL": "C",
                "HOME": str(paths.home),
                "XDG_RUNTIME_DIR": str(paths.runtime),
            },
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AcceptanceError("read-only input runtime verification failed") from exc
    if result.returncode != 0 or len(result.stdout) > 4096 or len(result.stderr) > 4096:
        raise AcceptanceError("InputPlumber did not reach the expected stable mode")


def preflight(paths: Paths, *, user_uid: int | None = None) -> Baseline:
    uid = os.getuid() if user_uid is None else user_uid
    artifacts = runtime_artifacts(paths, user_uid=uid)
    helper_wait(paths, "gamepad")
    state = read_input_state(paths.input_state, expected_uid=uid)
    if state.mode != "gamepad":
        raise AcceptanceError("Wiliwili is not in native gamepad mode")
    plumbing = inputplumber_snapshot(paths)
    session = managed_session(paths, user_uid=uid)
    if state.token != session.input_token:
        raise AcceptanceError("managed Wiliwili no longer owns the input lease")
    return Baseline(state, plumbing, session, artifacts)


def runtime_guard(paths: Paths, baseline: Baseline, *, user_uid: int | None = None) -> None:
    uid = os.getuid() if user_uid is None else user_uid
    if read_input_state(paths.input_state, expected_uid=uid) != baseline.input_state:
        raise AcceptanceError("input lease changed during physical acceptance")
    if inputplumber_snapshot(paths) != baseline.inputplumber:
        raise AcceptanceError("InputPlumber changed during physical acceptance")
    if managed_session(paths, user_uid=uid) != baseline.session:
        raise AcceptanceError("managed Wiliwili changed during physical acceptance")
    if runtime_artifacts(paths, user_uid=uid) != baseline.artifacts:
        raise AcceptanceError("Wiliwili input runtime changed during physical acceptance")


def get_control_spec(name: str) -> ControlSpec:
    value = name.strip().lower()
    if value in GUIDE_NAMES:
        raise AcceptanceError(
            "Guide changes the system input mode and requires its separate transition test"
        )
    try:
        return CONTROL_SPECS[value]
    except KeyError as exc:
        raise AcceptanceError(f"unknown Wiliwili control: {name}") from exc


def validate_device(device: StateDevice) -> dict[int, AxisRange]:
    info = device.info
    if (
        device.name != EXPECTED_EVENT_NAME
        or int(info.bustype) != EXPECTED_BUS
        or int(info.vendor) != EXPECTED_VENDOR
        or int(info.product) != EXPECTED_PRODUCT
        or int(info.version) != EXPECTED_VERSION
    ):
        raise AcceptanceError("opened input device identity is wrong")
    capabilities = device.capabilities(absinfo=False)
    keys = {int(item) for item in capabilities.get(EV_KEY, ())}
    axes = {int(item) for item in capabilities.get(EV_ABS, ())}
    if not MONITORED_KEYS.issubset(keys) or not MONITORED_AXES.issubset(axes):
        raise AcceptanceError("opened input device lacks required controls")
    ranges: dict[int, AxisRange] = {}
    for code in MONITORED_AXES:
        item = device.absinfo(code)
        minimum, maximum = int(item.min), int(item.max)
        if minimum >= maximum:
            raise AcceptanceError("opened input device axis range is invalid")
        ranges[code] = AxisRange(minimum, maximum)
    return ranges


def snapshot_device(device: StateDevice) -> DeviceState:
    return DeviceState(
        frozenset(int(value) for value in device.active_keys(verbose=False)),
        {code: int(device.absinfo(code).value) for code in MONITORED_AXES},
    )


def axis_magnitude(value: int, limits: AxisRange, direction: int) -> float:
    center = (limits.minimum + limits.maximum) / 2.0
    if limits.minimum >= 0:
        center = float(limits.minimum)
    if direction > 0:
        denominator = limits.maximum - center
        return max(0.0, (value - center) / denominator) if denominator else 0.0
    denominator = center - limits.minimum
    return max(0.0, (center - value) / denominator) if denominator else 0.0


def neutral_magnitude(value: int, limits: AxisRange) -> float:
    center = (limits.minimum + limits.maximum) / 2.0
    if limits.minimum >= 0:
        center = float(limits.minimum)
    denominator = max(limits.maximum - center, center - limits.minimum)
    return abs(value - center) / denominator if denominator else 1.0


def assess_state(
    state: DeviceState, spec: ControlSpec, ranges: Mapping[int, AxisRange]
) -> Assessment:
    unexpected = False
    if spec.kind == "key":
        active = spec.code in state.keys
        neutral = not active
        magnitude = 1.0 if active else 0.0
    else:
        value = state.axes[spec.code]
        limits = ranges[spec.code]
        magnitude = axis_magnitude(value, limits, spec.direction)
        opposite = axis_magnitude(value, limits, -spec.direction)
        active = magnitude >= spec.active_threshold
        neutral = neutral_magnitude(value, limits) <= spec.neutral_threshold
        if opposite >= spec.active_threshold:
            unexpected = True
    allowed_keys = {spec.code} if spec.kind == "key" else set()
    if state.keys - allowed_keys:
        unexpected = True
    for code, value in state.axes.items():
        if spec.kind == "axis" and code == spec.code:
            continue
        limits = ranges[code]
        threshold = (
            0.0
            if code in (ABS_HAT0X, ABS_HAT0Y)
            else (0.05 if limits.minimum >= 0 else 0.20)
        )
        if neutral_magnitude(value, limits) > threshold:
            unexpected = True
            break
    return Assessment(active, neutral, min(magnitude, 1.5), unexpected)


def wait_for_phase(
    device: StateDevice,
    spec: ControlSpec,
    ranges: Mapping[int, AxisRange],
    *,
    phase: str,
    timeout: float,
    poll_interval: float,
    stable_polls: int,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    deadline = monotonic() + timeout
    stable = 0
    seen_match = False
    while monotonic() < deadline:
        assessment = assess_state(snapshot_device(device), spec, ranges)
        if assessment.unexpected:
            return "unexpected-control"
        matches = assessment.active if phase == "active" else assessment.neutral
        if matches:
            seen_match = True
            stable += 1
            if stable >= stable_polls:
                return "pass"
        else:
            if seen_match:
                return "sampled-level-instability"
            stable = 0
        sleep(poll_interval)
    return "activation-timeout" if phase == "active" else "recenter-timeout"


def capture_control(
    device: StateDevice,
    spec: ControlSpec,
    *,
    rounds: int,
    timeout: float,
    poll_interval: float,
    stable_polls: int,
    announce: Callable[[str], None] = print,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    ranges = validate_device(device)
    initial = assess_state(snapshot_device(device), spec, ranges)
    if not initial.neutral or initial.unexpected:
        return "initial-state-not-neutral"
    for number in range(1, rounds + 1):
        announce(
            f"[{number}/{rounds}] Press and hold the Pocket DS built-in "
            f"{spec.label}; do not use an external controller."
        )
        status = wait_for_phase(
            device,
            spec,
            ranges,
            phase="active",
            timeout=timeout,
            poll_interval=poll_interval,
            stable_polls=stable_polls,
            monotonic=monotonic,
            sleep=sleep,
        )
        if status != "pass":
            return status
        announce(
            f"[{number}/{rounds}] Release the Pocket DS built-in {spec.label} "
            "and let it recenter."
        )
        status = wait_for_phase(
            device,
            spec,
            ranges,
            phase="neutral",
            timeout=timeout,
            poll_interval=poll_interval,
            stable_polls=stable_polls,
            monotonic=monotonic,
            sleep=sleep,
        )
        if status != "pass":
            return status
    return "pass"


def observer_confirmation(spec: ControlSpec, input_fn: Callable[[str], str] = input) -> bool:
    answer = input_fn(
        f"Did only the Pocket DS built-in {spec.label} perform the correct "
        "Wiliwili action, with no external controller connected? "
        f"Type {OBSERVER_PASS} or {OBSERVER_FAIL}: "
    )
    if answer == OBSERVER_PASS:
        return True
    if answer == OBSERVER_FAIL:
        return False
    raise AcceptanceError("observer confirmation token is invalid")


def process_identity_exited(proc: Path, pid: int, starttime: str) -> bool:
    try:
        current = proc_starttime(proc, pid)
    except AcceptanceError:
        if not (proc / str(pid)).exists():
            return True
        raise
    return current != starttime


def flatpak_instance_absent(paths: Paths, instance: str) -> bool:
    path = paths.flatpak_root / instance
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise AcceptanceError("Flatpak instance exit state is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise AcceptanceError("Flatpak instance exit path is linked")
    return False


def session_record_absent(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise AcceptanceError("managed-session cleanup state is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise AcceptanceError("managed-session cleanup path is linked")
    return False


def wait_for_natural_exit(
    paths: Paths,
    baseline: Baseline,
    *,
    timeout: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = monotonic() + timeout
    session = baseline.session
    while monotonic() < deadline:
        if (
            process_identity_exited(
                paths.proc, session.wiliwili_pid, session.wiliwili_starttime
            )
            and process_identity_exited(
                paths.proc, session.owner_pid, session.owner_starttime
            )
            and flatpak_instance_absent(paths, session.flatpak_instance)
            and session_record_absent(paths.session_record)
        ):
            return
        sleep(0.1)
    raise AcceptanceError("exact managed Wiliwili session did not exit and clean up in time")


def verify_restoration(paths: Paths, baseline: Baseline, *, user_uid: int) -> None:
    if baseline.session.input_previous != "joymouse":
        raise AcceptanceError("physical acceptance did not begin from desktop mouse mode")
    helper_wait(paths, "joymouse")
    restored_state = read_input_state(paths.input_state, expected_uid=user_uid)
    if (
        restored_state.mode != "joymouse"
        or restored_state.token == baseline.session.input_token
    ):
        raise AcceptanceError("desktop input lease was not restored")
    if inputplumber_snapshot(paths) != baseline.inputplumber:
        raise AcceptanceError("InputPlumber restarted during physical acceptance")
    if processes_named(paths.proc, "wiliwili"):
        raise AcceptanceError("a Wiliwili process remains after managed exit")
    if not session_record_absent(paths.session_record):
        raise AcceptanceError("managed-session journal remains after exit")
    if runtime_artifacts(paths, user_uid=user_uid) != baseline.artifacts:
        raise AcceptanceError("Wiliwili input runtime changed during physical acceptance")
    # Close the verification window after every slower filesystem/process check.
    # A Guide/Panel transition in the middle must not let an earlier joymouse
    # sample authorize a PASS while the final live lease is gamepad (or a new
    # joymouse generation).  This remains passive: helper `wait` and the state
    # read are both observation-only paths.
    helper_wait(paths, "joymouse")
    final_state = read_input_state(paths.input_state, expected_uid=user_uid)
    if final_state != restored_state:
        raise AcceptanceError("desktop input lease changed during restoration check")
    if inputplumber_snapshot(paths) != baseline.inputplumber:
        raise AcceptanceError("InputPlumber changed during final restoration check")


def run_attended(
    paths: Paths,
    specs: Sequence[ControlSpec],
    *,
    rounds: int,
    timeout: float,
    poll_interval: float,
    stable_polls: int,
    exit_timeout: float,
    input_fn: Callable[[str], str] = input,
) -> int:
    uid = os.getuid()
    baseline = preflight(paths, user_uid=uid)
    device = IoctlStateDevice(
        paths.dev_input / baseline.session.event.event_name, baseline.session.event
    )
    controls_passed = True
    try:
        for spec in specs:
            runtime_guard(paths, baseline, user_uid=uid)
            status = capture_control(
                device,
                spec,
                rounds=rounds,
                timeout=timeout,
                poll_interval=poll_interval,
                stable_polls=stable_polls,
            )
            if status != "pass":
                print(f"{spec.label}: sampled-state failure ({status})", file=sys.stderr)
                controls_passed = False
                break
            runtime_guard(paths, baseline, user_uid=uid)
            observed = observer_confirmation(spec, input_fn=input_fn)
            runtime_guard(paths, baseline, user_uid=uid)
            if not observed:
                controls_passed = False
                break
    finally:
        device.close()

    answer = input_fn(
        "Close Wiliwili normally from its UI; do not kill it. Then type "
        f"{EXIT_CONFIRMATION}: "
    )
    if answer != EXIT_CONFIRMATION:
        raise AcceptanceError("natural-exit confirmation token is invalid")
    wait_for_natural_exit(paths, baseline, timeout=exit_timeout)
    verify_restoration(paths, baseline, user_uid=uid)
    if not controls_passed:
        return 1
    print(
        f"PASS: {len(specs)} controls x {rounds} rounds; Wiliwili exited "
        "naturally; joymouse restored."
    )
    return 0


def parse_controls(raw: str) -> list[ControlSpec]:
    names = raw.split(",")
    if not names or any(not name.strip() for name in names):
        raise AcceptanceError("control list is empty or malformed")
    specs = [get_control_spec(name) for name in names]
    if len({item.name for item in specs}) != len(specs):
        raise AcceptanceError("control list contains duplicates")
    return specs


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--controls", default="a,b,x,y")
    result.add_argument("--rounds", type=int, default=3)
    result.add_argument("--timeout", type=float, default=60.0)
    result.add_argument("--poll-interval", type=float, default=0.04)
    result.add_argument("--stable-polls", type=int, default=3)
    result.add_argument("--exit-timeout", type=float, default=180.0)
    result.add_argument("--confirm", required=True)
    return result


def validate_arguments(arguments: argparse.Namespace) -> None:
    if arguments.confirm != CAPTURE_CONFIRMATION:
        raise AcceptanceError("exact passive-capture confirmation is required")
    if not 1 <= arguments.rounds <= 50:
        raise AcceptanceError("rounds must be between 1 and 50")
    if not 1.0 <= arguments.timeout <= 60.0:
        raise AcceptanceError("control timeout must be between 1 and 60 seconds")
    if not 0.01 <= arguments.poll_interval <= 0.25:
        raise AcceptanceError("poll interval must be between 0.01 and 0.25 seconds")
    if not 2 <= arguments.stable_polls <= 10:
        raise AcceptanceError("stable poll count must be between 2 and 10")
    if not 10.0 <= arguments.exit_timeout <= 600.0:
        raise AcceptanceError("exit timeout must be between 10 and 600 seconds")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        validate_arguments(arguments)
        if not sys.stdin.isatty():
            raise AcceptanceError("attended acceptance requires an interactive terminal")
        specs = parse_controls(arguments.controls)
        return run_attended(
            default_paths(),
            specs,
            rounds=arguments.rounds,
            timeout=arguments.timeout,
            poll_interval=arguments.poll_interval,
            stable_polls=arguments.stable_polls,
            exit_timeout=arguments.exit_timeout,
        )
    except (AcceptanceError, OSError, UnicodeError) as exc:
        print(f"PDS-010 attended acceptance failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

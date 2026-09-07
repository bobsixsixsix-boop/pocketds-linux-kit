#!/usr/bin/env python3
"""Read current Linux input state and report only held modifier keys."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
from typing import Any


KEY_MAX = 0x2FF
BITMAP_BYTES = (KEY_MAX + 8) // 8
MODIFIERS = {
    29: "KEY_LEFTCTRL",
    42: "KEY_LEFTSHIFT",
    54: "KEY_RIGHTSHIFT",
    56: "KEY_LEFTALT",
    97: "KEY_RIGHTCTRL",
    100: "KEY_RIGHTALT",
    125: "KEY_LEFTMETA",
    126: "KEY_RIGHTMETA",
}
EVENT_NAME = re.compile(r"event[0-9]+")


def ioc(direction: int, kind: int, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (kind << 8) | number


def eviocgkey(size: int) -> int:
    return ioc(2, ord("E"), 0x18, size)


def held_modifier_names(bitmap: bytes) -> list[str]:
    result: list[str] = []
    for code, name in MODIFIERS.items():
        if code // 8 < len(bitmap) and bitmap[code // 8] & (1 << (code % 8)):
            result.append(name)
    return result


def bounded_name(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError:
        return "unknown"
    if len(payload) > 256:
        return "invalid"
    try:
        name = payload.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        return "invalid"
    return name or "unknown"


def read_bitmap(path: Path) -> bytes:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        bitmap = bytearray(BITMAP_BYTES)
        fcntl.ioctl(descriptor, eviocgkey(len(bitmap)), bitmap, True)
        return bytes(bitmap)
    finally:
        os.close(descriptor)


def error_category(exc: OSError) -> str:
    if isinstance(exc, PermissionError):
        return "permission-denied"
    if isinstance(exc, FileNotFoundError):
        return "disappeared"
    return "io-error"


def observe(sys_input: Path, dev_input: Path) -> dict[str, Any]:
    held: list[dict[str, Any]] = []
    unavailable: list[dict[str, str]] = []
    devices = 0
    for event in sorted(sys_input.glob("event*"), key=lambda path: path.name):
        if EVENT_NAME.fullmatch(event.name) is None:
            continue
        devices += 1
        name = bounded_name(event / "device/name")
        try:
            bitmap = read_bitmap(dev_input / event.name)
        except OSError as exc:
            unavailable.append(
                {"event": event.name, "name": name, "reason": error_category(exc)}
            )
            continue
        keys = held_modifier_names(bitmap)
        if keys:
            held.append({"event": event.name, "name": name, "keys": keys})
    return {
        "schema": 1,
        "read_only": True,
        "device_count": devices,
        "unavailable": unavailable,
        "held_modifiers": held,
        "accepted": devices > 0 and not unavailable and not held,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sys-input", type=Path, default=Path("/sys/class/input"))
    parser.add_argument("--dev-input", type=Path, default=Path("/dev/input"))
    parser.add_argument("--require-clear", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = observe(args.sys_input, args.dev_input)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["accepted"] or not args.require_clear else 3


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only, fresh-context check for Pocket DS's narrow libinput lid quirk.

Opens only the matched device, read-only and without EVIOCGRAB. No dispatch,
injected input, display changes, or service operations. This verifies a NEW
context; an already-running KWin context caches its original quirks.
"""

from __future__ import annotations

import argparse
import ctypes as C
import ctypes.util
import json
import os
from pathlib import Path
import stat
import sys


COMPATIBLE = "ayaneo,pocketds"
KEY_VOLUMEUP = 115
CAP_KEYBOARD = 0
CAP_SWITCH = 6
# libinput.h's public enum is not Linux input-event-codes.h's SW_LID (0).
# https://wayland.freedesktop.org/libinput/doc/latest/api/libinput_8h_source.html#l00722
LIBINPUT_SWITCH_LID = 1
EVDEV_SW_LID = 0


def bit_set(text: str, bit: int) -> bool:
    words = text.split()[::-1]
    bits = C.sizeof(C.c_ulong) * 8
    index, offset = divmod(bit, bits)
    return index < len(words) and bool(int(words[index], 16) & (1 << offset))


def find_device(sysfs: Path = Path("/sys")) -> Path:
    compatible = (sysfs / "firmware/devicetree/base/compatible").read_bytes()
    if compatible.split(b"\0")[0].decode() != COMPATIBLE:
        raise RuntimeError("device tree is not AYANEO Pocket DS")
    matches = []
    for event in (sysfs / "class/input").glob("event*"):
        device = event / "device"
        if (device / "name").read_text().strip() != "gpio-keys":
            continue
        if (bit_set((device / "capabilities/sw").read_text(), EVDEV_SW_LID)
                and bit_set((device / "capabilities/key").read_text(), KEY_VOLUMEUP)):
            matches.append(Path("/dev/input") / event.name)
    if len(matches) != 1:
        raise RuntimeError("expected one gpio-keys device with raw SW_LID and KEY_VOLUMEUP")
    return matches[0]


def probe(device: Path, quirks_dir: Path | None = None) -> dict:
    # LIBINPUT_QUIRKS_DIR is an internal, process-local staging hook. A child
    # process is used for every probe; no existing compositor environment changes.
    os.environ.pop("LIBINPUT_RUNNING_TEST_SUITE", None)
    os.environ.pop("LIBINPUT_QUIRKS_DIR", None)
    if quirks_dir is not None:
        os.environ["LIBINPUT_QUIRKS_DIR"] = str(quirks_dir.resolve(strict=True))
    library = ctypes.util.find_library("input")
    if not library:
        raise RuntimeError("libinput shared library is unavailable")
    lib = C.CDLL(library)
    ptr = C.c_void_p
    opener_type = C.CFUNCTYPE(C.c_int, C.c_char_p, C.c_int, ptr)
    closer_type = C.CFUNCTYPE(None, C.c_int, ptr)
    logger_type = C.CFUNCTYPE(None, ptr, C.c_int, C.c_char_p, ptr)

    class Interface(C.Structure):
        _fields_ = [("open_restricted", opener_type), ("close_restricted", closer_type)]

    @opener_type
    def opener(path, _flags, _data):
        try:
            if os.fsdecode(path) != str(device):
                return -13
            # libinput requests O_RDWR; this diagnostic deliberately grants less.
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
            if not stat.S_ISCHR(os.fstat(fd).st_mode):
                os.close(fd)
                return -19
            return fd
        except OSError as error:
            return -error.errno

    @closer_type
    def closer(fd, _data):
        os.close(fd)

    errors = []

    @logger_type
    def logger(_context, priority, message, _args):
        if priority >= 30:
            # The format string is enough to identify parser/init errors. Never
            # attempt to decode a platform-specific va_list through ctypes.
            errors.append(message.decode(errors="replace").strip())

    signatures = {
        "libinput_path_create_context": ([C.POINTER(Interface), ptr], ptr),
        "libinput_path_add_device": ([ptr, C.c_char_p], ptr),
        "libinput_path_remove_device": ([ptr], None),
        "libinput_unref": ([ptr], ptr),
        "libinput_log_set_handler": ([ptr, logger_type], None),
        "libinput_device_has_capability": ([ptr, C.c_int], C.c_int),
        "libinput_device_keyboard_has_key": ([ptr, C.c_uint32], C.c_int),
        "libinput_device_switch_has_switch": ([ptr, C.c_int], C.c_int),
    }
    for name, (args, result) in signatures.items():
        function = getattr(lib, name)
        function.argtypes, function.restype = args, result
    interface = Interface(opener, closer)
    context = lib.libinput_path_create_context(C.byref(interface), None)
    if not context:
        raise RuntimeError("libinput context initialization failed")
    handle = None
    try:
        lib.libinput_log_set_handler(context, logger)
        handle = lib.libinput_path_add_device(context, os.fsencode(device))
        if not handle:
            raise RuntimeError("libinput did not initialize gpio-keys: " + "; ".join(errors))

        def flag(value: int, operation: str) -> bool:
            if value not in (0, 1):
                raise RuntimeError(f"libinput {operation} returned an invalid capability result: {value}")
            return value == 1

        keyboard = flag(lib.libinput_device_has_capability(handle, CAP_KEYBOARD), "keyboard")
        switches = flag(lib.libinput_device_has_capability(handle, CAP_SWITCH), "switch")
        # Removing the last switch removes CAP_SWITCH too. In that case the
        # switch API returns -1 by contract; an error on a switch-capable device
        # must instead fail the check, never masquerade as successful filtering.
        lid = (flag(lib.libinput_device_switch_has_switch(handle, LIBINPUT_SWITCH_LID), "lid")
               if switches else False)
        volume_up = (flag(lib.libinput_device_keyboard_has_key(handle, KEY_VOLUMEUP), "volume-up")
                     if keyboard else False)
        result = {
            "device": str(device), "fresh_context": True,
            "raw_lid": True, "raw_volume_up": True,
            "keyboard": keyboard, "switch": switches,
            "lid": lid, "volume_up": volume_up,
            "errors": errors,
        }
        if errors:
            raise RuntimeError("libinput parser/device errors: " + "; ".join(errors))
        return result
    finally:
        if handle:
            lib.libinput_path_remove_device(handle)
        lib.libinput_unref(context)


def validate(result: dict, expected: str) -> None:
    if result.get("errors") or not all(result.get(key) is True for key in
                                      ("fresh_context", "raw_lid", "raw_volume_up", "keyboard", "volume_up")):
        raise RuntimeError("libinput must retain keyboard and KEY_VOLUMEUP without parser errors")
    if not isinstance(result.get("lid"), bool):
        raise RuntimeError("libinput did not return a boolean lid capability")
    if expected != "any" and result["lid"] != (expected == "native"):
        raise RuntimeError("new libinput context has the wrong lid capability")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quirks-dir", type=Path)
    parser.add_argument("--expect", choices=("filtered", "native", "any"), default="filtered")
    args = parser.parse_args()
    try:
        result = probe(find_device(), args.quirks_dir)
        validate(result, args.expect)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

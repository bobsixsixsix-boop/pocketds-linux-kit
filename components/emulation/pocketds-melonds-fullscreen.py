#!/usr/bin/env python3
"""Put both melonDS windows into application-native fullscreen once."""

from __future__ import annotations

import argparse
import sys
import time

import pyatspi
from evdev import UInput, ecodes


APP_NAME = "melonDS"
FRAME_PREFIXES = ("[w1]", "[w2]")
VIRTUAL_KEYBOARD_NAME = "Pocket DS melonDS Fullscreen"


def melon_frames() -> dict[str, object]:
    found: dict[str, object] = {}
    desktop = pyatspi.Registry.getDesktop(0)
    for app in desktop:
        if app.name != APP_NAME:
            continue
        for frame in app:
            if frame.getRoleName() != "frame":
                continue
            for prefix in FRAME_PREFIXES:
                if frame.name.startswith(prefix):
                    found[prefix] = frame
    return found


def menu_height(frame: object) -> int | None:
    for child in frame:
        if child.getRoleName() == "menu bar":
            return child.queryComponent().getExtents(pyatspi.DESKTOP_COORDS).height
    return None


def correctly_placed(frames: dict[str, object]) -> bool:
    top = frames["[w1]"].queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
    bottom = frames["[w2]"].queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
    return top.y == 0 and top.width >= 1000 and bottom.y >= top.height


def send_f11(frame: object) -> None:
    if not frame.queryComponent().grabFocus():
        raise RuntimeError("could not focus the melonDS top-screen window")
    time.sleep(0.2)
    with UInput(
        {ecodes.EV_KEY: [ecodes.KEY_F11]}, name=VIRTUAL_KEYBOARD_NAME
    ) as keyboard:
        time.sleep(0.6)
        keyboard.write(ecodes.EV_KEY, ecodes.KEY_F11, 1)
        keyboard.syn()
        time.sleep(0.08)
        keyboard.write(ecodes.EV_KEY, ecodes.KEY_F11, 0)
        keyboard.syn()
        time.sleep(0.4)


def fullscreen(timeout: float) -> None:
    deadline = time.monotonic() + timeout
    frames: dict[str, object] = {}
    while time.monotonic() < deadline:
        frames = melon_frames()
        if set(frames) == set(FRAME_PREFIXES) and correctly_placed(frames):
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("melonDS dual windows did not become ready")

    heights = [menu_height(frames[prefix]) for prefix in FRAME_PREFIXES]
    if heights == [0, 0]:
        return
    send_f11(frames["[w1]"])
    heights = [menu_height(frames[prefix]) for prefix in FRAME_PREFIXES]
    if heights != [0, 0]:
        raise RuntimeError(f"melonDS native fullscreen failed; menu heights={heights}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        fullscreen(args.timeout)
    except Exception as exc:
        print(f"pocketds-melonds-fullscreen: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

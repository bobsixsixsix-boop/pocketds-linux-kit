#!/usr/bin/env python3
"""Select the Pocket DS speaker-only PipeWire enhancement without volume jumps."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


PHYSICAL_SINK = "alsa_output.platform-sound.HiFi__Speaker__sink"
ENHANCED_SINK = "effect_input.pocketds_speaker_enhanced"
# The virtual sink is the only user-facing volume control. Keep its physical
# target at unity so 100% remains the device's verified full speaker output;
# stacking a 70% backing volume below it caused a permanent -9.29 dB cut.
BACKING_VOLUME_PERCENT = 100
STATE_PATH = Path.home() / ".local/state/pocketds-linux-kit/audio/speaker-enhancement.json"


class PactlError(RuntimeError):
    pass


def run_pactl(*args: str, check: bool = True) -> str:
    # pactl localizes boolean values (for example ``Mute: no`` becomes
    # ``静音: 否`` under zh_CN). Every parser in this controller consumes
    # pactl's machine-facing output, so keep that one subprocess in the C
    # locale instead of teaching safety-critical state handling every possible
    # translation.
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    result = subprocess.run(
        ("pactl", *args),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
        env=environment,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown pactl error"
        raise PactlError(f"pactl {' '.join(args)}: {detail}")
    return result.stdout.strip()


def sink_names() -> set[str]:
    names: set[str] = set()
    for line in run_pactl("list", "short", "sinks").splitlines():
        fields = line.split()
        if len(fields) >= 2:
            names.add(fields[1])
    return names


def wait_for_sinks(timeout: float = 12.0) -> set[str]:
    deadline = time.monotonic() + timeout
    last: set[str] = set()
    while time.monotonic() < deadline:
        try:
            last = sink_names()
        except (PactlError, subprocess.TimeoutExpired):
            last = set()
        if {PHYSICAL_SINK, ENHANCED_SINK}.issubset(last):
            return last
        time.sleep(0.2)
    missing = sorted({PHYSICAL_SINK, ENHANCED_SINK} - last)
    raise PactlError("audio sinks did not appear: " + ", ".join(missing))


def volume_percent(sink: str) -> int:
    output = run_pactl("get-sink-volume", sink)
    match = re.search(r"\b(\d{1,3})%", output)
    if match is None:
        raise PactlError(f"cannot parse volume for {sink}: {output}")
    return int(match.group(1))


def muted(sink: str) -> bool:
    output = run_pactl("get-sink-mute", sink).lower()
    if output.endswith("yes"):
        return True
    if output.endswith("no"):
        return False
    raise PactlError(f"cannot parse mute state for {sink}: {output}")


def set_volume(sink: str, percent: int) -> None:
    run_pactl("set-sink-volume", sink, f"{max(0, min(percent, 100))}%")


def set_muted(sink: str, value: bool) -> None:
    run_pactl("set-sink-mute", sink, "1" if value else "0")


def virtual_volume_for(effective_percent: int) -> int:
    """Preserve the effective cubic-volume control across the two sinks."""
    return max(0, min(round(effective_percent * 100 / BACKING_VOLUME_PERCENT), 100))


def effective_volume_for(virtual_percent: int, physical_percent: int) -> int:
    return max(0, min(round(virtual_percent * physical_percent / 100), 100))


def read_state() -> dict[str, object]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_state(value: dict[str, object]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_name(f".{STATE_PATH.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, STATE_PATH)


def activate() -> None:
    names = wait_for_sinks()
    default = run_pactl("get-default-sink")
    state = read_state()

    if default == ENHANCED_SINK:
        set_volume(PHYSICAL_SINK, BACKING_VOLUME_PERCENT)
        set_muted(PHYSICAL_SINK, False)
    else:
        old_volume = volume_percent(PHYSICAL_SINK)
        old_muted = muted(PHYSICAL_SINK)
        set_volume(PHYSICAL_SINK, BACKING_VOLUME_PERCENT)
        set_muted(PHYSICAL_SINK, False)
        set_volume(ENHANCED_SINK, virtual_volume_for(old_volume))
        set_muted(ENHANCED_SINK, old_muted)
        # An explicitly selected external sink remains untouched.  The new
        # sink becomes default only when the built-in speaker was selected.
        if default == PHYSICAL_SINK or default not in names:
            run_pactl("set-default-sink", ENHANCED_SINK)

    state.update(
        {
            "version": 1,
            "physical_sink": PHYSICAL_SINK,
            "enhanced_sink": ENHANCED_SINK,
            "backing_volume_percent": BACKING_VOLUME_PERCENT,
            "active": True,
        }
    )
    write_state(state)


def deactivate() -> None:
    names = sink_names()
    default = run_pactl("get-default-sink")
    if PHYSICAL_SINK not in names:
        raise PactlError(f"physical sink is unavailable: {PHYSICAL_SINK}")

    if ENHANCED_SINK in names:
        virtual_volume = volume_percent(ENHANCED_SINK)
        virtual_muted = muted(ENHANCED_SINK)
        physical_volume = volume_percent(PHYSICAL_SINK)
        set_volume(
            PHYSICAL_SINK,
            effective_volume_for(virtual_volume, physical_volume),
        )
        set_muted(PHYSICAL_SINK, virtual_muted)
        if default == ENHANCED_SINK:
            run_pactl("set-default-sink", PHYSICAL_SINK)

    state = read_state()
    state["active"] = False
    write_state(state)


def status() -> None:
    names = sink_names()
    payload: dict[str, object] = {
        "default_sink": run_pactl("get-default-sink"),
        "physical_present": PHYSICAL_SINK in names,
        "enhanced_present": ENHANCED_SINK in names,
        "state": read_state(),
    }
    if PHYSICAL_SINK in names:
        payload["physical_volume_percent"] = volume_percent(PHYSICAL_SINK)
        payload["physical_muted"] = muted(PHYSICAL_SINK)
    if ENHANCED_SINK in names:
        payload["enhanced_volume_percent"] = volume_percent(ENHANCED_SINK)
        payload["enhanced_muted"] = muted(ENHANCED_SINK)
    print(json.dumps(payload, indent=2, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("action", choices=("activate", "deactivate", "status"))
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.action == "activate":
            activate()
        elif args.action == "deactivate":
            deactivate()
        else:
            status()
    except (PactlError, OSError, subprocess.TimeoutExpired) as error:
        print(f"pocketds-speaker-enhancement: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Wait until the Pocket DS ASoC card is safe for WirePlumber to enumerate."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path


CONTROL_DEVICE = Path("/dev/snd/controlC0")
REQUIRED_CONTROLS = (
    "WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia1",
    "DISPLAY_PORT_RX_0 Audio Mixer MultiMedia2",
    "MultiMedia3 Mixer VA_CODEC_DMA_TX_0",
    "SpkrLeft PA Volume",
    "SpkrRight PA Volume",
)
PLAYBACK_MARKER = "device 0: MultiMedia1 Playback"
CAPTURE_MARKER = "device 2: MultiMedia3 Capture"


CommandRunner = Callable[[tuple[str, ...]], tuple[int, str]]
Probe = Callable[[], list[str]]


def run_command(command: tuple[str, ...]) -> tuple[int, str]:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=2,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, str(error)
    return result.returncode, result.stdout


def probe_card(
    control_device: Path = CONTROL_DEVICE,
    runner: CommandRunner = run_command,
) -> list[str]:
    missing: list[str] = []
    if not control_device.exists():
        return [str(control_device)]

    controls_rc, controls = runner(("amixer", "-c", "0", "controls"))
    if controls_rc != 0:
        missing.append("ALSA controls")
    else:
        missing.extend(
            f"control:{name}" for name in REQUIRED_CONTROLS if f"name='{name}'" not in controls
        )

    playback_rc, playback = runner(("aplay", "-l"))
    if playback_rc != 0 or PLAYBACK_MARKER not in playback:
        missing.append("MultiMedia1 playback PCM")

    capture_rc, capture = runner(("arecord", "-l"))
    if capture_rc != 0 or CAPTURE_MARKER not in capture:
        missing.append("MultiMedia3 capture PCM")

    return missing


def wait_until_ready(
    timeout: float,
    interval: float,
    stable_samples: int,
    probe: Probe,
) -> tuple[bool, list[str]]:
    deadline = time.monotonic() + timeout
    stable = 0
    last_missing: list[str] = ["not probed"]
    while time.monotonic() < deadline:
        last_missing = probe()
        if last_missing:
            stable = 0
        else:
            stable += 1
            if stable >= stable_samples:
                return True, []
        time.sleep(interval)
    return False, last_missing


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--timeout", type=float, default=30.0)
    result.add_argument("--interval", type=float, default=0.2)
    result.add_argument("--stable-samples", type=int, default=3)
    return result


def main() -> int:
    args = parser().parse_args()
    if args.timeout <= 0 or args.interval <= 0 or args.stable_samples <= 0:
        print("pocketds-audio-card-ready: timing values must be positive", file=sys.stderr)
        return 2
    ready, missing = wait_until_ready(
        args.timeout,
        args.interval,
        args.stable_samples,
        probe_card,
    )
    if not ready:
        print(
            "pocketds-audio-card-ready: card did not settle; missing "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    print("pocketds-audio-card-ready: playback, capture and mixer controls are stable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

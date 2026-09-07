#!/usr/bin/python3
"""Expose the Pocket DS Hall sensor when the boot DTB lacks native SW_LID."""

from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import time

from evdev import UInput, ecodes


GPIO_CHIP = "gpiochip4"
GPIO_LINE = "166"
GPIOGET = "/usr/bin/gpioget"
GPIOMON = "/usr/bin/gpiomon"
LOG = logging.getLogger("pocketds-lid-switch")
FALLBACK_NAME = "Pocket DS Lid Switch"
FALLBACK_PHYS = "py-evdev-uinput"


def native_lid_present(input_root: Path = Path("/sys/class/input")) -> bool:
    """Return whether an existing input device exposes SW_LID (switch bit 0)."""

    for capability in input_root.glob("event*/device/capabilities/sw"):
        try:
            device = capability.parent.parent
            words = capability.read_text(encoding="ascii").strip().split()
            try:
                name = (device / "name").read_text(encoding="utf-8").strip()
                phys = (device / "phys").read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError):
                name = phys = ""
            if name == FALLBACK_NAME and phys == FALLBACK_PHYS:
                # A just-stopped generation can remain in sysfs briefly while
                # systemd starts its replacement. Never mistake it for native.
                continue
            if words and int(words[-1], 16) & 1:
                return True
        except (OSError, UnicodeError, ValueError):
            continue
    return False


def read_lid_closed() -> bool:
    result = subprocess.run(
        [GPIOGET, "--numeric", "-c", GPIO_CHIP, GPIO_LINE],
        check=True,
        capture_output=True,
        text=True,
        timeout=2,
    )
    value = result.stdout.strip()
    if value not in {"0", "1"}:
        raise RuntimeError("Hall sensor returned an invalid level")
    # BU52053NVX drives the line low when the lid magnet is present.
    return value == "0"


def publish_lid(device: UInput, closed: bool) -> None:
    device.write(ecodes.EV_SW, ecodes.SW_LID, int(closed))
    device.syn()
    LOG.info("lid %s", "closed" if closed else "open")


def monitor(device: UInput) -> None:
    publish_lid(device, read_lid_closed())
    command = [
        GPIOMON,
        "--format=%E",
        "--edges=both",
        "--chip",
        GPIO_CHIP,
        GPIO_LINE,
    ]
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    ) as process:
        assert process.stdout is not None
        for line in process.stdout:
            edge = line.strip()
            if edge == "falling":
                publish_lid(device, True)
            elif edge == "rising":
                publish_lid(device, False)
            else:
                LOG.warning("unexpected GPIO event: %r", edge)

        stderr = process.stderr.read().strip() if process.stderr else ""
        raise RuntimeError(
            f"gpiomon exited with status {process.returncode}: {stderr[:240]}"
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if native_lid_present():
        LOG.info("native SW_LID is present; fallback bridge is not needed")
        return

    for executable in (GPIOGET, GPIOMON):
        if not Path(executable).is_file():
            raise RuntimeError(f"required GPIO tool is missing: {executable}")

    capabilities = {ecodes.EV_SW: [ecodes.SW_LID]}
    with UInput(capabilities, name=FALLBACK_NAME, version=1) as device:
        while True:
            try:
                monitor(device)
            except Exception:
                LOG.exception("lid monitor failed; retrying")
                time.sleep(1)


if __name__ == "__main__":
    main()

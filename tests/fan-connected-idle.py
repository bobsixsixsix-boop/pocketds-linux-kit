#!/usr/bin/env python3
"""Pure smoke tests for the daily connected-idle fan override."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "components/fan/pocketds-fancontrol-connected-idle.py"
DROP_IN = ROOT / "components/fan/pocketds-fancontrol-connected-idle.conf"
PACKAGED = ROOT / "packaging/pocketds-userspace/pocketds-fancontrol.pds1"
SOURCE_LOCK = ROOT / "packaging/pocketds-userspace/source-lock.json"

spec = importlib.util.spec_from_file_location("pocketds_fan_connected_idle", CONTROLLER)
assert spec is not None and spec.loader is not None
fan = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fan
spec.loader.exec_module(fan)


stopped, ticks = fan.next_screen_off_fan_state(False, 0, True, True, 50.0)
assert (stopped, ticks) == (False, 1)
stopped, ticks = fan.next_screen_off_fan_state(stopped, ticks, True, True, 50.0)
assert (stopped, ticks) == (True, 2)
assert fan.next_screen_off_fan_state(True, ticks, True, True, 59.9) == (True, ticks)
assert fan.next_screen_off_fan_state(True, ticks, True, True, 60.0) == (False, 0)
assert fan.next_screen_off_fan_state(True, ticks, True, False, 40.0) == (False, 0)
assert fan.next_screen_off_fan_state(True, ticks, False, True, 0.0) == (False, 0)
assert fan.next_screen_off_fan_state(False, 0, True, True, 50.1) == (False, 0)

# An off blower must restart at the proven 20% floor, while sensor failures and
# instantaneous thermal floors must bypass the normal acoustic ramp.
assert fan.output_pwm(0, 51, 44.0, True, False) == 51
assert fan.output_pwm(0, 204, 0.0, False, False) == 204
assert fan.output_pwm(0, 0, 44.0, True, True) == 0
assert fan.output_pwm(0, 0, 84.0, True, False) == 153
assert fan.output_pwm(0, 0, 88.0, True, False) == 204
assert fan.output_pwm(0, 0, 95.0, True, False) == 255
assert fan.stopped_on_hardware(True, 0) is True
assert fan.stopped_on_hardware(True, 51) is False

with tempfile.TemporaryDirectory(prefix="pocketds-fan-connected-idle-") as name:
    base = Path(name)
    top = base / "top"
    bottom = base / "bottom"
    original_paths = fan.BACKLIGHT_POWER_PATHS
    fan.BACKLIGHT_POWER_PATHS = (str(top), str(bottom))
    try:
        top.write_text("4\n", encoding="utf-8")
        bottom.write_text("4\n", encoding="utf-8")
        assert fan.internal_displays_blanked() is True
        bottom.write_text("0\n", encoding="utf-8")
        assert fan.internal_displays_blanked() is False
        bottom.unlink()
        assert fan.internal_displays_blanked() is False
        assert fan.write_pwm(str(base / "missing" / "pwm1"), 51) is False
    finally:
        fan.BACKLIGHT_POWER_PATHS = original_paths

drop_in = DROP_IN.read_text(encoding="utf-8")
assert drop_in == (
    "[Service]\n"
    "ExecStart=\n"
    "ExecStart=/usr/local/libexec/pocketds/pocketds-fancontrol-connected-idle\n"
)

# Release locks must continue to describe the historical packaged controller,
# not silently claim that the daily override was rebuilt and audited as an RPM.
lock = json.loads(SOURCE_LOCK.read_text(encoding="utf-8"))
locked = lock["transform"]["fan_controller_override"]
payload = PACKAGED.read_bytes()
assert locked["size"] == len(payload)
assert locked["sha256"] == hashlib.sha256(payload).hexdigest()
assert hashlib.sha256(CONTROLLER.read_bytes()).hexdigest() != locked["sha256"]

print("  [OK] connected-idle fan override is safe and release-lock isolated")

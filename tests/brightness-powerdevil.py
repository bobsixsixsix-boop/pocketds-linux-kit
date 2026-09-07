#!/usr/bin/env python3
"""Pure tests for PowerDevil clobber detection and optional API sync planning."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
PROGRAM = ROOT / "components" / "brightness" / "pocketds-brightness.py"
INSTALLER = ROOT / "scripts" / "install-brightness.sh"
MAIN_INSTALLER = ROOT / "scripts" / "install.sh"
SPEC = importlib.util.spec_from_file_location("pocketds_brightness", PROGRAM)
assert SPEC and SPEC.loader
brightness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(brightness)


state = brightness.make_state(56, 68, "test")


def snapshot(top: int, bottom: int) -> dict[str, dict[str, int]]:
    return {
        "top": {"raw": top, "max": 100, "percent": top},
        "bottom": {"raw": bottom, "max": 100, "percent": bottom},
    }


def differences_for(actual: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    return brightness.drift(state, actual)


# Both at 100 is the first observed PowerDevil restart pattern. It bypasses debounce.
actual = snapshot(100, 100)
apply, count, reason = brightness.reconcile_decision(
    state, actual, differences_for(actual), 0
)
assert (apply, count, reason) == (True, 0, "powerdevil-global-clobber")

# With KWin configured to 56/68, PowerDevil was observed copying 56 to the bottom.
# The top is not drifted, but equality of both actual values is still the strict signature.
actual = snapshot(56, 56)
apply, count, reason = brightness.reconcile_decision(
    state, actual, differences_for(actual), 0
)
assert (apply, count, reason) == (True, 0, "powerdevil-global-clobber")

# An arbitrary one-screen transient retains the original two-observation debounce.
actual = snapshot(57, 68)
drift = differences_for(actual)
assert brightness.reconcile_decision(state, actual, drift, 0) == (
    False,
    1,
    "debouncing",
)
assert brightness.reconcile_decision(state, actual, drift, 1) == (
    True,
    0,
    "debounced-drift",
)

# Equal desired levels are intentionally not classified as a global overwrite.
equal_state = brightness.make_state(60, 60, "test")
actual = snapshot(100, 100)
equal_drift = brightness.drift(equal_state, actual)
assert brightness.reconcile_decision(equal_state, actual, equal_drift, 0) == (
    False,
    1,
    "debouncing",
)

# SY7758 leaves its raw brightness nonzero when the kernel marks it DPMS-off.
# The daemon must zero only that LCD backlight, hold it at zero while off, and
# immediately restore the persisted preference after DPMS-on.
assert brightness.bottom_dpms_action(
    {"top": 4, "bottom": 4}, snapshot(56, 68)
) == "blank"
assert brightness.bottom_dpms_action(
    {"top": 4, "bottom": 4}, snapshot(56, 0)
) == "hold"
assert brightness.bottom_dpms_action(
    {"top": 0, "bottom": 0}, snapshot(56, 0)
) == "restore"
assert brightness.bottom_dpms_action(
    {"top": 0, "bottom": 0}, snapshot(56, 68)
) == "normal"
assert brightness.displays_powered({"top": 4, "bottom": 4}) is False
assert brightness.displays_powered({"top": 4, "bottom": 0}) is True
assert brightness.BACKLIGHT_WAKE_POLL_INTERVAL_S == 0.25


class FakeBusctl:
    def __init__(self, maxima: dict[str, int]) -> None:
        self.maxima = maxima
        self.calls: list[list[str]] = []

    def __call__(self, arguments: list[str]) -> str:
        self.calls.append(arguments)
        if arguments[0] == "get-property" and arguments[-1] == "DisplaysDBusNames":
            names = " ".join(f'"{name}"' for name in self.maxima)
            return f"as {len(self.maxima)} {names}"
        if arguments[0] == "get-property" and arguments[-1] == "MaxBrightness":
            name = arguments[2].rsplit("/", 1)[-1]
            return f"i {self.maxima[name]}"
        if arguments[0] == "call":
            return ""
        raise AssertionError(arguments)


# The plan enumerates official child names and never assumes connector names.
fake = FakeBusctl({"display7": 1000, "display9": 255})
plan = brightness.powerdevil_neutral_plan(fake)
assert [(item["dbus_name"], item["target_brightness"]) for item in plan] == [
    ("display7", 1000),
    ("display9", 255),
]
assert all("DSI" not in " ".join(call) for call in fake.calls)
result = brightness.apply_powerdevil_neutral_plan(plan, fake)
assert result == {
    "neutral_percent": 100,
    "display_count": 2,
    "applied": ["display7", "display9"],
}
write_calls = [call for call in fake.calls if call[0] == "call"]
assert [call[-2:] for call in write_calls] == [["1000", "1"], ["255", "1"]]
assert all(call[-3] == "iu" for call in write_calls)

assert not brightness.parse_args(["sync-powerdevil-neutral"]).apply
assert brightness.parse_args(["sync-powerdevil-neutral", "--apply"]).apply
assert brightness.POWERDEVIL_NEUTRAL_PERCENT == 100

# Updating the installed program must also replace the active process.  In an
# already-enabled service, `enable --now` is a no-op and leaves the old Python
# process resident, so the installer contract requires an explicit restart.
installer_text = INSTALLER.read_text(encoding="utf-8")
assert "systemctl --user restart pocketds-brightness.service" in installer_text
assert "enable --now pocketds-brightness.service" not in installer_text

# The normal app installer must not silently omit the writer/daemon. Preserve
# the physical levels on a clean or recovered installation.
main_installer_text = MAIN_INSTALLER.read_text(encoding="utf-8")
assert 'components/brightness/pocketds-brightness.py' in main_installer_text
assert 'components/brightness/pocketds-brightness.service' in main_installer_text
assert 'pocketds-brightness" initialize --capture-current' in main_installer_text
assert "systemctl --user enable pocketds-brightness.service" in main_installer_text
assert "systemctl --user restart pocketds-brightness.service" in main_installer_text


# A per-child failure stops further mutation and reports the already-applied prefix.
class FailSecondCall(FakeBusctl):
    def __init__(self, maxima: dict[str, int]) -> None:
        super().__init__(maxima)
        self.write_count = 0

    def __call__(self, arguments: list[str]) -> str:
        if arguments[0] == "call":
            self.calls.append(arguments)
            self.write_count += 1
            if self.write_count == 2:
                raise RuntimeError("synthetic timeout")
            return ""
        return super().__call__(arguments)


failing = FailSecondCall({"display7": 1000, "display9": 255})
try:
    brightness.apply_powerdevil_neutral_plan(plan, failing)
except RuntimeError as exc:
    assert "applied=display7" in str(exc)
    assert "synthetic timeout" in str(exc)
else:
    raise AssertionError("failed SetBrightness was accepted")
assert failing.write_count == 2


# Timeout handling is tested with a mocked subprocess; no session DBus is contacted.
timeout = brightness.subprocess.TimeoutExpired(["busctl"], 3)
with mock.patch.object(brightness.subprocess, "run", side_effect=timeout):
    try:
        brightness.run_busctl(["get-property"])
    except RuntimeError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("busctl timeout was accepted")


# All properties are validated before the first SetBrightness mutation.
class BrokenMax(FakeBusctl):
    def __call__(self, arguments: list[str]) -> str:
        if arguments[0] == "get-property" and arguments[-1] == "MaxBrightness":
            self.calls.append(arguments)
            name = arguments[2].rsplit("/", 1)[-1]
            return "x invalid" if name == "display9" else "i 1000"
        return super().__call__(arguments)


broken = BrokenMax({"display7": 1000, "display9": 255})
try:
    brightness.powerdevil_neutral_plan(broken)
except ValueError as exc:
    assert "MaxBrightness" in str(exc)
else:
    raise AssertionError("invalid MaxBrightness was accepted")
assert not any(call[0] == "call" for call in broken.calls)

for invalid in (
    'as 2 "display0"',
    'as 2 "display0" "display0"',
    'as 1 "../display0"',
    "as 0",
):
    try:
        brightness.parse_busctl_string_array(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError(f"invalid child list was accepted: {invalid}")

print("  [OK] PowerDevil clobber detection and pure neutral-sync planning")

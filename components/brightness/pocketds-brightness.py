#!/usr/bin/env python3
"""Persist and reconcile the two independent Pocket DS hardware backlights."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import shlex
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterator
from contextlib import contextmanager


SCHEMA = 1
MIN_PERCENT = 5
MAX_PERCENT = 100
DEFAULT_PERCENT = 60
STOP_REQUESTED = False
STOP_EVENT = threading.Event()
POWERDEVIL_SERVICE = "org.kde.ScreenBrightness"
POWERDEVIL_PATH = "/org/kde/ScreenBrightness"
POWERDEVIL_INTERFACE = "org.kde.ScreenBrightness"
POWERDEVIL_DISPLAY_INTERFACE = "org.kde.ScreenBrightness.Display"
# KWin software brightness is multiplicative. Only 100% is neutral when the
# two independent kernel backlights remain the hardware source of truth.
POWERDEVIL_NEUTRAL_PERCENT = 100
POWERDEVIL_MAX_DISPLAYS = 32
# The lower LCD backlight is forced to raw zero while DPMS is off, so wake
# detection is user-visible. While blanked, use a tiny single-sysfs fast path;
# full maintenance still runs at the configured interval while screens are off.
BACKLIGHT_WAKE_POLL_INTERVAL_S = 0.25


def state_path() -> Path:
    override = os.environ.get("POCKETDS_BRIGHTNESS_STATE")
    if override:
        return Path(override)
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config / "pocketds" / "brightness.json"


def backlight_paths() -> dict[str, Path]:
    return {
        "top": Path(
            os.environ.get(
                "POCKETDS_TOP_BACKLIGHT", "/sys/class/backlight/ae94000.dsi.0"
            )
        ),
        "bottom": Path(
            os.environ.get(
                "POCKETDS_BOTTOM_BACKLIGHT", "/sys/class/backlight/sy7758-backlight"
            )
        ),
    }


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def read_int(path: Path) -> int:
    return int(path.read_text(encoding="utf-8").strip())


def backlight_power_snapshot() -> dict[str, int]:
    states = {
        screen: read_int(path / "bl_power")
        for screen, path in backlight_paths().items()
    }
    if any(not 0 <= value <= 4 for value in states.values()):
        raise ValueError("invalid backlight power state")
    return states


def displays_powered(states: dict[str, int] | None = None) -> bool:
    """Fail active when DPMS state cannot be read."""
    try:
        observed = states if states is not None else backlight_power_snapshot()
    except (OSError, UnicodeError, ValueError):
        return True
    return any(state == 0 for state in observed.values())


def raw_for_percent(maximum: int, percent: int) -> int:
    return maximum * percent // 100


def percent_for_raw(value: int, maximum: int) -> int:
    if maximum <= 0:
        raise ValueError("backlight max_brightness must be positive")
    return max(MIN_PERCENT, min(MAX_PERCENT, round(value * 100 / maximum)))


def hardware_snapshot() -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for screen, path in backlight_paths().items():
        maximum = read_int(path / "max_brightness")
        value = read_int(path / "brightness")
        if maximum <= 0 or not 0 <= value <= maximum:
            raise ValueError(f"invalid {screen} backlight values: {value}/{maximum}")
        result[screen] = {
            "raw": value,
            "max": maximum,
            "percent": percent_for_raw(value, maximum),
        }
    return result


def validate_percent(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not MIN_PERCENT <= value <= MAX_PERCENT:
        raise ValueError(f"{name} must be between {MIN_PERCENT} and {MAX_PERCENT}")
    return value


def validate_state(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("unsupported brightness state schema")
    return {
        "schema": SCHEMA,
        "top_percent": validate_percent(data.get("top_percent"), "top_percent"),
        "bottom_percent": validate_percent(
            data.get("bottom_percent"), "bottom_percent"
        ),
        "updated_at": str(data.get("updated_at") or "unknown"),
        "source": str(data.get("source") or "unknown"),
    }


def load_state(path: Path | None = None) -> dict[str, Any]:
    target = path or state_path()
    return validate_state(json.loads(target.read_text(encoding="utf-8")))


def make_state(top: int, bottom: int, source: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "top_percent": validate_percent(top, "top_percent"),
        "bottom_percent": validate_percent(bottom, "bottom_percent"),
        "updated_at": utc_now(),
        "source": source,
    }


def atomic_write_state(data: dict[str, Any], path: Path | None = None) -> None:
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    payload = json.dumps(validate_state(data), ensure_ascii=False, indent=2) + "\n"
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


@contextmanager
def state_lock(path: Path | None = None) -> Iterator[None]:
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = target.with_suffix(target.suffix + ".lock")
    with lock.open("a", encoding="utf-8") as stream:
        os.chmod(lock, 0o600)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def quarantine_invalid_state(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.invalid-{stamp}")
    os.replace(path, backup)
    return backup


def capture_state(source: str = "capture") -> dict[str, Any]:
    snapshot = hardware_snapshot()
    state = make_state(
        snapshot["top"]["percent"], snapshot["bottom"]["percent"], source
    )
    with state_lock():
        atomic_write_state(state)
    return state


def initialize_state(capture_current: bool) -> tuple[dict[str, Any], str]:
    target = state_path()
    with state_lock(target):
        try:
            return load_state(target), "existing"
        except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
            backup = quarantine_invalid_state(target)
            if capture_current:
                snapshot = hardware_snapshot()
                state = make_state(
                    snapshot["top"]["percent"],
                    snapshot["bottom"]["percent"],
                    "captured-existing-hardware",
                )
            else:
                state = make_state(DEFAULT_PERCENT, DEFAULT_PERCENT, "first-run-default")
            atomic_write_state(state, target)
            return state, f"created; invalid_backup={backup}" if backup else "created"


def helper_command(screen: str, percent: int) -> list[str]:
    override = os.environ.get("POCKETDS_BRIGHTNESS_ROOT_HELPER")
    prefix = [override] if override else ["sudo", "-n", "/usr/local/libexec/pocketds-panel-root"]
    return prefix + ["brightness", screen, str(percent)]


def blank_helper_command() -> list[str]:
    override = os.environ.get("POCKETDS_BRIGHTNESS_ROOT_HELPER")
    prefix = (
        [override]
        if override
        else ["sudo", "-n", "/usr/local/libexec/pocketds-panel-root"]
    )
    return prefix + ["backlight-blank", "bottom"]


def set_hardware(screen: str, percent: int) -> None:
    result = subprocess.run(
        helper_command(screen, percent),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise RuntimeError(f"brightness helper failed for {screen}: {detail}")


def blank_bottom_backlight() -> None:
    result = subprocess.run(
        blank_helper_command(),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise RuntimeError(f"bottom backlight blank helper failed: {detail}")


def desired_percent(state: dict[str, Any], screen: str) -> int:
    return int(state[f"{screen}_percent"])


def drift(
    state: dict[str, Any],
    snapshot: dict[str, dict[str, int]] | None = None,
) -> dict[str, dict[str, int]]:
    if snapshot is None:
        snapshot = hardware_snapshot()
    result: dict[str, dict[str, int]] = {}
    for screen, current in snapshot.items():
        target_percent = desired_percent(state, screen)
        target_raw = raw_for_percent(current["max"], target_percent)
        if current["raw"] != target_raw:
            result[screen] = {
                "actual_raw": current["raw"],
                "target_raw": target_raw,
                "actual_percent": current["percent"],
                "target_percent": target_percent,
            }
    return result


def is_powerdevil_global_clobber(
    state: dict[str, Any],
    snapshot: dict[str, dict[str, int]],
    differences: dict[str, dict[str, int]],
) -> bool:
    """Recognize the strict two-backlight global overwrite seen on PowerDevil start."""

    if not differences or set(snapshot) != {"top", "bottom"}:
        return False
    if desired_percent(state, "top") == desired_percent(state, "bottom"):
        return False
    top_actual = snapshot["top"].get("percent")
    bottom_actual = snapshot["bottom"].get("percent")
    return (
        isinstance(top_actual, int)
        and not isinstance(top_actual, bool)
        and isinstance(bottom_actual, int)
        and not isinstance(bottom_actual, bool)
        and top_actual == bottom_actual
    )


def reconcile_decision(
    state: dict[str, Any],
    snapshot: dict[str, dict[str, int]],
    differences: dict[str, dict[str, int]],
    consecutive_mismatches: int,
) -> tuple[bool, int, str]:
    """Return whether to reconcile, the next debounce count, and an audit reason."""

    if not differences:
        return False, 0, "in-sync"
    if is_powerdevil_global_clobber(state, snapshot, differences):
        return True, 0, "powerdevil-global-clobber"
    next_mismatches = consecutive_mismatches + 1
    if next_mismatches >= 2:
        return True, 0, "debounced-drift"
    return False, next_mismatches, "debouncing"


def bottom_dpms_action(
    power: dict[str, int], snapshot: dict[str, dict[str, int]]
) -> str:
    """Bridge the SY7758 DPMS/brightness mismatch without changing preferences."""

    bottom_power = power.get("bottom")
    bottom = snapshot.get("bottom")
    if bottom_power is None or bottom is None:
        raise ValueError("bottom backlight state is missing")
    raw = bottom.get("raw")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise ValueError("bottom backlight brightness is invalid")
    if bottom_power != 0:
        return "blank" if raw != 0 else "hold"
    return "restore" if raw == 0 else "normal"


def lid_is_open() -> bool:
    """Do not turn a resumed closed-lid display back on from a brightness tick."""
    try:
        reply = subprocess.run(
            ["/usr/bin/busctl", "--system", "--timeout=500ms", "get-property",
             "org.freedesktop.login1", "/org/freedesktop/login1",
             "org.freedesktop.login1.Manager", "LidClosed"],
            capture_output=True, text=True, check=False, timeout=0.6,
        )
        return reply.returncode == 0 and reply.stdout.strip() == "b false"
    except (OSError, subprocess.TimeoutExpired):
        return False


def reconcile(state: dict[str, Any], dry_run: bool = False,
              screens: tuple[str, ...] | None = None) -> dict[str, Any]:
    differences = drift(state)
    if screens is not None:
        differences = {screen: value for screen, value in differences.items()
                       if screen in screens}
    applied: list[str] = []
    if not dry_run:
        for screen in ("top", "bottom"):
            if screen in differences:
                # Daemon reconciliation must never restore a powered-off screen.
                if screens is not None and (
                    not lid_is_open() or backlight_power_snapshot()[screen] != 0
                ):
                    continue
                set_hardware(screen, desired_percent(state, screen))
                applied.append(screen)
    return {"drift": differences, "applied": applied, "dry_run": dry_run}


def set_preference(screen: str, percent: int) -> dict[str, Any]:
    validate_percent(percent, "percent")
    target = state_path()
    with state_lock(target):
        try:
            state = load_state(target)
        except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
            snapshot = hardware_snapshot()
            state = make_state(
                snapshot["top"]["percent"],
                snapshot["bottom"]["percent"],
                "recovered-from-hardware",
            )
        set_hardware(screen, percent)
        state[f"{screen}_percent"] = percent
        state["updated_at"] = utc_now()
        state["source"] = "panel"
        atomic_write_state(state, target)
        return state


BusctlRunner = Callable[[list[str]], str]


def run_busctl(arguments: list[str]) -> str:
    command = ["busctl", "--user", "--timeout=2s", *arguments]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("busctl is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("PowerDevil D-Bus call timed out") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise RuntimeError(f"PowerDevil D-Bus call failed: {detail[:300]}")
    return result.stdout.strip()


def parse_busctl_string_array(output: str) -> list[str]:
    try:
        fields = shlex.split(output)
    except ValueError as exc:
        raise ValueError("invalid PowerDevil display list") from exc
    if len(fields) < 2 or fields[0] != "as":
        raise ValueError("unexpected PowerDevil display list type")
    try:
        count = int(fields[1])
    except ValueError as exc:
        raise ValueError("invalid PowerDevil display count") from exc
    names = fields[2:]
    if count != len(names) or not 1 <= count <= POWERDEVIL_MAX_DISPLAYS:
        raise ValueError("invalid PowerDevil display count")
    if len(set(names)) != len(names):
        raise ValueError("duplicate PowerDevil display child")
    if any(re.fullmatch(r"[A-Za-z0-9_]+", name) is None for name in names):
        raise ValueError("unsafe PowerDevil display child name")
    return names


def parse_busctl_int(output: str, property_name: str) -> int:
    fields = output.split()
    if len(fields) != 2 or fields[0] != "i":
        raise ValueError(f"unexpected {property_name} D-Bus type")
    try:
        value = int(fields[1])
    except ValueError as exc:
        raise ValueError(f"invalid {property_name} D-Bus value") from exc
    if not 1 <= value <= 2_147_483_647:
        raise ValueError(f"invalid {property_name} D-Bus range")
    return value


def powerdevil_neutral_plan(
    runner: BusctlRunner = run_busctl,
) -> list[dict[str, Any]]:
    display_output = runner(
        [
            "get-property",
            POWERDEVIL_SERVICE,
            POWERDEVIL_PATH,
            POWERDEVIL_INTERFACE,
            "DisplaysDBusNames",
        ]
    )
    display_names = parse_busctl_string_array(display_output)
    plan: list[dict[str, Any]] = []
    for name in display_names:
        path = f"{POWERDEVIL_PATH}/{name}"
        maximum = parse_busctl_int(
            runner(
                [
                    "get-property",
                    POWERDEVIL_SERVICE,
                    path,
                    POWERDEVIL_DISPLAY_INTERFACE,
                    "MaxBrightness",
                ]
            ),
            "MaxBrightness",
        )
        target = maximum * POWERDEVIL_NEUTRAL_PERCENT // 100
        plan.append(
            {
                "dbus_name": name,
                "path": path,
                "max_brightness": maximum,
                "target_brightness": target,
                "target_percent": POWERDEVIL_NEUTRAL_PERCENT,
            }
        )
    return plan


def apply_powerdevil_neutral_plan(
    plan: list[dict[str, Any]],
    runner: BusctlRunner = run_busctl,
) -> dict[str, Any]:
    applied: list[str] = []
    for item in plan:
        name = str(item["dbus_name"])
        try:
            runner(
                [
                    "call",
                    POWERDEVIL_SERVICE,
                    str(item["path"]),
                    POWERDEVIL_DISPLAY_INTERFACE,
                    "SetBrightness",
                    "iu",
                    str(item["target_brightness"]),
                    "1",
                ]
            )
        except RuntimeError as exc:
            completed = ",".join(applied) if applied else "none"
            raise RuntimeError(
                f"PowerDevil neutral sync stopped at {name}; "
                f"applied={completed}; cause={exc}"
            ) from exc
        applied.append(name)
    return {
        "neutral_percent": POWERDEVIL_NEUTRAL_PERCENT,
        "display_count": len(plan),
        "applied": applied,
    }


def request_stop(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True
    STOP_EVENT.set()


def daemon(interval: float, startup_delay: float) -> int:
    if startup_delay and STOP_EVENT.wait(startup_delay):
        return 0
    mismatches = 0
    bottom_blanked = False
    next_maintenance = 0.0
    while not STOP_REQUESTED:
        wait_interval = interval
        try:
            if bottom_blanked:
                bottom_power = read_int(backlight_paths()["bottom"] / "bl_power")
                if not 0 <= bottom_power <= 4:
                    raise ValueError("invalid bottom backlight power state")
                if bottom_power != 0 and time.monotonic() < next_maintenance:
                    STOP_EVENT.wait(BACKLIGHT_WAKE_POLL_INTERVAL_S)
                    continue
            next_maintenance = time.monotonic() + interval
            state, _ = initialize_state(capture_current=False)
            power = backlight_power_snapshot()
            snapshot = hardware_snapshot()
            dpms_action = bottom_dpms_action(power, snapshot)
            bottom_blanked = power["bottom"] != 0
            if dpms_action == "blank":
                blank_bottom_backlight()
                print(
                    json.dumps(
                        {"event": "bottom-backlight-blanked", "reason": "dpms-off"},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            active = {screen: value for screen, value in snapshot.items()
                      if power[screen] == 0}
            if dpms_action == "restore":
                result = reconcile(state, screens=tuple(active))
                mismatches = 0
                if result["applied"]:
                    print(
                        json.dumps(
                            {"event": "reconciled", "reason": "dpms-on", **result},
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
            else:
                differences = drift(state, active)
                should_apply, mismatches, reason = reconcile_decision(
                    state, active, differences, mismatches
                )
                if should_apply:
                    result = reconcile(state, screens=tuple(active))
                    if result["applied"]:
                        print(
                            json.dumps(
                                {"event": "reconciled", "reason": reason, **result},
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
        except Exception as exc:  # keep the safety monitor alive and visible in the journal
            print(f"brightness monitor error: {exc}", file=sys.stderr, flush=True)
            mismatches = 0
            bottom_blanked = False
        if bottom_blanked:
            wait_interval = min(interval, BACKLIGHT_WAKE_POLL_INTERVAL_S)
        STOP_EVENT.wait(wait_interval)
    return 0


def parse_percent(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("percent must be an integer") from exc
    if not MIN_PERCENT <= value <= MAX_PERCENT:
        raise argparse.ArgumentTypeError(
            f"percent must be between {MIN_PERCENT} and {MAX_PERCENT}"
        )
    return value


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser("initialize")
    initialize.add_argument("--capture-current", action="store_true")
    capture = subparsers.add_parser("capture")
    capture.add_argument("--source", default="capture")
    subparsers.add_parser("status")

    setter = subparsers.add_parser("set")
    setter.add_argument("screen", choices=("top", "bottom"))
    setter.add_argument("percent", type=parse_percent)

    apply = subparsers.add_parser("reconcile")
    apply.add_argument("--dry-run", action="store_true")

    monitor = subparsers.add_parser("daemon")
    monitor.add_argument("--interval", type=float, default=2.0)
    monitor.add_argument("--startup-delay", type=float, default=5.0)
    powerdevil_sync = subparsers.add_parser(
        "sync-powerdevil-neutral",
        help="plan a 100%% sync through the official PowerDevil display API",
    )
    powerdevil_sync.add_argument(
        "--apply",
        action="store_true",
        help="perform the calls; without this flag the command is read-only",
    )
    args = parser.parse_args(argv)
    if args.command == "daemon":
        if not 0.5 <= args.interval <= 60:
            parser.error("--interval must be between 0.5 and 60 seconds")
        if not 0 <= args.startup_delay <= 60:
            parser.error("--startup-delay must be between 0 and 60 seconds")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.command == "initialize":
            state, result = initialize_state(args.capture_current)
            print(json.dumps({"result": result, "state": state}, ensure_ascii=False))
        elif args.command == "capture":
            print(json.dumps(capture_state(args.source), ensure_ascii=False))
        elif args.command == "status":
            state = load_state()
            print(
                json.dumps(
                    {"state": state, "hardware": hardware_snapshot(), "drift": drift(state)},
                    ensure_ascii=False,
                )
            )
        elif args.command == "set":
            print(json.dumps(set_preference(args.screen, args.percent), ensure_ascii=False))
        elif args.command == "reconcile":
            print(json.dumps(reconcile(load_state(), args.dry_run), ensure_ascii=False))
        elif args.command == "daemon":
            signal.signal(signal.SIGINT, request_stop)
            signal.signal(signal.SIGTERM, request_stop)
            return daemon(args.interval, args.startup_delay)
        elif args.command == "sync-powerdevil-neutral":
            plan = powerdevil_neutral_plan()
            result: dict[str, Any] = {
                "dry_run": not args.apply,
                "neutral_percent": POWERDEVIL_NEUTRAL_PERCENT,
                "plan": plan,
            }
            if args.apply:
                result.update(apply_powerdevil_neutral_plan(plan))
            print(json.dumps(result, ensure_ascii=False))
    except (FileNotFoundError, json.JSONDecodeError, ValueError, RuntimeError, OSError) as exc:
        print(f"brightness error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

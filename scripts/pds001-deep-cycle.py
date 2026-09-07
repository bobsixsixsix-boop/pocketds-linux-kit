#!/usr/bin/env python3
"""Preflight or explicitly run bounded, RTC-rescued Pocket DS deep cycles."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Callable, Sequence


SCHEMA = "pocketds.deep-cycle.v3"
MODEL = "AYANEO Pocket DS"
MAX_TEXT_BYTES = 16_384
SYSTEM_SERVICES = (
    "NetworkManager.service",
    "inputplumber.service",
    "tuned.service",
    "pocketds-fancontrol.service",
)
USER_SERVICES = (
    "pocketds-keyboard.service",
    "pocketds-gpu-telemetry.service",
    "pocketds-brightness.service",
)
CONFIRMATION = "POCKETDS-DEEP-ONLY"
REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_REFERENCE = REPO_ROOT / "components/control-panel/pocketds-panel-root"
GUARD_REFERENCE = REPO_ROOT / "components/system/pocketds-deep-suspend.py"
BOOT_TOKEN_DOMAIN = b"pocketds.deep-cycle.boot-token.v1\0"
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
BATTERY_STATES = {
    "Charging": "charging",
    "Discharging": "discharging",
    "Full": "full",
    "Not charging": "not-charging",
    "Unknown": "unknown",
}


class AcceptanceError(RuntimeError):
    """A fail-closed preflight, execution or evidence error."""


def read_text(path: Path, maximum: int = MAX_TEXT_BYTES) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AcceptanceError("required state is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise AcceptanceError("required state is unsafe")
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(descriptor, min(4096, maximum + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > maximum:
                raise AcceptanceError("required state is oversized")
        try:
            return b"".join(chunks).decode("utf-8", errors="strict").strip("\x00\r\n ")
        except UnicodeDecodeError as exc:
            raise AcceptanceError("required state is not UTF-8") from exc
    finally:
        os.close(descriptor)


def _read_or_empty(path: Path) -> str:
    try:
        return read_text(path)
    except AcceptanceError:
        return ""


def _service_active(
    service: str,
    *,
    user: bool,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> bool:
    command = ["/usr/bin/systemctl"]
    if user:
        command.append("--user")
    command.extend(("is-active", "--quiet", service))
    try:
        result = runner(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def regular_file_sha256(path: Path, maximum: int = 131_072) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AcceptanceError("required helper is unavailable") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise AcceptanceError("required helper is unsafe")
        while True:
            block = os.read(descriptor, min(16_384, maximum + 1 - total))
            if not block:
                break
            total += len(block)
            if total > maximum:
                raise AcceptanceError("required helper is oversized")
            digest.update(block)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def trusted_file_matches(path: Path, reference: Path, expected_uid: int) -> bool:
    try:
        metadata = os.lstat(path)
        return (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_nlink == 1
            and metadata.st_uid == expected_uid
            and not (metadata.st_mode & 0o022)
            and os.access(path, os.X_OK)
            and regular_file_sha256(path) == regular_file_sha256(reference)
        )
    except (AcceptanceError, OSError):
        return False


def config_has_assignment(text: str, section: str, key: str, value: str) -> bool:
    current = ""
    matches = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            continue
        if "=" not in line or current != section:
            continue
        candidate_key, candidate_value = line.split("=", 1)
        if candidate_key.strip() == key and candidate_value.strip() == value:
            matches += 1
    return matches == 1


def battery_context(power_supply_root: Path) -> dict[str, object]:
    battery = power_supply_root / "battery"
    state = BATTERY_STATES.get(_read_or_empty(battery / "status"), "unavailable")
    try:
        capacity = int(read_text(battery / "capacity"))
        percent_valid = 0 <= capacity <= 100
    except (AcceptanceError, ValueError):
        percent_valid = False
    external_power: bool | None = None
    try:
        entries = list(os.scandir(power_supply_root))
    except OSError:
        entries = []
    for entry in entries:
        if entry.name == "battery" or entry.name in {".", ".."}:
            continue
        candidate = power_supply_root / entry.name
        supply_type = _read_or_empty(candidate / "type")
        online = _read_or_empty(candidate / "online")
        if not supply_type or supply_type == "Battery" or online not in {"0", "1"}:
            continue
        external_power = (external_power is True) or online == "1"
    return {
        "state": state,
        "external_power": external_power,
        "ready": percent_valid and state != "unavailable",
    }


def repository_identity(
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> tuple[str, bool]:
    outputs: list[bytes] = []
    for arguments in (("rev-parse", "HEAD"), ("status", "--porcelain=v1")):
        try:
            result = runner(
                ["/usr/bin/git", "-C", str(REPO_ROOT), *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AcceptanceError("repository identity is unavailable") from exc
        if result.returncode != 0 or len(result.stdout) > 65_536:
            raise AcceptanceError("repository identity failed or overflowed")
        outputs.append(result.stdout)
    try:
        revision = outputs[0].decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise AcceptanceError("repository revision is not ASCII") from exc
    if REVISION_RE.fullmatch(revision) is None:
        raise AcceptanceError("repository revision is invalid")
    return revision, not outputs[1].strip()


def boot_token(boot_id: str) -> str:
    if not boot_id or len(boot_id) > 128 or not boot_id.isascii():
        raise AcceptanceError("boot identity is invalid")
    return hashlib.sha256(BOOT_TOKEN_DOMAIN + boot_id.encode("ascii")).hexdigest()


def collect_snapshot(
    *,
    sys_root: Path = Path("/sys"),
    proc_root: Path = Path("/proc"),
    etc_root: Path = Path("/etc"),
    helper: Path = Path("/usr/local/libexec/pocketds-panel-root"),
    guard: Path = Path("/usr/local/libexec/pocketds-deep-suspend"),
    helper_reference: Path = HELPER_REFERENCE,
    guard_reference: Path = GUARD_REFERENCE,
    expected_helper_uid: int = 0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object]:
    model = _read_or_empty(proc_root / "device-tree/model")
    power = sys_root / "power"
    state_tokens = _read_or_empty(power / "state").split()
    mem_sleep_tokens = _read_or_empty(power / "mem_sleep").split()
    sleep_policy = _read_or_empty(
        etc_root / "systemd/sleep.conf.d/80-pocketds-sleep.conf"
    )
    lid_policy = _read_or_empty(
        etc_root / "systemd/logind.conf.d/80-pocketds-lid-safety.conf"
    )
    rtc = sys_root / "class/rtc/rtc0"
    rtc_wakeup = _read_or_empty(rtc / "device/power/wakeup")
    rtc_alarm = _read_or_empty(rtc / "wakealarm")

    displays = {
        name: _read_or_empty(sys_root / f"class/drm/card0-{name}/status") == "connected"
        for name in ("DSI-1", "DSI-2")
    }
    backlights: dict[str, bool] = {}
    for name, relative in (
        ("top", "class/backlight/ae94000.dsi.0"),
        ("bottom", "class/backlight/sy7758-backlight"),
    ):
        base = sys_root / relative
        try:
            value = int(read_text(base / "brightness"))
            maximum = int(read_text(base / "max_brightness"))
            backlights[name] = 0 <= value <= maximum and maximum > 0
        except (AcceptanceError, ValueError):
            backlights[name] = False

    system_services = {
        service: _service_active(service, user=False, runner=runner)
        for service in SYSTEM_SERVICES
    }
    user_services = {
        service: _service_active(service, user=True, runner=runner)
        for service in USER_SERVICES
    }
    battery = battery_context(sys_root / "class/power_supply")
    audio_cards = _read_or_empty(proc_root / "asound/cards")
    audio_ready = bool(audio_cards) and "no soundcards" not in audio_cards.lower()
    helper_ready = trusted_file_matches(helper, helper_reference, expected_helper_uid)
    guard_ready = trusted_file_matches(guard, guard_reference, expected_helper_uid)

    gates = {
        "model_exact": model == MODEL,
        "mem_supported": "mem" in state_tokens,
        "deep_selected": "[deep]" in mem_sleep_tokens,
        "callbacks_serial": _read_or_empty(power / "pm_async") == "0",
        "deep_only_policy": (
            config_has_assignment(sleep_policy, "Sleep", "SuspendState", "mem")
            and config_has_assignment(
                sleep_policy, "Sleep", "MemorySleepMode", "deep"
            )
        ),
        "suspend_explicitly_enabled": config_has_assignment(
            sleep_policy, "Sleep", "AllowSuspend", "yes"
        ),
        "hardware_actions_safe_ignore": (
            config_has_assignment(
                lid_policy, "Login", "HandleLidSwitch", "ignore"
            )
            and config_has_assignment(
                lid_policy, "Login", "HandlePowerKey", "ignore"
            )
        ),
        "rtc_wake_enabled": rtc_wakeup == "enabled",
        "rtc_alarm_clear": rtc_alarm == "",
        "displays_connected": all(displays.values()),
        "backlights_readable": all(backlights.values()),
        "system_services_active": all(system_services.values()),
        "user_services_active": all(user_services.values()),
        "battery_readable": battery["ready"] is True,
        "audio_card_present": audio_ready,
        "privileged_helper_current": helper_ready,
        "privileged_guard_current": guard_ready,
    }
    return {
        "gates": gates,
        "displays": displays,
        "backlights": backlights,
        "system_services": system_services,
        "user_services": user_services,
        "battery": {
            "state": battery["state"],
            "external_power": battery["external_power"],
        },
        "ready": all(gates.values()),
    }


def evaluate_cycle(
    before: dict[str, object],
    after: dict[str, object],
    *,
    helper_returncode: int,
    elapsed_boottime_s: float,
    rtc_seconds: int,
    boot_id_unchanged: bool,
    source_binding_unchanged: bool = True,
) -> dict[str, object]:
    gates = {
        "helper_returned_success": helper_returncode == 0,
        "boot_id_unchanged": boot_id_unchanged,
        "source_binding_unchanged": source_binding_unchanged,
        "minimum_sleep_elapsed": elapsed_boottime_s >= min(10.0, rtc_seconds * 0.5),
        "bounded_resume_elapsed": elapsed_boottime_s <= rtc_seconds + 120.0,
        "preflight_ready": before.get("ready") is True,
        "post_resume_ready": after.get("ready") is True,
    }
    return {
        "elapsed_boottime_s": round(elapsed_boottime_s, 3),
        "gates": gates,
        "accepted": all(gates.values()),
        "before": before,
        "after": after,
    }


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = os.lstat(path.parent)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o022
    ):
        raise AcceptanceError("output parent is unsafe")


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short report write")
        view = view[written:]


def create_report(path: Path, report: dict[str, object]) -> None:
    _validate_parent(path)
    payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def checkpoint_report(path: Path, report: dict[str, object]) -> None:
    metadata = os.lstat(path)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise AcceptanceError("report destination changed or is unsafe")
    payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _boot_id(proc_root: Path = Path("/proc")) -> str:
    value = read_text(proc_root / "sys/kernel/random/boot_id")
    if not value:
        raise AcceptanceError("boot identity is unavailable")
    return value


def _boottime() -> float:
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    if clock is None:
        raise AcceptanceError("CLOCK_BOOTTIME is unavailable")
    return time.clock_gettime(clock)


def _lock_runtime() -> object:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    lock_path = runtime / "pocketds-deep-cycle.lock"
    descriptor = os.open(
        lock_path,
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    stream = os.fdopen(descriptor, "r+", encoding="utf-8")
    metadata = os.fstat(stream.fileno())
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        stream.close()
        raise AcceptanceError("runtime lock is unsafe")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        stream.close()
        raise AcceptanceError("another deep-cycle acceptance is active") from exc
    return stream


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--rtc-seconds", type=int, default=60)
    parser.add_argument("--settle-seconds", type=float, default=10.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--helper", type=Path, default=Path("/usr/local/libexec/pocketds-panel-root")
    )
    parser.add_argument(
        "--guard", type=Path, default=Path("/usr/local/libexec/pocketds-deep-suspend")
    )
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.attempts <= 10:
        parser.error("--attempts must be between 1 and 10")
    if not 60 <= arguments.rtc_seconds <= 300:
        parser.error("--rtc-seconds must be between 60 and 300")
    if not 1.0 <= arguments.settle_seconds <= 60.0:
        parser.error("--settle-seconds must be between 1 and 60")
    if arguments.execute and arguments.output is None:
        parser.error("--output is required with --execute")
    if arguments.execute and (
        arguments.confirm != CONFIRMATION
        or os.environ.get("POCKETDS_ALLOW_DEEP_CYCLE") != "YES"
    ):
        parser.error("execution requires the exact confirmation and environment gate")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    snapshot = collect_snapshot(helper=arguments.helper, guard=arguments.guard)
    if not arguments.execute:
        print(json.dumps({"schema": SCHEMA, "execute": False, **snapshot}, indent=2))
        return 0 if snapshot["ready"] else 1

    assert arguments.output is not None
    try:
        source_revision, source_clean = repository_identity()
        source_binding = {
            "repo_revision": source_revision,
            "helper_reference_sha256": regular_file_sha256(HELPER_REFERENCE),
            "guard_reference_sha256": regular_file_sha256(GUARD_REFERENCE),
        }
        initial_boot_id = _boot_id()
        private_boot_token = boot_token(initial_boot_id)
    except (AcceptanceError, OSError) as exc:
        print(f"deep-cycle acceptance failed: {exc}", file=sys.stderr)
        return 2
    report: dict[str, object] = {
        "schema": SCHEMA,
        "execute": True,
        "rtc_rescue_seconds": arguments.rtc_seconds,
        "requested_attempts": arguments.attempts,
        "phase": "preflight",
        "complete": False,
        "accepted": False,
        "source_binding": source_binding,
        "source_binding_unchanged": False,
        "boot_token": private_boot_token,
        "cycles": [],
    }
    report_created = False
    lock: object | None = None
    try:
        create_report(arguments.output, report)
        report_created = True
        lock = _lock_runtime()
        if not source_clean:
            raise AcceptanceError("repository is not clean")
        if not snapshot["ready"]:
            raise AcceptanceError("preflight did not pass")
        cycles: list[dict[str, object]] = []
        for index in range(arguments.attempts):
            report["phase"] = "suspend-entering"
            report["current_attempt"] = index + 1
            checkpoint_report(arguments.output, report)
            started = _boottime()
            try:
                result = subprocess.run(
                    [
                        "/usr/bin/sudo",
                        "-n",
                        str(arguments.helper),
                        "deep-suspend",
                        str(arguments.rtc_seconds),
                    ],
                    check=False,
                )
                returncode = result.returncode
            except OSError:
                returncode = 125
            elapsed = _boottime() - started
            time.sleep(arguments.settle_seconds)
            after = collect_snapshot(helper=arguments.helper, guard=arguments.guard)
            current_revision, current_clean = repository_identity()
            binding_unchanged = bool(
                current_clean
                and current_revision == source_revision
                and regular_file_sha256(HELPER_REFERENCE)
                == source_binding["helper_reference_sha256"]
                and regular_file_sha256(GUARD_REFERENCE)
                == source_binding["guard_reference_sha256"]
            )
            cycle = evaluate_cycle(
                snapshot,
                after,
                helper_returncode=returncode,
                elapsed_boottime_s=elapsed,
                rtc_seconds=arguments.rtc_seconds,
                boot_id_unchanged=_boot_id() == initial_boot_id,
                source_binding_unchanged=binding_unchanged,
            )
            cycles.append(cycle)
            report["cycles"] = cycles
            report["phase"] = "post-resume"
            checkpoint_report(arguments.output, report)
            if not cycle["accepted"]:
                break
            snapshot = after
        report["phase"] = "completed"
        report["complete"] = len(cycles) == arguments.attempts
        final_revision, final_clean = repository_identity()
        report["source_binding_unchanged"] = bool(
            final_clean
            and final_revision == source_revision
            and regular_file_sha256(HELPER_REFERENCE)
            == source_binding["helper_reference_sha256"]
            and regular_file_sha256(GUARD_REFERENCE)
            == source_binding["guard_reference_sha256"]
        )
        report["accepted"] = report["complete"] and all(
            cycle.get("accepted") is True for cycle in cycles
        ) and report["source_binding_unchanged"] is True
        checkpoint_report(arguments.output, report)
        return 0 if report["accepted"] else 1
    except (AcceptanceError, OSError) as exc:
        report["phase"] = "failed"
        report["error"] = str(exc)
        if report_created:
            try:
                checkpoint_report(arguments.output, report)
            except (AcceptanceError, OSError):
                pass
        print(f"deep-cycle acceptance failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())

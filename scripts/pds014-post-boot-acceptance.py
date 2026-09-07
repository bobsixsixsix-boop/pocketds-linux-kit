#!/usr/bin/env python3
"""Run the bounded read-only PDS-014 matrix soon after a deliberate boot."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
from typing import Sequence


SCHEMA = "pocketds.post-boot-acceptance.v2"
CONFIRMATION = "POCKETDS-POST-BOOT-READONLY"
MAX_CAPTURE_CHARS = 1_048_576
MAX_STATE_BYTES = 64 * 1024
BOOT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BACKLIGHTS = {
    "top": Path("/sys/class/backlight/ae94000.dsi.0"),
    "bottom": Path("/sys/class/backlight/sy7758-backlight"),
}
RENESAS_VENDOR = "0x1912"
RENESAS_DEVICE = "0x0014"
RENESAS_DRIVER = "xhci-pci-renesas"
SYSTEM_SERVICES = (
    "pocketds-fancontrol.service",
    "inputplumber.service",
    "tuned.service",
)
USER_SERVICES = (
    "pocketds-keyboard.service",
    "pocketds-gpu-telemetry.service",
    "pocketds-brightness.service",
    "plasma-plasmashell.service",
)


class AcceptanceError(RuntimeError):
    """A post-boot gate was unsafe, unavailable or ambiguous."""


@dataclass(frozen=True)
class Step:
    name: str
    argv: tuple[str, ...]
    timeout: float


@dataclass
class Result:
    name: str
    status: str
    returncode: int
    seconds: float
    stdout: str
    stderr: str


def _service_command(*, user: bool, units: tuple[str, ...]) -> tuple[str, ...]:
    prefix = ("systemctl", "--user") if user else ("systemctl",)
    return prefix + (
        "show",
        "--no-pager",
        "--property=Id,ActiveState,SubState,NRestarts,MainPID",
        *units,
    )


def build_plan(repo_root: Path) -> list[Step]:
    make = ("make", "-C", str(repo_root))
    return [
        Step("repo-revision-before", ("git", "-C", str(repo_root), "rev-parse", "HEAD"), 10),
        Step(
            "repo-status-before",
            ("git", "-C", str(repo_root), "status", "--porcelain=v1", "--untracked-files=normal"),
            10,
        ),
        Step("system-services-before", _service_command(user=False, units=SYSTEM_SERVICES), 15),
        Step("user-services-before", _service_command(user=True, units=USER_SERVICES), 15),
        Step("full-tests", make + ("test",), 240),
        Step("hardware-readonly", make + ("test-hardware",), 90),
        Step("suspend-preflight", make + ("test-suspend",), 90),
        Step("diagnostic-bundle", make + ("diagnostic-bundle",), 240),
        Step("repo-revision-after", ("git", "-C", str(repo_root), "rev-parse", "HEAD"), 10),
        Step(
            "repo-status-after",
            ("git", "-C", str(repo_root), "status", "--porcelain=v1", "--untracked-files=normal"),
            10,
        ),
        Step("system-services-after", _service_command(user=False, units=SYSTEM_SERVICES), 15),
        Step("user-services-after", _service_command(user=True, units=USER_SERVICES), 15),
    ]


class Runner:
    def __init__(self) -> None:
        self.child: subprocess.Popen[str] | None = None
        self.cancelled = False

    def cancel(self, *_unused: object) -> None:
        self.cancelled = True
        if self.child is not None and self.child.poll() is None:
            try:
                os.killpg(self.child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def run(self, step: Step) -> Result:
        if self.cancelled:
            return Result(step.name, "cancelled", 130, 0.0, "", "")
        started = time.monotonic()
        try:
            self.child = subprocess.Popen(
                step.argv,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env={**os.environ, "LC_ALL": "C"},
            )
        except OSError as exc:
            return Result(step.name, "error", 127, 0.0, "", str(exc))
        try:
            stdout, stderr = self.child.communicate(timeout=step.timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.child.pid, signal.SIGTERM)
                stdout, stderr = self.child.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(self.child.pid, signal.SIGKILL)
                stdout, stderr = self.child.communicate()
            return Result(
                step.name,
                "cancelled" if self.cancelled else "timeout",
                130 if self.cancelled else 124,
                round(time.monotonic() - started, 3),
                stdout[:MAX_CAPTURE_CHARS],
                stderr[:MAX_CAPTURE_CHARS],
            )
        finally:
            child = self.child
            self.child = None
        if len(stdout) > MAX_CAPTURE_CHARS or len(stderr) > MAX_CAPTURE_CHARS:
            return Result(
                step.name,
                "overflow",
                125,
                round(time.monotonic() - started, 3),
                stdout[:MAX_CAPTURE_CHARS],
                stderr[:MAX_CAPTURE_CHARS],
            )
        status = "cancelled" if self.cancelled else ("pass" if child.returncode == 0 else "fail")
        return Result(
            step.name,
            status,
            130 if self.cancelled else child.returncode,
            round(time.monotonic() - started, 3),
            stdout,
            stderr,
        )


def _result(results: list[Result], name: str) -> Result | None:
    return next((item for item in results if item.name == name), None)


def parse_services(text: str, expected: tuple[str, ...]) -> dict[str, tuple[int, int]]:
    found: dict[str, tuple[int, int]] = {}
    for block in re.split(r"\n\s*\n", text.strip()):
        values: dict[str, str] = {}
        for line in block.splitlines():
            if "=" not in line:
                raise AcceptanceError("service snapshot contains an invalid line")
            key, value = line.split("=", 1)
            if key in values:
                raise AcceptanceError("service snapshot contains duplicate fields")
            values[key] = value
        if set(values) != {"Id", "ActiveState", "SubState", "NRestarts", "MainPID"}:
            raise AcceptanceError("service snapshot fields are incomplete")
        unit = values["Id"]
        if unit not in expected or unit in found:
            raise AcceptanceError("service snapshot identity is unexpected")
        try:
            restarts = int(values["NRestarts"])
            pid = int(values["MainPID"])
        except ValueError as exc:
            raise AcceptanceError("service snapshot counters are invalid") from exc
        if (
            values["ActiveState"] != "active"
            or values["SubState"] != "running"
            or restarts != 0
            or pid <= 0
        ):
            raise AcceptanceError("required service is unhealthy")
        found[unit] = (pid, restarts)
    if set(found) != set(expected):
        raise AcceptanceError("service snapshot is missing required units")
    return found


def _stable_service_gate(
    results: list[Result], before_name: str, after_name: str, units: tuple[str, ...]
) -> bool:
    before = _result(results, before_name)
    after = _result(results, after_name)
    if not before or not after or before.status != "pass" or after.status != "pass":
        return False
    try:
        return parse_services(before.stdout, units) == parse_services(after.stdout, units)
    except AcceptanceError:
        return False


def evaluate(
    results: list[Result],
    *,
    boot_token: str,
    boot_unchanged: bool,
    boot_observations_before: dict[str, bool],
    boot_observations_after: dict[str, bool],
    start_uptime_s: float,
    end_uptime_s: float,
    max_start_uptime_s: int,
) -> dict[str, object]:
    revision_before = _result(results, "repo-revision-before")
    revision_after = _result(results, "repo-revision-after")
    status_before = _result(results, "repo-status-before")
    status_after = _result(results, "repo-status-after")
    diagnostic = _result(results, "diagnostic-bundle")
    before_value = (
        revision_before.stdout.strip()
        if revision_before and revision_before.status == "pass"
        else ""
    )
    after_value = (
        revision_after.stdout.strip()
        if revision_after and revision_after.status == "pass"
        else ""
    )
    expected_names = [step.name for step in build_plan(Path("/repo"))]
    all_steps_pass = [result.name for result in results] == expected_names and all(
        result.status == "pass" for result in results
    )
    observation_names = {
        "brightness_targets_restored",
        "renesas_xhci_wake_disabled",
    }
    if (
        not SHA256_RE.fullmatch(boot_token)
        or set(boot_observations_before) != observation_names
        or set(boot_observations_after) != observation_names
        or any(type(value) is not bool for value in boot_observations_before.values())
        or any(type(value) is not bool for value in boot_observations_after.values())
    ):
        raise AcceptanceError("boot observation identity is invalid")
    gates = {
        "started_in_post_boot_window": 0 <= start_uptime_s <= max_start_uptime_s,
        "uptime_monotonic": end_uptime_s >= start_uptime_s,
        "boot_unchanged": boot_unchanged,
        "repo_revision_unchanged": bool(
            re.fullmatch(r"[0-9a-f]{40}", before_value)
            and before_value == after_value
        ),
        "repo_clean_before_after": bool(
            status_before
            and status_after
            and status_before.status == "pass"
            and status_after.status == "pass"
            and not status_before.stdout.strip()
            and not status_after.stdout.strip()
        ),
        "all_steps_passed": all_steps_pass,
        "system_services_same_pid_zero_restarts": _stable_service_gate(
            results, "system-services-before", "system-services-after", SYSTEM_SERVICES
        ),
        "user_services_same_pid_zero_restarts": _stable_service_gate(
            results, "user-services-before", "user-services-after", USER_SERVICES
        ),
        "diagnostic_bundle_created": bool(
            diagnostic
            and diagnostic.status == "pass"
            and "Diagnostic bundle:" in diagnostic.stdout
            and "It remains local" in diagnostic.stdout
        ),
        "brightness_targets_restored": bool(
            boot_observations_before["brightness_targets_restored"]
            and boot_observations_after["brightness_targets_restored"]
        ),
        "renesas_xhci_wake_disabled": bool(
            boot_observations_before["renesas_xhci_wake_disabled"]
            and boot_observations_after["renesas_xhci_wake_disabled"]
        ),
    }
    complete = all(gates.values())
    return {
        "schema": SCHEMA,
        "mode": "deliberate-post-boot-readonly-matrix",
        "privacy": {
            "boot_id_emitted": False,
            "command_output_emitted": False,
            "diagnostic_path_emitted": False,
            "host_or_user_emitted": False,
        },
        "boot_token": boot_token,
        "repo_revision": before_value if gates["repo_revision_unchanged"] else None,
        "start_uptime_s": round(start_uptime_s, 3),
        "end_uptime_s": round(end_uptime_s, 3),
        "max_start_uptime_s": max_start_uptime_s,
        "steps": [
            {
                "name": result.name,
                "status": result.status,
                "returncode": result.returncode,
                "seconds": result.seconds,
            }
            for result in results
        ],
        "gates": gates,
        "complete": complete,
        "result": "PASS" if complete else "FAIL",
    }


def _read_boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise AcceptanceError("boot identity is unavailable") from exc
    if BOOT_ID_RE.fullmatch(value) is None:
        raise AcceptanceError("boot identity is invalid")
    return value


def boot_token(boot_id: str) -> str:
    if BOOT_ID_RE.fullmatch(boot_id) is None:
        raise AcceptanceError("boot identity is invalid")
    return hashlib.sha256(f"{SCHEMA}\0{boot_id}".encode("ascii")).hexdigest()


def _read_small_text(
    path: Path,
    *,
    expected_uid: int | None = None,
    expected_mode: int | None = None,
) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AcceptanceError("boot observation input is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (expected_uid is not None and metadata.st_uid != expected_uid)
            or (
                expected_mode is not None
                and stat.S_IMODE(metadata.st_mode) != expected_mode
            )
            or not 0 < metadata.st_size <= MAX_STATE_BYTES
        ):
            raise AcceptanceError("boot observation input is unsafe")
        content = bytearray()
        while len(content) <= MAX_STATE_BYTES:
            block = os.read(
                descriptor,
                min(4096, MAX_STATE_BYTES + 1 - len(content)),
            )
            if not block:
                break
            content.extend(block)
        if not content or len(content) > MAX_STATE_BYTES:
            raise AcceptanceError("boot observation input is empty or oversized")
        if expected_mode is not None and len(content) != metadata.st_size:
            raise AcceptanceError("boot observation input changed")
        return bytes(content).decode("utf-8", errors="strict").strip()
    except UnicodeError as exc:
        raise AcceptanceError("boot observation input is not UTF-8") from exc
    finally:
        os.close(descriptor)


def _strict_json_object(text: str) -> dict[str, object]:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise AcceptanceError("brightness state has duplicate keys")
            result[key] = value
        return result

    try:
        value = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                AcceptanceError("brightness state has a non-finite number")
            ),
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise AcceptanceError("brightness state is invalid JSON") from exc
    if type(value) is not dict:
        raise AcceptanceError("brightness state is not an object")
    return value


def brightness_targets_restored(
    *,
    state_path: Path | None = None,
    backlights: dict[str, Path] = BACKLIGHTS,
) -> bool:
    target = state_path or Path(
        os.environ.get(
            "POCKETDS_BRIGHTNESS_STATE",
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            / "pocketds"
            / "brightness.json",
        )
    )
    try:
        state = _strict_json_object(
            _read_small_text(target, expected_uid=os.getuid(), expected_mode=0o600)
        )
        if set(state) != {
            "schema",
            "top_percent",
            "bottom_percent",
            "updated_at",
            "source",
        } or type(state["schema"]) is not int or state["schema"] != 1:
            return False
        for name, path in backlights.items():
            percent = state.get(f"{name}_percent")
            if type(percent) is not int or not 5 <= percent <= 100:
                return False
            maximum = int(_read_small_text(path / "max_brightness"))
            current = int(_read_small_text(path / "brightness"))
            if maximum <= 0 or current != maximum * percent // 100:
                return False
        return set(backlights) == {"top", "bottom"}
    except (AcceptanceError, OSError, ValueError):
        return False


def renesas_xhci_wake_disabled(pci_root: Path = Path("/sys/bus/pci/devices")) -> bool:
    matches: list[Path] = []
    try:
        entries = list(pci_root.iterdir())
    except OSError:
        return False
    for entry in entries:
        try:
            if (
                _read_small_text(entry / "vendor") != RENESAS_VENDOR
                or _read_small_text(entry / "device") != RENESAS_DEVICE
                or (entry / "driver").resolve(strict=True).name != RENESAS_DRIVER
            ):
                continue
            matches.append(entry)
        except (AcceptanceError, OSError):
            continue
    return len(matches) == 1 and _read_observation_wakeup(matches[0])


def _read_observation_wakeup(controller: Path) -> bool:
    try:
        return _read_small_text(controller / "power" / "wakeup") == "disabled"
    except AcceptanceError:
        return False


def collect_boot_observations() -> dict[str, bool]:
    return {
        "brightness_targets_restored": brightness_targets_restored(),
        "renesas_xhci_wake_disabled": renesas_xhci_wake_disabled(),
    }


def _read_uptime() -> float:
    try:
        value = float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError) as exc:
        raise AcceptanceError("boot uptime is unavailable") from exc
    if not math.isfinite(value) or value < 0:
        raise AcceptanceError("boot uptime is invalid")
    return value


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short post-boot report write")
        view = view[written:]


def write_report(report: dict[str, object], destination: Path) -> None:
    content = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(destination, flags, 0o600)
    try:
        _write_all(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-start-uptime-seconds", type=int, default=900)
    arguments = parser.parse_args(argv)
    if not 300 <= arguments.max_start_uptime_seconds <= 1_800:
        parser.error("--max-start-uptime-seconds must be between 300 and 1800")
    if arguments.execute:
        if arguments.confirm != CONFIRMATION or arguments.output is None:
            parser.error("execution needs the exact confirmation and a new --output path")
    elif arguments.confirm is not None or arguments.output is not None:
        parser.error("--confirm/--output are valid only with --execute")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = Path(__file__).resolve().parents[1]
    if not arguments.execute:
        print("post-boot acceptance plan (no commands executed):")
        for step in build_plan(repo_root):
            print(f"- {step.name}")
        return 0
    runner = Runner()
    signal.signal(signal.SIGINT, runner.cancel)
    signal.signal(signal.SIGTERM, runner.cancel)
    try:
        if arguments.output.exists() or arguments.output.is_symlink():
            raise AcceptanceError("post-boot report target already exists")
        if not arguments.output.parent.is_dir() or arguments.output.parent.is_symlink():
            raise AcceptanceError("post-boot report parent is unavailable or linked")
        start_uptime = _read_uptime()
        if start_uptime > arguments.max_start_uptime_seconds:
            raise AcceptanceError("outside the deliberate post-boot window")
        boot_id = _read_boot_id()
        token = boot_token(boot_id)
        observations_before = collect_boot_observations()
        results: list[Result] = []
        for step in build_plan(repo_root):
            result = runner.run(step)
            results.append(result)
            if result.status in {"cancelled", "timeout", "overflow", "error"}:
                break
        report = evaluate(
            results,
            boot_token=token,
            boot_unchanged=_read_boot_id() == boot_id,
            boot_observations_before=observations_before,
            boot_observations_after=collect_boot_observations(),
            start_uptime_s=start_uptime,
            end_uptime_s=_read_uptime(),
            max_start_uptime_s=arguments.max_start_uptime_seconds,
        )
        write_report(report, arguments.output)
    except (AcceptanceError, OSError) as exc:
        print(f"post-boot acceptance failed: {exc}", file=sys.stderr)
        return 2
    if any(result.status == "cancelled" for result in results):
        return 130
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

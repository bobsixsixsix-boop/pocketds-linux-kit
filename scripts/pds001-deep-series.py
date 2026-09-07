#!/usr/bin/env python3
"""Aggregate four private PDS-001 v2 reports into a bounded deep matrix."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Sequence


INPUT_SCHEMA = "pocketds.deep-cycle.v2"
OUTPUT_SCHEMA = "pocketds.deep-series.v1"
MAX_REPORT_BYTES = 2 * 1024 * 1024
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SYSTEM_SERVICES = {
    "NetworkManager.service",
    "inputplumber.service",
    "tuned.service",
    "pocketds-fancontrol.service",
}
USER_SERVICES = {
    "pocketds-keyboard.service",
    "pocketds-gpu-telemetry.service",
    "pocketds-brightness.service",
}
SNAPSHOT_GATES = {
    "model_exact",
    "mem_supported",
    "deep_selected",
    "callbacks_serial",
    "deep_only_policy",
    "hardware_actions_safe_ignore",
    "rtc_wake_enabled",
    "rtc_alarm_clear",
    "displays_connected",
    "backlights_readable",
    "system_services_active",
    "user_services_active",
    "battery_readable",
    "audio_card_present",
    "privileged_helper_current",
}
CYCLE_GATES = {
    "helper_returned_success",
    "boot_id_unchanged",
    "source_binding_unchanged",
    "minimum_sleep_elapsed",
    "bounded_resume_elapsed",
    "preflight_ready",
    "post_resume_ready",
}
ROLE_POLICY = {
    "discharging-short": {
        "rtc_seconds": 30,
        "attempts": 10,
        "state": "discharging",
        "external_power": False,
    },
    "charging-short": {
        "rtc_seconds": 30,
        "attempts": 10,
        "state": "charging",
        "external_power": True,
    },
    "full-short": {
        "rtc_seconds": 30,
        "attempts": 5,
        "state": "full",
        "external_power": True,
    },
    "discharging-five-minute": {
        "rtc_seconds": 300,
        "attempts": 3,
        "state": "discharging",
        "external_power": False,
    },
}


class SeriesError(RuntimeError):
    """Unsafe, malformed or ambiguous deep-cycle series evidence."""


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short deep-series report write")
        view = view[written:]


def read_private(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SeriesError("deep-cycle report is unavailable or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= MAX_REPORT_BYTES
        ):
            raise SeriesError("deep-cycle report is unsafe, empty or oversized")
        remaining = metadata.st_size
        content = bytearray()
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise SeriesError("deep-cycle report changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise SeriesError("deep-cycle report grew while reading")
        return bytes(content)
    finally:
        os.close(descriptor)


def strict_json(content: bytes) -> object:
    def reject_constant(value: str) -> object:
        raise SeriesError(f"deep-cycle report contains non-finite JSON: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise SeriesError("deep-cycle report contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        return json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise SeriesError("deep-cycle report is not UTF-8") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        raise SeriesError("deep-cycle report is invalid JSON") from exc


def _exact_bool_map(value: object, keys: set[str], name: str) -> dict[str, bool]:
    if not isinstance(value, dict) or set(value) != keys:
        raise SeriesError(f"{name} fields are invalid")
    if any(not isinstance(item, bool) for item in value.values()):
        raise SeriesError(f"{name} values are invalid")
    return value


def _integer(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise SeriesError(f"invalid {name}")
    return value


def _number(value: object, name: str, low: float, high: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not low <= float(value) <= high
    ):
        raise SeriesError(f"invalid {name}")
    return float(value)


def parse_snapshot(raw: object) -> dict[str, object]:
    fields = {
        "gates",
        "displays",
        "backlights",
        "system_services",
        "user_services",
        "battery",
        "ready",
    }
    if not isinstance(raw, dict) or set(raw) != fields:
        raise SeriesError("deep snapshot fields are invalid")
    gates = _exact_bool_map(raw["gates"], SNAPSHOT_GATES, "snapshot gate")
    displays = _exact_bool_map(raw["displays"], {"DSI-1", "DSI-2"}, "display")
    backlights = _exact_bool_map(raw["backlights"], {"top", "bottom"}, "backlight")
    system_services = _exact_bool_map(
        raw["system_services"], SYSTEM_SERVICES, "system service"
    )
    user_services = _exact_bool_map(
        raw["user_services"], USER_SERVICES, "user service"
    )
    battery = raw["battery"]
    if (
        not isinstance(battery, dict)
        or set(battery) != {"state", "external_power"}
        or battery["state"]
        not in {"charging", "discharging", "full", "not-charging", "unknown"}
        or (battery["external_power"] is not None and not isinstance(battery["external_power"], bool))
    ):
        raise SeriesError("battery context is invalid")
    if not isinstance(raw["ready"], bool):
        raise SeriesError("snapshot readiness is invalid")
    return {
        "gates": gates,
        "displays": displays,
        "backlights": backlights,
        "system_services": system_services,
        "user_services": user_services,
        "battery": battery,
        "ready": raw["ready"],
    }


def parse_report(raw: object) -> dict[str, object]:
    fields = {
        "schema",
        "execute",
        "rtc_rescue_seconds",
        "requested_attempts",
        "phase",
        "complete",
        "accepted",
        "source_binding",
        "source_binding_unchanged",
        "boot_token",
        "cycles",
        "current_attempt",
    }
    if not isinstance(raw, dict) or set(raw) != fields or raw.get("schema") != INPUT_SCHEMA:
        raise SeriesError("deep-cycle report root is invalid")
    if (
        raw["execute"] is not True
        or raw["phase"] != "completed"
        or raw["complete"] is not True
        or raw["accepted"] is not True
        or raw["source_binding_unchanged"] is not True
    ):
        raise SeriesError("deep-cycle report is not a completed success")
    rtc_seconds = _integer(raw["rtc_rescue_seconds"], "RTC seconds", 15, 300)
    attempts = _integer(raw["requested_attempts"], "requested attempts", 1, 10)
    current_attempt = _integer(raw["current_attempt"], "current attempt", 1, 10)
    if current_attempt != attempts:
        raise SeriesError("deep-cycle attempt checkpoint is inconsistent")
    binding = raw["source_binding"]
    if (
        not isinstance(binding, dict)
        or set(binding) != {"repo_revision", "helper_reference_sha256"}
        or not isinstance(binding["repo_revision"], str)
        or HEX40.fullmatch(binding["repo_revision"]) is None
        or not isinstance(binding["helper_reference_sha256"], str)
        or HEX64.fullmatch(binding["helper_reference_sha256"]) is None
        or not isinstance(raw["boot_token"], str)
        or HEX64.fullmatch(raw["boot_token"]) is None
    ):
        raise SeriesError("deep-cycle binding is invalid")
    raw_cycles = raw["cycles"]
    if not isinstance(raw_cycles, list) or len(raw_cycles) != attempts:
        raise SeriesError("deep-cycle cycle count is invalid")
    cycles: list[dict[str, object]] = []
    for raw_cycle in raw_cycles:
        if not isinstance(raw_cycle, dict) or set(raw_cycle) != {
            "elapsed_boottime_s",
            "gates",
            "accepted",
            "before",
            "after",
        }:
            raise SeriesError("deep cycle fields are invalid")
        gates = _exact_bool_map(raw_cycle["gates"], CYCLE_GATES, "cycle gate")
        if raw_cycle["accepted"] is not True or not all(gates.values()):
            raise SeriesError("deep cycle is not accepted")
        elapsed = _number(raw_cycle["elapsed_boottime_s"], "elapsed boottime", 0, 420)
        if elapsed < min(10.0, rtc_seconds * 0.5) or elapsed > rtc_seconds + 120.0:
            raise SeriesError("deep cycle elapsed time contradicts its accepted gate")
        cycles.append(
            {
                "elapsed_boottime_s": elapsed,
                "gates": gates,
                "accepted": True,
                "before": parse_snapshot(raw_cycle["before"]),
                "after": parse_snapshot(raw_cycle["after"]),
            }
        )
    return {
        "rtc_rescue_seconds": rtc_seconds,
        "requested_attempts": attempts,
        "source_binding": binding,
        "boot_token": raw["boot_token"],
        "cycles": cycles,
    }


def role_matches(report: dict[str, object], policy: dict[str, object]) -> bool:
    if (
        report["rtc_rescue_seconds"] != policy["rtc_seconds"]
        or report["requested_attempts"] != policy["attempts"]
    ):
        return False
    for cycle in report["cycles"]:
        for snapshot_name in ("before", "after"):
            snapshot = cycle[snapshot_name]
            if snapshot["ready"] is not True or not all(snapshot["gates"].values()):
                return False
            if snapshot["battery"] != {
                "state": policy["state"],
                "external_power": policy["external_power"],
            }:
                return False
    return True


def evaluate(reports: list[dict[str, object]]) -> dict[str, object]:
    if len(reports) != 4:
        raise SeriesError("exactly four deep-cycle reports are required")
    binding = reports[0]["source_binding"]
    binding_consistent = all(report["source_binding"] == binding for report in reports)
    candidates: dict[str, list[int]] = {
        role: [index for index, report in enumerate(reports) if role_matches(report, policy)]
        for role, policy in ROLE_POLICY.items()
    }
    unique_assignment = all(len(indices) == 1 for indices in candidates.values()) and len(
        {indices[0] for indices in candidates.values() if len(indices) == 1}
    ) == len(ROLE_POLICY)
    roles = [
        {
            "role": role,
            "required_attempts": policy["attempts"],
            "rtc_seconds": policy["rtc_seconds"],
            "matched": len(candidates[role]) == 1,
        }
        for role, policy in ROLE_POLICY.items()
    ]
    automated_matrix_pass = bool(
        binding_consistent and unique_assignment and all(item["matched"] for item in roles)
    )
    return {
        "schema": OUTPUT_SCHEMA,
        "mode": "offline-deep-report-series",
        "privacy": {
            "boot_tokens_emitted": False,
            "paths_emitted": False,
            "timestamps_emitted": False,
            "raw_battery_values_emitted": False,
            "device_identifiers_emitted": False,
        },
        "source_binding": binding if binding_consistent else None,
        "source_binding_consistent": binding_consistent,
        "roles": roles,
        "required_report_count": 4,
        "required_cycle_count": 28,
        "observed_cycle_count": sum(report["requested_attempts"] for report in reports),
        "automated_matrix_pass": automated_matrix_pass,
        "physical_post_resume": {
            "speaker_audible": "NOT_EVALUATED",
            "touch": "NOT_EVALUATED",
            "gamepad": "NOT_EVALUATED",
            "wifi_traffic": "NOT_EVALUATED",
        },
        "long_standby_over_5m": "NOT_EVALUATED",
        "pds001_complete": False,
        "result": "AUTOMATED_PASS_PHYSICAL_PENDING" if automated_matrix_pass else "INCOMPLETE",
    }


def write_report(report: dict[str, object], destination: str) -> None:
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
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", nargs=4, type=Path, required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        resolved = [path.resolve(strict=True) for path in arguments.reports]
        if len(set(resolved)) != 4:
            raise SeriesError("deep-cycle report paths must be distinct")
        reports = [parse_report(strict_json(read_private(path))) for path in resolved]
        report = evaluate(reports)
        write_report(report, arguments.output)
    except (OSError, SeriesError, UnicodeError) as exc:
        print(f"deep-series evaluation failed: {exc}", file=sys.stderr)
        return 2
    return 0 if report["automated_matrix_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

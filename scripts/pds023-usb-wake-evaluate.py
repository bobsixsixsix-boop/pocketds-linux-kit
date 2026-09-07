#!/usr/bin/env python3
"""Evaluate a private, supervised PDS-023 USB wake acceptance ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Sequence


EVIDENCE_SCHEMA = "pocketds.usb-wake-evidence.v1"
REPORT_SCHEMA = "pocketds.usb-wake-evaluation.v1"
MAX_BYTES = 262_144
MAX_COUNT = 10_000
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
BINDING_FIELDS = {"repo_revision", "helper_sha256", "udev_rule_sha256"}
COLD_FIELDS = {
    "attempts",
    "passes",
    "failures",
    "cancellations",
    "unique_boot_count",
    "driver_bound_count",
    "wake_disabled_before_count",
    "wake_disabled_after_count",
    "observer_confirmed",
}
DEEP_FIELDS = {
    "attempts",
    "passes",
    "failures",
    "cancellations",
    "rtc_resume_count",
    "wake_disabled_before_count",
    "wake_disabled_after_count",
    "renesas_ebusy_count",
    "xhci_error_count",
    "observer_confirmed",
}
PHYSICAL_POLICY = {
    "internal_keyboard": 50,
    "internal_gamepad": 50,
    "upper_touch": 20,
    "lower_touch": 20,
    "usb_c_usb2": 5,
    "usb_c_usb3": 5,
}
PHYSICAL_FIELDS = {
    *(f"{name}_actions" for name in PHYSICAL_POLICY),
    *(f"{name}_failures" for name in PHYSICAL_POLICY),
    "observer_confirmed",
}


class UsbWakeError(RuntimeError):
    """USB wake evidence is malformed, unsafe, or ambiguous."""


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short USB wake report write")
        view = view[written:]


def _strict_json(content: bytes) -> object:
    def reject_constant(value: str) -> object:
        raise UsbWakeError(f"USB wake evidence contains non-finite JSON: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise UsbWakeError("USB wake evidence contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        return json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise UsbWakeError("USB wake evidence is not UTF-8") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        raise UsbWakeError("USB wake evidence is invalid JSON") from exc


def _read_private(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise UsbWakeError("USB wake evidence is unavailable or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 0 < metadata.st_size <= MAX_BYTES
        ):
            raise UsbWakeError("USB wake evidence identity is unsafe")
        remaining = metadata.st_size
        content = bytearray()
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise UsbWakeError("USB wake evidence changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise UsbWakeError("USB wake evidence grew while reading")
        return bytes(content)
    finally:
        os.close(descriptor)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COUNT:
        raise UsbWakeError(f"invalid {name}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise UsbWakeError(f"invalid {name}")
    return value


def _digest(value: object, name: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise UsbWakeError(f"invalid {name}")
    return value


def _outcome_section(raw: object, fields: set[str], name: str) -> dict[str, object]:
    if not isinstance(raw, dict) or set(raw) != fields:
        raise UsbWakeError(f"USB wake {name} fields are invalid")
    result: dict[str, object] = {}
    for field in fields - {"observer_confirmed"}:
        result[field] = _integer(raw[field], f"{name}/{field}")
    result["observer_confirmed"] = _boolean(
        raw["observer_confirmed"], f"{name}/observer_confirmed"
    )
    attempts = result["attempts"]
    if result["passes"] + result["failures"] + result["cancellations"] != attempts:
        raise UsbWakeError(f"USB wake {name} outcomes do not add up")
    for field, value in result.items():
        if field not in {
            "attempts",
            "passes",
            "failures",
            "cancellations",
            "renesas_ebusy_count",
            "xhci_error_count",
            "observer_confirmed",
        } and value > attempts:
            raise UsbWakeError(f"USB wake {name}/{field} exceeds attempts")
    return result


def parse_evidence(
    raw: object,
) -> tuple[dict[str, str], dict[str, object], dict[str, object], dict[str, object]]:
    if not isinstance(raw, dict) or set(raw) != {
        "schema",
        "binding",
        "cold_boot",
        "deep_resume",
        "physical_io",
    }:
        raise UsbWakeError("USB wake evidence root fields are invalid")
    if raw["schema"] != EVIDENCE_SCHEMA:
        raise UsbWakeError("USB wake evidence schema is invalid")
    binding_raw = raw["binding"]
    if not isinstance(binding_raw, dict) or set(binding_raw) != BINDING_FIELDS:
        raise UsbWakeError("USB wake binding fields are invalid")
    binding = {
        "repo_revision": _digest(binding_raw["repo_revision"], "repo revision", HEX40),
        "helper_sha256": _digest(binding_raw["helper_sha256"], "helper hash", HEX64),
        "udev_rule_sha256": _digest(binding_raw["udev_rule_sha256"], "udev rule hash", HEX64),
    }
    cold = _outcome_section(raw["cold_boot"], COLD_FIELDS, "cold_boot")
    deep = _outcome_section(raw["deep_resume"], DEEP_FIELDS, "deep_resume")
    physical_raw = raw["physical_io"]
    if not isinstance(physical_raw, dict) or set(physical_raw) != PHYSICAL_FIELDS:
        raise UsbWakeError("USB wake physical_io fields are invalid")
    physical: dict[str, object] = {
        field: _integer(physical_raw[field], f"physical_io/{field}")
        for field in PHYSICAL_FIELDS - {"observer_confirmed"}
    }
    physical["observer_confirmed"] = _boolean(
        physical_raw["observer_confirmed"], "physical_io/observer_confirmed"
    )
    for name in PHYSICAL_POLICY:
        if physical[f"{name}_failures"] > physical[f"{name}_actions"]:
            raise UsbWakeError(f"USB wake physical_io/{name} failures exceed actions")
    return binding, cold, deep, physical


def _read_source(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise UsbWakeError("USB wake source binding is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= 2 * 1024 * 1024
        ):
            raise UsbWakeError("USB wake source binding is unsafe")
        content = os.read(descriptor, metadata.st_size + 1)
        if len(content) != metadata.st_size:
            raise UsbWakeError("USB wake source binding changed while reading")
        return content
    finally:
        os.close(descriptor)


def expected_binding(repo_revision: str, system_dir: Path) -> dict[str, str]:
    revision = _digest(repo_revision, "expected repo revision", HEX40)
    helper = _read_source(system_dir / "pocketds-usb-wake-policy")
    rule = _read_source(system_dir / "71-pocketds-usb-wakeup.rules")
    return {
        "repo_revision": revision,
        "helper_sha256": hashlib.sha256(helper).hexdigest(),
        "udev_rule_sha256": hashlib.sha256(rule).hexdigest(),
    }


def empty_template(binding: dict[str, str]) -> dict[str, object]:
    def blank(fields: set[str]) -> dict[str, object]:
        record: dict[str, object] = {field: 0 for field in fields}
        record["observer_confirmed"] = False
        return record

    return {
        "schema": EVIDENCE_SCHEMA,
        "binding": binding,
        "cold_boot": blank(COLD_FIELDS),
        "deep_resume": blank(DEEP_FIELDS),
        "physical_io": blank(PHYSICAL_FIELDS),
    }


def evaluate(
    binding: dict[str, str],
    cold: dict[str, object],
    deep: dict[str, object],
    physical: dict[str, object],
    expected: dict[str, str],
) -> dict[str, object]:
    cold_attempts = int(cold["attempts"])
    deep_attempts = int(deep["attempts"])
    cold_gates = {
        "attempt_floor": cold_attempts >= 5,
        "all_attempts_passed": cold["passes"] == cold_attempts
        and cold["failures"] == 0
        and cold["cancellations"] == 0,
        "five_distinct_boots": cold["unique_boot_count"] >= 5,
        "driver_bound_every_attempt": cold["driver_bound_count"] == cold_attempts,
        "wake_disabled_before_every_attempt": cold["wake_disabled_before_count"]
        == cold_attempts,
        "wake_disabled_after_every_attempt": cold["wake_disabled_after_count"]
        == cold_attempts,
        "observer_confirmed": cold["observer_confirmed"] is True,
    }
    deep_gates = {
        "attempt_floor": deep_attempts >= 30,
        "all_attempts_passed": deep["passes"] == deep_attempts
        and deep["failures"] == 0
        and deep["cancellations"] == 0,
        "rtc_resumed_every_attempt": deep["rtc_resume_count"] == deep_attempts,
        "wake_disabled_before_every_attempt": deep["wake_disabled_before_count"]
        == deep_attempts,
        "wake_disabled_after_every_attempt": deep["wake_disabled_after_count"]
        == deep_attempts,
        "no_renesas_ebusy": deep["renesas_ebusy_count"] == 0,
        "no_xhci_errors": deep["xhci_error_count"] == 0,
        "observer_confirmed": deep["observer_confirmed"] is True,
    }
    physical_gates: dict[str, bool] = {}
    for name, minimum in PHYSICAL_POLICY.items():
        physical_gates[f"{name}_action_floor"] = physical[f"{name}_actions"] >= minimum
        physical_gates[f"{name}_zero_failures"] = physical[f"{name}_failures"] == 0
    physical_gates["observer_confirmed"] = physical["observer_confirmed"] is True
    binding_matches = binding == expected
    complete = bool(
        binding_matches
        and all(cold_gates.values())
        and all(deep_gates.values())
        and all(physical_gates.values())
    )
    return {
        "schema": REPORT_SCHEMA,
        "mode": "offline-supervised-usb-wake-evaluation",
        "privacy": {
            "paths_emitted": False,
            "timestamps_emitted": False,
            "usb_serials_emitted": False,
            "raw_logs_emitted": False,
            "notes_emitted": False,
        },
        "binding": binding,
        "binding_matches_current_source": binding_matches,
        "cold_boot": {"attempts": cold_attempts, "gates": cold_gates, "pass": all(cold_gates.values())},
        "deep_resume": {"attempts": deep_attempts, "gates": deep_gates, "pass": all(deep_gates.values())},
        "physical_io": {"gates": physical_gates, "pass": all(physical_gates.values())},
        "complete": complete,
        "result": "PASS" if complete else "INCOMPLETE",
    }


def write_new(payload: dict[str, object], destination: str) -> None:
    content = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if destination == "-":
        sys.stdout.buffer.write(content)
        return
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
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--evidence", type=Path)
    source.add_argument("--template-output")
    parser.add_argument("--repo-revision", required=True)
    parser.add_argument("--output", default="-")
    arguments = parser.parse_args(argv)
    if arguments.template_output is not None and arguments.output != "-":
        parser.error("--output is valid only with --evidence")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    system_dir = Path(__file__).resolve().parents[1] / "components/system"
    try:
        current = expected_binding(arguments.repo_revision, system_dir)
        if arguments.template_output is not None:
            write_new(empty_template(current), arguments.template_output)
            print(json.dumps({"schema": EVIDENCE_SCHEMA, "template_created": True}))
            return 0
        assert arguments.evidence is not None
        binding, cold, deep, physical = parse_evidence(
            _strict_json(_read_private(arguments.evidence))
        )
        report = evaluate(binding, cold, deep, physical, current)
        write_new(report, arguments.output)
    except (UsbWakeError, OSError, UnicodeError) as exc:
        print(f"USB wake evaluation failed: {exc}", file=sys.stderr)
        return 2
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

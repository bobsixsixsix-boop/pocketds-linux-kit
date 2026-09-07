#!/usr/bin/env python3
"""Aggregate five private PDS-014 reports without exposing boot identities."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Sequence


SCHEMA = "pocketds.post-boot-series.v1"
INPUT_SCHEMA = "pocketds.post-boot-acceptance.v2"
REQUIRED_REPORTS = 5
MAX_REPORT_BYTES = 2 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_STEPS = (
    "repo-revision-before",
    "repo-status-before",
    "system-services-before",
    "user-services-before",
    "full-tests",
    "hardware-readonly",
    "suspend-preflight",
    "diagnostic-bundle",
    "repo-revision-after",
    "repo-status-after",
    "system-services-after",
    "user-services-after",
)
EXPECTED_GATES = {
    "started_in_post_boot_window",
    "uptime_monotonic",
    "boot_unchanged",
    "repo_revision_unchanged",
    "repo_clean_before_after",
    "all_steps_passed",
    "system_services_same_pid_zero_restarts",
    "user_services_same_pid_zero_restarts",
    "diagnostic_bundle_created",
    "brightness_targets_restored",
    "renesas_xhci_wake_disabled",
}
EXPECTED_PRIVACY = {
    "boot_id_emitted",
    "command_output_emitted",
    "diagnostic_path_emitted",
    "host_or_user_emitted",
}
ALLOWED_STEP_STATUS = {"pass", "fail", "timeout", "overflow", "error", "cancelled"}


class SeriesError(RuntimeError):
    """A private boot report was unsafe, malformed or internally inconsistent."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SeriesError("post-boot report has duplicate keys")
        result[key] = value
    return result


def strict_json(content: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                SeriesError("post-boot report has a non-finite number")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SeriesError("post-boot report is invalid JSON") from exc
    if type(value) is not dict:
        raise SeriesError("post-boot report is not an object")
    return value


def read_private_report(path: Path) -> tuple[dict[str, Any], tuple[int, int]]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SeriesError("private post-boot report is unavailable or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 0 < metadata.st_size <= MAX_REPORT_BYTES
        ):
            raise SeriesError("private post-boot report identity is unsafe")
        content = bytearray()
        remaining = metadata.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise SeriesError("private post-boot report changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise SeriesError("private post-boot report grew while reading")
        return strict_json(bytes(content)), (metadata.st_dev, metadata.st_ino)
    finally:
        os.close(descriptor)


def _bounded_number(value: object, *, minimum: float, maximum: float) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(float(value))
        and minimum <= float(value) <= maximum
    )


def validate_report(report: dict[str, Any]) -> dict[str, Any]:
    expected_fields = {
        "schema",
        "mode",
        "privacy",
        "boot_token",
        "repo_revision",
        "start_uptime_s",
        "end_uptime_s",
        "max_start_uptime_s",
        "steps",
        "gates",
        "complete",
        "result",
    }
    if set(report) != expected_fields:
        raise SeriesError("post-boot report fields are unsupported")
    if report["schema"] != INPUT_SCHEMA or report["mode"] != "deliberate-post-boot-readonly-matrix":
        raise SeriesError("post-boot report schema or mode is unsupported")
    privacy = report["privacy"]
    if (
        type(privacy) is not dict
        or set(privacy) != EXPECTED_PRIVACY
        or any(value is not False for value in privacy.values())
    ):
        raise SeriesError("post-boot report privacy contract is invalid")
    token = report["boot_token"]
    if type(token) is not str or SHA256_RE.fullmatch(token) is None:
        raise SeriesError("post-boot report token is invalid")
    revision = report["repo_revision"]
    if revision is not None and (type(revision) is not str or REVISION_RE.fullmatch(revision) is None):
        raise SeriesError("post-boot report revision is invalid")
    if (
        not _bounded_number(report["start_uptime_s"], minimum=0, maximum=7_200)
        or not _bounded_number(report["end_uptime_s"], minimum=0, maximum=14_400)
        or type(report["max_start_uptime_s"]) is not int
        or not 300 <= report["max_start_uptime_s"] <= 1_800
    ):
        raise SeriesError("post-boot report uptime fields are invalid")

    steps = report["steps"]
    if type(steps) is not list or len(steps) > len(EXPECTED_STEPS):
        raise SeriesError("post-boot report step list is invalid")
    names: list[str] = []
    all_steps_pass = len(steps) == len(EXPECTED_STEPS)
    for step in steps:
        if type(step) is not dict or set(step) != {"name", "status", "returncode", "seconds"}:
            raise SeriesError("post-boot report step fields are invalid")
        if type(step["name"]) is not str or type(step["status"]) is not str:
            raise SeriesError("post-boot report step identity is invalid")
        if step["status"] not in ALLOWED_STEP_STATUS:
            raise SeriesError("post-boot report step status is invalid")
        if type(step["returncode"]) is not int or not -255 <= step["returncode"] <= 255:
            raise SeriesError("post-boot report return code is invalid")
        if not _bounded_number(step["seconds"], minimum=0, maximum=300):
            raise SeriesError("post-boot report step duration is invalid")
        names.append(step["name"])
        all_steps_pass = all_steps_pass and step["status"] == "pass" and step["returncode"] == 0
    if tuple(names) != EXPECTED_STEPS[: len(names)]:
        raise SeriesError("post-boot report step order is invalid")

    gates = report["gates"]
    if (
        type(gates) is not dict
        or set(gates) != EXPECTED_GATES
        or any(type(value) is not bool for value in gates.values())
    ):
        raise SeriesError("post-boot report gate fields are invalid")
    complete = report["complete"]
    if type(complete) is not bool or report["result"] not in {"PASS", "FAIL"}:
        raise SeriesError("post-boot report result fields are invalid")
    expected_complete = all(gates.values()) and all_steps_pass
    if complete != expected_complete or report["result"] != ("PASS" if complete else "FAIL"):
        raise SeriesError("post-boot report result is internally inconsistent")
    if bool(gates["repo_revision_unchanged"]) != (revision is not None):
        raise SeriesError("post-boot report revision gate is inconsistent")
    return report


def evaluate_series(reports: list[dict[str, Any]]) -> dict[str, object]:
    validated = [validate_report(report) for report in reports]
    tokens = {str(report["boot_token"]) for report in validated}
    revisions = {
        str(report["repo_revision"])
        for report in validated
        if report["repo_revision"] is not None
    }
    all_reports_pass = len(validated) == REQUIRED_REPORTS and all(
        report["complete"] is True for report in validated
    )
    distinct_boots = len(tokens) == REQUIRED_REPORTS
    same_revision = len(revisions) == 1 and all(
        report["repo_revision"] is not None for report in validated
    )
    accepted = all_reports_pass and distinct_boots and same_revision
    return {
        "schema": SCHEMA,
        "mode": "five-distinct-cold-boots",
        "privacy": {
            "input_paths_emitted": False,
            "boot_tokens_emitted": False,
            "raw_boot_ids_emitted": False,
            "timestamps_emitted": False,
        },
        "required_report_count": REQUIRED_REPORTS,
        "report_count": len(validated),
        "passed_report_count": sum(report["complete"] is True for report in validated),
        "distinct_boot_count": len(tokens),
        "same_repo_revision": same_revision,
        "all_reports_passed": all_reports_pass,
        "accepted": accepted,
        "result": "PASS" if accepted else "INCOMPLETE",
    }


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short boot series report write")
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
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, nargs=REQUIRED_REPORTS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        reports: list[dict[str, Any]] = []
        identities: set[tuple[int, int]] = set()
        for path in arguments.reports:
            report, identity = read_private_report(path)
            if identity in identities:
                raise SeriesError("post-boot report input is repeated")
            identities.add(identity)
            reports.append(report)
        result = evaluate_series(reports)
        write_report(result, arguments.output)
    except (SeriesError, OSError, UnicodeError) as exc:
        print(f"post-boot series evaluation failed: {exc}", file=sys.stderr)
        return 2
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

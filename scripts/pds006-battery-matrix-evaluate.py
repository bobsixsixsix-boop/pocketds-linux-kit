#!/usr/bin/env python3
"""Evaluate a private, supervised Pocket DS battery acceptance ledger."""

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


EVIDENCE_SCHEMA = "pocketds.battery-matrix-evidence.v1"
REPORT_SCHEMA = "pocketds.battery-matrix-evaluation.v1"
MAX_EVIDENCE_BYTES = 262_144
MAX_TRANSITION_ATTEMPTS = 100
MAX_SOAK_SAMPLES = 1_000_000
MAX_SOAK_SECONDS = 7 * 86_400
SOAK_MINIMUM_SECONDS = 86_400
SOAK_MINIMUM_SAMPLES = 2_880
SOAK_MAXIMUM_GAP_SECONDS = 60
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

BINDING_FIELDS = {
    "repo_revision",
    "panelctl_sha256",
    "panel_qml_sha256",
}
TRANSITION_COMMON_FIELDS = {
    "attempts",
    "passes",
    "failures",
    "cancellations",
    "panelctl_read_failures",
    "telemetry_complete_count",
    "plasma_restart_delta",
    "observer_confirmed",
}
CASE_POLICY: dict[str, tuple[int, set[str]]] = {
    "unplug-discharging": (
        5,
        {"external_power_false_count", "discharging_state_count"},
    ),
    "plug-charging": (
        5,
        {"external_power_true_count", "charging_state_count"},
    ),
    "full-on-external": (
        2,
        {
            "external_power_true_count",
            "full_state_count",
            "percent_100_count",
        },
    ),
}
SOAK_FIELDS = {
    "duration_seconds",
    "sample_count",
    "panelctl_read_failures",
    "invalid_sample_count",
    "max_gap_seconds",
    "plasma_restart_delta",
    "observer_confirmed",
}


class MatrixError(RuntimeError):
    """Malformed, unsafe or ambiguous battery evidence."""


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short battery report write")
        view = view[written:]


def _validate_private(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or metadata.st_nlink != 1
        or not 0 < metadata.st_size <= MAX_EVIDENCE_BYTES
    ):
        raise MatrixError("battery evidence is public, unsafe, empty or oversized")


def read_private(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MatrixError("battery evidence is unavailable or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        _validate_private(metadata)
        remaining = metadata.st_size
        content = bytearray()
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise MatrixError("battery evidence changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise MatrixError("battery evidence grew while reading")
        return bytes(content)
    finally:
        os.close(descriptor)


def strict_json(content: bytes) -> object:
    def reject_constant(value: str) -> object:
        raise MatrixError(f"battery evidence contains non-finite JSON: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise MatrixError("battery evidence contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        text = content.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise MatrixError("battery evidence is not UTF-8") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        raise MatrixError("battery evidence is invalid JSON") from exc


def _integer(value: object, name: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > maximum
    ):
        raise MatrixError(f"invalid {name}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise MatrixError(f"invalid {name}")
    return value


def _revision(value: object, name: str) -> str:
    if not isinstance(value, str) or REVISION_RE.fullmatch(value) is None:
        raise MatrixError(f"invalid {name}")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        raise MatrixError(f"invalid {name}")
    return value


def parse_evidence(
    raw: object,
) -> tuple[dict[str, str], dict[str, dict[str, object]], dict[str, object] | None]:
    if not isinstance(raw, dict) or set(raw) != {
        "schema",
        "binding",
        "transitions",
        "telemetry-soak",
    }:
        raise MatrixError("battery evidence root fields are invalid")
    if raw.get("schema") != EVIDENCE_SCHEMA:
        raise MatrixError("battery evidence schema is invalid")
    raw_binding = raw.get("binding")
    raw_transitions = raw.get("transitions")
    raw_soak = raw.get("telemetry-soak")
    if (
        not isinstance(raw_binding, dict)
        or set(raw_binding) != BINDING_FIELDS
        or not isinstance(raw_transitions, dict)
        or (raw_soak is not None and not isinstance(raw_soak, dict))
    ):
        raise MatrixError("battery evidence structure is invalid")
    binding = {
        "repo_revision": _revision(raw_binding["repo_revision"], "repo revision"),
        "panelctl_sha256": _digest(raw_binding["panelctl_sha256"], "panelctl hash"),
        "panel_qml_sha256": _digest(raw_binding["panel_qml_sha256"], "Panel QML hash"),
    }
    if not set(raw_transitions).issubset(CASE_POLICY):
        raise MatrixError("battery evidence contains an unknown transition")
    transitions: dict[str, dict[str, object]] = {}
    for case_id, record in raw_transitions.items():
        _minimum, extras = CASE_POLICY[case_id]
        expected = TRANSITION_COMMON_FIELDS | extras
        if not isinstance(record, dict) or set(record) != expected:
            raise MatrixError(f"battery transition {case_id} fields are invalid")
        parsed: dict[str, object] = {}
        for field in expected - {"observer_confirmed"}:
            parsed[field] = _integer(
                record[field], f"{case_id}/{field}", MAX_TRANSITION_ATTEMPTS
            )
        parsed["observer_confirmed"] = _boolean(
            record["observer_confirmed"], f"{case_id}/observer_confirmed"
        )
        attempts = parsed["attempts"]
        if parsed["passes"] + parsed["failures"] + parsed["cancellations"] != attempts:
            raise MatrixError(f"battery transition {case_id} outcomes do not add up")
        for field in (
            expected
            - {
                "attempts",
                "passes",
                "failures",
                "cancellations",
                "observer_confirmed",
            }
        ):
            if parsed[field] > attempts:
                raise MatrixError(f"battery transition {case_id}/{field} exceeds attempts")
        transitions[case_id] = parsed

    soak: dict[str, object] | None = None
    if raw_soak is not None:
        if set(raw_soak) != SOAK_FIELDS:
            raise MatrixError("battery soak fields are invalid")
        soak = {
            "duration_seconds": _integer(
                raw_soak["duration_seconds"], "soak/duration_seconds", MAX_SOAK_SECONDS
            ),
            "sample_count": _integer(
                raw_soak["sample_count"], "soak/sample_count", MAX_SOAK_SAMPLES
            ),
            "panelctl_read_failures": _integer(
                raw_soak["panelctl_read_failures"],
                "soak/panelctl_read_failures",
                MAX_SOAK_SAMPLES,
            ),
            "invalid_sample_count": _integer(
                raw_soak["invalid_sample_count"],
                "soak/invalid_sample_count",
                MAX_SOAK_SAMPLES,
            ),
            "max_gap_seconds": _integer(
                raw_soak["max_gap_seconds"],
                "soak/max_gap_seconds",
                MAX_SOAK_SECONDS,
            ),
            "plasma_restart_delta": _integer(
                raw_soak["plasma_restart_delta"],
                "soak/plasma_restart_delta",
                MAX_TRANSITION_ATTEMPTS,
            ),
            "observer_confirmed": _boolean(
                raw_soak["observer_confirmed"], "soak/observer_confirmed"
            ),
        }
        if soak["panelctl_read_failures"] + soak["invalid_sample_count"] > soak["sample_count"]:
            raise MatrixError("battery soak failure counts exceed samples")
    return binding, transitions, soak


def _read_source(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MatrixError("battery source binding is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= 2 * 1024 * 1024
        ):
            raise MatrixError("battery source binding is unsafe or oversized")
        remaining = metadata.st_size
        content = bytearray()
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise MatrixError("battery source binding changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise MatrixError("battery source binding grew while reading")
        return bytes(content)
    finally:
        os.close(descriptor)


def expected_binding(repo_revision: str, control_panel_dir: Path) -> dict[str, str]:
    revision = _revision(repo_revision, "expected repo revision")
    panelctl = _read_source(control_panel_dir / "pocketds-panelctl.cpp")
    panel_qml = _read_source(control_panel_dir / "plasmoid/contents/ui/main.qml")
    return {
        "repo_revision": revision,
        "panelctl_sha256": hashlib.sha256(panelctl).hexdigest(),
        "panel_qml_sha256": hashlib.sha256(panel_qml).hexdigest(),
    }


def empty_template(binding: dict[str, str]) -> dict[str, object]:
    transitions: dict[str, dict[str, object]] = {}
    for case_id, (_minimum, extras) in CASE_POLICY.items():
        record: dict[str, object] = {
            field: 0 for field in TRANSITION_COMMON_FIELDS | extras
        }
        record["observer_confirmed"] = False
        transitions[case_id] = record
    return {
        "schema": EVIDENCE_SCHEMA,
        "binding": binding,
        "transitions": transitions,
        "telemetry-soak": {
            "duration_seconds": 0,
            "sample_count": 0,
            "panelctl_read_failures": 0,
            "invalid_sample_count": 0,
            "max_gap_seconds": 0,
            "plasma_restart_delta": 0,
            "observer_confirmed": False,
        },
    }


def evaluate(
    binding: dict[str, str],
    transitions: dict[str, dict[str, object]],
    soak: dict[str, object] | None,
    expected: dict[str, str],
) -> dict[str, object]:
    results: list[dict[str, object]] = []
    for case_id, (minimum_attempts, extras) in CASE_POLICY.items():
        record = transitions.get(case_id)
        if record is None:
            results.append(
                {
                    "case_id": case_id,
                    "present": False,
                    "attempts": 0,
                    "minimum_attempts": minimum_attempts,
                    "gates": {},
                    "pass": False,
                }
            )
            continue
        attempts = record["attempts"]
        gates = {
            "attempt_floor": attempts >= minimum_attempts,
            "all_attempts_passed": (
                record["passes"] == attempts
                and record["failures"] == 0
                and record["cancellations"] == 0
            ),
            "panelctl_reads_clean": record["panelctl_read_failures"] == 0,
            "telemetry_complete_every_attempt": record["telemetry_complete_count"] == attempts,
            "plasma_not_restarted": record["plasma_restart_delta"] == 0,
            "observer_confirmed": record["observer_confirmed"] is True,
        }
        for field in extras:
            gates[f"{field}_every_attempt"] = record[field] == attempts
        results.append(
            {
                "case_id": case_id,
                "present": True,
                "attempts": attempts,
                "minimum_attempts": minimum_attempts,
                "gates": gates,
                "pass": all(gates.values()),
            }
        )

    soak_gates = {
        "present": soak is not None,
        "duration_at_least_24h": bool(
            soak is not None and soak["duration_seconds"] >= SOAK_MINIMUM_SECONDS
        ),
        "sample_floor": bool(
            soak is not None and soak["sample_count"] >= SOAK_MINIMUM_SAMPLES
        ),
        "panelctl_reads_clean": bool(
            soak is not None and soak["panelctl_read_failures"] == 0
        ),
        "samples_valid": bool(
            soak is not None and soak["invalid_sample_count"] == 0
        ),
        "gap_within_limit": bool(
            soak is not None
            and 1 <= soak["max_gap_seconds"] <= SOAK_MAXIMUM_GAP_SECONDS
        ),
        "duration_sample_coverage_consistent": bool(
            soak is not None
            and soak["sample_count"] > 1
            and (soak["sample_count"] - 1) * soak["max_gap_seconds"]
            >= soak["duration_seconds"]
        ),
        "plasma_not_restarted": bool(
            soak is not None and soak["plasma_restart_delta"] == 0
        ),
        "observer_confirmed": bool(
            soak is not None and soak["observer_confirmed"] is True
        ),
    }
    binding_matches = binding == expected
    complete = bool(
        binding_matches
        and all(item["pass"] for item in results)
        and all(soak_gates.values())
    )
    return {
        "schema": REPORT_SCHEMA,
        "mode": "offline-supervised-ledger-evaluation",
        "privacy": {
            "timestamps_emitted": False,
            "paths_emitted": False,
            "device_identifiers_emitted": False,
            "raw_telemetry_emitted": False,
            "notes_emitted": False,
        },
        "binding": binding,
        "binding_matches_current_source": binding_matches,
        "transition_cases": results,
        "passed_transition_count": sum(bool(item["pass"]) for item in results),
        "required_transition_count": len(results),
        "telemetry_soak": {
            "minimum_duration_seconds": SOAK_MINIMUM_SECONDS,
            "minimum_samples": SOAK_MINIMUM_SAMPLES,
            "maximum_gap_seconds": SOAK_MAXIMUM_GAP_SECONDS,
            "observed_duration_seconds": soak["duration_seconds"] if soak else 0,
            "observed_sample_count": soak["sample_count"] if soak else 0,
            "gates": soak_gates,
            "pass": all(soak_gates.values()),
        },
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
    control_panel_dir = Path(__file__).resolve().parents[1] / "components/control-panel"
    try:
        binding = expected_binding(arguments.repo_revision, control_panel_dir)
        if arguments.template_output is not None:
            write_new(empty_template(binding), arguments.template_output)
            print(json.dumps({"schema": EVIDENCE_SCHEMA, "template_created": True}))
            return 0
        assert arguments.evidence is not None
        evidence_binding, transitions, soak = parse_evidence(
            strict_json(read_private(arguments.evidence))
        )
        report = evaluate(evidence_binding, transitions, soak, binding)
        write_new(report, arguments.output)
    except (MatrixError, OSError, UnicodeError) as exc:
        print(f"battery matrix evaluation failed: {exc}", file=sys.stderr)
        return 2
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

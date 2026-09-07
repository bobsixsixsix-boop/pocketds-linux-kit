#!/usr/bin/env python3
"""Evaluate a private, supervised Pocket DS audio acceptance ledger."""

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


EVIDENCE_SCHEMA = "pocketds.audio-matrix-evidence.v1"
REPORT_SCHEMA = "pocketds.audio-matrix-evaluation.v1"
MAX_EVIDENCE_BYTES = 262_144
MAX_ATTEMPTS = 100

COMMON_FIELDS = {
    "attempts",
    "passes",
    "failures",
    "cancellations",
    "pipewire_restart_delta",
    "wireplumber_restart_delta",
    "auto_null_observed",
    "xrun_count",
    "route_mismatch_count",
    "observer_confirmed",
}
BINDING_FIELDS = {"repo_revision", "hifi_sha256", "card_sha256"}
CASE_POLICY: dict[str, tuple[int, set[str]]] = {
    "speaker-left": (1, set()),
    "speaker-right": (1, set()),
    "speaker-stereo": (1, set()),
    "physical-microphone": (
        3,
        {
            "consent_confirmed",
            "monitor_source_used",
            "capture_cleanup_failures",
            "clipping_count",
        },
    ),
    "displayport-hotplug": (
        20,
        {"dp_sink_present_during_test", "speaker_recovered_after_disconnect"},
    ),
    "cold-boot": (5, {"speaker_recovered"}),
    "deep-resume": (10, {"speaker_recovered"}),
}


class MatrixError(RuntimeError):
    """Malformed, unsafe or ambiguous audio evidence."""


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short audio report write")
        view = view[written:]


def _validate_private(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or metadata.st_nlink != 1
        or not 0 < metadata.st_size <= MAX_EVIDENCE_BYTES
    ):
        raise MatrixError("audio evidence is public, unsafe, empty or oversized")


def read_private(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MatrixError("audio evidence is unavailable or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        _validate_private(metadata)
        remaining = metadata.st_size
        content = bytearray()
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise MatrixError("audio evidence changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise MatrixError("audio evidence grew while reading")
        return bytes(content)
    finally:
        os.close(descriptor)


def strict_json(content: bytes) -> object:
    def reject_constant(value: str) -> object:
        raise MatrixError(f"audio evidence contains non-finite JSON: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise MatrixError("audio evidence contains duplicate JSON keys")
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
        raise MatrixError("audio evidence is not UTF-8") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        raise MatrixError("audio evidence is invalid JSON") from exc


def _integer(value: object, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > MAX_ATTEMPTS
    ):
        raise MatrixError(f"invalid {name}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise MatrixError(f"invalid {name}")
    return value


def _digest(value: object, name: str, *, length: int) -> str:
    if not isinstance(value, str) or re.fullmatch(f"[0-9a-f]{{{length}}}", value) is None:
        raise MatrixError(f"invalid {name}")
    return value


def parse_evidence(
    raw: object,
) -> tuple[dict[str, str], dict[str, dict[str, object]]]:
    if not isinstance(raw, dict) or set(raw) != {"schema", "binding", "cases"}:
        raise MatrixError("audio evidence root fields are invalid")
    if (
        raw.get("schema") != EVIDENCE_SCHEMA
        or not isinstance(raw.get("binding"), dict)
        or not isinstance(raw.get("cases"), dict)
    ):
        raise MatrixError("audio evidence schema is invalid")
    raw_binding = raw["binding"]
    if set(raw_binding) != BINDING_FIELDS:
        raise MatrixError("audio evidence binding fields are invalid")
    binding = {
        "repo_revision": _digest(raw_binding["repo_revision"], "repo revision", length=40),
        "hifi_sha256": _digest(raw_binding["hifi_sha256"], "HiFi hash", length=64),
        "card_sha256": _digest(raw_binding["card_sha256"], "card hash", length=64),
    }
    raw_cases = raw["cases"]
    if not set(raw_cases).issubset(CASE_POLICY):
        raise MatrixError("audio evidence contains an unknown case")
    cases: dict[str, dict[str, object]] = {}
    for case_id, record in raw_cases.items():
        _, extras = CASE_POLICY[case_id]
        expected = COMMON_FIELDS | extras
        if not isinstance(record, dict) or set(record) != expected:
            raise MatrixError(f"audio case {case_id} fields are invalid")
        parsed: dict[str, object] = {}
        for field in (
            "attempts",
            "passes",
            "failures",
            "cancellations",
            "pipewire_restart_delta",
            "wireplumber_restart_delta",
            "xrun_count",
            "route_mismatch_count",
        ):
            parsed[field] = _integer(record[field], f"{case_id}/{field}")
        for field in ("auto_null_observed", "observer_confirmed"):
            parsed[field] = _boolean(record[field], f"{case_id}/{field}")
        for field in extras:
            if field in {
                "capture_cleanup_failures",
                "clipping_count",
            }:
                parsed[field] = _integer(record[field], f"{case_id}/{field}")
            else:
                parsed[field] = _boolean(record[field], f"{case_id}/{field}")
        attempts = parsed["attempts"]
        if parsed["passes"] + parsed["failures"] + parsed["cancellations"] != attempts:
            raise MatrixError(f"audio case {case_id} outcome counts do not add up")
        cases[case_id] = parsed
    return binding, cases


def expected_binding(repo_revision: str, repo_ucm_dir: Path) -> dict[str, str]:
    revision = _digest(repo_revision, "expected repo revision", length=40)
    hashes: dict[str, str] = {}
    for key, filename in (
        ("hifi_sha256", "HiFi.conf"),
        ("card_sha256", "SM8550-APS.conf"),
    ):
        path = repo_ucm_dir / filename
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise MatrixError("repository UCM source is unavailable") from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or not 0 < metadata.st_size <= 1_048_576
            ):
                raise MatrixError("repository UCM source is linked, invalid or oversized")
            remaining = metadata.st_size
            content = bytearray()
            while remaining:
                block = os.read(descriptor, min(65_536, remaining))
                if not block:
                    raise MatrixError("repository UCM source changed while reading")
                content.extend(block)
                remaining -= len(block)
            if os.read(descriptor, 1):
                raise MatrixError("repository UCM source grew while reading")
            hashes[key] = hashlib.sha256(content).hexdigest()
        finally:
            os.close(descriptor)
    return {"repo_revision": revision, **hashes}


def evaluate(
    binding: dict[str, str],
    cases: dict[str, dict[str, object]],
    expected: dict[str, str],
) -> dict[str, object]:
    results: list[dict[str, object]] = []
    for case_id, (minimum_attempts, _extras) in CASE_POLICY.items():
        record = cases.get(case_id)
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
        gates = {
            "attempt_floor": record["attempts"] >= minimum_attempts,
            "all_attempts_passed": (
                record["passes"] == record["attempts"]
                and record["failures"] == 0
                and record["cancellations"] == 0
            ),
            "audio_services_not_restarted": (
                record["pipewire_restart_delta"] == 0
                and record["wireplumber_restart_delta"] == 0
            ),
            "no_auto_null": record["auto_null_observed"] is False,
            "no_xrun": record["xrun_count"] == 0,
            "no_route_mismatch": record["route_mismatch_count"] == 0,
            "observer_confirmed": record["observer_confirmed"] is True,
        }
        if case_id == "physical-microphone":
            gates.update(
                {
                    "explicit_consent": record["consent_confirmed"] is True,
                    "physical_source_not_monitor": record["monitor_source_used"] is False,
                    "temporary_capture_cleaned": record["capture_cleanup_failures"] == 0,
                    "no_clipping": record["clipping_count"] == 0,
                }
            )
        elif case_id == "displayport-hotplug":
            gates.update(
                {
                    "dp_sink_was_present": record["dp_sink_present_during_test"] is True,
                    "speaker_recovered": record["speaker_recovered_after_disconnect"] is True,
                }
            )
        elif case_id in {"cold-boot", "deep-resume"}:
            gates["speaker_recovered"] = record["speaker_recovered"] is True
        results.append(
            {
                "case_id": case_id,
                "present": True,
                "attempts": record["attempts"],
                "minimum_attempts": minimum_attempts,
                "gates": gates,
                "pass": all(gates.values()),
            }
        )
    binding_matches = binding == expected
    complete = binding_matches and all(item["pass"] for item in results)
    return {
        "schema": REPORT_SCHEMA,
        "mode": "offline-supervised-ledger-evaluation",
        "privacy": {
            "audio_content_emitted": False,
            "transcripts_emitted": False,
            "timestamps_emitted": False,
            "paths_emitted": False,
            "device_identifiers_emitted": False,
        },
        "policy": {
            case_id: minimum for case_id, (minimum, _extras) in CASE_POLICY.items()
        },
        "binding": binding,
        "binding_matches_current_source": binding_matches,
        "cases": results,
        "passed_case_count": sum(bool(item["pass"]) for item in results),
        "required_case_count": len(results),
        "complete": complete,
        "result": "PASS" if complete else "INCOMPLETE",
    }


def write_report(report: dict[str, object], destination: str) -> None:
    content = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
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
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--repo-revision", required=True)
    parser.add_argument(
        "--repo-ucm-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "components/audio",
    )
    parser.add_argument("--output", default="-")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        binding, cases = parse_evidence(strict_json(read_private(arguments.evidence)))
        expected = expected_binding(arguments.repo_revision, arguments.repo_ucm_dir)
        report = evaluate(binding, cases, expected)
        write_report(report, arguments.output)
    except (MatrixError, OSError) as exc:
        print(f"audio matrix evaluation failed: {exc}", file=sys.stderr)
        return 2
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

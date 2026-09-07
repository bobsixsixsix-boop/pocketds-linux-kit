#!/usr/bin/env python3
"""Record private noise markers or align them to read-only fan evidence."""

from __future__ import annotations

import argparse
import bisect
import fcntl
import json
import math
import os
from pathlib import Path
import stat
import statistics
import sys
import time
from typing import Sequence


MARKER_SCHEMA = "pocketds.fan-noise-marker.v1"
REPORT_SCHEMA = "pocketds.fan-noise-correlation.v1"
MAX_MARKER_BYTES = 65_536
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
MAX_LINE_BYTES = 262_144
MAX_SAMPLES = 2_400
MAX_MARKERS = 100


class CorrelationError(RuntimeError):
    """An unsafe evidence file, invalid record or unalignable contract."""


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short fan evidence write")
        view = view[written:]


def _validate_private(metadata: os.stat_result, *, maximum: int) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or metadata.st_nlink != 1
        or not 0 <= metadata.st_size <= maximum
    ):
        raise CorrelationError("fan evidence is public, unsafe or oversized")


def append_marker(path: Path, *, unix_ms: int | None = None) -> None:
    timestamp = round(time.time() * 1000) if unix_ms is None else unix_ms
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
        raise CorrelationError("invalid noise marker timestamp")
    record = {
        "schema": MARKER_SCHEMA,
        "type": "noise-spike",
        "unix_ms": timestamp,
    }
    content = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
    flags = (
        os.O_WRONLY
        | os.O_APPEND
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        metadata = os.fstat(descriptor)
        _validate_private(metadata, maximum=MAX_MARKER_BYTES)
        if metadata.st_size + len(content) > MAX_MARKER_BYTES:
            raise CorrelationError("noise marker file would exceed its limit")
        _write_all(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_private_bytes(path: Path, *, maximum: int, allow_empty: bool = False) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CorrelationError("fan evidence is unavailable or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        _validate_private(metadata, maximum=maximum)
        if not allow_empty and metadata.st_size == 0:
            raise CorrelationError("fan evidence is empty")
        remaining = metadata.st_size
        content = bytearray()
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise CorrelationError("fan evidence changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise CorrelationError("fan evidence grew while reading")
        return bytes(content)
    finally:
        os.close(descriptor)


def parse_jsonl(content: bytes, *, maximum_records: int) -> list[object]:
    def reject_constant(value: str) -> object:
        raise CorrelationError(f"fan evidence contains non-finite JSON: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise CorrelationError("fan evidence contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CorrelationError("fan evidence is not UTF-8") from exc
    lines = text.splitlines()
    if not 1 <= len(lines) <= maximum_records:
        raise CorrelationError("fan evidence record count is out of range")
    records: list[object] = []
    for line in lines:
        if not line or len(line.encode("utf-8")) > MAX_LINE_BYTES:
            raise CorrelationError("fan evidence line is empty or oversized")
        try:
            records.append(
                json.loads(
                    line,
                    object_pairs_hook=unique_object,
                    parse_constant=reject_constant,
                )
            )
        except CorrelationError:
            raise
        except json.JSONDecodeError as exc:
            raise CorrelationError("fan evidence line is invalid JSON") from exc
    return records


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CorrelationError(f"invalid {name}")
    return value


def _number_or_none(value: object, name: str) -> int | float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise CorrelationError(f"invalid {name}")
    return value


def _nested(record: dict[str, object], path: tuple[str, ...]) -> object:
    value: object = record
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise CorrelationError("fan sample is missing a required field")
        value = value[key]
    return value


def parse_evidence(records: list[object]) -> tuple[list[dict[str, object]], dict[str, object]]:
    if len(records) < 3:
        raise CorrelationError("fan evidence needs at least two samples and a summary")
    raw_summary = records[-1]
    if (
        not isinstance(raw_summary, dict)
        or raw_summary.get("schema") != 1
        or raw_summary.get("type") != "summary"
        or raw_summary.get("read_only") is not True
    ):
        raise CorrelationError("fan evidence has no valid final summary")
    samples: list[dict[str, object]] = []
    previous_timestamp = 0
    for raw_sample in records[:-1]:
        if (
            not isinstance(raw_sample, dict)
            or raw_sample.get("schema") != 1
            or raw_sample.get("type") != "sample"
            or raw_sample.get("read_only") is not True
        ):
            raise CorrelationError("fan evidence contains a non-sample record")
        timestamp = _integer(raw_sample.get("sample_unix_ms"), "sample timestamp", minimum=1)
        if timestamp <= previous_timestamp:
            raise CorrelationError("fan sample timestamps are not strictly increasing")
        previous_timestamp = timestamp
        if raw_sample.get("collector_integrity") != "PASS":
            raise CorrelationError("fan sample collector integrity failed")
        for path in (
            ("temperature", "controller_hotspot_c"),
            ("fan", "target_pwm", "value"),
            ("fan", "actual_pwm", "value"),
            ("fan", "actual_rpm", "value"),
            ("compute", "cpu_percent"),
            ("compute", "gpu", "gpu_percent"),
        ):
            _number_or_none(_nested(raw_sample, path), "/".join(path))
        decoder = _nested(raw_sample, ("video_decode", "decoder_fd_observed"))
        if not isinstance(decoder, bool):
            raise CorrelationError("invalid decoder observation")
        samples.append(raw_sample)
    if len(samples) > MAX_SAMPLES:
        raise CorrelationError("too many fan samples")
    if raw_summary.get("sample_count") != len(samples):
        raise CorrelationError("fan summary sample count mismatch")
    return samples, raw_summary


def parse_markers(records: list[object]) -> list[int]:
    if len(records) > MAX_MARKERS:
        raise CorrelationError("too many noise markers")
    markers: list[int] = []
    for record in records:
        if (
            not isinstance(record, dict)
            or set(record) != {"schema", "type", "unix_ms"}
            or record.get("schema") != MARKER_SCHEMA
            or record.get("type") != "noise-spike"
        ):
            raise CorrelationError("invalid noise marker record")
        markers.append(_integer(record.get("unix_ms"), "noise marker", minimum=1))
    return sorted(markers)


def _delta(before: dict[str, object], after: dict[str, object], path: tuple[str, ...]):
    first = _number_or_none(_nested(before, path), "/".join(path))
    last = _number_or_none(_nested(after, path), "/".join(path))
    if first is None or last is None:
        return None
    return round(last - first, 3)


def correlate(
    samples: list[dict[str, object]],
    summary: dict[str, object],
    markers: list[int],
    *,
    max_offset_ms: int,
) -> dict[str, object]:
    timestamps = [int(sample["sample_unix_ms"]) for sample in samples]
    events: list[dict[str, object]] = []
    unmatched = 0
    for marker_index, marker in enumerate(markers, start=1):
        after_index = bisect.bisect_left(timestamps, marker)
        if after_index == 0 or after_index >= len(samples):
            unmatched += 1
            continue
        before_index = after_index - 1
        before_offset = marker - timestamps[before_index]
        after_offset = timestamps[after_index] - marker
        if before_offset > max_offset_ms or after_offset > max_offset_ms:
            unmatched += 1
            continue
        before = samples[before_index]
        after = samples[after_index]
        events.append(
            {
                "marker_index": marker_index,
                "before_offset_ms": before_offset,
                "after_offset_ms": after_offset,
                "hotspot_delta_c": _delta(
                    before, after, ("temperature", "controller_hotspot_c")
                ),
                "target_pwm_delta": _delta(
                    before, after, ("fan", "target_pwm", "value")
                ),
                "actual_pwm_delta": _delta(
                    before, after, ("fan", "actual_pwm", "value")
                ),
                "rpm_delta": _delta(
                    before, after, ("fan", "actual_rpm", "value")
                ),
                "cpu_percent_delta": _delta(
                    before, after, ("compute", "cpu_percent")
                ),
                "gpu_percent_delta": _delta(
                    before, after, ("compute", "gpu", "gpu_percent")
                ),
                "decoder_before": _nested(
                    before, ("video_decode", "decoder_fd_observed")
                ),
                "decoder_after": _nested(
                    after, ("video_decode", "decoder_fd_observed")
                ),
            }
        )
    intervals = [right - left for left, right in zip(timestamps, timestamps[1:])]
    integrity = (
        summary.get("collector_integrity") == "PASS"
        and summary.get("controller_policy_evaluation") == "PASS"
    )
    gates = {
        "collector_and_policy_pass": integrity,
        "all_markers_aligned": unmatched == 0,
        "at_least_one_marker": bool(markers),
    }
    return {
        "schema": REPORT_SCHEMA,
        "read_only": True,
        "privacy": {
            "absolute_timestamps_emitted": False,
            "process_names_emitted": False,
            "video_identifiers_emitted": False,
        },
        "sample_count": len(samples),
        "marker_count": len(markers),
        "matched_marker_count": len(events),
        "unmatched_marker_count": unmatched,
        "median_sample_interval_ms": round(statistics.median(intervals), 3),
        "events": events,
        "observations": {
            "positive_target_pwm_at_markers": sum(
                isinstance(event["target_pwm_delta"], (int, float))
                and event["target_pwm_delta"] > 0
                for event in events
            ),
            "positive_actual_pwm_at_markers": sum(
                isinstance(event["actual_pwm_delta"], (int, float))
                and event["actual_pwm_delta"] > 0
                for event in events
            ),
            "positive_rpm_at_markers": sum(
                isinstance(event["rpm_delta"], (int, float))
                and event["rpm_delta"] > 0
                for event in events
            ),
            "decoder_observed_at_markers": sum(
                event["decoder_before"] or event["decoder_after"] for event in events
            ),
        },
        "acoustic_causality": "NOT_DETERMINED",
        "gates": gates,
        "complete": all(gates.values()),
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
    subparsers = parser.add_subparsers(dest="command", required=True)
    mark = subparsers.add_parser("mark", help="append one private noise marker")
    mark.add_argument("--markers", type=Path, required=True)
    analyze = subparsers.add_parser("analyze", help="align markers to collector JSONL")
    analyze.add_argument("--evidence", type=Path, required=True)
    analyze.add_argument("--markers", type=Path, required=True)
    analyze.add_argument("--output", default="-")
    analyze.add_argument("--max-offset-ms", type=int, default=3_000)
    arguments = parser.parse_args(argv)
    if arguments.command == "analyze" and not 500 <= arguments.max_offset_ms <= 10_000:
        parser.error("--max-offset-ms must be between 500 and 10000")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if arguments.command == "mark":
            append_marker(arguments.markers)
            print("fan noise marker recorded")
            return 0
        evidence_records = parse_jsonl(
            read_private_bytes(arguments.evidence, maximum=MAX_EVIDENCE_BYTES),
            maximum_records=MAX_SAMPLES + 1,
        )
        samples, summary = parse_evidence(evidence_records)
        marker_records = parse_jsonl(
            read_private_bytes(arguments.markers, maximum=MAX_MARKER_BYTES),
            maximum_records=MAX_MARKERS,
        )
        markers = parse_markers(marker_records)
        report = correlate(
            samples,
            summary,
            markers,
            max_offset_ms=arguments.max_offset_ms,
        )
        write_report(report, arguments.output)
    except (CorrelationError, OSError) as exc:
        print(f"fan correlation failed: {exc}", file=sys.stderr)
        return 2
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

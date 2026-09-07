#!/usr/bin/env python3
"""Evaluate private matched PDS-002 IFPC A/B collector reports offline."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "ab-evaluation-lock.json"
LEDGER_SCHEMA = "pocketds.kernel-ab-run-ledger.v1"
REPORT_SCHEMA = "pocketds.kernel-ab-matched-evaluation.v1"
MAX_LEDGER_BYTES = 256 * 1024
MAX_REPORT_BYTES = 64 * 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
REPORT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
OPERATOR_FIELDS = {
    "black_screen_or_desktop_failed",
    "thermal_power_or_fan_limit_exceeded",
    "local_console_and_ssh_both_unavailable",
    "manual_recovery_required",
}
ZERO_CANDIDATE_EVENTS = (
    "gmu_oob_timeout",
    "hfi_error",
    "gpu_lockup",
    "gpu_recover",
    "gpu_offender",
    "fenced_register_delay",
    "dma_fence_log",
    "dma_fence_wait",
    "hung_task",
)


class EvaluationError(RuntimeError):
    """Input evidence is unsafe, malformed or not bound to the experiment."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def strict_json(content: bytes, where: str) -> object:
    def reject_constant(value: str) -> object:
        raise EvaluationError(f"{where} contains non-finite JSON: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise EvaluationError(f"{where} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise EvaluationError(f"{where} is not strict UTF-8 JSON") from exc


def read_regular(path: Path, maximum: int, *, private: bool) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvaluationError(f"evidence is unavailable or linked: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > maximum
            or before.st_nlink != 1
            or (private and (mode != 0o600 or before.st_uid != os.getuid()))
            or (not private and mode & 0o022)
        ):
            raise EvaluationError(f"evidence metadata is unsafe: {path.name}")
        remaining = before.st_size
        content = bytearray()
        while remaining:
            block = os.read(descriptor, min(1_048_576, remaining))
            if not block:
                raise EvaluationError(f"evidence changed while reading: {path.name}")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise EvaluationError(f"evidence grew while reading: {path.name}")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_mode,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_mode,
        ):
            raise EvaluationError(f"evidence identity changed: {path.name}")
        return bytes(content), before
    finally:
        os.close(descriptor)


def load_lock() -> tuple[dict[str, Any], str]:
    content, _metadata = read_regular(LOCK_PATH, MAX_LEDGER_BYTES, private=False)
    parsed = strict_json(content, "A/B evaluation lock")
    if not isinstance(parsed, dict) or parsed.get("schema_version") != 1:
        raise EvaluationError("A/B evaluation lock schema differs")
    return parsed, hashlib.sha256(content).hexdigest()


def load_ledger(path: Path, lock: dict[str, Any]) -> dict[str, Any]:
    content, _metadata = read_regular(path, MAX_LEDGER_BYTES, private=True)
    ledger = strict_json(content, "A/B run ledger")
    if not isinstance(ledger, dict) or set(ledger) != {"schema", "experiment", "workloads", "runs"}:
        raise EvaluationError("A/B run ledger fields differ")
    if ledger["schema"] != LEDGER_SCHEMA or ledger["experiment"] != lock["experiment"]:
        raise EvaluationError("A/B run ledger identity differs")
    workloads = ledger["workloads"]
    if not isinstance(workloads, dict) or set(workloads) != set(lock["profiles"]):
        raise EvaluationError("workload profile set differs")
    if any(not isinstance(value, str) or HEX64.fullmatch(value) is None for value in workloads.values()):
        raise EvaluationError("workload identity is not a lowercase SHA-256")
    runs = ledger["runs"]
    if not isinstance(runs, list):
        raise EvaluationError("run list is invalid")
    expected_count = 2 * len(lock["profiles"]) * lock["minimum_rounds_per_variant"]
    if len(runs) != expected_count:
        raise EvaluationError("run count differs from the complete matched matrix")
    return ledger


def finite_number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise EvaluationError(f"invalid numeric field: {name}")
    return float(value)


def control_identity(value: object, expected: dict[str, Any], name: str) -> bool:
    return isinstance(value, dict) and value == {
        "sha256": expected["sha256"], "size": expected["size"], "error": None
    }


def control_set_matches(value: object, expected: dict[str, Any]) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(expected)
        and all(
            control_identity(value[name], identity, name)
            for name, identity in expected.items()
        )
    )


def profile_matches(display: object, expected: dict[str, list[int]]) -> bool:
    if not isinstance(display, dict) or not isinstance(display.get("kwin"), dict):
        return False
    kwin = display["kwin"]
    if kwin.get("backend") != "DRM" or kwin.get("atomic") != "true" or kwin.get("renderer") != "FD740":
        return False
    outputs = kwin.get("outputs")
    if not isinstance(outputs, list):
        return False
    enabled: dict[str, object] = {}
    for output in outputs:
        if not isinstance(output, dict) or not isinstance(output.get("name"), str):
            return False
        if output.get("enabled") is True:
            enabled[output["name"]] = output.get("refresh_millihz")
    return set(enabled) == set(expected) and all(enabled[name] in rates for name, rates in expected.items())


def pressure_means(samples: list[dict[str, Any]]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for resource in ("cpu", "io", "memory"):
        values: list[float] = []
        for sample in samples:
            try:
                value = sample["pressure"][resource]["some"]["avg10"]
            except (KeyError, TypeError):
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                values.append(float(value))
        result[resource] = round(sum(values) / len(values), 6) if values else None
    return result


def load_collector_report(path: Path, lock: dict[str, Any]) -> dict[str, Any]:
    content, metadata = read_regular(path, MAX_REPORT_BYTES, private=True)
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(content.splitlines(), 1):
        if not line:
            continue
        parsed = strict_json(line, f"collector line {line_number}")
        if not isinstance(parsed, dict) or parsed.get("schema") != lock["collector_schema"]:
            raise EvaluationError("collector record schema differs")
        records.append(parsed)
    if len(records) < 3 or records[0].get("type") != "header" or records[-1].get("type") != "summary":
        raise EvaluationError("collector framing differs")
    if any(record.get("type") == "header" for record in records[1:]) or any(
        record.get("type") == "summary" for record in records[:-1]
    ):
        raise EvaluationError("collector has duplicate framing records")
    header = records[0]
    summary = records[-1]
    samples = [record for record in records[1:-1] if record.get("type") == "sample"]
    if len(samples) != summary.get("sample_count"):
        raise EvaluationError("collector sample count differs")
    if [sample.get("seq") for sample in samples] != list(range(len(samples))):
        raise EvaluationError("collector sample sequence differs")
    boot = header.get("boot")
    notes = boot.get("notes") if isinstance(boot, dict) else None
    boot_id = boot.get("id") if isinstance(boot, dict) else None
    if (
        not isinstance(boot, dict)
        or not isinstance(boot_id, str)
        or not 1 <= len(boot_id) <= 128
        or boot.get("kernel") != lock["kernel_release"]
        or not isinstance(notes, dict)
        or notes.get("size") != 128
        or notes.get("error") is not None
    ):
        raise EvaluationError("collector kernel identity is incomplete")
    matching = [name for name, value in lock["variants"].items() if notes.get("sha256") == value["notes_sha256"]]
    if len(matching) != 1:
        raise EvaluationError("collector kernel notes are not a locked variant")
    controls = header.get("fixed_controls")
    if not isinstance(controls, dict) or set(controls) != set(lock["fixed_controls"]):
        raise EvaluationError("collector fixed controls are incomplete")
    runtime_controls = header.get("runtime_controls")
    if (
        not isinstance(runtime_controls, dict)
        or set(runtime_controls) != set(lock["runtime_controls"])
    ):
        raise EvaluationError("collector runtime controls are incomplete")
    summary_fixed_controls = summary.get("fixed_controls")
    summary_runtime_controls = summary.get("runtime_controls")
    controls_match = control_set_matches(controls, lock["fixed_controls"])
    controls_match = controls_match and (
        isinstance(summary_fixed_controls, dict)
        and summary_fixed_controls.get("start") == controls
        and summary_fixed_controls.get("end") == controls
        and summary_fixed_controls.get("unchanged") is True
    )
    runtime_controls_match = control_set_matches(
        runtime_controls, lock["runtime_controls"]
    )
    runtime_controls_match = runtime_controls_match and all(
        control_set_matches(sample.get("runtime_controls"), lock["runtime_controls"])
        for sample in samples
    )
    runtime_controls_match = runtime_controls_match and (
        isinstance(summary_runtime_controls, dict)
        and summary_runtime_controls.get("start") == runtime_controls
        and summary_runtime_controls.get("end") == runtime_controls
        and summary_runtime_controls.get("unchanged") is True
        and summary_runtime_controls.get("signatures_seen")
        == [hashlib.sha256(canonical_bytes(runtime_controls)).hexdigest()]
    )
    interval = finite_number(header.get("interval_s"), "interval")
    duration = finite_number(header.get("duration_s"), "duration")
    wall = finite_number(summary.get("wall_s"), "wall duration")
    gap = summary.get("sample_gap_max_s")
    gap_value = finite_number(gap, "maximum sample gap") if gap is not None else math.inf
    expected_samples = duration / interval if interval > 0 else math.inf
    valid_gpu = [
        sample["gpu"] for sample in samples
        if isinstance(sample.get("gpu"), dict)
        and isinstance(sample["gpu"].get("age_ms"), int)
        and 0 <= sample["gpu"]["age_ms"] <= lock["maximum_gpu_sample_age_ms"]
    ]
    gpu_temperatures = [
        float(gpu["gpu_temp_c"])
        for gpu in valid_gpu
        if isinstance(gpu.get("gpu_temp_c"), (int, float))
        and not isinstance(gpu.get("gpu_temp_c"), bool)
        and math.isfinite(gpu["gpu_temp_c"])
    ]
    journal = summary.get("journal")
    display = summary.get("display")
    summary_boot = summary.get("boot")
    header_display = header.get("display")
    counts = summary.get("counts")
    bursts = summary.get("bursts")
    admissible = {
        "minimum_duration": duration >= lock["minimum_duration_seconds"] and wall >= lock["minimum_duration_seconds"],
        "sample_coverage": len(samples) >= expected_samples * lock["minimum_sample_coverage"],
        "sample_gap": gap_value <= interval * lock["maximum_sample_gap_factor"],
        "gpu_telemetry_coverage": len(valid_gpu) >= len(samples) * lock["minimum_sample_coverage"],
        "gpu_temperature_coverage": len(gpu_temperatures) >= len(samples) * lock["minimum_sample_coverage"],
        "gpu_temperature_bounded": bool(gpu_temperatures) and max(gpu_temperatures) <= lock["maximum_gpu_temperature_c"],
        "journal_integrity": isinstance(journal, dict) and journal.get("integrity") is True and journal.get("follow_errors") == [] and journal.get("follower_returncode") == 0,
        "boot_unchanged": isinstance(summary_boot, dict) and summary_boot.get("unchanged") is True and summary_boot.get("start") == boot.get("id") and summary_boot.get("end") == boot.get("id"),
        "display_unchanged": isinstance(display, dict) and isinstance(header_display, dict) and display.get("unchanged") is True and display.get("changed_during_run") is False and display.get("probe_errors") == [] and display.get("start_signature") == header_display.get("signature") and display.get("end_signature") == header_display.get("signature"),
        "fixed_controls_match": controls_match,
        "runtime_controls_match": runtime_controls_match,
        "collector_cpu_bounded": finite_number(summary.get("collector_cpu_percent"), "collector CPU") <= lock["maximum_collector_cpu_percent"],
        "completed": summary.get("interrupted") is False and summary.get("result") in {"clean", "observed"},
    }
    if not isinstance(counts, dict) or not isinstance(bursts, dict):
        raise EvaluationError("collector event aggregates are incomplete")
    event_counts: dict[str, int] = {}
    for name in ZERO_CANDIDATE_EVENTS + ("kwin_atomic_ebusy", "smmu_fault"):
        value = counts.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise EvaluationError("collector event count is invalid")
        event_counts[name] = value
    ebusy_burst = bursts.get("kwin_atomic_ebusy")
    if (
        not isinstance(ebusy_burst, dict)
        or not isinstance(ebusy_burst.get("max_events"), int)
        or isinstance(ebusy_burst.get("max_events"), bool)
        or ebusy_burst["max_events"] < 0
    ):
        raise EvaluationError("collector EBUSY burst is incomplete")
    critical_d_states = summary.get("critical_user_d_state_intervals")
    if not isinstance(critical_d_states, list):
        raise EvaluationError("collector critical D-state aggregate is incomplete")
    return {
        "variant": matching[0],
        "boot_id": boot_id,
        "workload_sha256": header.get("workload_sha256"),
        "repo_revision": header.get("repo_revision"),
        "display": header_display,
        "admissible": admissible,
        "counts": event_counts,
        "ebusy_max_burst": ebusy_burst["max_events"],
        "pressure_some_avg10": pressure_means(samples),
        "gpu_temperature_c": {
            "mean": round(sum(gpu_temperatures) / len(gpu_temperatures), 6) if gpu_temperatures else None,
            "max": round(max(gpu_temperatures), 6) if gpu_temperatures else None,
        },
        "collector_cpu_seconds": round(summary["collector_cpu_percent"] * wall / 100.0, 6),
        "critical_d_state_count": len(critical_d_states),
        "evidence": {"sha256": hashlib.sha256(content).hexdigest(), "size": metadata.st_size},
    }


def nonregression(candidate: float, baseline: float, ratio: float, allowance: float = 0.0) -> bool:
    return candidate <= baseline * ratio + allowance


def evaluate(ledger: dict[str, Any], lock: dict[str, Any], lock_sha256: str, root: Path) -> dict[str, Any]:
    indexed: dict[tuple[str, str, int], dict[str, Any]] = {}
    file_ids: set[tuple[int, int]] = set()
    evidence_hashes: set[str] = set()
    for raw in ledger["runs"]:
        if not isinstance(raw, dict) or set(raw) != {"variant", "profile", "round", "report", "operator"}:
            raise EvaluationError("run entry fields differ")
        variant, profile, round_number, filename = raw["variant"], raw["profile"], raw["round"], raw["report"]
        if variant not in lock["variants"] or profile not in lock["profiles"]:
            raise EvaluationError("run variant or profile differs")
        if not isinstance(round_number, int) or isinstance(round_number, bool) or not 1 <= round_number <= lock["minimum_rounds_per_variant"]:
            raise EvaluationError("run round differs")
        if not isinstance(filename, str) or REPORT_NAME.fullmatch(filename) is None:
            raise EvaluationError("report name is not a safe basename")
        operator = raw["operator"]
        if not isinstance(operator, dict) or set(operator) != OPERATOR_FIELDS or any(not isinstance(value, bool) for value in operator.values()):
            raise EvaluationError("operator observation fields differ")
        key = (variant, profile, round_number)
        if key in indexed:
            raise EvaluationError("duplicate run matrix cell")
        path = root / filename
        metadata = os.stat(path, follow_symlinks=False)
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in file_ids:
            raise EvaluationError("collector report inode was reused")
        file_ids.add(identity)
        report = load_collector_report(path, lock)
        if report["evidence"]["sha256"] in evidence_hashes:
            raise EvaluationError("collector report content was reused")
        evidence_hashes.add(report["evidence"]["sha256"])
        if report["variant"] != variant or report["workload_sha256"] != ledger["workloads"][profile]:
            raise EvaluationError("run identity or workload binding differs")
        report["profile_matches"] = profile_matches(report["display"], lock["profiles"][profile])
        report["operator_passed"] = not any(operator.values())
        indexed[key] = report
    expected = {
        (variant, profile, round_number)
        for variant in lock["variants"]
        for profile in lock["profiles"]
        for round_number in range(1, lock["minimum_rounds_per_variant"] + 1)
    }
    if set(indexed) != expected:
        raise EvaluationError("run matrix is incomplete")
    boot_groups: dict[tuple[str, int], set[object]] = defaultdict(set)
    for (variant, _profile, round_number), report in indexed.items():
        boot_groups[(variant, round_number)].add(report["boot_id"])
    if any(len(values) != 1 or None in values for values in boot_groups.values()):
        raise EvaluationError("each variant round must use one complete boot")
    boot_values = [next(iter(values)) for values in boot_groups.values()]
    if len(set(boot_values)) != len(boot_values):
        raise EvaluationError("a boot was reused across variant rounds")
    revisions = {report["repo_revision"] for report in indexed.values()}
    repo_revision_match = len(revisions) == 1 and isinstance(next(iter(revisions)), str) and re.fullmatch(r"[0-9a-f]{40}", next(iter(revisions))) is not None
    pairs: list[dict[str, Any]] = []
    for profile in lock["profiles"]:
        for round_number in range(1, lock["minimum_rounds_per_variant"] + 1):
            baseline = indexed[("baseline", profile, round_number)]
            candidate = indexed[("candidate", profile, round_number)]
            pressure_checks = {
                resource: baseline["pressure_some_avg10"][resource] is not None
                and candidate["pressure_some_avg10"][resource] is not None
                and nonregression(
                    candidate["pressure_some_avg10"][resource],
                    baseline["pressure_some_avg10"][resource],
                    lock["non_regression_ratio"],
                    lock["pressure_absolute_allowance"],
                )
                for resource in ("cpu", "io", "memory")
            }
            checks = {
                "reports_admissible": all(baseline["admissible"].values()) and all(candidate["admissible"].values()),
                "display_profiles_match": baseline["profile_matches"] and candidate["profile_matches"],
                "operator_observations_pass": baseline["operator_passed"] and candidate["operator_passed"],
                "candidate_zero_critical_events": all(candidate["counts"][name] == 0 for name in ZERO_CANDIDATE_EVENTS) and candidate["critical_d_state_count"] == 0,
                "smmu_fault_non_regression": candidate["counts"]["smmu_fault"] <= baseline["counts"]["smmu_fault"],
                "kwin_ebusy_rate_non_regression": candidate["counts"]["kwin_atomic_ebusy"] <= baseline["counts"]["kwin_atomic_ebusy"],
                "kwin_ebusy_burst_non_regression": candidate["ebusy_max_burst"] <= baseline["ebusy_max_burst"],
                "collector_cpu_non_regression": nonregression(candidate["collector_cpu_seconds"], baseline["collector_cpu_seconds"], lock["non_regression_ratio"], lock["collector_cpu_seconds_allowance"]),
                "gpu_temperature_non_regression": candidate["gpu_temperature_c"]["max"] is not None and baseline["gpu_temperature_c"]["max"] is not None and candidate["gpu_temperature_c"]["max"] <= baseline["gpu_temperature_c"]["max"] + lock["gpu_temperature_absolute_allowance_c"],
                "pressure_non_regression": all(pressure_checks.values()),
            }
            pairs.append({
                "profile": profile,
                "round": round_number,
                "baseline_evidence": baseline["evidence"],
                "candidate_evidence": candidate["evidence"],
                "checks": checks,
                "passed": all(checks.values()),
            })
    all_reports_admissible = all(all(report["admissible"].values()) for report in indexed.values())
    experiment_passed = repo_revision_match and all(pair["passed"] for pair in pairs)
    return {
        "schema": REPORT_SCHEMA,
        "read_only": True,
        "network": False,
        "experiment": lock["experiment"],
        "matrix": {
            "profiles": list(lock["profiles"]),
            "rounds_per_variant": lock["minimum_rounds_per_variant"],
            "collector_report_count": len(indexed),
            "matched_pair_count": len(pairs),
            "distinct_boot_count": len(set(boot_values)),
            "raw_boot_identifiers_recorded": False,
        },
        "pairs": pairs,
        "gates": {
            "complete_matrix": True,
            "all_reports_admissible": all_reports_admissible,
            "fixed_controls_and_workloads_bound": all(
                report["admissible"]["fixed_controls_match"]
                and report["admissible"]["runtime_controls_match"]
                for report in indexed.values()
            ),
            "power_and_fan_controls_bound": all(
                report["admissible"]["runtime_controls_match"]
                for report in indexed.values()
            ),
            "repository_revision_unchanged": repo_revision_match,
            "all_pair_checks_passed": all(pair["passed"] for pair in pairs),
            "experiment_acceptance_passed": experiment_passed,
            "candidate_install_authorized": False,
        },
        "lock_sha256": lock_sha256,
    }


def safe_output(path: Path, report: dict[str, Any]) -> None:
    payload = canonical_bytes(report) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if os.fstat(descriptor).st_nlink != 1:
            raise EvaluationError("output link count differs")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise EvaluationError("output write failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-pass", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        lock, lock_sha256 = load_lock()
        ledger = load_ledger(args.ledger, lock)
        report = evaluate(ledger, lock, lock_sha256, args.ledger.parent)
        safe_output(args.output, report)
    except (OSError, EvaluationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if args.require_pass and not report["gates"]["experiment_acceptance_passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

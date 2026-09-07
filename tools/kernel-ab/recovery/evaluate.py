#!/usr/bin/env python3
"""Aggregate a supervised PDS-002 independent recovery proof offline."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import sys


HERE = Path(__file__).resolve().parent
PREFLIGHT_SOURCE = HERE / "preflight.py"
PREFLIGHT_SPEC = importlib.util.spec_from_file_location(
    "pocketds_recovery_evaluate_preflight", PREFLIGHT_SOURCE
)
if PREFLIGHT_SPEC is None or PREFLIGHT_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("cannot load recovery preflight library")
preflight = importlib.util.module_from_spec(PREFLIGHT_SPEC)
sys.modules[PREFLIGHT_SPEC.name] = preflight
PREFLIGHT_SPEC.loader.exec_module(preflight)
payload = preflight.payload

DISPATCH_SOURCE = HERE / "dispatch.py"
DISPATCH_SPEC = importlib.util.spec_from_file_location(
    "pocketds_recovery_evaluate_dispatch", DISPATCH_SOURCE
)
if DISPATCH_SPEC is None or DISPATCH_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("cannot load recovery dispatch library")
dispatch = importlib.util.module_from_spec(DISPATCH_SPEC)
sys.modules[DISPATCH_SPEC.name] = dispatch
DISPATCH_SPEC.loader.exec_module(dispatch)

ROLLBACK_SOURCE = HERE.parent / "rollback" / "preflight.py"
ROLLBACK_SPEC = importlib.util.spec_from_file_location(
    "pocketds_recovery_evaluate_static_rollback", ROLLBACK_SOURCE
)
if ROLLBACK_SPEC is None or ROLLBACK_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("cannot load static rollback preflight library")
rollback_preflight = importlib.util.module_from_spec(ROLLBACK_SPEC)
sys.modules[ROLLBACK_SPEC.name] = rollback_preflight
ROLLBACK_SPEC.loader.exec_module(rollback_preflight)


EvaluationError = preflight.PreflightError
REPORT_SCHEMA = "pocketds.kernel-fastboot-recovery-validation.v1"
STATIC_SCHEMA = "pocketds.kernel-rollback-static-preflight.v1"
MAX_REPORT = 2 * 1024 * 1024
HOST_FIELDS = {
    "schema", "read_only", "network", "device_access", "device_id",
    "kernel_release", "artifacts", "boot_images", "rocknix_abl",
    "host_fastboot", "recovery_design", "gates", "lock_sha256",
}
DISPATCH_FIELDS = {
    "schema", "action", "device_access", "preflight", "artifact",
    "recovery_design", "gates", "lock_sha256",
}
RUNTIME_FIELDS = {
    "schema", "read_only", "network", "device_id", "runtime", "disk",
    "gates", "lock_sha256",
}
RUNTIME_IDENTITY_FIELDS = {
    "expected_identity", "kernel_release", "machine_model", "notes_sha256",
    "build_id_hex", "boot_token_sha256", "uptime_seconds",
    "max_uptime_seconds", "observed_at_unix_ns", "boot_started_at_unix_ns",
}
DISK_FIELDS = {"expected_identity", "sha256", "size", "aliases"}
ALIAS_FIELDS = {"path_id", "sha256", "size"}
RUNTIME_GATE_FIELDS = {
    "machine_and_release_match", "running_kernel_build_id_matches",
    "disk_boot_aliases_match", "boot_is_within_recovery_window",
    "independent_runtime_boot_observed", "rollback_artifact_prevalidated",
    "candidate_install_authorized",
}
HOST_GATE_FIELDS = {
    "host_recovery_material_preflight_passed", "live_fastboot_unlock_verified",
    "independent_runtime_boot_observed", "rollback_artifact_prevalidated",
    "candidate_install_authorized",
}
DISPATCH_GATE_FIELDS = {
    "live_fastboot_unlock_verified", "temporary_baseline_boot_dispatched",
    "independent_runtime_boot_observed", "rollback_artifact_prevalidated",
    "candidate_install_authorized",
}


def _strict(value: object, fields: set[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise EvaluationError(f"{name} fields differ")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or payload.HEX64.fullmatch(value) is None:
        raise EvaluationError(f"invalid {name}")
    return value


def _private_report(path: Path, schema: str) -> tuple[dict[str, object], dict[str, object]]:
    content, metadata = payload._read_regular(path, maximum=MAX_REPORT)
    if (
        stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_uid != os.getuid()
    ):
        raise EvaluationError(f"report privacy metadata differs: {path.name}")
    parsed = payload._strict_json(content, path.name)
    if not isinstance(parsed, dict) or parsed.get("schema") != schema:
        raise EvaluationError(f"report schema differs: {path.name}")
    return parsed, {
        "filename": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": metadata.st_size,
    }


def _false_install(gates: dict[str, object], name: str) -> None:
    if (
        gates.get("rollback_artifact_prevalidated") is not False
        or gates.get("candidate_install_authorized") is not False
    ):
        raise EvaluationError(f"{name} overclaims recovery or install readiness")


def _validate_static(
    report: dict[str, object], rollback_lock: dict[str, object], rollback_lock_sha256: str
) -> None:
    artifact = report.get("artifact")
    expected = rollback_lock["artifacts"]["rollback_bootimg"]
    if (
        report.get("read_only") is not True
        or report.get("network") is not False
        or report.get("lock_sha256") != rollback_lock_sha256
        or not isinstance(artifact, dict)
        or artifact.get("artifact_id") != rollback_lock["artifact_id"]
        or artifact.get("device_id") != rollback_lock["device_id"]
        or artifact.get("kernel_release") != rollback_lock["kernel_release"]
        or {key: artifact.get(key) for key in ("filename", "sha256", "size")} != expected
        or report.get("recovery_validation") != {
            "status": "not_run",
            "successful_boots": 0,
            "successful_recovery_tests": 0,
            "independent_recovery_path": False,
        }
    ):
        raise EvaluationError("static rollback preflight binding differs")
    gates = report.get("gates")
    if not isinstance(gates, dict):
        raise EvaluationError("static rollback gates are missing")
    for key in (
        "rollback_artifact_content_locked",
        "baseline_source_to_binary_evidence_bound",
        "signed_and_reproduced_baseline_bound",
        "custom_dtb_source_and_rebuild_evidence_bound",
        "runtime_boot_composition_bound",
        "android_bootimg_structure_verified",
        "candidate_evidence_bound",
        "static_rollback_preflight_passed",
    ):
        if gates.get(key) is not True:
            raise EvaluationError(f"static rollback gate is not true: {key}")
    _false_install(gates, "static rollback preflight")


def _validate_host(
    report: dict[str, object], lock: dict[str, object], lock_sha256: str
) -> None:
    _strict(report, HOST_FIELDS, "host preflight")
    if (
        report["read_only"] is not True
        or report["network"] is not False
        or report["device_access"] is not False
        or report["device_id"] != lock["device_id"]
        or report["kernel_release"] != lock["kernel_release"]
        or report["lock_sha256"] != lock_sha256
        or report["artifacts"] != lock["artifacts"]
    ):
        raise EvaluationError("host recovery preflight binding differs")
    gates = _strict(report["gates"], HOST_GATE_FIELDS, "host preflight gates")
    boot = report["boot_images"]
    abl = report["rocknix_abl"]
    fastboot = report["host_fastboot"]
    design = report["recovery_design"]
    if (
        gates.get("host_recovery_material_preflight_passed") is not True
        or gates.get("live_fastboot_unlock_verified") is not False
        or gates.get("independent_runtime_boot_observed") is not False
        or not isinstance(boot, dict)
        or set(boot) != {"baseline", "candidate"}
        or boot["baseline"] != {
            "kernel_size": lock["boot_images"]["baseline_kernel_size"],
            "page_size": lock["boot_images"]["page_size"],
            "image_id_hex": lock["boot_images"]["baseline_image_id_hex"],
        }
        or boot["candidate"] != {
            "kernel_size": lock["boot_images"]["candidate_kernel_size"],
            "page_size": lock["boot_images"]["page_size"],
            "image_id_hex": lock["boot_images"]["candidate_image_id_hex"],
        }
        or not isinstance(abl, dict)
        or abl.get("release") != lock["rocknix_abl"]["release"]
        or abl.get("linuxloader_commit") != lock["rocknix_abl"]["linuxloader_commit"]
        or abl.get("official_release_asset_digest_bound") is not True
        or not isinstance(abl.get("fastboot_evidence_string_plaintext_in_wrapper"), bool)
        or fastboot != {
            "version_line": lock["host_fastboot"]["version_line"],
            "exit_code": 0,
        }
        or design != {
            "transport": "fastboot-ram-boot",
            "temporary_boot_subcommand": "boot",
            "requires_unlocked": True,
            "writes_partition": False,
            "runtime_proof": "baseline /sys/kernel/notes with candidate image still on disk",
        }
    ):
        raise EvaluationError("host recovery preflight gates differ")
    _false_install(gates, "host recovery preflight")


def _validate_dispatch(
    report: dict[str, object], host: dict[str, object], lock: dict[str, object], lock_sha256: str
) -> None:
    _strict(report, DISPATCH_FIELDS, "fastboot dispatch")
    design = report["recovery_design"]
    gates = _strict(report["gates"], DISPATCH_GATE_FIELDS, "fastboot dispatch gates")
    if (
        report["action"] != "temporary_baseline_boot_dispatched"
        or report["device_access"] is not True
        or report["preflight"] != host["gates"]
        or report["artifact"] != lock["artifacts"]["baseline_rollback_boot"]
        or report["lock_sha256"] != lock_sha256
        or not isinstance(design, dict)
        or design.get("transport") != "fastboot-ram-boot"
        or design.get("subcommand") != "boot"
        or design.get("device_count") != 1
        or design.get("unlocked") is not True
        or design.get("writes_partition") is not False
        or design.get("device_identifier_recorded") is not False
        or gates.get("live_fastboot_unlock_verified") is not True
        or gates.get("temporary_baseline_boot_dispatched") is not True
        or gates.get("independent_runtime_boot_observed") is not False
    ):
        raise EvaluationError("fastboot dispatch binding differs")
    _false_install(gates, "fastboot dispatch")


def _runtime_expected(lock: dict[str, object], identity: str) -> dict[str, object]:
    runtime = lock["runtime_identity"]
    return {
        "notes_sha256": runtime[f"{identity}_notes_sha256"],
        "build_id_hex": runtime[f"{identity}_build_id_hex"],
    }


def _disk_expected(lock: dict[str, object], identity: str) -> dict[str, object]:
    artifact = (
        "baseline_rollback_boot" if identity == "baseline" else "candidate_runtime_boot"
    )
    return lock["artifacts"][artifact]


def _validate_runtime(
    report: dict[str, object],
    lock: dict[str, object],
    lock_sha256: str,
    *,
    running: str,
    disk_identity: str,
    independent: bool,
) -> tuple[str, float, int, int]:
    _strict(report, RUNTIME_FIELDS, "runtime attestation")
    runtime = _strict(report["runtime"], RUNTIME_IDENTITY_FIELDS, "runtime identity")
    disk = _strict(report["disk"], DISK_FIELDS, "disk identity")
    gates = _strict(report["gates"], RUNTIME_GATE_FIELDS, "runtime gates")
    expected_runtime = _runtime_expected(lock, running)
    expected_disk = _disk_expected(lock, disk_identity)
    uptime = runtime["uptime_seconds"]
    maximum = runtime["max_uptime_seconds"]
    observed_at = runtime["observed_at_unix_ns"]
    boot_started_at = runtime["boot_started_at_unix_ns"]
    if (
        report["read_only"] is not True
        or report["network"] is not False
        or report["device_id"] != lock["device_id"]
        or report["lock_sha256"] != lock_sha256
        or runtime["expected_identity"] != running
        or runtime["kernel_release"] != lock["kernel_release"]
        or runtime["machine_model"] != lock["machine_model"]
        or runtime["notes_sha256"] != expected_runtime["notes_sha256"]
        or runtime["build_id_hex"] != expected_runtime["build_id_hex"]
        or not isinstance(uptime, (int, float))
        or isinstance(uptime, bool)
        or not math.isfinite(uptime)
        or uptime < 0
        or not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or maximum < 60
        or maximum > 1800
        or uptime > maximum
        or not isinstance(observed_at, int)
        or isinstance(observed_at, bool)
        or not isinstance(boot_started_at, int)
        or isinstance(boot_started_at, bool)
        or boot_started_at <= 0
        or observed_at <= boot_started_at
        or abs((observed_at - boot_started_at) - float(uptime) * 1_000_000_000) > 2_000_000_000
        or disk["expected_identity"] != disk_identity
        or disk["sha256"] != expected_disk["sha256"]
        or disk["size"] != expected_disk["size"]
    ):
        raise EvaluationError("runtime or disk identity binding differs")
    aliases = disk["aliases"]
    if not isinstance(aliases, list) or len(aliases) != len(lock["disk_boot_aliases"]):
        raise EvaluationError("disk alias count differs")
    expected_path_ids = {
        hashlib.sha256(("pds002-boot-alias-v1\x00" + relative).encode()).hexdigest()
        for relative in lock["disk_boot_aliases"]
    }
    measured_path_ids: set[str] = set()
    for item in aliases:
        alias = _strict(item, ALIAS_FIELDS, "disk alias")
        measured_path_ids.add(_digest(alias["path_id"], "disk alias path ID"))
        if alias["sha256"] != expected_disk["sha256"] or alias["size"] != expected_disk["size"]:
            raise EvaluationError("disk alias content differs")
    if measured_path_ids != expected_path_ids:
        raise EvaluationError("disk alias path set differs")
    expected_gates = {
        "machine_and_release_match": True,
        "running_kernel_build_id_matches": True,
        "disk_boot_aliases_match": True,
        "boot_is_within_recovery_window": True,
        "independent_runtime_boot_observed": independent,
        "rollback_artifact_prevalidated": False,
        "candidate_install_authorized": False,
    }
    if gates != expected_gates:
        raise EvaluationError("runtime attestation gates differ")
    token = _digest(runtime["boot_token_sha256"], "boot token")
    return token, float(uptime), observed_at, boot_started_at


def collect(
    lock: dict[str, object],
    *,
    lock_sha256: str,
    rollback_lock: dict[str, object],
    rollback_lock_sha256: str,
    static_report: dict[str, object],
    host_report: dict[str, object],
    dispatch_report: dict[str, object],
    ram_report: dict[str, object],
    restored_report: dict[str, object],
    normal_report: dict[str, object],
    evidence: dict[str, dict[str, object]],
) -> dict[str, object]:
    static_artifact = rollback_lock["artifacts"]["rollback_bootimg"]
    if (
        rollback_lock["device_id"] != lock["device_id"]
        or rollback_lock["kernel_release"] != lock["kernel_release"]
        or static_artifact != lock["artifacts"]["baseline_rollback_boot"]
    ):
        raise EvaluationError("static and runtime recovery locks are not bound")
    _validate_static(static_report, rollback_lock, rollback_lock_sha256)
    _validate_host(host_report, lock, lock_sha256)
    _validate_dispatch(dispatch_report, host_report, lock, lock_sha256)
    ram_token, ram_uptime, ram_observed, ram_started = _validate_runtime(
        ram_report, lock, lock_sha256, running="baseline", disk_identity="candidate",
        independent=True,
    )
    restored_token, restored_uptime, restored_observed, restored_started = _validate_runtime(
        restored_report, lock, lock_sha256, running="baseline", disk_identity="baseline",
        independent=False,
    )
    normal_token, _normal_uptime, _normal_observed, normal_started = _validate_runtime(
        normal_report, lock, lock_sha256, running="baseline", disk_identity="baseline",
        independent=False,
    )
    if (
        ram_token != restored_token
        or restored_uptime < ram_uptime
        or abs(restored_started - ram_started) > 2_000_000_000
        or restored_observed < ram_observed
        or normal_token == ram_token
        or normal_started <= restored_observed
    ):
        raise EvaluationError("recovery boot-token or observation order differs")
    return {
        "schema": REPORT_SCHEMA,
        "read_only": True,
        "network": False,
        "device_id": lock["device_id"],
        "artifact": {
            "artifact_id": rollback_lock["artifact_id"],
            **lock["artifacts"]["baseline_rollback_boot"],
        },
        "evidence": evidence,
        "sequence": {
            "host_material_preflight_bound": True,
            "temporary_baseline_ram_boot_dispatched": True,
            "baseline_running_over_candidate_disk_observed": True,
            "same_ram_boot_restored_all_baseline_aliases": True,
            "new_normal_baseline_boot_observed": True,
            "raw_boot_identifier_recorded": False,
        },
        "validation": {
            "status": "passed",
            "successful_boots": 1,
            "successful_recovery_tests": 1,
            "independent_recovery_path": True,
        },
        "gates": {
            "static_rollback_preflight_passed": True,
            "host_recovery_material_preflight_passed": True,
            "live_fastboot_unlock_verified": True,
            "independent_runtime_boot_observed": True,
            "disk_aliases_restored_in_same_ram_boot": True,
            "normal_baseline_boot_verified": True,
            "rollback_artifact_prevalidated": True,
            "candidate_install_authorized": False,
        },
        "recovery_lock_sha256": lock_sha256,
        "static_rollback_lock_sha256": rollback_lock_sha256,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-preflight", type=Path, required=True)
    parser.add_argument("--host-preflight", type=Path, required=True)
    parser.add_argument("--dispatch", type=Path, required=True)
    parser.add_argument("--ram-attestation", type=Path, required=True)
    parser.add_argument("--restored-attestation", type=Path, required=True)
    parser.add_argument("--normal-boot-attestation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    paths = {
        "static_preflight": (args.static_preflight, STATIC_SCHEMA),
        "host_preflight": (args.host_preflight, preflight.REPORT_SCHEMA),
        "dispatch": (args.dispatch, dispatch.REPORT_SCHEMA),
        "ram_attestation": (args.ram_attestation, "pocketds.kernel-fastboot-recovery-runtime-attestation.v2"),
        "restored_attestation": (args.restored_attestation, "pocketds.kernel-fastboot-recovery-runtime-attestation.v2"),
        "normal_boot_attestation": (args.normal_boot_attestation, "pocketds.kernel-fastboot-recovery-runtime-attestation.v2"),
    }
    try:
        if len({os.path.abspath(path) for path, _schema in paths.values()}) != len(paths):
            raise EvaluationError("recovery evidence paths are not unique")
        raw, lock_sha256 = payload.load_lock(HERE / "lock.json")
        lock = preflight.validate_lock(raw)
        rollback_raw, rollback_lock_sha256 = payload.load_lock(
            HERE.parent / "rollback" / "lock.json"
        )
        rollback_lock = rollback_preflight.validate_lock(rollback_raw)
        reports: dict[str, dict[str, object]] = {}
        evidence: dict[str, dict[str, object]] = {}
        for name, (path, schema) in paths.items():
            reports[name], evidence[name] = _private_report(path, schema)
        report = collect(
            lock,
            lock_sha256=lock_sha256,
            rollback_lock=rollback_lock,
            rollback_lock_sha256=rollback_lock_sha256,
            static_report=reports["static_preflight"],
            host_report=reports["host_preflight"],
            dispatch_report=reports["dispatch"],
            ram_report=reports["ram_attestation"],
            restored_report=reports["restored_attestation"],
            normal_report=reports["normal_boot_attestation"],
            evidence=evidence,
        )
        dispatch._safe_output(args.output, report)
    except (
        OSError,
        EvaluationError,
        dispatch.DispatchError,
        rollback_preflight.PreflightError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

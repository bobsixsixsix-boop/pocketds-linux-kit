#!/usr/bin/env python3
"""Fail-closed, stdout-only planner for PocketDS kernel A/B manifests.

The planner is deliberately not a builder. It never invokes Git, emits shell
commands, writes output files, builds a kernel, installs an artifact, or contacts
a device. It validates a manifest against the pinned policy beside this file and
prints a deterministic declarative plan only after every check succeeds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


PLANNER_VERSION = "1.1.0"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
FIXTURE_PLACEHOLDER_PREFIX = "PLACEHOLDER-NOT-FOR-REAL-PLAN:"


class PlanError(ValueError):
    """The requested plan is ambiguous, unsafe, or outside pinned policy."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PlanError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle, object_pairs_hook=_reject_duplicate_keys)
    except OSError as exc:
        raise PlanError(f"cannot read {path}: {exc.strerror}") from exc
    except json.JSONDecodeError as exc:
        raise PlanError(f"invalid JSON in {path}: {exc.msg} at line {exc.lineno}") from exc
    if not isinstance(value, dict):
        raise PlanError(f"top-level JSON value in {path} must be an object")
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _strict_keys(
    obj: dict[str, Any],
    *,
    where: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    unknown = set(obj) - required - optional
    missing = required - set(obj)
    if unknown:
        raise PlanError(f"{where} contains unknown keys: {', '.join(sorted(unknown))}")
    if missing:
        raise PlanError(f"{where} is missing required keys: {', '.join(sorted(missing))}")


def _object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanError(f"{where} must be an object")
    return value


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PlanError(f"{where} must be a non-empty string without edge whitespace")
    return value


def _integer(value: Any, where: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlanError(f"{where} must be an integer")
    if not minimum <= value <= maximum:
        raise PlanError(f"{where} must be between {minimum} and {maximum}")
    return value


def _true(value: Any, where: str) -> bool:
    if value is not True:
        raise PlanError(f"{where} must be true")
    return True


def _hex(value: Any, where: str, regex: re.Pattern[str]) -> str:
    text = _string(value, where)
    if not regex.fullmatch(text):
        width = 40 if regex is HEX40 else 64
        raise PlanError(f"{where} must be a full lowercase {width}-hex digest")
    return text


def _artifact_hash(value: Any, where: str) -> str:
    text = _string(value, where)
    if text.startswith(FIXTURE_PLACEHOLDER_PREFIX):
        raise PlanError(f"{where} is a fixture placeholder and is forbidden in a real plan")
    return _hex(text, where, HEX64)


def _non_placeholder_string(value: Any, where: str) -> str:
    text = _string(value, where)
    if text.startswith(FIXTURE_PLACEHOLDER_PREFIX):
        raise PlanError(f"{where} is a fixture placeholder and is forbidden in a real plan")
    return text


def _string_list(value: Any, where: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        qualifier = "non-empty " if nonempty else ""
        raise PlanError(f"{where} must be a {qualifier}array")
    result = [_string(item, f"{where}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise PlanError(f"{where} must not contain duplicates")
    return result


def validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    _strict_keys(
        policy,
        where="policy",
        required={
            "policy_version",
            "planner_schema_version",
            "allowed_baselines",
            "experiments",
            "symmetric_patches",
        },
    )
    if policy["policy_version"] != 1 or policy["planner_schema_version"] != 2:
        raise PlanError("only policy_version=1 and planner_schema_version=2 are supported")

    baselines = _object(policy["allowed_baselines"], "policy.allowed_baselines")
    if not baselines:
        raise PlanError("policy.allowed_baselines must not be empty")
    for commit, entry_value in baselines.items():
        _hex(commit, "policy baseline key", HEX40)
        entry = _object(entry_value, f"policy.allowed_baselines[{commit}]")
        _strict_keys(
            entry,
            where=f"baseline {commit}",
            required={"label", "source_url", "device_id", "kernel_release"},
        )
        _string(entry["label"], f"baseline {commit}.label")
        _string(entry["source_url"], f"baseline {commit}.source_url")
        _string(entry["device_id"], f"baseline {commit}.device_id")
        _string(entry["kernel_release"], f"baseline {commit}.kernel_release")

    symmetric = _object(policy["symmetric_patches"], "policy.symmetric_patches")
    for commit, entry_value in symmetric.items():
        _hex(commit, "symmetric patch key", HEX40)
        entry = _object(entry_value, f"policy.symmetric_patches[{commit}]")
        _strict_keys(entry, where=f"symmetric patch {commit}", required={"name", "source_url"})
        _string(entry["name"], f"symmetric patch {commit}.name")
        _string(entry["source_url"], f"symmetric patch {commit}.source_url")

    experiments = _object(policy["experiments"], "policy.experiments")
    if not experiments:
        raise PlanError("policy.experiments must not be empty")
    seen_reverts: dict[str, str] = {}
    acceptance_keys = {
        "profile",
        "minimum_observation_minutes",
        "minimum_matched_runs",
        "recommended_matched_runs",
        "zero_event_classes",
        "non_regression_metrics",
        "required_invariants",
        "rollback_conditions",
    }
    for variable, entry_value in experiments.items():
        if not SLUG.fullmatch(variable):
            raise PlanError(f"invalid experiment variable slug in policy: {variable}")
        entry = _object(entry_value, f"policy.experiments[{variable}]")
        _strict_keys(
            entry,
            where=f"experiment {variable}",
            required={"description", "required_reverts", "acceptance"},
            optional={"review_patch"},
        )
        _string(entry["description"], f"experiment {variable}.description")
        if "review_patch" in entry:
            review_patch = _object(
                entry["review_patch"], f"experiment {variable}.review_patch"
            )
            _strict_keys(
                review_patch,
                where=f"experiment {variable}.review_patch",
                required={"path", "sha256"},
            )
            review_path = _string(
                review_patch["path"], f"experiment {variable}.review_patch.path"
            )
            if (
                not review_path.startswith("tools/kernel-ab/patches/")
                or ".." in review_path.split("/")
                or "//" in review_path
                or "\\" in review_path
            ):
                raise PlanError(
                    f"experiment {variable}.review_patch.path must be a safe repository patch path"
                )
            _artifact_hash(
                review_patch["sha256"], f"experiment {variable}.review_patch.sha256"
            )
        reverts = _string_list(
            entry["required_reverts"], f"experiment {variable}.required_reverts", nonempty=True
        )
        for index, commit in enumerate(reverts):
            _hex(commit, f"experiment {variable}.required_reverts[{index}]", HEX40)
            if commit in seen_reverts:
                raise PlanError(
                    f"revert {commit} is assigned to both {seen_reverts[commit]} and {variable}"
                )
            if commit in symmetric:
                raise PlanError(f"commit {commit} cannot be a variable revert and symmetric patch")
            seen_reverts[commit] = variable

        acceptance = _object(entry["acceptance"], f"experiment {variable}.acceptance")
        _strict_keys(acceptance, where=f"experiment {variable}.acceptance", required=acceptance_keys)
        _string(acceptance["profile"], f"experiment {variable}.acceptance.profile")
        minimum_minutes = _integer(
            acceptance["minimum_observation_minutes"],
            f"experiment {variable}.acceptance.minimum_observation_minutes",
            30,
            1440,
        )
        minimum_runs = _integer(
            acceptance["minimum_matched_runs"],
            f"experiment {variable}.acceptance.minimum_matched_runs",
            1,
            100,
        )
        _integer(
            acceptance["recommended_matched_runs"],
            f"experiment {variable}.acceptance.recommended_matched_runs",
            minimum_runs,
            100,
        )
        if minimum_minutes < 30:
            raise PlanError(f"experiment {variable} observation floor must be at least 30 minutes")
        for key in (
            "zero_event_classes",
            "non_regression_metrics",
            "required_invariants",
            "rollback_conditions",
        ):
            _string_list(acceptance[key], f"experiment {variable}.acceptance.{key}", nonempty=True)
    return policy


def normalize_manifest(manifest: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    _strict_keys(
        manifest,
        where="manifest",
        required={
            "schema_version",
            "experiment_id",
            "source_commit",
            "variable",
            "revert_commits",
            "acceptance_profile",
            "controls",
        },
        optional={"symmetric_patches"},
    )
    if manifest["schema_version"] != policy["planner_schema_version"]:
        raise PlanError("manifest schema_version does not match pinned planner policy")

    experiment_id = _string(manifest["experiment_id"], "manifest.experiment_id")
    if not SLUG.fullmatch(experiment_id):
        raise PlanError("manifest.experiment_id must be a 1-64 character lowercase slug")
    source_commit = _hex(manifest["source_commit"], "manifest.source_commit", HEX40)
    if source_commit not in policy["allowed_baselines"]:
        raise PlanError(f"source commit {source_commit} is not an allowed baseline")

    variable = _string(manifest["variable"], "manifest.variable")
    experiments = policy["experiments"]
    if variable not in experiments:
        raise PlanError(f"unknown or composite experiment variable: {variable}")
    reverts = _string_list(manifest["revert_commits"], "manifest.revert_commits", nonempty=True)
    for index, commit in enumerate(reverts):
        _hex(commit, f"manifest.revert_commits[{index}]", HEX40)
    expected_reverts = experiments[variable]["required_reverts"]
    if reverts != expected_reverts:
        owners = {
            commit: owner
            for owner, entry in experiments.items()
            for commit in entry["required_reverts"]
        }
        foreign = sorted({owners[c] for c in reverts if c in owners and owners[c] != variable})
        suffix = f"; variable mixing detected with: {', '.join(foreign)}" if foreign else ""
        raise PlanError(
            f"revert list for {variable} must exactly equal {expected_reverts}{suffix}"
        )

    symmetric_patches = _string_list(
        manifest.get("symmetric_patches", []), "manifest.symmetric_patches"
    )
    for index, commit in enumerate(symmetric_patches):
        _hex(commit, f"manifest.symmetric_patches[{index}]", HEX40)
        if commit not in policy["symmetric_patches"]:
            raise PlanError(f"symmetric patch {commit} is not pinned by policy")
    overlap = set(reverts) & set(symmetric_patches)
    if overlap:
        raise PlanError(f"commits cannot be reverted and symmetrically applied: {sorted(overlap)}")

    acceptance_profile = _string(manifest["acceptance_profile"], "manifest.acceptance_profile")
    expected_profile = experiments[variable]["acceptance"]["profile"]
    if acceptance_profile != expected_profile:
        raise PlanError(f"acceptance profile for {variable} must be exactly {expected_profile}")

    controls = _object(manifest["controls"], "manifest.controls")
    _strict_keys(
        controls,
        where="manifest.controls",
        required={
            "display_signature",
            "mesa_nevra",
            "kwin_nevra",
            "firmware_set_sha256",
            "kernel_config_sha256",
            "workload_id",
            "observation_minutes",
            "matched_runs",
            "rollback_artifact",
        },
    )
    rollback = _object(controls["rollback_artifact"], "controls.rollback_artifact")
    _strict_keys(
        rollback,
        where="controls.rollback_artifact",
        required={
            "artifact_id",
            "sha256",
            "size_bytes",
            "format",
            "device_id",
            "kernel_release",
            "validation",
        },
    )
    artifact_id = _non_placeholder_string(
        rollback["artifact_id"], "controls.rollback_artifact.artifact_id"
    )
    if not SLUG.fullmatch(artifact_id):
        raise PlanError("controls.rollback_artifact.artifact_id must be a lowercase slug")
    artifact_format = _string(rollback["format"], "controls.rollback_artifact.format")
    if artifact_format != "android-bootimg-v0":
        raise PlanError("controls.rollback_artifact.format must be android-bootimg-v0")
    baseline = policy["allowed_baselines"][source_commit]
    device_id = _non_placeholder_string(
        rollback["device_id"], "controls.rollback_artifact.device_id"
    )
    if device_id != baseline["device_id"]:
        raise PlanError("controls.rollback_artifact.device_id does not match the pinned baseline")
    kernel_release = _non_placeholder_string(
        rollback["kernel_release"], "controls.rollback_artifact.kernel_release"
    )
    if kernel_release != baseline["kernel_release"]:
        raise PlanError(
            "controls.rollback_artifact.kernel_release does not match the pinned baseline"
        )
    validation = _object(
        rollback["validation"], "controls.rollback_artifact.validation"
    )
    _strict_keys(
        validation,
        where="controls.rollback_artifact.validation",
        required={
            "status",
            "successful_boots",
            "successful_recovery_tests",
            "report_sha256",
            "independent_recovery_path",
        },
    )
    validation_status = _string(
        validation["status"], "controls.rollback_artifact.validation.status"
    )
    if validation_status != "passed":
        raise PlanError("controls.rollback_artifact.validation.status must be passed")
    normalized_rollback = {
        "artifact_id": artifact_id,
        "sha256": _artifact_hash(
            rollback["sha256"], "controls.rollback_artifact.sha256"
        ),
        "size_bytes": _integer(
            rollback["size_bytes"], "controls.rollback_artifact.size_bytes", 1, 1 << 34
        ),
        "format": artifact_format,
        "device_id": device_id,
        "kernel_release": kernel_release,
        "validation": {
            "status": validation_status,
            "successful_boots": _integer(
                validation["successful_boots"],
                "controls.rollback_artifact.validation.successful_boots",
                1,
                100,
            ),
            "successful_recovery_tests": _integer(
                validation["successful_recovery_tests"],
                "controls.rollback_artifact.validation.successful_recovery_tests",
                1,
                100,
            ),
            "report_sha256": _artifact_hash(
                validation["report_sha256"],
                "controls.rollback_artifact.validation.report_sha256",
            ),
            "independent_recovery_path": _true(
                validation["independent_recovery_path"],
                "controls.rollback_artifact.validation.independent_recovery_path",
            ),
        },
    }
    normalized_controls = {
        "display_signature": _string(controls["display_signature"], "controls.display_signature"),
        "mesa_nevra": _string(controls["mesa_nevra"], "controls.mesa_nevra"),
        "kwin_nevra": _string(controls["kwin_nevra"], "controls.kwin_nevra"),
        "firmware_set_sha256": _artifact_hash(
            controls["firmware_set_sha256"], "controls.firmware_set_sha256"
        ),
        "kernel_config_sha256": _artifact_hash(
            controls["kernel_config_sha256"], "controls.kernel_config_sha256"
        ),
        "workload_id": _string(controls["workload_id"], "controls.workload_id"),
        "observation_minutes": _integer(
            controls["observation_minutes"], "controls.observation_minutes", 1, 1440
        ),
        "matched_runs": _integer(controls["matched_runs"], "controls.matched_runs", 1, 100),
        "rollback_artifact": normalized_rollback,
    }
    acceptance = experiments[variable]["acceptance"]
    if normalized_controls["observation_minutes"] < acceptance["minimum_observation_minutes"]:
        raise PlanError(
            "observation_minutes is below the acceptance profile minimum of "
            f"{acceptance['minimum_observation_minutes']}"
        )
    if normalized_controls["matched_runs"] < acceptance["minimum_matched_runs"]:
        raise PlanError(
            f"matched_runs is below the acceptance profile minimum of {acceptance['minimum_matched_runs']}"
        )
    return {
        "schema_version": 2,
        "experiment_id": experiment_id,
        "source_commit": source_commit,
        "variable": variable,
        "revert_commits": reverts,
        "symmetric_patches": symmetric_patches,
        "acceptance_profile": acceptance_profile,
        "controls": normalized_controls,
    }


def _operations(source: str, symmetric: list[str], reverts: list[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = [{"order": 0, "op": "assert-source", "commit": source}]
    for commit in symmetric:
        result.append({"order": len(result), "op": "apply-symmetric", "commit": commit})
    for commit in reverts:
        result.append({"order": len(result), "op": "revert-variable", "commit": commit})
    return result


def _variant(experiment_id: str, role: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
    recipe_hash = digest({"role": role, "operations": operations})
    return {
        "role": role,
        "variant_id": f"{experiment_id}-{role}-{recipe_hash[:12]}",
        "recipe_sha256": recipe_hash,
        "patch_order": operations,
    }


def compile_plan(manifest: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    policy = validate_policy(policy)
    normalized = normalize_manifest(manifest, policy)
    source = normalized["source_commit"]
    symmetric = normalized["symmetric_patches"]
    control = _variant(normalized["experiment_id"], "control", _operations(source, symmetric, []))
    candidate = _variant(
        normalized["experiment_id"],
        "candidate",
        _operations(source, symmetric, normalized["revert_commits"]),
    )
    acceptance = policy["experiments"][normalized["variable"]]["acceptance"]
    review_patch = policy["experiments"][normalized["variable"]].get("review_patch")
    core = {
        "planner_version": PLANNER_VERSION,
        "schema_version": 2,
        "policy_sha256": digest(policy),
        "manifest_sha256": digest(normalized),
        "experiment": {
            "id": normalized["experiment_id"],
            "variable": normalized["variable"],
            "source_commit": source,
            "source_label": policy["allowed_baselines"][source]["label"],
            "review_patch": review_patch,
        },
        "variants": [control, candidate],
        "controls": normalized["controls"],
        "acceptance": {
            "profile": acceptance["profile"],
            "observation_minutes": normalized["controls"]["observation_minutes"],
            "matched_runs": normalized["controls"]["matched_runs"],
            "recommended_matched_runs": acceptance["recommended_matched_runs"],
            "zero_event_classes": acceptance["zero_event_classes"],
            "non_regression_metrics": acceptance["non_regression_metrics"],
            "required_invariants": acceptance["required_invariants"],
            "rollback_conditions": acceptance["rollback_conditions"],
        },
        "safety": {
            "single_variable": True,
            "symmetric_patch_order_identical": True,
            "requires_clean_exact_source": True,
            "requires_revert_ancestry_verification": True,
            "requires_symmetric_patch_availability_verification": bool(symmetric),
            "requires_review_patch_hash_verification": review_patch is not None,
            "rollback_attestation_structurally_valid": True,
            "requires_rollback_artifact_rehash": True,
            "requires_rollback_report_verification": True,
            "planner_executes_build_or_install": False,
        },
    }
    return {**core, "plan_sha256": digest(core)}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a pinned PocketDS kernel A/B manifest and print a plan."
    )
    parser.add_argument("manifest", type=Path, help="input experiment manifest (JSON)")
    parser.add_argument("--pretty", action="store_true", help="pretty-print validated JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        manifest = load_json(args.manifest)
        policy = load_json(Path(__file__).with_name("policy.json"))
        plan = compile_plan(manifest, policy)
    except PlanError as exc:
        print(f"planner: refused: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(plan, indent=2, ensure_ascii=False, sort_keys=True)
        if args.pretty
        else canonical_bytes(plan).decode("utf-8")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

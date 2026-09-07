#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "kernel-ab"
SOURCE = TOOL / "planner.py"
SPEC = importlib.util.spec_from_file_location("pocketds_kernel_ab_planner", SOURCE)
assert SPEC and SPEC.loader
planner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = planner
SPEC.loader.exec_module(planner)

POLICY = planner.load_json(TOOL / "policy.json")
IFPC = "d84f65cc8cd7af0bf62cca52d13583394851cfe8"
DMABUF = "9f8fbaaeb9e8d6c11bca695de77fd6ee2778fa02"
SHRINKER = "3392291fc509d8ad6e4ad90f15b0a193f721cbc9"


def fixture(name: str) -> dict:
    return planner.load_json(TOOL / "fixtures" / name)


def materialized_fixture(name: str) -> dict:
    """Replace conspicuous invalid sentinels only inside the mock test process."""
    manifest = fixture(name)
    controls = manifest["controls"]
    controls["firmware_set_sha256"] = hashlib.sha256(b"mock-firmware-set").hexdigest()
    controls["kernel_config_sha256"] = hashlib.sha256(b"mock-kernel-config").hexdigest()
    rollback = controls["rollback_artifact"]
    rollback["artifact_id"] = "mock-prevalidated-rollback-artifact"
    rollback["sha256"] = hashlib.sha256(b"mock-rollback-bootimg").hexdigest()
    rollback["size_bytes"] = len(b"mock-rollback-bootimg")
    rollback["validation"]["report_sha256"] = hashlib.sha256(
        b"mock-rollback-validation-report"
    ).hexdigest()
    return manifest


class PlannerTests(unittest.TestCase):
    def test_ifpc_only_changes_one_revert(self) -> None:
        plan = planner.compile_plan(materialized_fixture("ifpc-only.json"), POLICY)
        control, candidate = plan["variants"]
        self.assertEqual([item["op"] for item in control["patch_order"]], ["assert-source"])
        self.assertEqual(
            candidate["patch_order"][-1],
            {"order": 1, "op": "revert-variable", "commit": IFPC},
        )
        self.assertTrue(plan["safety"]["single_variable"])
        self.assertTrue(plan["safety"]["requires_review_patch_hash_verification"])

    def test_policy_review_patch_matches_repository_bytes(self) -> None:
        review = POLICY["experiments"]["a740-ifpc"]["review_patch"]
        patch_path = ROOT / review["path"]
        self.assertTrue(patch_path.is_file())
        self.assertEqual(hashlib.sha256(patch_path.read_bytes()).hexdigest(), review["sha256"])

    def test_dmabuf_only_changes_one_revert(self) -> None:
        plan = planner.compile_plan(materialized_fixture("dmabuf-only.json"), POLICY)
        control, candidate = plan["variants"]
        self.assertEqual(len(control["patch_order"]), 1)
        self.assertEqual(candidate["patch_order"][-1]["commit"], DMABUF)
        self.assertEqual(candidate["patch_order"][-1]["op"], "revert-variable")

    def test_shrinker_patch_is_symmetric_and_before_variable(self) -> None:
        plan = planner.compile_plan(
            materialized_fixture("ifpc-with-symmetric-shrinker.json"), POLICY
        )
        control, candidate = plan["variants"]
        control_common = [item for item in control["patch_order"] if item["op"] == "apply-symmetric"]
        candidate_common = [
            item for item in candidate["patch_order"] if item["op"] == "apply-symmetric"
        ]
        self.assertEqual(control_common, candidate_common)
        self.assertEqual(control_common[0]["commit"], SHRINKER)
        self.assertLess(control_common[0]["order"], candidate["patch_order"][-1]["order"])

    def test_mixed_variables_are_rejected_before_placeholder_check(self) -> None:
        with self.assertRaisesRegex(planner.PlanError, "variable mixing detected"):
            planner.compile_plan(fixture("reject-mixed-variables.json"), POLICY)

    def test_fixture_placeholders_are_refused_by_library(self) -> None:
        with self.assertRaisesRegex(planner.PlanError, "fixture placeholder"):
            planner.compile_plan(fixture("ifpc-only.json"), POLICY)

    def test_placeholder_rollback_is_refused_after_hashes_are_measured(self) -> None:
        manifest = fixture("ifpc-only.json")
        manifest["controls"]["firmware_set_sha256"] = hashlib.sha256(b"measured-fw").hexdigest()
        manifest["controls"]["kernel_config_sha256"] = hashlib.sha256(b"measured-config").hexdigest()
        with self.assertRaisesRegex(
            planner.PlanError, "rollback_artifact.artifact_id is a fixture placeholder"
        ):
            planner.compile_plan(manifest, POLICY)

    def test_rollback_requires_content_hash(self) -> None:
        manifest = materialized_fixture("ifpc-only.json")
        manifest["controls"]["rollback_artifact"]["sha256"] = "pending"
        with self.assertRaisesRegex(planner.PlanError, "full lowercase 64-hex digest"):
            planner.compile_plan(manifest, POLICY)

    def test_rollback_requires_passed_validation(self) -> None:
        manifest = materialized_fixture("ifpc-only.json")
        manifest["controls"]["rollback_artifact"]["validation"]["status"] = "pending"
        with self.assertRaisesRegex(planner.PlanError, "status must be passed"):
            planner.compile_plan(manifest, POLICY)

    def test_rollback_requires_a_successful_recovery_test(self) -> None:
        manifest = materialized_fixture("ifpc-only.json")
        validation = manifest["controls"]["rollback_artifact"]["validation"]
        validation["successful_recovery_tests"] = 0
        with self.assertRaisesRegex(planner.PlanError, "must be between 1 and 100"):
            planner.compile_plan(manifest, POLICY)

    def test_rollback_requires_independent_recovery_path(self) -> None:
        manifest = materialized_fixture("ifpc-only.json")
        validation = manifest["controls"]["rollback_artifact"]["validation"]
        validation["independent_recovery_path"] = False
        with self.assertRaisesRegex(planner.PlanError, "must be true"):
            planner.compile_plan(manifest, POLICY)

    def test_rollback_is_bound_to_pinned_device_and_release(self) -> None:
        manifest = materialized_fixture("ifpc-only.json")
        manifest["controls"]["rollback_artifact"]["device_id"] = "another-device"
        with self.assertRaisesRegex(planner.PlanError, "does not match the pinned baseline"):
            planner.compile_plan(manifest, POLICY)

    def test_fixture_placeholders_are_refused_by_cli_without_stdout(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SOURCE), str(TOOL / "fixtures" / "ifpc-only.json")],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("fixture placeholder", result.stderr)

    def test_unknown_field_is_rejected(self) -> None:
        manifest = materialized_fixture("ifpc-only.json")
        manifest["helpful_extra"] = True
        with self.assertRaisesRegex(planner.PlanError, "unknown keys"):
            planner.compile_plan(manifest, POLICY)

    def test_abbreviated_revert_is_rejected(self) -> None:
        manifest = materialized_fixture("ifpc-only.json")
        manifest["revert_commits"] = [IFPC[:12]]
        with self.assertRaises(planner.PlanError):
            planner.compile_plan(manifest, POLICY)

    def test_unpinned_symmetric_patch_is_rejected(self) -> None:
        manifest = materialized_fixture("ifpc-only.json")
        manifest["symmetric_patches"] = ["a" * 40]
        with self.assertRaisesRegex(planner.PlanError, "not pinned by policy"):
            planner.compile_plan(manifest, POLICY)

    def test_observation_floor_is_enforced(self) -> None:
        manifest = materialized_fixture("ifpc-only.json")
        manifest["controls"]["observation_minutes"] = 44
        with self.assertRaisesRegex(planner.PlanError, "below the acceptance profile minimum"):
            planner.compile_plan(manifest, POLICY)

    def test_matched_run_floor_is_enforced(self) -> None:
        manifest = materialized_fixture("ifpc-only.json")
        manifest["controls"]["matched_runs"] = 2
        with self.assertRaisesRegex(planner.PlanError, "minimum of 3"):
            planner.compile_plan(manifest, POLICY)

    def test_output_is_deterministic(self) -> None:
        manifest = materialized_fixture("ifpc-only.json")
        first = planner.compile_plan(copy.deepcopy(manifest), POLICY)
        second = planner.compile_plan(copy.deepcopy(manifest), POLICY)
        self.assertEqual(first, second)
        self.assertEqual(len(first["plan_sha256"]), 64)
        self.assertNotEqual(first["variants"][0]["variant_id"], first["variants"][1]["variant_id"])

    def test_cli_has_no_build_install_or_policy_override_switch(self) -> None:
        help_result = subprocess.run(
            [sys.executable, str(SOURCE), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertNotIn("--policy", help_result.stdout)
        self.assertNotIn("--build", help_result.stdout)
        self.assertNotIn("--install", help_result.stdout)
        source_text = SOURCE.read_text(encoding="utf-8")
        for forbidden in ("subprocess", "os.system", "git ", "make ", "dnf ", "rpm "):
            self.assertNotIn(forbidden, source_text)

    def test_compiled_output_is_one_json_document(self) -> None:
        plan = planner.compile_plan(materialized_fixture("ifpc-only.json"), POLICY)
        encoded = planner.canonical_bytes(plan).decode("utf-8")
        parsed = json.loads(encoded)
        self.assertEqual(parsed["experiment"]["variable"], "a740-ifpc")
        self.assertEqual(parsed["schema_version"], 2)
        self.assertTrue(parsed["safety"]["rollback_attestation_structurally_valid"])
        self.assertTrue(parsed["safety"]["requires_rollback_artifact_rehash"])
        self.assertFalse(parsed["safety"]["planner_executes_build_or_install"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Tests for the complete PDS-005 UI update transaction and older recovery."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pds005-ui-update-transaction.py"
SPEC = importlib.util.spec_from_file_location("pds005_ui_transaction", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.repo = root / "repo"
        self.home = root / "home"
        self.system = root / "system"
        self.uid = os.getuid()
        self.artifacts = MODULE.artifact_plan(
            self.repo,
            self.home,
            self.system,
            user_uid=self.uid,
            root_uid=self.uid,
        )
        for index, artifact in enumerate(self.artifacts):
            write(artifact.source, f"new-{index}\n".encode(), 0o755 if artifact.target_mode == 0o755 else 0o644)
            artifact.target.parent.mkdir(parents=True, exist_ok=True)
            if artifact.artifact_id not in (
                "deep-suspend-guard",
                "deep-suspend-polkit",
                "keyboard-geometry",
                "keyboard-voice-artifacts",
            ):
                old_mode = 0o755 if artifact.artifact_id == "keyboard-adapter" else artifact.target_mode
                write(artifact.target, f"old-{index}\n".encode(), old_mode)
        self.transaction = root / "transaction"

    @staticmethod
    def replace(source: Path, target: Path, mode: int, token: str) -> None:
        MODULE.atomic_user_replace(source, target, mode, token)

    @staticmethod
    def remove(target: Path, token: str) -> None:
        MODULE.atomic_user_remove(target, token)


class PlanAndStageTests(unittest.TestCase):
    def test_plan_is_exact_private_and_reports_drift_missing_and_mode(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            # Exercise MODE_DRIFT independently of the other deliberately stale files.
            adapter = next(item for item in fixture.artifacts if item.artifact_id == "keyboard-adapter")
            adapter.target.write_bytes(adapter.source.read_bytes())
            adapter.target.chmod(0o755)
            report = MODULE.deployment_plan(fixture.artifacts)
        states = {item["artifact_id"]: item["state"] for item in report["artifacts"]}
        self.assertEqual(report["artifact_count"], 10)
        self.assertEqual(states["panel-qml"], "DRIFT")
        self.assertEqual(states["keyboard-adapter"], "MODE_DRIFT")
        self.assertEqual(states["keyboard-geometry"], "MISSING")
        self.assertTrue(report["activation_deferred"])
        serialized = json.dumps(report)
        self.assertNotIn(temporary, serialized)

    def test_stage_is_private_hash_bound_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            report = MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "a" * 40)
            manifest = json.loads((fixture.transaction / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(fixture.transaction.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((fixture.transaction / "manifest.json").stat().st_mode), 0o600)
            for artifact in fixture.artifacts:
                self.assertEqual(
                    stat.S_IMODE((fixture.transaction / "payload" / artifact.artifact_id).stat().st_mode),
                    0o600,
                )
            self.assertEqual(report["artifact_count"], 10)
            self.assertFalse(manifest["services_restarted"])
            self.assertNotIn(temporary, json.dumps(manifest))
            with self.assertRaises(MODULE.TransactionError):
                MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "a" * 40)

    def test_stage_rejects_unsafe_source_and_required_missing_root_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            source = fixture.artifacts[0].source
            real = source.with_suffix(".real")
            source.rename(real)
            source.symlink_to(real)
            with self.assertRaises(MODULE.TransactionError):
                MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "b" * 40)
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.artifacts[0].source.chmod(0o600)
            with self.assertRaises(MODULE.TransactionError):
                MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "b" * 40)
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            helper = next(
                item for item in fixture.artifacts if item.artifact_id == "panel-root-helper"
            )
            helper.target.unlink()
            with self.assertRaises(MODULE.TransactionError):
                MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "b" * 40)

    def test_concurrent_transaction_operation_is_rejected_before_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            with MODULE._transaction_lock(fixture.transaction.parent):
                with self.assertRaises(MODULE.TransactionError):
                    MODULE.stage_transaction(
                        fixture.artifacts, fixture.transaction, "7" * 40
                    )
            self.assertFalse(fixture.transaction.exists())
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "7" * 40)
            with MODULE._transaction_lock(fixture.transaction.parent):
                for operation in (
                    lambda: MODULE.apply_transaction(
                        fixture.artifacts,
                        fixture.transaction,
                        privileged_replace=fixture.replace,
                    ),
                    lambda: MODULE.verify_transaction(
                        fixture.artifacts, fixture.transaction
                    ),
                    lambda: MODULE.rollback_transaction(
                        fixture.artifacts,
                        fixture.transaction,
                        privileged_replace=fixture.replace,
                    ),
                ):
                    with self.assertRaises(MODULE.TransactionError):
                        operation()
            self.assertFalse((fixture.transaction / "applied.json").exists())


class ApplyAndRollbackTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "fork"), "power-loss fixture needs fork")
    def test_applied_marker_power_loss_never_publishes_partial_final(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            before = {
                item.artifact_id: (
                    item.target.read_bytes() if item.target.exists() else None,
                    stat.S_IMODE(item.target.stat().st_mode)
                    if item.target.exists()
                    else None,
                )
                for item in fixture.artifacts
            }
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "6" * 40)
            child = os.fork()
            if child == 0:
                original = MODULE._rename_noreplace

                def crash_before_publish(source: Path, destination: Path) -> None:
                    if destination.name == "applied.json":
                        if not source.name.startswith(".applied.json.staging-"):
                            os._exit(96)
                        os._exit(88)
                    original(source, destination)

                MODULE._rename_noreplace = crash_before_publish
                MODULE.apply_transaction(
                    fixture.artifacts,
                    fixture.transaction,
                    privileged_replace=fixture.replace,
                )
                os._exit(97)
            _pid, status = os.waitpid(child, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 88)
            self.assertFalse((fixture.transaction / "applied.json").exists())
            for item in fixture.artifacts:
                self.assertEqual(item.target.read_bytes(), item.source.read_bytes())

            MODULE.rollback_transaction(
                fixture.artifacts,
                fixture.transaction,
                privileged_replace=fixture.replace,
                privileged_remove=fixture.remove,
            )
            for item in fixture.artifacts:
                content, mode = before[item.artifact_id]
                if content is None:
                    self.assertFalse(item.target.exists())
                else:
                    self.assertEqual(item.target.read_bytes(), content)
                    self.assertEqual(stat.S_IMODE(item.target.stat().st_mode), mode)

    def test_fixture_apply_verify_and_rollback_restore_exact_preimages(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            before = {
                item.artifact_id: (
                    item.target.read_bytes() if item.target.exists() else None,
                    stat.S_IMODE(item.target.stat().st_mode) if item.target.exists() else None,
                )
                for item in fixture.artifacts
            }
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "c" * 40)
            applied = MODULE.apply_transaction(
                fixture.artifacts,
                fixture.transaction,
                privileged_replace=fixture.replace,
                privileged_remove=fixture.remove,
            )
            self.assertTrue(applied["all_targets_verified"])
            self.assertTrue(MODULE.verify_transaction(fixture.artifacts, fixture.transaction)["complete"])
            rolled = MODULE.rollback_transaction(
                fixture.artifacts,
                fixture.transaction,
                privileged_replace=fixture.replace,
                privileged_remove=fixture.remove,
            )
            self.assertTrue(rolled["all_preimages_verified"])
            for item in fixture.artifacts:
                content, mode = before[item.artifact_id]
                if content is None:
                    self.assertFalse(item.target.exists())
                else:
                    self.assertEqual(item.target.read_bytes(), content)
                    self.assertEqual(stat.S_IMODE(item.target.stat().st_mode), mode)

    def test_preimage_or_payload_drift_refuses_apply_without_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "d" * 40)
            first = fixture.artifacts[0]
            first.target.write_bytes(b"changed-after-stage\n")
            with self.assertRaises(MODULE.TransactionError):
                MODULE.apply_transaction(
                    fixture.artifacts,
                    fixture.transaction,
                    privileged_replace=fixture.replace,
                )
            self.assertEqual(first.target.read_bytes(), b"changed-after-stage\n")
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "e" * 40)
            payload = fixture.transaction / "payload" / fixture.artifacts[0].artifact_id
            payload.write_bytes(b"tampered\n")
            payload.chmod(0o600)
            with self.assertRaises(MODULE.TransactionError):
                MODULE.apply_transaction(
                    fixture.artifacts,
                    fixture.transaction,
                    privileged_replace=fixture.replace,
                )

    def test_verify_requires_applied_marker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "9" * 40)
            with self.assertRaises(MODULE.TransactionError):
                MODULE.verify_transaction(fixture.artifacts, fixture.transaction)

    def test_rollback_refuses_unknown_post_apply_change(self) -> None:
        for unmarked in (False, True):
            with self.subTest(unmarked=unmarked), tempfile.TemporaryDirectory(
                prefix="pds005-ui-tx-"
            ) as temporary:
                fixture = Fixture(Path(temporary))
                MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "f" * 40)
                MODULE.apply_transaction(
                    fixture.artifacts,
                    fixture.transaction,
                    privileged_replace=fixture.replace,
                )
                if unmarked:
                    (fixture.transaction / "applied.json").unlink()
                fixture.artifacts[0].target.write_bytes(b"user-edited\n")
                with self.assertRaises(MODULE.TransactionError):
                    MODULE.rollback_transaction(
                        fixture.artifacts,
                        fixture.transaction,
                        privileged_replace=fixture.replace,
                    )

    def test_unmarked_recovery_rejects_malformed_or_linked_marker(self) -> None:
        for marker_kind in ("malformed", "linked"):
            with self.subTest(marker_kind=marker_kind), tempfile.TemporaryDirectory(
                prefix="pds005-ui-tx-"
            ) as temporary:
                fixture = Fixture(Path(temporary))
                MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "8" * 40)
                marker = fixture.transaction / "applied.json"
                if marker_kind == "malformed":
                    write(marker, b"{}\n", 0o600)
                else:
                    marker.symlink_to(fixture.transaction / "manifest.json")
                with self.assertRaises(MODULE.TransactionError):
                    MODULE.rollback_transaction(
                        fixture.artifacts,
                        fixture.transaction,
                        privileged_replace=fixture.replace,
                    )

    def test_post_rename_failure_automatically_restores_all_changed_targets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            before = {
                item.artifact_id: (
                    item.target.read_bytes() if item.target.exists() else None,
                    stat.S_IMODE(item.target.stat().st_mode) if item.target.exists() else None,
                )
                for item in fixture.artifacts
            }
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "1" * 40)

            failed = False

            def mutate_then_fail(source: Path, target: Path, mode: int, token: str) -> None:
                nonlocal failed
                MODULE.atomic_user_replace(source, target, mode, token)
                if not failed:
                    failed = True
                    raise OSError("synthetic post-rename failure")

            with self.assertRaises(MODULE.TransactionError):
                MODULE.apply_transaction(
                    fixture.artifacts,
                    fixture.transaction,
                    privileged_replace=mutate_then_fail,
                    privileged_remove=fixture.remove,
                )
            for item in fixture.artifacts:
                content, mode = before[item.artifact_id]
                if content is None:
                    self.assertFalse(item.target.exists())
                else:
                    self.assertEqual(item.target.read_bytes(), content)
                    self.assertEqual(stat.S_IMODE(item.target.stat().st_mode), mode)

    def test_automatic_rollback_continues_after_one_restore_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            before = {
                item.artifact_id: (
                    item.target.read_bytes() if item.target.exists() else None,
                    stat.S_IMODE(item.target.stat().st_mode) if item.target.exists() else None,
                )
                for item in fixture.artifacts
            }
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "3" * 40)
            real_replace = MODULE.atomic_user_replace

            def fail_apply_and_one_restore(
                source: Path, target: Path, mode: int, token: str
            ) -> None:
                if token == "pds005-keyboard-adapter":
                    raise OSError("synthetic apply failure")
                if token == "pds005-auto-keyboard-main":
                    raise OSError("synthetic restore failure")
                real_replace(source, target, mode, token)

            MODULE.atomic_user_replace = fail_apply_and_one_restore
            try:
                with self.assertRaises(MODULE.TransactionError):
                    MODULE.apply_transaction(
                        fixture.artifacts,
                        fixture.transaction,
                        privileged_replace=fixture.replace,
                    )
            finally:
                MODULE.atomic_user_replace = real_replace

            panel = next(item for item in fixture.artifacts if item.artifact_id == "panel-qml")
            keyboard = next(
                item for item in fixture.artifacts if item.artifact_id == "keyboard-main"
            )
            # The earlier implementation stopped at keyboard-main and stranded
            # panel-qml too.  The independent Panel restoration must still run.
            self.assertEqual(panel.target.read_bytes(), before[panel.artifact_id][0])
            self.assertEqual(
                keyboard.target.read_bytes(),
                (fixture.transaction / "payload" / keyboard.artifact_id).read_bytes(),
            )
            rolled = MODULE.rollback_transaction(
                fixture.artifacts,
                fixture.transaction,
                privileged_replace=fixture.replace,
                privileged_remove=fixture.remove,
            )
            self.assertTrue(rolled["all_preimages_verified"])
            for item in fixture.artifacts:
                content, mode = before[item.artifact_id]
                if content is None:
                    self.assertFalse(item.target.exists())
                else:
                    self.assertEqual(item.target.read_bytes(), content)
                    self.assertEqual(stat.S_IMODE(item.target.stat().st_mode), mode)

    def test_applied_marker_failure_rolls_back_every_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            before = {
                item.artifact_id: (
                    item.target.read_bytes() if item.target.exists() else None,
                    stat.S_IMODE(item.target.stat().st_mode) if item.target.exists() else None,
                )
                for item in fixture.artifacts
            }
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "4" * 40)
            real_write_marker = MODULE._write_marker

            def fail_marker(
                transaction: Path, name: str, revision: str, manifest_hash: str
            ) -> None:
                raise OSError("synthetic marker write failure")

            MODULE._write_marker = fail_marker
            try:
                with self.assertRaises(MODULE.TransactionError):
                    MODULE.apply_transaction(
                        fixture.artifacts,
                        fixture.transaction,
                        privileged_replace=fixture.replace,
                        privileged_remove=fixture.remove,
                    )
            finally:
                MODULE._write_marker = real_write_marker

            self.assertFalse((fixture.transaction / "applied.json").exists())
            for item in fixture.artifacts:
                content, mode = before[item.artifact_id]
                if content is None:
                    self.assertFalse(item.target.exists())
                else:
                    self.assertEqual(item.target.read_bytes(), content)
                    self.assertEqual(stat.S_IMODE(item.target.stat().st_mode), mode)

    def test_partial_explicit_rollback_is_safe_to_retry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            before = {
                item.artifact_id: (
                    item.target.read_bytes() if item.target.exists() else None,
                    stat.S_IMODE(item.target.stat().st_mode) if item.target.exists() else None,
                )
                for item in fixture.artifacts
            }
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "2" * 40)
            MODULE.apply_transaction(
                fixture.artifacts,
                fixture.transaction,
                privileged_replace=fixture.replace,
                privileged_remove=fixture.remove,
            )

            failed = False

            def restore_then_fail(source: Path, target: Path, mode: int, token: str) -> None:
                nonlocal failed
                MODULE.atomic_user_replace(source, target, mode, token)
                if token.startswith("pds005-rollback-") and not failed:
                    failed = True
                    raise OSError("synthetic post-rename rollback failure")

            with self.assertRaises(OSError):
                MODULE.rollback_transaction(
                    fixture.artifacts,
                    fixture.transaction,
                    privileged_replace=restore_then_fail,
                )
            # The privileged helper crossed its rename boundary, while user
            # targets are still payload.  A second invocation must finish the
            # exact known-state rollback rather than reject its own progress.
            privileged = next(
                item for item in fixture.artifacts if item.artifact_id == "panel-root-helper"
            )
            self.assertEqual(privileged.target.read_bytes(), before[privileged.artifact_id][0])
            rolled = MODULE.rollback_transaction(
                fixture.artifacts,
                fixture.transaction,
                privileged_replace=fixture.replace,
                privileged_remove=fixture.remove,
            )
            self.assertTrue(rolled["all_preimages_verified"])
            for item in fixture.artifacts:
                content, mode = before[item.artifact_id]
                if content is None:
                    self.assertFalse(item.target.exists())
                else:
                    self.assertEqual(item.target.read_bytes(), content)
                    self.assertEqual(stat.S_IMODE(item.target.stat().st_mode), mode)

    def test_rollback_accepts_an_artifact_identical_before_and_after(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            unchanged = fixture.artifacts[0]
            unchanged.target.write_bytes(unchanged.source.read_bytes())
            unchanged.target.chmod(unchanged.target_mode)
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "5" * 40)
            MODULE.apply_transaction(
                fixture.artifacts,
                fixture.transaction,
                privileged_replace=fixture.replace,
                privileged_remove=fixture.remove,
            )
            rolled = MODULE.rollback_transaction(
                fixture.artifacts,
                fixture.transaction,
                privileged_replace=fixture.replace,
                privileged_remove=fixture.remove,
            )
            self.assertTrue(rolled["all_preimages_verified"])

    def test_privileged_apply_order_and_first_install_rollback_to_absent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-tx-") as temporary:
            fixture = Fixture(Path(temporary))
            expected_ids = [
                "deep-suspend-guard",
                "deep-suspend-polkit",
                "panel-root-helper",
            ]
            privileged = [item for item in fixture.artifacts if item.privileged]
            self.assertEqual([item.artifact_id for item in privileged], expected_ids)
            self.assertFalse(privileged[0].target.exists())
            self.assertFalse(privileged[1].target.exists())

            apply_order: list[str] = []

            def ordered_replace(
                source: Path, target: Path, mode: int, token: str
            ) -> None:
                apply_order.append(target.name)
                fixture.replace(source, target, mode, token)

            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "0" * 40)
            MODULE.apply_transaction(
                fixture.artifacts,
                fixture.transaction,
                privileged_replace=ordered_replace,
                privileged_remove=fixture.remove,
            )
            self.assertEqual(
                apply_order,
                [
                    "pocketds-deep-suspend",
                    "90-pocketds-deep-suspend.rules",
                    "pocketds-panel-root",
                ],
            )
            MODULE.rollback_transaction(
                fixture.artifacts,
                fixture.transaction,
                privileged_replace=fixture.replace,
                privileged_remove=fixture.remove,
            )
            self.assertFalse(privileged[0].target.exists())
            self.assertFalse(privileged[1].target.exists())


class ComponentAndLegacyTests(unittest.TestCase):
    def test_new_qml_components_are_published_before_main_and_removed_on_rollback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-components-") as temporary:
            fixture = Fixture(Path(temporary))
            self.assertEqual(
                [item.artifact_id for item in fixture.artifacts[:3]],
                ["panel-controller-diagram", "panel-controller-session", "panel-qml"],
            )
            for component in fixture.artifacts[:2]:
                component.target.unlink()
            main = fixture.artifacts[2]
            before = main.target.read_bytes()
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "a" * 40)
            MODULE.apply_transaction(
                fixture.artifacts, fixture.transaction,
                privileged_replace=fixture.replace, privileged_remove=fixture.remove,
            )
            for component in fixture.artifacts[:3]:
                self.assertEqual(component.target.read_bytes(), component.source.read_bytes())
            MODULE.rollback_transaction(
                fixture.artifacts, fixture.transaction,
                privileged_replace=fixture.replace, privileged_remove=fixture.remove,
            )
            self.assertEqual(main.target.read_bytes(), before)
            self.assertTrue(all(not item.target.exists() for item in fixture.artifacts[:2]))

    def test_exact_legacy_bundle_can_verify_and_rollback_but_not_apply_as_current(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-legacy-") as temporary:
            fixture = Fixture(Path(temporary))
            legacy = fixture.artifacts[2:]
            main = legacy[0]
            before = main.target.read_bytes()
            MODULE.stage_transaction(legacy, fixture.transaction, "b" * 40)
            with self.assertRaises(MODULE.TransactionError):
                MODULE.apply_transaction(
                    fixture.artifacts, fixture.transaction,
                    privileged_replace=fixture.replace, privileged_remove=fixture.remove,
                )
            MODULE.apply_transaction(
                legacy, fixture.transaction,
                privileged_replace=fixture.replace, privileged_remove=fixture.remove,
            )
            selected = MODULE.recovery_artifact_plan(fixture.artifacts, fixture.transaction)
            self.assertEqual(selected, legacy)
            self.assertTrue(MODULE.verify_transaction(selected, fixture.transaction)["complete"])
            MODULE.rollback_transaction(
                selected, fixture.transaction,
                privileged_replace=fixture.replace, privileged_remove=fixture.remove,
            )
            self.assertEqual(main.target.read_bytes(), before)

    def test_recovery_rejects_arbitrary_eight_artifact_subsets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-subset-") as temporary:
            fixture = Fixture(Path(temporary))
            MODULE.stage_transaction(fixture.artifacts[:8], fixture.transaction, "c" * 40)
            with self.assertRaises(MODULE.TransactionError):
                MODULE.recovery_artifact_plan(fixture.artifacts, fixture.transaction)


class StaticContractTests(unittest.TestCase):
    def test_cli_is_ten_file_only_confirmation_gated_and_never_activates(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn(MODULE.STAGE_CONFIRMATION, source)
        self.assertIn(MODULE.APPLY_CONFIRMATION, source)
        self.assertIn(MODULE.ROLLBACK_CONFIRMATION, source)
        self.assertEqual(source.count('Artifact(\n            "'), 10)
        for forbidden in (
            "systemctl",
            "plasmashell",
            "kwin_wayland",
            "reboot",
            "poweroff",
            "install.sh",
            "c++",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('"activation_deferred": True', source)
        self.assertIn('"services_restarted": False', source)


class ControllerServiceUpdateTests(unittest.TestCase):
    def exercise(self, *, listener_active=True, failed_body=False, failed_start=False,
                 stopped=False, stop_failure=False, failed_rollback=False):
        spec = importlib.util.spec_from_file_location(
            "controller_update_services", ROOT / "scripts/controller_update_services.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        test = "pocketds-controller-test.service"
        listener = "pocketds-mode-listener.service"
        states = {test: "inactive" if stopped else "active",
                  listener: "active" if listener_active and not stopped else "inactive"}
        calls = []
        from types import SimpleNamespace

        def run(argv, **kwargs):
            unit = argv[-1]
            if argv[1] == "show":
                unit = argv[2]
                active = states[unit]
                return SimpleNamespace(returncode=0, stdout=
                    f"LoadState=loaded\nActiveState={active}\nSubState={'running' if active == 'active' else 'dead'}")
            verb = argv[-2]
            calls.append((verb, unit))
            if verb == "stop":
                states[test] = states[listener] = "inactive"
                if stop_failure:
                    return SimpleNamespace(returncode=1, stdout="")
            elif verb == "start":
                if failed_start and unit == listener:
                    return SimpleNamespace(returncode=1, stdout="")
                states[unit] = "active"
            else:
                raise AssertionError(argv)
            return SimpleNamespace(returncode=0, stdout="")

        error = None
        body_entered = False
        try:
            with helper.paused_controller_services(run=run) as checkpoint:
                body_entered = True
                self.assertEqual(set(states.values()), {"inactive"})
                if failed_rollback:
                    raise ValueError("mock file rollback failed")
                checkpoint.files_verified()
                if failed_body:
                    raise ValueError("mock file rollback completed")
        except (ValueError, helper.ServiceUpdateError) as exc:
            error = exc
        return states, calls, error, body_entered

    def test_success_restores_controller_and_listener(self):
        states, calls, error, entered = self.exercise()
        self.assertIsNone(error)
        self.assertTrue(entered)
        self.assertEqual(set(states.values()), {"active"})
        self.assertEqual([verb for verb, _ in calls], ["stop", "start", "start"])

    def test_failed_file_update_restores_both_after_rollback(self):
        states, calls, error, entered = self.exercise(failed_body=True)
        self.assertIsInstance(error, ValueError)
        self.assertEqual(set(states.values()), {"active"})

    def test_failed_file_rollback_keeps_both_services_stopped(self):
        states, calls, error, entered = self.exercise(failed_rollback=True)
        self.assertTrue(entered)
        self.assertIn("files are not verified", str(error))
        self.assertEqual(set(states.values()), {"inactive"})
        self.assertEqual([verb for verb, _ in calls], ["stop"])

    def test_previously_stopped_listener_is_not_started(self):
        states, calls, error, entered = self.exercise(listener_active=False)
        self.assertIsNone(error)
        self.assertEqual(states["pocketds-mode-listener.service"], "inactive")
        self.assertEqual([verb for verb, _ in calls], ["stop", "start"])

    def test_previously_stopped_pair_is_not_started(self):
        states, calls, error, entered = self.exercise(stopped=True)
        self.assertIsNone(error)
        self.assertEqual(set(states.values()), {"inactive"})
        self.assertEqual([verb for verb, _ in calls], ["stop"])

    def test_partial_stop_failure_restores_without_entering_update(self):
        states, calls, error, entered = self.exercise(stop_failure=True)
        self.assertIsNotNone(error)
        self.assertFalse(entered)
        self.assertEqual(set(states.values()), {"active"})

    def test_listener_restart_failure_cannot_report_success(self):
        states, calls, error, entered = self.exercise(failed_start=True)
        self.assertIsNotNone(error)
        self.assertIn("service command failed", str(error))
        self.assertEqual(states["pocketds-mode-listener.service"], "inactive")


if __name__ == "__main__":
    unittest.main(verbosity=2)

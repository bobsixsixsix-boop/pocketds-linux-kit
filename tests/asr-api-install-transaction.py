#!/usr/bin/env python3
"""Offline policy and rollback tests for the focused asr_api ASR installer."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/install-asr-api.sh"
LOADER = importlib.machinery.SourceFileLoader("install_asr_api_asr", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
LOADER.exec_module(MODULE)


def write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)


class FakeUnit:
    def __init__(
        self,
        *,
        enabled: str = "enabled",
        active: str = "active",
        fail_action: str | None = None,
        raise_action: str | None = None,
        unit_path: Path | None = None,
    ) -> None:
        self.enabled = enabled
        self.active = active
        self.fail_action = fail_action
        self.raise_action = raise_action
        self.unit_path = unit_path
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, arguments: tuple[str, ...]) -> MODULE.CommandResult:
        self.calls.append(arguments)
        action = arguments[0]
        if action == self.raise_action:
            raise MODULE.InstallError("deployment interrupted by signal 15")
        if action == self.fail_action:
            return MODULE.CommandResult(1)
        if action == "is-enabled":
            return MODULE.CommandResult(0 if self.enabled == "enabled" else 1, self.enabled)
        if action == "is-active":
            return MODULE.CommandResult(0 if self.active == "active" else 3, self.active)
        if action == "restart" or action == "start":
            self.active = "active"
        elif action == "stop":
            self.active = "inactive"
        elif action == "reset-failed" and self.active == "failed":
            self.active = "inactive"
        elif action == "daemon-reload" and self.unit_path is not None:
            self.enabled = "disabled" if self.unit_path.is_file() else "not-found"
        return MODULE.CommandResult(0)


class Fixture:
    def __init__(
        self,
        root: Path,
        *,
        provisioner_missing: bool = False,
        unit_missing: bool = False,
    ) -> None:
        self.repo = root / "repo"
        self.home = root / "home"
        self.transaction = root / "transactions" / "fixture"
        self.transaction.parent.mkdir(parents=True, mode=0o700)
        self.transaction.parent.chmod(0o700)
        self.artifacts = MODULE.artifact_plan(self.repo, self.home)
        self.before: dict[str, tuple[bytes | None, int | None]] = {}
        for index, artifact in enumerate(self.artifacts):
            source = f"new-{artifact.artifact_id}-{index}\n".encode()
            write(artifact.source, source, artifact.target_mode)
            missing = (
                provisioner_missing and artifact.artifact_id == "asr-api-provisioner"
            ) or (unit_missing and artifact.artifact_id == "keyboard-unit")
            if missing:
                artifact.target.parent.mkdir(parents=True, exist_ok=True)
                self.before[artifact.artifact_id] = (None, None)
            else:
                mode = 0o600 if artifact.artifact_id == "keyboard-unit" else artifact.target_mode
                content = f"old-{artifact.artifact_id}-{index}\n".encode()
                write(artifact.target, content, mode)
                self.before[artifact.artifact_id] = (content, mode)

    def assert_preimages(self, testcase: unittest.TestCase) -> None:
        for artifact in self.artifacts:
            content, mode = self.before[artifact.artifact_id]
            if content is None:
                testcase.assertFalse(artifact.target.exists())
            else:
                testcase.assertEqual(artifact.target.read_bytes(), content)
                testcase.assertEqual(stat.S_IMODE(artifact.target.stat().st_mode), mode)


class FocusedTransactionTests(unittest.TestCase):
    def test_transitional_unit_states_remain_rejected(self) -> None:
        for state in ("activating", "deactivating", "reloading", "maintenance", "unknown"):
            with self.subTest(state=state), self.assertRaises(MODULE.InstallError):
                MODULE.capture_unit_state(unit_command=FakeUnit(active=state))

    def test_failed_unit_is_repaired_and_rollback_keeps_old_generation_stopped(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-repair-") as temporary:
            fixture = Fixture(Path(temporary))
            unit = FakeUnit(active="failed")
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "a" * 40,
                                     unit_command=unit)
            self.assertEqual(MODULE._load_state(fixture.transaction)["active"], "failed")
            self.assertNotIn(("reset-failed", MODULE.UNIT), unit.calls)
            MODULE.apply_transaction(fixture.artifacts, fixture.transaction,
                                     activate=True, unit_command=unit)
            self.assertTrue(MODULE.verify_transaction(
                fixture.artifacts, fixture.transaction, unit_command=unit)["complete"])
            self.assertLess(unit.calls.index(("reset-failed", MODULE.UNIT)),
                            unit.calls.index(("restart", MODULE.UNIT)))
            rolled = MODULE.rollback_transaction(fixture.artifacts, fixture.transaction,
                                                  unit_command=unit)
            fixture.assert_preimages(self)
            self.assertEqual(unit.active, "inactive")
            self.assertTrue(rolled["prior_failed_unit_left_stopped"])
            self.assertEqual(unit.calls.count(("restart", MODULE.UNIT)), 1)

    def test_failed_repair_activation_error_restores_files_without_starting_old_code(self) -> None:
        class FailedStartUnit(FakeUnit):
            def __call__(self, arguments):
                result = super().__call__(arguments)
                if arguments[0] == "restart" and result.returncode != 0:
                    self.active = "failed"
                return result

        with tempfile.TemporaryDirectory(prefix="pds-asr_api-repair-") as temporary:
            fixture = Fixture(Path(temporary))
            unit = FailedStartUnit(active="failed", fail_action="restart")
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "a" * 40,
                                     unit_command=unit)
            with self.assertRaises(MODULE.InstallError):
                MODULE.apply_transaction(fixture.artifacts, fixture.transaction,
                                         activate=True, unit_command=unit)
            fixture.assert_preimages(self)
            self.assertEqual(unit.active, "inactive")
            self.assertEqual(unit.calls.count(("restart", MODULE.UNIT)), 1)
            self.assertTrue(MODULE._load_activation_rollback(fixture.transaction))

    def test_interrupted_failed_repair_after_reset_can_roll_back(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-repair-") as temporary:
            fixture = Fixture(Path(temporary))
            unit = FakeUnit(active="failed")
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "a" * 40,
                                     unit_command=unit)
            MODULE._write_activation_intent(fixture.transaction, requested=True)
            MODULE._shared_transaction().apply_transaction(fixture.artifacts, fixture.transaction)
            unit(("reset-failed", MODULE.UNIT))
            rolled = MODULE.rollback_transaction(fixture.artifacts, fixture.transaction,
                                                  unit_command=unit)
            fixture.assert_preimages(self)
            self.assertEqual(unit.active, "inactive")
            self.assertTrue(rolled["prior_failed_unit_left_stopped"])
            self.assertNotIn(("restart", MODULE.UNIT), unit.calls)

    def test_failed_unit_is_untouched_without_activation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-repair-") as temporary:
            fixture = Fixture(Path(temporary))
            unit = FakeUnit(active="failed")
            MODULE.stage_transaction(fixture.artifacts, fixture.transaction, "a" * 40,
                                     unit_command=unit)
            MODULE.apply_transaction(fixture.artifacts, fixture.transaction,
                                     activate=False, unit_command=unit)
            MODULE.rollback_transaction(fixture.artifacts, fixture.transaction,
                                        unit_command=unit)
            fixture.assert_preimages(self)
            self.assertEqual(unit.active, "failed")
            self.assertTrue(all(call[0] in ("is-enabled", "is-active") for call in unit.calls))

    def test_systemctl_environment_binds_owned_private_runtime_and_bus(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-user-bus-") as temporary:
            runtime_root = Path(temporary)
            runtime = runtime_root / str(os.getuid())
            runtime.mkdir(mode=0o700)
            runtime.chmod(0o700)
            bus = runtime / "bus"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(bus))
                environment = MODULE._user_manager_environment(
                    runtime_root=runtime_root
                )
            finally:
                listener.close()
            self.assertEqual(environment["XDG_RUNTIME_DIR"], str(runtime))
            self.assertEqual(
                environment["DBUS_SESSION_BUS_ADDRESS"], f"unix:path={bus}"
            )
            self.assertEqual(
                set(environment),
                {"PATH", "LC_ALL", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"},
            )

            runtime.chmod(0o755)
            with self.assertRaisesRegex(MODULE.InstallError, "runtime directory"):
                MODULE._user_manager_environment(runtime_root=runtime_root)

    def test_wrapper_lock_and_state_root_fail_closed_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-install-lock-") as temporary:
            root = Path(temporary)
            fixture = Fixture(root / "fixture")
            lock = fixture.transaction.parent / ".asr-api-wrapper.lock"
            lock.write_bytes(b"sentinel")
            lock.chmod(0o644)
            with self.assertRaisesRegex(MODULE.InstallError, "lock identity"):
                MODULE.stage_transaction(
                    fixture.artifacts,
                    fixture.transaction,
                    "9" * 40,
                    unit_command=FakeUnit(),
                )
            self.assertEqual(lock.read_bytes(), b"sentinel")
            self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o644)

            link_fixture = Fixture(root / "link-fixture")
            linked_lock = (
                link_fixture.transaction.parent / ".asr-api-wrapper.lock"
            )
            linked_lock.write_bytes(b"lock")
            linked_lock.chmod(0o600)
            os.link(linked_lock, root / "lock-hardlink")
            with self.assertRaisesRegex(MODULE.InstallError, "lock identity"):
                MODULE.stage_transaction(
                    link_fixture.artifacts,
                    link_fixture.transaction,
                    "8" * 40,
                    unit_command=FakeUnit(),
                )

            replaced_fixture = Fixture(root / "replaced-fixture")
            replaced_lock = (
                replaced_fixture.transaction.parent / ".asr-api-wrapper.lock"
            )
            original_flock = MODULE.fcntl.flock
            replaced = False

            def replace_after_lock(descriptor: int, operation: int) -> None:
                nonlocal replaced
                original_flock(descriptor, operation)
                if operation == MODULE.fcntl.LOCK_EX and not replaced:
                    replaced = True
                    replaced_lock.rename(replaced_lock.with_suffix(".retired"))
                    replaced_lock.write_bytes(b"replacement")
                    replaced_lock.chmod(0o600)

            with (
                mock.patch.object(
                    MODULE.fcntl, "flock", side_effect=replace_after_lock
                ),
                self.assertRaisesRegex(MODULE.InstallError, "changed identity"),
            ):
                MODULE.stage_transaction(
                    replaced_fixture.artifacts,
                    replaced_fixture.transaction,
                    "7" * 40,
                    unit_command=FakeUnit(),
                )
            self.assertFalse(replaced_fixture.transaction.exists())

            target = root / "state-target"
            target.mkdir(mode=0o755)
            target.chmod(0o755)
            linked_root = root / "state-link"
            linked_root.symlink_to(target, target_is_directory=True)
            shared = MODULE._shared_transaction()
            with self.assertRaises((shared.TransactionError, OSError)):
                MODULE._ensure_state_root(linked_root / "transaction")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

    def test_plan_is_exact_seven_artifacts_and_does_not_touch_credentials(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-install-") as temporary:
            fixture = Fixture(Path(temporary))
            identifiers = [item.artifact_id for item in fixture.artifacts]
        self.assertEqual(
            identifiers,
            [
                "keyboard-main",
                "keyboard-visibility-state",
                "keyboard-adapter",
                "keyboard-geometry",
                "keyboard-voice-artifacts",
                "keyboard-unit",
                "asr-api-provisioner",
            ],
        )
        self.assertNotIn("config.json", identifiers)
        self.assertNotIn("api_key", identifiers)

    def test_active_runtime_requires_every_restart_preimage(self) -> None:
        required = sorted(MODULE.ACTIVE_RESTART_ARTIFACTS)
        for artifact_id in required:
            with self.subTest(artifact_id=artifact_id), tempfile.TemporaryDirectory(
                prefix="pds-asr_api-active-preimage-"
            ) as temporary:
                fixture = Fixture(Path(temporary))
                target = next(
                    item.target
                    for item in fixture.artifacts
                    if item.artifact_id == artifact_id
                )
                target.unlink()
                with self.assertRaisesRegex(MODULE.InstallError, "restart preimage"):
                    MODULE.stage_transaction(
                        fixture.artifacts,
                        fixture.transaction,
                        "3" * 40,
                        unit_command=FakeUnit(active="active"),
                    )
                self.assertFalse(fixture.transaction.exists())

        with tempfile.TemporaryDirectory(
            prefix="pds-asr_api-active-provisioner-missing-"
        ) as temporary:
            fixture = Fixture(Path(temporary), provisioner_missing=True)
            staged = MODULE.stage_transaction(
                fixture.artifacts,
                fixture.transaction,
                "2" * 40,
                unit_command=FakeUnit(active="active"),
            )
            self.assertTrue(staged["unit_state_captured"])

    def test_fresh_unit_file_can_activate_and_rollback_to_not_found(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-install-") as temporary:
            fixture = Fixture(Path(temporary), unit_missing=True)
            unit_artifact = next(
                item for item in fixture.artifacts if item.artifact_id == "keyboard-unit"
            )
            unit = FakeUnit(
                enabled="not-found",
                active="inactive",
                unit_path=unit_artifact.target,
            )
            MODULE.stage_transaction(
                fixture.artifacts,
                fixture.transaction,
                "4" * 40,
                unit_command=unit,
            )
            MODULE.apply_transaction(
                fixture.artifacts,
                fixture.transaction,
                activate=True,
                unit_command=unit,
            )
            self.assertEqual(unit.enabled, "disabled")
            self.assertEqual(unit.active, "active")
            verified = MODULE.verify_transaction(
                fixture.artifacts,
                fixture.transaction,
                unit_command=unit,
            )
            self.assertTrue(verified["unit_verified"])
            self.assertTrue(verified["complete"])
            MODULE.rollback_transaction(
                fixture.artifacts,
                fixture.transaction,
                unit_command=unit,
            )
            self.assertEqual(unit.enabled, "not-found")
            self.assertEqual(unit.active, "inactive")
            fixture.assert_preimages(self)

    @unittest.skipUnless(hasattr(os, "fork"), "power-loss fixture needs fork")
    def test_stage_power_loss_before_publication_never_claims_final_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-install-") as temporary:
            fixture = Fixture(Path(temporary))
            child = os.fork()
            if child == 0:
                def crash_before_publish(source: Path, destination: Path) -> None:
                    if destination != fixture.transaction:
                        os._exit(95)
                    if not (source / "unit-state.json").is_file():
                        os._exit(96)
                    os._exit(86)

                with mock.patch.object(
                    MODULE,
                    "_rename_transaction_noreplace",
                    side_effect=crash_before_publish,
                ):
                    MODULE.stage_transaction(
                        fixture.artifacts,
                        fixture.transaction,
                        "0" * 40,
                        unit_command=FakeUnit(),
                    )
                os._exit(97)
            _pid, status = os.waitpid(child, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 86)
            self.assertFalse(fixture.transaction.exists())

            staged = MODULE.stage_transaction(
                fixture.artifacts,
                fixture.transaction,
                "0" * 40,
                unit_command=FakeUnit(),
            )
            self.assertTrue(staged["unit_state_captured"])
            self.assertTrue((fixture.transaction / "unit-state.json").is_file())

    def test_default_apply_is_deferred_and_rollback_restores_exact_preimages(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-install-") as temporary:
            fixture = Fixture(Path(temporary), provisioner_missing=True)
            unit = FakeUnit()
            staged = MODULE.stage_transaction(
                fixture.artifacts,
                fixture.transaction,
                "a" * 40,
                unit_command=unit,
            )
            self.assertTrue(staged["activation_deferred"])
            self.assertFalse(staged["credentials_written"])
            unit.calls.clear()
            applied = MODULE.apply_transaction(
                fixture.artifacts,
                fixture.transaction,
                activate=False,
                unit_command=unit,
            )
            self.assertFalse(applied["activation_performed"])
            self.assertEqual(unit.calls, [])
            for artifact in fixture.artifacts:
                self.assertEqual(artifact.target.read_bytes(), artifact.source.read_bytes())
                self.assertEqual(stat.S_IMODE(artifact.target.stat().st_mode), artifact.target_mode)
            verified = MODULE.verify_transaction(
                fixture.artifacts, fixture.transaction, unit_command=unit
            )
            self.assertTrue(verified["complete"])
            rolled = MODULE.rollback_transaction(
                fixture.artifacts, fixture.transaction, unit_command=unit
            )
            self.assertTrue(rolled["all_preimages_verified"])
            self.assertFalse(rolled["unit_state_restored"])
            fixture.assert_preimages(self)

    def test_active_preimage_rollback_restarts_restored_process_generation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-install-") as temporary:
            fixture = Fixture(Path(temporary))
            unit = FakeUnit(enabled="enabled", active="active")
            MODULE.stage_transaction(
                fixture.artifacts,
                fixture.transaction,
                "6" * 40,
                unit_command=unit,
            )
            MODULE.apply_transaction(
                fixture.artifacts,
                fixture.transaction,
                activate=True,
                unit_command=unit,
            )
            self.assertEqual(unit.calls.count(("restart", MODULE.UNIT)), 1)
            MODULE.rollback_transaction(
                fixture.artifacts,
                fixture.transaction,
                unit_command=unit,
            )
            self.assertEqual(unit.calls.count(("restart", MODULE.UNIT)), 2)
            fixture.assert_preimages(self)

    @unittest.skipUnless(hasattr(os, "fork"), "power-loss fixture needs fork")
    def test_shared_applied_marker_crash_leaves_no_partial_final_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-install-") as temporary:
            fixture = Fixture(Path(temporary), provisioner_missing=True)
            unit = FakeUnit(active="inactive")
            MODULE.stage_transaction(
                fixture.artifacts,
                fixture.transaction,
                "5" * 40,
                unit_command=unit,
            )
            shared = MODULE._shared_transaction()
            child = os.fork()
            if child == 0:
                original = shared._rename_noreplace

                def crash_before_marker_publish(source: Path, destination: Path) -> None:
                    if destination.name == "applied.json":
                        if not source.name.startswith(".applied.json.staging-"):
                            os._exit(96)
                        os._exit(88)
                    original(source, destination)

                shared._rename_noreplace = crash_before_marker_publish
                MODULE.apply_transaction(
                    fixture.artifacts,
                    fixture.transaction,
                    activate=False,
                    unit_command=unit,
                )
                os._exit(97)
            _pid, status = os.waitpid(child, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 88)
            self.assertFalse((fixture.transaction / "applied.json").exists())
            for artifact in fixture.artifacts:
                self.assertEqual(artifact.target.read_bytes(), artifact.source.read_bytes())

            rolled = MODULE.rollback_transaction(
                fixture.artifacts,
                fixture.transaction,
                unit_command=unit,
            )
            self.assertTrue(rolled["all_preimages_verified"])
            fixture.assert_preimages(self)

    def test_explicit_activation_and_rollback_restore_inactive_unit_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-install-") as temporary:
            fixture = Fixture(Path(temporary))
            unit = FakeUnit(enabled="enabled", active="inactive")
            MODULE.stage_transaction(
                fixture.artifacts,
                fixture.transaction,
                "b" * 40,
                unit_command=unit,
            )
            applied = MODULE.apply_transaction(
                fixture.artifacts,
                fixture.transaction,
                activate=True,
                unit_command=unit,
            )
            self.assertTrue(applied["activation_performed"])
            self.assertEqual(unit.enabled, "enabled")
            self.assertEqual(unit.active, "active")
            self.assertIn(("daemon-reload",), unit.calls)
            self.assertIn(("restart", MODULE.UNIT), unit.calls)

            rolled = MODULE.rollback_transaction(
                fixture.artifacts,
                fixture.transaction,
                unit_command=unit,
            )
            self.assertTrue(rolled["unit_state_restored"])
            self.assertEqual(unit.enabled, "enabled")
            self.assertEqual(unit.active, "inactive")
            self.assertIn(("stop", MODULE.UNIT), unit.calls)
            fixture.assert_preimages(self)

    def test_activation_error_and_signal_equivalent_restore_files_and_unit(self) -> None:
        for mode in ("error", "signal"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(
                prefix="pds-asr_api-install-"
            ) as temporary:
                fixture = Fixture(Path(temporary), provisioner_missing=True)
                unit = FakeUnit(
                    active="inactive",
                    fail_action="restart" if mode == "error" else None,
                    raise_action="restart" if mode == "signal" else None,
                )
                MODULE.stage_transaction(
                    fixture.artifacts,
                    fixture.transaction,
                    "c" * 40,
                    unit_command=unit,
                )
                with self.assertRaises(MODULE.InstallError):
                    MODULE.apply_transaction(
                        fixture.artifacts,
                        fixture.transaction,
                        activate=True,
                        unit_command=unit,
                    )
                fixture.assert_preimages(self)
                self.assertEqual(unit.enabled, "enabled")
                self.assertEqual(unit.active, "inactive")

    @unittest.skipUnless(hasattr(os, "fork"), "power-loss fixture needs fork")
    def test_durable_intent_precedes_first_live_mutation_and_can_rollback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-install-") as temporary:
            fixture = Fixture(Path(temporary), provisioner_missing=True)
            unit = FakeUnit(active="inactive")
            MODULE.stage_transaction(
                fixture.artifacts,
                fixture.transaction,
                "e" * 40,
                unit_command=unit,
            )
            shared = MODULE._shared_transaction()
            child = os.fork()
            if child == 0:
                def crash_before_apply(*_args: object, **_kwargs: object) -> object:
                    if MODULE._load_activation_intent(fixture.transaction) is not True:
                        os._exit(90)
                    if MODULE._load_activation_complete(fixture.transaction) is not None:
                        os._exit(91)
                    os._exit(87)

                shared.apply_transaction = crash_before_apply
                MODULE.apply_transaction(
                    fixture.artifacts,
                    fixture.transaction,
                    activate=True,
                    unit_command=unit,
                )
                os._exit(92)
            _pid, status = os.waitpid(child, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 87)
            fixture.assert_preimages(self)
            self.assertTrue(MODULE._load_activation_intent(fixture.transaction))
            self.assertIsNone(MODULE._load_activation_complete(fixture.transaction))

            rolled = MODULE.rollback_transaction(
                fixture.artifacts,
                fixture.transaction,
                unit_command=unit,
            )
            self.assertTrue(rolled["all_preimages_verified"])
            fixture.assert_preimages(self)
            with self.assertRaisesRegex(MODULE.InstallError, "already rolled back"):
                MODULE.rollback_transaction(
                    fixture.artifacts,
                    fixture.transaction,
                    unit_command=unit,
                )

    @unittest.skipUnless(hasattr(os, "fork"), "power-loss fixture needs fork")
    def test_power_loss_before_activation_complete_restores_files_and_unit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-install-") as temporary:
            fixture = Fixture(Path(temporary), provisioner_missing=True)
            unit = FakeUnit(active="inactive")
            MODULE.stage_transaction(
                fixture.artifacts,
                fixture.transaction,
                "f" * 40,
                unit_command=unit,
            )
            child = os.fork()
            if child == 0:
                with mock.patch.object(
                    MODULE,
                    "_write_activation_complete",
                    side_effect=lambda *_args, **_kwargs: os._exit(88),
                ):
                    MODULE.apply_transaction(
                        fixture.artifacts,
                        fixture.transaction,
                        activate=True,
                        unit_command=unit,
                    )
                os._exit(93)
            _pid, status = os.waitpid(child, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 88)
            for artifact in fixture.artifacts:
                self.assertEqual(artifact.target.read_bytes(), artifact.source.read_bytes())
            self.assertTrue(MODULE._load_activation_intent(fixture.transaction))
            self.assertIsNone(MODULE._load_activation_complete(fixture.transaction))

            rolled = MODULE.rollback_transaction(
                fixture.artifacts,
                fixture.transaction,
                unit_command=unit,
            )
            self.assertTrue(rolled["unit_state_restored"])
            fixture.assert_preimages(self)
            self.assertEqual(unit.active, "inactive")

    @unittest.skipUnless(hasattr(os, "fork"), "power-loss fixture needs fork")
    def test_second_power_loss_after_file_rollback_resumes_unit_restore(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-install-") as temporary:
            fixture = Fixture(Path(temporary), provisioner_missing=True)
            unit = FakeUnit(active="inactive")
            MODULE.stage_transaction(
                fixture.artifacts,
                fixture.transaction,
                "1" * 40,
                unit_command=unit,
            )
            MODULE.apply_transaction(
                fixture.artifacts,
                fixture.transaction,
                activate=True,
                unit_command=unit,
            )
            self.assertEqual(unit.active, "active")

            child = os.fork()
            if child == 0:
                with mock.patch.object(
                    MODULE,
                    "_write_activation_rollback",
                    side_effect=lambda *_args, **_kwargs: os._exit(89),
                ):
                    MODULE.rollback_transaction(
                        fixture.artifacts,
                        fixture.transaction,
                        unit_command=unit,
                    )
                os._exit(94)
            _pid, status = os.waitpid(child, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 89)
            fixture.assert_preimages(self)
            self.assertIsNone(MODULE._load_activation_rollback(fixture.transaction))

            resumed = MODULE.rollback_transaction(
                fixture.artifacts,
                fixture.transaction,
                unit_command=unit,
            )
            self.assertTrue(resumed["unit_state_restored"])
            self.assertEqual(unit.active, "inactive")
            fixture.assert_preimages(self)

    def test_unit_snapshot_is_private_strict_and_contains_no_paths_or_secrets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-asr_api-install-") as temporary:
            fixture = Fixture(Path(temporary))
            MODULE.stage_transaction(
                fixture.artifacts,
                fixture.transaction,
                "d" * 40,
                unit_command=FakeUnit(enabled="disabled", active="inactive"),
            )
            state = fixture.transaction / "unit-state.json"
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)
            payload = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(
                set(payload), {"schema", "unit", "enabled", "active"}
            )
            serialized = json.dumps(payload)
            self.assertNotIn(temporary, serialized)
            self.assertNotIn("access", serialized.lower())

    def test_cli_activation_is_explicit_and_confirmed(self) -> None:
        args = MODULE.parse_args(["--apply", "update-1"])
        self.assertFalse(args.activate)
        args = MODULE.parse_args(["--apply", "update-1", "--activate"])
        self.assertTrue(args.activate)
        with self.assertRaises(SystemExit):
            MODULE.parse_args(["--stage", "update-1", "--activate"])
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("APPLY_CONFIRMATION", source)
        self.assertIn("ROLLBACK_CONFIRMATION", source)
        self.assertIn("SIGINT", source)
        self.assertIn("SIGTERM", source)
        self.assertIn("SIGHUP", source)
        self.assertIn('"credentials_written": False', source)
        self.assertNotIn("Authorization", source)
        self.assertNotIn("Bearer", source)
        self.assertNotIn("os.environ", source)

    def test_formal_installer_uses_one_seven_file_activated_transaction(self) -> None:
        installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        self.assertIn('python3 "$repo_root/scripts/install-asr-api.sh"', installer)
        self.assertIn("POCKETDS-STAGE-ASR-API-SEVEN-FILES", installer)
        self.assertIn("POCKETDS-APPLY-ASR-API-SEVEN-FILES", installer)
        self.assertIn('--apply "$asr_transaction" --activate', installer)
        self.assertIn('--verify "$asr_transaction"', installer)
        self.assertNotIn("restart pocketds-keyboard.service", installer)
        for direct_source in (
            "components/keyboard/pocketds-keyboard.py",
            "components/keyboard/visibility_state.py",
            "components/keyboard/keyboard_adapter.py",
            "components/keyboard/screen_geometry.py",
            "components/keyboard/voice_artifacts.py",
            "components/keyboard/pocketds-keyboard.service",
            "scripts/pocketds-asr-api-provision.py",
        ):
            self.assertNotIn(direct_source, installer)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Fixture and static tests for single-session PDS-010 acceptance."""

from __future__ import annotations

import importlib.util
from dataclasses import replace
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pds010-wiliwili-attended.py"
SPEC = importlib.util.spec_from_file_location("pds010_wiliwili_attended", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeInfo:
    def __init__(self, vendor: int = MODULE.EXPECTED_VENDOR) -> None:
        self.bustype = MODULE.EXPECTED_BUS
        self.vendor = vendor
        self.product = MODULE.EXPECTED_PRODUCT
        self.version = MODULE.EXPECTED_VERSION


class FakeAbs:
    def __init__(self, value: int, minimum: int, maximum: int) -> None:
        self.value = value
        self.min = minimum
        self.max = maximum


def neutral_axes() -> dict[int, int]:
    return {
        MODULE.ABS_X: 0,
        MODULE.ABS_Y: 0,
        MODULE.ABS_Z: 0,
        MODULE.ABS_RX: 0,
        MODULE.ABS_RY: 0,
        MODULE.ABS_RZ: 0,
        MODULE.ABS_HAT0X: 0,
        MODULE.ABS_HAT0Y: 0,
    }


def state(*keys: int, axes: dict[int, int] | None = None) -> MODULE.DeviceState:
    values = neutral_axes()
    if axes:
        values.update(axes)
    return MODULE.DeviceState(frozenset(keys), values)


class SequenceDevice:
    def __init__(
        self,
        states: list[MODULE.DeviceState],
        *,
        vendor: int = MODULE.EXPECTED_VENDOR,
    ) -> None:
        self.states = states
        self.index = 0
        self.current = states[0]
        self.name = MODULE.EXPECTED_EVENT_NAME
        self.info = FakeInfo(vendor)
        self.closed = False

    def capabilities(self, *, absinfo: bool = False):
        del absinfo
        return {
            MODULE.EV_KEY: sorted(MODULE.MONITORED_KEYS | {MODULE.BTN_MODE}),
            MODULE.EV_ABS: sorted(MODULE.MONITORED_AXES),
        }

    def active_keys(self, *, verbose: bool = False):
        del verbose
        self.current = self.states[min(self.index, len(self.states) - 1)]
        self.index += 1
        return sorted(self.current.keys)

    def absinfo(self, code: int):
        if code in (MODULE.ABS_Z, MODULE.ABS_RZ):
            minimum, maximum = 0, 255
        elif code in (MODULE.ABS_HAT0X, MODULE.ABS_HAT0Y):
            minimum, maximum = -1, 1
        else:
            minimum, maximum = -32768, 32767
        return FakeAbs(self.current.axes[code], minimum, maximum)

    def close(self) -> None:
        self.closed = True


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, duration: float) -> None:
        self.value += duration


def run_capture(
    device: SequenceDevice,
    control: str,
    *,
    rounds: int = 1,
    stable_polls: int = 2,
    timeout: float = 0.25,
) -> str:
    clock = Clock()
    return MODULE.capture_control(
        device,
        MODULE.get_control_spec(control),
        rounds=rounds,
        timeout=timeout,
        poll_interval=0.01,
        stable_polls=stable_polls,
        announce=lambda _message: None,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


def fixture_paths(root: Path) -> MODULE.Paths:
    home = root / "home"
    runtime = root / "runtime"
    repo = root / "repo"
    home.mkdir(exist_ok=True)
    runtime.mkdir(exist_ok=True)
    return MODULE.Paths(
        repo=repo,
        home=home,
        proc=root / "proc",
        dev_input=root / "dev/input",
        sys_input=root / "sys/class/input",
        runtime=runtime,
        input_state=root / "input-state",
        session_record=runtime / "pocketds-game-session/session.json",
        invocation_link=root / "invocation",
        flatpak_root=runtime / ".flatpak",
        helper_source=repo / "components/emulation/pocketds-input-mode",
        helper_live=root / "live/pocketds-input-mode",
        supervisor_source=repo / "components/emulation/pocketds-game-session.py",
        supervisor_live=root / "live/pocketds-game-session",
        launcher_source=repo / "components/wiliwili/pocketds-wiliwili",
        launcher_live=home / ".local/bin/pocketds-wiliwili",
        mapping_source=repo / "components/wiliwili/gamecontrollerdb.txt",
        mapping_installed=home / "installed/gamecontrollerdb.txt",
        mapping_live=home / "live/gamecontrollerdb.txt",
    )


def baseline() -> MODULE.Baseline:
    return MODULE.Baseline(
        input_state=MODULE.InputState(
            "11111111-1111-4111-8111-111111111111", "gamepad"
        ),
        inputplumber=MODULE.InputPlumberSnapshot(566, "1234", "b" * 32),
        session=MODULE.SessionSnapshot(
            owner_pid=1000,
            owner_starttime="2000",
            owner_cgroup="/user.slice/test.service",
            wiliwili_pid=1001,
            wiliwili_starttime="2001",
            flatpak_instance="12345",
            input_previous="joymouse",
            input_token="11111111-1111-4111-8111-111111111111",
            session_sha256="c" * 64,
            event=MODULE.EventFdBinding(22, "event91", 10, 20, 30),
        ),
        artifacts=MODULE.RuntimeArtifacts(
            "d" * 64, "e" * 64, "f" * 64, "1" * 64, "2" * 64
        ),
    )


class CaptureTests(unittest.TestCase):
    def test_button_press_release_passes(self) -> None:
        device = SequenceDevice(
            [
                state(),
                state(MODULE.BTN_SOUTH),
                state(MODULE.BTN_SOUTH),
                state(),
                state(),
            ]
        )
        self.assertEqual(run_capture(device, "a"), "pass")

    def test_wrong_button_is_rejected(self) -> None:
        device = SequenceDevice([state(), state(MODULE.BTN_EAST)])
        self.assertEqual(run_capture(device, "a"), "unexpected-control")

    def test_sampled_level_instability_is_rejected(self) -> None:
        device = SequenceDevice(
            [state(), state(MODULE.BTN_SOUTH), state(), state(MODULE.BTN_SOUTH)]
        )
        self.assertEqual(
            run_capture(device, "a", stable_polls=3),
            "sampled-level-instability",
        )

    def test_missing_release_is_rejected(self) -> None:
        device = SequenceDevice(
            [state(), state(MODULE.BTN_SOUTH), state(MODULE.BTN_SOUTH)]
        )
        self.assertEqual(
            run_capture(device, "a", timeout=0.05), "recenter-timeout"
        )

    def test_axis_activation_and_recenter_passes(self) -> None:
        device = SequenceDevice(
            [
                state(),
                state(axes={MODULE.ABS_RX: 30000}),
                state(axes={MODULE.ABS_RX: 30000}),
                state(),
                state(),
            ]
        )
        self.assertEqual(run_capture(device, "right-stick-right"), "pass")

    def test_opposite_selected_axis_direction_is_rejected(self) -> None:
        device = SequenceDevice(
            [state(), state(axes={MODULE.ABS_X: -30000})]
        )
        self.assertEqual(
            run_capture(device, "left-stick-right"), "unexpected-control"
        )

    def test_wrong_device_is_rejected(self) -> None:
        with self.assertRaisesRegex(MODULE.AcceptanceError, "identity is wrong"):
            MODULE.validate_device(SequenceDevice([state()], vendor=0x1234))

    def test_guide_is_kept_for_separate_transition_test(self) -> None:
        for name in ("guide", "mode", "home"):
            with self.assertRaisesRegex(MODULE.AcceptanceError, "separate"):
                MODULE.get_control_spec(name)

    def test_control_list_rejects_duplicates_and_empty_items(self) -> None:
        for value in ("a,a", "a,,b", ""):
            with self.assertRaises(MODULE.AcceptanceError):
                MODULE.parse_controls(value)

    def test_release_round_limit_allows_exactly_fifty(self) -> None:
        arguments = MODULE.parser().parse_args(
            ["--rounds", "50", "--confirm", MODULE.CAPTURE_CONFIRMATION]
        )
        MODULE.validate_arguments(arguments)
        arguments.rounds = 51
        with self.assertRaisesRegex(MODULE.AcceptanceError, "between 1 and 50"):
            MODULE.validate_arguments(arguments)

    def test_attended_default_allows_time_to_reach_the_device(self) -> None:
        arguments = MODULE.parser().parse_args(
            ["--confirm", MODULE.CAPTURE_CONFIRMATION]
        )
        self.assertEqual(arguments.timeout, 60.0)


class IdentityTests(unittest.TestCase):
    def test_strict_json_rejects_duplicates_and_nonfinite_numbers(self) -> None:
        for value in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
            with self.assertRaises(ValueError):
                MODULE.strict_json_loads(value)

    def test_multiple_wiliwili_processes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            proc = Path(raw)
            for pid in (100, 200):
                directory = proc / str(pid)
                directory.mkdir()
                (directory / "comm").write_text("wiliwili\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.AcceptanceError, "exactly one"):
                MODULE.unique_process_named(proc, "wiliwili")

    def test_preflight_rejects_foreign_gamepad_lease(self) -> None:
        foreign = MODULE.InputState(
            "99999999-9999-4999-8999-999999999999", "gamepad"
        )
        with tempfile.TemporaryDirectory() as raw:
            paths = fixture_paths(Path(raw))
            with (
                mock.patch.object(
                    MODULE, "runtime_artifacts", return_value=baseline().artifacts
                ),
                mock.patch.object(MODULE, "helper_wait"),
                mock.patch.object(MODULE, "read_input_state", return_value=foreign),
                mock.patch.object(
                    MODULE,
                    "inputplumber_snapshot",
                    return_value=baseline().inputplumber,
                ),
                mock.patch.object(
                    MODULE, "managed_session", return_value=baseline().session
                ),
            ):
                with self.assertRaisesRegex(MODULE.AcceptanceError, "lease"):
                    MODULE.preflight(paths, user_uid=os.getuid())

    def test_extra_or_duplicate_event_descriptors_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = fixture_paths(Path(raw))
            first = MODULE.EventFdBinding(7, "event42", 10, 20, 30)
            cases = (
                [first, MODULE.EventFdBinding(8, "event43", 11, 21, 31)],
                [first, MODULE.EventFdBinding(8, "event42", 10, 20, 30)],
            )
            for bindings in cases:
                with self.subTest(bindings=bindings):
                    with mock.patch.object(
                        MODULE,
                        "event_bindings_for_process",
                        return_value=("100", bindings),
                    ):
                        with self.assertRaisesRegex(
                            MODULE.AcceptanceError, "exactly one"
                        ):
                            MODULE.select_wiliwili_event(paths, 99)

    def test_event_enumeration_rejects_pid_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = fixture_paths(Path(raw))
            fd_root = paths.proc / "99/fd"
            fd_root.mkdir(parents=True)
            paths.dev_input.mkdir(parents=True)
            event = paths.dev_input / "event42"
            event.symlink_to("/dev/null")
            (fd_root / "7").symlink_to(event)
            with mock.patch.object(
                MODULE, "proc_starttime", side_effect=["100", "101"]
            ):
                with self.assertRaisesRegex(MODULE.AcceptanceError, "PID changed"):
                    MODULE.event_bindings_for_process(paths, 99)

    def test_single_event_descriptor_binding_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = fixture_paths(Path(raw))
            binding = MODULE.EventFdBinding(22, "event91", 10, 20, 30)
            metadata = types.SimpleNamespace(
                st_mode=stat.S_IFCHR | 0o600,
                st_dev=10,
                st_ino=20,
                st_rdev=30,
            )
            expected = (
                MODULE.EXPECTED_EVENT_NAME,
                MODULE.EXPECTED_BUS,
                MODULE.EXPECTED_VENDOR,
                MODULE.EXPECTED_PRODUCT,
                MODULE.EXPECTED_VERSION,
            )
            with (
                mock.patch.object(
                    MODULE,
                    "event_bindings_for_process",
                    return_value=("2001", [binding]),
                ),
                mock.patch.object(
                    MODULE,
                    "virtual_input_device_path",
                    return_value=Path("/sys/devices/virtual/input/input91"),
                ),
                mock.patch.object(MODULE, "read_sysfs_identity", return_value=expected),
                mock.patch.object(
                    MODULE,
                    "matching_virtual_gamepads",
                    return_value=("event91",),
                ),
                mock.patch.object(MODULE.Path, "lstat", return_value=metadata),
            ):
                starttime, observed = MODULE.select_wiliwili_event(paths, 1001)
            self.assertEqual(starttime, "2001")
            self.assertEqual(observed, binding)

    def test_duplicate_direct_virtual_identity_is_rejected(self) -> None:
        binding = MODULE.EventFdBinding(22, "event91", 10, 20, 30)
        expected = (
            MODULE.EXPECTED_EVENT_NAME,
            MODULE.EXPECTED_BUS,
            MODULE.EXPECTED_VENDOR,
            MODULE.EXPECTED_PRODUCT,
            MODULE.EXPECTED_VERSION,
        )
        with tempfile.TemporaryDirectory() as raw:
            paths = fixture_paths(Path(raw))
            with (
                mock.patch.object(
                    MODULE,
                    "event_bindings_for_process",
                    return_value=("2001", [binding]),
                ),
                mock.patch.object(
                    MODULE,
                    "virtual_input_device_path",
                    return_value=Path("/sys/devices/virtual/input/input91"),
                ),
                mock.patch.object(MODULE, "read_sysfs_identity", return_value=expected),
                mock.patch.object(
                    MODULE,
                    "matching_virtual_gamepads",
                    return_value=("event91", "event92"),
                ),
            ):
                with self.assertRaisesRegex(
                    MODULE.AcceptanceError, "identity is not unique"
                ):
                    MODULE.select_wiliwili_event(paths, 1001)

    def test_external_identity_collision_is_not_a_managed_virtual_pad(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            paths = fixture_paths(root)
            sys_input = root / "sys/class/input"
            virtual = root / "sys/devices/virtual/input"
            physical = root / "sys/devices/pci0000/usb1/1-1/input/input42"
            event = sys_input / "event7"
            virtual.mkdir(parents=True)
            physical.mkdir(parents=True)
            event.mkdir(parents=True)
            (event / "device").symlink_to(physical)
            paths = replace(paths, sys_input=sys_input)
            with self.assertRaisesRegex(
                MODULE.AcceptanceError, "not the managed virtual gamepad"
            ):
                MODULE.virtual_input_device_path(paths, "event7")

    def test_direct_virtual_input_ancestry_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            paths = fixture_paths(root)
            sys_input = root / "sys/class/input"
            virtual_device = root / "sys/devices/virtual/input/input42"
            event = sys_input / "event7"
            virtual_device.mkdir(parents=True)
            event.mkdir(parents=True)
            (event / "device").symlink_to(virtual_device)
            paths = replace(paths, sys_input=sys_input)
            self.assertEqual(
                MODULE.virtual_input_device_path(paths, "event7"),
                virtual_device.resolve(),
            )

    def test_opened_node_must_match_wiliwili_descriptor(self) -> None:
        expected = MODULE.EventFdBinding(1, "event1", 1, 2, 3)
        with self.assertRaisesRegex(MODULE.AcceptanceError, "no longer matches"):
            MODULE.IoctlStateDevice(Path("/dev/null"), expected)

    def test_runtime_artifacts_bind_source_and_live_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = fixture_paths(Path(raw))
            executable_pairs = (
                (paths.helper_source, paths.helper_live, b"helper\n"),
                (paths.supervisor_source, paths.supervisor_live, b"supervisor\n"),
                (paths.launcher_source, paths.launcher_live, b"launcher\n"),
            )
            for source, live, content in executable_pairs:
                source.parent.mkdir(parents=True, exist_ok=True)
                live.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(content)
                live.write_bytes(content)
                source.chmod(0o755)
                live.chmod(0o755)
            mapping = (ROOT / "components/wiliwili/gamecontrollerdb.txt").read_bytes()
            row = MODULE.parse_mapping(mapping)
            for path, content in (
                (paths.mapping_source, mapping),
                (paths.mapping_installed, mapping),
                (paths.mapping_live, (row + "\n").encode()),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                path.chmod(0o644)
            observed = MODULE.runtime_artifacts(
                paths, user_uid=os.getuid(), root_uid=os.getuid()
            )
            self.assertEqual(observed.mapping_sha256, MODULE.sha256(mapping))


class LifecycleTests(unittest.TestCase):
    def test_observer_window_is_closed_by_runtime_guard(self) -> None:
        fake = SequenceDevice([state()])
        with tempfile.TemporaryDirectory() as raw:
            paths = fixture_paths(Path(raw))
            with (
                mock.patch.object(MODULE, "preflight", return_value=baseline()),
                mock.patch.object(MODULE, "IoctlStateDevice", return_value=fake),
                mock.patch.object(MODULE, "capture_control", return_value="pass"),
                mock.patch.object(
                    MODULE,
                    "runtime_guard",
                    side_effect=[None, None, MODULE.AcceptanceError("drift")],
                ) as guard,
            ):
                with self.assertRaisesRegex(MODULE.AcceptanceError, "drift"):
                    MODULE.run_attended(
                        paths,
                        [MODULE.get_control_spec("a")],
                        rounds=1,
                        timeout=1.0,
                        poll_interval=0.04,
                        stable_polls=3,
                        exit_timeout=10.0,
                        input_fn=lambda _prompt: MODULE.OBSERVER_PASS,
                    )
            self.assertEqual(guard.call_count, 3)
            self.assertTrue(fake.closed)

    def test_observer_failure_cannot_be_upgraded_by_clean_exit(self) -> None:
        fake = SequenceDevice([state()])
        answers = iter((MODULE.OBSERVER_FAIL, MODULE.EXIT_CONFIRMATION))
        with tempfile.TemporaryDirectory() as raw:
            paths = fixture_paths(Path(raw))
            with (
                mock.patch.object(MODULE, "preflight", return_value=baseline()),
                mock.patch.object(MODULE, "IoctlStateDevice", return_value=fake),
                mock.patch.object(MODULE, "capture_control", return_value="pass"),
                mock.patch.object(MODULE, "runtime_guard"),
                mock.patch.object(MODULE, "wait_for_natural_exit"),
                mock.patch.object(MODULE, "verify_restoration"),
            ):
                result = MODULE.run_attended(
                    paths,
                    [MODULE.get_control_spec("a")],
                    rounds=1,
                    timeout=1.0,
                    poll_interval=0.04,
                    stable_polls=3,
                    exit_timeout=10.0,
                    input_fn=lambda _prompt: next(answers),
                )
            self.assertEqual(result, 1)

    def test_exact_session_natural_exit_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = fixture_paths(Path(raw))
            with (
                mock.patch.object(MODULE, "process_identity_exited", return_value=True),
                mock.patch.object(MODULE, "flatpak_instance_absent", return_value=True),
                mock.patch.object(MODULE, "session_record_absent", return_value=True),
            ):
                MODULE.wait_for_natural_exit(paths, baseline(), timeout=1.0)

    def test_natural_exit_timeout_is_fail_closed(self) -> None:
        clock = Clock()
        with tempfile.TemporaryDirectory() as raw:
            paths = fixture_paths(Path(raw))
            with mock.patch.object(
                MODULE, "process_identity_exited", return_value=False
            ):
                with self.assertRaisesRegex(MODULE.AcceptanceError, "did not exit"):
                    MODULE.wait_for_natural_exit(
                        paths,
                        baseline(),
                        timeout=0.2,
                        monotonic=clock.monotonic,
                        sleep=clock.sleep,
                    )

    def test_restoration_requires_new_joymouse_lease_and_same_plumber(self) -> None:
        restored = MODULE.InputState(
            "22222222-2222-4222-8222-222222222222", "joymouse"
        )
        with tempfile.TemporaryDirectory() as raw:
            paths = fixture_paths(Path(raw))
            with (
                mock.patch.object(MODULE, "helper_wait") as helper,
                mock.patch.object(MODULE, "read_input_state", return_value=restored),
                mock.patch.object(
                    MODULE,
                    "inputplumber_snapshot",
                    return_value=baseline().inputplumber,
                ),
                mock.patch.object(MODULE, "processes_named", return_value=[]),
                mock.patch.object(MODULE, "session_record_absent", return_value=True),
                mock.patch.object(
                    MODULE, "runtime_artifacts", return_value=baseline().artifacts
                ),
            ):
                MODULE.verify_restoration(
                    paths, baseline(), user_uid=os.getuid()
                )
            self.assertEqual(helper.call_args_list, [
                mock.call(paths, "joymouse"),
                mock.call(paths, "joymouse"),
            ])

    def test_restoration_rejects_mode_change_during_final_checks(self) -> None:
        restored = MODULE.InputState(
            "22222222-2222-4222-8222-222222222222", "joymouse"
        )
        switched = MODULE.InputState(
            "33333333-3333-4333-8333-333333333333", "gamepad"
        )
        with tempfile.TemporaryDirectory() as raw:
            paths = fixture_paths(Path(raw))
            with (
                mock.patch.object(MODULE, "helper_wait"),
                mock.patch.object(
                    MODULE,
                    "read_input_state",
                    side_effect=[restored, switched],
                ),
                mock.patch.object(
                    MODULE,
                    "inputplumber_snapshot",
                    return_value=baseline().inputplumber,
                ),
                mock.patch.object(MODULE, "processes_named", return_value=[]),
                mock.patch.object(MODULE, "session_record_absent", return_value=True),
                mock.patch.object(
                    MODULE, "runtime_artifacts", return_value=baseline().artifacts
                ),
            ):
                with self.assertRaisesRegex(
                    MODULE.AcceptanceError, "lease changed"
                ):
                    MODULE.verify_restoration(
                        paths, baseline(), user_uid=os.getuid()
                    )


class StaticSafetyTests(unittest.TestCase):
    def test_only_current_state_ioctls_are_used(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("os.O_RDONLY | os.O_NONBLOCK", source)
        self.assertIn("input_read_ioctl(0x18", source)
        self.assertIn("input_read_ioctl(0x40 + code", source)
        self.assertNotIn("event7", source)

    def test_single_session_tool_has_no_writer_or_replay_surface(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "EVIOCGRAB",
            ".grab(",
            "read_loop(",
            "device.read(",
            "os.O_WRONLY",
            "os.O_RDWR",
            "os.kill(",
            "SetTargetDevices",
            "LoadProfilePath",
            "write_private_report",
            "--output",
            "current_transaction",
            "committed.json",
        ):
            self.assertNotIn(forbidden, source)
        self.assertEqual(source.count("subprocess.run("), 1)
        self.assertIn('[str(paths.helper_live), "wait", mode]', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

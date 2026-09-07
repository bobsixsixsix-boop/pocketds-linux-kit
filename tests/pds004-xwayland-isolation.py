#!/usr/bin/env python3
"""Pure tests for the confirmation-gated nested Xwayland harness."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pds004-xwayland-isolation.py"
CLIENT_SCRIPT = ROOT / "scripts/pds004-x11-probe-client.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load("pds004_xwayland_isolation", SCRIPT)
CLIENT = load("pds004_x11_probe_client", CLIENT_SCRIPT)


def healthy_round() -> dict[str, object]:
    return {
        "probe_ready": True,
        "nested_kwin_unique": True,
        "nested_xwayland_unique": True,
        "probe_client_unique": True,
        "xwayland_signal_sent": True,
        "client_exited_within_bound": True,
        "nested_session_exited_within_bound": True,
        "cleanup_complete": True,
        "private_runtime_removed": True,
        "startup_ms": 1234,
        "failure_stage": "none",
    }


class ProcessBoundaryTests(unittest.TestCase):
    def test_proc_stat_parser_handles_spaces_and_parentheses(self) -> None:
        content = "123 (name with ) paren) S 44 55 66 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 98765 0"
        self.assertEqual(MODULE.parse_proc_stat(content), (44, 66, 98765))
        for malformed in ("", "1 no-parens", "1 (x) S 2"):
            with self.assertRaises(MODULE.HarnessError):
                MODULE.parse_proc_stat(malformed)

    def test_descendants_are_transitive_and_do_not_include_host_processes(self) -> None:
        uid = os.getuid()
        info = MODULE.ProcessInfo
        processes = {
            100: info(100, 1, 100, 1, uid, "/usr/bin/dbus-run-session"),
            110: info(110, 100, 100, 2, uid, str(MODULE.KWIN)),
            120: info(120, 110, 100, 3, uid, str(MODULE.XWAYLAND)),
            130: info(130, 110, 100, 4, uid, "/usr/bin/python3"),
            900: info(900, 1, 900, 5, uid, str(MODULE.XWAYLAND)),
        }
        self.assertEqual(MODULE.descendants(processes, 100), {110, 120, 130})

    def test_unique_owned_process_requires_descendant_session_runtime_and_token(self) -> None:
        uid = os.getuid()
        info = MODULE.ProcessInfo
        processes = {
            100: info(100, 1, 100, 1, uid, "/usr/bin/dbus-run-session"),
            110: info(110, 100, 100, 2, uid, str(MODULE.KWIN)),
            120: info(120, 110, 100, 3, uid, str(MODULE.XWAYLAND)),
            130: info(130, 110, 100, 4, uid, "/usr/bin/python3"),
            140: info(140, 110, 999, 5, uid, str(MODULE.XWAYLAND)),
        }
        runtime = Path("/private/runtime")
        with mock.patch.object(MODULE, "_has_private_runtime", return_value=True):
            found = MODULE.unique_owned_process(
                processes,
                root_pid=100,
                runtime=runtime,
                executable=str(MODULE.XWAYLAND),
            )
            self.assertEqual(found.pid, 120)
        with (
            mock.patch.object(MODULE, "_has_private_runtime", return_value=True),
            mock.patch.object(
                MODULE,
                "_proc_tokens",
                side_effect=lambda pid: [str(MODULE.CLIENT)] if pid == 130 else ["other"],
            ),
        ):
            found = MODULE.unique_owned_process(
                processes,
                root_pid=100,
                runtime=runtime,
                command_token=str(MODULE.CLIENT),
            )
            self.assertEqual(found.pid, 130)

    def test_missing_and_ambiguous_owned_processes_fail_closed(self) -> None:
        uid = os.getuid()
        info = MODULE.ProcessInfo
        root = info(100, 1, 100, 1, uid, "/usr/bin/dbus-run-session")
        with self.assertRaises(MODULE.RoundFailure):
            MODULE.unique_owned_process(
                {100: root},
                root_pid=100,
                runtime=Path("/runtime"),
                executable=str(MODULE.XWAYLAND),
            )
        processes = {
            100: root,
            120: info(120, 100, 100, 2, uid, str(MODULE.XWAYLAND)),
            121: info(121, 100, 100, 3, uid, str(MODULE.XWAYLAND)),
        }
        with (
            mock.patch.object(MODULE, "_has_private_runtime", return_value=True),
            self.assertRaises(MODULE.HarnessError),
        ):
            MODULE.unique_owned_process(
                processes,
                root_pid=100,
                runtime=Path("/runtime"),
                executable=str(MODULE.XWAYLAND),
            )

    def test_process_survival_uses_stable_identity_not_parent(self) -> None:
        uid = os.getuid()
        expected = MODULE.ProcessInfo(120, 110, 100, 99, uid, str(MODULE.XWAYLAND))
        reparented = MODULE.ProcessInfo(120, 1, 100, 99, uid, str(MODULE.XWAYLAND))
        with mock.patch.object(MODULE, "process_info", return_value=reparented):
            self.assertTrue(MODULE.same_process(expected))
        reused = MODULE.ProcessInfo(120, 1, 120, 100, uid, str(MODULE.XWAYLAND))
        with mock.patch.object(MODULE, "process_info", return_value=reused):
            self.assertFalse(MODULE.same_process(expected))

    def test_cleanup_signal_tolerates_exit_but_refuses_runtime_escape(self) -> None:
        uid = os.getuid()
        expected = MODULE.ProcessInfo(120, 110, 100, 99, uid, str(MODULE.XWAYLAND))
        with (
            mock.patch.object(MODULE, "same_process", return_value=False),
            mock.patch.object(MODULE.os, "kill") as sent,
        ):
            MODULE._signal_exact_process(expected, Path("/private/runtime"), signal.SIGKILL)
            sent.assert_not_called()
        with (
            mock.patch.object(MODULE, "same_process", return_value=True),
            mock.patch.object(MODULE, "_has_private_runtime", return_value=False),
            mock.patch.object(MODULE.os, "kill") as sent,
            self.assertRaises(MODULE.HarnessError),
        ):
            MODULE._signal_exact_process(expected, Path("/private/runtime"), signal.SIGKILL)
        sent.assert_not_called()

    def test_cleanup_signal_tolerates_process_lookup_race(self) -> None:
        uid = os.getuid()
        expected = MODULE.ProcessInfo(120, 110, 100, 99, uid, str(MODULE.XWAYLAND))
        with (
            mock.patch.object(MODULE, "same_process", return_value=True),
            mock.patch.object(MODULE, "_has_private_runtime", return_value=True),
            mock.patch.object(MODULE.os, "kill", side_effect=ProcessLookupError),
        ):
            MODULE._signal_exact_process(expected, Path("/private/runtime"), signal.SIGKILL)


class EvaluationTests(unittest.TestCase):
    def test_three_clean_rounds_and_unchanged_host_pass(self) -> None:
        report = MODULE.evaluate_run(
            [healthy_round(), healthy_round(), healthy_round()],
            host_processes_unchanged=True,
            support_services_unchanged=True,
        )
        self.assertTrue(report["accepted"])
        self.assertEqual(report["passed_round_count"], 3)
        serialized = json.dumps(report)
        for private in ("/run/user/1000", "wayland-0", "DISPLAY=:7", "PID=123"):
            self.assertNotIn(private, serialized)

    def test_missing_round_runtime_fault_or_host_drift_is_incomplete(self) -> None:
        bad = healthy_round()
        bad["client_exited_within_bound"] = False
        bad["failure_stage"] = "client-exit-timeout"
        for rounds, host_ok, services_ok in (
            ([healthy_round(), healthy_round()], True, True),
            ([healthy_round(), healthy_round(), bad], True, True),
            ([healthy_round()] * 3, False, True),
            ([healthy_round()] * 3, True, False),
        ):
            with self.subTest(rounds=len(rounds), host=host_ok, services=services_ok):
                report = MODULE.evaluate_run(
                    rounds,
                    host_processes_unchanged=host_ok,
                    support_services_unchanged=services_ok,
                )
                self.assertFalse(report["accepted"])

    def test_wrong_round_schema_types_and_stages_are_rejected(self) -> None:
        bad = healthy_round()
        bad["unexpected"] = True
        with self.assertRaises(MODULE.HarnessError):
            MODULE.evaluate_run([bad], host_processes_unchanged=True, support_services_unchanged=True)
        bad = healthy_round()
        bad["probe_ready"] = 1
        with self.assertRaises(MODULE.HarnessError):
            MODULE.evaluate_run([bad], host_processes_unchanged=True, support_services_unchanged=True)
        bad = healthy_round()
        bad["failure_stage"] = "invented"
        with self.assertRaises(MODULE.HarnessError):
            MODULE.evaluate_run([bad], host_processes_unchanged=True, support_services_unchanged=True)


class FileCommandAndCliTests(unittest.TestCase):
    def test_probe_readiness_is_private_exact_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds004-client-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            ready = root / "client-ready.json"
            CLIENT.write_ready(ready)
            self.assertEqual(stat.S_IMODE(ready.stat().st_mode), 0o600)
            self.assertEqual(json.loads(ready.read_text()), MODULE.read_ready(ready))
            with self.assertRaises(FileExistsError):
                CLIENT.write_ready(ready)
            root.chmod(0o755)
            another = root / "client-ready.json"
            with self.assertRaises(CLIENT.ProbeError):
                CLIENT._private_parent(another)

    def test_safe_executable_rejects_mode_and_hardlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds004-exe-") as temporary:
            path = Path(temporary) / "probe"
            path.write_bytes(b"#!/bin/false\n")
            path.chmod(0o755)
            evidence = MODULE.safe_executable(path, expected_uid=os.getuid())
            self.assertEqual(evidence["mode"], "0755")
            path.chmod(0o775)
            with self.assertRaises(MODULE.HarnessError):
                MODULE.safe_executable(path, expected_uid=os.getuid())
            path.chmod(0o755)
            link = path.with_name("hardlink")
            os.link(path, link)
            with self.assertRaises(MODULE.HarnessError):
                MODULE.safe_executable(path, expected_uid=os.getuid())

    def test_command_is_virtual_rootless_fixed_and_shell_free(self) -> None:
        command = MODULE.build_command(MODULE.CLIENT)
        self.assertEqual(command[:3], [str(MODULE.DBUS_RUN_SESSION), "--", str(MODULE.KWIN)])
        for required in ("--virtual", "--xwayland", "--no-lockscreen", "--no-global-shortcuts"):
            self.assertIn(required, command)
        self.assertNotIn("--drm", command)
        self.assertNotIn("--libinput", command)
        self.assertEqual(command[-1], f"--exit-with-session={MODULE.CLIENT}")
        with self.assertRaises(MODULE.HarnessError):
            MODULE.build_command(Path("/tmp/unsafe path;command"))

    def test_cli_is_default_plan_and_execution_needs_all_gates(self) -> None:
        plan = MODULE.parse_args([])
        self.assertFalse(plan.execute)
        for argv in (
            ["--execute"],
            ["--execute", "--confirm", MODULE.CONFIRMATION],
            ["--execute", "--confirm", "wrong", "--output", "new.json"],
            ["--execute", "--confirm", MODULE.CONFIRMATION, "--output", "new.json", "--rounds", "2"],
            ["--output", "new.json"],
        ):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                MODULE.parse_args(argv)
        accepted = MODULE.parse_args(
            [
                "--execute",
                "--confirm",
                MODULE.CONFIRMATION,
                "--output",
                "new.json",
                "--rounds",
                "3",
            ]
        )
        self.assertTrue(accepted.execute)

    def test_report_is_private_new_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds004-report-") as temporary:
            path = Path(temporary) / "report.json"
            MODULE.write_report({"accepted": False}, path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                MODULE.write_report({"accepted": False}, path)

    def test_signal_calls_exist_only_inside_owned_boundary_functions(self) -> None:
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        callers: dict[str, set[str]] = {"kill": set(), "killpg": set()}
        for function in [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]:
            for node in ast.walk(function):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    if node.func.attr in callers:
                        callers[node.func.attr].add(function.name)
        self.assertEqual(callers["kill"], {"_signal_exact_process", "_run_round_in_root"})
        self.assertEqual(callers["killpg"], {"cleanup_owned_session"})
        source = SCRIPT.read_text(encoding="utf-8")
        client = CLIENT_SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("shell=True", "sudo", "/dev/input", "/dev/uinput", "requests.", "urllib"):
            self.assertNotIn(forbidden, source)
            self.assertNotIn(forbidden, client)
        for forbidden_client in ("subprocess", "pyatspi", "evdev", "systemctl"):
            self.assertNotIn(forbidden_client, client)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Offline trust-boundary and real-handler tests for controller capture."""

import ast
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "controller_test_gate", ROOT / "components/inputplumber/controller_test_gate.py"
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class GateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pds-gate-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.directory.chmod(0o755)
        self.path = self.directory / "state.json"
        owner = patch.object(gate, "ROOT_UID", os.getuid())
        owner.start()
        self.addCleanup(owner.stop)

    def write(self, *, blocked=True, status="active", **extra):
        self.path.write_text(json.dumps({
            "protocol": "pds-controller-test-v1", "status": status,
            "blocked": blocked, **extra,
        }))
        self.path.chmod(0o644)

    def test_legacy_missing_directory_and_state_allow_normal_actions(self):
        self.assertFalse(gate.hardware_actions_blocked(self.path))
        self.assertFalse(gate.hardware_actions_blocked(self.directory / "absent/state.json"))

    def test_all_capture_and_failed_restore_states_block(self):
        for status in ("starting", "active", "draining", "error"):
            with self.subTest(status=status):
                self.write(status=status, updated_monotonic=0.0)
                self.assertTrue(gate.hardware_actions_blocked(self.path))

    def test_only_explicit_idle_completed_restore_unblocks(self):
        self.write(status="idle", blocked=False)
        self.assertFalse(gate.hardware_actions_blocked(self.path))
        for status in ("starting", "active", "draining", "error", "unknown"):
            self.write(status=status, blocked=False)
            self.assertTrue(gate.hardware_actions_blocked(self.path))

    def test_malformed_untrusted_and_oversized_journals_fail_closed(self):
        for payload in (b"{", b"[]", b"{}", b"x" * 65537):
            self.path.write_bytes(payload)
            self.path.chmod(0o644)
            self.assertTrue(gate.hardware_actions_blocked(self.path))
        self.write(status="idle", blocked=False, protocol="another-protocol")
        self.assertTrue(gate.hardware_actions_blocked(self.path))
        self.write(status="idle", blocked=0)
        self.assertTrue(gate.hardware_actions_blocked(self.path))

    def test_writable_parent_or_file_and_wrong_owner_fail_closed(self):
        self.write(status="idle", blocked=False)
        self.path.chmod(0o666)
        self.assertTrue(gate.hardware_actions_blocked(self.path))
        self.path.chmod(0o644)
        self.directory.chmod(0o777)
        self.assertTrue(gate.hardware_actions_blocked(self.path))
        self.directory.chmod(0o755)
        with patch.object(gate, "ROOT_UID", os.getuid() + 1):
            self.assertTrue(gate.hardware_actions_blocked(self.path))

    def test_symlink_and_hardlink_are_rejected(self):
        self.write(status="idle", blocked=False)
        real = self.directory / "real.json"
        self.path.rename(real)
        self.path.symlink_to(real)
        self.assertTrue(gate.hardware_actions_blocked(self.path))
        self.path.unlink()
        os.link(real, self.path)
        self.assertTrue(gate.hardware_actions_blocked(self.path))

    def test_io_errors_do_not_reopen_actions(self):
        self.write(status="idle", blocked=False)
        with patch.object(gate.os, "read", side_effect=PermissionError):
            self.assertTrue(gate.hardware_actions_blocked(self.path))

    def test_missing_module_fallback_allows_only_journal_absence(self):
        for relative in ("components/keyboard/pocketds-keyboard.py",
                         "components/inputplumber/pocketds-mode-listener.py"):
            parsed = ast.parse((ROOT / relative).read_text())
            function = next(node for node in ast.walk(parsed)
                            if isinstance(node, ast.FunctionDef)
                            and node.name == "hardware_actions_blocked")
            path = Mock()
            namespace = {"Path": Mock(return_value=path)}
            exec(compile(ast.Module(body=[function], type_ignores=[]), relative, "exec"), namespace)
            for error, expected in ((FileNotFoundError, False), (PermissionError, True), (None, True)):
                with self.subTest(source=relative, error=error):
                    path.lstat.side_effect = error
                    self.assertEqual(namespace["hardware_actions_blocked"](), expected)


def load_listener():
    source = ROOT / "components/inputplumber/pocketds-mode-listener.py"
    parsed = ast.parse(source.read_text())
    statements = [node for node in parsed.body if isinstance(node, ast.FunctionDef)]
    statements += [node for node in parsed.body if isinstance(node, ast.Assign)
                   and all(isinstance(t, ast.Name) and t.id.isupper() for t in node.targets)]
    blocked = Mock(return_value=False)
    namespace = {"hardware_actions_blocked": blocked, "GLib": Mock(),
                 "subprocess": Mock(), "time": SimpleNamespace(monotonic=lambda: 20.0),
                 "dbus": Mock(), "bus": Mock()}
    exec(compile(ast.Module(body=statements, type_ignores=[]), str(source), "exec"), namespace)
    return namespace, blocked


class ListenerTests(unittest.TestCase):
    def test_capture_suppresses_all_regular_hardware_actions(self):
        listener, blocked = load_listener()
        blocked.return_value = True
        for action in ("ui_game_menu", "ui_mode_view", "ui_mode_equals",
                       "ui_brightness_up", "ui_brightness_down", "ui_osk",
                       "ui_window_close", "ui_task_switcher"):
            listener["on_input_event"](action, 1.0)
            listener["on_input_event"](action, 0.0)
        listener["subprocess"].Popen.assert_not_called()
        listener["dbus"].Interface.assert_not_called()

    def test_quiet_capture_clears_preexisting_held_chord_and_confirmation(self):
        listener, blocked = load_listener()
        listener["MODE_HELD"]["ui_mode_view"] = True
        listener["EXIT_HELD"]["ui_mode_view"] = True
        listener["EXIT_CONFIRM_UNTIL"] = 100.0
        blocked.return_value = True
        listener["observe_controller_test_gate"]()
        self.assertFalse(any(listener["MODE_HELD"].values()))
        self.assertFalse(any(listener["EXIT_HELD"].values()))
        self.assertEqual(listener["EXIT_CONFIRM_UNTIL"], 0.0)
        blocked.return_value = False
        listener["on_input_event"]("ui_mode_view", 0.0)
        listener["subprocess"].Popen.assert_not_called()
        listener["dbus"].Interface.assert_not_called()

    def test_action_execution_points_recheck_capture(self):
        listener, blocked = load_listener()
        blocked.return_value = True
        listener["send_button_chord"](["Gamepad:Button:Guide"])
        listener["invoke_kwin_shortcut"]("Window Close")
        listener["toggle_input_mode"]()
        listener["handle_exit_chord"]()
        listener["subprocess"].Popen.assert_not_called()
        listener["dbus"].Interface.assert_not_called()

    def test_brightness_route_is_preserved_without_capture(self):
        listener, _ = load_listener()
        listener["on_input_event"]("ui_brightness_up", 1.0)
        self.assertEqual(listener["subprocess"].Popen.call_args.args[0],
                         ["/usr/bin/pocketds-brightness", "+"])


class KeyboardTests(unittest.TestCase):
    def setUp(self):
        source = ROOT / "components/keyboard/pocketds-keyboard.py"
        parsed = ast.parse(source.read_text())
        actual = next(node for node in parsed.body if isinstance(node, ast.ClassDef)
                      and node.name == "PocketDSKeyboard")
        methods = {"on_hardware_event", "apply_hardware_toggle", "invoke_hardware_shortcut"}
        stub = ast.ClassDef(name="Keyboard", bases=[], keywords=[], decorator_list=[],
                            body=[node for node in actual.body
                                  if isinstance(node, ast.FunctionDef) and node.name in methods])
        module = ast.fix_missing_locations(ast.Module(body=[stub], type_ignores=[]))
        self.blocked = Mock(return_value=False)
        self.queued = []
        namespace = {"hardware_actions_blocked": self.blocked,
                     "GLib": SimpleNamespace(idle_add=lambda *args: self.queued.append(args)),
                     "time": SimpleNamespace(monotonic=lambda: 20.0),
                     "WINDOW_SHORTCUTS": {"ui_window_close": "Window Close"},
                     "WINDOW_ACTION_DEBOUNCE_S": 0.25}
        exec(compile(module, str(source), "exec"), namespace)
        self.keyboard = namespace["Keyboard"]()
        self.keyboard.last_hardware_toggle = 0.0
        self.keyboard.last_window_action = {}
        self.keyboard.manual_toggle = Mock()
        self.keyboard.invoke_kwin_shortcut = Mock()

    def test_capture_does_not_queue_osk_or_window_actions(self):
        self.blocked.return_value = True
        for action in ("ui_osk", "ui_window_close"):
            self.keyboard.on_hardware_event(action, 1.0)
        self.assertEqual(self.queued, [])

    def test_preexisting_idle_callbacks_recheck_capture_before_execution(self):
        for action in ("ui_osk", "ui_window_close"):
            self.keyboard.on_hardware_event(action, 1.0)
        self.assertEqual(len(self.queued), 2)
        self.blocked.return_value = True
        for callback, *args in self.queued:
            callback(*args)
        self.keyboard.manual_toggle.assert_not_called()
        self.keyboard.invoke_kwin_shortcut.assert_not_called()

    def test_normal_hardware_actions_are_preserved(self):
        for action in ("ui_osk", "ui_window_close"):
            self.keyboard.on_hardware_event(action, 1.0)
        for callback, *args in self.queued:
            callback(*args)
        self.keyboard.manual_toggle.assert_called_once_with()
        self.keyboard.invoke_kwin_shortcut.assert_called_once_with("Window Close")


if __name__ == "__main__":
    unittest.main(verbosity=2)

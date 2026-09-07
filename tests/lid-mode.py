#!/usr/bin/env python3
"""Local fixtures only: no session D-Bus, service changes, or sleep requests."""

from __future__ import annotations

import configparser
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("lid_mode", ROOT / "components/system/pocketds-lid-mode.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def parsed(path):
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(path)
    return {section: dict(parser[section]) for section in parser.sections()}


class FakeAdapter:
    def __init__(self):
        self.opens = [True]
        self.available = True
        self.refreshes = 0
        self.writes = 0
        self.fail_refresh = set()
        self.before_write = None
        self.on_refresh = None

    def key(self, path, profile, key):
        return parsed(path).get(profile + "][SuspendAndShutdown", {}).get(key, "")

    def write_key(self, path, profile, value):
        self.writes += 1
        if self.before_write:
            self.before_write()
        lines = path.read_text().splitlines(keepends=True)
        active = False
        for index, line in enumerate(lines):
            if line.startswith("["):
                active = line.strip() == f"[{profile}][SuspendAndShutdown]"
            elif active and line.startswith("LidAction="):
                lines[index] = f"LidAction={value}\n"
        path.write_text("".join(lines))

    def lid_open(self):
        return self.opens.pop(0) if len(self.opens) > 1 else self.opens[0]

    def sleep_available(self, path):
        if not self.available:
            raise M.ModeError("kernel_unverified", "Daily sleep verification did not pass")

    def refresh(self):
        self.refreshes += 1
        if self.on_refresh:
            self.on_refresh()
        if self.refreshes in self.fail_refresh:
            raise M.ModeError("refresh_failed", "PowerDevil did not reload")

    def rollback_adapter(self):
        return self


class LidModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "powerdevilrc"
        self.state = Path(self.temp.name) / "state"
        self.original = (ROOT / "components/system/powerdevilrc.deep").read_bytes()
        self.path.write_bytes(self.original)
        self.path.chmod(0o640)
        self.adapter = FakeAdapter()

    def tearDown(self):
        self.temp.cleanup()

    def change(self, mode="connected"):
        return M.set_mode(self.path, mode, self.adapter, self.state)

    def test_guard_failure_preserves_readable_mode_and_lid_status(self):
        self.adapter.available = False
        result = M.status(self.path, self.adapter)
        self.assertEqual(result["mode"], "sleep")
        self.assertTrue(result["lid_open"])
        self.assertFalse(result["sleep_available"])
        self.assertEqual(result["reason_code"], "kernel_unverified")

    def test_mixed_profiles_are_unknown(self):
        self.path.write_bytes(self.original.replace(b"LidAction=1", b"LidAction=0", 1))
        self.assertEqual(M.status(self.path, self.adapter)["mode"], "unknown")

    def test_fifo_is_rejected_without_waiting_for_a_writer(self):
        self.path.unlink()
        os.mkfifo(self.path)
        result = subprocess.run([sys.executable, str(ROOT / "components/system/pocketds-lid-mode.py"), "status"],
                                env={**os.environ, "XDG_CONFIG_HOME": str(self.path.parent)},
                                text=True, capture_output=True, timeout=1)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["reason_code"], "config_unavailable")

    def test_owner_query_does_not_read_lid_again(self):
        with mock.patch.object(self.adapter, "lid_open", side_effect=AssertionError("owner must not query lid")), \
                mock.patch.object(self.adapter, "sleep_available", side_effect=AssertionError("owner must not query guard")):
            result = M.status(self.path, self.adapter, include_lid=False)
        self.assertEqual(result["mode"], "sleep")
        self.assertFalse(result["sleep_available"])

    def test_connected_owner_skips_sleep_service_queries(self):
        self.path.write_bytes(self.original.replace(b"LidAction=1", b"LidAction=0"))
        with mock.patch.object(self.adapter, "sleep_available", side_effect=AssertionError("connected mode must return immediately")):
            result = M.status(self.path, self.adapter, include_lid=False)
        self.assertEqual(result["mode"], "connected")
        self.assertFalse(result["sleep_available"])

    def test_connected_only_changes_lid_keys_even_when_sleep_blocked(self):
        before = parsed(self.path)
        self.adapter.available = False
        self.assertEqual(self.change(), {"mode": "connected", "changed": True})
        after = parsed(self.path)
        for profile in M.PROFILES:
            before[profile + "][SuspendAndShutdown"]["LidAction"] = "0"
        self.assertEqual(after, before)
        self.assertEqual((self.state / "powerdevilrc.before-lid-mode").read_bytes(), self.original)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.adapter.refreshes, 1)

    def test_sleep_denied_changes_nothing(self):
        self.path.write_bytes(self.original.replace(b"LidAction=1", b"LidAction=0"))
        previous = self.path.read_bytes()
        self.adapter.available = False
        with self.assertRaisesRegex(M.ModeError, "verification"):
            self.change("sleep")
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(self.adapter.refreshes, 0)

    def test_sleep_allowed_updates_all_profiles(self):
        self.path.write_bytes(self.original.replace(b"LidAction=1", b"LidAction=0"))
        self.change("sleep")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_existing_mode_can_retry_powerdevil_reload(self):
        self.assertEqual(self.change("sleep"), {"mode": "sleep", "changed": False})
        self.assertEqual(self.adapter.refreshes, 1)
        self.assertFalse((self.state / "powerdevilrc.before-lid-mode").exists())

    def test_closed_lid_rejected_before_editing(self):
        self.adapter.opens = [False]
        with self.assertRaises(M.ModeError) as error:
            self.change()
        self.assertEqual(error.exception.code, "lid_closed")
        self.assertEqual(self.adapter.writes, 0)

    def test_lid_close_during_preparation_leaves_original(self):
        self.adapter.opens = [True, False]
        with self.assertRaises(M.ModeError):
            self.change()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.adapter.refreshes, 0)

    def test_lid_close_after_commit_restores_without_triggering_original_sleep(self):
        self.adapter.opens = [True, True, False]
        with self.assertRaises(M.ModeError) as error:
            self.change()
        self.assertEqual(error.exception.code, "rollback_reload_failed")
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.adapter.refreshes, 0)

    def test_refresh_failure_restores_original_and_reloads(self):
        self.adapter.fail_refresh = {1}
        with self.assertRaises(M.ModeError) as error:
            self.change()
        self.assertEqual(error.exception.code, "refresh_failed")
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.adapter.refreshes, 2)

    def test_failed_rollback_reload_still_restores_file(self):
        self.adapter.fail_refresh = {1, 2}
        with self.assertRaises(M.ModeError) as error:
            self.change()
        self.assertEqual(error.exception.code, "rollback_reload_failed")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_native_writer_failure_never_publishes_partial_profile_change(self):
        def fail_second_write():
            if self.adapter.writes == 2:
                raise M.ModeError("config_unavailable", "Fixture writer failed")
        self.adapter.before_write = fail_second_write
        with self.assertRaises(M.ModeError):
            self.change()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.adapter.refreshes, 0)

    def test_concurrent_external_edit_before_commit_is_preserved(self):
        external = self.original + b"\n# external settings change\n"
        self.adapter.before_write = lambda: self.path.write_bytes(external)
        with self.assertRaises(M.ModeError) as error:
            self.change()
        self.assertEqual(error.exception.code, "config_changed")
        self.assertEqual(self.path.read_bytes(), external)

    def test_concurrent_external_edit_during_reload_is_not_rolled_back(self):
        external = self.original + b"\n# external settings change\n"
        self.adapter.on_refresh = lambda: self.path.write_bytes(external)
        with self.assertRaises(M.ModeError) as error:
            self.change()
        self.assertEqual(error.exception.code, "rollback_conflict")
        self.assertEqual(self.path.read_bytes(), external)
        self.assertEqual((self.state / "powerdevilrc.before-lid-mode").read_bytes(), self.original)

    def test_parallel_click_is_rejected_under_transaction_lock(self):
        self.state.mkdir()
        with (self.state / "lid-mode.lock").open("w") as locked:
            fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(M.ModeError) as error:
                self.change()
        self.assertEqual(error.exception.code, "busy")
        self.assertEqual(self.adapter.writes, 0)

    def test_symlink_configuration_and_lock_are_rejected(self):
        target = self.path.with_name("target")
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            self.change()
        self.assertEqual(target.read_bytes(), self.original)
        self.path.unlink()
        target.rename(self.path)
        (self.state / "lid-mode.lock").unlink()
        (self.state / "lid-mode.lock").symlink_to(target)
        with self.assertRaises(OSError):
            self.change()

    @unittest.skipUnless(shutil.which("kreadconfig6") and shutil.which("kwriteconfig6"), "KConfig tools only on Linux runner")
    def test_real_kconfig_writer_preserves_unrelated_values(self):
        class KConfigAdapter(FakeAdapter):
            def __init__(self):
                super().__init__()
                self.runtime = M.LiveAdapter()
            def key(self, path, profile, key):
                return M.LiveAdapter.key(self.runtime, path, profile, key)
            def write_key(self, path, profile, value):
                M.LiveAdapter.write_key(self.runtime, path, profile, value)
        before = parsed(self.path)
        self.adapter = KConfigAdapter()
        self.change()
        for profile in M.PROFILES:
            before[profile + "][SuspendAndShutdown"]["LidAction"] = "0"
        self.assertEqual(parsed(self.path), before)


class NativeQueryTests(unittest.TestCase):
    def test_busctl_property_scalar_and_method_tuple_match_device_replies(self):
        fixtures = [
            ("get-property", "LidClosed", "b", {"type": "b", "data": False}, False),
            ("get-property", "LidClosed", "b", {"type": "b", "data": True}, True),
            ("call", "CanSuspend", "s", {"type": "s", "data": ["yes"]}, "yes"),
            ("call", "CanSuspend", "s", {"type": "s", "data": ["no"]}, "no"),
        ]
        for operation, member, kind, payload, expected in fixtures:
            adapter = M.LiveAdapter()
            reply = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
            with self.subTest(payload=payload), mock.patch.object(adapter, "run", return_value=reply):
                self.assertEqual(adapter.bus_value(operation, member, kind), expected)

    def test_busctl_wrong_scalar_or_tuple_shapes_are_rejected(self):
        for operation, member, kind, data in [
            ("get-property", "LidClosed", "b", [False]),
            ("get-property", "LidClosed", "b", 0),
            ("get-property", "LidClosed", "b", None),
            ("call", "CanSuspend", "s", "yes"),
            ("call", "CanSuspend", "s", []),
            ("call", "CanSuspend", "s", ["yes", "no"]),
        ]:
            adapter = M.LiveAdapter()
            reply = subprocess.CompletedProcess([], 0, json.dumps({"type": kind, "data": data}), "")
            with self.subTest(data=data), mock.patch.object(adapter, "run", return_value=reply):
                with self.assertRaises(M.ModeError):
                    adapter.bus_value(operation, member, kind)

    def test_capability_rejects_no_challenge_na_and_malformed_values(self):
        for value in ("no", "challenge", "na", True, 1):
            adapter = M.LiveAdapter()
            def reply(args):
                if "check" in args:
                    return subprocess.CompletedProcess(args, 0, '{"eligible":true}', "")
                return subprocess.CompletedProcess(args, 0, json.dumps({"type": "s", "data": [value]}), "")
            with self.subTest(value=value), mock.patch.object(adapter, "run", side_effect=reply), mock.patch.object(adapter, "key", return_value="1"):
                with self.assertRaises(M.ModeError):
                    adapter.sleep_available(Path("unused"))

    def test_guard_failure_does_not_probe_further_capabilities(self):
        cases = [("kernel notes differ", "kernel_unverified"),
                 ("daily deep sleep is not opted in", "not_enabled"),
                 ("another suspend is blocked after a failed cycle", "previous_cycle_failed")]
        for reason, expected in cases:
            adapter = M.LiveAdapter()
            result = subprocess.CompletedProcess([], 1, "", json.dumps({"reason": reason}))
            with self.subTest(reason=reason), mock.patch.object(adapter, "run", return_value=result) as run:
                with self.assertRaises(M.ModeError) as error:
                    adapter.sleep_available(Path("unused"))
                self.assertEqual(error.exception.code, expected)
                self.assertEqual(run.call_count, 1)

    def test_hybrid_sleep_setting_is_rejected_without_changing_it(self):
        adapter = M.LiveAdapter()
        result = subprocess.CompletedProcess([], 0, '{"eligible":true}', "")
        with mock.patch.object(adapter, "run", return_value=result), mock.patch.object(adapter, "key", return_value="2"):
            with self.assertRaises(M.ModeError) as error:
                adapter.sleep_available(Path("unused"))
        self.assertEqual(error.exception.code, "unsupported_sleep_mode")

    def test_expired_query_budget_never_starts_another_process(self):
        adapter = M.LiveAdapter(-1)
        with mock.patch.object(M.subprocess, "run") as run:
            with self.assertRaises(M.ModeError):
                adapter.run(["unused"])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

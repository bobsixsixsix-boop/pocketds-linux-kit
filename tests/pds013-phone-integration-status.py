#!/usr/bin/env python3
"""Test the PDS-013 privacy-minimal phone integration preflight."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pds013-phone-integration-status.py"
SPEC = importlib.util.spec_from_file_location("pds013_phone_status", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


PASS_RESULTS = {
    "kde_connect_rpm": (0, b""),
    "kdeconnectd_rpm": (0, b""),
    "kdeconnect_daemon": (0, b"b true\n"),
    "firewalld": (0, b"running\n"),
    "trusted_zone": (0, b"yes\n"),
    "public_zone": (1, b"no\n"),
    "kdeconnect_ports": (0, b"1714-1764/udp 1714-1764/tcp\n"),
    "localsend_rpm": (1, b""),
}


class FakeRunner:
    def __init__(self, overrides=None):
        self.results = dict(PASS_RESULTS)
        self.results.update(overrides or {})
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        command_id = next(
            key for key, expected in MODULE.COMMANDS.items() if expected == command
        )
        result = self.results[command_id]
        if isinstance(result, Exception):
            raise result
        return MODULE.ProbeResult(*result)


class PreflightTests(unittest.TestCase):
    def test_passing_fixture_is_privacy_minimal_and_not_physical_acceptance(self):
        runner = FakeRunner()
        report = MODULE.build_report(runner)
        self.assertTrue(report["preflight_accepted"])
        self.assertTrue(report["read_only"])
        self.assertEqual(report["physical_phone_acceptance"], "not_run")
        self.assertEqual(
            report["privacy"],
            {
                "network_identity_collected": False,
                "peer_identity_collected": False,
                "credentials_collected": False,
            },
        )
        self.assertEqual(set(runner.commands), set(MODULE.COMMANDS.values()))

    def test_each_failed_or_unavailable_probe_fails_closed(self):
        for command_id in MODULE.COMMANDS:
            with self.subTest(command_id=command_id):
                runner = FakeRunner({command_id: MODULE.ProbeError("synthetic")})
                self.assertFalse(MODULE.build_report(runner)["preflight_accepted"])

    def test_unexpected_text_non_ascii_and_extra_ports_fail_closed(self):
        cases = {
            "daemon-text": {"kdeconnect_daemon": (0, b"b false\n")},
            "trusted-nonascii": {"trusted_zone": (0, b"\xff\n")},
            "public-return-code": {"public_zone": (0, b"no\n")},
            "extra-port": {
                "kdeconnect_ports": (
                    0,
                    b"1714-1764/tcp 1714-1764/udp 53317/tcp\n",
                )
            },
            "package-output": {"kde_connect_rpm": (0, b"private canary\n")},
        }
        for label, overrides in cases.items():
            with self.subTest(label=label):
                report = MODULE.build_report(FakeRunner(overrides))
                self.assertFalse(report["preflight_accepted"])
                self.assertNotIn("private canary", json.dumps(report))

    def test_command_allowlist_cannot_enumerate_network_or_peers_or_mutate(self):
        commands = [token for command in MODULE.COMMANDS.values() for token in command]
        forbidden_programs = {
            "nmcli",
            "ip",
            "ss",
            "kdeconnect-cli",
            "hostname",
            "resolvectl",
        }
        forbidden_tokens = {
            "pair",
            "send",
            "modify",
            "add",
            "remove",
            "reload",
            "restart",
            "enable",
            "disable",
        }
        self.assertTrue(forbidden_programs.isdisjoint(commands))
        self.assertTrue(forbidden_tokens.isdisjoint(commands))
        self.assertNotIn("53317", " ".join(commands))

    def test_report_file_is_private_and_never_overwritten(self):
        report = MODULE.build_report(FakeRunner())
        with tempfile.TemporaryDirectory(prefix="pds013-report-") as temporary:
            output = Path(temporary) / "status.json"
            MODULE.write_report(report, str(output))
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)
            with self.assertRaises(FileExistsError):
                MODULE.write_report(report, str(output))


if __name__ == "__main__":
    unittest.main(verbosity=2)

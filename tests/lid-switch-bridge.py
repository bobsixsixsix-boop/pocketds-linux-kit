#!/usr/bin/env python3
"""Contract tests for the conditional Pocket DS Hall sensor bridge."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "components/system/pocketds-lid-switch.py"
SERVICE = ROOT / "components/system/pocketds-lid-switch.service"
if "evdev" not in sys.modules:
    fake_evdev = types.ModuleType("evdev")
    fake_evdev.UInput = object
    fake_evdev.ecodes = types.SimpleNamespace(EV_SW=5, SW_LID=0)
    sys.modules["evdev"] = fake_evdev
SPEC = importlib.util.spec_from_file_location("pocketds_lid_switch", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class LidSwitchTests(unittest.TestCase):
    def _capability(
        self,
        root: Path,
        event: str,
        value: str,
        *,
        name: str = "Test device",
        phys: str = "test/input0",
    ) -> None:
        path = root / event / "device/capabilities"
        path.mkdir(parents=True)
        (path / "sw").write_text(value + "\n", encoding="ascii")
        (path.parent / "name").write_text(name + "\n", encoding="utf-8")
        (path.parent / "phys").write_text(phys + "\n", encoding="utf-8")

    def test_native_lid_detection_checks_only_sw_lid_bit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pocketds-lid-") as temporary:
            root = Path(temporary)
            self._capability(root, "event1", "d4")
            self.assertFalse(MODULE.native_lid_present(root))
            self._capability(
                root,
                "event2",
                "1",
                name="Pocket DS Lid Switch",
                phys="py-evdev-uinput",
            )
            self.assertFalse(MODULE.native_lid_present(root))
            self._capability(root, "event3", "1")
            self.assertTrue(MODULE.native_lid_present(root))

    def test_gpio_level_is_strict_and_active_low(self) -> None:
        for value, expected in (("0\n", True), ("1\n", False)):
            completed = subprocess.CompletedProcess([], 0, stdout=value, stderr="")
            with mock.patch.object(MODULE.subprocess, "run", return_value=completed) as run:
                self.assertEqual(MODULE.read_lid_closed(), expected)
                self.assertEqual(run.call_args.args[0][0], "/usr/bin/gpioget")

        completed = subprocess.CompletedProcess([], 0, stdout="2\n", stderr="")
        with mock.patch.object(MODULE.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "invalid level"):
                MODULE.read_lid_closed()

    def test_service_is_conditional_hardened_and_installed(self) -> None:
        service = SERVICE.read_text(encoding="utf-8")
        installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        self.assertIn("ConditionPathExists=/dev/gpiochip4", service)
        self.assertIn("DeviceAllow=/dev/gpiochip4 rw", service)
        self.assertIn("DeviceAllow=/dev/uinput rw", service)
        self.assertIn("NoNewPrivileges=yes", service)
        self.assertIn("enable --now pocketds-lid-switch.service", installer)
        self.assertNotIn("rm -f /etc/systemd/system/pocketds-lid-switch.service", installer)

    def test_runtime_check_does_not_bind_lid_state_to_display_readiness(self) -> None:
        check = (ROOT / "scripts/check.sh").read_text(encoding="utf-8")
        self.assertIn('assert isinstance(data.get("lid_closed"), bool)', check)
        self.assertNotIn('assert data.get("okay") is True', check)
        self.assertIn("lid_status=$($lid_helper check 2>/dev/null || true)", check)
        self.assertIn("lid helper exposes a readable Pocket DS lid state", check)


if __name__ == "__main__":
    unittest.main(verbosity=2)

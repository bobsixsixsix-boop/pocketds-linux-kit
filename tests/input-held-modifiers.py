#!/usr/bin/env python3
"""Pure/static tests for the read-only held-modifier observer."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/pocketds-input-held-modifiers.py"
SPEC = importlib.util.spec_from_file_location("pocketds_input_held_modifiers", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class BitmapTests(unittest.TestCase):
    def test_empty_and_multiple_modifiers(self) -> None:
        self.assertEqual(module.held_modifier_names(bytes(module.BITMAP_BYTES)), [])
        bitmap = bytearray(module.BITMAP_BYTES)
        for code in (29, 56, 126):
            bitmap[code // 8] |= 1 << (code % 8)
        self.assertEqual(
            module.held_modifier_names(bytes(bitmap)),
            ["KEY_LEFTCTRL", "KEY_LEFTALT", "KEY_RIGHTMETA"],
        )

    def test_ioctl_number_is_linux_read_direction_and_size_bound(self) -> None:
        request = module.eviocgkey(module.BITMAP_BYTES)
        self.assertEqual(request >> 30, 2)
        self.assertEqual((request >> 16) & 0x3FFF, module.BITMAP_BYTES)
        self.assertEqual((request >> 8) & 0xFF, ord("E"))
        self.assertEqual(request & 0xFF, 0x18)


class StaticTests(unittest.TestCase):
    def test_source_has_no_input_or_service_mutation(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "EVIOCSKEY",
            "UINPUT",
            "systemctl",
            "subprocess",
            "sudo ",
            "O_WRONLY",
            "O_RDWR",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('"read_only": True', source)
        self.assertIn("--require-clear", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

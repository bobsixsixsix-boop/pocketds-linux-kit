#!/usr/bin/env python3
"""Pure fixtures: quirk transaction rollback and capability safety boundaries."""

from __future__ import annotations

import ctypes
import importlib.util
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSTALL = load("libinput_lid_install", "install-libinput-lid.py")
CHECK = load("libinput_lid_check", "check-libinput-lid.py")


class QuirkTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.data = self.base / "system"
        self.data.mkdir()
        (self.data / "10-generic.quirks").write_text("[Generic]\nMatchName=mouse\nAttrEventCode=-BTN_MIDDLE;\n")
        self.override = self.base / "local-overrides.quirks"
        self.override.write_bytes(b"# Existing local settings must survive verbatim.\n")
        self.override.chmod(0o640)
        self.backups = self.base / "backups"
        self.target = self.data / INSTALL.NAME
        self.patchers = [mock.patch.object(INSTALL, key, value) for key, value in (
            ("DATA_DIR", self.data), ("OVERRIDE", self.override), ("BACKUPS", self.backups))]
        for patcher in self.patchers:
            patcher.start()
        self.calls = 0
        self.fail_on = set()
        self.on_call = None
        self.checker = mock.patch.object(INSTALL, "check", self.fake_check)
        self.checker.start()

    def tearDown(self):
        self.checker.stop()
        for patcher in self.patchers:
            patcher.stop()
        self.temp.cleanup()

    def fake_check(self, quirks_dir=None, expected="filtered"):
        self.calls += 1
        if self.on_call:
            self.on_call(self.calls, quirks_dir)
        if self.calls in self.fail_on:
            raise RuntimeError("simulated native parser or capability failure")
        return {"ok": True, "lid": expected in ("any", "native")}

    def prior(self):
        self.target.write_bytes(b"# Previous distribution/site quirk\n")
        self.target.chmod(0o640)
        return INSTALL.snapshot(self.target)

    def test_success_retains_overrides_and_explicit_restore_preserves_metadata(self):
        previous = self.prior()
        override = INSTALL.snapshot(self.override)
        result = INSTALL.install()
        self.assertEqual(self.target.read_bytes(), INSTALL.SOURCE.read_bytes())
        self.assertEqual(INSTALL.snapshot(self.override), override)
        self.assertFalse(result["live_kwin_changed"])
        self.assertTrue(result["requires_new_kwin_context"])
        INSTALL.restore(Path(result["backup"]))
        self.assertEqual(INSTALL.snapshot(self.target), previous)
        self.assertEqual(INSTALL.snapshot(self.override), override)

    def test_staged_parser_failure_does_not_publish_or_create_backup(self):
        previous = self.prior()
        self.fail_on = {2}
        with self.assertRaisesRegex(RuntimeError, "simulated native"):
            INSTALL.install()
        self.assertEqual(INSTALL.snapshot(self.target), previous)
        self.assertFalse(self.backups.exists())

    def test_post_install_failure_restores_bytes_and_mode(self):
        previous = self.prior()
        self.fail_on = {3}
        with self.assertRaises(RuntimeError):
            INSTALL.install()
        self.assertEqual(INSTALL.snapshot(self.target), previous)
        self.assertEqual(len(list(self.backups.glob("*/manifest.json"))), 1)

    def test_post_install_failure_removes_new_file(self):
        self.fail_on = {3}
        with self.assertRaises(RuntimeError):
            INSTALL.install()
        self.assertFalse(self.target.exists())

    def test_restore_rejects_external_edit(self):
        result = INSTALL.install()
        self.target.write_bytes(b"# Changed by administrator after installation\n")
        with self.assertRaisesRegex(RuntimeError, "changed since backup"):
            INSTALL.restore(Path(result["backup"]))
        self.assertIn(b"administrator", self.target.read_bytes())

    def test_failed_restore_check_keeps_working_installed_file(self):
        self.prior()
        result = INSTALL.install()
        installed = INSTALL.snapshot(self.target)
        self.fail_on = {5}
        with self.assertRaises(RuntimeError):
            INSTALL.restore(Path(result["backup"]))
        self.assertEqual(INSTALL.snapshot(self.target), installed)

    def test_unrelated_database_change_during_preflight_aborts(self):
        def change(call, directory):
            if call == 2:
                (self.data / "10-generic.quirks").write_text("# Another installer updated this file\n")
        self.on_call = change
        with self.assertRaisesRegex(RuntimeError, "changed during preflight"):
            INSTALL.install()
        self.assertFalse(self.target.exists())

    def test_staging_includes_unmodified_database_and_local_override_last(self):
        (self.data / "zzzz-user.quirks").write_bytes(b"# Late system rule\n")
        self.override.write_bytes(b"[User final]\nMatchName=gpio-keys\nAttrEventCode=+SW_LID;\n")
        staged = self.base / "staged"
        staged.mkdir()
        state = INSTALL.database()
        INSTALL.stage(staged, state, INSTALL.SOURCE.read_bytes())
        self.assertEqual((staged / "10-generic.quirks").read_bytes(), state["files"]["10-generic.quirks"]["data"])
        last = sorted(staged.iterdir())[-1]
        self.assertEqual(last.read_bytes(), self.override.read_bytes())
        self.assertEqual((staged / INSTALL.NAME).read_bytes(), INSTALL.SOURCE.read_bytes())

    def test_symlink_override_is_rejected_before_any_write(self):
        self.override.unlink()
        self.override.symlink_to(self.data / "10-generic.quirks")
        with self.assertRaisesRegex(RuntimeError, "regular file"):
            INSTALL.install()
        self.assertFalse(self.target.exists())
        self.assertEqual(self.calls, 0)


class CapabilityTests(unittest.TestCase):
    def result(self, **fields):
        return dict(fresh_context=True, raw_lid=True, raw_volume_up=True,
                    keyboard=True, volume_up=True, lid=False, errors=[], **fields)

    def test_filtered_lid_requires_keyboard_and_volume_key(self):
        valid = self.result()
        CHECK.validate(valid, "filtered")
        for key in ("keyboard", "volume_up", "raw_lid", "raw_volume_up"):
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                CHECK.validate({**valid, key: False}, "filtered")
        with self.assertRaises(RuntimeError):
            CHECK.validate({**valid, "lid": True}, "filtered")
        with self.assertRaises(RuntimeError):
            CHECK.validate({**valid, "errors": ["parser failure"]}, "filtered")

    def test_native_lid_required_when_restoring_native_configuration(self):
        with self.assertRaises(RuntimeError):
            CHECK.validate(self.result(), "native")
        CHECK.validate({**self.result(), "lid": True}, "native")

    def test_sysfs_masks_keep_full_ulong_width_for_zero_words(self):
        bits = ctypes.sizeof(ctypes.c_ulong) * 8
        words = [0] * (115 // bits + 1)
        words[115 // bits] = 1 << (115 % bits)
        mask = " ".join(f"{word:x}" for word in words[::-1])
        self.assertTrue(CHECK.bit_set(mask, 115))
        self.assertFalse(CHECK.bit_set(mask, 114))

    def test_device_tree_and_mixed_device_must_both_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            sysfs = Path(temporary)
            compatible = sysfs / "firmware/devicetree/base/compatible"
            compatible.parent.mkdir(parents=True)
            compatible.write_bytes(b"ayaneo,pocketds\0qcom,sm8550\0")
            device = sysfs / "class/input/event19/device"
            (device / "capabilities").mkdir(parents=True)
            (device / "name").write_text("gpio-keys\n")
            (device / "capabilities/sw").write_text("1\n")
            bits = ctypes.sizeof(ctypes.c_ulong) * 8
            words = [0] * (115 // bits + 1)
            words[115 // bits] = 1 << (115 % bits)
            (device / "capabilities/key").write_text(" ".join(f"{word:x}" for word in words[::-1]))
            self.assertEqual(CHECK.find_device(sysfs), Path("/dev/input/event19"))
            compatible.write_bytes(b"another,vendor\0ayaneo,pocketds\0")
            with self.assertRaisesRegex(RuntimeError, "device tree"):
                CHECK.find_device(sysfs)


class ApiFunction:
    """Callable CDLL symbol with ctypes signature attributes."""
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


class LibinputAbiFixture:
    """Model the external libinput ABI, independently of CHECK constants.

    The native header assigns LID=1, while linux/input-event-codes.h assigns
    SW_LID=0. Upstream evdev_device_has_switch rejects unknown libinput enums
    and devices lacking CAP_SWITCH with -1, rather than a false capability.
    """
    def __init__(self, switches=(1,), keyboard=True, volume=True):
        self.switches = set(switches)
        self.calls = []
        self.libinput_path_create_context = ApiFunction(lambda *args: 101)
        self.libinput_path_add_device = ApiFunction(lambda *args: 102)
        self.libinput_path_remove_device = ApiFunction(lambda handle: self.calls.append(('remove', handle)))
        self.libinput_unref = ApiFunction(lambda handle: self.calls.append(('unref', handle)))
        self.libinput_log_set_handler = ApiFunction(lambda *args: None)
        self.libinput_device_has_capability = ApiFunction(
            lambda handle, capability: int(keyboard) if capability == 0 else int(bool(self.switches)) if capability == 6 else 0)
        self.libinput_device_keyboard_has_key = ApiFunction(
            lambda handle, key: int(volume and key == 115) if keyboard else -1)
        self.libinput_device_switch_has_switch = ApiFunction(self.has_switch)

    def has_switch(self, handle, switch):
        self.calls.append(('switch', switch))
        if not self.switches or switch not in (1, 2, 3):
            return -1
        return int(switch in self.switches)


class NativeAbiTests(unittest.TestCase):
    def probe(self, library):
        with mock.patch.object(CHECK.ctypes.util, 'find_library', return_value='fixture-libinput'), \
                mock.patch.object(CHECK.C, 'CDLL', return_value=library), mock.patch.dict(os.environ):
            return CHECK.probe(Path('/dev/input/fixture'))

    def test_unfiltered_mixed_device_requires_libinput_lid_enum(self):
        library = LibinputAbiFixture()
        result = self.probe(library)
        self.assertTrue(result['lid'])
        self.assertTrue(result['keyboard'])
        self.assertTrue(result['volume_up'])
        CHECK.validate(result, 'native')
        self.assertEqual(library.calls, [('switch', 1), ('remove', 102), ('unref', 101)])

    def test_filtering_last_switch_retains_volume_and_does_not_call_invalid_switch_api(self):
        library = LibinputAbiFixture(switches=())
        result = self.probe(library)
        CHECK.validate(result, 'filtered')
        self.assertFalse(result['switch'])
        self.assertEqual(library.calls, [('remove', 102), ('unref', 101)])

    def test_other_switch_remains_but_lid_is_filtered(self):
        library = LibinputAbiFixture(switches=(2,))
        result = self.probe(library)
        CHECK.validate(result, 'filtered')
        self.assertTrue(result['switch'])
        self.assertEqual(library.calls[0], ('switch', 1))

    def test_invalid_switch_argument_cannot_be_accepted_as_filtered(self):
        library = LibinputAbiFixture()
        with mock.patch.object(CHECK, 'LIBINPUT_SWITCH_LID', 0):
            with self.assertRaisesRegex(RuntimeError, 'invalid capability result: -1'):
                self.probe(library)
        self.assertEqual(library.calls[-2:], [('remove', 102), ('unref', 101)])

    def test_header_enum_matches_the_installed_native_c_header(self):
        compiler = shutil.which('cc')
        if not compiler:
            self.skipTest('native C compiler unavailable')
        pkg_config = shutil.which('pkg-config')
        if not pkg_config:
            self.skipTest('pkg-config unavailable for native header discovery')
        available = subprocess.run([pkg_config, '--exists', 'libinput'],
                                   capture_output=True, timeout=5)
        if available.returncode:
            self.skipTest('libinput development package unavailable')
        cflags = shlex.split(subprocess.check_output(
            [pkg_config, '--cflags', 'libinput'], text=True, timeout=5))
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / 'abi.c'
            binary = Path(directory) / 'abi'
            program.write_text('#include <libinput.h>\n#include <stdio.h>\n'
                               'int main(void) { printf("%d %d %d\\n", LIBINPUT_SWITCH_LID, '
                               'LIBINPUT_DEVICE_CAP_KEYBOARD, LIBINPUT_DEVICE_CAP_SWITCH); return 0; }\n')
            built = subprocess.run([compiler, *cflags, str(program), '-o', str(binary)],
                                   capture_output=True, text=True, timeout=15)
            if built.returncode:
                self.fail(built.stderr)
            output = subprocess.check_output([str(binary)], text=True).strip()
            self.assertEqual(tuple(map(int, output.split())),
                             (CHECK.LIBINPUT_SWITCH_LID, CHECK.CAP_KEYBOARD, CHECK.CAP_SWITCH))


if __name__ == "__main__":
    unittest.main()

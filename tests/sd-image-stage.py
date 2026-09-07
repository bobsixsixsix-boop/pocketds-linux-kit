#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the offline image boundary against disposable synthetic roots."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sd_stage", REPO / "scripts/pocketds-sd-image-stage.py")
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class StageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "root"
        self.source = self.base / "source"
        self.root.mkdir()
        self.source.mkdir()
        self.inventory = json.loads((REPO / "packaging/sd-image/kit-files.json").read_text())
        names = {row["source"] for row in self.inventory["files"]} | {"packaging/sd-image/kit-files.json"}
        for name in names:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, path)
        (self.source / "components/assets").mkdir(parents=True, exist_ok=True)
        (self.source / "components/assets/ASSETS.json").write_text(json.dumps({"schema": 1, "profile": "asset-free", "assets": []}))
        def write(name, data):
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        write("etc/pocketds-image-build-root", stage.MARKER)
        write("usr/lib/os-release", b"ID=fedora\nVERSION_ID=44\n")
        write("etc/passwd", b"pocketds:x:1000:1000::/home/pocketds:/bin/bash\n")
        write("etc/group", b"pocketds:x:1000:\n")
        write("etc/shadow", b"pocketds:!:20000:0:99999:7:::\n")
        (self.root / "home/pocketds").mkdir(parents=True)
        for name in stage.COMMANDS:
            write("usr/bin/" + name, b"fixture\n")
        write("usr/lib64/libseccomp.so.2", b"fixture\n")
        for name in ("usr/lib/systemd/user/filter-chain.service", "usr/share/applications/org.fcitx.Fcitx5.desktop", "usr/libexec/pocketds-fancontrol-set-profile", "usr/sbin/chpasswd"):
            write(name, b"fixture\n")
        for name in stage.SYSTEM_UNITS:
            write("usr/lib/systemd/system/" + name, b"[Unit]\nDescription=Fixture\n")
        self.binary = self.base / "native-aarch64"
        elf = bytearray(64)
        elf[:6] = b"\x7fELF\x02\x01"
        elf[18:20] = (183).to_bytes(2, "little")
        self.binary.write_bytes(elf)

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self):
        return stage.plan(self.root, self.source, self.binary, self.binary)

    def test_complete_inventory_no_host_mutation(self):
        plan = self.plan()
        with patch.object(os, "chown"):
            result = stage.apply(self.root, plan)
        self.assertEqual(len(result["files"]), len(self.inventory["files"]) + 8)
        self.assertFalse(result["api_configured"])
        self.assertFalse(result["deep_suspend_enabled"])
        self.assertIn(b"AllowSuspend=no", (self.root / "etc/systemd/sleep.conf.d/80-pocketds-sleep.conf").read_bytes())
        self.assertFalse((self.root / "home/pocketds/.config/pocketds-keyboard/asr-api/config.json").exists())
        self.assertEqual(os.readlink(self.root / "etc/systemd/system/power-profiles-daemon.service"), "/dev/null")
        self.assertEqual(os.readlink(self.root / "etc/systemd/system/plasma-setup.service"), "/dev/null")
        rime = (self.root / "home/pocketds/.local/share/fcitx5/rime/default.custom.yaml").read_text()
        schemas = [line.strip().split(": ", 1)[1] for line in rime.splitlines() if line.strip().startswith("- schema: ")]
        self.assertEqual(schemas, ["luna_pinyin_simp", "luna_pinyin"])
        paths = {r["target"] for r in result["files"]}
        for expected in ("/usr/local/libexec/pocketds-controller-test.py", "/usr/local/lib/pocketds/controller_test_gate.py", "/home/pocketds/.local/bin/voice_artifacts.py", "/usr/libexec/pocketds-image-desktop"):
            self.assertIn(expected, paths)

    def test_rejects_live_root_and_missing_marker(self):
        with self.assertRaises(stage.StageError):
            stage.plan(Path("/"), self.source, self.binary, self.binary)
        (self.root / "etc/pocketds-image-build-root").write_bytes(b"wrong\n")
        with self.assertRaises(stage.StageError):
            self.plan()

    def test_rejects_unlocked_password_without_showing_value(self):
        (self.root / "etc/shadow").write_text("pocketds:synthetic-test-password:20000:0:99999:7:::\n")
        with self.assertRaises(stage.StageError) as error:
            self.plan()
        self.assertNotIn("synthetic-test-password", str(error.exception))

    def test_only_bare_locks_are_allowed(self):
        shadow = self.root / "etc/shadow"
        for retained in ("!synthetic-password-hash", "!$6$fixture$synthetic", "*$6$fixture$synthetic", "!!"):
            with self.subTest(retained_hash=True):
                shadow.write_text("pocketds:" + retained + ":20000:0:99999:7:::\n")
                with self.assertRaises(stage.StageError) as error:
                    self.plan()
                self.assertNotIn(retained, str(error.exception))
        for bare in ("!", "*", "!*"):
            with self.subTest(bare_lock=True):
                shadow.write_text("pocketds:" + bare + ":20000:0:99999:7:::\n")
                self.plan()

    def test_rejects_private_source_and_native_wrong_arch(self):
        (self.source / ".git").mkdir()
        with self.assertRaises(stage.StageError):
            self.plan()
        (self.source / ".git").rmdir()
        self.binary.write_bytes(b"not an executable")
        with self.assertRaises(stage.StageError):
            self.plan()

    def test_destination_link_cannot_escape_root(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.root / "usr/local").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(stage.StageError):
            self.plan()
        self.assertEqual(list(outside.iterdir()), [])

    def test_missing_dependency_and_source_fail_before_write(self):
        (self.root / "usr/bin/parecord").unlink()
        with self.assertRaisesRegex(stage.StageError, "parecord"):
            self.plan()
        self.assertFalse((self.root / "usr/local").exists())

    def test_manifest_has_complete_keyboard_generation(self):
        sources = {r["source"] for r in self.inventory["files"]}
        for name in ("pocketds-keyboard.py", "visibility_state.py", "keyboard_adapter.py", "screen_geometry.py", "voice_artifacts.py", "pocketds-keyboard.service"):
            self.assertIn("components/keyboard/" + name, sources)
        self.assertIn("scripts/pocketds-asr-api-provision.py", sources)

    def test_android_return_helper_survives_fat_boot_mount(self):
        helper_source = "components/system/pocketds-switch-to-linux-android.sh"
        helper = next(row for row in self.inventory["files"] if row["source"] == helper_source)
        with patch.object(os, "chown"):
            stage.apply(self.root, self.plan())
        staged = self.root / helper["target"].lstrip("/")
        self.assertEqual(staged.read_bytes(), (self.source / helper_source).read_bytes())

        recipe = (REPO / "packaging/sd-image/repart/10-rocknix.conf").read_text()
        copies = [line.removeprefix("CopyFiles=").split(":", 1)
                  for line in recipe.splitlines() if line.startswith("CopyFiles=")]
        # Only these narrow paths are permitted; an EFI loader would shadow Image.
        self.assertCountEqual(copies, [
            ["/boot/Image", "/boot/Image"],
            [helper["target"], "/PocketDS-Switch-to-Linux.sh"],
        ])
        fat_destination = next(destination for source, destination in copies
                               if source == helper["target"])
        runtime_path = Path("/boot") / fat_destination.lstrip("/")
        self.assertEqual(str(runtime_path), helper["target"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for the Plasma scripting fixture")
    def test_layout_selects_lower_screen_idempotently(self):
        script = REPO / "packaging/sd-image/kit-desktop-layout.js"
        fixture = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const script = fs.readFileSync(process.argv[1], 'utf8');
const items = [], output = [];
const desktop = { widgets: () => items, addWidget: type => { const w = {type}; items.push(w); return w; }, writeConfig: () => {} };
const context = { screenForConnector: n => { assert.equal(n, 'DSI-2'); return 1; }, desktopForScreen: n => { assert.equal(n, 1); return desktop; }, screenGeometry: () => ({width:819,height:614}), QRectF: (x,y,width,height) => ({x,y,width,height}), print: s => output.push(s) };
vm.runInNewContext(script, context); vm.runInNewContext(script, context);
assert.equal(items.length, 1); assert.equal(items[0].geometry.width, 819);
assert.equal(output.length, 2); assert.equal(output[1], 'POCKETDS-DESKTOP-READY');
assert.throws(() => vm.runInNewContext(script, {...context, screenForConnector: () => -1}));
"""
        subprocess.run(["node", "-e", fixture, str(script)], check=True)


if __name__ == "__main__":
    unittest.main()

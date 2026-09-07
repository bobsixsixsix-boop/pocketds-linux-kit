#!/usr/bin/env python3
"""Hermetic desktop-switch tests: fake dialog/controller, never real reboot."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'components/system/pocketds-switch-to-android.sh'


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dialog, self.controller = self.root / 'dialog', self.root / 'controller'
        self.dialog.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DIALOG_LOG"\ncase "$*" in *--yesno*) exit "$DIALOG_STATUS";; *) exit 0;; esac\n')
        self.controller.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SWITCH_LOG"\nexit "$SWITCH_STATUS"\n')
        self.dialog.chmod(0o700)
        self.controller.chmod(0o700)
        source = SOURCE.read_text().replace('/usr/bin/kdialog', shlex.quote(str(self.dialog))).replace('/usr/local/bin/pocketds-panelctl', shlex.quote(str(self.controller)))
        self.script = self.root / 'entry.sh'
        self.script.write_text(source)
        self.environment = {**os.environ, 'DIALOG_LOG': str(self.root / 'dialog.log'),
                            'SWITCH_LOG': str(self.root / 'switch.log'),
                            'DIALOG_STATUS': '1', 'SWITCH_STATUS': '0'}

    def run_entry(self, *args):
        return subprocess.run(['/bin/sh', str(self.script), *args], env=self.environment, timeout=5).returncode

    def test_cancel_never_requests_boot_change(self):
        self.assertEqual(self.run_entry(), 0)
        self.assertFalse((self.root / 'switch.log').exists())

    def test_confirmation_uses_existing_exact_backend(self):
        self.environment['DIALOG_STATUS'] = '0'
        self.assertEqual(self.run_entry(), 0)
        self.assertEqual((self.root / 'switch.log').read_text(), 'boot-android CONFIRM\n')

    def test_failed_dialog_is_fail_closed(self):
        self.environment['DIALOG_STATUS'] = '255'
        self.assertEqual(self.run_entry(), 0)
        self.assertFalse((self.root / 'switch.log').exists())

    def test_backend_failure_has_visible_error(self):
        self.environment.update(DIALOG_STATUS='0', SWITCH_STATUS='1')
        self.assertEqual(self.run_entry(), 1)
        self.assertIn('--error', (self.root / 'dialog.log').read_text())

    def test_no_argument_bypass(self):
        self.assertEqual(self.run_entry('CONFIRM'), 2)
        self.assertFalse((self.root / 'dialog.log').exists())

    def test_desktop_entry_has_no_direct_reboot(self):
        entry = (ROOT / 'components/system/pocketds-switch-to-android.desktop').read_text()
        self.assertIn('Exec=/usr/local/libexec/pocketds-switch-to-android\n', entry)
        self.assertNotIn('boot-android CONFIRM', entry)


if __name__ == '__main__':
    unittest.main()

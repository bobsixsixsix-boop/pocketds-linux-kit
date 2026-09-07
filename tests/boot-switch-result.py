#!/usr/bin/env python3
"""Run the real dispatcher and QML result function without touching boot state."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
QML = (ROOT / 'components/control-panel/plasmoid/contents/ui/main.qml').read_text()
FUNCTION = QML[QML.index('    function finishAndroidBoot('):QML.index('    function requestStatus(')]


@unittest.skipUnless(shutil.which('node'), 'Node.js needed to execute the production QML JavaScript function')
class BootResultTests(unittest.TestCase):
    def ui(self, stdout, code, prior=False):
        script = '''let bootSwitchCommitted = JSON.parse(process.argv[3]);
let bootSwitchPending = true, bootSwitchError = "";
''' + FUNCTION + '''
finishAndroidBoot(process.argv[1], Number(process.argv[2]));
console.log(JSON.stringify({committed:bootSwitchCommitted,pending:bootSwitchPending,error:bootSwitchError}));
'''
        return json.loads(subprocess.check_output(['node', '-e', script, stdout, str(code), json.dumps(prior)], text=True, timeout=5))

    def dispatch(self, write_ok, reboot_ok):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            marker = folder / 'boot-target'
            marker.write_text('linux')
            writer = folder / 'writer'
            writer.write_text('#!/bin/sh\n' + (
                'printf android > "$BOOT_TEST_MARKER"\nprintf \'{"status":"ok","to":"android"}\\n\'\n'
                if write_ok else 'exit 1\n'))
            reboot = folder / 'reboot'
            reboot.write_text('#!/bin/sh\nexit ' + ('0' if reboot_ok else '1') + '\n')
            writer.chmod(0o700)
            reboot.chmod(0o700)
            source = (ROOT / 'components/control-panel/pocketds-panel-root').read_text()
            source = source.replace('/usr/bin/python3 /usr/local/libexec/pocketds-boot-mode', str(writer))
            source = source.replace('/usr/bin/systemctl', str(reboot))
            dispatcher = folder / 'dispatcher'
            dispatcher.write_text(source)
            result = subprocess.run(['/bin/sh', str(dispatcher), 'boot-android', 'CONFIRM'],
                env={**os.environ, 'BOOT_TEST_MARKER':str(marker)}, capture_output=True, text=True, timeout=5)
            return marker.read_text(), self.ui(result.stdout, result.returncode)

    def test_write_succeeded_reboot_failed_preserves_committed_target(self):
        target, ui = self.dispatch(True, False)
        self.assertEqual(target, 'android')
        self.assertTrue(ui['committed'])
        self.assertFalse(ui['pending'])
        self.assertIn('自动重启失败', ui['error'])
        self.assertNotIn('未改', ui['error'])

    def test_write_failure_reports_unknown_instead_of_claiming_no_change(self):
        target, ui = self.dispatch(False, True)
        self.assertEqual(target, 'linux')
        self.assertFalse(ui['committed'])
        self.assertFalse(ui['pending'])
        self.assertIn('未能确认', ui['error'])

    def test_success_waits_for_requested_reboot(self):
        target, ui = self.dispatch(True, True)
        self.assertEqual(target, 'android')
        self.assertEqual(ui, {'committed':True, 'pending':True, 'error':''})

    def test_malformed_missing_or_failed_receipt_does_not_claim_unchanged(self):
        for value in ('', 'bad json', 'null', '{"status":"failed","to":"android"}'):
            with self.subTest(value=value):
                ui = self.ui(value, 1)
                self.assertFalse(ui['committed'])
                self.assertIn('未能确认', ui['error'])

    def test_failed_retry_does_not_erase_previous_commit_receipt(self):
        ui = self.ui('', 1, prior=True)
        self.assertTrue(ui['committed'])
        self.assertIn('已设为 Android', ui['error'])
        begin = QML[QML.index('    function beginAndroidBoot('):QML.index('    function finishAndroidBoot(')]
        self.assertNotIn('bootSwitchCommitted = false', begin)
        self.assertIn('root.finishAndroidBoot(stdout, exitCode)', QML)


if __name__ == '__main__':
    unittest.main()

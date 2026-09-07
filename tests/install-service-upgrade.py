#!/usr/bin/env python3
"""Actual installer functions, real temp files, and an in-memory service model."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'scripts/install.sh').read_text()
FUNCTIONS = SOURCE[SOURCE.index('backup_user_file() {'):SOURCE.index('\ninstall_apps() {')]
ACTIVATION = SOURCE[SOURCE.index('    systemctl --user enable pocketds-speaker-enhancement.service'):
                    SOURCE.index('    systemctl --user enable pocketds-brightness.service')]
FILES = {
    'filter-chain.service': [
        '.config/pipewire/filter-chain.conf.d/90-pocketds-speaker-enhancement.conf',
        '.config/systemd/user/filter-chain.service.d/90-pocketds-speaker-enhancement.conf'],
    'pocketds-speaker-enhancement.service': [
        '.local/libexec/pocketds/pocketds-speaker-enhancement',
        '.config/systemd/user/pocketds-speaker-enhancement.service',
        '.config/pipewire/filter-chain.conf.d/90-pocketds-speaker-enhancement.conf',
        '.config/systemd/user/filter-chain.service.d/90-pocketds-speaker-enhancement.conf'],
    'pocketds-touchpad.service': ['.local/bin/pocketds-touchpad.py', '.local/bin/touchpad_gestures.py',
        '.local/bin/touchpad_raw.py', '.config/systemd/user/pocketds-touchpad.service'],
    'pocketds-light-standby.service': ['.local/libexec/pocketds/pocketds-light-standby',
        '.config/systemd/user/pocketds-light-standby.service'],
    'pocketds-gamepad-activity.service': ['.local/libexec/pocketds/pocketds-gamepad-activity',
        '.config/systemd/user/pocketds-gamepad-activity.service'],
}


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='install-upgrade-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.desktop = self.base / 'desktop'
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.source = self.base / 'source'
        self.source.write_text('new-generation\n')
        self.state = self.base / 'services.json'
        self.state.write_text('{}')
        mapping = {unit: [str(self.desktop / rel) for rel in paths] for unit, paths in FILES.items()}
        (self.base / 'mapping.json').write_text(json.dumps(mapping))
        for paths in mapping.values():
            for name in paths:
                path = Path(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('old-generation\n')
                path.chmod(0o644)
        self.tool('systemctl', '''import json,os,sys
from pathlib import Path
state_path=Path(os.environ['TEST_SERVICES']); state=json.loads(state_path.read_text())
mapping=json.loads(Path(os.environ['TEST_MAPPING']).read_text())
_,action,*args=sys.argv[1:]
unit=args[-1]
if action=='enable': sys.exit(0)
if action=='is-active': sys.exit(0 if unit in state else 3)
if action not in ('start','restart'): raise SystemExit('unexpected service operation')
if os.environ.get('TEST_ACTIVATION_FAIL')==unit: sys.exit(1)
if action=='restart' or unit not in state:
    previous=state.get(unit,{})
    state[unit]={'generation':[Path(p).read_text() for p in mapping[unit]],
                 'starts':previous.get('starts',0)+1}
state_path.write_text(json.dumps(state))
''')
        self.tool('stat', '''import os,sys,stat
s=os.lstat(sys.argv[-1]); print(f'{stat.S_IMODE(s.st_mode):o}:{s.st_uid}:{s.st_gid}')
''')
        self.env = {**os.environ, 'PATH':str(self.bin)+os.pathsep+os.environ['PATH'],
            'TEST_DESKTOP':str(self.desktop), 'TEST_SERVICES':str(self.state),
            'TEST_MAPPING':str(self.base/'mapping.json')}

    def tool(self, name, body):
        path = self.bin / name
        path.write_text('#!' + sys.executable + '\n' + body)
        path.chmod(0o755)

    def run_script(self, body, extra_env=None):
        # Root install tests mock only privilege/ownership, never invoking sudo.
        root_mock = r'''
sudo() {
    if [[ $1 == stat ]]; then
        shift
        local metadata
        metadata=$(stat "$@") || return
        printf '%s:0:0\n' "${metadata%%:*}"
    elif [[ $1 == install ]]; then
        shift
        local args=()
        while (($#)); do
            case $1 in -o|-g) shift 2 ;; *) args+=("$1"); shift ;; esac
        done
        command install "${args[@]}"
    else
        "$@"
    fi
}
'''
        prefix = '\n'.join(['set -Eeuo pipefail',
            'activation_journal='+shlex.quote(str(self.base/'pending-activation')),
            'user_backup='+shlex.quote(str(self.base/'user-backup')),
            'root_backup='+shlex.quote(str(self.base/'root-backup'))])+'\n'
        script = self.base / 'run.sh'
        script.write_text(prefix + root_mock + FUNCTIONS.replace('$HOME', '$TEST_DESKTOP') + '\n' + body)
        return subprocess.run(['/bin/bash',str(script)], env={**self.env, **(extra_env or {})},
                              capture_output=True, text=True, timeout=15)

    def copy(self, target, mode='0644', root=False):
        return ('install_root_file' if root else 'install_user_file') + ' ' + ' '.join(
            shlex.quote(value) for value in (str(self.source), str(target), mode)) + '\n'

    def test_changed_active_services_run_new_generation(self):
        for unit, paths in FILES.items():
            with self.subTest(unit=unit):
                self.state.write_text('{}')
                target = self.desktop / paths[0]
                target.write_text('old-generation\n')
                result = self.run_script('systemctl --user start '+unit+'\n' + self.copy(target) +
                                        ACTIVATION.replace('$HOME', '$TEST_DESKTOP'))
                self.assertEqual(result.returncode, 0, result.stderr)
                state = json.loads(self.state.read_text())
                self.assertEqual(state[unit]['generation'][0], 'new-generation\n')
                self.assertEqual(state[unit]['starts'], 2)

    def test_unchanged_reinstall_preserves_running_generation(self):
        target = self.desktop / FILES['pocketds-touchpad.service'][0]
        self.source.write_text(target.read_text())
        result = self.run_script('systemctl --user start pocketds-touchpad.service\n' + self.copy(target) +
                                ACTIVATION.replace('$HOME', '$TEST_DESKTOP'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.state.read_text())['pocketds-touchpad.service']['starts'], 1)

    def test_failed_activation_is_not_reported_as_success(self):
        target = self.desktop / FILES['pocketds-light-standby.service'][0]
        result = self.run_script(self.copy(target) + ACTIVATION.replace('$HOME', '$TEST_DESKTOP'),
                                {'TEST_ACTIVATION_FAIL':'pocketds-light-standby.service'})
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.base/'pending-activation').exists())

    def test_interrupted_install_retry_activates_already_copied_files(self):
        target = self.desktop / FILES['pocketds-light-standby.service'][0]
        first = self.run_script('systemctl --user start pocketds-light-standby.service\n' +
                                self.copy(target) + 'exit 1\n')
        self.assertEqual(first.returncode, 1)
        retry = self.run_script(self.copy(target) + ACTIVATION.replace('$HOME', '$TEST_DESKTOP'))
        self.assertEqual(retry.returncode, 0, retry.stderr)
        state = json.loads(self.state.read_text())['pocketds-light-standby.service']
        self.assertEqual(state['generation'][0], 'new-generation\n')
        self.assertEqual(state['starts'], 2)
        self.assertFalse((self.base/'pending-activation').exists())

    def test_unreadable_activation_journal_cannot_be_treated_as_unchanged(self):
        self.tool('grep', 'import sys\nsys.exit(2)\n')
        target = self.desktop / FILES['pocketds-light-standby.service'][0]
        result = self.run_script(self.copy(target) + ACTIVATION.replace('$HOME', '$TEST_DESKTOP'))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue((self.base/'pending-activation').exists())

    def test_same_content_repairs_missing_execute_or_excess_permissions(self):
        for root in (False, True):
            for before in (0o644, 0o777):
                with self.subTest(root=root, mode=oct(before)):
                    target = self.desktop / 'helper'
                    target.write_text(self.source.read_text())
                    target.chmod(before)
                    result = self.run_script(self.copy(target, '0755', root=root))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(target.stat().st_mode & 0o777, 0o755)

    def test_symlink_target_is_rejected_without_mutating_referent(self):
        for root in (False, True):
            with self.subTest(root=root):
                target = self.desktop / 'link'
                if target.is_symlink(): target.unlink()
                referent = self.desktop / 'referent'
                referent.write_text(self.source.read_text())
                referent.chmod(0o644)
                target.symlink_to(referent)
                result = self.run_script(self.copy(target, '0755', root=root))
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(target.is_symlink())
                self.assertEqual(referent.stat().st_mode & 0o777, 0o644)

    def test_shared_touchpad_module_change_restarts_touchpad(self):
        target = self.desktop / '.local/bin/touchpad_raw.py'
        result = self.run_script('systemctl --user start pocketds-touchpad.service\n' + self.copy(target) +
                                ACTIVATION.replace('$HOME', '$TEST_DESKTOP'))
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state.read_text())['pocketds-touchpad.service']
        self.assertEqual(state['generation'][2], 'new-generation\n')
        self.assertEqual(state['starts'], 2)


if __name__ == '__main__':
    unittest.main()

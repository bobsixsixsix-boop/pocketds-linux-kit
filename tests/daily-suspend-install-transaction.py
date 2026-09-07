#!/usr/bin/env python3
"""Execute the daily installer in temporary files; all hardware/services are mocks."""
import configparser
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'scripts/install-daily-suspend.sh').read_text()
PROFILES = ('AC', 'Battery', 'LowBattery')


def power_config(lid=0, button=64, idle=0, down=16, sleep_mode=3):
    return '# User settings and comments\n' + '\n'.join(
        f'[{profile}][Display]\nTurnOffDisplayIdleTimeoutSec=432\nDisplayBrightness=37\n'
        f'[{profile}][SuspendAndShutdown]\nLidAction={lid}\nPowerButtonAction={button}\n'
        f'AutoSuspendAction={idle}\nPowerDownAction={down}\nSleepMode={sleep_mode}\n'
        'AutoSuspendIdleTimeoutSec=987\nInhibitLidActionWhenExternalMonitorPresent=true\n'
        for profile in PROFILES) + '\n[Unrelated]\nPreserveThis=value\n'


def parsed(text):
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read_string(text)
    return {section: dict(parser[section]) for section in parser.sections()}


def fixture_source(root, desktop, base, model):
    source = SOURCE.replace('$HOME', '$TEST_DESKTOP').replace('/home/pocketds', str(desktop))
    for prefix in ('/etc/', '/var/lib/', '/usr/local/libexec/'):
        source = source.replace(prefix, str(base) + prefix)
    source = source.replace('/proc/device-tree/model', str(model))
    # Inject the source path last: on the actual device ROOT itself is
    # under /home/pocketds and must not become a fixture destination.
    return source.replace('repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)',
                          'repo_root=' + shlex.quote(str(root)))


class TransactionTests(unittest.TestCase):
    def test_device_checkout_path_is_preserved_while_destinations_are_isolated(self):
        root = Path('/home/pocketds/pocketds-linux-kit-final')
        base = Path('/tmp/daily-install-path-fixture')
        source = fixture_source(root, base / 'desktop', base, base / 'model')
        assignment = next(line for line in source.splitlines() if line.startswith('repo_root='))
        self.assertEqual(assignment, 'repo_root=' + shlex.quote(str(root)))
        self.assertIn(str(base / 'etc/pocketds-linux-kit/daily-suspend.enabled'), source)
        self.assertNotIn('repo_root=' + str(base / 'desktop'), source)

    def exercise(self, failure='', existing=False, mode='--enable', rollback_failure=False,
                 helper_existing=None, user_config=None, native_kconfig=False):
        with tempfile.TemporaryDirectory(prefix='daily-install-test-') as temporary:
            base = Path(temporary)
            desktop = base / 'desktop'
            roots = [base / path.lstrip('/') for path in (
                '/usr/local/libexec/pocketds-daily-suspend',
                '/etc/systemd/system/systemd-suspend.service.d/30-pocketds-daily-suspend-guard.conf',
                '/etc/polkit-1/rules.d/89-pocketds-daily-suspend.rules',
                '/etc/systemd/sleep.conf.d/90-pocketds-daily-suspend.conf',
                '/etc/pocketds-linux-kit/daily-suspend.enabled')]
            users = [desktop / '.config/powerdevilrc',
                     desktop / '.local/libexec/pocketds/pocketds-light-standby',
                     desktop / '.local/libexec/pocketds/pocketds-lid-mode']
            targets = roots + users
            for path in targets:
                path.parent.mkdir(parents=True, exist_ok=True)
            for index, path in enumerate(targets):
                exists = existing or path in users[:2]
                if path == users[-1] and helper_existing is not None:
                    exists = helper_existing
                if exists:
                    path.write_text('preimage-' + str(index))
                    path.chmod(0o640 if index < 6 else 0o750)
            users[0].write_text(power_config() if user_config is None else user_config)
            model = base / 'model'
            model.write_bytes(b'AYANEO Pocket DS\0')
            before = {path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
                      for path in targets}
            # Only platform identity and destinations change; guards, backups,
            # mutation order, EXIT trap, rollback and capability waits execute.
            source = fixture_source(ROOT, desktop, base, model)
            # The test may run as root in CI, but never calls a real sudo/service.
            source = source.replace('$EUID -ne 0', '1 -ne 0')
            prelude = r'''
id() { if [[ $* == -un ]]; then printf 'pocketds\n'; else command id "$@"; fi; }
sleep() { :; }
step() {
    printf '%s\n' "$*" >> "$TEST_LOG"
    if [[ -n $TEST_FAIL && ! -e $TEST_FAILED && $* == *"$TEST_FAIL"* ]]; then
        : > "$TEST_FAILED"; return 1
    fi
}
sudo() {
    [[ ${1:-} != -n ]] || shift
    [[ ${1:-} != python3 ]] || return 0
    if [[ $TEST_ROLLBACK_FAIL == 1 && -e $TEST_FAILED && ${1:-} == cp ]]; then return 1; fi
    if [[ $TEST_ROLLBACK_FAIL == 2 && -e $TEST_FAILED && ${1:-} == stat ]]; then return 1; fi
    step "sudo $*" || return
    "$@"
}
install() { step "install $*" || return; command install "$@"; }
kreadconfig6() {
    step "kreadconfig6 $*" || return
    if [[ $TEST_FAIL == readback-mismatch && -e $TEST_CONFIG_WRITTEN ]]; then printf '999\n'; return; fi
    if [[ $TEST_NATIVE_KCONFIG == 1 ]]; then command kreadconfig6 "$@"; return; fi
    command python3 "$TEST_KCONFIG" read "$@"
}
kwriteconfig6() {
    step "kwriteconfig6 $*" || return
    : > "$TEST_CONFIG_WRITTEN"
    if [[ $TEST_NATIVE_KCONFIG == 1 ]]; then command kwriteconfig6 "$@"; return; fi
    command python3 "$TEST_KCONFIG" write "$@"
}
stat() {
    # GNU stat metadata query, implemented with real lstat for macOS too.
    python3 - "$3" <<'PY'
import os,sys,stat
s=os.lstat(sys.argv[1]); print(f'{stat.S_IMODE(s.st_mode):o}:{s.st_uid}:{s.st_gid}')
PY
}
systemctl() {
    step "systemctl $*" || return
    case "$*" in
        *MainPID*) printf '123\n' ;;
        *is-active*) return 0 ;;
    esac
}
pkcheck() {
    if [[ $TEST_FAIL == authorization-timeout ]]; then : > "$TEST_FAILED"; return 1; fi
}
busctl() {
    if [[ $* == *LidClosed* ]]; then printf 'b false\n'; return; fi
    if [[ $TEST_FAIL == capability-timeout && -e $TEST_TOKEN && ! -e $TEST_FAILED ]]; then
        printf 'b false\n'; return
    fi
    [[ ! -e $TEST_TOKEN ]] && printf 'b false\n' || printf 'b true\n'
}
'''
            script = base / 'installer.sh'
            # Capability-timeout must end before rollback checks; mark the
            # injected failure when EXIT restoration starts, not during waits.
            source = source.replace('rollback_policy() {', 'rollback_policy() {\n    : > "$TEST_FAILED"')
            script.write_text(prelude + source)
            # A byte-preserving fixture for the KConfig CLI only. The actual
            # installer still performs every read, write and read-back check.
            kconfig = base / 'kconfig.py'
            kconfig.write_text(r'''
import sys
from pathlib import Path
mode,*args=sys.argv[1:]
path=Path(args[args.index('--file')+1]); key=args[args.index('--key')+1]
groups=[args[i+1] for i,arg in enumerate(args) if arg=='--group']
header='['+']['.join(groups)+']'
lines=path.read_text().splitlines(keepends=True)
active=False; start=None; end=len(lines); found=None
for i,line in enumerate(lines):
    if line.strip().startswith('['):
        if active: end=i; break
        active=line.strip()==header
        if active:start=i
    elif active and line.split('=',1)[0].strip()==key:
        found=i
if mode=='read':
    default=args[args.index('--default')+1]
    print(lines[found].split('=',1)[1].strip() if found is not None else default)
else:
    value=args[-1]
    if found is not None:lines[found]=key+'='+value+'\n'
    elif start is not None:lines.insert(end,key+'='+value+'\n')
    else:lines.extend(['\n'+header+'\n',key+'='+value+'\n'])
    path.write_text(''.join(lines))
''')
            env = {**os.environ, 'TEST_DESKTOP':str(desktop), 'TEST_LOG':str(base/'calls'),
                'TEST_FAIL':failure, 'TEST_FAILED':str(base/'failed'), 'TEST_TOKEN':str(roots[4]),
                'TEST_ROLLBACK_FAIL':str(int(rollback_failure)), 'TEST_KCONFIG':str(kconfig),
                'TEST_CONFIG_WRITTEN':str(base/'config-written'),
                'TEST_NATIVE_KCONFIG':str(int(native_kconfig))}
            result = subprocess.run(['/bin/bash', str(script), mode, '--confirm', 'POCKETDS-DAILY-DEEP-20260905'],
                env=env, capture_output=True, text=True, timeout=15)
            after = {path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
                     for path in targets}
            self.last_config = users[0].read_text()
            self.last_config_mode = users[0].stat().st_mode & 0o777
            if result.returncode == 0:
                self.assertEqual(after[users[-1]],
                                 ((ROOT / 'components/system/pocketds-lid-mode.py').read_bytes(), 0o755))
            return result, before == after, roots[4].exists(), (base/'calls').read_text()

    def test_every_enable_mutation_failure_restores_absent_or_existing_preimages(self):
        failures = (
            'install -m 0755 ' + str(ROOT / 'components/system/pocketds-lid-mode.py'),
            'install -m 0755 ' + str(ROOT / 'components/system/pocketds-daily-suspend.py'),
            'install -m 0644 ' + str(ROOT / 'components/system/30-pocketds-daily-suspend-guard.conf'),
            'systemctl daemon-reload',
            'install -m 0644 ' + str(ROOT / 'components/system/89-pocketds-daily-suspend.rules'),
            'install -m 0644 ' + str(ROOT / 'components/system/90-pocketds-daily-suspend.conf'),
            'install -m 0644 ' + str(ROOT / 'components/system/daily-suspend.enabled'),
            'install -m 0755 ' + str(ROOT / 'components/light-standby/pocketds-light-standby.py'),
            *('--group ' + profile + ' --group SuspendAndShutdown --key SleepMode --type int'
              for profile in PROFILES),
            'kreadconfig6',
            'readback-mismatch',
            'systemctl --user restart pocketds-light-standby.service',
            'authorization-timeout',
            'systemctl --user restart plasma-powerdevil.service',
            'capability-timeout',
        )
        for existing in (False, True):
            for failure in failures:
                if existing and failure == 'capability-timeout':
                    continue  # initial capability must be true for this baseline
                with self.subTest(existing=existing, failure=failure):
                    result, restored, _, _ = self.exercise(failure, existing)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertTrue(restored, result.stderr)
                    self.assertIn('previous policy restored', result.stderr)

    def test_successful_enable_commits_after_capability_check(self):
        result, restored, token, _ = self.exercise()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(restored)
        self.assertTrue(token)
        self.assertNotIn('restored', result.stderr)

    def test_successful_disable_withdraws_opt_in(self):
        result, _, token, _ = self.exercise(existing=True, mode='--disable')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(token)

    def test_enable_only_changes_sleep_mode_and_preserves_all_explicit_preferences(self):
        before = power_config(lid=0, button=64, idle=0, down=128)
        result, _, _, _ = self.exercise(user_config=before)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = parsed(before)
        for profile in PROFILES:
            expected[profile + '][SuspendAndShutdown']['SleepMode'] = '1'
        self.assertEqual(parsed(self.last_config), expected)
        self.assertEqual(self.last_config_mode, 0o640)
        self.assertIn('# User settings and comments', self.last_config)

    def test_enable_preserves_previously_selected_sleep_actions(self):
        before = power_config(lid=1, button=1, idle=1, down=1, sleep_mode=1)
        result, _, _, _ = self.exercise(existing=True, user_config=before)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(parsed(self.last_config), parsed(before))

    def test_missing_capability_dependent_defaults_keep_previous_effective_actions(self):
        for existing in (False, True):
            before = power_config().replace('LidAction=0\n', '').replace('AutoSuspendAction=0\n', '')
            with self.subTest(existing=existing):
                result, _, _, _ = self.exercise(existing=existing, user_config=before)
                self.assertEqual(result.returncode, 0, result.stderr)
                after = parsed(self.last_config)
                for profile in PROFILES:
                    group = after[profile + '][SuspendAndShutdown']
                    self.assertEqual(group['LidAction'], '1' if existing else '64')
                    self.assertEqual(group['AutoSuspendAction'], '1' if existing else '0')

    def test_failure_while_pinning_missing_defaults_restores_their_absence(self):
        before = power_config().replace('LidAction=0\n', '').replace('AutoSuspendAction=0\n', '')
        for profile in PROFILES:
            for key in ('LidAction', 'AutoSuspendAction'):
                failure = '--group ' + profile + ' --group SuspendAndShutdown --key ' + key + ' --type int'
                with self.subTest(profile=profile, key=key):
                    result, restored, token, _ = self.exercise(failure, user_config=before)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertTrue(restored, result.stderr)
                    self.assertFalse(token)

    def test_disable_only_clears_actual_sleep_entry_points(self):
        before = power_config(lid=1, button=64, idle=1, down=1)
        result, _, _, _ = self.exercise(existing=True, mode='--disable', user_config=before)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = parsed(before)
        for profile in PROFILES:
            group = expected[profile + '][SuspendAndShutdown']
            for key in ('LidAction', 'AutoSuspendAction', 'PowerDownAction'):
                group[key] = '0'
        self.assertEqual(parsed(self.last_config), expected)
        self.assertEqual(self.last_config_mode, 0o640)

    def test_disable_does_not_override_unrelated_non_sleep_actions(self):
        before = power_config(lid=64, button=128, idle=8, down=2)
        result, _, _, _ = self.exercise(existing=True, mode='--disable', user_config=before)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.last_config, before)

    @unittest.skipUnless(shutil.which('kreadconfig6') and shutil.which('kwriteconfig6'),
                         'native KConfig tools are not installed on this test host')
    def test_native_kconfig_updates_only_temporary_fixture_preferences(self):
        before = power_config(lid=1, button=64, idle=0, down=128)
        for mode in ('--enable', '--disable'):
            with self.subTest(mode=mode):
                result, _, _, _ = self.exercise(existing=True, mode=mode, user_config=before,
                                                  native_kconfig=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = parsed(before)
                for profile in PROFILES:
                    group = expected[profile + '][SuspendAndShutdown']
                    group['SleepMode' if mode == '--enable' else 'LidAction'] = '1' if mode == '--enable' else '0'
                self.assertEqual(parsed(self.last_config), expected)
                self.assertEqual(self.last_config_mode, 0o640)

    def test_every_disable_kconfig_failure_restores_preferences_and_opt_in(self):
        before = power_config(lid=1, button=1, idle=1, down=1)
        failures = ['kreadconfig6', 'readback-mismatch'] + [
            '--group ' + profile + ' --group SuspendAndShutdown --key ' + key + ' --type int'
            for profile in PROFILES for key in ('LidAction', 'PowerButtonAction', 'PowerDownAction', 'AutoSuspendAction')]
        for failure in failures:
            with self.subTest(failure=failure):
                result, restored, token, _ = self.exercise(failure, True, '--disable', user_config=before)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertTrue(restored, result.stderr)
                self.assertTrue(token)

    def test_failed_disable_restores_previous_policy_and_reports_failure(self):
        result, restored, token, _ = self.exercise(
            'systemctl --user restart plasma-powerdevil.service', True, '--disable')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(restored, result.stderr)
        self.assertTrue(token)

    def test_disable_failure_restores_or_removes_user_helper_preimage(self):
        for helper_existing in (False, True):
            for failure in (
                'install -m 0755 ' + str(ROOT / 'components/system/pocketds-lid-mode.py'),
                'systemctl --user restart pocketds-light-standby.service',
            ):
                with self.subTest(helper_existing=helper_existing, failure=failure):
                    result, restored, token, _ = self.exercise(
                        failure, True, '--disable', helper_existing=helper_existing)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertTrue(restored, result.stderr)
                    self.assertTrue(token)

    def test_helper_is_deployed_before_standby_daemon_and_any_service_restart(self):
        result, _, _, calls = self.exercise()
        self.assertEqual(result.returncode, 0, result.stderr)
        helper = calls.index('install -m 0755 ' + str(ROOT / 'components/system/pocketds-lid-mode.py'))
        daemon = calls.index('install -m 0755 ' + str(ROOT / 'components/light-standby/pocketds-light-standby.py'))
        restart = calls.index('systemctl --user restart pocketds-light-standby.service')
        self.assertLess(helper, daemon)
        self.assertLess(daemon, restart)

    def test_rollback_failure_is_reported_and_opt_in_is_withdrawn(self):
        for fault in (1, 2):
            with self.subTest(fault=fault):
                result, restored, token, _ = self.exercise('authorization-timeout', rollback_failure=fault)
                self.assertEqual(result.returncode, 70, result.stderr)
                if fault == 1:
                    self.assertFalse(restored)
                self.assertFalse(token)
                self.assertIn('rollback incomplete', result.stderr)


if __name__ == '__main__':
    unittest.main()

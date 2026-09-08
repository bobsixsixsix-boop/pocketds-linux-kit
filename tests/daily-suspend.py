#!/usr/bin/env python3
"""Pure, non-sleeping checks for the explicit daily deep-sleep gate."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('daily', ROOT / 'components/system/pocketds-daily-suspend.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class CompositorLidTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.input_dir = Path(temporary.name)
        patcher = mock.patch.object(M, 'INPUT_DIR', self.input_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.add_device('event27')
        # Shape captured from busctl Properties.GetAll on KWin 6.7.3. The
        # method body is an array, but each variant value is a scalar.
        self.reply = {'type': 'a{sv}', 'data': [{
            'name': {'type': 's', 'data': 'gpio-keys'},
            'sysName': {'type': 's', 'data': 'event27'},
            'lidSwitch': {'type': 'b', 'data': False},
            'keyboard': {'type': 'b', 'data': True},
            'enabled': {'type': 'b', 'data': True},
        }]}

    def add_device(self, event, name='gpio-keys', switches='1'):
        device = self.input_dir / event / 'device'
        (device / 'capabilities').mkdir(parents=True)
        (device / 'name').write_text(name + '\n')
        (device / 'capabilities/sw').write_text(switches + '\n')

    def check(self):
        quirk = (ROOT / 'components/system/99-pocketds-lid.quirks').read_bytes()
        with mock.patch.object(M, 'root_file', return_value=quirk), mock.patch.object(M.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1234)), mock.patch.object(M.subprocess, 'check_output', return_value=json.dumps(self.reply)) as query:
            result = M.compositor_lid_ready()
        return result, query

    def test_actual_getall_shape_and_current_event_are_accepted(self):
        self.add_device('event4', name='another device')
        self.add_device('event8', switches='2')
        result, query = self.check()
        self.assertEqual(result, {'device': 'event27', 'lid_filtered': True,
                                  'keyboard': True, 'enabled': True})
        args = query.call_args.args[0]
        self.assertEqual(args[:5], ['/usr/sbin/runuser', '-u', 'pocketds', '--', '/usr/bin/busctl'])
        self.assertIn('--address=unix:path=/run/user/1234/bus', args)
        self.assertIn('--auto-start=no', args)
        self.assertIn('/org/kde/KWin/InputDevice/event27', args)
        self.assertEqual(args[-4:], ['org.freedesktop.DBus.Properties', 'GetAll', 's', 'org.kde.KWin.InputDevice'])
        self.assertEqual(query.call_args.kwargs['timeout'], 3)
        query.assert_called_once()

    def test_installed_quirk_does_not_accept_the_old_running_context(self):
        self.reply['data'][0]['lidSwitch']['data'] = True
        with self.assertRaisesRegex(RuntimeError, 'not loaded'):
            self.check()

    def test_missing_or_changed_root_quirk_stops_before_session_query(self):
        with mock.patch.object(M, 'root_file', side_effect=FileNotFoundError), mock.patch.object(M.subprocess, 'check_output') as query:
            with self.assertRaises(FileNotFoundError):
                M.compositor_lid_ready()
            query.assert_not_called()
        with mock.patch.object(M, 'root_file', return_value=b'wrong quirk'), mock.patch.object(M.subprocess, 'check_output') as query:
            with self.assertRaisesRegex(RuntimeError, 'quirk differs'):
                M.compositor_lid_ready()
            query.assert_not_called()

    def test_missing_duplicate_or_wrong_raw_lid_is_rejected(self):
        switches = self.input_dir / 'event27/device/capabilities/sw'
        switches.write_text('2\n')
        with self.assertRaisesRegex(RuntimeError, 'one raw'):
            self.check()
        switches.write_text('1\n')
        self.add_device('event42')
        with self.assertRaisesRegex(RuntimeError, 'one raw'):
            self.check()

    def test_mismatched_live_device_is_rejected(self):
        for prop, value in [('name', 'some keyboard'), ('sysName', 'event4')]:
            with self.subTest(prop=prop):
                variant = self.reply['data'][0][prop]
                old = variant['data']
                variant['data'] = value
                with self.assertRaisesRegex(RuntimeError, 'does not match'):
                    self.check()
                variant['data'] = old

    def test_disabling_gpio_keys_is_not_valid_lid_filtering(self):
        for prop in ('keyboard', 'enabled'):
            with self.subTest(prop=prop):
                self.reply['data'][0][prop]['data'] = False
                with self.assertRaisesRegex(RuntimeError, 'remain enabled'):
                    self.check()
                self.reply['data'][0][prop]['data'] = True

    def test_missing_or_malformed_variants_fail_closed(self):
        properties = self.reply['data'][0]
        for prop in ('name', 'sysName', 'lidSwitch', 'keyboard', 'enabled'):
            original = properties[prop]
            for variant in (None, False, {}, {'type': 'b', 'data': [False]},
                            {'type': 'b', 'data': 0}, {'type': 's', 'data': False}):
                with self.subTest(prop=prop, variant=variant):
                    properties[prop] = variant
                    with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                        self.check()
            del properties[prop]
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                self.check()
            properties[prop] = original

    def test_malformed_getall_envelope_is_rejected(self):
        for reply in (None, [], {}, {'type': 'a{sv}', 'data': {}},
                      {'type': 'a{sv}', 'data': []},
                      {'type': 'a{sv}', 'data': [None]},
                      {'type': 'a{sv}', 'data': [{}, {}]},
                      {'type': 'v', 'data': [{}]}):
            with self.subTest(reply=reply):
                self.reply = reply
                with self.assertRaisesRegex(RuntimeError, 'unexpected'):
                    self.check()

    def test_session_timeout_does_not_pass(self):
        with mock.patch.object(M, 'root_file', return_value=(ROOT / 'components/system/99-pocketds-lid.quirks').read_bytes()), mock.patch.object(M.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1000)), mock.patch.object(M.subprocess, 'check_output', side_effect=subprocess.TimeoutExpired('busctl', 3)):
            with self.assertRaises(subprocess.TimeoutExpired):
                M.compositor_lid_ready()


class ConsoleSuspendTests(unittest.TestCase):
    def setUp(self):
        self.console_path = '/sys/module/printk/parameters/console_suspend'
        self.values = {
            '/sys/power/pm_async': '0', '/sys/power/mem_sleep': 's2idle [deep]',
            '/sys/power/pm_test': '[none] core', '/sys/power/pm_debug_messages': '0',
            self.console_path: 'Y', str(M.POWER_WAKE): 'enabled',
            '/sys/devices/platform/gpio-keys/power/wakeup': 'enabled',
        }
        self.image_reads = mock.Mock(return_value=b'test boot image')

        def read_value(path):
            result = self.values[str(path)]
            if isinstance(result, Exception):
                raise result
            return result

        mounts = ('1 0 8:13 / / rw - ext4 /dev/sda13 rw\n'
                  '2 1 8:12 / /boot rw - vfat /dev/sda12 rw\n')
        patches = [mock.patch.object(M.os, 'geteuid', return_value=0),
                   mock.patch.object(M, 'runtime_identity', return_value='test-boot'),
                   mock.patch.object(M, 'storage_health'),
                   mock.patch.object(M, 'compositor_lid_ready', return_value={'lid_filtered': True}),
                   mock.patch.object(M, 'powerdevil_resume_ready', return_value={'pid': 1650}),
                   mock.patch.object(M, 'value', side_effect=read_value),
                   mock.patch.object(M, 'root_file', self.image_reads),
                   mock.patch.object(M, 'IMAGE', M.sha(b'test boot image')),
                   mock.patch.object(M, 'THERMAL', M.sha(b'')),
                   mock.patch.object(M, 'counters', return_value={'success': 0, 'fail': 0}),
                   mock.patch.object(Path, 'read_text', return_value=mounts),
                   mock.patch.object(Path, 'rglob', return_value=[])]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_normal_console_suspension_passes_the_full_gate(self):
        self.assertEqual(M.full_check()['boot_id'], 'test-boot')
        self.image_reads.assert_called_once_with(Path('/boot/boot/Image'))

    def test_diagnostic_and_unknown_console_states_stop_before_image_reads(self):
        for observed in ('N', '', '0', '1', 'true', 'y'):
            with self.subTest(observed=observed):
                self.values[self.console_path] = observed
                with self.assertRaisesRegex(RuntimeError, 'console suspension is disabled'):
                    M.full_check()
                self.image_reads.assert_not_called()

    def test_missing_console_parameter_fails_closed(self):
        self.values[self.console_path] = FileNotFoundError(self.console_path)
        with self.assertRaises(FileNotFoundError):
            M.full_check()
        self.image_reads.assert_not_called()

    def test_console_gate_failure_never_records_pending_sleep(self):
        self.values[self.console_path] = 'N'
        with mock.patch.object(M, 'eligibility_policy'), mock.patch.object(M.os.path, 'lexists', return_value=False), mock.patch.object(M, 'save_runtime') as save:
            with self.assertRaisesRegex(RuntimeError, 'console suspension is disabled'):
                M.pre()
            save.assert_not_called()


class DailyTests(unittest.TestCase):
    def test_pm_defaults_restore_normal_console_suspend_and_serial_callbacks(self):
        content = (ROOT / 'components/system/80-pocketds-pm.conf').read_text()
        writes = {fields[1]: fields[6] for line in content.splitlines()
                  if (fields := line.split()) and fields[0] == 'w'}
        self.assertEqual(writes['/sys/module/printk/parameters/console_suspend'], 'Y')
        self.assertEqual(writes['/sys/power/pm_async'], '0')

    def test_open_lid_does_not_require_a_closed_display_snapshot(self):
        with mock.patch.object(M, 'lid_closed', return_value=False), mock.patch.object(M, 'value') as read:
            self.assertEqual(M.closed_display_ready(), {'lid_closed': False})
            read.assert_not_called()

    def test_closed_sleep_requires_both_blank_and_zero_lower_brightness(self):
        for values, good in [(['4', '4', '0', '0'], True),
                             (['0', '4', '0', '0'], False),
                             (['4', '0', '0', '0'], False),
                             (['4', '4', '2000', '0'], False),
                             (['4', '4', '0', '2000'], False)]:
            with self.subTest(values=values), mock.patch.object(M, 'lid_closed', return_value=True), mock.patch.object(M, 'value', side_effect=values):
                if good:
                    self.assertTrue(M.closed_display_ready()['lid_closed'])
                else:
                    with self.assertRaisesRegex(RuntimeError, 'physically blanked'):
                        M.closed_display_ready()

    def test_reopening_during_closed_preparation_refuses_sleep(self):
        with mock.patch.object(M, 'lid_closed', side_effect=[True, False]), mock.patch.object(M, 'value', side_effect=['4', '4', '0', '0']):
            with self.assertRaisesRegex(RuntimeError, 'reopened'):
                M.closed_display_ready()

    def test_real_busctl_scalar_lid_shape_is_required(self):
        for data in (True, False, [False], None, 0, 'false'):
            with self.subTest(data=data), mock.patch.object(M.subprocess, 'check_output', return_value=json.dumps({'type': 'b', 'data': data})):
                if type(data) is bool:
                    self.assertIs(M.lid_closed(), data)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                        M.lid_closed()

    def test_failed_display_preflight_never_records_pending_sleep(self):
        with mock.patch.object(M, 'eligibility_policy'), mock.patch.object(M.os.path, 'lexists', return_value=False), mock.patch.object(M, 'full_check', return_value={'counters': {'success': 0, 'fail': 0}}), mock.patch.object(M, 'effective_policy'), mock.patch.object(M, 'closed_display_ready', side_effect=RuntimeError('not blanked')), mock.patch.object(M, 'save_runtime') as save:
            with self.assertRaisesRegex(RuntimeError, 'not blanked'):
                M.pre()
            save.assert_not_called()

    def test_opt_in_and_exact_runtime_are_both_required(self):
        with mock.patch.object(M, 'runtime_identity') as identity, mock.patch.object(M, 'root_file', return_value=M.TOKEN), mock.patch.object(M.os.path, 'lexists', return_value=False), mock.patch.object(M, 'compositor_lid_ready') as compositor, mock.patch.object(M, 'powerdevil_artifact') as artifact:
            M.eligible()
            artifact.assert_called_once()
            identity.assert_called_once()
            compositor.assert_not_called()

    def test_opt_in_drift_is_rejected(self):
        with mock.patch.object(M, 'runtime_identity'), mock.patch.object(M, 'root_file', return_value=b'yes\n'):
            with self.assertRaisesRegex(RuntimeError, 'not opted'):
                M.eligible()

    def test_old_v3_opt_in_does_not_enable_the_new_kernel(self):
        with mock.patch.object(M, 'runtime_identity'), mock.patch.object(M, 'root_file', return_value=b'POCKETDS-V3-DAILY-DEEP-20260905\n'):
            with self.assertRaisesRegex(RuntimeError, 'not opted'):
                M.eligible()
        self.assertEqual((ROOT / 'components/system/daily-suspend.enabled').read_bytes(), M.TOKEN)

    def test_failed_cycle_blocks_next_ordinary_request(self):
        with mock.patch.object(M, 'runtime_identity'), mock.patch.object(M, 'root_file', return_value=M.TOKEN), mock.patch.object(M.os.path, 'lexists', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'failed cycle'):
                M.eligible()

    def test_baseline_runtime_cannot_sleep(self):
        with mock.patch.object(M, 'value', return_value='AYANEO Pocket DS'), mock.patch.object(M.os, 'uname') as uname:
            uname.return_value.release = '7.1.0-original'
            with self.assertRaisesRegex(RuntimeError, 'kernel release'):
                M.runtime_identity()

    def test_kernel_notes_drift_cannot_sleep(self):
        with mock.patch.object(M, 'value', return_value='AYANEO Pocket DS'), mock.patch.object(M.os, 'uname') as uname, mock.patch.object(Path, 'read_bytes', return_value=b'wrong notes'):
            uname.return_value.release = M.RELEASE
            with self.assertRaisesRegex(RuntimeError, 'notes differ'):
                M.runtime_identity()

    def test_storage_fault_stops_before_any_boot_image_read(self):
        with mock.patch.object(M.os, 'geteuid', return_value=0), mock.patch.object(M, 'runtime_identity'), mock.patch.object(M, 'storage_health', side_effect=RuntimeError('I/O')), mock.patch.object(M, 'root_file') as read, mock.patch.object(M, 'compositor_lid_ready') as compositor:
            with self.assertRaisesRegex(RuntimeError, 'I/O'):
                M.full_check()
            read.assert_not_called()
            compositor.assert_not_called()

    def test_full_check_refuses_unloaded_compositor_before_image_reads(self):
        with mock.patch.object(M.os, 'geteuid', return_value=0), mock.patch.object(M, 'runtime_identity'), mock.patch.object(M, 'storage_health') as storage, mock.patch.object(M, 'compositor_lid_ready', side_effect=RuntimeError('not loaded')), mock.patch.object(M, 'root_file') as read:
            with self.assertRaisesRegex(RuntimeError, 'not loaded'):
                M.full_check()
            storage.assert_called_once()
            read.assert_not_called()

    def test_dmesg_failure_is_not_treated_as_healthy(self):
        with mock.patch.object(M.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'dmesg')):
            with self.assertRaises(subprocess.CalledProcessError):
                M.storage_health()

    def test_storage_error_patterns(self):
        for text in ('EXT4-fs error', 'ufshcd device failed', 'Buffer I/O error', 'ufs fatal'):
            with self.subTest(text=text), mock.patch.object(M.subprocess, 'run') as run:
                run.return_value.stdout = text
                with self.assertRaises(RuntimeError):
                    M.storage_health()

    def test_effective_policy_and_later_override(self):
        policy = (ROOT / 'components/system/90-pocketds-daily-suspend.conf').read_text()
        with mock.patch.object(M.subprocess, 'check_output', return_value=policy):
            M.effective_policy()
        for override in ('MemorySleepMode=s2idle', 'AllowHybridSleep=yes', 'AllowSuspend=no'):
            with mock.patch.object(M.subprocess, 'check_output', return_value=policy+'\n'+override):
                with self.assertRaises(RuntimeError):
                    M.effective_policy()

    def test_stale_pending_refuses_without_hardware_access(self):
        with mock.patch.object(M, 'eligibility_policy'), mock.patch.object(M.os.path, 'lexists', return_value=True), mock.patch.object(M, 'full_check') as check:
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                M.pre()
            check.assert_not_called()

    def test_pre_failure_counter_is_not_retried(self):
        with mock.patch.object(M, 'eligibility_policy'), mock.patch.object(M.os.path, 'lexists', return_value=False), mock.patch.object(M, 'full_check', return_value={'counters': {'success': 0, 'fail': 1}}), mock.patch.object(M, 'effective_policy'), mock.patch.object(M, 'save_runtime') as save:
            with self.assertRaisesRegex(RuntimeError, 'failed suspend'):
                M.pre()
            save.assert_not_called()

    def test_post_requires_success_and_preserves_block_on_failure(self):
        before = {'boot_id': 'boot', 'counters': {'success': 1, 'fail': 0}, 'clocks': {'boot': 10, 'awake': 10}}
        for good in (True, False):
            with self.subTest(good=good), mock.patch.object(M.os, 'geteuid', return_value=0), mock.patch.object(Path, 'exists', return_value=True), mock.patch.object(Path, 'unlink') as unlink, mock.patch.object(M, 'root_file', return_value=json.dumps(before)), mock.patch.object(M, 'storage_health'), mock.patch.object(M, 'value', return_value='boot'), mock.patch.object(M, 'counters', return_value={'success': 2 if good else 1, 'fail': 0 if good else 1}), mock.patch.dict(M.os.environ, {'SERVICE_RESULT': 'success'}), mock.patch.object(M, 'clocks', return_value={'boot': 75, 'awake': 15}), mock.patch.object(M, 'save_runtime') as save:
                if good:
                    self.assertEqual(M.post()['suspended_seconds'], 60)
                    unlink.assert_called_once()
                else:
                    with self.assertRaises(RuntimeError):
                        M.post()
                    self.assertEqual(save.call_args.args[0], M.BLOCK)
                    unlink.assert_not_called()

    def test_no_direct_sleep_or_rtc_alarm_in_daily_gate(self):
        source = (ROOT / 'components/system/pocketds-daily-suspend.py').read_text()
        self.assertNotIn('rtcwake', source)
        self.assertNotIn('systemctl suspend', source)
        self.assertNotIn('write_text(', source)
        unit = (ROOT / 'components/system/30-pocketds-daily-suspend-guard.conf').read_text()
        self.assertIn('ExecStartPre=/usr/local/libexec/pocketds-daily-suspend pre', unit)
        self.assertNotIn('ExecStartPre=-', unit)

    def test_native_actions_and_manual_opt_in(self):
        profile = (ROOT / 'components/system/powerdevilrc.deep').read_text()
        for key in ('LidAction=1', 'PowerButtonAction=1', 'AutoSuspendAction=1', 'SleepMode=1'):
            self.assertEqual(profile.count(key), 3)
        installer = (ROOT / 'scripts/install-daily-suspend.sh').read_text()
        self.assertLess(installer.index('sudo systemctl daemon-reload'), installer.index('# Publish the enablement'))
        self.assertNotIn('systemctl suspend', installer)
        self.assertNotIn('systemctl restart systemd-logind', installer)
        rule = (ROOT / 'components/system/89-pocketds-daily-suspend.rules').read_text()
        self.assertIn('subject.local && subject.active', rule)
        self.assertNotIn('ignore-inhibit"', rule)

    def test_capability_reload_waits_for_polkit_and_checks_kde(self):
        installer = (ROOT / 'scripts/install-daily-suspend.sh').read_text()
        self.assertLess(installer.index('pkcheck --action-id'), installer.index('systemctl --user restart plasma-powerdevil.service'))
        self.assertIn('org.freedesktop.PowerManagement CanSuspend', installer)
        self.assertIn('KDE did not confirm the requested sleep capability', installer)

    def test_generic_redeploy_and_live_import_preserve_opt_in_separation(self):
        for name in ('install.sh', 'import-live.sh'):
            text = (ROOT / 'scripts' / name).read_text()
            self.assertIn('daily-suspend.enabled', text)
            self.assertIn('components/system/powerdevilrc.deep', text)
            self.assertIn('components/system/powerdevilrc"', text)

    def test_deployment_rejects_blocking_sleep_but_allows_idle_only_and_delays(self):
        for what, mode, allowed in [('sleep:idle', 'block', False), ('sleep', 'delay', True), ('idle', 'block', True), ('handle-lid-switch', 'block', True)]:
            reply = {'type': 'a(ssssuu)', 'data': [[[what, 'test-owner', 'reason', mode, 0, 123]]]}
            with self.subTest(what=what, mode=mode), mock.patch.object(M, 'full_check', return_value={}), mock.patch.object(M.subprocess, 'check_output', return_value=json.dumps(reply)):
                if allowed:
                    M.deployment_ready()
                else:
                    with self.assertRaisesRegex(RuntimeError, 'test-owner'):
                        M.deployment_ready()


class PowerDevilResumeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.plugin = self.directory / 'powerdevil_dpmsaction.so'
        self.plugin.write_bytes(b'fixture verified ELF artifact')
        self.qtcore = self.directory / 'libQt6Core.so.6.11.2'
        self.qtcore.write_bytes(b'fixture verified QtCore ABI')
        self.qt_alias = self.directory / 'libQt6Core.so.6'
        self.qt_alias.symlink_to(self.qtcore)
        self.pairs = ({'name': 'fixture-pair', 'dpms_sha256': M.sha(self.plugin.read_bytes()),
                       'qtcore_path': str(self.qtcore), 'qtcore_size': self.qtcore.stat().st_size,
                       'qtcore_sha256': M.sha(self.qtcore.read_bytes())},)
        self.proc = self.directory / 'proc' / '1650'
        self.proc.mkdir(parents=True)
        self.start = 12345
        self.write_start()
        (self.proc / 'status').write_text('Name:\tpowerdevil\nUid:\t1000\t1000\t1000\t1000\n')
        self.write_maps()
        self.owner_uid = 0
        real_fstat, real_lstat = os.fstat, Path.lstat

        def metadata(info):
            # Let these real temporary files model root ownership without
            # requiring sudo in the offline fixture; all other stat fields
            # and all reads/open flags come from the real filesystem.
            return SimpleNamespace(**{name: self.owner_uid if name == 'st_uid' else getattr(info, name)
                                      for name in ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_nlink',
                                                   'st_size', 'st_mtime_ns', 'st_ctime_ns')})

        patches = [mock.patch.object(M, 'DPMS_PLUGIN', self.plugin),
                   mock.patch.object(M, 'POWERDEVIL_RUNTIME_PAIRS', self.pairs),
                   mock.patch.object(M, 'QTCORE_ALIAS', self.qt_alias),
                   mock.patch.object(M, 'PROC', self.proc.parent),
                   mock.patch.object(M.os, 'fstat', side_effect=lambda fd: metadata(real_fstat(fd))),
                   mock.patch.object(Path, 'lstat', autospec=True, side_effect=lambda path: metadata(real_lstat(path))),
                   mock.patch.object(M.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1000))]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        bus_patcher = mock.patch.object(M.subprocess, 'check_output', side_effect=self.bus_reply)
        self.bus = bus_patcher.start()
        self.addCleanup(bus_patcher.stop)

    def write_start(self):
        # Include ')' and spaces in comm to exercise real /proc/stat parsing.
        (self.proc / 'stat').write_text('1650 (powerdevil ) fixture) S ' + ' '.join(['0'] * 18 + [str(self.start), '0']) + '\n')

    def write_maps(self, inode=None, device=None, pathname=None, permissions='r-xp',
                   qt_inode=None, qt_pathname=None, qt_permissions='r-xp'):
        info = self.plugin.stat()
        device = device or f'{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}'
        pathname = str(self.plugin) if pathname is None else pathname
        (self.proc / 'maps').write_text(f'1000-2000 {permissions} 00000000 {device} {info.st_ino if inode is None else inode} {pathname}\n')
        qt_info = self.qtcore.stat()
        qt_device = f'{os.major(qt_info.st_dev):02x}:{os.minor(qt_info.st_dev):02x}'
        with (self.proc / 'maps').open('a') as stream:
            stream.write(f'3000-4000 {qt_permissions} 00000000 {qt_device} {qt_info.st_ino if qt_inode is None else qt_inode} {self.qtcore if qt_pathname is None else qt_pathname}\n')


    def bus_reply(self, command, **kwargs):
        method = command[-3]
        result = {'type': 'u', 'data': [1650]} if method == 'GetConnectionUnixProcessID' else {'type': 's', 'data': [':1.50']}
        return json.dumps(result)

    def test_live_bus_owner_maps_the_exact_verified_inode(self):
        result = M.powerdevil_resume_ready()
        self.assertEqual(result['pid'], 1650)
        self.assertEqual(result['owner'], ':1.50')
        self.assertEqual(result['inode'], self.plugin.stat().st_ino)
        self.assertEqual(self.bus.call_count, 3)
        for call in self.bus.call_args_list:
            command = call.args[0]
            self.assertIn('--auto-start=no', command)
            self.assertIn('--timeout=1s', command)
            self.assertIn('--address=unix:path=/run/user/1000/bus', command)
            self.assertEqual(call.kwargs['timeout'], 1.5)
        self.assertEqual(self.bus.call_args_list[1].args[0][-1], ':1.50')

    def test_unpinned_artifact_fails_before_reading_or_bus(self):
        for pin in (None, (), 'current', ({'dpms_sha256': '0' * 63},)):
            with self.subTest(pin=pin), mock.patch.object(M, 'POWERDEVIL_RUNTIME_PAIRS', pin), mock.patch.object(M, 'read_regular_bounded') as read:
                with self.assertRaisesRegex(RuntimeError, 'not pinned'):
                    M.powerdevil_artifact()
                read.assert_not_called()
        self.bus.assert_not_called()

    def test_distribution_replacement_rejects_deep_without_touching_bus(self):
        self.plugin.write_bytes(b'old distribution plugin')
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            M.powerdevil_resume_ready()
        self.bus.assert_not_called()

    def test_untrusted_file_owner_modes_and_hardlinks_are_rejected(self):
        self.owner_uid = 1000
        with self.assertRaisesRegex(RuntimeError, 'untrusted'):
            M.powerdevil_artifact()
        self.owner_uid = 0
        self.plugin.chmod(0o666)
        with self.assertRaisesRegex(RuntimeError, 'untrusted'):
            M.powerdevil_artifact()
        self.plugin.chmod(0o644)
        os.link(self.plugin, self.directory / 'hardlink')
        with self.assertRaisesRegex(RuntimeError, 'untrusted'):
            M.powerdevil_artifact()

    def test_symlink_fifo_and_oversize_files_fail_without_blocking(self):
        other = self.directory / 'original'
        self.plugin.rename(other)
        self.plugin.symlink_to(other)
        with self.assertRaises(OSError):
            M.powerdevil_artifact()
        self.plugin.unlink()
        os.mkfifo(self.plugin)
        with self.assertRaisesRegex(RuntimeError, 'regular file'):
            M.powerdevil_artifact()
        self.plugin.unlink()
        self.plugin.write_bytes(b'x' * 32)
        with mock.patch.object(M, 'DPMS_PLUGIN_MAX', 16):
            with self.assertRaisesRegex(RuntimeError, 'bounded'):
                M.powerdevil_artifact()

    def test_deleted_old_mapping_is_rejected_even_with_a_good_new_mapping(self):
        good = (self.proc / 'maps').read_text()
        self.write_maps(pathname=str(self.plugin) + ' (deleted)')
        with (self.proc / 'maps').open('a') as stream:
            stream.write(good)
        with self.assertRaisesRegex(RuntimeError, 'deleted'):
            M.powerdevil_resume_ready()

    def test_alternate_path_old_inode_and_wrong_device_are_rejected(self):
        for kwargs, reason in [({'pathname': '/tmp/powerdevil_dpmsaction.so'}, 'alternate'),
                               ({'inode': self.plugin.stat().st_ino + 1}, 'different'),
                               ({'device': 'ffff:ffff'}, 'different')]:
            with self.subTest(kwargs=kwargs):
                self.write_maps(**kwargs)
                with self.assertRaisesRegex(RuntimeError, reason):
                    M.powerdevil_resume_ready()

    def test_missing_nonexecutable_and_malformed_mappings_are_rejected(self):
        for text in ('', '1000-2000 r--p 0000 00:00 0 [heap]\n'):
            (self.proc / 'maps').write_text(text)
            with self.assertRaisesRegex(RuntimeError, 'not loaded'):
                M.powerdevil_resume_ready()
        self.write_maps(permissions='r--p')
        with self.assertRaisesRegex(RuntimeError, 'not loaded'):
            M.powerdevil_resume_ready()
        self.write_maps(device='bad:address')
        with self.assertRaisesRegex(RuntimeError, 'malformed'):
            M.powerdevil_resume_ready()

    def test_mixed_or_wrong_process_uids_are_rejected(self):
        for status in ('Uid:\t0\t0\t0\t0\n', 'Uid:\t1000\t0\t1000\t1000\n', 'Name:\tpowerdevil\n'):
            (self.proc / 'status').write_text(status)
            with self.assertRaisesRegex(RuntimeError, 'desktop user'):
                M.powerdevil_resume_ready()

    def test_restart_and_pid_reuse_during_verification_are_rejected(self):
        with mock.patch.object(M, 'process_start', side_effect=[1, 2]):
            with self.assertRaisesRegex(RuntimeError, 'owner changed'):
                M.powerdevil_resume_ready()
        with mock.patch.object(M, 'powerdevil_bus', side_effect=[':1.50', 1650, ':1.51']):
            with self.assertRaisesRegex(RuntimeError, 'owner changed'):
                M.powerdevil_resume_ready()

    def test_file_exchange_after_maps_read_is_rejected(self):
        original = self.bus_reply

        def exchange(command, **kwargs):
            if self.bus.call_count == 3:
                replacement = self.directory / 'replacement'
                replacement.write_bytes(self.plugin.read_bytes())
                os.replace(replacement, self.plugin)
            return original(command, **kwargs)

        self.bus.side_effect = exchange
        with self.assertRaisesRegex(RuntimeError, 'replaced'):
            M.powerdevil_resume_ready()

    def test_method_reply_requires_a_single_typed_pid(self):
        for reply in ({'type': 'u', 'data': True}, {'type': 'u', 'data': [True]},
                      {'type': 'u', 'data': [0]}, {'type': 'u', 'data': [-1]},
                      {'type': 'u', 'data': ['1650']}, {'type': 'i', 'data': [1650]},
                      {'type': 'u', 'data': [1650, 1651]}, None):
            with self.subTest(reply=reply):
                self.bus.return_value = json.dumps(reply)
                self.bus.side_effect = None
                with self.assertRaises(RuntimeError):
                    M.powerdevil_bus(1000, 'GetConnectionUnixProcessID', ':1.50', 'u')

    def test_owner_query_timeout_and_disappearing_process_fail_closed(self):
        self.bus.side_effect = subprocess.TimeoutExpired('busctl', 1.5)
        with self.assertRaises(subprocess.TimeoutExpired):
            M.powerdevil_resume_ready()
        self.bus.side_effect = self.bus_reply
        (self.proc / 'maps').unlink()
        with self.assertRaises(FileNotFoundError):
            M.powerdevil_resume_ready()

    def test_well_known_or_malformed_bus_names_are_not_stable_owners(self):
        for owner in (M.POWERDEVIL_BUS_NAME, '', ':1', ':1.2/../3', 1650, None):
            with self.subTest(owner=owner):
                self.bus.side_effect = None
                self.bus.return_value = json.dumps({'type': 's', 'data': [owner]})
                with self.assertRaisesRegex(RuntimeError, 'invalid PowerDevil bus owner'):
                    M.powerdevil_resume_ready()

    def test_atomic_exchange_during_hash_read_is_rejected(self):
        original_read = os.read
        exchanged = False

        def exchange(fd, size):
            nonlocal exchanged
            data = original_read(fd, size)
            if not exchanged:
                exchanged = True
                replacement = self.directory / 'replacement'
                replacement.write_bytes(self.plugin.read_bytes())
                os.replace(replacement, self.plugin)
            return data

        with mock.patch.object(M.os, 'read', side_effect=exchange):
            with self.assertRaisesRegex(RuntimeError, 'changed while reading'):
                M.powerdevil_artifact()

    def test_eligible_uses_only_bounded_pair_check_not_runtime_queries(self):
        with mock.patch.object(M, 'eligibility_policy'), mock.patch.object(M, 'powerdevil_resume_ready') as runtime, mock.patch.object(M, 'storage_health') as storage:
            M.eligible()
            runtime.assert_not_called()
            storage.assert_not_called()
            self.bus.assert_not_called()

    def test_storage_failure_precedes_plugin_reads_in_full_precheck(self):
        with mock.patch.object(M.os, 'geteuid', return_value=0), mock.patch.object(M, 'runtime_identity'), mock.patch.object(M, 'storage_health', side_effect=RuntimeError('I/O fault')), mock.patch.object(M, 'powerdevil_artifact') as artifact:
            with self.assertRaisesRegex(RuntimeError, 'I/O fault'):
                M.full_check()
            artifact.assert_not_called()

    def test_exact_runtime_pairs_reject_both_cross_combinations(self):
        first_plugin = self.plugin.read_bytes()
        second_plugin = b'fixture second reviewed DPMS artifact'
        second_qt = self.directory / 'libQt6Core.so.6.11.1'
        second_qt.write_bytes(b'fixture second reviewed QtCore ABI')
        pairs = self.pairs + ({'name': 'fixture-qt6111-p2', 'dpms_sha256': M.sha(second_plugin),
                              'qtcore_path': str(second_qt), 'qtcore_size': second_qt.stat().st_size,
                              'qtcore_sha256': M.sha(second_qt.read_bytes())},)
        with mock.patch.object(M, 'POWERDEVIL_RUNTIME_PAIRS', pairs):
            self.assertEqual(M.powerdevil_artifact()['runtime_pair'], 'fixture-pair')
            self.plugin.write_bytes(second_plugin)
            with self.assertRaisesRegex(RuntimeError, 'runtime pair is not verified'):
                M.powerdevil_artifact()  # pocketds2 with the published Qt 6.11.2 runtime.
            self.qt_alias.unlink(); self.qt_alias.symlink_to(second_qt)
            self.assertEqual(M.powerdevil_artifact()['runtime_pair'], 'fixture-qt6111-p2')
            self.plugin.write_bytes(first_plugin)
            with self.assertRaisesRegex(RuntimeError, 'runtime pair is not verified'):
                M.powerdevil_artifact()  # pocketds1 with the daily Qt 6.11.1 runtime.
        self.bus.assert_not_called()

    def test_same_qt_filename_with_changed_bytes_is_rejected(self):
        self.qtcore.write_bytes(b'changed QtCore despite same version filename')
        with self.assertRaisesRegex(RuntimeError, 'QtCore runtime differs'):
            M.powerdevil_artifact()
        self.bus.assert_not_called()

    def test_stale_deleted_or_alternate_live_qtcore_is_rejected(self):
        for kwargs, reason in [({'qt_inode': self.qtcore.stat().st_ino + 1}, 'different QtCore'),
                               ({'qt_pathname': str(self.qtcore) + ' (deleted)'}, 'deleted'),
                               ({'qt_pathname': '/tmp/libQt6Core.so.6.11.2'}, 'alternate'),
                               ({'qt_pathname': str(self.directory / 'libQt6Core.so.6.11.1')}, 'alternate'),
                               ({'qt_permissions': 'r--p'}, 'not loaded')]:
            with self.subTest(kwargs=kwargs):
                self.write_maps(**kwargs)
                with self.assertRaisesRegex(RuntimeError, reason):
                    M.powerdevil_resume_ready()

    def test_deleted_qtcore_rejected_even_with_a_good_mapping(self):
        self.write_maps(qt_pathname=str(self.qtcore) + ' (deleted)')
        bad = (self.proc / 'maps').read_text()
        self.write_maps()
        with (self.proc / 'maps').open('a') as stream: stream.write(bad)
        with self.assertRaisesRegex(RuntimeError, 'deleted'):
            M.powerdevil_resume_ready()

    def test_qtcore_exchange_after_maps_read_is_rejected(self):
        original = self.bus_reply
        def exchange(command, **kwargs):
            if self.bus.call_count == 3:
                replacement = self.directory / 'replacement-qt'
                replacement.write_bytes(self.qtcore.read_bytes())
                os.replace(replacement, self.qtcore)
            return original(command, **kwargs)
        self.bus.side_effect = exchange
        with self.assertRaisesRegex(RuntimeError, 'QtCore.*replaced'):
            M.powerdevil_resume_ready()

    def test_qt_loader_alias_exchange_during_read_is_rejected(self):
        original_read = M.read_regular_bounded
        def exchange(path, *args, **kwargs):
            result = original_read(path, *args, **kwargs)
            if path == self.qtcore:
                self.qt_alias.unlink(); self.qt_alias.symlink_to(self.qtcore)
            return result
        with mock.patch.object(M, 'read_regular_bounded', side_effect=exchange):
            with self.assertRaisesRegex(RuntimeError, 'alias changed'):
                M.powerdevil_artifact()

    def test_dpms_exchange_while_qtcore_is_checked_rejects_mixed_snapshot(self):
        original_read = M.read_regular_bounded
        def exchange(path, *args, **kwargs):
            result = original_read(path, *args, **kwargs)
            if path == self.qtcore:
                replacement = self.directory / 'replacement-plugin'
                replacement.write_bytes(self.plugin.read_bytes())
                os.replace(replacement, self.plugin)
            return result
        with mock.patch.object(M, 'read_regular_bounded', side_effect=exchange):
            with self.assertRaisesRegex(RuntimeError, 'plugin was replaced'):
                M.powerdevil_artifact()

    def test_pre_rejects_stale_running_plugin_without_recording_a_sleep(self):
        with mock.patch.object(M, 'eligibility_policy'), mock.patch.object(M.os.path, 'lexists', return_value=False), mock.patch.object(M, 'full_check', side_effect=RuntimeError('PowerDevil still maps a different resume plugin')), mock.patch.object(M, 'save_runtime') as save:
            with self.assertRaisesRegex(RuntimeError, 'different resume plugin'):
                M.pre()
            save.assert_not_called()


if __name__ == '__main__':
    unittest.main()

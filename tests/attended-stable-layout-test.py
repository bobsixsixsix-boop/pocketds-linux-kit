#!/usr/bin/env python3
"""Offline-only stable-layout gates; no device query or sleep is executed."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'tools/kernel-ab/attended-stable-layout-test.py'
SPEC = importlib.util.spec_from_file_location('stable_layout_candidate', SOURCE)
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


def outputs_fixture():
    # Literal accepted topology, independent of the implementation's EXPECTED.
    return {'outputs': [
        {'name': 'DSI-1', 'connected': True, 'enabled': True, 'type': 7,
         'clones': [], 'replicationSource': 0, 'currentModeId': 'upper-120',
         'modes': [{'id': 'upper-120', 'size': {'width': 1080, 'height': 1920},
                    'refreshRate': 120.0}],
         'scale': 1.5, 'rotation': 2, 'pos': {'x': 0, 'y': 0}, 'priority': 1},
        {'name': 'DSI-2', 'connected': True, 'enabled': True, 'type': 7,
         'clones': [], 'replicationSource': 0, 'currentModeId': 'lower-60',
         'modes': [{'id': 'lower-60', 'size': {'width': 768, 'height': 1024},
                    'refreshRate': 59.999}],
         'scale': 1.25, 'rotation': 2, 'pos': {'x': 283, 'y': 720}, 'priority': 2},
    ]}


class OutputValidationTests(unittest.TestCase):
    def test_accepted_fixed_dual_layout_and_reordered_inventory(self):
        snapshot = outputs_fixture()
        app.validate_outputs(snapshot)
        snapshot['outputs'].reverse()
        app.validate_outputs(snapshot)

    def test_rejects_extra_missing_duplicate_or_external_outputs(self):
        for change in ('extra', 'missing', 'duplicate', 'external_name'):
            with self.subTest(change=change):
                snapshot = outputs_fixture()
                if change == 'extra':
                    snapshot['outputs'].append({'name': 'HDMI-1'})
                elif change == 'missing':
                    snapshot['outputs'].pop()
                elif change == 'duplicate':
                    snapshot['outputs'][1]['name'] = 'DSI-1'
                else:
                    snapshot['outputs'][1]['name'] = 'HDMI-1'
                with self.assertRaises(RuntimeError):
                    app.validate_outputs(snapshot)

    def test_rejects_disabled_disconnected_external_or_mirrored_panels(self):
        changes = [('enabled', False), ('connected', False), ('type', 8),
                   ('clones', [2]), ('replicationSource', 1)]
        for index in (0, 1):
            for key, value in changes:
                with self.subTest(index=index, key=key):
                    snapshot = outputs_fixture()
                    snapshot['outputs'][index][key] = value
                    with self.assertRaises(RuntimeError):
                        app.validate_outputs(snapshot)

    def test_rejects_missing_or_ambiguous_active_mode(self):
        for change in ('missing', 'duplicate'):
            with self.subTest(change=change):
                snapshot = outputs_fixture()
                upper = snapshot['outputs'][0]
                if change == 'missing':
                    upper['currentModeId'] = 'not-present'
                else:
                    upper['modes'].append(copy.deepcopy(upper['modes'][0]))
                with self.assertRaises(RuntimeError):
                    app.validate_outputs(snapshot)

    def test_rejects_resolution_refresh_scale_rotation_position_priority_drift(self):
        for index in (0, 1):
            for change in ('resolution', 'refresh', 'scale', 'rotation', 'position', 'priority'):
                with self.subTest(index=index, change=change):
                    snapshot = outputs_fixture()
                    output = snapshot['outputs'][index]
                    if change == 'resolution':
                        output['modes'][0]['size']['width'] += 1
                    elif change == 'refresh':
                        output['modes'][0]['refreshRate'] += 1
                    elif change == 'scale':
                        output['scale'] += 0.25
                    elif change == 'rotation':
                        output['rotation'] = 1
                    elif change == 'position':
                        output['pos']['y'] += 1
                    else:
                        output['priority'] = 3
                    with self.assertRaises(RuntimeError):
                        app.validate_outputs(snapshot)

    def test_rejects_nonfinite_refresh_and_nonboolean_enable_state(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(value=value):
                snapshot = outputs_fixture()
                snapshot['outputs'][0]['modes'][0]['refreshRate'] = value
                with self.assertRaises(RuntimeError):
                    app.validate_outputs(snapshot)
        snapshot = outputs_fixture()
        snapshot['outputs'][0]['enabled'] = 1
        with self.assertRaises(RuntimeError):
            app.validate_outputs(snapshot)

    def test_rejects_boolean_or_wrong_type_numeric_fields(self):
        cases = [('type', 7.0), ('replicationSource', False), ('replicationSource', 0.0),
                 ('currentModeId', 1), ('priority', True), ('rotation', 2.0),
                 ('scale', '1.5'), ('width', 1080.0), ('pos_x', False),
                 ('refreshRate', True), ('refreshRate', '120')]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                snapshot = outputs_fixture()
                output = snapshot['outputs'][0]
                if key == 'width':
                    output['modes'][0]['size']['width'] = value
                elif key == 'pos_x':
                    output['pos']['x'] = value
                elif key == 'refreshRate':
                    output['modes'][0]['refreshRate'] = value
                else:
                    output[key] = value
                with self.assertRaises(RuntimeError):
                    app.validate_outputs(snapshot)


class PinnedFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'candidate'
        self.path.write_bytes(b'accepted candidate\n')
        self.path.chmod(0o600)
        self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.real_fstat = os.fstat

    def fake_metadata(self, *, uid=1000, mode=None, size=None):
        def inspect(descriptor):
            actual = self.real_fstat(descriptor)
            return SimpleNamespace(st_uid=uid,
                                   st_mode=actual.st_mode if mode is None else mode,
                                   st_size=actual.st_size if size is None else size)
        return patch.object(app.os, 'fstat', side_effect=inspect)

    def test_accepts_exact_private_user_file_without_changing_bytes(self):
        before = self.path.read_bytes()
        with self.fake_metadata():
            app.pinned_user_file(self.path, self.digest)
        self.assertEqual(self.path.read_bytes(), before)

    def test_rejects_changed_file_hash(self):
        self.path.write_bytes(b'changed candidate\n')
        with self.fake_metadata(), self.assertRaisesRegex(RuntimeError, 'candidate file changed'):
            app.pinned_user_file(self.path, self.digest)

    def test_rejects_untrusted_metadata(self):
        cases = ({'uid': 0}, {'uid': 1001},
                 {'mode': stat.S_IFREG | 0o620}, {'mode': stat.S_IFREG | 0o602},
                 {'mode': stat.S_IFDIR | 0o700}, {'size': 0}, {'size': 262145})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.fake_metadata(**kwargs):
                with self.assertRaisesRegex(RuntimeError, 'untrusted candidate file'):
                    app.pinned_user_file(self.path, self.digest)

    def test_rejects_symlink_without_reading_target(self):
        alias = self.path.with_name('alias')
        alias.symlink_to(self.path)
        with self.fake_metadata(), self.assertRaises(OSError):
            app.pinned_user_file(alias, self.digest)

    def test_rejects_fifo_without_waiting_for_a_writer(self):
        fifo = self.path.with_name('fifo')
        os.mkfifo(fifo, 0o600)
        with self.fake_metadata(), self.assertRaisesRegex(RuntimeError, 'untrusted candidate file'):
            app.pinned_user_file(fifo, self.digest)

    def test_rejects_oversize_read_even_if_stat_size_was_small(self):
        self.path.write_bytes(b'x' * 262145)
        expected = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.fake_metadata(size=1), self.assertRaisesRegex(RuntimeError, 'candidate file changed'):
            app.pinned_user_file(self.path, expected)


class ScreenQueryTests(unittest.TestCase):
    def test_query_uses_fixed_unprivileged_session_and_timeout(self):
        with patch.object(app.subprocess, 'check_output', return_value='{}') as command:
            self.assertEqual(app.screen_query('-j'), '{}')
        argv = command.call_args.args[0]
        self.assertEqual(argv[:5], ['/usr/bin/runuser', '-u', 'pocketds', '--', '/usr/bin/env'])
        self.assertEqual(argv[-2:], ['/usr/bin/kscreen-doctor', '-j'])
        self.assertIn('QT_LINUX_ACCESSIBILITY_ALWAYS_ON=0', argv)
        self.assertEqual(command.call_args.kwargs, {'text': True, 'timeout': 10})

    def test_query_limit_counts_utf8_bytes_not_characters(self):
        for reply in ('x' * 131073, '\u00e9' * 65537):
            with self.subTest(length=len(reply)), \
                 patch.object(app.subprocess, 'check_output', return_value=reply):
                with self.assertRaisesRegex(RuntimeError, 'oversized screen query reply'):
                    app.screen_query('-j')
        boundary = '\u00e9' * 65536
        with patch.object(app.subprocess, 'check_output', return_value=boundary):
            self.assertEqual(app.screen_query('-j'), boundary)


class CandidateGateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.backlight = Path(self.directory.name)
        for name in ('ae94000.dsi.0', 'sy7758-backlight'):
            (self.backlight / name).mkdir()
            (self.backlight / name / 'bl_power').write_text('4\n')
        for field in ('brightness', 'actual_brightness'):
            (self.backlight / 'sy7758-backlight' / field).write_text('0\n')
        self.backlight_patch = patch.object(app, 'BACKLIGHT', self.backlight)
        self.backlight_patch.start()
        self.addCleanup(self.backlight_patch.stop)
        self.files_patch = patch.object(app, 'pinned_user_file')
        self.files = self.files_patch.start()
        self.addCleanup(self.files_patch.stop)
        self.snapshot = outputs_fixture()
        self.dpms = 'dpms mode for screen DSI-1: off\ndpms mode for screen DSI-2: off\n'
        self.query_patch = patch.object(app, 'screen_query', side_effect=self.query)
        self.queries = self.query_patch.start()
        self.addCleanup(self.query_patch.stop)

    def query(self, *args):
        if args == ('-j',):
            return json.dumps(self.snapshot)
        self.assertEqual(args, ('--dpms', 'show'))
        return self.dpms

    def test_read_only_open_preflight_does_not_require_display_off(self):
        self.dpms = 'not queried'
        result = app.candidate_check(closed=False)
        self.queries.assert_called_once_with('-j')
        self.assertFalse(result['closed_display_off_checked'])
        self.assertEqual(result['prior_hall_physical_acceptance'], 'FAILED')
        self.assertEqual(self.files.call_count, 3)

    def test_closed_preflight_requires_both_dpms_and_physical_blank(self):
        result = app.candidate_check(closed=True)
        self.assertTrue(result['closed_display_off_checked'])
        self.assertEqual(result['kind'], 'retained-dual-layout')
        self.assertEqual(result['outputs'], self.snapshot)
        self.assertEqual(self.files.call_args.args, (app.CONFIG, app.CONFIG_SHA))

    def test_rejects_on_missing_duplicate_or_invalid_dpms(self):
        states = ['dpms mode for screen DSI-1: on\ndpms mode for screen DSI-2: off\n',
                  'dpms mode for screen DSI-2: off\n',
                  self.dpms + 'dpms mode for screen DSI-1: off\n',
                  self.dpms + 'dpms mode for screen HDMI-1: off\n',
                  'garbled output\n', '']
        for value in states:
            with self.subTest(value=value):
                self.dpms = value
                with self.assertRaises(RuntimeError):
                    app.candidate_check(closed=True)

    def test_rejects_either_unblanked_backlight_or_nonzero_lower_light(self):
        paths = [self.backlight / name / 'bl_power'
                 for name in ('ae94000.dsi.0', 'sy7758-backlight')]
        paths += [self.backlight / 'sy7758-backlight' / field
                  for field in ('brightness', 'actual_brightness')]
        for path in paths:
            with self.subTest(path=path):
                original = path.read_text()
                path.write_text('0\n' if path.name == 'bl_power' else '1\n')
                try:
                    with self.assertRaises(RuntimeError):
                        app.candidate_check(closed=True)
                finally:
                    path.write_text(original)

    def test_rejects_config_change_after_live_queries(self):
        self.files.side_effect = [None, None, RuntimeError('candidate file changed')]
        with self.assertRaisesRegex(RuntimeError, 'candidate file changed'):
            app.candidate_check(closed=True)

    def test_rejects_initial_config_or_helper_change_before_query(self):
        for side_effect in ([RuntimeError('config changed')],
                            [None, RuntimeError('helper changed')]):
            with self.subTest(side_effect=side_effect):
                self.files.side_effect = side_effect
                self.queries.reset_mock()
                with self.assertRaises(RuntimeError):
                    app.candidate_check(closed=False)
                self.queries.assert_not_called()


class PredecessorTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.base = SimpleNamespace(BOOT='authorized-boot')
        self.verifier = Mock()
        self.verifier.storage_health.side_effect = lambda: self.events.append('health')
        self.before = {'state': {'boot_id': self.base.BOOT,
                                 'counters': {'success': 1, 'fail': 0}}}
        self.result = {'kernel_cycle': 'returned', 'counters': {'success': 2, 'fail': 0},
                       'suspended_seconds': 128.367,
                       'physical_display_acceptance': 'pending'}
        def receipt(path):
            self.events.append(path.name)
            return json.dumps(self.before if path.name == 'before.json' else self.result).encode()
        self.verifier.root_file.side_effect = receipt
        self.base.load_verifier = Mock(return_value=self.verifier)

    def prepare(self):
        # Replace only module construction/execution; never import device GI checks.
        fake_spec = SimpleNamespace(loader=Mock())
        with patch.object(app.importlib.util, 'spec_from_file_location', return_value=fake_spec), \
             patch.object(app.importlib.util, 'module_from_spec', return_value=self.base):
            return app.prepare()

    def test_fixed_third_parameters_and_health_before_trusted_receipts(self):
        prepared = self.prepare()
        self.assertEqual(self.events, ['health', 'before.json', 'result.json'])
        self.assertEqual(prepared.OUT, Path('/run/pds001-v4-lid-3'))
        self.assertEqual(prepared.EXPECTED_SUCCESS, 2)
        self.assertEqual(prepared.RTC_SECONDS, 180)
        self.assertIs(prepared.candidate_check, app.candidate_check)
        # A returned kernel cycle does not magically become physical acceptance.
        self.assertEqual(self.result['physical_display_acceptance'], 'pending')

    def test_rejects_wrong_boot_or_starting_counter_receipt(self):
        for change in ('boot', 'start_success', 'start_fail'):
            with self.subTest(change=change):
                original = copy.deepcopy(self.before)
                if change == 'boot':
                    self.before['state']['boot_id'] = 'other-boot'
                elif change == 'start_success':
                    self.before['state']['counters']['success'] = 0
                else:
                    self.before['state']['counters']['fail'] = 1
                with self.assertRaisesRegex(RuntimeError, 'starting receipt'):
                    self.prepare()
                self.before = original

    def test_rejects_wrong_return_count_duration_or_incomplete_receipt(self):
        for change in ('return', 'success', 'fail', 'duration'):
            with self.subTest(change=change):
                original = copy.deepcopy(self.result)
                if change == 'return':
                    self.result['kernel_cycle'] = 'incomplete'
                elif change == 'success':
                    self.result['counters']['success'] = 1
                elif change == 'fail':
                    self.result['counters']['fail'] = 1
                else:
                    self.result['suspended_seconds'] = 119.153
                with self.assertRaisesRegex(RuntimeError, 'second-cycle kernel return'):
                    self.prepare()
                self.result = original

    def test_storage_failure_stops_before_any_receipt_read(self):
        self.verifier.storage_health.side_effect = RuntimeError('storage failed')
        with self.assertRaisesRegex(RuntimeError, 'storage failed'):
            self.prepare()
        self.verifier.root_file.assert_not_called()

    def test_untrusted_predecessor_receipt_refuses(self):
        self.verifier.root_file.side_effect = RuntimeError('untrusted root file')
        with self.assertRaisesRegex(RuntimeError, 'untrusted root file'):
            self.prepare()


if __name__ == '__main__':
    unittest.main()

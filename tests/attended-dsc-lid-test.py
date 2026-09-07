#!/usr/bin/env python3
"""Offline lifecycle checks; these fixtures never request actual suspend."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'tools/kernel-ab/attended-dsc-lid-test.py'
spec = importlib.util.spec_from_file_location('attended', SOURCE)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.policy = root / 'baseline.conf'
        self.policy.write_bytes(b'[Sleep]\nAllowSuspend=no\n')
        self.out = root / 'evidence'
        self.stack.enter_context(patch.object(app, 'OUT', self.out))
        self.stack.enter_context(patch.object(app, 'POLICY', self.policy))
        self.stack.enter_context(patch.object(app.os, 'geteuid', return_value=0))
        self.stack.enter_context(patch.object(app.os, 'umask'))
        self.stack.enter_context(patch.object(app.signal, 'signal'))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.verifier = Mock(STORAGE_ERROR=re.compile('I/O error'))
        self.verifier.value.side_effect = [app.BOOT, '']
        self.verifier.counters.return_value = {'success': 1, 'fail': 0}
        self.stack.enter_context(patch.object(app, 'load_verifier', return_value=self.verifier))
        self.stack.enter_context(patch.object(app, 'check', return_value={'boot_id': app.BOOT}))
        self.lid = self.stack.enter_context(patch.object(app, 'lid_closed', return_value=True))
        self.calls = []
        def command(*args):
            self.calls.append(args)
            return '{"execution_ready":true}' if args == (str(app.GUARD), 'check') else ''
        self.stack.enter_context(patch.object(app, 'call', side_effect=command))
        self.stack.enter_context(patch.object(app, 'clocks', side_effect=[(100, 100), (125, 105)]))
        self.run = self.stack.enter_context(patch.object(app.subprocess, 'run', return_value=Mock(returncode=0)))

    def invoke(self, command):
        with patch.object(sys, 'argv', ['test', command]):
            app.main()

    def test_check_has_no_mutation_or_dispatch(self):
        self.invoke('check')
        self.assertFalse(self.out.exists())
        self.assertEqual(self.calls, [])
        self.run.assert_not_called()

    def test_open_lid_refuses_before_mutation(self):
        self.lid.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'close physical lid'):
            self.invoke('run')
        self.assertFalse(self.out.exists())
        self.run.assert_not_called()

    def test_one_cycle_cleans_policy_and_reports_only_pending_display(self):
        self.invoke('run')
        self.run.assert_called_once_with([str(app.GUARD), 'run', '120'], timeout=175)
        self.assertEqual(self.calls[-1], ('umount', str(self.policy)))
        self.assertEqual(self.policy.read_bytes(), b'[Sleep]\nAllowSuspend=no\n')
        result = json.loads((self.out / 'result.json').read_text())
        self.assertEqual(result['suspended_seconds'], 20)
        self.assertEqual(result['physical_display_acceptance'], 'pending')

    def test_failed_guard_still_restores_policy(self):
        self.run.return_value.returncode = 6
        with self.assertRaisesRegex(RuntimeError, 'guard did not complete'):
            self.invoke('run')
        self.assertEqual(self.calls[-1], ('umount', str(self.policy)))
        self.assertEqual(json.loads((self.out / 'result.json').read_text()), 'incomplete')

    def test_fixed_followup_parameters_keep_one_dispatch_and_pending_acceptance(self):
        self.verifier.counters.return_value = {'success': 2, 'fail': 0}
        with patch.object(app, 'EXPECTED_SUCCESS', 1), patch.object(app, 'RTC_SECONDS', 180):
            self.invoke('run')
        self.run.assert_called_once_with([str(app.GUARD), 'run', '180'], timeout=235)
        self.assertEqual(self.calls[-1], ('umount', str(self.policy)))
        result = json.loads((self.out / 'result.json').read_text())
        self.assertEqual(result['physical_display_acceptance'], 'pending')

    def test_followup_cannot_reuse_first_cycle_count(self):
        with patch.object(app, 'EXPECTED_SUCCESS', 1):
            with self.assertRaisesRegex(RuntimeError, 'unexpected counters'):
                self.invoke('run')
        self.assertEqual(self.calls[-1], ('umount', str(self.policy)))

    def test_lid_opened_after_enable_refuses_and_restores(self):
        self.lid.side_effect = [True, True, False]
        with self.assertRaisesRegex(RuntimeError, 'lid opened before dispatch'):
            self.invoke('run')
        self.run.assert_not_called()
        self.assertEqual(self.calls[-1], ('umount', str(self.policy)))

    def test_candidate_preflight_refusal_prevents_evidence_policy_and_dispatch(self):
        for command in ('check', 'run'):
            with self.subTest(command=command), \
                 patch.object(app, 'candidate_check', side_effect=RuntimeError('candidate drift')) as gate:
                with self.assertRaisesRegex(RuntimeError, 'candidate drift'):
                    self.invoke(command)
                gate.assert_called_once_with(closed=False)
                self.assertFalse(self.out.exists())
                self.assertEqual(self.calls, [])
                self.run.assert_not_called()

    def test_candidate_dispatch_refusal_restores_policy_without_sleep(self):
        with patch.object(app, 'candidate_check',
                          side_effect=[{'initial': 'okay'}, RuntimeError('closed display lit')]) as gate:
            with self.assertRaisesRegex(RuntimeError, 'closed display lit'):
                self.invoke('run')
        self.assertEqual([entry.kwargs for entry in gate.call_args_list],
                         [{'closed': False}, {'closed': True}])
        self.assertEqual(self.calls[-1], ('umount', str(self.policy)))
        self.assertEqual(self.policy.read_bytes(), b'[Sleep]\nAllowSuspend=no\n')
        self.assertEqual(json.loads((self.out / 'result.json').read_text()), 'incomplete')
        self.assertTrue(self.out.exists())  # The failed one-use receipt stays consumed.
        self.run.assert_not_called()

    def test_candidate_dispatch_gate_runs_after_guard_readiness_before_sleep(self):
        events = []
        original_call = app.call
        def command(*args):
            events.append(args[0])
            return original_call(*args)
        def candidate(*, closed):
            events.append('candidate-closed' if closed else 'candidate-open')
            return {'closed': closed}
        self.run.side_effect = lambda *args, **kwargs: (events.append('sleep') or Mock(returncode=0))
        with patch.object(app, 'call', side_effect=command), \
             patch.object(app, 'candidate_check', side_effect=candidate):
            self.invoke('run')
        self.assertLess(events.index('mount'), events.index('candidate-closed'))
        self.assertLess(events.index(str(app.GUARD)), events.index('candidate-closed'))
        self.assertLess(events.index('candidate-closed'), events.index('sleep'))
        self.assertEqual(json.loads((self.out / 'candidate-dispatch.json').read_text()), {'closed': True})
        self.assertEqual(json.loads((self.out / 'before.json').read_text())['candidate'], {'closed': False})

    def test_base_storage_preflight_failure_never_probes_candidate(self):
        with patch.object(app, 'check', side_effect=RuntimeError('storage fault')), \
             patch.object(app, 'candidate_check') as gate:
            with self.assertRaisesRegex(RuntimeError, 'storage fault'):
                self.invoke('check')
            gate.assert_not_called()
        self.assertFalse(self.out.exists())
        self.run.assert_not_called()


if __name__ == '__main__':
    unittest.main()

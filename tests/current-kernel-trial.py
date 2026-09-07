#!/usr/bin/env python3
"""Pure temporary-file transaction tests; no live system access."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('trial', Path(__file__).resolve().parents[1] / 'tools/kernel-ab/install-current-trial.py')
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'Image'
        self.old, self.new = b'baseline', b'candidate'
        self.target.write_bytes(self.old)
        self.intent, self.result = self.root / 'intent.json', self.root / 'result.json'
        self.lock = patch.object(trial, 'IMAGES', {'baseline': (trial.digest(self.old), len(self.old)), 'candidate': (trial.digest(self.new), len(self.new))})
        self.lock.start()
        self.addCleanup(self.lock.stop)

    def swap(self):
        return trial.swap(self.target, self.new, self.intent, self.result, {})

    def test_replace_and_receipts(self):
        self.swap()
        self.assertEqual(self.target.read_bytes(), self.new)
        self.assertTrue(json.loads(self.intent.read_text())['intent_only'])
        self.assertFalse(json.loads(self.result.read_text())['rolled_back'])

    def test_unknown_target_refused(self):
        self.target.write_bytes(b'unknown')
        with self.assertRaises(RuntimeError): self.swap()
        self.assertEqual(self.target.read_bytes(), b'unknown')
        self.assertFalse(self.intent.exists())

    def test_symlink_refused(self):
        link = self.root / 'link'
        link.symlink_to(self.target)
        with self.assertRaises(OSError): trial.read_regular(link)

    def test_no_clobber_receipts(self):
        self.intent.write_text('previous')
        with self.assertRaises(FileExistsError): self.swap()
        self.assertEqual(self.target.read_bytes(), self.old)

    def test_failed_readback_restores_baseline(self):
        atomic = trial.atomic_replace
        calls = []
        def corrupt_once(path, data):
            calls.append(data)
            atomic(path, b'bad-write' if len(calls) == 1 else data)
        with patch.object(trial, 'atomic_replace', corrupt_once):
            with self.assertRaises(RuntimeError): self.swap()
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertTrue(json.loads(self.result.read_text())['rolled_back'])

    def test_drift_before_write_is_not_overwritten(self):
        save = trial.save_new
        def drift(path, report):
            save(path, report)
            self.target.write_bytes(b'other-writer')
        with patch.object(trial, 'save_new', drift):
            with self.assertRaises(RuntimeError): self.swap()
        self.assertEqual(self.target.read_bytes(), b'other-writer')

    def test_already_target_has_no_write(self):
        self.target.write_bytes(self.new)
        with self.assertRaises(RuntimeError): self.swap()
        self.assertFalse(self.intent.exists())


if __name__ == '__main__':
    unittest.main()

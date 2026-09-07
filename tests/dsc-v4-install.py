#!/usr/bin/env python3
"""Known candidate selection plus the existing atomic transaction fault suite."""
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WRAPPER = load('v4_installer', ROOT / 'tools/kernel-ab/install-dsc-v4.py')
SUITE = load('v4_atomic_suite', ROOT / 'tests/current-kernel-trial.py')


def prepared(identity='V4_NOTES'):
    notes = b'isolated-' + identity.encode()
    read = Path.read_bytes

    def read_fixture(path):
        return notes if str(path) == '/sys/kernel/notes' else read(path)

    with patch.object(WRAPPER, identity, hashlib.sha256(notes).hexdigest()), \
            patch.object(Path, 'read_bytes', read_fixture):
        return WRAPPER.prepare()


class CandidateIdentityTests(unittest.TestCase):
    def test_v3_and_v4_and_original_rescue_are_explicitly_supported(self):
        for identity in ('V3_NOTES', 'V4_NOTES', 'RESCUE_NOTES'):
            with self.subTest(identity=identity):
                transaction = prepared(identity)
                self.assertEqual(transaction.IMAGES, WRAPPER.IMAGES)
                self.assertEqual(transaction.ROOT.name, 'kernel-dsc-v4-20260907')
                self.assertEqual(transaction.CONFIRM, 'POCKETDS-DSC-V4-BOOT-20260907')
                if identity != 'RESCUE_NOTES':
                    self.assertEqual(transaction.NOTES[WRAPPER.RELEASE],
                                     hashlib.sha256(b'isolated-' + identity.encode()).hexdigest())

    def test_unknown_notes_cannot_select_a_transaction(self):
        original = Path.read_bytes
        with patch.object(Path, 'read_bytes', lambda p: b'unknown' if str(p) == '/sys/kernel/notes' else original(p)):
            with self.assertRaisesRegex(RuntimeError, 'unverified'):
                WRAPPER.prepare()

    def test_base_tool_change_is_refused_before_inspecting_runtime(self):
        with patch.object(Path, 'read_bytes', return_value=b'changed') as read:
            with self.assertRaisesRegex(RuntimeError, 'base transaction changed'):
                WRAPPER.prepare()
            self.assertEqual(read.call_count, 1)

    def test_original_installer_locks_are_not_mutated(self):
        prepared()
        original = load('original_trial_check', ROOT / 'tools/kernel-ab/install-current-trial.py')
        self.assertNotEqual(original.IMAGES, WRAPPER.IMAGES)
        self.assertEqual(original.NOTES[WRAPPER.RELEASE], WRAPPER.V3_NOTES)


class V4TransactionTests(SUITE.TrialTests):
    def setUp(self):
        replace = patch.object(SUITE, 'trial', prepared())
        replace.start()
        self.addCleanup(replace.stop)
        super().setUp()


if __name__ == '__main__':
    unittest.main()

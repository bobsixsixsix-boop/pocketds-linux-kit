# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('sd_resize', Path(__file__).resolve().parents[1] / 'packaging/sd-image/resize-root.py')
resize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resize)


class PartitionBoundary(unittest.TestCase):
    def setUp(self):
        self.table = {'label': 'gpt', 'device': '/dev/mmcblk0', 'unit': 'sectors', 'sectorsize': 512,
                      'partitions': [
                          {'node': '/dev/mmcblk0p1', 'name': 'ROCKNIX', 'type': resize.ESP,
                           'start': 2048, 'size': 1048576},
                          {'node': '/dev/mmcblk0p2', 'name': 'STORAGE', 'type': resize.ARM64_ROOT,
                           'start': 1050624, 'size': 16777216}]}

    def check(self, table):
        return resize.validate_table(table, '/dev/mmcblk0p2', '/dev/mmcblk0')

    def test_expected_image(self):
        self.assertEqual(self.check(self.table)['name'], 'STORAGE')

    def test_wrong_disk_even_with_matching_labels(self):
        self.table['device'] = '/dev/sda'
        with self.assertRaises(ValueError): self.check(self.table)

    def test_extra_partition_is_never_overwritten(self):
        self.table['partitions'].append({'name': 'USER_DATA'})
        with self.assertRaises(ValueError): self.check(self.table)

    def test_refuses_changed_boot_geometry_type_and_root(self):
        changes = [(0, 'size', 524288), (0, 'type', 'linux'), (0, 'name', 'OTHER'),
                   (1, 'start', 2048), (1, 'type', 'linux'), (1, 'node', '/dev/sda2'),
                   (1, 'size', 16), (1, 'name', 'HOME')]
        for index, key, value in changes:
            with self.subTest(index=index, key=key):
                table = copy.deepcopy(self.table)
                table['partitions'][index][key] = value
                with self.assertRaises(ValueError): self.check(table)

    def test_non_image_tables(self):
        for key, value in [('label', 'dos'), ('unit', 'bytes'), ('sectorsize', 4096)]:
            with self.subTest(key=key):
                table = copy.deepcopy(self.table)
                table[key] = value
                with self.assertRaises(ValueError): self.check(table)

    def test_partition_geometry_must_be_positive_integers(self):
        for value in (0, -1, True, '2048', 2048.0, None):
            with self.subTest(value=value):
                table = copy.deepcopy(self.table)
                table['partitions'][0]['start'] = value
                with self.assertRaises(ValueError): self.check(table)


class ApplyBoundary(unittest.TestCase):
    def setUp(self):
        PartitionBoundary.setUp(self)
        self.table['id'] = 'A0A1B2C3-D4E5-4678-9012-ABCDEF012345'
        self.table['partitions'][1]['uuid'] = 'B0A1B2C3-D4E5-4678-9012-ABCDEF012345'
        self.table['partitions'][1]['attrs'] = 'GUID:59'
        self.after = copy.deepcopy(self.table)
        self.after['partitions'][1]['size'] += 4096
        self.actual = self.after['partitions'][1]['size'] * 512
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.pending = Path(self.temporary.name) / 'resize-pending'
        self.pending.write_bytes(b'pocketds.sd-resize.v1\n')
        self.patch_pending = mock.patch.object(resize, 'PENDING', self.pending)
        self.patch_pending.start()
        self.addCleanup(self.patch_pending.stop)
        self.resize_fails = False
        self.calls = []

    def fake_run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[0].endswith('/sfdisk'):
            return types.SimpleNamespace(stdout=json.dumps({'partitiontable': self.after}))
        if args[0].endswith('/blockdev'):
            return types.SimpleNamespace(stdout=str(self.actual))
        if args[0].endswith('/resize2fs'):
            if self.resize_fails:
                raise subprocess.CalledProcessError(1, args)
            return types.SimpleNamespace(stdout='')
        self.fail('Unexpected command')

    def apply(self, returncode=0, output='CHANGED: partition=2'):
        with mock.patch.object(resize, 'run', side_effect=self.fake_run), \
                mock.patch.object(resize.subprocess, 'run', return_value=types.SimpleNamespace(
                    returncode=returncode, stdout=output)), \
                mock.patch('builtins.print'):
            resize.apply_resize('/dev/mmcblk0p2', '/dev/mmcblk0', self.table,
                                self.table['partitions'][1])

    def did_resize(self):
        return any(args[0].endswith('/resize2fs') for args, _ in self.calls)

    def test_grown_partition_resizes_and_clears_marker(self):
        self.apply()
        self.assertTrue(self.did_resize())
        self.assertFalse(self.pending.exists())
        writer = [kwargs for args, kwargs in self.calls if args[0].endswith('/resize2fs')]
        self.assertEqual(writer, [{'timeout': None}])

    def test_nochange_after_reboot_completes_idempotently(self):
        self.table = copy.deepcopy(self.after)
        self.apply(1, 'NOCHANGE: partition 2 is size 16781312. it cannot be grown')
        self.assertTrue(self.did_resize())
        self.assertFalse(self.pending.exists())

    def test_growpart_failure_preserves_marker_and_stops(self):
        for code, text in [(2, 'FAILED: resize'), (1, 'unrecognized'), (1, '')]:
            with self.subTest(code=code, text=text):
                with self.assertRaises(ValueError): self.apply(code, text)
                self.assertTrue(self.pending.exists())
                self.assertFalse(self.did_resize())

    def test_kernel_size_lag_waits_for_reboot_without_resizing(self):
        self.actual = self.table['partitions'][1]['size'] * 512
        with self.assertRaises(ValueError): self.apply()
        self.assertTrue(self.pending.exists())
        self.assertFalse(self.did_resize())

    def test_changed_identity_stops_before_filesystem_mutation(self):
        for field, value in [('start', 1052672), ('uuid', 'CHANGED'), ('attrs', 'GUID:60')]:
            with self.subTest(field=field):
                original = self.after['partitions'][1][field]
                self.after['partitions'][1][field] = value
                with self.assertRaises(ValueError): self.apply()
                self.assertTrue(self.pending.exists())
                self.assertFalse(self.did_resize())
                self.after['partitions'][1][field] = original

    def test_changed_boot_or_disk_identity_stops(self):
        self.after['partitions'][0]['start'] = 4096
        with self.assertRaises(ValueError): self.apply()
        self.assertFalse(self.did_resize())
        self.after = copy.deepcopy(self.table)
        self.after['id'] = 'OTHER'
        with self.assertRaises(ValueError): self.apply()
        self.assertTrue(self.pending.exists())
        self.assertFalse(self.did_resize())

    def test_failed_filesystem_resize_keeps_marker_for_next_boot(self):
        self.resize_fails = True
        with self.assertRaises(subprocess.CalledProcessError): self.apply()
        self.assertTrue(self.pending.exists())


if __name__ == '__main__': unittest.main()

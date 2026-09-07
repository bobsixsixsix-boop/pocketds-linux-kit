# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('sd_assemble', Path(__file__).resolve().parents[1] / 'scripts/pocketds-sd-image-assemble.py')
assemble = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assemble)


class OutputBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / 'root'
        self.root.mkdir()
        self.output = self.base / 'candidate.raw'

    def tearDown(self): self.tmp.cleanup()

    def refuse(self, root, output):
        args = ['assemble', '--root', str(root), '--output', str(output), '--definitions', str(self.base)]
        with patch.object(sys, 'argv', args), patch.object(assemble.subprocess, 'run') as writer:
            with self.assertRaises((ValueError, FileNotFoundError)):
                assemble.main()
            writer.assert_not_called()

    def test_never_overwrites_existing_file(self):
        self.output.write_bytes(b'keep existing content')
        self.refuse(self.root, self.output)
        self.assertEqual(self.output.read_bytes(), b'keep existing content')

    def test_never_accepts_device_or_live_root(self):
        self.refuse(self.root, Path('/dev/pocketds-not-a-device'))
        self.refuse(Path('/'), self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_redirected_output_parent(self):
        link = self.base / 'link'
        link.symlink_to(self.base, target_is_directory=True)
        self.refuse(self.root, link / 'candidate.raw')
        self.assertFalse(self.output.exists())

    def test_rejects_unsealed_root_and_output_in_root(self):
        self.refuse(self.root, self.output)
        self.refuse(self.root, self.root / 'candidate.raw')
        self.assertFalse(self.output.exists())

    def test_existing_receipt_is_never_clobbered(self):
        seal = self.root / 'usr/share/pocketds-linux-kit/image-seal.json'
        seal.parent.mkdir(parents=True)
        seal.write_text(json.dumps({'schema': 'pocketds.sd-image-sealed.v1', 'accepted_modules_verified': 317}))
        receipt = self.output.with_suffix('.raw.json')
        receipt.write_bytes(b'keep receipt')
        self.refuse(self.root, self.output)
        self.assertEqual(receipt.read_bytes(), b'keep receipt')
        self.assertFalse(self.output.exists())


if __name__ == '__main__': unittest.main()

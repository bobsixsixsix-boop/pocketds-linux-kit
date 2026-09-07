#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic filesystem checks for sealing; no root, mount or device operations."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('seal', REPO / 'scripts/pocketds-sd-image-seal.py')
seal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seal)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class SealTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / 'root'
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def file(self, name, data=b'fixture', mode=0o644):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        p.chmod(mode)
        return p

    def test_parent_link_and_hardlink_cannot_write_outside(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (self.root / 'etc').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            seal.checked(self.root, 'etc/shadow')
        (self.root / 'etc').unlink()
        original = self.base / 'original'
        original.write_bytes(b'unchanged')
        (self.root / 'etc').mkdir()
        os.link(original, self.root / 'etc/shadow')
        with self.assertRaises(ValueError):
            seal.checked(self.root, 'etc/shadow')
        self.assertEqual(original.read_bytes(), b'unchanged')

    def test_only_boot_leaf_link_can_be_replaced(self):
        original = self.base / 'original'
        original.write_bytes(b'unchanged')
        (self.root / 'boot').mkdir()
        leaf = self.root / 'boot/Image'
        leaf.symlink_to(original)
        with self.assertRaises(ValueError):
            seal.checked(self.root, 'boot/Image')
        with patch.object(os, 'fchown'):
            seal.write(self.root, 'boot/Image', data=b'new kernel', allow_link=True)
        self.assertFalse(leaf.is_symlink())
        self.assertEqual(leaf.read_bytes(), b'new kernel')
        self.assertEqual(original.read_bytes(), b'unchanged')

    def test_rejects_live_and_nested_bind_mounts(self):
        with self.assertRaises(ValueError):
            seal.validate_root(Path('/'))
        seal.reject_mounts(self.root, '1 0 0:1 / / rw - tmpfs tmpfs rw\n')
        for name in (str(self.root), str(self.root / 'dev'), str(self.root / 'var/cache/nested')):
            with self.subTest(mount=name):
                info = f'2 1 0:2 / {name} rw - tmpfs tmpfs rw\n'
                with self.assertRaises(ValueError):
                    seal.reject_mounts(self.root, info)
        spaced = self.base / 'root with space'
        spaced.mkdir()
        name = str(spaced / 'dev').replace(' ', '\\040')
        with self.assertRaises(ValueError):
            seal.reject_mounts(spaced, f'2 1 0:2 / {name} rw - tmpfs tmpfs rw\n')

    def export(self, archive_payload=b'current source'):
        source = self.base / 'source'
        source.mkdir()
        p = source / 'test.py'
        p.write_bytes(b'current source')
        p.chmod(0o644)
        archive = self.base / 'source.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            item = tarfile.TarInfo('source/test.py')
            item.size = len(archive_payload)
            item.mode = 0o644
            tar.addfile(item, io.BytesIO(archive_payload))
        data = {'schema': 1, 'profile': 'asset-free', 'source_commit': 'a' * 40,
                'archive': {'sha256': seal.digest(archive)},
                'files': [{'path': 'test.py', 'sha256': sha(b'current source'), 'size': 14, 'mode': '0644'}]}
        manifest = self.base / 'manifest.json'
        manifest.write_text(json.dumps(data))
        return source, archive, manifest

    def test_export_binds_tree_and_archive(self):
        source, archive, manifest = self.export()
        seal.validate_export(source, archive, manifest)
        (source / 'unexpected-history').write_bytes(b'stale')
        with self.assertRaises(ValueError):
            seal.validate_export(source, archive, manifest)

    def test_prior_archive_is_rejected_even_with_its_own_archive_hash(self):
        source, archive, manifest = self.export(b'previous stale')
        with self.assertRaises(ValueError):
            seal.validate_export(source, archive, manifest)

    def test_private_dirs_and_consumed_desktop_state_are_rejected(self):
        (self.root / 'root').mkdir()
        home = self.root / 'home/pocketds'
        home.mkdir(parents=True)
        seal.validate_privacy(self.root, {'files': []})
        (home / '.ssh').mkdir()
        with self.assertRaises(ValueError):
            seal.validate_privacy(self.root, {'files': []})
        (home / '.ssh').rmdir()
        self.file('home/pocketds/.local/state/pocketds-linux-kit/image-desktop-v1.ready', b'ready')
        with self.assertRaises(ValueError):
            seal.validate_privacy(self.root, {'files': []})

    def test_normal_firstboot_is_required(self):
        self.file('etc/shadow', b'root:!:20000:0:99999:7:::\npocketds:!:20000:0:99999:7:::\n', 0o600)
        pending = self.file('var/lib/pocketds-firstboot/pending', b'', 0o600)
        self.file('var/lib/pocketds-firstboot/resize-pending', b'pocketds.sd-resize.v1\n')
        original = seal.regular
        def root_info(path, maximum=256 * 1024 * 1024):
            info = original(path, maximum)
            values = list(info)
            values[4] = 0
            return os.stat_result(values)
        with patch.object(seal, 'regular', side_effect=root_info):
            seal.validate_firstboot(self.root)
            pending.unlink()
            with self.assertRaises(FileNotFoundError):
                seal.validate_firstboot(self.root)
        (self.root / 'etc/shadow').chmod(0o600)

    def test_only_exact_staged_home_links_are_allowed(self):
        (self.root / 'root').mkdir()
        directory = self.root / 'home/pocketds/.config/systemd/user'
        directory.mkdir(parents=True)
        path = directory / 'legacy.service'
        path.symlink_to('/dev/null')
        row = {'target': '/home/pocketds/.config/systemd/user/legacy.service',
               'link': '/dev/null', 'uid': os.getuid(), 'gid': os.getgid()}
        seal.validate_privacy(self.root, {'files': [], 'links': [row]})
        with self.assertRaises(ValueError):
            seal.validate_privacy(self.root, {'files': [], 'links': []})
        with self.assertRaises(ValueError):
            seal.validate_privacy(self.root, {'files': [], 'links': [row | {'link': '/outside'}]})

    def test_public_haptics_binds_both_preimages_and_rejects_other_old_copies(self):
        source = self.base / 'source'
        component = source / 'components/inputplumber'
        component.mkdir(parents=True)
        binary = self.base / 'new-inputplumber'
        binary.write_bytes(b'public build')
        old = b'old rpm executable'
        sidecar = b'old sidecar executable'
        destinations = {
            'usr/bin/inputplumber': {'sha256': sha(old), 'size': len(old), 'origin': 'fixture RPM'},
            'usr/local/libexec/pocketds-inputplumber-haptics': {
                'sha256': sha(sidecar), 'size': len(sidecar), 'origin': 'fixture overlay'}}
        rpm = self.file('usr/bin/inputplumber', old, 0o755)
        self.file('usr/local/libexec/pocketds-inputplumber-haptics', sidecar, 0o755)
        for name in ('pulse.patch', 'public.patch'):
            (component / name).write_bytes(name.encode())
        lock = {'binary_sha256': sha(binary.read_bytes()), 'binary_size': binary.stat().st_size,
                'downstream_patch': 'pulse.patch', 'downstream_patch_sha256': sha(b'pulse.patch'),
                'downstream_patch_size': 11, 'additional_patches': [
                    {'source': 'public.patch', 'sha256': sha(b'public.patch'), 'size': 12}]}
        lock_path = component / 'inputplumber-haptics-source-lock.json'
        lock_path.write_text(json.dumps(lock))
        with patch.object(seal, 'HAPTICS_SHA', sha(binary.read_bytes())), \
                patch.object(seal, 'HAPTICS_DESTINATIONS', destinations):
            _lock, overlays = seal.validate_haptics(self.root, source, binary)
            self.assertEqual({r['path'] for r in overlays}, {'/' + p for p in destinations})
            self.assertTrue(all(r['rpm_database_modified'] is False for r in overlays))
            extra = self.file('opt/leftover-inputplumber', old)
            with self.assertRaisesRegex(ValueError, 'Another retired'):
                seal.validate_haptics(self.root, source, binary)
            extra.unlink()
            rpm.write_bytes(b'unexpected package')
            with self.assertRaisesRegex(ValueError, 'Unexpected InputPlumber preimage'):
                seal.validate_haptics(self.root, source, binary)
            rpm.write_bytes(old)
            (component / 'public.patch').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'source patch'):
                seal.validate_haptics(self.root, source, binary)

    def test_third_party_notice_inventory_is_staged_and_content_bound(self):
        base = REPO / 'packaging/sd-image/third-party'
        inventory = json.loads((base / 'NOTICE-INVENTORY.json').read_text())
        staged = json.loads((REPO / 'packaging/sd-image/kit-files.json').read_text())
        staged = {row['source']: row for row in staged['files']}
        for row in inventory['files']:
            path = base / row['path']
            self.assertEqual((seal.digest(path), path.stat().st_size), (row['sha256'], row['size']))
            source_name = str(path.relative_to(REPO))
            destination = ('/usr/share/doc/pocketds-inputplumber-public/' + row['path'].removeprefix('inputplumber-public/')
                           if row['path'].startswith('inputplumber-public/') else
                           '/usr/share/doc/pocketds-linux-kit/third-party/' + row['path'])
            self.assertEqual(staged[source_name]['target'], destination)
        for required in ('fonts/Spleen-BSD-2-Clause.txt', 'rpm-source-access/SOURCE-RPM-LOCATIONS.tsv',
                         'initramfs/splash-avfs-firmware/notices/LICENSE.qcom.txt'):
            self.assertIn(required, {row['path'] for row in inventory['files']})

    def test_module_exact_allowlist_rejects_compressed_extra_and_metadata_drift(self):
        modules = self.base / 'modules'
        modules.mkdir()
        source = self.base / 'source'
        (source / 'packaging/sd-image').mkdir(parents=True)
        baseline = {f'kernel/test/module{i:03}.ko': b'module' + str(i).encode() for i in range(317)}
        payloads = baseline | {seal.RFCOMM: b'rfcomm'} | {n: n.encode() for n in seal.MODULE_METADATA}
        rows = []
        for name, data in payloads.items():
            path = modules / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o644)
            rows.append({'path': 'usr/lib/modules/' + seal.VERSION + '/' + name,
                         'sha256': sha(data), 'size': len(data), 'mode': '0644'})
        (source / 'packaging/sd-image/kernel-modules.sha256').write_text(''.join(sha(data) + '  ' + name + '\n' for name, data in baseline.items()))
        manifest = self.base / 'modules.json'
        manifest.write_text(json.dumps({'status': 'complete', 'kernel_release': seal.VERSION,
                                       'regular_files': 332, 'module_count': 318, 'files': rows}))
        with patch.object(seal, 'MODULE_MANIFEST_SHA', seal.digest(manifest)), patch.object(seal, 'RFCOMM_SHA', sha(b'rfcomm')):
            self.assertEqual(len(seal.validate_modules(modules, manifest, source)), 332)
            extra = modules / 'kernel/unexpected.ko.xz'
            extra.write_bytes(b'unverified')
            with self.assertRaises(ValueError):
                seal.validate_modules(modules, manifest, source)
            extra.unlink()
            (modules / 'modules.builtin').write_bytes(b'different kernel')
            with self.assertRaises(ValueError):
                seal.validate_modules(modules, manifest, source)


if __name__ == '__main__':
    unittest.main()

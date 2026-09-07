#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assemble a sealed root into a NEW regular disk-image file; never flash a drive."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--definitions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root, output = args.root, args.output
    if root == Path('/') or not root.is_absolute() or root.resolve() != root:
        raise ValueError('A canonical sealed image root is required')
    if not output.is_absolute() or output.exists() or output.is_symlink():
        raise ValueError('Output must be a new regular image file')
    if output.parent.resolve() != output.parent or not output.parent.is_dir():
        raise ValueError('Output parent must exist and be canonical')
    if str(output).startswith(('/dev/', '/proc/', '/sys/')) or output.is_relative_to(root):
        raise ValueError('Output cannot be a device or inside the source root')
    receipt_path = output.with_suffix(output.suffix + '.json')
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ValueError('Output receipt must also be a new regular file')
    seal = json.loads((root / 'usr/share/pocketds-linux-kit/image-seal.json').read_text())
    if (seal.get('schema') != 'pocketds.sd-image-sealed.v1'
            or seal.get('accepted_modules_verified') != 317
            or (root / 'etc/pocketds-image-build-root').exists()):
        raise ValueError('Root has not completed sealing')
    definitions = args.definitions.resolve(strict=True)
    reference = Path(__file__).resolve().parents[1] / 'packaging/sd-image/repart'
    names = {'10-rocknix.conf', '20-storage.conf'}
    if (not definitions.is_dir() or {p.name for p in definitions.iterdir()} != names
            or any((definitions / name).is_symlink()
                   or (definitions / name).read_bytes() != (reference / name).read_bytes() for name in names)):
        raise ValueError('Partition definitions do not match this source revision')
    # Reserve an ordinary file atomically. systemd-repart is never passed a block device.
    with output.open('xb'):
        pass
    output.chmod(0o600)
    subprocess.run([
        '/usr/bin/systemd-repart', '--empty=allow', '--size=auto', '--offline=yes',
        '--dry-run=no', '--sector-size=512', '--root=' + str(root),
        '--definitions=' + str(definitions), str(output),
    ], check=True)
    table = json.loads(subprocess.check_output(['/usr/sbin/sfdisk', '--json', str(output)], text=True))
    parts = table['partitiontable']['partitions']
    if (table['partitiontable']['label'] != 'gpt'
            or table['partitiontable']['sectorsize'] != 512 or len(parts) != 2
            or [part['name'] for part in parts] != ['ROCKNIX', 'STORAGE']
            or parts[0]['size'] != 1048576 or parts[1]['size'] < 16777216):
        raise ValueError('Generated image partition contract failed')
    with output.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    result = {'schema': 'pocketds.sd-image-assembled.v1', 'file': output.name,
              'size': output.stat().st_size, 'sha256': checksum, 'partition_table': table,
              'hardware_boot_tested': False, 'public_release_ready': False}
    with receipt_path.open('x') as receipt:
        receipt.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ['file', 'size', 'sha256', 'hardware_boot_tested']}))


if __name__ == '__main__':
    main()

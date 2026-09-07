#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Grow only this image's mounted SD-card root, never a label-selected disk."""
import json
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys

PENDING = Path('/var/lib/pocketds-firstboot/resize-pending')
ESP = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
ARM64_ROOT = 'b921b045-1df0-41c3-af44-4c6f280d3fae'


def run(args, *, timeout=120):
    return subprocess.run(args, check=True, text=True, capture_output=True,
                          env={'PATH': '/usr/sbin:/usr/bin', 'LC_ALL': 'C'}, timeout=timeout)


def validate_table(table, root_node, disk_node):
    """Only the two-partition, 512-byte GPT layout of our image is eligible."""
    if table.get('label') != 'gpt' or table.get('device') != disk_node:
        raise ValueError('Unexpected partition table')
    if table.get('unit') != 'sectors' or table.get('sectorsize') != 512:
        raise ValueError('Unexpected sector geometry')
    parts = table.get('partitions', [])
    if not isinstance(parts, list) or len(parts) != 2:
        raise ValueError('The SD card must contain exactly the two image partitions')
    boot, root = parts
    for part in parts:
        if (not isinstance(part, dict) or any(type(part.get(key)) is not int
                or part[key] <= 0 for key in ('start', 'size'))):
            raise ValueError('Invalid partition geometry')
    if (boot.get('name') != 'ROCKNIX' or boot.get('type', '').lower() != ESP
            or boot.get('size') != 1048576 or root.get('name') != 'STORAGE'
            or root.get('type', '').lower() != ARM64_ROOT
            or root.get('node') != root_node
            or root.get('start', 0) < boot.get('start', 0) + boot['size']
            or root.get('size', 0) < 8 * 1024**3 // 512):
        raise ValueError('Mounted root does not match the SD image layout')
    return root


def discover():
    mounted = json.loads(run(['/usr/bin/findmnt', '--json', '--mountpoint', '/',
                             '--output', 'FSTYPE,MAJ:MIN']).stdout)['filesystems']
    dev = os.stat('/').st_dev
    identity = f'{os.major(dev)}:{os.minor(dev)}'
    if len(mounted) != 1 or mounted[0]['fstype'] != 'ext4' or mounted[0]['maj:min'] != identity:
        raise ValueError('Root is not a directly mounted ext4 filesystem')
    sysroot = Path('/sys/dev/block', identity).resolve(strict=True)
    if (sysroot / 'partition').read_text().strip() != '2':
        raise ValueError('Root is not partition 2')
    parent = sysroot.parent
    if (parent / 'device/type').read_text().strip() != 'SD':
        raise ValueError('Automatic expansion is restricted to an SD card')
    root_node = str(Path('/dev/block', identity).resolve(strict=True))
    disk_node = str(Path('/dev/block', (parent / 'dev').read_text().strip()).resolve(strict=True))
    if not stat.S_ISBLK(os.stat(root_node).st_mode) or not stat.S_ISBLK(os.stat(disk_node).st_mode):
        raise ValueError('Block device is absent')
    table = json.loads(run(['/usr/sbin/sfdisk', '--json', disk_node]).stdout)['partitiontable']
    part = validate_table(table, root_node, disk_node)
    return root_node, disk_node, table, part


def apply_resize(root_node, disk_node, before, root):
    # Do not forcibly terminate tools while they are updating disk metadata.
    result = subprocess.run(['/usr/bin/growpart', disk_node, '2'], text=True,
                            capture_output=True,
                            env={'PATH': '/usr/sbin:/usr/bin', 'LC_ALL': 'C'})
    if result.returncode != 0 and not (result.returncode == 1 and result.stdout.startswith('NOCHANGE:')):
        raise ValueError('SD partition expansion did not complete')
    after = json.loads(run(['/usr/sbin/sfdisk', '--json', disk_node]).stdout)['partitiontable']
    grown = validate_table(after, root_node, disk_node)
    before_identity = {key: value for key, value in root.items() if key != 'size'}
    after_identity = {key: value for key, value in grown.items() if key != 'size'}
    if (after['id'] != before['id'] or after['partitions'][0] != before['partitions'][0]
            or after_identity != before_identity
            or grown['size'] < root['size']):
        raise ValueError('Partition invariants changed; filesystem expansion stopped')
    actual = int(run(['/usr/sbin/blockdev', '--getsize64', root_node]).stdout)
    if actual != grown['size'] * 512:
        raise ValueError('Kernel has not accepted the new partition size; retry on next boot')
    run(['/usr/sbin/resize2fs', root_node], timeout=None)
    PENDING.unlink()
    directory = os.open(PENDING.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    print('Pocket DS SD root expansion complete')


def checked_file(path, *, create=False):
    flags = os.O_CLOEXEC | os.O_NOFOLLOW
    flags |= os.O_RDWR | os.O_CREAT if create else os.O_RDONLY
    descriptor = os.open(path, flags, 0o600)
    status = os.fstat(descriptor)
    if (not stat.S_ISREG(status.st_mode) or status.st_uid != 0
            or status.st_nlink != 1 or status.st_mode & 0o022):
        os.close(descriptor)
        raise ValueError('Unsafe first-boot state')
    return descriptor


def pending():
    try:
        descriptor = checked_file(PENDING)
    except FileNotFoundError:
        return False
    with os.fdopen(descriptor, 'rb') as source:
        expected = b'pocketds.sd-resize.v1\n'
        if source.read(len(expected) + 1) != expected:
            raise ValueError('Unrecognized first-boot marker')
    return True


def main():
    if sys.argv[1:] not in ([], ['--apply']):
        raise ValueError('Usage: pocketds-resize-root [--apply]')
    if os.geteuid() != 0:
        raise ValueError('Run as root')
    if not PENDING.parent.exists():
        return
    directory = PENDING.parent.lstat()
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0
            or directory.st_mode & 0o022):
        raise ValueError('Unsafe first-boot state directory')
    if sys.argv[1:] != ['--apply']:
        if not pending():
            return
        root_node, disk_node, _, root = discover()
        print(json.dumps({'disk': disk_node, 'root': root_node, 'partition': 2,
                          'start': root['start'], 'apply': False}))
        return
    with os.fdopen(checked_file(PENDING.parent / 'resize.lock', create=True), 'r+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not pending():
            return
        root_node, disk_node, before, root = discover()
        apply_resize(root_node, disk_node, before, root)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print(f'Pocket DS SD root expansion did not finish: {error}', file=sys.stderr)
        sys.exit(1)

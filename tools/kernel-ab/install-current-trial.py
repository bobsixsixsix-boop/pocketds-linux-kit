#!/usr/bin/env python3
"""Locked single-file transaction for the September Pocket DS installation.

The old PDS-002 lock/three aliases do NOT describe this installation.
No partition, reboot, boot-mode, module installation or sleep-policy writes.
Default is inspection only. Each execution preserves a durable intent/result.
Atomic replacement follows recovery/transaction.py, with a scoped rollback.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess

ROOT = Path('/var/lib/pocketds-linux-kit/kernel-trial-20260905')
TARGET = Path('/boot/boot/Image')
IMAGES = {
    'baseline': ('85b41b68738090e5e04aa9672eecf8b08d9ef189d7c9207d5ec99ef8c7252673', 17086464),
    'candidate': ('a136aeb060d38f9a305336af36577c5971e7c7e81e78839a608a60d30ff24c71', 19077120),
}
NOTES = {
    '7.1.0-100.20260717114522.pocketds.fc44.aarch64': '2b7ae7949422c36be686322fb12a8f8340127596accccc133716f2c420bae6f1',
    '7.1.12-pdsdiag.20260905.aarch64': '044509cbbdd347135937ee3de42fb2bebe099a17bfa939e5a0093bd4760c64d0',
}
CONFIRM = 'POCKETDS-CURRENT-BOOT-TRIAL-20260905'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'unsafe image metadata')
        require(info.st_size <= 32 * 1024 * 1024, 'oversized image')
        data = stream.read()
        require(len(data) == info.st_size, 'image read changed size')
        return data


def identity(data):
    record = digest(data), len(data)
    matches = [name for name, expected in IMAGES.items() if expected == record]
    require(len(matches) == 1, 'image is not the locked baseline or candidate')
    return matches[0]


def atomic_replace(path, data):
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    name = f'.pds001-{secrets.token_hex(12)}.tmp'
    created = False
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        created = True
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(name, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        created = False
        os.fsync(parent)
    finally:
        if created:
            os.unlink(name, dir_fd=parent)
        os.close(parent)


def save_new(path, record):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(record, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def swap(target, data, intent_path, result_path, context):
    require(not result_path.exists(), 'result already exists')
    before = read_regular(target)
    previous, desired = identity(before), identity(data)
    require(previous != desired, 'already at requested identity; no write needed')
    report = {**context, 'before': previous, 'after': desired, 'intent_only': True,
              'before_sha256': digest(before), 'after_sha256': digest(data)}
    save_new(intent_path, report)
    require(read_regular(target) == before, 'boot image changed after intent; no write performed')
    try:
        atomic_replace(target, data)
        require(read_regular(target) == data, 'post-write readback mismatch')
    except Exception:
        # Restore exact known preimage once; never reboot after an exception.
        atomic_replace(target, before)
        require(read_regular(target) == before, 'ROLLBACK FAILED: do not reboot')
        save_new(result_path, {**report, 'intent_only': False, 'rolled_back': True})
        raise
    save_new(result_path, {**report, 'intent_only': False, 'rolled_back': False})
    return report


def inspect():
    require(os.geteuid() == 0, 'run inspection/transaction as root')
    require(Path('/proc/device-tree/model').read_bytes().rstrip(b'\0') == b'AYANEO Pocket DS', 'wrong model')
    release = os.uname().release
    require(release in NOTES and digest(Path('/sys/kernel/notes').read_bytes()) == NOTES[release], 'unverified running kernel')
    for directory in (ROOT, TARGET.parent):
        info = directory.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'unsafe directory')
    for mountpoint, source, filesystem in (('/boot', '/dev/sda12', 'vfat'), ('/', '/dev/sda13', 'ext4')):
        records = []
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            left, right = line.split(' - ', 1)
            fields, post = left.split(), right.split()
            if fields[4] == mountpoint:
                records.append((fields[5], post[0], post[1]))
        require(len(records) == 1, 'ambiguous mount')
        options, kind, device = records[0]
        require('rw' in options.split(',') and (device, kind) == (source, filesystem), 'unexpected mount')
    guard_path = '/usr/local/libexec/pocketds-deep-suspend'
    require(digest(read_regular(Path(guard_path))) == '68877ca5eb98bde3ebe5f4d1e01953b2e732965619cd0da75c97058dfcdcf36f', 'guard drift')
    guard = json.loads(subprocess.check_output([guard_path, 'check'], text=True, timeout=15))
    require(guard['safely_blocked'] and guard['gates']['rtc_alarm_clear'], 'sleep not safely blocked')
    kernel_log = subprocess.check_output(['dmesg'], text=True, timeout=15)
    require(not re.search(r'I/O error|Buffer I/O|EXT4-fs error|ufshcd.*(?:error|fail)|ufs.*(?:abort|fatal)', kernel_log, re.I), 'storage error: stop')
    for absent in ('/boot/Image', '/boot/boot.img', '/boot/Image.gz', '/boot/boot/Image.gz', '/boot/boot/boot.img'):
        require(not os.path.lexists(absent), 'unexpected alternative boot image')
    inputs = {name: read_regular(ROOT / f'{name}.img') for name in IMAGES}
    require(all(identity(data) == name for name, data in inputs.items()), 'backup/candidate mismatch')
    current = identity(read_regular(TARGET))
    return {'release': release, 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'disk_identity': current, 'target_path': str(TARGET), 'automatic_suspend': False}, inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=IMAGES)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--confirm')
    parser.add_argument('--receipt', help='new simple basename, stored in the locked private directory')
    args = parser.parse_args()
    context, inputs = inspect()
    if not args.execute:
        print(json.dumps({**context, 'requested': args.target, 'write_executed': False}, indent=2))
        return
    require(args.confirm == CONFIRM, 'explicit confirmation required')
    require(args.receipt and re.fullmatch(r'[a-z0-9-]{1,60}', args.receipt), 'invalid receipt basename')
    fd = os.open(ROOT / 'transaction.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        context, inputs = inspect()
        result = swap(TARGET, inputs[args.target], ROOT / f'{args.receipt}.intent.json',
                      ROOT / f'{args.receipt}.result.json', context)
        print(json.dumps({**result, 'intent_only': False, 'write_executed': True}, indent=2))
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()

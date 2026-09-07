#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Seal a marked, unmounted build directory; never mount or write a device."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile

VERSION = '7.1.12-pdsdiag.20260905.aarch64'
BOOT_SHA = '57322cb6dc3bce822289bcd6bde5e5934362efc2547120ebc88be0eea7a89d92'
HAPTICS_SHA = '4dbb8a7dc494e27abdbe3b38191dabfaef54caa8f6f2c7357bc84008bbfb488b'
HAPTICS_DESTINATIONS = {
    'usr/local/libexec/pocketds-inputplumber-haptics': {
        'sha256': '2c06a4cbfaa2aa93c923b1dc790bbaf15ae44e048662840cce0705bf2d7df244',
        'size': 9574272, 'origin': 'prior Pocket DS Pulse overlay'},
    'usr/bin/inputplumber': {
        'sha256': 'f76a1a1f531fe28e3a2ebc76d8040c134ed15374c93512cf5ad0e69069dbd11d',
        'size': 9507728, 'origin': 'inputplumber-0.75.2-20260506235201.fc44.aarch64'},
}
RFCOMM_SHA = 'bd0a0116dd66f0d0c73f92c62ada06b2961266f44384aeaa88841056d4af576b'
MODULE_MANIFEST_SHA = 'ac32f54308ed35c238d62aa0e6907c58d3a9f009754edf5e667211d9fc78348a'
RFCOMM = 'kernel/net/bluetooth/rfcomm/rfcomm.ko'
MODULE_METADATA = {'modules.alias', 'modules.alias.bin', 'modules.builtin',
                   'modules.builtin.alias.bin', 'modules.builtin.bin',
                   'modules.builtin.modinfo', 'modules.dep', 'modules.dep.bin',
                   'modules.devname', 'modules.order', 'modules.softdep',
                   'modules.symbols', 'modules.symbols.bin', 'modules.weakdep'}
MASKS = ('sshd.service', 'sshd.socket', 'plasma-setup.service', 'pocketds-fs-resize.service')
TRANSIENTS = ('tmp', 'run', 'dev', 'var/log', 'var/cache')
PRIVATE_DIRS = {'.ssh', '.codex', '.gnupg', '.aws', '.kube', '.mozilla'}


def relative(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or '..' in path.parts
            or '\\' in value or path.as_posix() != value):
        raise ValueError('Invalid relative artifact path')
    return path


def checked(root, name, *, kind='file', allow_link=False):
    """Resolve no parent links, and never follow a final link for mutation."""
    path = root.joinpath(*relative(name).parts)
    current = root
    for part in relative(name).parts[:-1]:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise ValueError('Unsafe image or artifact parent path')
    if path.is_symlink():
        if allow_link:
            return path
        raise ValueError('Unexpected final symlink')
    if path.exists():
        info = path.lstat()
        if kind == 'directory':
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('Expected a real directory')
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Expected an unshared regular file')
    return path


def regular(path, maximum=256 * 1024 * 1024):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_size > maximum):
        raise ValueError('Unsafe or oversized artifact file')
    return info


def digest(path):
    regular(path)
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def json_file(path):
    regular(path, 4 * 1024 * 1024)
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate manifest field')
            value[key] = item
        return value
    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def real_directory(path):
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path or not path.is_dir():
        raise ValueError('A canonical real directory is required')


def reject_mounts(root, mountinfo=None):
    text = Path('/proc/self/mountinfo').read_text() if mountinfo is None else mountinfo
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 6:
            raise ValueError('Invalid mount inventory')
        name = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
        mount = Path(name)
        if mount == root or mount.is_relative_to(root):
            raise ValueError('The image directory contains a mounted filesystem')


def validate_root(root):
    real_directory(root)
    if root == Path('/'):
        raise ValueError('The live root is forbidden')
    marker = checked(root, 'etc/pocketds-image-build-root')
    info = regular(marker, 128)
    if info.st_uid != 0 or info.st_mode & 0o022 or marker.read_bytes() != b'pocketds.sd-image-root.v1\n':
        raise ValueError('Image-build marker missing or unsafe')
    reject_mounts(root)
    staged = json_file(checked(root, 'usr/share/pocketds-linux-kit/image-install-manifest.json'))
    if staged.get('schema') != 'pocketds.sd-image-staged.v1':
        raise ValueError('Kit has not been staged')
    return staged


def validate_export(source, archive, manifest_path):
    real_directory(source)
    manifest = json_file(manifest_path)
    if (manifest.get('schema') != 1 or manifest.get('profile') != 'asset-free'
            or not re.fullmatch(r'[0-9a-f]{40}', manifest.get('source_commit', ''))
            or digest(archive) != manifest['archive']['sha256']):
        raise ValueError('Source export identity or archive hash mismatch')
    files = {}
    for row in manifest['files']:
        name = str(relative(row['path']))
        if name in files or row['mode'] not in {'0644', '0755'}:
            raise ValueError('Invalid source inventory')
        path = checked(source, name)
        info = regular(path)
        if (info.st_size != row['size'] or stat.S_IMODE(info.st_mode) != int(row['mode'], 8)
                or digest(path) != row['sha256']):
            raise ValueError('Source tree differs from export manifest')
        files[name] = row
    actual = set()
    for path in source.rglob('*'):
        if path.is_symlink():
            raise ValueError('Source export contains a link')
        if path.is_file():
            actual.add(str(path.relative_to(source)))
        elif not path.is_dir():
            raise ValueError('Source export contains a special file')
    if not files or actual != set(files) or '.git' in {p.name for p in source.rglob('*')}:
        raise ValueError('Source tree file set differs from export manifest')
    seen = set()
    with tarfile.open(archive, 'r:gz') as stream:
        for member in stream:
            if not member.isfile() or not member.name.startswith('source/'):
                raise ValueError('Unsafe source archive entry')
            name = str(relative(member.name.removeprefix('source/')))
            if name in seen or name not in files:
                raise ValueError('Source archive file set differs from export manifest')
            row = files[name]
            if (member.size != row['size'] or member.mode != int(row['mode'], 8)
                    or member.uid != 0 or member.gid != 0):
                raise ValueError('Source archive metadata mismatch')
            data = stream.extractfile(member)
            if data is None or hashlib.file_digest(data, 'sha256').hexdigest() != row['sha256']:
                raise ValueError('Source archive content mismatch')
            seen.add(name)
    if seen != set(files):
        raise ValueError('Source archive is incomplete')
    return manifest, files


def validate_modules(modules, manifest_path, source):
    real_directory(modules)
    if digest(manifest_path) != MODULE_MANIFEST_SHA:
        raise ValueError('The module manifest is not the frozen accepted receipt')
    manifest = json_file(manifest_path)
    if (manifest.get('status') != 'complete' or manifest.get('kernel_release') != VERSION
            or manifest.get('regular_files') != 332 or manifest.get('module_count') != 318):
        raise ValueError('Wrong kernel module bundle identity')
    baseline = {}
    lock = checked(source, 'packaging/sd-image/kernel-modules.sha256')
    for line in lock.read_text().splitlines():
        checksum, name = line.split('  ', 1)
        relative(name)
        if name in baseline or not re.fullmatch('[0-9a-f]{64}', checksum):
            raise ValueError('Invalid baseline module lock')
        baseline[name] = checksum
    if len(baseline) != 317:
        raise ValueError('Incomplete baseline module lock')
    prefix = 'usr/lib/modules/' + VERSION + '/'
    rows = {}
    for row in manifest['files']:
        if not row['path'].startswith(prefix):
            raise ValueError('Module manifest path has the wrong kernel release')
        name = str(relative(row['path'][len(prefix):]))
        if name in rows or row['mode'] != '0644':
            raise ValueError('Invalid module manifest record')
        if name not in baseline and name != RFCOMM and name not in MODULE_METADATA:
            raise ValueError('Unexpected kernel module or metadata path')
        path = checked(modules, name)
        info = regular(path)
        if (info.st_size != row['size'] or stat.S_IMODE(info.st_mode) != 0o644
                or digest(path) != row['sha256']):
            raise ValueError('Kernel module bundle differs from frozen manifest')
        if name in baseline and row['sha256'] != baseline[name]:
            raise ValueError('Baseline module checksum changed')
        if name == RFCOMM and row['sha256'] != RFCOMM_SHA:
            raise ValueError('RFCOMM addition checksum changed')
        rows[name] = row
    wanted = set(baseline) | {RFCOMM} | MODULE_METADATA
    actual = set()
    for path in modules.rglob('*'):
        if path.is_symlink():
            raise ValueError('Unexpected module tree link')
        if path.is_file():
            actual.add(str(path.relative_to(modules)))
        elif not path.is_dir():
            raise ValueError('Module bundle contains a special file')
    if set(rows) != wanted or actual != wanted or len(rows) != 332:
        raise ValueError('Kernel module file set is incomplete or contains extras')
    return rows


def validate_staged(root, staged, source_files):
    seen = set()
    for row in staged['files']:
        name = row['target'].removeprefix('/')
        path = checked(root, name)
        info = regular(path)
        if (name in seen or digest(path) != row['sha256'] or info.st_size != row['size']
                or info.st_uid != row['uid'] or info.st_gid != row['gid']
                or stat.S_IMODE(info.st_mode) != int(row['mode'], 8)):
            raise ValueError('Staged Kit file was changed after installation')
        seen.add(name)
        origin = row['source']
        if origin != 'generated' and not origin.startswith('native-build/'):
            if origin not in source_files or source_files[origin]['sha256'] != row['sha256']:
                raise ValueError('Staged Kit generation differs from the source export')
    for row in staged['links']:
        path = checked(root, row['target'].removeprefix('/'), allow_link=True)
        if (not path.is_symlink() or os.readlink(path) != row['link']
                or path.lstat().st_uid != row['uid'] or path.lstat().st_gid != row['gid']):
            raise ValueError('Staged Kit service link changed')


def validate_firstboot(root):
    shadow = checked(root, 'etc/shadow')
    info = regular(shadow, 4 * 1024 * 1024)
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Unsafe image password database')
    rows = [line.split(':') for line in shadow.read_text().splitlines()]
    for account in ('root', 'pocketds'):
        entries = [row for row in rows if row[0] == account]
        if (len(entries) != 1 or len(entries[0]) != 9
                or entries[0][1] not in {'!', '*', '!*'}
                or not entries[0][2].isdigit() or int(entries[0][2]) == 0
                or entries[0][7] not in {'', '-1'}):
            raise ValueError('Image accounts must be bare-locked with normal aging')
    for name, data, mode in (('pending', b'', 0o600), ('resize-pending', b'pocketds.sd-resize.v1\n', 0o644)):
        path = checked(root, 'var/lib/pocketds-firstboot/' + name)
        info = regular(path, 64)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != mode or path.read_bytes() != data:
            raise ValueError('First-use or resize pending marker is missing or unsafe')
    return rows


def validate_privacy(root, staged):
    allowed = {row['target'].removeprefix('/') for row in staged['files']}
    allowed_links = {row['target'].removeprefix('/'): row for row in staged.get('links', [])}
    skeleton = checked(root, 'etc/skel', kind='directory')
    if skeleton.exists():
        for source in skeleton.rglob('*'):
            if source.is_file() and not source.is_symlink():
                name = 'home/pocketds/' + str(source.relative_to(skeleton))
                path = checked(root, name)
                if path.is_file() and digest(path) == digest(source):
                    allowed.add(name)
    for directory in ('root', 'home/pocketds'):
        home = checked(root, directory, kind='directory')
        for path in home.rglob('*'):
            name = str(path.relative_to(root))
            if path.is_symlink():
                row = allowed_links.get(name)
                if (row is None or os.readlink(path) != row['link']
                        or path.lstat().st_uid != row['uid'] or path.lstat().st_gid != row['gid']):
                    raise ValueError('Unexpected private link in image home')
                continue
            if path.name in PRIVATE_DIRS:
                raise ValueError('Unexpected private directory or link in image home')
            if not path.is_dir() and not path.is_file():
                raise ValueError('Unexpected special file in image home')
            if path.is_file() and name not in allowed:
                raise ValueError('Unexpected unstaged state or private file in image home')
    for directory in ('etc/NetworkManager/system-connections', 'var/lib/bluetooth'):
        path = checked(root, directory, kind='directory')
        if path.exists() and any(path.iterdir()):
            raise ValueError('Unexpected network or Bluetooth identity')


def validate_haptics(root, source_root, binary):
    lock = json_file(checked(source_root, 'components/inputplumber/inputplumber-haptics-source-lock.json'))
    checksum = digest(binary)
    if (checksum != HAPTICS_SHA or checksum != lock['binary_sha256']
            or regular(binary).st_size != lock['binary_size']
            or checksum in {row['sha256'] for row in HAPTICS_DESTINATIONS.values()}):
        raise ValueError('The public InputPlumber binary does not match its new source lock')
    patches = [{'source': lock['downstream_patch'], 'sha256': lock['downstream_patch_sha256'],
                'size': lock['downstream_patch_size']}] + lock.get('additional_patches', [])
    if len(patches) != 2:
        raise ValueError('Both the Pulse and public backend patches are required')
    for row in patches:
        source = checked(source_root, 'components/inputplumber/' + row['source'])
        if digest(source) != row['sha256'] or regular(source).st_size != row['size']:
            raise ValueError('InputPlumber source patch differs from the source lock')
    overlays = []
    for name, expected in HAPTICS_DESTINATIONS.items():
        path = checked(root, name)
        before = None
        if path.exists():
            if digest(path) != expected['sha256'] or regular(path).st_size != expected['size']:
                raise ValueError('Unexpected InputPlumber preimage; inspect the selected base root')
            before = expected.copy()
        elif name == 'usr/bin/inputplumber':
            raise ValueError('The selected base is missing its InputPlumber RPM executable')
        overlays.append({'path': '/' + name, 'preimage': before, 'sha256': checksum,
                         'size': regular(binary).st_size, 'mode': '0755',
                         'source_lock_sha256': digest(checked(source_root,
                             'components/inputplumber/inputplumber-haptics-source-lock.json')),
                         'rpm_database_modified': False})
    reject_retired_haptics(root, allowed=set(HAPTICS_DESTINATIONS))
    return lock, overlays


def reject_retired_haptics(root, allowed=()):
    """Find other exact old payload copies without following directory/file links."""
    sizes = {row['size'] for row in HAPTICS_DESTINATIONS.values()}
    hashes = {row['sha256'] for row in HAPTICS_DESTINATIONS.values()}
    for base, _directories, files in os.walk(root, followlinks=False):
        for leaf in files:
            path = Path(base) / leaf
            if path.is_symlink() or not path.is_file():
                continue
            name = str(path.relative_to(root))
            if name not in allowed and path.stat().st_size in sizes and digest(path) in hashes:
                raise ValueError('Another retired InputPlumber payload remains: ' + name)


def prepare(args):
    root = args.root
    staged = validate_root(root)
    export, source_files = validate_export(args.source_root, args.source_archive, args.export_manifest)
    module_files = validate_modules(args.modules, args.module_manifest, args.source_root)
    module_target = checked(root, 'usr/lib/modules/' + VERSION, kind='directory')
    if module_target.exists():
        for path in module_target.rglob('*'):
            if path.is_symlink() or (not path.is_dir() and not path.is_file()):
                raise ValueError('Unexpected existing target module-tree entry')
            if path.is_file():
                regular(path)
                if str(path.relative_to(module_target)) not in module_files:
                    raise ValueError('Target kernel directory contains unstaged files')
    validate_staged(root, staged, source_files)
    rows = validate_firstboot(root)
    validate_privacy(root, staged)
    copies = []
    if digest(args.boot_image) != BOOT_SHA:
        raise ValueError('Boot artifact hash mismatch')
    copies.append((args.boot_image, 'boot/Image', 0o644))
    haptics, overlays = validate_haptics(root, args.source_root, args.haptics_binary)
    copies.extend((args.haptics_binary, name, 0o755) for name in HAPTICS_DESTINATIONS)
    for item in haptics['install_payloads'].values():
        source = checked(args.source_root, 'components/inputplumber/' + item['source'])
        if digest(source) != item['sha256'] or regular(source).st_size != item['size']:
            raise ValueError('Haptics configuration generation mismatch')
        copies.append((source, item['destination'].removeprefix('/'), int(item['mode'], 8)))
    copies.extend((args.modules / name, 'usr/lib/modules/' + VERSION + '/' + name, 0o644) for name in sorted(module_files))
    copies.append((args.source_archive, 'usr/share/pocketds-linux-kit/source.tar.gz', 0o644))
    for _source, name, _mode in copies:
        checked(root, name, allow_link=(name == 'boot/Image'))
    deletes = ['etc/' + n for n in ('shadow-', 'gshadow-', 'passwd-', 'group-')]
    deletes += ['var/lib/systemd/random-seed', 'var/lib/NetworkManager/secret_key',
                'var/lib/NetworkManager/seen-bssids', 'var/lib/NetworkManager/timestamps',
                'var/lib/pocketds-firstboot/lock', 'var/lib/pocketds-firstboot/resize.lock']
    ssh = checked(root, 'etc/ssh', kind='directory')
    deletes.extend(str(p.relative_to(root)) for p in ssh.glob('ssh_host_*'))
    for name in deletes:
        checked(root, name)
    for folder in TRANSIENTS:
        checked(root, folder, kind='directory')
    for unit in MASKS:
        checked(root, 'etc/systemd/system/' + unit, allow_link=True)
    for name in ('etc/plasma-setup-done', 'etc/machine-id',
                 'usr/share/pocketds-linux-kit/rpm-sources.tsv',
                 'usr/share/pocketds-linux-kit/image-seal.json'):
        checked(root, name)
    return staged, export, module_files, rows, copies, deletes, overlays


def write(root, name, *, source=None, data=None, mode=0o644, allow_link=False):
    path = checked(root, name, allow_link=allow_link)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(prefix='.pocketds-seal-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            if source is not None:
                regular(source)
                with source.open('rb') as payload:
                    shutil.copyfileobj(payload, stream)
            else:
                stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fchown(stream.fileno(), 0, 0)
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.lexists(temp):
            os.unlink(temp)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'source-root', 'source-archive', 'export-manifest', 'boot-image',
                 'haptics-binary', 'modules', 'module-manifest'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--plan', action='store_true', help='validate all inputs without writing')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('Run only inside the isolated image builder as root')
    staged, export, modules, shadow, copies, deletes, overlays = prepare(args)
    if args.plan:
        print(json.dumps({'planned': True, 'files': len(copies), 'module_files_verified': len(modules),
                          'binary_overlays': overlays}))
        return
    receipt = {'schema': 'pocketds.sd-image-sealed.v1', 'kernel': VERSION,
               'accepted_modules_verified': 317, 'extra_modules': [RFCOMM],
               'module_files_verified': len(modules), 'module_manifest_sha256': digest(args.module_manifest),
               'source_export_manifest_sha256': digest(args.export_manifest), 'source_commit': export['source_commit'],
               'hardware_boot_tested': False, 'public_release_ready': False,
               'source_archive_sha256': digest(args.source_archive), 'files': [],
               'binary_overlays': overlays}
    root = args.root
    for source, destination, mode in copies:
        path = write(root, destination, source=source, mode=mode, allow_link=(destination == 'boot/Image'))
        receipt['files'].append({'path': '/' + destination, 'size': path.stat().st_size,
                                 'sha256': digest(path), 'mode': f'{mode:04o}'})
    subprocess.run(['/usr/sbin/depmod', '-b', str(root), VERSION], check=True)
    module_root = checked(root, 'usr/lib/modules/' + VERSION, kind='directory')
    after_files = {str(path.relative_to(module_root)) for path in module_root.rglob('*') if path.is_file()}
    if after_files != set(modules) or any(path.is_symlink() for path in module_root.rglob('*')):
        raise ValueError('depmod changed the frozen module file set')
    for name, row in modules.items():
        if digest(checked(root, 'usr/lib/modules/' + VERSION + '/' + name)) != row['sha256']:
            raise ValueError('depmod output differs from the frozen module bundle')
    for row in shadow:
        if row[0] in {'root', 'pocketds'}:
            row[1] = '!'
    write(root, 'etc/shadow', data=('\n'.join(':'.join(row) for row in shadow) + '\n').encode(), mode=0o000)
    for name in deletes:
        checked(root, name).unlink(missing_ok=True)
    for unit in MASKS:
        target = checked(root, 'etc/systemd/system/' + unit, allow_link=True)
        if target.exists() or target.is_symlink():
            target.unlink()
        target.symlink_to('/dev/null')
    write(root, 'etc/plasma-setup-done', data=b'')
    write(root, 'etc/machine-id', data=b'', mode=0o444)
    reject_mounts(root)
    for folder in TRANSIENTS:
        base = checked(root, folder, kind='directory')
        if not base.exists():
            continue
        for path in base.iterdir():
            if path.is_symlink() or not path.is_dir():
                path.unlink()
            else:
                shutil.rmtree(path)
    reject_retired_haptics(root)
    remaining_rpms = [str(path.relative_to(root)) for path in root.rglob('*.rpm')
                      if path.is_file() and not path.is_symlink()]
    if remaining_rpms:
        raise ValueError('Unexpected RPM payload archives remain in the sealed root')
    packages = subprocess.check_output(
        ['/usr/bin/rpm', '--root', str(root), '-qa', '--qf',
         '%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\t%{SOURCERPM}\n'], text=True)
    manifest = write(root, 'usr/share/pocketds-linux-kit/rpm-sources.tsv',
                     data=''.join(sorted(packages.splitlines(keepends=True))).encode())
    receipt.update(rpm_manifest_sha256=digest(manifest), rpm_package_count=len(packages.splitlines()),
                   passwords_locked=True, machine_identity_empty=True, api_configured=False, firstboot_ready=True,
                   retired_inputplumber_payloads_absent=True, rpm_payload_archive_count=0)
    write(root, 'usr/share/pocketds-linux-kit/image-seal.json',
          data=(json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode())
    checked(root, 'etc/pocketds-image-build-root').unlink()
    print(json.dumps({'sealed': True, 'kernel': VERSION, 'files': len(receipt['files']),
                      'packages': receipt['rpm_package_count'], 'hardware_boot_tested': False}))


if __name__ == '__main__':
    main()

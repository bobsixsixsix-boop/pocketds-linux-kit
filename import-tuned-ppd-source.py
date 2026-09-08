#!/usr/bin/env python3
"""Fixed history-free TuneD source import; validate before mutation."""
from pathlib import Path, PurePosixPath
import hashlib, io, json, os, re, stat, subprocess, sys, tarfile, tempfile
ARCHIVE_SHA = '11305f3b3a9f05d53ca696aef8d441f045320bab61e90e24b99133ee8966bad2'
MANIFEST_SHA = '093e21a7264a7de7df3bcfebe8bd08a456e97feced1733e3f103bbf7cddf555e'
SOURCE_COMMIT = 'fcdd1febec755bfabdad169c2b4e863c0af6cca5'
SOURCE_COUNT = 1466
OLD_SOURCE_COMMIT = '68d0dea21f495b0266e8ced4c79dbd6f3cbe7d5f'
OLD_SOURCE_COUNT = 1459
OLD_MANIFEST_SHA = '06682725dc759bccec52a229e3636dda6b95d4557dbd63411f1834c004ce350c'
ARCHIVE_NAME = 'tuned-ppd-source.tar.gz'
MANIFEST_NAME = 'tuned-ppd-source-manifest.json'
SCRIPT_NAME = 'import-tuned-ppd-source.py'
MARKER = '.github/tuned-ppd-source-import-complete.json'
WORKFLOW_PATH = '.github/workflows/import-tuned-ppd-source.yml'
RELEASE_MANIFEST = 'docs/release/source-preview-manifest.json'
PRESERVED_METADATA = {'.github/alpha3-source-import-complete.json': {'sha256': '4be42738e8a4beedf5816b0395d6a9831de88b5d6f0b04fc3dd3ad8e68a8f086', 'size': 342, 'mode': '0644'}, '.github/powerdevil-abi-source-import-complete.json': {'sha256': 'b5d838fbfee91900ef906d675c42874a54c7723b876fff87c53b0b73ab5437ae', 'size': 342, 'mode': '0644'}, '.github/source-import-complete.json': {'sha256': 'ccaca275a69672e9768ab56a1d57e7ba8948cafe822d8221feadf48819f7a288', 'size': 269, 'mode': '0644'}, '.github/workflows/import-alpha3-source.yml': {'sha256': '4d6a917abc450f834b7a46450979004125d20de87de6d0fa6c4e35582703ddcf', 'size': 1036, 'mode': '0644'}, '.github/workflows/import-reviewed-source.yml': {'sha256': '3d7deb2392d68049605350eca8d432a29778d3ea6583f0c90f213f52f92b7658', 'size': 3541, 'mode': '0644'}, '.github/workflows/import-powerdevil-abi-source.yml': {'sha256': 'e054b2e3f4f8e1777af97f3a85e8c9921f14fc5d6a3e07bdd4cdaedf223a8729', 'size': 1101, 'mode': '0644'}}
WORKFLOW_TEMPLATE = "name: Import reviewed TuneD PPD bridge source update\non:\n  workflow_dispatch:\npermissions: {}\nconcurrency:\n  group: pocketds-reviewed-source-import\n  cancel-in-progress: false\njobs:\n  import:\n    if: github.repository == 'bobsixsixsix-boop/pocketds-linux-kit' && github.ref == 'refs/heads/main'\n    runs-on: ubuntu-latest\n    timeout-minutes: 10\n    permissions:\n      contents: write\n    steps:\n      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262\n        with:\n          fetch-depth: 1\n      - name: Verify the reviewed importer\n        run: echo 'SCRIPT_SHA  import-tuned-ppd-source.py' | sha256sum --check --strict\n      - name: Verify predecessor and import the fixed TuneD bridge archive\n        run: python3 import-tuned-ppd-source.py\n      - name: Commit the reviewed TuneD bridge source\n        run: |\n          git config user.name 'github-actions[bot]'\n          git config user.email '41898282+github-actions[bot]@users.noreply.github.com'\n          git -c core.fileMode=true add --all\n          git commit -m 'Connect KDE power profiles to TuneD with reviewed source fcdd1fe'\n          git push origin HEAD:main\n"
ROOT = Path.cwd()
INPUT_NAMES = (ARCHIVE_NAME, MANIFEST_NAME, SCRIPT_NAME)

class ImportRejected(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ImportRejected(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name, *, metadata=False):
    require(isinstance(name, str) and bool(name), 'invalid path type')
    p = PurePosixPath(name)
    require(not p.is_absolute() and str(p) == name, 'noncanonical path')
    require(not any(ord(c) < 32 or ord(c) == 127 for c in name), 'control character in path')
    require('\\' not in name and ':' not in name, 'nonportable path')
    for part in p.parts:
        normalized = part.casefold().rstrip(' .')
        require(part not in ('.', '..') and normalized != '.git', 'Git or traversal path')
        require(metadata or normalized != '.github', 'workflow content in source archive')
    return name


def file_bytes(path):
    # Parents are checked without following links before opening any leaf.
    rel = path.relative_to(ROOT)
    parent = ROOT
    for part in rel.parts[:-1]:
        parent /= part
        meta = parent.lstat()
        require(stat.S_ISDIR(meta.st_mode), 'unsafe parent: ' + str(rel))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'unsafe regular file: ' + str(rel))
        data = stream.read()
        after = os.fstat(stream.fileno())
    require(identity(before) == identity(after) == identity(path.lstat()), 'file changed while read: ' + str(rel))
    return data, stat.S_IMODE(before.st_mode), identity(before)


def identity(meta):
    return (meta.st_dev, meta.st_ino, meta.st_mode, meta.st_nlink,
            meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)


def inventory():
    files = {}
    for directory, dirs, names in os.walk(ROOT, followlinks=False):
        parent = Path(directory)
        if parent == ROOT:
            require('.git' in dirs and not (ROOT / '.git').is_symlink(), 'expected a regular checkout .git directory')
            dirs.remove('.git')
        for name in dirs:
            p = parent / name
            safe_name(p.relative_to(ROOT).as_posix(), metadata=True)
            require(stat.S_ISDIR(p.lstat().st_mode), 'linked or special directory')
        for name in names:
            p = parent / name
            rel = safe_name(p.relative_to(ROOT).as_posix(), metadata=True)
            data, mode, token = file_bytes(p)
            files[rel] = {'sha256': sha(data), 'size': len(data), 'mode': f'{mode:04o}', 'identity': token}
    return files


def unique_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def parse_manifest(data, digest, commit, count):
    require(sha(data) == digest, 'manifest hash mismatch')
    result = unique_json(data)
    require(result['source_commit'] == commit, 'source commit mismatch')
    rows = {}
    folded = set()
    for row in result['files']:
        name = safe_name(row['path'])
        require(name not in rows and name.casefold() not in folded, 'duplicate source path')
        require(row['mode'] in ('0644', '0755'), 'invalid source mode')
        require(type(row['size']) is int and row['size'] >= 0, 'invalid source size')
        require(re.fullmatch('[0-9a-f]{64}', row['sha256']) is not None, 'invalid source hash')
        require(name != RELEASE_MANIFEST and name not in INPUT_NAMES, 'reserved metadata path')
        rows[name] = row
        folded.add(name.casefold())
    require(len(rows) == count, 'source count mismatch')
    for name in rows:
        require(not any(str(p) in rows for p in PurePosixPath(name).parents), 'file/directory path collision')
    return result, rows


def validate_archive(data, rows):
    require(sha(data) == ARCHIVE_SHA, 'archive hash mismatch')
    return validate_archive_members(data, rows)


def validate_archive_members(data, rows):
    # Pure parser for isolated malformed-archive fixtures; production calls the pinned wrapper.
    payload = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for item in archive:
            require(item.isfile() and not item.islnk() and not item.issym(), 'non-regular archive entry')
            require(item.name.startswith('source/'), 'archive prefix mismatch')
            name = safe_name(item.name.removeprefix('source/'))
            require(name in rows and name not in payload, 'unknown or duplicate archive path')
            row = rows[name]
            require(item.mode == int(row['mode'], 8) and item.size == row['size'], 'archive metadata mismatch')
            content = archive.extractfile(item).read(row['size'] + 1)
            require(len(content) == row['size'] and sha(content) == row['sha256'], 'archive content mismatch')
            payload[name] = (content, item.mode)
    require(set(payload) == set(rows), 'incomplete archive')
    return payload


def verify_records(actual, expected):
    require(set(actual) == set(expected), 'unexpected or missing checkout file')
    for name, row in expected.items():
        require(all(actual[name][k] == row[k] for k in ('sha256', 'size', 'mode')), 'checkout hash/size/mode mismatch: ' + name)


def record(data, mode='0644'):
    return {'sha256': sha(data), 'size': len(data), 'mode': mode}


def verify_output_paths(names, actual):
    for name in names:
        path = ROOT / name
        for parent in reversed(path.parents):
            if parent == ROOT or ROOT in parent.parents:
                try:
                    require(stat.S_ISDIR(parent.lstat().st_mode), 'unsafe future parent: ' + name)
                except FileNotFoundError:
                    pass
        try:
            meta = path.lstat()
        except FileNotFoundError:
            continue
        require(name in actual and stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1,
                'occupied future output path: ' + name)


def verify_index(expected):
    entries = {}
    for entry in subprocess.check_output(['git', 'ls-files', '--stage', '-z']).split(b'\0'):
        if not entry:
            continue
        header, encoded = entry.split(b'\t', 1)
        mode, _object, stage = header.split()
        name = safe_name(encoded.decode('utf-8'), metadata=True)
        require(stage == b'0' and mode in (b'100644', b'100755'), 'unsafe or conflicted index entry')
        require(name not in entries, 'duplicate index path')
        entries[name] = mode[-4:].decode('ascii')
    require(set(entries) == set(expected), 'unexpected or missing tracked file')
    require(all(entries[name] == row['mode'] for name, row in expected.items()), 'tracked mode mismatch')


def preflight():
    require(not (ROOT / MARKER).exists() and not (ROOT / MARKER).is_symlink(), 'update already imported or marker occupied')
    git_root = subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip()
    require(Path(git_root) == ROOT, 'run from the checkout root')
    require(not subprocess.check_output(['git', 'status', '--porcelain=v1', '--untracked-files=all']), 'checkout must be clean')
    actual = inventory()
    old_data, _, _ = file_bytes(ROOT / RELEASE_MANIFEST)
    old_manifest, old = parse_manifest(old_data, OLD_MANIFEST_SHA, OLD_SOURCE_COMMIT, OLD_SOURCE_COUNT)
    manifest_data, _, _ = file_bytes(ROOT / MANIFEST_NAME)
    new_manifest, new = parse_manifest(manifest_data, MANIFEST_SHA, SOURCE_COMMIT, SOURCE_COUNT)
    require(new_manifest['archive']['sha256'] == ARCHIVE_SHA, 'manifest archive binding mismatch')
    archive_data, _, _ = file_bytes(ROOT / ARCHIVE_NAME)
    payload = validate_archive(archive_data, new)
    script_data, _, _ = file_bytes(ROOT / SCRIPT_NAME)
    expected = dict(old)
    expected.update(PRESERVED_METADATA)
    expected[RELEASE_MANIFEST] = record(old_data)
    expected[ARCHIVE_NAME] = record(archive_data)
    expected[MANIFEST_NAME] = record(manifest_data)
    expected[SCRIPT_NAME] = record(script_data)
    expected[WORKFLOW_PATH] = record(WORKFLOW_TEMPLATE.replace('SCRIPT_SHA', sha(script_data)).encode())
    verify_records(actual, expected)
    verify_index(expected)
    verify_output_paths([*new, RELEASE_MANIFEST, MARKER], actual)
    require(all(name in old or name not in actual for name in new), 'new source would overwrite unrelated file')
    # Recheck every leaf after all archive validation; no source writes precede this.
    require(inventory() == actual, 'checkout changed during validation')
    return actual, old, new, payload, manifest_data


def atomic_write(name, data, mode):
    path = ROOT / name
    parent = ROOT
    for part in path.relative_to(ROOT).parts[:-1]:
        parent /= part
        try:
            parent.mkdir(mode=0o755)
        except FileExistsError:
            require(stat.S_ISDIR(parent.lstat().st_mode), 'unsafe output parent')
    if path.exists() or path.is_symlink():
        file_bytes(path)
    fd, temporary = tempfile.mkstemp(prefix='.reviewed-source-', dir=parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    actual, old, new, payload, manifest_data = preflight()
    for name in old.keys() - new.keys():
        require(file_bytes(ROOT / name)[2] == actual[name]['identity'], 'removed file changed after validation')
        (ROOT / name).unlink()
    for name, (data, mode) in payload.items():
        if name in actual:
            require(file_bytes(ROOT / name)[2] == actual[name]['identity'], 'source changed after validation')
        atomic_write(name, data, mode)
    atomic_write(RELEASE_MANIFEST, manifest_data, 0o644)
    receipt = {
        'schema': 'pocketds.public-source-import.v3',
        'source_commit': SOURCE_COMMIT, 'files_verified': SOURCE_COUNT,
        'archive_sha256': ARCHIVE_SHA, 'manifest_sha256': MANIFEST_SHA,
        'previous_source_commit': OLD_SOURCE_COMMIT,
        'previous_manifest_sha256': OLD_MANIFEST_SHA,
        'previous_files_verified': OLD_SOURCE_COUNT,
        'previous_modes_verified': True, 'new_modes_verified': True,
        'preserved_github_paths': sorted(PRESERVED_METADATA),
        'original_git_history_imported': False,
        'published_sd_alpha3_changed': False,
    }
    marker_data = (json.dumps(receipt, indent=2) + '\n').encode()
    atomic_write(MARKER, marker_data, 0o644)
    for name in INPUT_NAMES:
        require(file_bytes(ROOT / name)[2] == actual[name]['identity'], 'bootstrap changed after validation')
        (ROOT / name).unlink()
    expected = dict(new)
    expected.update(PRESERVED_METADATA)
    expected[RELEASE_MANIFEST] = record(manifest_data)
    expected[WORKFLOW_PATH] = {k: actual[WORKFLOW_PATH][k] for k in ('sha256', 'size', 'mode')}
    expected[MARKER] = record(marker_data)
    verify_records(inventory(), expected)
    print('Verified and imported', SOURCE_COUNT, 'source files; previous/new modes and preserved GitHub metadata verified; no original Git history.')


if __name__ == '__main__':
    try:
        main()
    except (ImportRejected, OSError, ValueError, KeyError, tarfile.TarError, subprocess.SubprocessError) as error:
        print('Import rejected:', str(error), file=sys.stderr)
        sys.exit(1)

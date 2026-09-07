#!/usr/bin/env python3
"""Build only in the prepared, isolated v4 tree; never install or boot it."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

BASE = Path('/home/pocketds/pds001-kernel-build-input/linux-7.1.12')
COPY = Path('/home/pocketds/pds001-kernel-build-input/linux-7.1.12-dsc-v4')
REL = 'drivers/gpu/drm/msm/disp/dpu1/dpu_rm.c'
CONFIG_SHA = 'fe66e634d06015d7494ac657bfc96655222bdf56998db4b03f317371beccd51c'
OLD_SHA = 'aab48d26ed2a7413d7944e84068e07150a469073573f8a0fb4da628cf8ec2851'
NEW_SHA = '2a7a2215949451918ba39f9badf8a59afd1d27cd13dae3d5fd1c566a0192956a'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def temperatures():
    result = []
    for zone in Path('/sys/class/thermal').glob('thermal_zone*'):
        try:
            value = int((zone / 'temp').read_text())
        except (OSError, ValueError):
            continue
        if 0 < value < 150000:
            result.append(value)
    require(bool(result), 'No usable temperature sensor')
    return max(result)


def initramfs_members(path):
    """Compare every newc field/payload except the reproducible-build mtime."""
    blob = path.read_bytes()
    result, offset = {}, 0
    while offset + 110 <= len(blob):
        require(blob[offset:offset + 6] == b'070701', 'Unknown initramfs encoding')
        fields = [int(blob[offset + 6 + i * 8:offset + 14 + i * 8], 16)
                  for i in range(13)]
        length = fields[11]
        name = blob[offset + 110:offset + 110 + length]
        require(name.endswith(b'\0'), 'Truncated initramfs filename')
        offset = (offset + 110 + length + 3) & ~3
        data = blob[offset:offset + fields[6]]
        require(len(data) == fields[6], 'Truncated initramfs payload')
        offset = (offset + fields[6] + 3) & ~3
        if name == b'TRAILER!!!\0':
            require(not any(blob[offset:]), 'Unexpected trailing initramfs content')
            return result
        require(name not in result, 'Duplicate initramfs filename')
        fields[5] = 0  # KBUILD_BUILD_TIMESTAMP intentionally differs for #4.
        result[name] = (fields, data)
    raise RuntimeError('Missing initramfs trailer')


def verify_outputs():
    require(digest(BASE / REL) == OLD_SHA and digest(COPY / REL) == NEW_SHA,
            'Source changed during compilation')
    require(digest(COPY / '.config') == CONFIG_SHA, 'Build changed configuration')
    baseline_exports = {line for line in (BASE / 'Module.symvers').read_text().splitlines()
                        if line.split()[2] == 'vmlinux'}
    candidate_exports = set((COPY / 'vmlinux.symvers').read_text().splitlines())
    require(baseline_exports and baseline_exports == candidate_exports,
            'Freshly linked exported module ABI differs')
    require(initramfs_members(BASE / 'usr/initramfs_data.cpio') ==
            initramfs_members(COPY / 'usr/initramfs_data.cpio'),
            'Initramfs content changed beyond archive timestamps')
    for metadata in ('modules.builtin', 'modules.builtin.modinfo'):
        require((BASE / metadata).read_bytes() == (COPY / metadata).read_bytes(),
                'Built-in metadata changed: ' + metadata)
    modules = list(BASE.rglob('*.ko'))
    require(bool(modules), 'No module comparison possible')
    for module in modules:
        require(digest(module) == digest(COPY / module.relative_to(BASE)),
                'External module changed: ' + str(module.relative_to(BASE)))
    print(json.dumps({'build': 'passed', 'module_count_identical': len(modules),
                      'fresh_exports_identical': len(baseline_exports),
                      'initramfs_payload_identical': True,
                      'raw_image_sha256': digest(COPY / 'arch/arm64/boot/Image'),
                      'source_sha256': digest(COPY / REL), 'installed': False}), flush=True)


def main():
    require(os.geteuid() != 0, 'Build as ordinary user')
    require(COPY.is_dir() and not COPY.is_symlink(), 'Missing isolated build copy')
    require(not os.path.samefile(BASE / REL, COPY / REL), 'Source must not be hard-linked')
    require(digest(BASE / REL) == OLD_SHA and digest(COPY / REL) == NEW_SHA,
            'Source identities differ')
    require(digest(BASE / '.config') == CONFIG_SHA == digest(COPY / '.config'),
            'Kernel configuration differs')
    require(temperatures() < 70000, 'Too hot to start compilation')
    env = dict(os.environ, KBUILD_BUILD_VERSION='4',
               KBUILD_BUILD_TIMESTAMP='Sat Sep 5 08:00:00 UTC 2026',
               KBUILD_BUILD_USER='pocketds', KBUILD_BUILD_HOST='pocketds')
    command = ['taskset', '-c', '0-2', 'nice', '-n', '10', 'make', '-j3',
               'LOCALVERSION=-pdsdiag.20260905.aarch64', 'Image']
    started = time.monotonic()
    log = COPY.parent / 'build-v4-dsc.log'
    with log.open('x') as stream:
        process = subprocess.Popen(command, cwd=COPY, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None:
                temp = temperatures()
                print(json.dumps({'build_pid': process.pid, 'max_millicelsius': temp,
                                  'elapsed_seconds': round(time.monotonic() - started)}),
                      flush=True)
                require(temp < 75000, 'Thermal stop at 75 C')
                require(time.monotonic() - started < 1800, 'Build time bound exceeded')
                time.sleep(5)
            require(process.returncode == 0, 'Compilation failed; see build-v4-dsc.log')
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    verify_outputs()


if __name__ == '__main__':
    main()

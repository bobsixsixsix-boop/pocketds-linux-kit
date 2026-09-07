#!/usr/bin/env python3
"""Offline, fail-closed check/repack of the one Pocket DS DT-only experiment.

No device access, installation or suspend. Output must not exist. Exact v2
input is locked; every surviving DT node/property must be byte-identical.
"""
import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib

if not __debug__:
    raise RuntimeError('optimized Python disables verification; refusing to run')


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def tree(blob):
    header = struct.unpack_from('>10I', blob)
    magic, total, start, strings, reserve, version, compat, cpu, strsize, size = header
    assert magic == 0xd00dfeed and total == len(blob)
    assert version == 17 and start + size <= total and strings + strsize <= total
    result, stack, pos = {}, [], start
    while pos < start + size:
        token = struct.unpack_from('>I', blob, pos)[0]
        pos += 4
        if token == 1:
            end = blob.index(b'\0', pos, start + size)
            stack.append(blob[pos:end].decode())
            path = '/'.join(stack) or '/'
            assert path not in result
            result[path] = {}
            pos = (end + 4) & ~3
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, offset = struct.unpack_from('>2I', blob, pos)
            pos += 8
            assert offset < strsize and pos + length <= start + size
            end = blob.index(b'\0', strings + offset, strings + strsize)
            name = blob[strings + offset:end].decode()
            props = result['/'.join(stack) or '/']
            assert name not in props
            props[name] = blob[pos:pos + length]
            pos = (pos + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            assert not stack
            break
        else:
            raise ValueError('unexpected FDT token')
    else:
        raise ValueError('missing FDT end')
    reserved, pos = [], reserve
    while True:
        entry = struct.unpack_from('>2Q', blob, pos)
        pos += 16
        reserved.append(entry)
        if entry == (0, 0):
            break
    return result, (version, compat, cpu, reserved)


def verify(before, after):
    old, oldmeta = tree(before)
    new, newmeta = tree(after)
    assert oldmeta == newmeta
    for zone in range(4):
        for index, temp in enumerate((40000, 50000, 60000, 65000, 70000, 75000, 80000), 2):
            path = f'/thermal-zones/cpuss{zone}-thermal/trips/trip-point{index}'
            assert old.pop(path) == {
                'temperature': struct.pack('>I', temp),
                'hysteresis': struct.pack('>I', 3000),
                'type': b'passive\0',
            }, path
    assert old == new, 'DT delta exceeds the 28 whitelisted orphan nodes'


def compose(boot, after):
    assert digest(boot) == '79f008b6aae1cfbe0c984ee987d1de4feafbe3c4f607fcc4f596f02b3e800bcb'
    assert boot[:8] == b'ANDROID!' and struct.unpack_from('<I', boot, 36)[0] == 2048
    assert all(struct.unpack_from('<I', boot, n)[0] == 0 for n in (16, 24, 40))
    length = struct.unpack_from('<I', boot, 8)[0]
    kernel = boot[2048:2048 + length]
    decoder = zlib.decompressobj(31)
    raw = decoder.decompress(kernel)
    assert decoder.eof and digest(raw) == 'bde22bbb420b27a71c095d367bbb32ada42e7dbaae1d1fcd6c5b761877ae1079'
    before = decoder.unused_data
    assert digest(before) == '3b3d654f86a97c50764e22712edefeef1d687ce602cc6e00a5a352d155024c25'
    verify(before, after)
    compressed = kernel[:-len(before)]
    payload = compressed + after
    header = bytearray(boot[:2048])
    struct.pack_into('<I', header, 8, len(payload))
    header[576:608] = hashlib.sha1(payload + struct.pack('<I', len(payload)) + bytes(8)).digest() + bytes(12)
    result = bytes(header) + payload + bytes((-len(payload)) % 2048)
    assert result[:8] == boot[:8] and result[12:576] == boot[12:576] and result[608:2048] == boot[608:2048]
    return result, {'removed_orphan_trips': 28, 'all_surviving_dt_properties_identical': True,
                    'kernel_gzip_identical': True, 'gzip_sha256': digest(compressed),
                    'dtb_sha256': digest(after), 'boot_sha256': digest(result), 'boot_size': len(result)}


if __name__ == '__main__':
    import os
    os.umask(0o077)
    assert len(sys.argv) == 4, 'usage: verify-orphan-trip-dtb.py V2_BOOT NEW_DTB NEW_BOOT'
    result, report = compose(Path(sys.argv[1]).read_bytes(), Path(sys.argv[2]).read_bytes())
    with Path(sys.argv[3]).open('xb') as target:
        target.write(result)
    print(json.dumps(report, indent=2))

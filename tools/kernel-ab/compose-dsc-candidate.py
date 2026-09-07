#!/usr/bin/env python3
"""Repack the locked v3 container with the verified v4 raw Image, offline only."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import zlib

BASE_SHA = 'a136aeb060d38f9a305336af36577c5971e7c7e81e78839a608a60d30ff24c71'
RAW_SHA = '37c5d65c381aa434cdfc43dbe8923ebe8525b6dd85818b41da5e003974a5b653'
BASE_RAW_SHA = 'bde22bbb420b27a71c095d367bbb32ada42e7dbaae1d1fcd6c5b761877ae1079'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unpack(boot):
    require(boot[:8] == b'ANDROID!', 'Wrong container magic')
    require(struct.unpack_from('<I', boot, 36)[0] == 2048, 'Wrong page size')
    require(all(struct.unpack_from('<I', boot, n)[0] == 0 for n in (16, 24, 40)),
            'Unexpected ramdisk, second stage or header version')
    length = struct.unpack_from('<I', boot, 8)[0]
    require(len(boot) == 2048 + ((length + 2047) // 2048) * 2048,
            'Wrong container length')
    require(not any(boot[2048 + length:]), 'Nonzero container padding')
    payload = boot[2048:2048 + length]
    image_id = hashlib.sha1(payload + struct.pack('<I', length) + bytes(8)).digest() + bytes(12)
    require(boot[576:608] == image_id, 'Wrong Android image ID')
    decoder = zlib.decompressobj(31)
    raw = decoder.decompress(payload) + decoder.flush()
    require(decoder.eof and not decoder.unconsumed_tail, 'Incomplete compressed Image')
    dtb = decoder.unused_data
    require(len(dtb) >= 40 and struct.unpack_from('>I', dtb)[0] == 0xd00dfeed,
            'Missing device tree')
    require(struct.unpack_from('>I', dtb, 4)[0] == len(dtb), 'Wrong device tree length')
    return raw, dtb


def compose(boot, raw):
    require(sha(boot) == BASE_SHA and sha(raw) == RAW_SHA, 'Unrecognized build inputs')
    old_raw, dtb = unpack(boot)
    require(sha(old_raw) == BASE_RAW_SHA, 'Unexpected original raw Image')
    require(raw[56:60] == b'ARM\x64', 'Not an arm64 Image')
    output = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=output, mtime=0) as compressed:
        compressed.write(raw)
    payload = output.getvalue() + dtb
    header = bytearray(boot[:2048])
    struct.pack_into('<I', header, 8, len(payload))
    header[576:608] = hashlib.sha1(payload + struct.pack('<I', len(payload)) + bytes(8)).digest() + bytes(12)
    result = bytes(header) + payload + bytes((-len(payload)) % 2048)
    require(result[:8] == boot[:8] and result[12:576] == boot[12:576] and
            result[608:2048] == boot[608:2048], 'Unexpected boot header changes')
    new_raw, new_dtb = unpack(result)
    require(new_raw == raw and new_dtb == dtb, 'Repacked contents differ')
    return result, {'boot_sha256': sha(result), 'boot_size': len(result),
                    'raw_image_sha256': sha(raw), 'dtb_sha256': sha(dtb),
                    'dtb_identical': True, 'header_addresses_cmdline_identical': True,
                    'installed': False, 'hardware_accepted': False}


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit('usage: compose-dsc-candidate.py V3_BOOT V4_RAW NEW_BOOT')
    result, report = compose(Path(sys.argv[1]).read_bytes(), Path(sys.argv[2]).read_bytes())
    with Path(sys.argv[3]).open('xb') as target:
        target.write(result)
    print(json.dumps(report, indent=2))

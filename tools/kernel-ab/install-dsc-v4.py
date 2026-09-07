#!/usr/bin/env python3
"""Locked v3 <-> DSC-v4 boot-file transaction; inspection by default.

Uses the previously tested atomic single-file transaction unchanged. It never
enables sleep, reboots, flashes a partition, or touches modules. Populate the
root-private stage with the two exact images before inspection; preserve the
independent off-device recovery image from the original installation.
"""
import hashlib
import importlib.util
from pathlib import Path

BASE_TOOL_SHA = '181715b132024f7e4c575f5417f3c383cc43bd2008db9a17f41a83eacffba85f'
RELEASE = '7.1.12-pdsdiag.20260905.aarch64'
V3_NOTES = '044509cbbdd347135937ee3de42fb2bebe099a17bfa939e5a0093bd4760c64d0'
V4_NOTES = '05ffb67550d15176df5302978a51cf1834fa70869e663f8e01c440c253116092'
RESCUE_NOTES = '2b7ae7949422c36be686322fb12a8f8340127596accccc133716f2c420bae6f1'
IMAGES = {
    'baseline': ('a136aeb060d38f9a305336af36577c5971e7c7e81e78839a608a60d30ff24c71', 19077120),
    'candidate': ('57322cb6dc3bce822289bcd6bde5e5934362efc2547120ebc88be0eea7a89d92', 18993152),
}


def prepare():
    source = Path(__file__).with_name('install-current-trial.py')
    if hashlib.sha256(source.read_bytes()).hexdigest() != BASE_TOOL_SHA:
        raise RuntimeError('base transaction changed; review before using the v4 wrapper')
    spec = importlib.util.spec_from_file_location('dsc_v4_boot_transaction', source)
    transaction = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transaction)
    notes = hashlib.sha256(Path('/sys/kernel/notes').read_bytes()).hexdigest()
    # Both diagnostic kernels share uname. Select only an exact known notes
    # identity; the original rescue kernel retains its separate release lock.
    if notes not in (V3_NOTES, V4_NOTES, RESCUE_NOTES):
        raise RuntimeError('unverified running kernel notes')
    transaction.NOTES[RELEASE] = notes if notes in (V3_NOTES, V4_NOTES) else V4_NOTES
    transaction.IMAGES = dict(IMAGES)
    transaction.ROOT = Path('/var/lib/pocketds-linux-kit/kernel-dsc-v4-20260907')
    transaction.CONFIRM = 'POCKETDS-DSC-V4-BOOT-20260907'
    return transaction


if __name__ == '__main__':
    prepare().main()

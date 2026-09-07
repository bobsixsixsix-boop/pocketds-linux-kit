#!/usr/bin/env python3
"""Synthetic FDT regression checks: no hardware access."""
import copy
import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location('check', Path(__file__).resolve().parents[1] / 'tools/kernel-ab/verify-orphan-trip-dtb.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def fixture():
    nodes = {'/': {}, '/thermal-zones': {}}
    for zone in range(4):
        path = f'/thermal-zones/cpuss{zone}-thermal'
        nodes[path] = {}
        nodes[path + '/trips'] = {}
        for i, temp in enumerate((40000, 50000, 60000, 65000, 70000, 75000, 80000), 2):
            nodes[path + f'/trips/trip-point{i}'] = {'temperature': struct.pack('>I', temp), 'hysteresis': struct.pack('>I', 3000), 'type': b'passive\0'}
    nodes['/thermal-zones/cpu-critical'] = {'temperature': struct.pack('>I', 110000), 'type': b'critical\0'}
    new = {p: v for p, v in nodes.items() if '/trip-point' not in p}
    return nodes, copy.deepcopy(new)


def encode(nodes):
    strings, offsets = b'', {}
    for props in nodes.values():
        for key in props:
            if key not in offsets:
                offsets[key] = len(strings)
                strings += key.encode() + b'\0'
    def padded(data):
        return data + bytes(-len(data) % 4)
    def node(path):
        name = '' if path == '/' else path.rsplit('/', 1)[1]
        data = struct.pack('>I', 1) + padded(name.encode() + b'\0')
        for key, value in nodes[path].items():
            data += struct.pack('>3I', 3, len(value), offsets[key]) + padded(value)
        for child in nodes:
            if child != '/' and (child.rsplit('/', 1)[0] or '/') == path:
                data += node(child)
        return data + struct.pack('>I', 2)
    body = node('/') + struct.pack('>I', 9)
    header = struct.pack('>10I', 0xd00dfeed, 56 + len(body) + len(strings), 56, 56 + len(body), 40, 17, 16, 0, len(strings), len(body))
    return header + bytes(16) + body + strings


class Verification(unittest.TestCase):
    def test_exact_delta(self):
        old, new = fixture()
        check.verify(encode(old), encode(new))

    def test_reject_protection_change(self):
        old, new = fixture()
        new['/thermal-zones/cpu-critical']['temperature'] = struct.pack('>I', 120000)
        with self.assertRaises(AssertionError):
            check.verify(encode(old), encode(new))

    def test_reject_missing_safety_node(self):
        old, new = fixture()
        del new['/thermal-zones/cpu-critical']
        with self.assertRaises(AssertionError):
            check.verify(encode(old), encode(new))

    def test_reject_referenced_trip(self):
        old, new = fixture()
        old['/thermal-zones/cpuss0-thermal/trips/trip-point2']['phandle'] = struct.pack('>I', 99)
        with self.assertRaises(AssertionError):
            check.verify(encode(old), encode(new))

    def test_reject_missing_removal(self):
        old, new = fixture()
        path = '/thermal-zones/cpuss0-thermal/trips/trip-point2'
        new[path] = old[path]
        with self.assertRaises(AssertionError):
            check.verify(encode(old), encode(new))


if __name__ == '__main__':
    unittest.main()

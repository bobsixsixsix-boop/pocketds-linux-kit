#!/usr/bin/env python3
"""Run production cache readers and QML state updates against isolated faults."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'components/control-panel/pocketds-panelctl.cpp'
QML = (ROOT / 'components/control-panel/plasmoid/contents/ui/main.qml').read_text()
QUOTA = {
    'available': True, 'fresh': True, 'source': 'app-server',
    'synced_at': int(time.time()), 'source_timestamp': int(time.time()),
    'limit_id': 'codex', 'limit_name': 'Codex', 'plan_type': 'pro',
    'primary_used_percent': 25, 'primary_reset': 1800000000,
    'primary_window_minutes': 300, 'secondary_used_percent': None,
    'secondary_reset': None, 'secondary_window_minutes': None,
}


class CacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix='pds-panel-cache-')
        cls.base = Path(cls.workspace.name)
        cls.binary = cls.base / 'panelctl'
        driver = cls.base / 'driver.cpp'
        driver.write_text('#define main panelctl_main\n#include ' + json.dumps(str(SOURCE)) + r'''
#undef main
int main(int argc, char **argv) {
    if (argc == 3 && std::string(argv[1]) == "fan") {
        const auto fan = fan_telemetry(argv[2], getuid());
        std::cout << "{\"fan_status\":\"" << fan.status
          << "\",\"fan_profile\":\"" << fan.profile
          << "\",\"fan_percent\":" << json_number_or_null(fan.percent)
          << ",\"temp_c\":" << json_number_or_null(fan.temperature_c)
          << ",\"fan_sample_age_ms\":" << (fan.age_ms ? std::to_string(*fan.age_ms) : "null") << "}\n";
        return 0;
    }
    if (argc == 3 && std::string(argv[1]) == "quota") {
        std::cout << quota_json(read_private_runtime_text(argv[2])) << '\n';
        return 0;
    }
    return panelctl_main(argc, argv);
}
''')
        command = ['c++', '-std=c++17', '-Wall', '-Wextra', '-O1']
        if sys.platform == 'darwin':
            command.append('-Dst_mtim=st_mtimespec')
        subprocess.run(command + [str(driver), '-o', str(cls.binary)], check=True, timeout=45)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=self.base)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cache = self.root / 'cache'

    def call(self, mode, path=None, env=None):
        args = [str(self.binary), mode]
        if path is not None:
            args.append(str(path))
        result = subprocess.run(args, env=env, capture_output=True, text=True, check=True, timeout=3)
        return json.loads(result.stdout)

    def write(self, content, mode=0o600):
        self.cache.write_bytes(content.encode() if isinstance(content, str) else content)
        self.cache.chmod(mode)

    def assert_no_fan_value(self, expected):
        result = self.call('fan', self.cache)
        self.assertEqual(result['fan_status'], expected)
        self.assertIsNone(result['fan_percent'])
        self.assertIsNone(result['temp_c'])
        self.assertEqual(result['fan_profile'], 'unavailable')

    def test_fresh_fan_and_real_zero_are_available(self):
        for pwm, temperature, profile in ((51, 41, 'moderate'), (0, 0, 'off'), (255, 110, 'aggressive')):
            self.write(f'profile={profile}\ntemp_c={temperature}\npwm={pwm}\n')
            result = self.call('fan', self.cache)
            self.assertEqual(result['fan_status'], 'ok')
            self.assertEqual(result['fan_percent'], round(pwm / 255 * 100))
            self.assertEqual(result['temp_c'], temperature)
            self.assertLess(result['fan_sample_age_ms'], 1000)

    def test_missing_old_and_future_fan_never_claim_zero_or_fresh(self):
        self.assert_no_fan_value('unavailable')
        for offset in (-86400, -8, 1, 10):
            self.write('profile=moderate\ntemp_c=41\npwm=51\n')
            timestamp = time.time() + offset
            os.utime(self.cache, (timestamp, timestamp))
            self.assert_no_fan_value('stale')

    def test_bad_fan_fields_and_untrusted_file_are_isolated(self):
        for content in ('', 'profile=moderate\ntemp_c=41\n',
                        'profile=moderate\ntemp_c=41\npwm=51junk\n',
                        'profile=moderate\ntemp_c=41\npwm=256\n',
                        'profile=moderate\ntemp_c=151\npwm=51\n',
                        'profile=moderate\ntemp_c=41\npwm=51\npwm=0\n',
                        'x' * 4097):
            with self.subTest(content=content[:80]):
                self.write(content)
                self.assert_no_fan_value('invalid')
        self.write('profile=moderate\ntemp_c=41\npwm=51\n', 0o666)
        self.assert_no_fan_value('invalid')

    def test_quota_valid_unicode_escapes_and_explicit_unavailable(self):
        for ensure_ascii in (True, False):
            value = dict(QUOTA, limit_name='额度 🎮 / "测试" \\')
            self.write(json.dumps(value, ensure_ascii=ensure_ascii))
            self.assertEqual(self.call('quota', self.cache), value)
        value = dict(QUOTA, available=False, fresh=False, source='unavailable',
                     source_timestamp=None, primary_used_percent=None,
                     primary_reset=None, primary_window_minutes=None)
        self.write(json.dumps(value))
        self.assertEqual(self.call('quota', self.cache), value)

    def test_quota_rejects_invalid_syntax_duplicate_keys_and_utf8(self):
        valid = json.dumps(QUOTA)
        cases = [b'', b'{"available":true,', b'{', b'[]', b'null',
                 valid[:-1] + ',}', valid + '{}', '\ufeff' + valid,
                 valid.replace('"available": true', '"available": true, "available": false'),
                 valid.replace('"available": true', '"available": true, "\\u0061vailable": false'),
                 valid.replace('"primary_used_percent": 25', '"primary_used_percent": 025'),
                 valid.replace('"primary_used_percent": 25', '"primary_used_percent": +25'),
                 valid.replace('"primary_used_percent": 25', '"primary_used_percent": NaN'),
                 valid.replace('"primary_used_percent": 25', '"primary_used_percent": 1e9999'),
                 valid.replace('"Codex"', '"\\ud800"'),
                 valid.replace('"Codex"', '"\\udc00"'),
                 valid.replace('"Codex"', '"\\u0000"'),
                 valid.encode().replace(b'"Codex"', b'"\xc0\xaf"'),
                 valid.encode().replace(b'"Codex"', b'"\xed\xa0\x80"'),
                 valid.encode().replace(b'"Codex"', b'"\xf4\x90\x80\x80"'),
                 valid.replace('"Codex"', '{"nested":true}'),
                 ' ' * 65536 + valid]
        for content in cases:
            with self.subTest(content=str(content)[:110]):
                self.write(content)
                self.assertIsNone(self.call('quota', self.cache))

    def test_quota_schema_mismatch_does_not_emit_unvalidated_fields(self):
        cases = [dict(QUOTA, available='true'), dict(QUOTA, fresh=1),
                 dict(QUOTA, primary_used_percent=101), dict(QUOTA, primary_used_percent=True),
                 dict(QUOTA, primary_used_percent=None), dict(QUOTA, primary_reset=-1),
                 dict(QUOTA, synced_at=0), dict(QUOTA, primary_window_minutes=0),
                 dict(QUOTA, private_extra='do not emit')]
        missing = dict(QUOTA)
        del missing['secondary_used_percent']
        cases.append(missing)
        for value in cases:
            self.write(json.dumps(value))
            self.assertIsNone(self.call('quota', self.cache))

    def test_fifo_symlink_hardlink_and_public_quota_do_not_block(self):
        self.write(json.dumps(QUOTA), 0o644)
        self.assertIsNone(self.call('quota', self.cache))
        self.cache.unlink()
        target = self.root / 'target'
        target.write_text(json.dumps(QUOTA))
        target.chmod(0o600)
        self.cache.symlink_to(target)
        self.assertIsNone(self.call('quota', self.cache))
        self.assert_no_fan_value('invalid')
        self.cache.unlink()
        os.link(target, self.cache)
        self.assertIsNone(self.call('quota', self.cache))
        self.assert_no_fan_value('invalid')
        self.cache.unlink()
        os.mkfifo(self.cache, 0o600)
        self.assertIsNone(self.call('quota', self.cache))
        self.assert_no_fan_value('invalid')

    def test_bad_quota_keeps_complete_helper_status_json_valid(self):
        runtime = self.root / 'run'
        runtime.mkdir(mode=0o700)
        tools = self.root / 'bin'
        tools.mkdir()
        wpctl = tools / 'wpctl'
        wpctl.write_text('#!/bin/sh\nprintf "Volume: 0.42\\n"\n')
        wpctl.chmod(0o700)
        quota = runtime / 'pocketds-codex-quota.json'
        quota.write_text('{"available":true,')
        quota.chmod(0o600)
        result = self.call('status', env=dict(os.environ, XDG_RUNTIME_DIR=str(runtime),
            PATH=str(tools) + os.pathsep + os.environ.get('PATH', ''),
            POCKETDS_POWER_SUPPLY_ROOT=str(self.root / 'missing-supplies')))
        self.assertIsNone(result['quota'])
        self.assertEqual(result['volume'], 42)
        self.assertIn('cpu_total', result)
        self.assertIn('battery_state', result)
        self.assertIn('fan_status', result)

    @unittest.skipUnless(shutil.which('node'), 'Node.js needed for production QML functions')
    def test_qml_isolates_fan_and_quota_and_expires_sample_between_replies(self):
        functions = QML[QML.index('    function clamp('):QML.index('    P5Support.DataSource {')]
        fan_binding = re.search(r'readonly property bool fanHealthy:\s*(.*?)\n    readonly property', QML, re.S)[1]
        script = '''const assert = require('assert');
var previousCpuTotal=0, previousCpuIdle=0, previousRx=0, previousTx=0, previousSampleMs=0;
var pendingActions={}, quotaMaximumAgeS=360;
''' + functions + '''
const sample={cpu_total:100,cpu_idle:60,cpu_ghz:2,cpu_cores:8,rx_bytes:0,tx_bytes:0,
  volume:42,muted:false,top_brightness:50,bottom_brightness:60,brightness_write_status:'ok',
  power_profile:'balanced',quota:null,fan_status:'unavailable',fan_sample_age_ms:null,
  fan_percent:null,fan_profile:'unavailable',temp_c:null};
assert(applyStatus(sample));
assert(statusReady && statusSchemaValid && volumeTelemetryValid && topBrightnessValid && powerTelemetryValid);
assert.strictEqual(temperature,-1); assert.strictEqual(fanPercent,-1);
var telemetryHealthy=true;
const healthy=()=>(''' + fan_binding + ''');
assert(!healthy());
assert(applyStatus({...sample,fan_status:'ok',fan_sample_age_ms:6500,fan_percent:20,fan_profile:'moderate',temp_c:41}));
assert(healthy()); telemetryAgeMs=501; assert(!healthy());
assert(applyStatus({...sample,fan_status:'stale',fan_sample_age_ms:10000})); assert(!healthy());
assert.strictEqual(temperature,-1); assert(volumeTelemetryValid && powerTelemetryValid);
assert(applyStatus({...sample,fan_status:'ok',fan_sample_age_ms:0,fan_percent:20,fan_profile:'moderate',temp_c:'broken'}));
assert(!healthy()); assert(volumeTelemetryValid && powerTelemetryValid);
console.log('production QML isolates missing/stale/invalid fan and quota; fan expires between replies');
'''
        subprocess.run(['node', '-e', script], check=True, text=True, capture_output=True, timeout=5)


if __name__ == '__main__':
    unittest.main()

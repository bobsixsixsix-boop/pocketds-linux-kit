#!/usr/bin/python3
"""Second, separately coordinated v4 cycle; prepare only until user closes lid.

Keep the first receipt intact. Reuse every existing safety gate and lifecycle;
change only the fixed one-use output, expected count and RTC rescue duration.
This never declares Hall wake successful; operator must inspect wake IRQ,
actual sleep duration, lid events, screens and the user's physical report.
"""
import importlib.util
import json
from pathlib import Path


def prepare():
    source = Path(__file__).with_name('attended-dsc-lid-test.py')
    spec = importlib.util.spec_from_file_location('attended_dsc', source)
    test = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(test)
    # Check immutable prior receipts as trusted root files. This records a
    # particular successful predecessor, not authority to run an open-ended loop.
    verifier = test.load_verifier()
    before = json.loads(verifier.root_file(Path('/run/pds001-v4-lid-1/before.json')))
    result = json.loads(verifier.root_file(Path('/run/pds001-v4-lid-1/result.json')))
    test.require(before['state']['boot_id'] == test.BOOT, 'predecessor boot differs')
    test.require(before['state']['counters'] == {'success': 0, 'fail': 0},
                 'unexpected predecessor start counters')
    test.require(result['kernel_cycle'] == 'returned'
                 and result['counters'] == {'success': 1, 'fail': 0}
                 and result['suspended_seconds'] == 119.153,
                 'predecessor is not the recorded accepted cycle')
    test.OUT = Path('/run/pds001-v4-lid-2')
    test.EXPECTED_SUCCESS = 1
    test.RTC_SECONDS = 180
    return test


if __name__ == '__main__':
    prepare().main()

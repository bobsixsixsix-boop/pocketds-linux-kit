#!/usr/bin/python3
"""One explicitly attended v4 RAM-boot lid cycle; no permanent sleep enablement.

Run as root under a transient system service. `check` is read-only; `run`
requires a physically closed lid and records evidence only in /run. A 120 s
RTC rescue belongs solely to this test. Normal reboot returns installed v3.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import time

REPO = Path('/home/pocketds/pocketds-linux-kit-final')
POLICY = Path('/etc/systemd/sleep.conf.d/80-pocketds-sleep.conf')
GUARD = Path('/usr/local/libexec/pocketds-deep-suspend')
OUT = Path('/run/pds001-v4-lid-1')
BOOT = '72a9286e-7eae-46cf-966a-6afc7d2102be'
NOTES = '05ffb67550d15176df5302978a51cf1834fa70869e663f8e01c440c253116092'
POLICY_SHA = 'd3caafee10b81d3718f13fb076b1703a294a3490c1650771e9ee6f33d1b97211'
GUARD_SHA = '68877ca5eb98bde3ebe5f4d1e01953b2e732965619cd0da75c97058dfcdcf36f'
EXPECTED_SUCCESS = 0
RTC_SECONDS = 120


def call(*args):
    return subprocess.check_output(args, text=True, timeout=10).strip()


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clocks():
    return (time.clock_gettime(time.CLOCK_BOOTTIME), time.monotonic())


def lid_closed():
    return call('busctl', 'get-property', 'org.freedesktop.login1',
                '/org/freedesktop/login1', 'org.freedesktop.login1.Manager',
                'LidClosed') == 'b true'


def load_verifier():
    # Reuse all existing v3 storage/thermal/wake checks. The ONLY substituted
    # identity is this RAM candidate's notes, inside this process. Production
    # files, eligibility and the original installed-image lock stay unchanged.
    spec = importlib.util.spec_from_file_location(
        'daily_checks', REPO / 'components/system/pocketds-daily-suspend.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.NOTES = NOTES
    return module


def check(verifier):
    state = verifier.deployment_ready()
    require(state['boot_id'] == BOOT, 'not the authorized RAM boot')
    require(state['counters'] == {'success': EXPECTED_SUCCESS, 'fail': 0},
            'unexpected prior cycle count')
    require(not os.path.lexists(verifier.OPT_IN), 'daily opt-in must remain absent')
    require(not os.path.lexists(OUT), 'test evidence already exists; no repeat')
    require(not POLICY.is_symlink(), 'policy cannot be a symlink')
    require(subprocess.run(['mountpoint', '-q', str(POLICY)]).returncode == 32,
            'policy already mounted or mountpoint check failed')
    require(digest(POLICY) == POLICY_SHA, 'baseline policy changed')
    require(digest(GUARD) == GUARD_SHA, 'attended guard changed')
    require(json.loads(call(str(GUARD), 'check'))['safely_blocked'],
            'daily sleep is not safely blocked')
    return state


def candidate_check(*, closed):
    """Optional read-only gate for a separately reviewed one-use followup."""
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'run'))
    args = parser.parse_args()
    require(os.geteuid() == 0, 'root required')
    verifier = load_verifier()
    state = check(verifier)
    candidate = candidate_check(closed=False)
    print(json.dumps({'preflight': state, 'lid_closed': lid_closed()}), flush=True)
    if args.command == 'check':
        return
    require(lid_closed(), 'close physical lid before this single cycle')
    os.umask(0o077)
    OUT.mkdir(mode=0o700)
    mounted = False
    result = 'incomplete'
    def interrupted(_signum, _frame):
        raise RuntimeError('test interrupted; restore baseline policy')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        policy = POLICY.read_bytes()
        require(policy.count(b'AllowSuspend=no') == 1, 'unexpected policy structure')
        (OUT / 'policy-before.conf').write_bytes(policy)
        override = OUT / 'attended.conf'
        override.write_bytes(policy.replace(b'AllowSuspend=no', b'AllowSuspend=yes'))
        override.chmod(0o644)
        (OUT / 'dmesg-before.txt').write_text(call('dmesg'))
        before = clocks()
        (OUT / 'before.json').write_text(json.dumps(
            {'state': state, 'clocks': before, 'candidate': candidate}))
        call('mount', '--bind', str(override), str(POLICY))
        mounted = True
        require(json.loads(call(str(GUARD), 'check'))['execution_ready'],
                'temporary root dispatch not ready')
        dispatch_candidate = candidate_check(closed=True)
        (OUT / 'candidate-dispatch.json').write_text(json.dumps(dispatch_candidate))
        require(lid_closed(), 'lid opened before dispatch; refused')
        print(f'START: one closed-lid deep cycle, RTC rescue {RTC_SECONDS} seconds', flush=True)
        # Existing guard owns delay inhibitor, RTC and PrepareForSleep true/false.
        # No inhibitor bypass; no debug sleep or production policy replacement.
        completed = subprocess.run([str(GUARD), 'run', str(RTC_SECONDS)],
                                   timeout=RTC_SECONDS + 55)
        after = clocks()
        kernel = call('dmesg')
        (OUT / 'dmesg-after.txt').write_text(kernel)
        require(not verifier.STORAGE_ERROR.search(kernel),
                'storage error: stop persistent filesystem probes')
        require(completed.returncode == 0, 'guard did not complete its lifecycle')
        require(verifier.value('/proc/sys/kernel/random/boot_id') == BOOT, 'boot changed')
        require(verifier.counters() == {'success': EXPECTED_SUCCESS + 1, 'fail': 0},
                'unexpected counters')
        require(not verifier.value('/sys/class/rtc/rtc0/wakealarm'), 'RTC alarm remains')
        result = {'kernel_cycle': 'returned', 'clocks': after,
                  'suspended_seconds': round((after[0] - before[0]) - (after[1] - before[1]), 3),
                  'counters': verifier.counters(), 'physical_display_acceptance': 'pending'}
        print(json.dumps(result), flush=True)
    finally:
        if mounted:
            call('umount', str(POLICY))
        # Volatile receipts only. Do not clear an unobserved-cycle RTC alarm.
        (OUT / 'result.json').write_text(json.dumps(result))
        print('Temporary policy removed; automatic deep sleep remains disabled', flush=True)


if __name__ == '__main__':
    main()

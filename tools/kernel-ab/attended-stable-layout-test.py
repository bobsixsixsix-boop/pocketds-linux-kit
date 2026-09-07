#!/usr/bin/python3
"""Third one-use v4 cycle: retained dual layout, not a daily sleep deployment.

This only observes the already-existing paired KWin layouts. It never changes
outputs, DPMS, config, helper, installed kernel or production sleep ownership.
The previous Hall cycle FAILED physical display acceptance. Its kernel-return
receipt identifies the start point only; it is not acceptance of that cycle.
"""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess

CONFIG = Path('/home/pocketds/.config/kwinoutputconfig.json')
CONFIG_SHA = '0bcd7440ea1374cf3de780a309039b4183fcf91402cc71d7b6bd4f7118735c74'
HELPER = Path('/home/pocketds/.local/libexec/pocketds/pocketds-light-standby')
HELPER_SHA = '244bdf838c3fbcc53816369cff0fec09cc732614aa766c49d3151d4f0e0af0bb'
BACKLIGHT = Path('/sys/class/backlight')
EXPECTED = {
    'DSI-1': (1080, 1920, 120.0, 1.5, {'x': 0, 'y': 0}, 1),
    'DSI-2': (768, 1024, 59.999, 1.25, {'x': 283, 'y': 720}, 2),
}


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def pinned_user_file(path, expected):
    # Never execute these user files as root; constrain identity and read only.
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 1000
                and not info.st_mode & 0o022 and 0 < info.st_size <= 262144,
                'untrusted candidate file metadata')
        data = stream.read(262145)
    require(len(data) <= 262144 and hashlib.sha256(data).hexdigest() == expected,
            'candidate file changed; do not repair or override it automatically')


def screen_query(*args):
    reply = subprocess.check_output([
        '/usr/bin/runuser', '-u', 'pocketds', '--', '/usr/bin/env',
        'WAYLAND_DISPLAY=wayland-0', 'XDG_RUNTIME_DIR=/run/user/1000',
        'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus',
        'QT_QPA_PLATFORM=wayland', 'QT_ACCESSIBILITY=0',
        'QT_LINUX_ACCESSIBILITY_ALWAYS_ON=0', '/usr/bin/kscreen-doctor', *args,
    ], text=True, timeout=10)
    require(len(reply.encode()) <= 131072, 'oversized screen query reply')
    return reply


def validate_outputs(snapshot):
    outputs = snapshot['outputs']
    require(len(outputs) == 2 and {o['name'] for o in outputs} == set(EXPECTED),
            'not exactly the two internal outputs')
    for output in outputs:
        width, height, rate, scale, position, priority = EXPECTED[output['name']]
        require(output['connected'] is True and output['enabled'] is True
                and type(output['type']) is int and output['type'] == 7
                and output['clones'] == []
                and type(output['replicationSource']) is int
                and output['replicationSource'] == 0,
                'disabled, external or mirrored output')
        require(type(output['currentModeId']) is str, 'non-string active mode ID')
        modes = [m for m in output['modes'] if m['id'] == output['currentModeId']]
        require(len(modes) == 1, 'ambiguous or absent active mode')
        mode = modes[0]
        require(all(type(mode['size'][key]) is int for key in ('width', 'height'))
                and mode['size'] == {'width': width, 'height': height}
                and type(mode['refreshRate']) in (int, float)
                and math.isfinite(mode['refreshRate'])
                and abs(mode['refreshRate'] - rate) < 0.002
                and type(output['scale']) in (int, float) and output['scale'] == scale
                and type(output['rotation']) is int and output['rotation'] == 2
                and all(type(output['pos'][key]) is int for key in ('x', 'y'))
                and output['pos'] == position
                and type(output['priority']) is int and output['priority'] == priority,
                'mode or layout differs from the ordinary-lid accepted candidate')


def candidate_check(*, closed):
    pinned_user_file(CONFIG, CONFIG_SHA)
    pinned_user_file(HELPER, HELPER_SHA)
    snapshot = json.loads(screen_query('-j'))
    validate_outputs(snapshot)
    if closed:
        states = []
        for line in screen_query('--dpms', 'show').splitlines():
            match = re.fullmatch(r'dpms mode for screen (DSI-[12]): (on|off)', line.strip())
            require(match is not None, 'unexpected DPMS reply')
            states.append(match.groups())
        require(sorted(states) == [('DSI-1', 'off'), ('DSI-2', 'off')],
                'both displays must already be off before suspend')
        for name in ('ae94000.dsi.0', 'sy7758-backlight'):
            require((BACKLIGHT / name / 'bl_power').read_text().strip() == '4',
                    'backlight is not blanked')
        for field in ('brightness', 'actual_brightness'):
            require((BACKLIGHT / 'sy7758-backlight' / field).read_text().strip() == '0',
                    'lower LCD backlight must be physically zero before suspend')
    # Recheck after the live queries; no live JSON edit/reload is attempted.
    pinned_user_file(CONFIG, CONFIG_SHA)
    return {'kind': 'retained-dual-layout', 'config_sha256': CONFIG_SHA,
            'helper_sha256': HELPER_SHA, 'closed_display_off_checked': closed,
            'outputs': snapshot, 'prior_hall_physical_acceptance': 'FAILED'}


def prepare():
    source = Path(__file__).with_name('attended-dsc-lid-test.py')
    spec = importlib.util.spec_from_file_location('attended_stable_base', source)
    test = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(test)
    verifier = test.load_verifier()
    # Check volatile kernel health BEFORE any persistent candidate probes.
    verifier.storage_health()
    before = json.loads(verifier.root_file(Path('/run/pds001-v4-lid-2/before.json')))
    result = json.loads(verifier.root_file(Path('/run/pds001-v4-lid-2/result.json')))
    require(before['state']['boot_id'] == test.BOOT
            and before['state']['counters'] == {'success': 1, 'fail': 0},
            'unexpected second-cycle starting receipt')
    require(result['kernel_cycle'] == 'returned'
            and result['counters'] == {'success': 2, 'fail': 0}
            and result['suspended_seconds'] == 128.367,
            'not the recorded second-cycle kernel return (physical acceptance failed)')
    test.OUT = Path('/run/pds001-v4-lid-3')
    test.EXPECTED_SUCCESS = 2
    test.RTC_SECONDS = 180
    test.candidate_check = candidate_check
    return test


if __name__ == '__main__':
    prepare().main()

#!/usr/bin/python3
"""Opt-in DSC-v4 gate for native KDE/logind deep sleep; never requests sleep."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import time

RELEASE = '7.1.12-pdsdiag.20260905.aarch64'
NOTES = '05ffb67550d15176df5302978a51cf1834fa70869e663f8e01c440c253116092'
IMAGE = '57322cb6dc3bce822289bcd6bde5e5934362efc2547120ebc88be0eea7a89d92'
THERMAL = '922d6bea11837b70b162d53a50a0bc2153adf3dd412421ba5bdf91f8b70fc68f'
LID_QUIRK = Path('/usr/share/libinput/99-pocketds-lid.quirks')
LID_QUIRK_SHA = '552cb3a61fdd2505cbd8d60acbb58d263a793b1f52c334fc3108d28821b938ad'
DPMS_PLUGIN = Path('/usr/lib64/qt6/plugins/powerdevil/action/powerdevil_dpmsaction.so')
# Each pair was checked as a whole package against this exact QtCore binary.
# The two cross-pairs fail QML ABI checks; a version label is not acceptance.
POWERDEVIL_RUNTIME_PAIRS = (
    {'name': 'published-alpha3-qt6112-pocketds1',
     'dpms_sha256': '49358da688b5c2a661af11c3a2f3533a68973584d9a8bb2c4dfe7c2a0a713744',
     'qtcore_path': '/usr/lib64/libQt6Core.so.6.11.2', 'qtcore_size': 7358544,
     'qtcore_sha256': '945beb4bb99aad4ce333ee6d406ad72eec913f87e34a0b86c81f36775a2fcb17'},
    {'name': 'daily-qt6111-pocketds2',
     'dpms_sha256': 'fe6fe3635c5d3239ca3044806185eef56f04f69d6b43e6abcfbc4a4da8a2b48a',
     'qtcore_path': '/usr/lib64/libQt6Core.so.6.11.1', 'qtcore_size': 7423408,
     'qtcore_sha256': '2a7e86dbcbf0bd63585d64c8b43347e6e55a32f404b29d16a260c3a9a2f4f403'},
)
QTCORE_ALIAS = Path('/usr/lib64/libQt6Core.so.6')
QTCORE_MAX = 16 * 1024 * 1024
DPMS_PLUGIN_MAX = 2 * 1024 * 1024
PROC = Path('/proc')
POWERDEVIL_BUS_NAME = 'org.kde.Solid.PowerManagement'
INPUT_DIR = Path('/sys/class/input')
OPT_IN = Path('/etc/pocketds-linux-kit/daily-suspend.enabled')
TOKEN = b'POCKETDS-V4-DAILY-DEEP-20260907\n'
RUNTIME = Path('/run/pocketds-daily-suspend')
BLOCK = RUNTIME / 'blocked.json'
STATE = RUNTIME / 'pending.json'
STATS = Path('/sys/power/suspend_stats')
STORAGE_ERROR = re.compile(r'I/O error|Buffer I/O|EXT4-fs error|ufshcd.*(?:error|fail)|ufs.*(?:abort|fatal)', re.I)
POWER_WAKE = Path('/sys/devices/platform/soc@0/c400000.spmi/spmi-0/0-00/c400000.spmi:pmic@0:pon@1300/c400000.spmi:pmic@0:pon@1300:pwrkey/power/wakeup')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def value(path):
    return Path(path).read_text().strip('\0\n ')


def root_file(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
            and not info.st_mode & 0o022, 'untrusted root file')
    return path.read_bytes()


def runtime_identity():
    require(value('/proc/device-tree/model') == 'AYANEO Pocket DS', 'wrong device')
    require(os.uname().release == RELEASE, 'kernel release is not DSC-v4')
    require(sha(Path('/sys/kernel/notes').read_bytes()) == NOTES, 'kernel notes differ')
    return value('/proc/sys/kernel/random/boot_id')


def eligibility_policy():
    runtime_identity()
    require(root_file(OPT_IN) == TOKEN, 'daily deep sleep is not opted in')
    require(not os.path.lexists(BLOCK), 'another suspend is blocked after a failed cycle')


def eligible():
    eligibility_policy()
    # Also runs unprivileged from polkit and Panel. No process inspection,
    # service activation, or expensive full hardware check belongs here.
    powerdevil_artifact()


def file_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_regular_bounded(path, maximum, root_owned=False):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= maximum,
                'verification input is not a bounded regular file')
        if root_owned:
            require(before.st_uid == 0 and before.st_nlink == 1 and not before.st_mode & 0o022,
                    'untrusted PowerDevil plugin')
        chunks, length = [], 0
        while True:
            chunk = os.read(fd, min(65536, maximum + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            require(length <= maximum, 'verification input exceeds size limit')
        require(file_identity(before) == file_identity(os.fstat(fd)) == file_identity(path.lstat()),
                'verification file changed while reading')
        return b''.join(chunks), before
    finally:
        os.close(fd)


def powerdevil_artifact():
    require(isinstance(POWERDEVIL_RUNTIME_PAIRS, (tuple, list)) and POWERDEVIL_RUNTIME_PAIRS,
            'patched PowerDevil runtime pairs are not pinned')
    for pair in POWERDEVIL_RUNTIME_PAIRS:
        require(isinstance(pair, dict) and isinstance(pair.get('name'), str) and pair['name']
                and all(isinstance(pair.get(key), str)
                and re.fullmatch(r'[0-9a-f]{64}', pair[key])
                for key in ('dpms_sha256', 'qtcore_sha256'))
                and isinstance(pair.get('qtcore_path'), str)
                and Path(pair['qtcore_path']).is_absolute()
                and type(pair.get('qtcore_size')) is int and 0 < pair['qtcore_size'] <= QTCORE_MAX,
                'patched PowerDevil runtime pair is not pinned')
    data, info = read_regular_bounded(DPMS_PLUGIN, DPMS_PLUGIN_MAX, root_owned=True)
    checksum = sha(data)
    selected = [pair for pair in POWERDEVIL_RUNTIME_PAIRS if pair['dpms_sha256'] == checksum]
    require(selected, 'PowerDevil resume plugin differs from verified artifacts')
    alias_info = QTCORE_ALIAS.lstat()
    require(alias_info.st_uid == 0 and stat.S_ISLNK(alias_info.st_mode), 'untrusted QtCore loader alias')
    qt_path = QTCORE_ALIAS.resolve(strict=True)
    selected = [pair for pair in selected if pair['qtcore_path'] == str(qt_path)]
    require(len(selected) == 1, 'PowerDevil/QtCore runtime pair is not verified')
    pair = selected[0]
    qt_data, qt_info = read_regular_bounded(qt_path, QTCORE_MAX, root_owned=True)
    require(len(qt_data) == pair['qtcore_size'] and sha(qt_data) == pair['qtcore_sha256'],
            'QtCore runtime differs from the verified PowerDevil pair')
    require(file_identity(QTCORE_ALIAS.lstat()) == file_identity(alias_info)
            and QTCORE_ALIAS.resolve(strict=True) == qt_path,
            'QtCore loader alias changed during verification')
    require(file_identity(DPMS_PLUGIN.lstat()) == file_identity(info),
            'PowerDevil plugin was replaced during runtime-pair verification')
    return {'sha256': checksum, 'identity': file_identity(info),
            'device': (os.major(info.st_dev), os.minor(info.st_dev)), 'inode': info.st_ino,
            'runtime_pair': pair['name'],
            'qtcore': {'path': str(qt_path), 'sha256': pair['qtcore_sha256'],
                       'identity': file_identity(qt_info), 'alias_identity': file_identity(alias_info),
                       'device': (os.major(qt_info.st_dev), os.minor(qt_info.st_dev)),
                       'inode': qt_info.st_ino}}


def powerdevil_bus(uid, method, name, signature):
    raw = subprocess.check_output([
        '/usr/sbin/runuser', '-u', 'pocketds', '--', '/usr/bin/busctl',
        f'--address=unix:path=/run/user/{uid}/bus', '--auto-start=no',
        '--timeout=1s', '--json=short', 'call', 'org.freedesktop.DBus',
        '/org/freedesktop/DBus', 'org.freedesktop.DBus', method, 's', name,
    ], text=True, timeout=1.5)
    require(len(raw) <= 16384, 'unexpected PowerDevil owner reply size')
    reply = json.loads(raw)
    require(isinstance(reply, dict) and reply.get('type') == signature
            and isinstance(reply.get('data'), list) and len(reply['data']) == 1,
            'unexpected PowerDevil owner reply')
    result = reply['data'][0]
    if signature == 'u':
        require(type(result) is int and 0 < result <= 0xffffffff, 'invalid PowerDevil PID')
    else:
        require(type(result) is str and re.fullmatch(r':\d+\.\d+', result), 'invalid PowerDevil bus owner')
    return result


def process_start(pid):
    data, _ = read_regular_bounded(PROC / str(pid) / 'stat', 16384)
    text = data.decode('utf-8', errors='replace')
    require(text.startswith(f'{pid} (') and ')' in text, 'invalid PowerDevil process identity')
    fields = text.rsplit(')', 1)[1].split()
    require(len(fields) > 19 and fields[19].isdigit(), 'invalid PowerDevil process start time')
    return int(fields[19])  # /proc/PID/stat field 22; comm can contain spaces and ')'.


def powerdevil_resume_ready():
    artifact = powerdevil_artifact()
    uid = pwd.getpwnam('pocketds').pw_uid
    owner = powerdevil_bus(uid, 'GetNameOwner', POWERDEVIL_BUS_NAME, 's')
    pid = powerdevil_bus(uid, 'GetConnectionUnixProcessID', owner, 'u')
    started = process_start(pid)
    status, _ = read_regular_bounded(PROC / str(pid) / 'status', 65536)
    identities = re.findall(rb'^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$', status, re.M)
    require(len(identities) == 1 and all(int(value) == uid for value in identities[0]),
            'PowerDevil process is not owned by the desktop user')
    maps, _ = read_regular_bounded(PROC / str(pid) / 'maps', 4 * 1024 * 1024)
    qtcore = artifact['qtcore']
    for checked_path, checked_artifact, label in (
            (DPMS_PLUGIN, artifact, 'resume plugin'),
            (Path(qtcore['path']), qtcore, 'QtCore runtime')):
        mapped = []
        for line in maps.decode('utf-8', errors='strict').splitlines():
            fields = line.split(maxsplit=5)
            if len(fields) != 6:
                continue
            pathname = fields[5]
            plain_path = pathname.removesuffix(' (deleted)')
            candidate_name = Path(plain_path).name
            relevant = (candidate_name.startswith('libQt6Core.so') if label == 'QtCore runtime'
                        else candidate_name == DPMS_PLUGIN.name)
            if not relevant:
                continue
            require(pathname == str(checked_path), 'PowerDevil loaded a deleted or alternate ' + label)
            device = fields[3].split(':')
            require(len(device) == 2 and all(re.fullmatch(r'[0-9a-fA-F]+', part) for part in device)
                    and fields[4].isdigit() and re.fullmatch(r'[r-][w-][x-][ps]', fields[1]),
                    'malformed PowerDevil ' + label + ' mapping')
            require(tuple(int(part, 16) for part in device) == checked_artifact['device']
                    and int(fields[4]) == checked_artifact['inode'],
                    'PowerDevil still maps a different ' + label)
            mapped.append(fields[1])
        require(any('x' in permissions for permissions in mapped),
                'PowerDevil has not loaded the verified ' + label)
    require(process_start(pid) == started
            and powerdevil_bus(uid, 'GetNameOwner', POWERDEVIL_BUS_NAME, 's') == owner,
            'PowerDevil owner changed during verification')
    require(file_identity(DPMS_PLUGIN.lstat()) == artifact['identity'],
            'PowerDevil plugin was replaced during verification')
    require(file_identity(Path(qtcore['path']).lstat()) == qtcore['identity']
            and file_identity(QTCORE_ALIAS.lstat()) == qtcore['alias_identity']
            and str(QTCORE_ALIAS.resolve(strict=True)) == qtcore['path'],
            'QtCore runtime or loader alias was replaced during verification')
    return {'owner': owner, 'pid': pid, 'sha256': artifact['sha256'],
            'device': artifact['device'], 'inode': artifact['inode'],
            'runtime_pair': artifact['runtime_pair'], 'qtcore_sha256': qtcore['sha256']}



def counters():
    return {name: int(value(STATS / name)) for name in ('success', 'fail')}


def storage_health():
    result = subprocess.run(['/usr/bin/dmesg'], capture_output=True, text=True,
                            check=True, timeout=5)
    matches = [line for line in result.stdout.splitlines() if STORAGE_ERROR.search(line)]
    require(not matches, 'storage error: stop further disk access; collect volatile evidence')


def lid_input():
    # Event numbers change between boots. Resolve the raw kernel Hall device,
    # which still advertises SW_LID even when libinput filters that event.
    matches = []
    for event in INPUT_DIR.glob('event*'):
        if not re.fullmatch(r'event\d+', event.name):
            continue
        device = event / 'device'
        if value(device / 'name') != 'gpio-keys':
            continue
        words = value(device / 'capabilities/sw').split()
        if words and int(words[-1], 16) & 1:  # Linux SW_LID is bit 0.
            matches.append(event.name)
    require(len(matches) == 1, 'expected one raw gpio-keys lid device')
    return matches[0]


def compositor_lid_ready():
    require(sha(root_file(LID_QUIRK)) == LID_QUIRK_SHA,
            'installed compositor lid quirk differs')
    event = lid_input()
    uid = pwd.getpwnam('pocketds').pw_uid
    # Root cannot authenticate directly to this user's dbus-broker. This only
    # reads the existing KWin context; a fresh libinput context would not prove
    # that the desktop loaded the installed quirk. Never activate a new KWin.
    reply = json.loads(subprocess.check_output([
        '/usr/sbin/runuser', '-u', 'pocketds', '--', '/usr/bin/busctl',
        f'--address=unix:path=/run/user/{uid}/bus', '--auto-start=no',
        '--timeout=2s', '--json=short', 'call', 'org.kde.KWin',
        f'/org/kde/KWin/InputDevice/{event}',
        'org.freedesktop.DBus.Properties', 'GetAll', 's',
        'org.kde.KWin.InputDevice',
    ], text=True, timeout=3))
    require(isinstance(reply, dict) and reply.get('type') == 'a{sv}'
            and isinstance(reply.get('data'), list) and len(reply['data']) == 1
            and isinstance(reply['data'][0], dict), 'unexpected KWin input properties')
    properties = reply['data'][0]

    def property_value(name, signature, kind):
        variant = properties.get(name)
        require(isinstance(variant, dict) and variant.get('type') == signature
                and type(variant.get('data')) is kind,
                f'KWin input property {name} is unavailable')
        return variant['data']

    require(property_value('name', 's', str) == 'gpio-keys'
            and property_value('sysName', 's', str) == event,
            'KWin input device does not match raw lid device')
    require(not property_value('lidSwitch', 'b', bool),
            'KWin has not loaded compositor lid filtering; start a new desktop session')
    require(property_value('keyboard', 'b', bool)
            and property_value('enabled', 'b', bool),
            'KWin gpio-keys keyboard must remain enabled')
    return {'device': event, 'lid_filtered': True, 'keyboard': True, 'enabled': True}


def full_check():
    require(os.geteuid() == 0, 'root verification required')
    boot_id = runtime_identity()
    # Check volatile kernel evidence before reading the disk image.
    storage_health()
    input_preflight = compositor_lid_ready()
    powerdevil_preflight = powerdevil_resume_ready()
    require(value('/sys/power/pm_async') == '0', 'device callbacks are not serial')
    require('[deep]' in value('/sys/power/mem_sleep').split(), 'deep sleep not selected')
    require('[none]' in value('/sys/power/pm_test').split(), 'PM debug test still selected')
    require(value('/sys/power/pm_debug_messages') == '0', 'PM debug messages still enabled')
    # no_console_suspend also overrides DRM's skip_vt_switch request. Reject
    # that diagnostic state before fbcon can relight a closed display in PM.
    require(value('/sys/module/printk/parameters/console_suspend') == 'Y',
            'console suspension is disabled; diagnostic VT switching is unsafe')
    require(value(POWER_WAKE) == 'enabled', 'power-button wake is disabled')
    require(value('/sys/devices/platform/gpio-keys/power/wakeup') == 'enabled', 'lid wake is disabled')
    for mountpoint, device, filesystem in (('/', '/dev/sda13', 'ext4'), ('/boot', '/dev/sda12', 'vfat')):
        matches = []
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            left, right = line.split(' - ', 1)
            fields, post = left.split(), right.split()
            if fields[4] == mountpoint:
                matches.append((fields[5], post[0], post[1]))
        require(len(matches) == 1 and matches[0][1:] == (filesystem, device)
                and 'rw' in matches[0][0].split(','), 'unexpected or read-only mount')
    require(sha(root_file(Path('/boot/boot/Image'))) == IMAGE, 'boot image differs from DSC-v4')
    root = Path('/sys/firmware/devicetree/base/thermal-zones')
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob('*') if p.is_file()):
        digest.update(str(path.relative_to(root)).encode() + b'\0' + path.read_bytes() + b'\0')
    require(digest.hexdigest() == THERMAL, 'thermal DT differs from the verified v3 tree')
    return {'boot_id': boot_id, 'release': RELEASE, 'counters': counters(),
            'input_preflight': input_preflight, 'powerdevil_preflight': powerdevil_preflight}


def deployment_ready():
    snapshot = full_check()
    reply = json.loads(subprocess.check_output([
        '/usr/bin/busctl', '--json=short', 'call', 'org.freedesktop.login1',
        '/org/freedesktop/login1', 'org.freedesktop.login1.Manager', 'ListInhibitors',
    ], text=True, timeout=5))
    require(reply['type'] == 'a(ssssuu)' and len(reply['data']) == 1,
            'unexpected inhibitor inventory')
    blockers = [row[1] for row in reply['data'][0]
                if row[3] == 'block' and 'sleep' in row[0].split(':')]
    require(not blockers, 'finish blocking sleep activity before deployment: ' + ', '.join(blockers))
    return snapshot


def effective_policy():
    text = subprocess.check_output(['/usr/bin/systemd-analyze', 'cat-config',
                                    'systemd/sleep.conf'], text=True, timeout=5)
    section, result = '', {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(('#', ';')):
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1]
        elif section == 'Sleep' and '=' in line:
            key, val = line.split('=', 1)
            result[key.strip()] = val.strip()
    required = {'AllowSuspend': 'yes', 'SuspendState': 'mem', 'MemorySleepMode': 'deep',
                'AllowHibernation': 'no', 'AllowHybridSleep': 'no', 'AllowSuspendThenHibernate': 'no'}
    require(all(result.get(k) == v for k, v in required.items()), 'effective deep-only policy drifted')


def clocks():
    return {'boot': time.clock_gettime(time.CLOCK_BOOTTIME), 'awake': time.monotonic()}


def lid_closed():
    reply = json.loads(subprocess.check_output([
        '/usr/bin/busctl', '--timeout=2s', '--json=short', 'get-property',
        'org.freedesktop.login1', '/org/freedesktop/login1',
        'org.freedesktop.login1.Manager', 'LidClosed',
    ], text=True, timeout=3))
    require(reply.get('type') == 'b' and type(reply.get('data')) is bool,
            'lid status is unavailable')
    return reply['data']


def closed_display_ready():
    # systemd invokes this after logind's delay inhibitors have released.
    # The user display coordinator must have finished physical blanking; this
    # root precheck only verifies it and never tries to repair the display.
    if not lid_closed():
        return {'lid_closed': False}
    base = Path('/sys/class/backlight')
    observed = {
        'top_power': value(base / 'ae94000.dsi.0/bl_power'),
        'bottom_power': value(base / 'sy7758-backlight/bl_power'),
        'bottom_brightness': value(base / 'sy7758-backlight/brightness'),
        'bottom_actual': value(base / 'sy7758-backlight/actual_brightness'),
    }
    require(observed == {'top_power': '4', 'bottom_power': '4',
                         'bottom_brightness': '0', 'bottom_actual': '0'},
            'closed displays are not physically blanked')
    require(lid_closed(), 'lid reopened during sleep preparation')
    return {'lid_closed': True, **observed}


def save_runtime(path, payload):
    RUNTIME.mkdir(mode=0o755, exist_ok=True)
    info = RUNTIME.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
            'unsafe runtime directory')
    descriptor, temporary = tempfile.mkstemp(prefix='.pending-', dir=RUNTIME)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def pre():
    # The full root check below verifies storage health before hashing the
    # plugin. Avoid duplicating eligible()'s unprivileged artifact read here.
    eligibility_policy()
    require(not os.path.lexists(STATE), 'previous sleep transaction is incomplete')
    snapshot = full_check()
    effective_policy()
    require(snapshot['counters']['fail'] == 0, 'this boot already has a failed suspend')
    snapshot['display_preflight'] = closed_display_ready()
    snapshot['clocks'] = clocks()
    save_runtime(STATE, snapshot)
    return snapshot


def post():
    require(os.geteuid() == 0, 'root verification required')
    if not STATE.exists():
        return {'resume': 'no prepared transaction'}
    before = json.loads(root_file(STATE))
    try:
        # On an I/O fault, do not probe /boot or make persistent config writes.
        storage_health()
        require(value('/proc/sys/kernel/random/boot_id') == before['boot_id'], 'boot changed')
        after = counters()
        require(os.environ.get('SERVICE_RESULT') == 'success', 'systemd-sleep failed')
        require(after['success'] == before['counters']['success'] + 1
                and after['fail'] == before['counters']['fail'], 'suspend counters did not pass')
    except Exception as error:
        save_runtime(BLOCK, {'boot_id': before['boot_id'], 'reason': str(error)})
        raise
    STATE.unlink()
    now = clocks()
    seconds = (now['boot'] - before['clocks']['boot']) - (now['awake'] - before['clocks']['awake'])
    result = {'boot_id': before['boot_id'], 'resume': 'passed', 'counters': after,
              'suspended_seconds': round(seconds, 3)}
    save_runtime(RUNTIME / 'last-resume.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'verify', 'verify-deployment', 'pre', 'post'))
    args = parser.parse_args()
    try:
        if args.command == 'check':
            eligible()
            result = {'eligible': True}
        elif args.command == 'verify':
            result = full_check()
        elif args.command == 'verify-deployment':
            result = deployment_ready()
        elif args.command == 'pre':
            result = pre()
        else:
            result = post()
        print(json.dumps({'event': args.command, **result}, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({'event': args.command, 'eligible': False, 'reason': str(error)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

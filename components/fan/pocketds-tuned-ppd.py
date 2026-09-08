#!/usr/bin/python3 -Es
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pocket DS startup checks around the installed, official TuneD PPD bridge.

The exporter/controller wiring follows TuneD v2.27.0's tuned-ppd.py entry point:
https://github.com/redhat-performance/tuned/blob/v2.27.0/tuned-ppd.py
The D-Bus protocol, authorization and profile control remain in TuneD itself.
"""

import argparse
import configparser
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile


CONFIG = Path('/etc/tuned/ppd.conf')
ACTIVE = Path('/etc/tuned/active_profile')
BASE = Path('/etc/tuned/ppd_base_profile')
PROFILES = {
    'power-saver': 'pocketds-powersave',
    'balanced': 'pocketds-balanced',
    'performance': 'pocketds-performance',
}
NAMES = ('org.freedesktop.UPower.PowerProfiles', 'net.hadess.PowerProfiles')
POLKIT_NAMESPACE = 'org.pocketds.TunedPowerProfiles'


class PreflightError(RuntimeError):
    pass


def require_secure(metadata, description, *, directory=False):
    kind_ok = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if not kind_ok or metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise PreflightError(f'{description}: expected root-owned, non-writable regular '
                             + ('directory' if directory else 'file'))


def secure_parent(path):
    for parent in reversed(path.parents):
        require_secure(parent.lstat(), str(parent), directory=True)


def read_secure(path, *, optional=False):
    secure_parent(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if optional:
            return None
        raise
    with os.fdopen(fd, 'rb') as source:
        require_secure(os.fstat(source.fileno()), str(path))
        data = source.read(16385)
    if len(data) > 16384:
        raise PreflightError(f'{path}: oversized state/configuration')
    return data.decode('utf-8')


def validate_config(text):
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(text)
    if config.defaults() or set(config.sections()) != {'main', 'profiles'}:
        raise PreflightError('PPD configuration must contain only main and profiles')
    if dict(config['profiles']) != PROFILES:
        raise PreflightError('PPD mappings must use the three Pocket DS TuneD profiles')
    main = config['main']
    if set(main) != {'default', 'battery_detection', 'sysfs_acpi_monitor'}:
        raise PreflightError('unexpected PPD main configuration')
    if main['default'] not in PROFILES:
        raise PreflightError('unknown PPD default profile')
    if main.getboolean('battery_detection') or main.getboolean('sysfs_acpi_monitor'):
        raise PreflightError('PPD automatic battery/ACPI profile switching must remain disabled')


def map_profile(active):
    profile = active.strip()
    for public, tuned in PROFILES.items():
        if profile == tuned:
            return public
    raise PreflightError('refusing to replace an unmanaged TuneD profile')


def unit_properties(name, runner=subprocess.run):
    result = runner(
        ['/usr/bin/systemctl', 'show', '--property=ActiveState',
         '--property=UnitFileState', name],
        check=True, capture_output=True, text=True, timeout=8,
        env={'PATH': '/usr/sbin:/usr/bin', 'LC_ALL': 'C'},
    )
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def validate_services(ppd, tuned):
    if ppd.get('UnitFileState') != 'masked' or ppd.get('ActiveState') != 'inactive':
        raise PreflightError('power-profiles-daemon must remain masked and inactive')
    if tuned.get('ActiveState') != 'active':
        raise PreflightError('TuneD must already be active')


def current_profile(tuned_interface):
    profile = map_profile(read_secure(ACTIVE))
    if map_profile(str(tuned_interface.active_profile(timeout=8))) != profile:
        raise PreflightError('TuneD profile changed during startup; retry required')
    return profile


def preflight(bus, tuned_interface):
    if os.geteuid() != 0:
        raise PreflightError('root is required')
    validate_config(read_secure(CONFIG))
    read_secure(BASE, optional=True)
    validate_services(unit_properties('power-profiles-daemon.service'),
                      unit_properties('tuned.service'))
    if any(bus.name_has_owner(name) for name in NAMES):
        raise PreflightError('a PPD D-Bus name already has an owner')
    if not bus.name_has_owner('com.redhat.tuned'):
        raise PreflightError('TuneD D-Bus service is unavailable')
    return current_profile(tuned_interface)


def save_base_profile(profile, path=BASE):
    if profile not in PROFILES:
        raise PreflightError('invalid PPD base profile')
    read_secure(path, optional=True)
    fd, temporary = tempfile.mkstemp(prefix='.pocketds-ppd-base.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as target:
            target.write((profile + '\n').encode('ascii'))
            target.flush()
            os.fchmod(target.fileno(), 0o644)
            os.fsync(target.fileno())
        # Never replace a symlink or an unexpectedly writable existing state.
        read_secure(path, optional=True)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def initial_state_exporter(exports, bridge, expected_count):
    """Notify already-running desktop clients only after both objects exist."""
    ready = set()
    announced = False

    class InitialStateExporter(exports.dbus_with_properties.DBusExporterWithProperties):
        def start(self):
            nonlocal announced
            super().start()
            ready.add(id(self))
            if announced or len(ready) != expected_count:
                return
            # PowerDevil can have failed its one-time GetAll before this service
            # existed. Use the official getters and notification path to recover
            # that subscriber without restarting it or polling repeatedly.
            state = (
                ('Profiles', bridge.get_profiles(None)),
                ('ActiveProfile', bridge.get_active_profile(None)),
                ('PerformanceDegraded', bridge.get_performance_degraded(None)),
                ('ActiveProfileHolds', bridge.get_active_profile_holds(None)),
            )
            announced = True
            for name, value in state:
                exports.property_changed(name, value)

    return InitialStateExporter


def run(args):
    if os.geteuid() != 0:
        raise PreflightError('root is required')
    # -Es in the service and shebang ignores PYTHON* overrides and user site packages.
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from tuned import exports
    from tuned.ppd import controller
    import tuned.consts as consts

    if (consts.PPD_CONFIG_FILE != str(CONFIG) or consts.PPD_BASE_PROFILE_FILE != str(BASE)
            or tuple(item['bus'] for item in consts.PPD_DBUS_NAMES) != NAMES):
        raise PreflightError('installed TuneD PPD paths/interfaces require review')
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    if not bus.name_has_owner(consts.DBUS_BUS):
        raise PreflightError('TuneD D-Bus service is unavailable')
    tuned_object = bus.get_object(consts.DBUS_BUS, consts.DBUS_OBJECT)
    tuned_interface = dbus.Interface(tuned_object, consts.DBUS_INTERFACE)
    profile = preflight(bus, tuned_interface)
    if args.check:
        print(json.dumps({'status': 'ready', 'active_profile': profile, 'writes': False}))
        return

    # Keep these references alive. No queued/replacement owner can start a second bridge.
    owned_names = [dbus.service.BusName(name, bus, do_not_queue=True) for name in NAMES]
    # Panel may have changed profiles while service/ownership checks ran.
    profile = current_profile(tuned_interface)
    save_base_profile(profile)
    bridge = controller.Controller(bus, tuned_interface)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _number, _frame: bridge.terminate())
    # A reload exits only after the official controller's orderly cleanup.
    # Exit failure explicitly: systemd otherwise treats SIGHUP as a clean exit.
    restart_requested = False
    def restart(_number, _frame):
        nonlocal restart_requested
        restart_requested = True
        bridge.terminate()
    signal.signal(signal.SIGHUP, restart)
    exporter_class = initial_state_exporter(exports, bridge, len(consts.PPD_DBUS_NAMES))
    for name in consts.PPD_DBUS_NAMES:
        # Keep the standard protocol, but avoid duplicate Polkit action IDs
        # owned by the retained, inactive power-profiles-daemon package.
        exporter = exporter_class(
            name['bus'], name['interface'], name['object'], POLKIT_NAMESPACE)
        exports.register_exporter(exporter)
    exports.register_object(bridge)
    try:
        bridge.run()
    finally:
        exports.stop()
        owned_names.clear()
    if restart_requested:
        raise PreflightError('SIGHUP requested restart with fresh startup checks')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='read-only startup preflight')
    mode.add_argument('--run', action='store_true', help='start the official TuneD PPD bridge')
    args = parser.parse_args()
    try:
        run(args)
    except Exception as error:
        print(json.dumps({'status': 'error', 'reason': str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

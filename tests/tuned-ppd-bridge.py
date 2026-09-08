#!/usr/bin/env python3
"""Exercise the bridge guard without importing TuneD or touching system D-Bus."""

import importlib.util
import contextlib
import io
from pathlib import Path
import stat
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    'pocketds_tuned_ppd', ROOT / 'components/fan/pocketds-tuned-ppd.py')
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)
CONFIG = (ROOT / 'components/fan/ppd.conf').read_text()


class BridgeTests(unittest.TestCase):
    def test_three_exact_mappings_and_configuration(self):
        bridge.validate_config(CONFIG)
        for public, internal in bridge.PROFILES.items():
            self.assertEqual(bridge.map_profile(internal + '\n'), public)
        for value in ('', 'balanced', 'pocketds-balanced pocketds-performance', '../balanced'):
            with self.assertRaises(bridge.PreflightError):
                bridge.map_profile(value)

    def test_reject_default_upstream_or_automatic_configuration(self):
        for config in (
            CONFIG.replace('pocketds-performance', 'throughput-performance'),
            CONFIG.replace('battery_detection=false', 'battery_detection=true'),
            CONFIG.replace('sysfs_acpi_monitor=false', 'sysfs_acpi_monitor=true'),
            CONFIG + '\n[battery]\nbalanced=balanced-battery\n',
            CONFIG.replace('default=balanced', 'default=unknown'),
        ):
            with self.assertRaises(bridge.PreflightError):
                bridge.validate_config(config)

    def test_exclusive_runtime_authority(self):
        bridge.validate_services({'UnitFileState': 'masked', 'ActiveState': 'inactive'},
                                 {'ActiveState': 'active'})
        for ppd, tuned in (
            ({'UnitFileState': 'disabled', 'ActiveState': 'inactive'}, {'ActiveState': 'active'}),
            ({'UnitFileState': 'masked', 'ActiveState': 'active'}, {'ActiveState': 'active'}),
            ({'UnitFileState': 'masked', 'ActiveState': 'inactive'}, {'ActiveState': 'failed'}),
        ):
            with self.assertRaises(bridge.PreflightError):
                bridge.validate_services(ppd, tuned)

    def test_root_file_permissions(self):
        bridge.require_secure(SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0), 'state')
        for mode, uid in ((stat.S_IFREG | 0o664, 0), (stat.S_IFREG | 0o644, 1000),
                          (stat.S_IFLNK | 0o777, 0), (stat.S_IFIFO | 0o600, 0)):
            with self.assertRaises(bridge.PreflightError):
                bridge.require_secure(SimpleNamespace(st_mode=mode, st_uid=uid), 'state')

    def test_atomic_state_preserves_current_profile_and_cleans_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ppd_base_profile'
            path.write_text('balanced\n')
            # The temp directory belongs to the test user; permission validation
            # is tested independently above. Exercise the real atomic writer here.
            with patch.object(bridge, 'read_secure', return_value='balanced\n'):
                bridge.save_base_profile('performance', path)
                self.assertEqual(path.read_text(), 'performance\n')
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
                with patch.object(bridge.os, 'replace', side_effect=OSError('fixture failure')):
                    with self.assertRaises(OSError):
                        bridge.save_base_profile('power-saver', path)
                self.assertEqual(path.read_text(), 'performance\n')
                self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_read_rejects_symlink_and_size(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'state'
            source.write_text('balanced\n')
            link = Path(directory) / 'link'
            link.symlink_to(source)
            with patch.object(bridge, 'secure_parent'), patch.object(bridge, 'require_secure'):
                with self.assertRaises(OSError):
                    bridge.read_secure(link)
                source.write_text('x' * 16385)
                with self.assertRaises(bridge.PreflightError):
                    bridge.read_secure(source)

    def preflight_fixture(self, owner=None, current='pocketds-performance'):
        bus = SimpleNamespace(name_has_owner=lambda name: name == 'com.redhat.tuned' or name == owner)
        tuned = SimpleNamespace(active_profile=lambda **_kwargs: current)
        def read(path, **_kwargs):
            return {bridge.CONFIG: CONFIG, bridge.ACTIVE: 'pocketds-performance\n',
                    bridge.BASE: 'balanced\n'}[path]
        def properties(name):
            return ({'UnitFileState': 'masked', 'ActiveState': 'inactive'}
                    if name == 'power-profiles-daemon.service' else {'ActiveState': 'active'})
        with patch.object(bridge.os, 'geteuid', return_value=0), \
             patch.object(bridge, 'read_secure', side_effect=read), \
             patch.object(bridge, 'unit_properties', side_effect=properties), \
             patch.object(bridge, 'save_base_profile') as save:
            result = bridge.preflight(bus, tuned)
            save.assert_not_called()
            return result

    def test_readonly_preflight_uses_live_profile_not_stale_base(self):
        self.assertEqual(self.preflight_fixture(), 'performance')

    def test_preflight_rejects_owner_and_profile_race(self):
        for owner in bridge.NAMES:
            with self.assertRaises(bridge.PreflightError):
                self.preflight_fixture(owner=owner)
        with self.assertRaises(bridge.PreflightError):
            self.preflight_fixture(current='pocketds-balanced')

    def test_service_keeps_one_controller_and_python_environment_isolation(self):
        service = (ROOT / 'components/fan/pocketds-tuned-ppd.service').read_text()
        self.assertIn('Requires=tuned.service', service)
        self.assertIn('ExecStart=/usr/bin/python3 -Es ', service)
        self.assertEqual(service.count('BusName='), 1)
        self.assertNotIn('Conflicts=tuned', service)
        self.assertIn('ProtectKernelTunables=yes', service)

    def test_official_bridge_wiring_and_preservation_before_controller(self):
        events = []
        handlers = {}
        request_hup = False
        bus = SimpleNamespace(name_has_owner=lambda _name: True,
                              get_object=lambda *_args: object())
        dbus = ModuleType('dbus')
        dbus.SystemBus = lambda: bus
        dbus.Interface = lambda *_args: object()
        dbus.service = ModuleType('dbus.service')
        def own(name, _bus, **flags):
            self.assertTrue(flags['do_not_queue'])
            events.append(('owner', name))
            return object()
        dbus.service.BusName = own
        mainloop = ModuleType('dbus.mainloop')
        glib = ModuleType('dbus.mainloop.glib')
        glib.DBusGMainLoop = lambda **_kwargs: None
        tuned = ModuleType('tuned')
        tuned.consts = SimpleNamespace(
            PPD_CONFIG_FILE=str(bridge.CONFIG), PPD_BASE_PROFILE_FILE=str(bridge.BASE),
            DBUS_BUS='com.redhat.tuned', DBUS_OBJECT='/Tuned',
            DBUS_INTERFACE='com.redhat.tuned.control', PPD_DBUS_NAMES=[
                {'bus': name, 'interface': name, 'object': '/' + name.replace('.', '/'),
                 'namespace': name} for name in bridge.NAMES])
        class Exporter:
            def __init__(self, *arguments):
                events.append(('exporter', arguments))
            def start(self):
                pass
        tuned.exports = SimpleNamespace(
            dbus_with_properties=SimpleNamespace(DBusExporterWithProperties=Exporter),
            register_exporter=lambda _exporter: None,
            register_object=lambda _controller: None,
            stop=lambda: events.append(('stop',)))
        def controller(_bus, _interface):
            self.assertEqual(events[-1], ('save', 'power-saver'))
            events.append(('controller',))
            def run_controller():
                events.append(('run',))
                if request_hup:
                    handlers[bridge.signal.SIGHUP](bridge.signal.SIGHUP, None)
            return SimpleNamespace(terminate=lambda: events.append(('terminate',)),
                                   run=run_controller)
        ppd = ModuleType('tuned.ppd')
        ppd.controller = SimpleNamespace(Controller=controller)
        modules = {'dbus': dbus, 'dbus.service': dbus.service, 'dbus.mainloop': mainloop,
                   'dbus.mainloop.glib': glib, 'tuned': tuned,
                   'tuned.consts': tuned.consts, 'tuned.ppd': ppd}
        def invoke(check=False):
            with patch.dict(bridge.sys.modules, modules), \
                 patch.object(bridge.os, 'geteuid', return_value=0), \
                 patch.object(bridge, 'preflight', return_value='performance'), \
                 patch.object(bridge, 'current_profile', return_value='power-saver'), \
                 patch.object(bridge, 'save_base_profile', side_effect=lambda p: events.append(('save', p))), \
                 patch.object(bridge.signal, 'signal', side_effect=lambda number, handler: handlers.update({number: handler})):
                bridge.run(SimpleNamespace(check=check))
        invoke()
        self.assertEqual(events[:2], [('owner', name) for name in bridge.NAMES])
        registrations = [event[1] for event in events if event[0] == 'exporter']
        self.assertEqual([item[0] for item in registrations], list(bridge.NAMES))
        for arguments in registrations:
            self.assertEqual(arguments[1], arguments[0])
            self.assertEqual(arguments[3], 'org.pocketds.TunedPowerProfiles')
        self.assertEqual(events[-2:], [('run',), ('stop',)])
        events.clear()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            invoke(check=True)
        self.assertEqual(events, [])
        self.assertIn('"writes": false', output.getvalue())
        request_hup = True
        with self.assertRaisesRegex(bridge.PreflightError, 'SIGHUP'):
            invoke()
        self.assertEqual(events[-3:], [('run',), ('terminate',), ('stop',)])

    def test_initial_state_recovers_old_consumer_only_after_both_exports_ready(self):
        instances = []
        notifications = []
        # This models a PowerDevil consumer whose initial GetAll failed; its
        # subscription predates the service and it recovers only via signals.
        consumer = {'Profiles': [], 'ActiveProfile': ''}
        official_state = {'Profiles': object(), 'ActiveProfile': object(),
                          'PerformanceDegraded': object(), 'ActiveProfileHolds': object()}
        getter_calls = []
        class Exporter:
            def __init__(self):
                self.ready = False
                instances.append(self)
            def start(self):
                self.ready = True
        def notify(name, value):
            self.assertEqual(len(instances), 2)
            self.assertTrue(all(instance.ready for instance in instances))
            self.assertIs(value, official_state[name])
            notifications.append(name)
            consumer[name] = value
        def getter(name):
            def read(caller):
                self.assertIsNone(caller)
                getter_calls.append(name)
                return official_state[name]
            return read
        exports = SimpleNamespace(
            dbus_with_properties=SimpleNamespace(DBusExporterWithProperties=Exporter),
            property_changed=notify)
        controller = SimpleNamespace(
            get_profiles=getter('Profiles'), get_active_profile=getter('ActiveProfile'),
            get_performance_degraded=getter('PerformanceDegraded'),
            get_active_profile_holds=getter('ActiveProfileHolds'))
        notifying = bridge.initial_state_exporter(exports, controller, 2)
        first, second = notifying(), notifying()
        first.start()
        first.start()
        self.assertEqual(notifications, [])
        self.assertEqual(getter_calls, [])
        self.assertEqual(consumer, {'Profiles': [], 'ActiveProfile': ''})
        second.start()
        self.assertEqual(consumer, official_state)
        self.assertEqual(notifications, list(official_state))
        self.assertEqual(getter_calls, list(official_state))
        first.start()
        second.start()
        self.assertEqual(notifications, list(official_state))


if __name__ == '__main__':
    unittest.main()

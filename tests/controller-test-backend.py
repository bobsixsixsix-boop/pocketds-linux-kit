#!/usr/bin/env python3
"""Input-free regression checks for controller interception and lease recovery."""
import ast
import copy
import importlib.util
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

try:
    import dbus as real_dbus
    from dbus.lowlevel import MethodCallMessage
except ImportError:
    real_dbus = None

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / 'components/controller-test/pocketds-controller-test.py'
spec = importlib.util.spec_from_file_location('controller_test_backend', PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
A, B = 'a' * 32, 'b' * 32


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


class Backend:
    def __init__(self):
        self.events = []
        self.current_owner = ':1.50'
        self.mode = 0
        self.saved = {'owner': self.current_owner, 'previous_intercept': 0,
                      'profile': sorted(mod.PROFILES)[0], 'targets': ['gamepad','keyboard','dbus']}
        self.sample = {'buttons': mod.blank_buttons(), 'axes': mod.blank_axes(), 'neutral': True}
        self.source_paths = ['/dev/input/event5', '/dev/input/event6']
        self.locked = False
        self.confirmed = True
        self.failure = None
        self.restore_failure = False
    def acquire(self):
        if self.locked: raise mod.CaptureError('lock busy')
        self.events.append('lock'); self.locked = True
    def prepare(self):
        if self.mode not in (0,1): raise mod.CaptureError('other overlay')
        self.saved['previous_intercept'] = self.mode
        self.events.append('prepare'); return dict(self.saved)
    def set_intercept(self, value):
        self.events.append(('intercept',value)); self.mode = value
    def owner(self): return self.current_owner
    def get(self, name):
        return self.mode if name == 'InterceptMode' else self.saved.get(name)
    def snapshot(self, now):
        if self.failure: raise self.failure
        if self.current_owner != self.saved['owner'] or self.mode != 2:
            raise mod.CaptureLost('owner changed')
        return copy.deepcopy(self.sample)
    def capture_confirmed(self): return self.confirmed
    def restore(self):
        self.events.append('restore')
        if self.restore_failure: raise mod.CaptureError('restore failed')
        if self.current_owner != self.saved['owner'] or self.mode != 2:
            raise mod.CaptureLost('owner changed')
        self.mode = self.saved['previous_intercept']
    def release(self): self.events.append('unlock'); self.locked = False
    def load_sources(self): self.events.append('load-sources')
    def refresh_sources(self, now):
        if self.current_owner != self.saved['owner'] or self.mode != 2:
            raise mod.CaptureLost('owner changed')
        self.load_sources()
        return True


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(); self.backend = Backend(); self.records = []
        def write(record):
            self.records.append(copy.deepcopy(record))
            self.backend.events.append(('record',record['blocked'],record['status']))
        self.session = mod.Session(self.backend,write,self.clock)
    def active(self, token=A):
        self.assertEqual(self.session.request('begin',token)['status'],'starting')
        self.clock.advance(.11); self.session.tick()
        self.assertEqual(self.session.status,'active')
    def drain_complete(self):
        self.session.tick(); self.clock.advance(.31); self.session.tick()
    def test_gate_receipt_precedes_first_intercept_mutation(self):
        self.active()
        events=self.backend.events
        self.assertLess(events.index(('record',True,'starting')),events.index(('intercept',2)))
        self.assertTrue(self.backend.locked)
    def test_begin_idempotent_and_conflicting_token_is_busy(self):
        self.active()
        self.assertEqual(self.session.request('begin',A)['status'],'active')
        self.assertEqual(self.session.request('begin',B)['status'],'busy')
        self.assertEqual(self.backend.events.count(('intercept',2)),1)
        self.assertEqual(self.session.token,A)
    def test_lease_expiry_enters_drain_without_poll(self):
        self.active(); self.clock.advance(mod.LEASE_SECONDS); self.session.tick()
        self.assertEqual(self.session.status,'draining'); self.assertEqual(self.backend.mode,2)
        self.clock.advance(.31);self.session.tick()
        self.assertEqual(self.session.status,'idle');self.assertEqual(self.backend.mode,0)
    def test_late_heartbeat_never_revives_expired_lease(self):
        self.active();self.clock.advance(2)
        self.assertEqual(self.session.request('poll',A)['status'],'draining')
        self.assertEqual(self.session.request('begin',A)['status'],'draining')
    def test_valid_poll_renews_lease(self):
        self.active();self.clock.advance(1);self.session.request('poll',A)
        self.clock.advance(1);self.session.tick()
        self.assertEqual(self.session.status,'active')
    def test_end_waits_for_real_neutral_then_quiet_period(self):
        self.active();self.backend.sample['buttons']['a']=1;self.backend.sample['neutral']=False
        self.session.request('end',A);self.clock.advance(3);self.session.tick()
        self.assertEqual(self.backend.mode,2)
        self.backend.sample['buttons']['a']=0;self.backend.sample['neutral']=True
        self.session.tick();self.clock.advance(.29);self.session.tick()
        self.assertEqual(self.backend.mode,2)
        self.clock.advance(.02);self.session.tick()
        self.assertEqual(self.backend.mode,0);self.assertFalse(self.records[-1]['blocked'])
    def test_non_neutral_resets_drain_quiet_period(self):
        self.active();self.session.request('end',A);self.session.tick()
        self.clock.advance(.2);self.backend.sample['neutral']=False;self.session.tick()
        self.backend.sample['neutral']=True;self.clock.advance(.1);self.session.tick()
        self.clock.advance(.2);self.session.tick();self.assertEqual(self.backend.mode,2)
        self.clock.advance(.11);self.session.tick();self.assertEqual(self.backend.mode,0)
    def test_end_is_idempotent_and_old_end_cannot_stop_new_lease(self):
        self.active();self.session.request('end',A);self.drain_complete()
        self.assertEqual(self.session.request('end',A)['status'],'idle')
        self.active(B)
        self.assertEqual(self.session.request('end',A)['status'],'busy')
        self.assertEqual(self.session.token,B);self.assertEqual(self.backend.mode,2)
    def test_held_before_entering_is_visible_without_synthetic_output(self):
        self.backend.sample['buttons']['rb']=1;self.backend.sample['neutral']=False
        self.active();self.assertEqual(self.session.response()['buttons']['rb'],1)
        self.assertNotIn('restore',self.backend.events)
    def test_button_latch_does_not_extend_actual_neutral(self):
        self.active();self.backend.sample['buttons']['a']=1;self.backend.sample['neutral']=False
        self.session.tick();self.clock.advance(.01)
        self.backend.sample['buttons']['a']=0;self.backend.sample['neutral']=True;self.session.tick()
        self.assertEqual(self.session.response()['buttons']['a'],1)
        self.assertTrue(self.session.sample['neutral'])
        self.clock.advance(.13);self.assertEqual(self.session.response()['buttons']['a'],0)
    def test_original_pass_mode_is_restored(self):
        self.backend.mode=1;self.active();self.session.request('end',A);self.drain_complete()
        self.assertEqual(self.backend.mode,1)
    def test_other_overlay_is_never_overwritten(self):
        for mode in (2,3):
            self.backend.mode=mode
            self.assertEqual(self.session.request('begin',A)['status'],'error')
            self.assertEqual(self.backend.mode,mode);self.assertFalse(self.backend.locked)
        self.assertNotIn(('intercept',2),self.backend.events)
    def test_neutral_output_confirmation_required_before_active(self):
        self.backend.confirmed=False;self.session.request('begin',A)
        self.clock.advance(.2);self.session.tick();self.assertEqual(self.session.status,'starting')
        self.clock.advance(1);self.session.tick();self.assertEqual(self.session.status,'draining')
    def test_read_failure_keeps_gate_and_intercept(self):
        self.active();self.backend.failure=OSError('removed');self.session.tick()
        self.assertEqual(self.session.status,'draining');self.assertEqual(self.backend.mode,2)
        self.assertTrue(self.records[-1]['blocked']);self.assertTrue(self.backend.locked)
    def test_restore_failure_keeps_gate_and_lease_recoverable(self):
        self.active();self.backend.restore_failure=True
        self.session.request('end',A);self.drain_complete()
        self.assertEqual(self.session.status,'draining');self.assertTrue(self.records[-1]['blocked'])
        self.assertEqual(self.backend.mode,2);self.assertTrue(self.backend.locked)
    def test_owner_change_does_not_mutate_new_instance(self):
        self.active();self.backend.current_owner=':1.51';self.session.tick()
        self.assertEqual(self.session.status,'error');self.assertNotIn('restore',self.backend.events)
        self.assertFalse(self.backend.locked)
        self.assertEqual(self.records[-1]['status'],'idle')
        self.assertFalse(self.records[-1]['blocked'])
        self.assertEqual(self.session.request('poll',A)['status'],'error')
    def test_external_intercept_change_retires_gate_without_overwriting_it(self):
        self.active();self.backend.mode=3;self.session.tick()
        self.assertEqual(self.backend.mode,3);self.assertEqual(self.records[-1]['status'],'idle')
        self.assertFalse(self.records[-1]['blocked']);self.assertNotIn('restore',self.backend.events)
    def test_slow_prepare_and_capture_do_not_consume_new_lease(self):
        original_prepare=self.backend.prepare
        original_set=self.backend.set_intercept
        def prepare():self.clock.advance(.9);return original_prepare()
        def capture(value):self.clock.advance(.8);original_set(value)
        self.backend.prepare=prepare;self.backend.set_intercept=capture
        self.assertEqual(self.session.request('begin',A)['status'],'starting')
        self.assertAlmostEqual(self.session.deadline,self.clock()+mod.LEASE_SECONDS)
        self.clock.advance(.11);self.session.tick();self.assertEqual(self.session.status,'active')
    def test_source_replug_resumes_drain_with_a_new_full_neutral_period(self):
        self.active();self.session.request('end',A);self.session.tick();self.clock.advance(.2)
        self.backend.failure=mod.SourceUnavailable('unplugged');self.session.tick()
        self.assertIn('load-sources',self.backend.events);self.assertIsNone(self.session.neutral_since)
        self.assertEqual(self.backend.mode,2)
        self.backend.failure=None;self.clock.advance(.1);self.session.tick()
        self.clock.advance(.29);self.session.tick();self.assertEqual(self.backend.mode,2)
        self.clock.advance(.02);self.session.tick();self.assertEqual(self.backend.mode,0)
    def test_source_failure_removes_stale_highlights(self):
        self.active();self.backend.sample['buttons']['a']=1;self.session.tick()
        self.backend.failure=mod.SourceUnavailable('unplugged');self.session.tick()
        self.assertFalse(any(self.session.response()['buttons'].values()))
        self.assertFalse(any(self.session.response()['axes'].values()))
    def test_repeated_drain_does_not_rewrite_unchanged_journal(self):
        self.active();self.session.request('end',A);count=len(self.records)
        for _ in range(10):self.session.request('end',A)
        self.assertEqual(len(self.records),count)
    def test_journal_failure_before_capture_cannot_mutate_intercept(self):
        self.session.write=mock.Mock(side_effect=OSError('disk full'))
        with self.assertRaises(mod.JournalError):self.session.request('begin',A)
        self.assertEqual(self.backend.mode,0);self.assertFalse(self.backend.locked)
        self.assertNotIn(('intercept',2),self.backend.events)
    def test_journal_failure_while_active_terminates_for_stop_post(self):
        self.session.request('begin',A)
        self.session.write=mock.Mock(side_effect=OSError('disk full'))
        self.clock.advance(.11)
        with self.assertRaises(mod.JournalError):self.session.tick()
        self.assertEqual(self.backend.mode,2);self.assertTrue(self.records[-1]['blocked'])
    def test_journal_failure_after_restore_preserves_recovery_receipt(self):
        self.active();self.session.request('end',A);self.session.tick()
        self.session.write=mock.Mock(side_effect=OSError('disk full'));self.clock.advance(.31)
        with self.assertRaises(mod.JournalError):self.session.tick()
        self.assertEqual(self.backend.mode,0);self.assertTrue(self.records[-1]['blocked'])
    def test_invalid_requests_never_acquire(self):
        for token in ('', 'F'*32, 'a'*31, 'a'*33, '../state', [], None):
            self.assertEqual(self.session.request('begin',token)['status'],'error')
        self.assertEqual(self.session.request('destroy',A)['status'],'error')
        self.assertEqual(self.backend.events,[])
    def test_response_is_bounded_and_all_axes_finite(self):
        self.active();reply=self.session.response()
        self.assertLess(len(json.dumps(reply).encode()),16384)
        self.assertEqual(set(reply['axes']),set(mod.AXIS_NAMES))
        self.assertTrue(all(math.isfinite(value) for value in reply['axes'].values()))
        self.assertEqual(set(reply['buttons']),set(mod.BUTTON_NAMES))
    def test_only_graphical_user_and_root_authorized(self):
        self.assertTrue(mod.Server.authorized_uid(0,1000))
        self.assertTrue(mod.Server.authorized_uid(1000,1000))
        for uid in (-1,1,999,1001,65534):self.assertFalse(mod.Server.authorized_uid(uid,1000))


class SnapshotTests(unittest.TestCase):
    @staticmethod
    def info(value,low=-32768,high=32767):return (value,low,high,0,0,0)
    def test_physical_x_y_codes_light_only_the_corresponding_face_button(self):
        # Independent physical-label expectations, not derived from BUTTON_CODES.
        for code,expected in ((307,{'x':1,'y':0}),(308,{'x':0,'y':1})):
            with self.subTest(code=code):
                buttons=mod.decode_snapshots([({code},{})])['buttons']
                self.assertEqual({name:buttons[name] for name in ('x','y')},expected)
                self.assertEqual(sum(buttons.values()),1)
    def test_every_named_physical_button_decodes(self):
        for name,code in mod.BUTTON_CODES.items():
            with self.subTest(name=name):
                result=mod.decode_snapshots([({code},{})])
                self.assertEqual(result['buttons'][name],1);self.assertFalse(result['neutral'])
    def test_dual_device_same_button_is_or_not_overwritten(self):
        self.assertEqual(mod.decode_snapshots([({304},{}),(set(),{})])['buttons']['a'],1)
    def test_unknown_button_still_prevents_restore(self):
        self.assertFalse(mod.decode_snapshots([({300},{})])['neutral'])
    def test_dpad_axes_and_diagonals(self):
        for x,y in ((-1,-1),(1,1),(-1,1),(1,-1),(0,0)):
            result=mod.decode_snapshots([(set(),{16:self.info(x,-1,1),17:self.info(y,-1,1)})])
            self.assertEqual(result['buttons']['dpad_left'],int(x<0))
            self.assertEqual(result['buttons']['dpad_right'],int(x>0))
            self.assertEqual(result['buttons']['dpad_up'],int(y<0))
            self.assertEqual(result['buttons']['dpad_down'],int(y>0))
    def test_real_directinput_axis_layout(self):
        values={0:self.info(32767),1:self.info(-32768),2:self.info(-32768),5:self.info(32767),
                9:self.info(255,0,255),10:self.info(128,0,255)}
        axes=mod.decode_snapshots([(set(),values)])['axes']
        self.assertEqual(axes,{'lx':1,'ly':-1,'rx':-1,'ry':1,'lt':128/255,'rt':1})
    def test_xinput_axis_layout_without_gas_brake(self):
        values={0:self.info(0),1:self.info(0),3:self.info(32767),4:self.info(-32768),
                2:self.info(255,0,255),5:self.info(0,0,255)}
        axes=mod.decode_snapshots([(set(),values)])['axes']
        self.assertEqual((axes['rx'],axes['ry'],axes['lt'],axes['rt']),(1,-1,1,0))
    def test_small_real_stick_drift_is_neutral(self):
        values={0:self.info(0),1:self.info(-224),2:self.info(0),5:self.info(-180),
                9:self.info(0,0,255),10:self.info(0,0,255)}
        self.assertTrue(mod.decode_snapshots([(set(),values)])['neutral'])
    def test_any_held_stick_or_trigger_blocks_restore(self):
        for name in mod.AXIS_NAMES:
            values={0:self.info(0),1:self.info(0),2:self.info(0),5:self.info(0),
                    9:self.info(0,0,255),10:self.info(0,0,255)}
            code={'lx':0,'ly':1,'rx':2,'ry':5,'lt':10,'rt':9}[name]
            values[code]=self.info(255,0,255) if name in ('lt','rt') else self.info(32767)
            self.assertFalse(mod.decode_snapshots([(set(),values)])['neutral'])
    def test_out_of_range_axes_fail_instead_of_claiming_neutral(self):
        for value,low,high in ((0,0,0),(2,-1,1),(-2,-1,1)):
            with self.assertRaises(mod.CaptureError):mod.normalize(value,low,high)
    def test_ioctl_numbers_are_read_only(self):
        self.assertEqual(mod.ior(0x18,96),0x80604518)
        self.assertEqual(mod.ior(0x40,24),0x80184540)
        self.assertEqual(mod.bits(bytes([1,128])),{0,15})


class LiveSourceTests(unittest.TestCase):
    """Exercise the production source/identity logic with input-free devices."""
    def setUp(self):
        self.backend=object.__new__(mod.LiveBackend)
        self.backend.devices=[];self.backend.lock_fd=None;self.backend.source_paths=None
        self.backend.last_identity_check=0.;self.backend.next_source_reload=0.
        self.current_owner=':1.50'
        self.props={'InterceptMode':2,'ProfilePath':sorted(mod.PROFILES)[0],
                    'TargetDevices':['/org/shadowblip/InputPlumber/devices/target/'+kind+'0'
                                     for kind in ('dbus','gamepad','keyboard')],
                    'SourceDevicePaths':['/dev/input/event5','/dev/input/event6','/dev/hidraw2']}
        self.backend.owner=lambda:self.current_owner
        self.backend.get=lambda name:self.props[name]
        self.backend.set_intercept=lambda value:self.props.update(InterceptMode=value)
        self.backend.capture_confirmed=mock.Mock(return_value=True)
        self.backend.saved={'owner':self.current_owner,'previous_intercept':0,
                            'profile':self.props['ProfilePath'],'targets':self.props['TargetDevices'][:]}
        self.created=[]
        class Device:
            def __init__(device,path):
                device.path=path;device.closed=False;device.failure=None;device.keys=set()
                mcu=path.endswith('5')
                device.identity=(3,0x1c4f if mcu else 0x4001,0x0002 if mcu else 0x0428,273)
                device.supported_keys=set(range(256,264) if mcu else range(304,319))
                device.supported_axes=set() if mcu else {0,1,2,5,9,10,16,17}
                device.values={code:SnapshotTests.info(0,0,255) if code in (9,10)
                               else SnapshotTests.info(0,-1,1) if code in (16,17)
                               else SnapshotTests.info(0) for code in device.supported_axes}
                self.created.append(device)
            def snapshot(device):
                if device.failure:raise device.failure
                return device.keys,device.values
            def close(device):device.closed=True
        patcher=mock.patch.object(mod,'PhysicalDevice',Device)
        patcher.start();self.addCleanup(patcher.stop)
        self.addCleanup(self.backend.release)
    def test_same_event_number_replug_reopens_fd_and_waits_for_new_neutral(self):
        self.backend.load_sources();old=self.backend.devices[:]
        old[-1].failure=OSError('unplugged')
        with self.assertRaises(mod.SourceUnavailable):self.backend.snapshot(100)
        self.assertTrue(self.backend.refresh_sources(100));self.assertTrue(all(d.closed for d in old))
        self.assertIsNot(old[-1],self.backend.devices[-1])
        self.backend.devices[-1].keys={304}
        self.assertFalse(self.backend.snapshot(100.1)['neutral'])
        self.backend.devices[-1].keys.clear()
        self.assertTrue(self.backend.snapshot(100.2)['neutral'])
    def test_changed_event_paths_are_read_from_composite_not_guessed(self):
        self.backend.load_sources()
        self.props['SourceDevicePaths']=['/dev/input/event5','/dev/input/event8']
        with self.assertRaises(mod.SourceUnavailable):self.backend.snapshot(100)
        self.assertTrue(self.backend.refresh_sources(100))
        self.assertEqual(self.backend.source_paths,['/dev/input/event5','/dev/input/event8'])
        self.assertTrue(self.backend.snapshot(100.1)['neutral'])
    def test_missing_gamepad_or_mcu_cannot_be_treated_as_neutral(self):
        for paths in (['/dev/input/event5'],['/dev/input/event6'],[]):
            self.props['SourceDevicePaths']=paths
            with self.assertRaises(mod.SourceUnavailable):self.backend.load_sources()
            self.assertFalse(self.backend.devices)
    def test_missing_trigger_or_dpad_capabilities_rejects_incomplete_source(self):
        original=mod.PhysicalDevice
        for code in (2,5,9,10,16,17):
            def partial(path):
                device=original(path);device.supported_axes.discard(code);return device
            with mock.patch.object(mod,'PhysicalDevice',partial):
                with self.assertRaises(mod.SourceUnavailable):self.backend.load_sources()
    def test_source_reload_backoff_still_detects_lost_intercept_every_tick(self):
        self.assertTrue(self.backend.refresh_sources(100));count=len(self.created)
        self.assertFalse(self.backend.refresh_sources(100.1));self.assertEqual(len(self.created),count)
        self.props['InterceptMode']=0
        with self.assertRaises(mod.CaptureLost):self.backend.refresh_sources(100.2)
    def test_same_owner_target_replacement_never_receives_old_prior(self):
        self.backend.load_sources();self.props['TargetDevices']=['replacement']
        for action in (lambda:self.backend.snapshot(100),lambda:self.backend.refresh_sources(100),self.backend.restore):
            with self.assertRaises(mod.CaptureError) as caught:action()
            self.assertNotIsInstance(caught.exception,mod.CaptureLost)
        self.assertEqual(self.props['InterceptMode'],2)
    def test_same_owner_new_normal_mode_retires_capture_without_restore(self):
        self.backend.load_sources();self.props['TargetDevices']=['replacement'];self.props['InterceptMode']=1
        with self.assertRaises(mod.CaptureLost):self.backend.restore()
        self.assertEqual(self.props['InterceptMode'],1)
    def test_restore_rechecks_last_moment_real_held_state_and_sources(self):
        self.backend.load_sources();self.backend.devices[-1].keys={304}
        with self.assertRaises(mod.CaptureError):self.backend.restore()
        self.assertEqual(self.props['InterceptMode'],2)
        self.backend.devices[-1].keys.clear();self.props['SourceDevicePaths']=['/dev/input/event5']
        with self.assertRaises(mod.SourceUnavailable):self.backend.restore()
        self.assertEqual(self.props['InterceptMode'],2)


class MouseOutputRestoreTests(unittest.TestCase):
    """Actual restore/Session/recover with the pinned mouse-clear boundary."""
    class MouseBackend(Backend):
        restore = mod.LiveBackend.restore

        def __init__(self, *, fixed=False):
            super().__init__()
            self.fixed=fixed;self.left=True;self.physical_r1=True
            self.saved['profile']='/usr/share/inputplumber/profiles/pocketds-joymouse.yaml'
            self.saved['targets']=['dbus','keyboard','mouse']
            self.devices=[mock.Mock(snapshot=lambda:({311} if self.physical_r1 else set(),{}))]
        def set_intercept(self,value):
            super().set_intercept(value)
            if value==2 and self.fixed:self.left=False
        def check_capture(self):
            if self.current_owner!=self.saved['owner'] or self.mode!=2:
                raise mod.CaptureLost('owner/intercept changed')
        def current_source_paths(self):return self.source_paths
        def snapshot(self,now):
            self.check_capture()
            return mod.decode_snapshots([self.devices[0].snapshot()])
        def capture_confirmed(self):
            return mod.decode_snapshots([({272} if self.left else set(),{})])['neutral']

    def setUp(self):
        self.clock=Clock();self.records=[]
    def test_old_mouse_clear_cannot_report_idle_after_r1_release(self):
        backend=self.MouseBackend();session=mod.Session(backend,self.records.append,self.clock)
        session.request('begin',A);backend.physical_r1=False
        self.clock.advance(1.05);session.tick()
        self.clock.advance(.31);session.tick()
        self.assertEqual(session.status,'draining');self.assertEqual(backend.mode,2)
        self.assertTrue(backend.left);self.assertTrue(self.records[-1]['blocked'])
        self.assertIn('虚拟输入',session.error)
        backend.left=False
        session.tick();self.clock.advance(.31);session.tick()
        self.assertEqual(session.status,'idle');self.assertFalse(self.records[-1]['blocked'])
    def test_fixed_mouse_clear_accepts_held_r1_but_waits_for_physical_release(self):
        backend=self.MouseBackend(fixed=True);session=mod.Session(backend,self.records.append,self.clock)
        session.request('begin',A);self.clock.advance(.11);session.tick()
        self.assertEqual(session.status,'active');self.assertEqual(session.sample['buttons']['rb'],1)
        session.request('end',A);self.clock.advance(.5);session.tick()
        self.assertEqual(backend.mode,2);self.assertTrue(backend.physical_r1)
        backend.physical_r1=False
        session.tick();self.clock.advance(.31);session.tick()
        self.assertEqual(session.status,'idle');self.assertFalse(backend.left)
    def test_stop_post_output_failure_is_bounded_and_keeps_blocked_receipt(self):
        backend=self.MouseBackend();backend.mode=2;backend.physical_r1=False
        journal={'protocol':mod.PROTOCOL,'token':A,'blocked':True,'status':'draining',**backend.saved}
        args=dict(timeout=1,clock=self.clock,sleep=self.clock.advance,
                  read=lambda:copy.deepcopy(journal),write=self.records.append)
        self.assertFalse(mod.recover(backend,**args))
        self.assertLess(self.clock.now,101.1);self.assertEqual(backend.mode,2)
        self.assertEqual(self.records,[]);self.assertFalse(backend.locked)
        backend.left=False
        self.assertTrue(mod.recover(backend,**args));self.assertFalse(self.records[-1]['blocked'])
    def test_output_read_failure_does_not_release_intercept(self):
        backend=self.MouseBackend();backend.mode=2;backend.physical_r1=False
        backend.capture_confirmed=mock.Mock(side_effect=OSError('output disappeared'))
        with self.assertRaises(OSError):backend.restore()
        self.assertEqual(backend.mode,2)
    def test_owner_change_during_output_check_cannot_restore_old_intercept(self):
        backend=self.MouseBackend();backend.mode=2;backend.physical_r1=False
        def changed():backend.current_owner=':1.51';return True
        backend.capture_confirmed=changed
        with self.assertRaises(mod.CaptureLost):backend.restore()
        self.assertEqual(backend.mode,2)
    def test_physical_press_during_output_check_is_rechecked(self):
        backend=self.MouseBackend();backend.mode=2;backend.physical_r1=False
        def pressed():backend.physical_r1=True;return True
        backend.capture_confirmed=pressed
        with self.assertRaisesRegex(mod.CaptureError,'松开'):backend.restore()
        self.assertEqual(backend.mode,2)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.backend=Backend();self.backend.mode=2
        self.journal={'protocol':mod.PROTOCOL,'token':A,'blocked':True,'status':'active',**self.backend.saved}
        self.records=[]
    def recover(self,read=None):
        return mod.recover(self.backend,timeout=1,clock=self.clock,sleep=self.clock.advance,
                           read=read or (lambda:copy.deepcopy(self.journal)),write=self.records.append)
    def test_sigkill_recovery_waits_neutral_before_restore(self):
        self.assertTrue(self.recover());self.assertGreaterEqual(self.clock.now,100.3)
        self.assertEqual(self.backend.mode,0);self.assertFalse(self.records[-1]['blocked'])
    def test_held_on_sigkill_is_not_forcibly_released_at_timeout(self):
        self.backend.sample['neutral']=False
        self.assertFalse(self.recover());self.assertEqual(self.backend.mode,2)
        self.assertEqual(self.records,[]);self.assertFalse(self.backend.locked)
    def test_recovery_after_release_succeeds_on_next_attempt(self):
        self.backend.sample['neutral']=False;self.assertFalse(self.recover())
        self.backend.sample['neutral']=True;self.assertTrue(self.recover())
        self.assertEqual(self.backend.mode,0)
    def test_partial_enter_before_mutation_clears_receipt_only(self):
        self.backend.mode=0;self.assertTrue(self.recover())
        self.assertNotIn('restore',self.backend.events);self.assertFalse(self.records[-1]['blocked'])
    def test_new_inputplumber_owner_keeps_its_intercept_value(self):
        self.backend.current_owner=':1.51';self.backend.mode=3
        self.assertTrue(self.recover());self.assertEqual(self.backend.mode,3)
        self.assertNotIn('restore',self.backend.events)
    def test_newer_token_never_restored_by_stale_stop_post(self):
        count=0
        def read():
            nonlocal count
            count+=1
            return {**self.journal,'token':A if count==1 else B}
        self.assertFalse(self.recover(read));self.assertEqual(self.backend.mode,2)
        self.assertEqual(self.records,[])
    def test_restore_error_keeps_crash_journal(self):
        self.backend.restore_failure=True
        with self.assertRaises(mod.CaptureError):self.recover()
        self.assertEqual(self.backend.mode,2);self.assertEqual(self.records,[])
        self.assertFalse(self.backend.locked)
    def test_read_failure_does_not_unblock(self):
        self.backend.failure=OSError('device removed')
        self.assertFalse(self.recover())
        self.assertEqual(self.records,[]);self.assertEqual(self.backend.mode,2)
    def test_unplug_during_stop_post_can_recover_after_replug(self):
        self.backend.failure=mod.SourceUnavailable('unplugged')
        original=self.backend.refresh_sources
        def refresh(now):
            if now>=100.3:self.backend.failure=None
            return original(now)
        self.backend.refresh_sources=refresh
        self.assertTrue(self.recover());self.assertGreaterEqual(self.clock(),100.6)
        self.assertEqual(self.backend.mode,0);self.assertEqual(self.records[-1]['status'],'idle')
    def test_owner_replacement_during_stop_post_retires_only_old_receipt(self):
        self.backend.sample['neutral']=False
        def sleep(seconds):
            self.clock.advance(seconds)
            if self.clock()>=100.2:self.backend.current_owner=':1.51'
        self.assertTrue(mod.recover(self.backend,timeout=1,clock=self.clock,sleep=sleep,
                                   read=lambda:self.journal,write=self.records.append))
        self.assertNotIn('restore',self.backend.events);self.assertEqual(self.backend.mode,2)
        self.assertEqual(self.records[-1]['status'],'idle');self.assertFalse(self.records[-1]['blocked'])
    def test_legacy_terminal_error_record_is_normalized_for_gate_readers(self):
        record={**self.journal,'status':'error','blocked':False}
        self.assertTrue(mod.recover(self.backend,read=lambda:record,write=self.records.append))
        self.assertEqual(self.records[-1]['status'],'idle');self.assertEqual(self.backend.events,[])
    def test_missing_or_idle_record_performs_no_backend_operation(self):
        for record in (None,{'blocked':False}):
            self.assertTrue(mod.recover(self.backend,read=lambda:record))
        self.assertEqual(self.backend.events,[])


class ServerTests(unittest.TestCase):
    def test_slow_valid_begin_gets_a_fresh_bounded_reply_deadline(self):
        clock=Clock();client=mock.Mock();client.recv.return_value=json.dumps({'op':'begin','token':A}).encode()+b'\n'
        server=object.__new__(mod.Server);server.socket=object();server.selector=mock.Mock()
        server.clients={client:{'in':bytearray(),'out':bytearray(),'deadline':clock()+.5}}
        server.session=mock.Mock()
        def request(op,token):
            self.assertEqual((op,token),('begin',A));clock.advance(.8)
            return {'status':'starting','token':token,'buttons':{},'axes':{},'error':''}
        server.session.request.side_effect=request
        with mock.patch.object(mod.time,'monotonic',clock):
            server.event(mock.Mock(fileobj=client),mod.selectors.EVENT_READ)
        self.assertAlmostEqual(server.clients[client]['deadline'],clock()+.5)
        self.assertEqual(json.loads(server.clients[client]['out'])['status'],'starting')
        client.close.assert_not_called()


@unittest.skipUnless(real_dbus is not None, 'dbus-python is required for real message marshalling')
class DBusMarshallingTests(unittest.TestCase):
    def test_intercept_set_encodes_properties_ssv_with_uint32_variant(self):
        # Exercise the production setter with real dbus-python values and the
        # real message encoder; no bus connection or message send takes place.
        backend=object.__new__(mod.LiveBackend)
        backend.dbus=real_dbus
        messages=[]
        class Proxy:
            def Set(self,*arguments,**kwargs):
                message=MethodCallMessage(mod.SERVICE,mod.COMPOSITE,
                                          kwargs['dbus_interface'],'Set')
                message.append(*arguments)
                messages.append(message)
        backend.bus=mock.Mock()
        backend.bus.get_object.return_value=Proxy()
        for value in (0,1,2):
            with self.subTest(intercept=value):
                backend.get=mock.Mock(return_value=value)
                backend.set_intercept(value)
                message=messages[-1]
                self.assertEqual(str(message.get_signature()),'ssv')
                interface,property_name,encoded=message.get_args_list()
                self.assertEqual(str(interface),mod.INTERFACE)
                self.assertEqual(str(property_name),'InterceptMode')
                self.assertIsInstance(encoded,real_dbus.UInt32)
                self.assertEqual(encoded.variant_level,1)
                self.assertEqual(int(encoded),value)
                backend.get.assert_called_once_with('InterceptMode')


class SourceSafetyTests(unittest.TestCase):
    def test_no_input_emulation_or_profile_mutation_paths(self):
        tree=ast.parse(PATH.read_text())
        forbidden={'grab','ungrab','write_event','send_event','SendEvent','SendButtonChord',
                   'SetTargetDevices','LoadProfilePath','UInput','InputDevice','set_absinfo'}
        calls=[node.func.attr for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)]
        self.assertFalse(forbidden.intersection(calls))
        mutations=[node for node in ast.walk(tree) if isinstance(node,ast.Call)
                   and isinstance(node.func,ast.Attribute) and node.func.attr=='Set']
        self.assertEqual(len(mutations),1)
        self.assertEqual(ast.literal_eval(mutations[0].args[1]),'InterceptMode')
    def test_physical_open_is_read_only_and_never_accepts_arbitrary_paths(self):
        for path in ('/dev/hidraw2','/dev/input/../input/event6','/tmp/event6','/dev/input/event6/extra'):
            with self.assertRaises(mod.CaptureError):mod.PhysicalDevice(path)
        source=PATH.read_text()
        self.assertIn('os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW',source)
        self.assertIn('SourceDevicePaths',source)
        self.assertIn('DEVICE_IDS',source)
    def test_notify_ready_and_stop_post_are_shipped(self):
        unit=PATH.with_name('pocketds-controller-test.service').read_text()
        self.assertIn('Type=notify',unit);self.assertIn('ExecStopPost=',unit)
        self.assertIn('--recover',unit);self.assertIn('RuntimeDirectoryPreserve=yes',unit)
        self.assertIn('READY=1',PATH.read_text())
    def test_journal_atomic_publish_is_bounded_and_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json'
            mod.publish({'protocol':mod.PROTOCOL,'blocked':False},path)
            self.assertEqual(json.loads(path.read_text())['blocked'],False)
            self.assertEqual(path.stat().st_mode & 0o777,0o644)
            self.assertEqual(list(Path(directory).iterdir()),[path])
            with self.assertRaises(mod.CaptureError):mod.publish({'x':'a'*9000},path)


if __name__=='__main__': unittest.main()

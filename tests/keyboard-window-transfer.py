#!/usr/bin/env python3
"""One-shot KWin transfer: private-file/D-Bus boundaries and executable JS fixtures."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components/keyboard"))
import keyboard_adapter as adapter  # noqa: E402


class TransferBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name)
        self.runtime.chmod(0o700)
        self.calls = []
        self.script_names = []

    def runner(self, command, **kwargs):
        self.calls.append(command)
        self.assertEqual(command[:2], ['/usr/bin/qdbus-qt6', 'org.kde.KWin'])
        self.assertEqual(kwargs, dict(text=True, capture_output=True,
                                     stdin=subprocess.DEVNULL, timeout=1.0, check=False))
        method = command[3]
        if method.endswith('.loadScript'):
            path = Path(command[4])
            self.assertEqual(path.parent, self.runtime)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.read_text(), adapter.LOWER_WINDOWS_TO_UPPER_SCRIPT)
            self.script_names.append(command[5])
            return subprocess.CompletedProcess(command, 0, '37\n', '')
        if method.endswith('.run'):
            self.assertEqual(command[2], '/Scripting/Script37')
            return subprocess.CompletedProcess(command, 0, '', '')
        self.assertEqual(method, 'org.kde.kwin.Scripting.unloadScript')
        self.assertEqual(command[4], self.script_names[-1])
        return subprocess.CompletedProcess(command, 0, 'true\n', '')

    def transfer(self, runner=None):
        return adapter.move_lower_windows_to_upper(runtime=self.runtime,
                                                   runner=self.runner if runner is None else runner)

    def test_one_shot_uses_private_unique_file_and_cleans_after_completed_run(self):
        self.assertTrue(self.transfer())
        self.assertEqual([call[3] for call in self.calls], [
            'org.kde.kwin.Scripting.loadScript', 'org.kde.kwin.Script.run',
            'org.kde.kwin.Scripting.unloadScript'])
        self.assertEqual(list(self.runtime.iterdir()), [])
        self.assertTrue(self.transfer())
        self.assertNotEqual(*self.script_names)

    def test_load_timeout_still_unloads_unique_name_and_deletes_file(self):
        def run(command, **kwargs):
            result = self.runner(command, **kwargs)
            if command[3].endswith('.loadScript'):
                raise subprocess.TimeoutExpired(command, 1)
            return result
        self.assertFalse(self.transfer(run))
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(self.calls[-1][3].endswith('.unloadScript'))
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_run_timeout_and_error_still_unload_and_cleanup(self):
        for failure in ('timeout', 'error'):
            with self.subTest(failure=failure):
                self.calls.clear()
                def run(command, **kwargs):
                    result = self.runner(command, **kwargs)
                    if command[3].endswith('.run'):
                        if failure == 'timeout':
                            raise subprocess.TimeoutExpired(command, 1)
                        result.returncode = 1
                    return result
                self.assertFalse(self.transfer(run))
                self.assertEqual(len(self.calls), 3)
                self.assertTrue(self.calls[-1][3].endswith('.unloadScript'))
                self.assertEqual(list(self.runtime.iterdir()), [])

    def test_invalid_load_ids_never_address_an_arbitrary_dbus_object(self):
        for value in ('-1', '', '37\n38', '١', '2147483648', '../KWin'):
            with self.subTest(value=value):
                self.calls.clear()
                def run(command, **kwargs):
                    result = self.runner(command, **kwargs)
                    if command[3].endswith('.loadScript'):
                        result.stdout = value
                    return result
                self.assertFalse(self.transfer(run))
                self.assertEqual(len(self.calls), 2)
                self.assertEqual(list(self.runtime.iterdir()), [])

    def test_load_failure_and_missing_qdbus_report_false_and_cleanup(self):
        for failure in ('error', 'missing'):
            with self.subTest(failure=failure):
                def run(command, **kwargs):
                    result = self.runner(command, **kwargs)
                    if failure == 'missing':
                        raise FileNotFoundError('qdbus')
                    result.returncode = 1
                    return result
                self.assertFalse(self.transfer(run))
                self.assertEqual(list(self.runtime.iterdir()), [])

    def test_unload_failure_does_not_leave_private_file_or_report_success(self):
        for failure in ('timeout', 'false', 'error'):
            with self.subTest(failure=failure):
                def run(command, **kwargs):
                    result = self.runner(command, **kwargs)
                    if command[3].endswith('.unloadScript'):
                        if failure == 'timeout':
                            raise subprocess.TimeoutExpired(command, 1)
                        result.stdout = 'false' if failure == 'false' else ''
                        result.returncode = 1 if failure == 'error' else 0
                    return result
                self.assertFalse(self.transfer(run))
                self.assertEqual(list(self.runtime.iterdir()), [])

    def test_unsafe_runtime_is_rejected_without_dbus_or_permission_changes(self):
        for mode in (0o755, 0o750, 0o777):
            with self.subTest(mode=mode):
                self.runtime.chmod(mode)
                self.assertFalse(self.transfer())
                self.assertEqual(stat.S_IMODE(self.runtime.stat().st_mode), mode)
        self.assertEqual(self.calls, [])
        self.runtime.chmod(0o700)
        link = self.runtime / 'symlink'
        link.symlink_to(self.runtime, target_is_directory=True)
        self.assertFalse(adapter.move_lower_windows_to_upper(runtime=link, runner=self.runner))
        self.assertEqual(self.calls, [])

    def test_foreign_owner_and_missing_runtime_do_not_create_or_use_files(self):
        real = self.runtime.lstat()
        metadata = mock.Mock(st_mode=real.st_mode, st_uid=os.getuid() + 1)
        with mock.patch.object(Path, 'lstat', return_value=metadata):
            self.assertFalse(self.transfer())
        missing = self.runtime / 'absent'
        self.assertFalse(adapter.move_lower_windows_to_upper(runtime=missing, runner=self.runner))
        self.assertFalse(missing.exists())
        self.assertEqual(self.calls, [])

    def test_exclusive_file_collision_cannot_overwrite_or_unlink_an_existing_file(self):
        with mock.patch.object(adapter.secrets, 'token_hex', return_value='fixture'):
            existing = self.runtime / f'.pocketds-windows-top-{os.getpid()}-fixture.js'
            existing.write_text('pre-existing unrelated contents')
            self.assertFalse(self.transfer())
            self.assertEqual(existing.read_text(), 'pre-existing unrelated contents')
            self.assertEqual(self.calls, [])


JS_FIXTURE = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const upper = {name: 'DSI-1'}, lower = {name: 'DSI-2'}, external = {name: 'HDMI-A-1'};
const deskA = {id: 'a'}, deskB = {id: 'b'}, outputDesktop = {id: 'upper-current'};
let windows = [], sent = [], writes = [], logs = [];
function same(left, right) {
    return left.length === right.length && left.every((entry, index) => entry === right[index]);
}
function make(name, extras = {}) {
    const window = Object.assign({name, managed:true, deleted:false, moveableAcrossScreens:true,
        normalWindow:true, dialog:false, utility:false, toolbar:false, specialWindow:false,
        desktopWindow:false, dock:false, popupWindow:false, inputMethod:false,
        resourceClass:'ordinary.app', resourceName:'ordinary', desktopFileName:'ordinary.app',
        output:lower, transientFor:null, modal:false}, extras);
    let desktops = (extras.desktops || [deskA]).slice();
    Object.defineProperty(window, 'desktops', {get: () => desktops, set: value => {
        if (same(value, desktops)) return;
        desktops = value.slice();
        writes.push(window.name);
        // Native setDesktops recurses into descendants, and modal dialogs can
        // change their ancestors. Model both, not just the script's own order.
        for (const child of windows.filter(other => other.transientFor === window)) child.desktops = value;
        if (window.modal && window.transientFor) window.transientFor.desktops = value;
    }});
    for (const [key, value] of Object.entries({minimized:false, fullScreen:false,
            maximizedHorizontally:false, maximizedVertically:false, activities:['work'],
            frameGeometry:{x:10,y:720,width:300,height:200}, skipTaskbar:false, keepAbove:false})) {
        Object.defineProperty(window, key, {get: () => Object.hasOwn(extras,key) ? extras[key] : value,
            set: () => {throw new Error('script must not write '+key);}});
    }
    windows.push(window);
    return window;
}
const workspace = {
    screens: [upper, lower, external], windowList: () => windows,
    sendClientToScreen: (window, target) => {
        sent.push(window.name);
        if (window.refuse) return;
        window.output = target;
        window.desktops = [outputDesktop];
        for (const child of windows.filter(other => other.transientFor === window)) {
            workspace.sendClientToScreen(child, target);
        }
        if (window.failAfterMove) throw new Error('fixture transfer error');
    }
};
Object.defineProperty(workspace,'activeWindow',{set:()=>{throw new Error('activation forbidden');}});
Object.defineProperty(workspace,'windowAdded',{get:()=>{throw new Error('persistent handler forbidden');}});
// KWin provides the Qt console extension, not a global print() function.
function run() { vm.runInNewContext(PRODUCTION, {workspace, console: {log: line => logs.push(line)}}); }
"""


NODE = os.environ.get('NODE_BINARY') or shutil.which('node')


@unittest.skipUnless(NODE, 'Node.js executes the production KWin JavaScript fixtures')
class WindowTransferJavaScriptTests(unittest.TestCase):
    def js(self, test):
        import json
        result = subprocess.run([NODE, '-e', 'const PRODUCTION=' + json.dumps(
            adapter.LOWER_WINDOWS_TO_UPPER_SCRIPT) + ';\n' + JS_FIXTURE + '\n' + test],
            text=True, capture_output=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_filters_outputs_and_system_surfaces_but_keeps_all_application_states(self):
        self.js(r"""
const normal = make('normal'), mini = make('mini',{minimized:true}),
    max = make('max',{maximizedHorizontally:true,maximizedVertically:true}),
    full = make('full',{fullScreen:true}), dialog = make('dialog',{normalWindow:false,dialog:true}),
    utility = make('utility',{normalWindow:false,utility:true}),
    toolbar = make('toolbar',{normalWindow:false,toolbar:true,specialWindow:true}),
    hiddenTask = make('hiddenTask',{skipTaskbar:true,keepAbove:true});
for (const flags of [{output:upper},{output:external},{managed:false},{deleted:true},
    {moveableAcrossScreens:false},{specialWindow:true},{desktopWindow:true},{dock:true},
    {popupWindow:true},{inputMethod:true},{normalWindow:false},
    {resourceClass:'Pocketds-keyboard'},{resourceName:'pocketds-touchpad'},
    {desktopFileName:'org.kde.plasmashell'},{resourceClass:'pocketds-panel-shell'},
    {resourceClass:'xwaylandvideobridge'},{desktopFileName:'org.kde.xwaylandvideobridge'}]) make('excluded',flags);
run();
assert.deepEqual(sent,['normal','mini','max','full','dialog','utility','toolbar','hiddenTask']);
for (const w of [normal,mini,max,full,dialog,utility,toolbar,hiddenTask]) {
    assert.equal(w.output,upper); assert.deepEqual(w.desktops,[deskA]);
}
assert.equal(mini.minimized,true); assert.equal(full.fullScreen,true);
assert.equal(max.maximizedHorizontally,true); assert.equal(max.maximizedVertically,true);
assert.deepEqual(normal.activities,['work']); assert.equal(workspace.windowList().length,25);
assert.match(logs[0],/requested=8 moved=8 remaining=0 errors=0/);
""")

    def test_exact_identity_does_not_exclude_similarly_named_apps(self):
        self.js(r"""
const a=make('app',{resourceClass:'pocketds-keyboard-notes'});
run(); assert.equal(a.output,upper); assert.deepEqual(sent,['app']);
""")

    def test_parent_child_desktops_are_snapshotted_before_recursive_transfer(self):
        self.js(r"""
const parent=make('parent',{desktops:[deskA,deskB]}),
    child=make('child',{normalWindow:false,dialog:true,transientFor:parent,desktops:[deskB]}),
    sibling=make('sibling',{output:external,desktops:[]});
windows=[child,parent,sibling]; // enumeration need not be topological
run();
assert.equal(parent.output,upper); assert.equal(child.output,upper);
assert.deepEqual(parent.desktops,[deskA,deskB]); assert.deepEqual(child.desktops,[deskB]);
assert.deepEqual(sibling.desktops,[]); assert.equal(sibling.output,external);
assert.equal(sent.filter(name=>name==='child').length,1); // native recursion, no redundant direct move
assert.equal(sent.filter(name=>name==='parent').length,1);
""")

    def test_lower_modal_restores_the_already_upper_parent_changed_by_native_setter(self):
        self.js(r"""
const parent=make('parent',{output:upper,desktops:[deskA,deskB]}),
    modal=make('modal',{normalWindow:false,dialog:true,modal:true,transientFor:parent,desktops:[deskA,deskB]});
windows=[modal,parent];
run();
assert.deepEqual(sent,['modal']); assert.equal(parent.output,upper);
assert.deepEqual(parent.desktops,[deskA,deskB]); assert.deepEqual(modal.desktops,[deskA,deskB]);
assert.ok(writes.includes('parent')); // parent really changed, not a vacuous fixture
""")

    def test_restore_runs_after_partial_exception_and_other_apps_continue(self):
        self.js(r"""
const bad=make('bad',{failAfterMove:true}), next=make('next',{desktops:[]});
run(); assert.equal(bad.output,upper); assert.equal(next.output,upper);
assert.deepEqual(bad.desktops,[deskA]); assert.deepEqual(next.desktops,[]);
assert.match(logs[0],/errors=1/);
""")

    def test_rules_can_refuse_a_move_without_rewriting_geometry_or_activation(self):
        self.js(r"""
const fixed=make('fixed',{refuse:true}), normal=make('normal');
run(); assert.equal(fixed.output,lower); assert.equal(normal.output,upper);
assert.match(logs[0],/requested=2 moved=1 remaining=1 errors=0/);
""")

    def test_protected_overlay_cannot_be_moved_indirectly_through_parent(self):
        self.js(r"""
const parent=make('parent'), overlay=make('keyboard',{transientFor:parent,resourceClass:'pocketds-keyboard'});
run(); assert.deepEqual(sent,[]); assert.equal(parent.output,lower); assert.equal(overlay.output,lower);
""")

    def test_missing_output_and_new_windows_after_snapshot_are_not_persistently_managed(self):
        self.js(r"""
const a=make('a'); workspace.screens=[lower,external]; run(); assert.deepEqual(sent,[]);
workspace.screens=[upper,lower,external]; run();
const later=make('later'); assert.equal(later.output,lower); assert.deepEqual(sent,['a']);
""")


if __name__ == '__main__':
    unittest.main()

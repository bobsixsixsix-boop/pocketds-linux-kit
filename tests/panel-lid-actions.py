#!/usr/bin/env python3
"""Exercise production lid-mode QML and panelctl with no session or power actions.

QML functions, bindings, and asynchronous handlers run in a Node VM with a fake
clock and executable data source. The real C++ dispatcher only sees a temporary
HOME containing a harmless helper that prints its arguments.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "components/control-panel/pocketds-panelctl.cpp"
QML = (ROOT / "components/control-panel/plasmoid/contents/ui/main.qml").read_text()


def body_after(text: str, marker: str) -> str:
    """Extract an actual handler, preserving nested blocks and quoted braces."""
    start = text.index("{", text.index(marker))
    depth, quote, comment, escaped = 0, "", "", False
    for index in range(start, len(text)):
        char = text[index]
        pair = text[index:index + 2]
        if comment:
            if comment == "//" and char == "\n":
                comment = ""
            elif comment == "/*" and text[index - 1:index + 1] == "*/":
                comment = ""
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if pair in ("//", "/*"):
            comment = pair
        elif char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:index]
    raise AssertionError(f"unclosed QML block: {marker}")


def binding(name: str) -> str:
    match = re.search(r"^    readonly property \w+ " + name + r": (.*(?:\n        .*)*)", QML, re.M)
    if match is None:
        raise AssertionError(f"missing production binding: {name}")
    return match[1]


FUNCTIONS = QML[QML.index("    function clamp("):QML.index("    P5Support.DataSource {")]
HANDLER = body_after(QML, "onNewData: function(sourceName, data)")
TICK = body_after(QML[QML.index("id: refreshTimer"):], "onTriggered:")
BINDINGS = {name: binding(name) for name in (
    "lidModeHealthy", "lidModeWritable", "lidAutoPoweroffHealthy", "lidAutoPoweroffWritable",
)}

HARNESS = r"""
const assert = require('assert/strict');
const vm = require('vm');
let now = 100000;
const connected = [], disconnected = [];
const c = vm.createContext({
  Date: class extends Date { static now() { return now; } },
  Qt: {formatTime: () => '12:00'}, console,
  pendingActions: {}, actionErrors: {},
  lidMode: 'unknown', lidSleepAvailable: false, lidSleepReason: 'unavailable',
  lidOpen: false, lidModeValid: false, lidModeSampleMs: 0, lidModeSampleRequestMs: 0,
  lidModeAgeMs: -1, lidModeRequestMs: 0, lidModeAttemptMs: 0,
  panelctlPath: '/fixture/panelctl', lidModeCommand: '/fixture/panelctl lid-mode status',
  statusCommand: '/fixture/panelctl status', bootSwitchCommand: '/fixture/panelctl boot-android CONFIRM',
  actionCommandTimeoutMs: 10000, actionConfirmationTimeoutMs: 10000,
  telemetryHealthy: true, telemetryAgeMs: 0, statusSchemaValid: true, statusReady: true,
  lidAutoPoweroffTelemetryValid: true, lidAutoPoweroffStatus: 'ok', lidAutoPoweroffMinutes: 60,
  panelExpanded: false, statusRequestInFlight: false, statusRequestStartedMs: 0,
  statusTimeoutMs: 10000, lastStatusMs: now,
  previousCpuTotal: 0, previousCpuIdle: 0, previousRx: 0, previousTx: 0,
  previousSampleMs: 0, quotaMaximumAgeS: 360,
  executable: {connectSource: source => connected.push(source),
               disconnectSource: source => disconnected.push(source)},
  disconnectSource: source => disconnected.push(source),
  statusConfirmationTimer: {restart: () => {}},
});
c.root = c;
vm.runInContext(FUNCTIONS, c);
for (const [key, value] of Object.entries(BINDINGS)) {
  Object.defineProperty(c, key, {get: () => vm.runInContext(value, c)});
}
vm.runInContext('function newData(sourceName, data) {' + HANDLER + '\n}\n' +
                'function tick() {' + TICK + '\n}', c);
// Other status polling is outside these isolated timer tests.
c.requestStatus = () => {};
const sample = (extra = {}) => ({mode:'connected', sleep_available:true,
                               reason_code:'ready', lid_open:true, ...extra});
const deliver = (value, code=0) => c.newData(c.lidModeCommand,
  {stdout: typeof value === 'string' ? value : JSON.stringify(value), 'exit code':code});
const prime = (extra = {}) => {
  c.requestLidModeStatus(); now += 1; deliver(sample(extra));
};
const complete = (code = 0) => {
  const command = c.pendingActions['lid-mode'].command;
  now += 1; c.newData(command, {stdout:'', 'exit code':code});
};
const generalSample = {cpu_total:100,cpu_idle:60,cpu_ghz:2,cpu_cores:8,rx_bytes:0,tx_bytes:0,
  volume:42,muted:false,top_brightness:50,bottom_brightness:60,brightness_write_status:'ok',
  power_profile:'balanced',quota:null,fan_status:'unavailable',fan_sample_age_ms:null,
  fan_percent:null,fan_profile:'unavailable',temp_c:null};
"""


@unittest.skipUnless(shutil.which("node"), "Node.js needed for production QML behavior")
class QmlLidTests(unittest.TestCase):
    def js(self, script: str):
        preamble = "const FUNCTIONS=" + json.dumps(FUNCTIONS) + ";\n"
        preamble += "const HANDLER=" + json.dumps(HANDLER) + ";\n"
        preamble += "const TICK=" + json.dumps(TICK) + ";\n"
        preamble += "const BINDINGS=" + json.dumps(BINDINGS) + ";\n"
        result = subprocess.run(["node", "-e", preamble + HARNESS + script],
                                capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lid_schema_failure_does_not_disable_unrelated_status(self):
        self.js(r"""
for (const bad of [null, [], {}, {mode:'sleep'},
    sample({mode:'garbage'}), sample({sleep_available:1}),
    sample({reason_code:'garbage'}), sample({lid_open:0}),
    sample({lid_open:undefined}), '{', '[]', '', 'x'.repeat(2048)]) {
  prime(); assert(c.lidModeHealthy);
  c.requestLidModeStatus(); now += 1; deliver(bad);
  assert.equal(c.lidModeHealthy, false);
  const before = connected.length;
  assert.equal(c.chooseLidMode('sleep'), false);
  assert.equal(connected.length, before);
  assert(c.applyStatus(generalSample));
  assert(c.statusSchemaValid && c.volumeTelemetryValid && c.powerTelemetryValid);
}
// A valid independent lid reply also survives unavailable general telemetry.
c.telemetryHealthy=false;
prime(); assert(c.lidModeHealthy && c.lidModeWritable);
assert.equal(c.telemetryHealthy, false);
""")

    def test_query_failure_timeout_and_stale_sample_block_sleep(self):
        self.js(r"""
for (const code of [1, 127, undefined, 'bad']) {
  prime(); c.requestLidModeStatus(); now += 1;
  c.newData(c.lidModeCommand, {stdout:JSON.stringify(sample()), 'exit code':code});
  assert.equal(c.lidModeHealthy, false);
  assert.equal(c.chooseLidMode('sleep'), false);
}
prime(); c.requestLidModeStatus();
const request = c.lidModeRequestMs;
c.requestLidModeStatus(); assert.equal(c.lidModeRequestMs, request);
now = request + 10000; c.tick();
assert.equal(c.lidModeRequestMs, 0);
assert(disconnected.includes(c.lidModeCommand));
assert.equal(c.chooseLidMode('sleep'), false);
prime(); now=c.lidModeSampleMs+15000; c.tick();
assert.equal(c.lidModeHealthy, false);
assert.equal(c.chooseLidMode('sleep'), false);
prime(); now=c.lidModeSampleMs-1; c.tick();
assert.equal(c.lidModeHealthy, false);
assert.equal(c.chooseLidMode('sleep'), false);
""")

    def test_unavailable_sleep_closed_lid_and_invalid_click_never_send_action(self):
        self.js(r"""
for (const reason_code of ['kernel_unverified','not_enabled','previous_cycle_failed',
     'guard_unavailable','suspend_denied','unsupported_sleep_mode','config_unavailable','query_failed']) {
  prime({sleep_available:false, reason_code});
  const before=connected.length;
  assert.equal(c.chooseLidMode('sleep'), false);
  assert.equal(connected.length, before);
  assert(c.lidSleepUnavailableText().length > 0);
}
for (const lid_open of [false,null]) {
  prime({lid_open}); const before=connected.length;
  assert.equal(c.chooseLidMode('sleep'), false);
  assert.equal(c.chooseLidMode('connected'), false);
  assert.equal(connected.length, before);
}
prime(); const before=connected.length;
for (const mode of ['', 'SLEEP', 'sleep; shutdown', null])
  assert.equal(c.chooseLidMode(mode), false);
assert.equal(connected.length, before);
""")

    def test_unknown_mode_can_be_repaired_to_connected(self):
        self.js(r"""
prime({mode:'unknown',sleep_available:false,reason_code:'config_unavailable'});
assert.equal(c.presentedLidMode(), 'unknown');
assert(c.chooseLidMode('connected'));
assert.equal(connected.at(-1), c.panelctlPath+' lid-mode connected');
assert.equal(c.presentedLidMode(), 'connected');
assert.equal(c.lidMode, 'unknown');
""")

    def test_general_status_and_old_lid_sample_cannot_confirm_action(self):
        self.js(r"""
// Re-applying the already selected mode must still await a new backend read.
prime(); assert(c.chooseLidMode('connected'));
c.confirmPendingActions(); assert(c.actionPending('lid-mode'));
complete(); assert(c.actionPending('lid-mode'));
assert(c.applyStatus(generalSample));
assert(c.actionPending('lid-mode'));
assert(c.applyLidModeStatus(sample(), c.pendingActions['lid-mode'].commandCompletedMs-1));
assert(c.actionPending('lid-mode'));
assert(c.applyLidModeStatus(sample(), c.pendingActions['lid-mode'].commandCompletedMs));
assert(c.actionPending('lid-mode'));
now += 1;
assert(c.applyLidModeStatus(sample(), now));
assert.equal(c.actionPending('lid-mode'), false);
""")

    def test_query_started_before_command_completion_triggers_fresh_read(self):
        self.js(r"""
prime(); assert(c.chooseLidMode('sleep'));
now += 1; c.requestLidModeStatus();
const oldRequest=c.lidModeRequestMs;
complete(); assert.equal(c.lidModeRequestMs, oldRequest);
now += 1; deliver(sample({mode:'sleep'}));
assert(c.actionPending('lid-mode'));
assert(c.lidModeRequestMs > c.pendingActions['lid-mode'].commandCompletedMs);
now += 1; deliver(sample({mode:'sleep'}));
assert.equal(c.actionPending('lid-mode'), false);
assert.equal(c.presentedLidMode(), 'sleep');
""")

    def test_pending_timeout_and_command_failure_leave_real_state_visible(self):
        self.js(r"""
prime(); assert(c.chooseLidMode('sleep'));
assert.equal(c.chooseLidMode('sleep'), false);
const actionCommand=c.pendingActions['lid-mode'].command;
const deadline=c.pendingActions['lid-mode'].deadlineMs;
c.expirePendingActions(deadline);
assert.equal(c.actionPending('lid-mode'), false);
assert(disconnected.includes(actionCommand));
assert.equal(c.presentedLidMode(), 'connected');
assert(c.actionError('lid-mode').length > 0);
prime(); assert(c.chooseLidMode('sleep')); complete(1);
assert.equal(c.actionPending('lid-mode'), false);
assert.equal(c.presentedLidMode(), 'connected');
assert(c.actionError('lid-mode').length > 0 && c.lidModeRequestMs > 0);
prime(); assert(c.chooseLidMode('sleep')); complete();
now += 1; deliver(sample({mode:'connected'}));
assert(c.actionPending('lid-mode'));
c.expirePendingActions(c.pendingActions['lid-mode'].deadlineMs);
assert.equal(c.actionPending('lid-mode'), false);
assert.equal(c.presentedLidMode(), 'connected');
""")

    def test_autooff_and_lid_mode_are_mutually_exclusive_at_action_boundary(self):
        self.js(r"""
const autooff=()=>c.beginAction('standby','lid-auto-poweroff 120',{kind:'minutes',value:120});
prime(); assert(c.chooseLidMode('sleep'));
let before=connected.length;
assert.equal(autooff(), false); assert.equal(connected.length,before);
c.removePendingAction('lid-mode','');
assert(autooff()); before=connected.length;
assert.equal(c.chooseLidMode('sleep'), false); assert.equal(connected.length,before);
c.removePendingAction('standby','');
for (const mode of ['sleep','unknown']) {
  prime({mode}); before=connected.length;
  assert.equal(autooff(), false); assert.equal(connected.length,before);
}
prime(); c.lidModeValid=false; before=connected.length;
assert.equal(autooff(), false); assert.equal(connected.length,before);
prime(); assert(autooff());
""")


class PanelctlLidTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="pds-panel-lid-")
        cls.base = Path(cls.workspace.name)
        cls.binary = cls.base / "panelctl"
        flags = ["-Dst_mtim=st_mtimespec"] if sys.platform == "darwin" else []
        subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", *flags,
                        str(SOURCE), "-o", str(cls.binary)], check=True, timeout=45)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=self.base, prefix="home with spaces ")
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.helper = self.home / ".local/libexec/pocketds/pocketds-lid-mode"
        self.helper.parent.mkdir(parents=True)
        self.env = dict(os.environ, HOME=str(self.home), PATH="/nonexistent")

    def call(self, *args, env=None):
        return subprocess.run([str(self.binary), "lid-mode", *args],
                              env=self.env if env is None else env,
                              capture_output=True, text=True, timeout=3)

    def install_fixture(self, exit_code=0):
        self.helper.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\nexit " + str(exit_code) + "\n")
        self.helper.chmod(0o700)

    def test_only_exact_subcommands_reach_helper(self):
        self.install_fixture()
        for args in ((), ("" ,), ("set", "sleep"), ("sleep", "extra"),
                     ("Sleep",), ("sleep; false",), ("connected\n",), ("--help",)):
            with self.subTest(args=args):
                result = self.call(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
        for value, expected in (("status", "status\n"), ("sleep", "set\nsleep\n"),
                                ("connected", "set\nconnected\n")):
            with self.subTest(value=value):
                result = self.call(value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

    def test_missing_home_and_helper_report_failure(self):
        for value in ("status", "sleep", "connected"):
            with self.subTest(value=value):
                result = self.call(value)
                self.assertEqual(result.returncode, 1)
                self.assertIn("unavailable", result.stderr)
        for home in (None, ""):
            env = dict(self.env)
            if home is None:
                del env["HOME"]
            else:
                env["HOME"] = home
            self.assertEqual(self.call("status", env=env).returncode, 1)

    def test_unsafe_helper_is_not_executed_or_waited_on(self):
        self.install_fixture()
        for mode in (0o600, 0o722, 0o777):
            self.helper.chmod(mode)
            result = self.call("status")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
        self.helper.unlink()
        target = self.home / "target"
        target.write_text("#!/bin/sh\necho should-not-run\n")
        target.chmod(0o700)
        self.helper.symlink_to(target)
        self.assertEqual(self.call("status").returncode, 1)
        self.helper.unlink()
        os.mkfifo(self.helper, 0o700)
        self.assertEqual(self.call("status").returncode, 1)

    def test_helper_failure_propagates_to_ui_caller(self):
        self.install_fixture(exit_code=17)
        self.assertEqual(self.call("sleep").returncode, 17)


class InstallerLidPreferencesTests(unittest.TestCase):
    def run_install_branch(self, *, existing=None, token=None):
        installer = (ROOT / "scripts/install.sh").read_text()
        start = installer.index("    if [[ -f $HOME/.config/powerdevilrc ]]; then")
        end = installer.index("    if systemctl --user is-active --quiet plasma-powerdevil.service", start)
        branch = installer[start:end]
        with tempfile.TemporaryDirectory(prefix="pds-lid-install-") as directory:
            fixture = Path(directory)
            config = fixture / ".config/powerdevilrc"
            config.parent.mkdir()
            if existing is not None:
                config.write_bytes(existing)
                config.chmod(0o600)
            token_path = fixture / "enabled-token"
            if token is not None:
                token_path.write_bytes(token)
            commands = r'''
set -euo pipefail
install_user_file() {
    [[ "$2" == "$fixture_home/.config/powerdevilrc" ]]
    printf '%s\n' "$1" >> "$fixture_home/install-calls"
    cp "$1" "$2"
}
sudo() {
    if [[ "$#" == 3 && "$1" == test && "$2" == -f &&
          "$3" == /etc/pocketds-linux-kit/daily-suspend.enabled ]]; then
        [[ -f "$fixture_token" ]]
    elif [[ "$#" == 4 && "$1" == cmp && "$2" == -s &&
            "$4" == /etc/pocketds-linux-kit/daily-suspend.enabled ]]; then
        cmp -s "$3" "$fixture_token"
    else
        echo 'unexpected installer privileged action' >&2
        return 99
    fi
}
''' + branch
            result = subprocess.run(["bash", "-c", commands], capture_output=True,
                                    text=True, timeout=3,
                                    env=dict(os.environ, HOME=str(fixture),
                                             fixture_home=str(fixture),
                                             fixture_token=str(token_path), repo_root=str(ROOT)))
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = fixture / "install-calls"
            return config.read_bytes(), config.stat().st_mode & 0o777, \
                calls.read_text().splitlines() if calls.exists() else []

    def test_existing_preferences_survive_upgrade_byte_for_byte(self):
        content = (b"# user preferences\r\n[AC][HandleButtonEvents]\r\n"
                   b"lidAction=1\r\npowerButtonAction=8\r\n"
                   b"[Battery][SuspendSession]\r\nidleTime=56789\r\n")
        enabled = (ROOT / "components/system/daily-suspend.enabled").read_bytes()
        for token in (None, enabled, b"invalid-token\n"):
            with self.subTest(token_present=token is not None):
                actual, mode, calls = self.run_install_branch(existing=content, token=token)
                self.assertEqual(actual, content)
                self.assertEqual(mode, 0o600)
                self.assertEqual(calls, [])

    def test_first_install_uses_connected_default_without_exact_token(self):
        default = ROOT / "components/system/powerdevilrc"
        for token in (None, b"invalid-token\n"):
            actual, _, calls = self.run_install_branch(token=token)
            self.assertEqual(actual, default.read_bytes())
            self.assertEqual(calls, [str(default)])

    def test_first_install_uses_deep_defaults_with_enabled_token(self):
        deep = ROOT / "components/system/powerdevilrc.deep"
        token = (ROOT / "components/system/daily-suspend.enabled").read_bytes()
        actual, _, calls = self.run_install_branch(token=token)
        self.assertEqual(actual, deep.read_bytes())
        self.assertEqual(calls, [str(deep)])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Test real QML session/close lifecycle in an isolated, hardware-free Plasma host."""
from pathlib import Path
import json
import os
import selectors
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = shutil.which('plasmawindowed')
if not PROGRAM:
    print('SKIP: native controller test requires Plasma/Qt on Linux')
    raise SystemExit(0)

with tempfile.TemporaryDirectory(prefix='pds-controller-native-') as temporary:
    base = Path(temporary)
    package = base / 'share/plasma/plasmoids/org.pocketds.controllertest'
    ui = package / 'contents/ui'; ui.mkdir(parents=True)
    source = ROOT / 'components/control-panel/plasmoid'
    for name in ['ControllerDiagram.qml', 'ControllerTestSession.qml']:
        shutil.copy2(source / 'contents/ui' / name, ui / name)
    shutil.copy2(ROOT / 'tests/fixtures/controller-test-session-checks.js', ui / 'native-checks.js')
    metadata = json.loads((source / 'metadata.json').read_text())
    metadata['KPlugin']['Id'] = 'org.pocketds.controllertest'
    (package / 'metadata.json').write_text(json.dumps(metadata))
    qml = (source / 'contents/ui/main.qml').read_text()
    a = qml.index('    P5Support.DataSource {'); b = qml.index('    component Meter:', a)
    qml = qml[:a] + '''    Item {
        id:executable
        function connectSource(command) { throw new Error("External action forbidden in native test: " + command) }
        function disconnectSource(command) {}
    }
    Timer {id:statusConfirmationTimer}
''' + qml[b:]
    qml = qml.replace('import QtQuick\n', 'import QtQuick\nimport QtTest\nimport "native-checks.js" as NativeChecks\n', 1)
    qml = qml.replace('Math.max(width, root.screenGeometry.width)', '819').replace('Math.max(height, root.screenGeometry.height)', '614')
    qml = qml.replace('            id: controllerSession\n', '            id: controllerSession\n            transport:controllerTransport\n', 1)
    fixture = (ROOT / 'tests/fixtures/controller-test-native-driver.qml.inc').read_text()
    qml = qml.replace('        id: fullRoot\n', '        id: fullRoot\n' + fixture, 1)
    (ui / 'main.qml').write_text(qml)
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_FORCE_STDERR_LOGGING='1', QT_LOGGING_RULES='qml.debug=true',
               QT_QUICK_CONTROLS_STYLE='org.kde.desktop', QT_QPA_PLATFORMTHEME='kde',
               XDG_DATA_HOME=str(base/'share'), XDG_CONFIG_HOME=str(base/'config'),
               QT_LINUX_ACCESSIBILITY_ALWAYS_ON='0')
    log = base/'qt.log'
    with log.open('w') as output:
        process = subprocess.Popen([PROGRAM, 'org.pocketds.controllertest'], env=env,
                                   stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic()+20
            while process.poll() is None and time.monotonic()<deadline:
                text=log.read_text()
                if 'CONTROLLER_COMPLETE' in text or any(s in text for s in ['ReferenceError', 'SyntaxError', 'TypeError', 'Cannot assign', 'Error: ']):
                    break
                time.sleep(.1)
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=3)
    text=log.read_text()
    events=[line.split('CONTROLLER_TEST ',1)[1] for line in text.splitlines() if 'CONTROLLER_TEST ' in line]
    assert len(events)==20 and all(event.endswith(' true') for event in events),text
    assert 'CONTROLLER_COMPLETE' in text,text
    assert not any(s in text for s in ['ReferenceError','Binding loop','Cannot assign','SyntaxError','TypeError','External action forbidden']),text
    print('20 native QML controller-session event checks passed (mock transport, no hardware action)')

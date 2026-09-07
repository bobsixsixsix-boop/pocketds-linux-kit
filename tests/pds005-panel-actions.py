#!/usr/bin/env python3
"""Static and compile-time contract for touch-safe PDS-005 Panel actions."""

from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
PANEL = (ROOT / "components/control-panel/pocketds-panelctl.cpp").read_text(
    encoding="utf-8"
)
QML = (
    ROOT / "components/control-panel/plasmoid/contents/ui/main.qml"
).read_text(encoding="utf-8")
RECOVERY = (ROOT / "scripts/pocketds-plasma-recovery.py").read_text(
    encoding="utf-8"
)
KEYBOARD = (ROOT / "components/keyboard/pocketds-keyboard.py").read_text(
    encoding="utf-8"
)


with tempfile.TemporaryDirectory(prefix="pds005-panel-actions-") as temporary:
    binary = Path(temporary) / "pocketds-panelctl"
    subprocess.run(
        ["c++", "-std=c++17", "-Wall", "-Wextra"]
        + (["-Dst_mtim=st_mtimespec"] if sys.platform == "darwin" else [])
        + [str(ROOT / "components/control-panel/pocketds-panelctl.cpp"), "-o", str(binary)],
        check=True,
        timeout=30,
    )
    for args in (
        ("desktop-recover",),
        ("desktop-recover", "yes"),
        ("desktop-recovery-clear-stale",),
        ("desktop-recovery-clear-stale", "yes"),
        ("boot-android",),
        ("boot-android", "yes"),
        ("boot-android", "CONFIRM", "extra"),
        ("lid-auto-poweroff", "30"),
        ("lid-auto-poweroff", "60x"),
    ):
        result = subprocess.run(
            [str(binary), *args], capture_output=True, text=True, timeout=5, check=False
        )
        assert result.returncode == 2, (args, result.returncode, result.stderr)

for token in (
    '"desktop-recover"',
    '"desktop-recovery-clear-stale"',
    '"boot-android"',
    '"CONFIRM"',
    '"--confirm-unit", "plasma-plasmashell.service"',
    '"--confirm-action", "BOUNDED-PLASMASHELL-RECOVERY"',
    '"--clear-stale-request"',
):
    assert token in PANEL, token

for token in (
    '"/usr/bin/busctl --timeout=1s get-property',
    '"/usr/bin/busctl", "--user", "call"',
    '"org.kde.kglobalaccel", "/component/kwin"',
    '"org.kde.kglobalaccel.Component", "invokeShortcut"',
    '"s", "Window Fullscreen"',
):
    assert token in PANEL, token

for token in ('"input-toggle"', '"/usr/bin/pocketds-toggle-joymouse"'):
    assert token in PANEL, token

for token in ('std::strcmp(argv[1], "touchpad")', '"pocketds-touchpad.service"'):
    assert token in PANEL, token

for token in ('show_overlay', '"--signal=HUP"'):
    assert token in PANEL, token

for token in (
    'std::strcmp(argv[1], "lid-auto-poweroff")',
    '"configure-auto-poweroff"',
    '"0" || text == "60" || text == "120"',
    '"240" || text == "480"',
):
    assert token in PANEL, token

for forbidden in (
    "systemctl --user restart plasma-plasmashell",
    "kwin_wayland --replace",
    "killall",
    "pkill",
):
    assert forbidden not in PANEL, forbidden

for field in (
    "recovery_status",
    "recovery_request_status",
    "recovery_request_id",
    "recovery_error",
    "input_mode",
    "lid_auto_poweroff_minutes",
    "lid_auto_poweroff_status",
    "brightness_write_status",
):
    assert f'\\"{field}\\"' in PANEL, field

for token in (
    'root.exec("desktop-recover CONFIRM")',
    'root.exec("desktop-recovery-clear-stale CONFIRM")',
    'root.bootSwitchCommand',
    'root.beginAndroidBoot()',
    'root.beginAction("input", "input-toggle"',
    'root.exec("touchpad")',
    'function pendingExpectedValue(domain, fallback)',
    'id: confirmRecovery',
    'id: androidBootDialog',
    'enabled: !root.recoveryPending && root.recoveryRequestStatus === "none"',
    'enabled: !root.bootSwitchPending',
    'brightnessWriteStatus === "ok"',
    'text: root.brightnessWriteStatus === "unavailable"',
    'root.inputModeShortText()',
    'onClicked: shortcutDialog.open()',
    'requested: shortcutDialog.visible && fullRoot.visible',
    'buttons: controllerSession.buttons',
    'axes: controllerSession.axes',
    'root.beginAction("standby", "lid-auto-poweroff 60"',
    'id: standbyDialog',
    'controlHealthy: root.lidAutoPoweroffWritable',
):
    assert token in QML, token

# Preserve the current action contract without freezing explanatory UI copy
# from the old static controller-help page.
for minutes in (0, 60, 120, 240, 480):
    assert f'root.beginAction("standby", "lid-auto-poweroff {minutes}"' in QML

assert "running: root.panelExpanded || root.statusRequestInFlight ||" in QML
assert "root.pendingActionCount() > 0" in QML
assert 'if (recoveryRequestStatus !== "pending")' in QML
assert "implicitWidth: 819" in QML
assert "implicitHeight: 614" in QML
assert "surfaceWidth: Math.max(width, root.screenGeometry.width)" in QML
assert "surfaceHeight: Math.max(height, root.screenGeometry.height)" in QML
assert "width: fullRoot.surfaceWidth" in QML
assert "height: fullRoot.surfaceHeight" in QML
assert "component MetricItem: Rectangle" in QML
assert "showDivider: false" in QML
assert "component PrimaryButton: QQC2.Button" in QML
assert "component HeaderAction: QQC2.Button" in QML
assert "component TelemetryLine: Item" in QML
for label in ("键盘与语音", "触摸板", "实体按键", "切到安卓"):
    assert f'text: "{label}"' in QML
assert 'text: "返回桌面"' not in QML
assert "root.panelExpanded = false" not in QML
assert 'text: "全屏"' not in QML
assert 'text: "恢复"' not in QML
assert 'text: "风扇"' in QML
for fine_print in (
    'text: "HINGE CONSOLE"',
    'text: "UPPER DISPLAY"',
    'text: "HINGE MODE RAIL"',
    'text: "FAN RESPONSE"',
    'text: "直接调到顺手"',
    'text: root.powerModeDescription()',
    'text: root.fanModeDescription()',
    'text: root.displayFpsDetailText()',
    'text: root.gameFpsLimitSummary()',
    'text: root.lidAutoPoweroffSummary()',
):
    assert fine_print not in QML, fine_print
assert "fanSlider" not in QML
assert '"PWM "' not in QML
assert "readonly property bool telemetryHealthy:" in QML
for token in (
    "readonly property bool fanHealthy:",
    "readonly property bool topBrightnessHealthy:",
    "readonly property bool bottomBrightnessHealthy:",
    "readonly property bool volumeHealthy:",
    "readonly property bool powerHealthy:",
    "readonly property bool lidAutoPoweroffHealthy:",
    "readonly property bool inputHealthy:",
    "readonly property bool batteryHealthy:",
    "enabled: controlHealthy && !actionPending",
    "enabled: controlHealthy && !pending",
):
    assert token in QML, token
assert 'text: "键盘与语音"' in QML
assert "Behavior on width" not in QML
assert "STALE_CLEAR_MIN_AGE_S = 90.0" in RECOVERY
assert "recovery worker is still active; stale request was not removed" in RECOVERY
assert "st_nlink != 1" in RECOVERY

for token in (
    '"ui_task_switcher": "Walk Through Windows"',
    '"ui_window_close": "Window Close"',
    '"ui_window_fullscreen": "Window Fullscreen"',
    '"ui_window_next_screen": "Window to Next Screen"',
    '"org.kde.kglobalaccel.Component"',
    "interface.invokeShortcut(shortcut)",
):
    assert token in KEYBOARD, token

print("  [OK] Panel actions are closed, confirmed, reversible and KWin-native")

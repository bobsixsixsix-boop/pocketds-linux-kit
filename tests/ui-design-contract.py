#!/usr/bin/env python3
"""Executable interaction and static cohesion contract for the lower-screen UI."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
QML = (
    ROOT / "components/control-panel/plasmoid/contents/ui/main.qml"
).read_text(encoding="utf-8")
KEYBOARD = (
    ROOT / "components/keyboard/pocketds-keyboard.py"
).read_text(encoding="utf-8")


def relative_luminance(hex_color: str) -> float:
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92
        if value <= 0.04045
        else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (relative_luminance(foreground), relative_luminance(background)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


class ActionCoordinatorModel:
    """Executable reference for the timing invariants implemented in QML."""

    status_interval_ms = 2_000
    status_retry_cap_ms = 10_000
    command_timeout_ms = 10_000
    confirmation_timeout_ms = 10_000

    def __init__(self) -> None:
        self.panel_expanded = True
        self.status_request_in_flight = False
        self.status_failure_count = 0
        self.next_status_attempt_ms = 0
        self.pending: dict[str, dict[str, object]] = {}

    @property
    def has_pending_confirmation(self) -> bool:
        return any(bool(action["command_complete"]) for action in self.pending.values())

    @property
    def status_loop_running(self) -> bool:
        return (
            self.panel_expanded
            or self.status_request_in_flight
            or bool(self.pending)
        )

    def begin_action(self, domain: str, now_ms: int) -> None:
        self.pending[domain] = {
            "command_complete": False,
            "deadline_ms": now_ms + self.command_timeout_ms,
        }

    def complete_action(self, domain: str, now_ms: int) -> None:
        action = self.pending[domain]
        action["command_complete"] = True
        action["deadline_ms"] = now_ms + self.confirmation_timeout_ms

    def request_status(self, now_ms: int, *, force: bool = False) -> bool:
        priority = force or self.has_pending_confirmation
        if (
            (not self.panel_expanded and not self.pending)
            or self.status_request_in_flight
            or (now_ms < self.next_status_attempt_ms and not priority)
        ):
            return False
        self.status_request_in_flight = True
        return True

    def status_failure(self, now_ms: int) -> None:
        self.status_request_in_flight = False
        self.status_failure_count = min(self.status_failure_count + 1, 8)
        retry = min(
            self.status_retry_cap_ms,
            self.status_interval_ms * 2 ** (self.status_failure_count - 1),
        )
        self.next_status_attempt_ms = now_ms + retry

    def expire(self, now_ms: int) -> list[str]:
        expired = [
            domain
            for domain, action in self.pending.items()
            if now_ms >= int(action["deadline_ms"])
        ]
        for domain in expired:
            del self.pending[domain]
        return expired


class PaletteTests(unittest.TestCase):
    def test_hinge_console_uses_its_deliberate_mat_graphite_palette(self) -> None:
        panel = {
            "pageColor": "#090c0d",
            "surfaceColor": "#151a1b",
            "cardBorder": "#303839",
            "textColor": "#f3f0e9",
            "mutedColor": "#899392",
            "accentColor": "#62d8d5",
            "performanceColor": "#ff6a2b",
        }
        for name, value in panel.items():
            self.assertIn(f'property color {name}: "{value}"', QML)

    def test_text_and_semantic_states_meet_wcag_aa(self) -> None:
        pairs = {
            "text/surface": ("#f3f0e9", "#151a1b"),
            "muted/surface": ("#899392", "#151a1b"),
            "muted/page": ("#899392", "#090c0d"),
            "primary": ("#090c0d", "#62d8d5"),
            "selected": ("#f3f0e9", "#17302f"),
            "keyboard-function": ("#91a79f", "#101d18"),
            "keyboard-action": ("#e8f3ef", "#25392f"),
            "voice-live": ("#fff3ed", "#8b311f"),
        }
        for name, (foreground, background) in pairs.items():
            with self.subTest(name=name):
                self.assertGreaterEqual(contrast(foreground, background), 4.5)


class CoordinatorTimingTests(unittest.TestCase):
    def test_collapsed_panel_keeps_pending_action_and_status_timeout_alive(self) -> None:
        model = ActionCoordinatorModel()
        model.begin_action("brightness", 0)
        model.panel_expanded = False

        self.assertTrue(model.status_loop_running)
        self.assertTrue(model.request_status(1_000))
        self.assertFalse(model.request_status(1_001))
        model.status_failure(2_000)
        self.assertTrue(model.status_loop_running)

    def test_command_completion_starts_a_full_confirmation_window(self) -> None:
        model = ActionCoordinatorModel()
        model.begin_action("fan", 0)
        model.complete_action("fan", 9_000)

        self.assertEqual(model.expire(10_000), [])
        self.assertEqual(model.expire(18_999), [])
        self.assertEqual(model.expire(19_000), ["fan"])

    def test_pending_confirmation_bypasses_only_the_ordinary_backoff(self) -> None:
        model = ActionCoordinatorModel()
        model.begin_action("volume", 0)
        model.complete_action("volume", 100)
        model.panel_expanded = False
        model.status_failure_count = 3
        model.next_status_attempt_ms = 50_000

        self.assertTrue(model.request_status(1_000))
        model.status_request_in_flight = False
        del model.pending["volume"]
        self.assertFalse(model.request_status(1_001))
        self.assertFalse(model.status_loop_running)


class InteractionTests(unittest.TestCase):
    def test_high_frequency_surfaces_do_not_tween(self) -> None:
        self.assertNotIn("Behavior on width", QML)
        self.assertNotIn("transition:", KEYBOARD)
        self.assertNotIn("animation", KEYBOARD)
        self.assertIn("set_transition_type(Gtk.StackTransitionType.NONE)", KEYBOARD)

    def test_panel_press_feedback_is_short_and_property_specific(self) -> None:
        # Busy indicators have their own cycle; this contract covers press feedback.
        press_animations = re.findall(r"Behavior on scale\s*\{\s*NumberAnimation\s*\{([^}]+)\}", QML)
        durations = [int(re.search(r"duration:\s*(\d+)", animation).group(1))
                     for animation in press_animations]
        self.assertEqual(durations, [100, 110])
        self.assertTrue(all(duration <= 160 for duration in durations))
        self.assertEqual(QML.count("Behavior on scale"), 2)

    def test_touch_targets_and_domain_keys_remain_direct(self) -> None:
        for token in (
            "Layout.minimumWidth: 60",
            "Layout.minimumHeight: 54",
            "Layout.minimumHeight: 52",
            "Layout.minimumHeight: 64",
            "implicitHeight: 48",
        ):
            self.assertIn(token, QML)
        self.assertIn('for col, char in enumerate((".", "/"))', KEYBOARD)
        self.assertIn("self.primary_character_button(char)", KEYBOARD)

    def test_system_toolbar_is_large_and_voice_labels_do_not_reflow_grid(self) -> None:
        self.assertIn(
            "def button(self, label, callback, css_class=None, *, vexpand=True):",
            KEYBOARD,
        )
        self.assertIn("button.set_vexpand(vexpand)", KEYBOARD)
        self.assertIn("button.utility", KEYBOARD)
        self.assertIn("min-height: 72px", KEYBOARD)
        self.assertIn('"Alt+F4"', KEYBOARD)
        self.assertIn('"设置"', KEYBOARD)
        self.assertNotIn('"浏览器"', KEYBOARD)
        for token in (
            "frame.feedback-card",
            "button.feedback-preview",
            "min-height: 64px",
            "SOUND_LEVEL_DB = (-30.0, -24.0, -18.0, -12.0, -6.0)",
            "HAPTIC_LEVELS = (",
            "(0.30, 50),",
            "(0.40, 50),",
            "(0.50, 50),",
            "(0.60, 50),",
            "(0.70, 50),",
            "duration_ms=haptic_duration_ms",
        ):
            self.assertIn(token, KEYBOARD)
        self.assertNotIn("FUNCTION_KEYS", KEYBOARD)
        for compact_label in (
            'VoiceState.CONNECTING: "准备中"',
            'VoiceState.STOPPING: "收音中"',
            'VoiceState.PENDING_PASTE: "正在输入"',
            'VoiceState.DISCARDED: "已取消"',
        ):
            self.assertIn(compact_label, KEYBOARD)
        for reflowing_label in ("麦克风准备中", "结束录音中", "准备输入", "已丢弃"):
            self.assertNotIn(reflowing_label, KEYBOARD)

    def test_panel_icons_and_numeric_baselines_are_visually_coherent(self) -> None:
        self.assertIn('text: "上屏"', QML)
        self.assertIn('label: "CODEX"', QML)
        self.assertIn('font.family: root.numericFontFamily', QML)
        self.assertIn("import QtQuick.Shapes", QML)
        self.assertIn("component PanelIcon: Item {", QML)
        self.assertIn("Phosphor Icons Regular (MIT)", QML)
        self.assertIn("PathSvg {", QML)
        for icon_name in (
            "battery-charging",
            "cpu",
            "graphics-card",
            "lightning",
            "network",
            "code",
            "monitor",
            "device-tablet",
            "speaker-high",
            "gauge",
            "fan",
            "moon-stars",
            "keyboard",
            "hand-tap",
            "mouse-simple",
            "game-controller",
        ):
            with self.subTest(icon_name=icon_name):
                self.assertIn(f'"{icon_name}": "M', QML)
        self.assertNotIn('iconName: "applications-development"', QML)
        self.assertNotIn('source: "sensors-fan"', QML)
        self.assertNotIn("Kirigami.Icon", QML)
        self.assertNotIn("icon.name:", QML)
        self.assertEqual(QML.count("Layout.alignment: Qt.AlignBaseline"), 0)
        header_start = QML.index("    component HeaderAction: QQC2.Button {")
        header_end = QML.index("    component PrimaryButton: QQC2.Button {", header_start)
        header_action = QML[header_start:header_end]
        self.assertIn("font.pixelSize: 15", header_action)
        self.assertNotIn("font.pixelSize: 12", header_action)

    def test_panel_text_is_pixel_hinted_centered_and_contained(self) -> None:
        for token in (
            'readonly property string uiFontFamily: "Noto Sans CJK SC"',
            "component PanelText: Text {",
            "font.hintingPreference: Font.PreferFullHinting",
            "renderType: Text.QtRendering",
            "verticalAlignment: Text.AlignVCenter",
            "Layout.alignment: Qt.AlignVCenter",
            'label: "NET"',
            # Compact profile rows and the full-width utility row.
            "Layout.minimumWidth: 94",
            "Layout.minimumWidth: 254",
            "Layout.minimumWidth: 144",
        ):
            self.assertIn(token, QML)
        self.assertGreaterEqual(len(re.findall(r"(?m)^\s*PanelText \{", QML)), 43)
        self.assertEqual(len(re.findall(r"(?m)^\s*ControlLabel \{", QML)), 3)
        self.assertEqual(re.findall(r"(?m)^\s*Text \{", QML), [])
        telemetry_start = QML.index("    component TelemetryLine: Item {")
        telemetry_end = QML.index("    fullRepresentation: Item {", telemetry_start)
        self.assertIn("clip: true", QML[telemetry_start:telemetry_end])

    def test_status_freshness_is_visible_and_guards_hardware_controls(self) -> None:
        for token in (
            "property bool statusReady: false",
            "property bool statusSchemaValid: false",
            "readonly property bool telemetryHealthy:",
            'return telemetryHealthy ? "实时" : "遥测中断"',
            "readonly property bool fanHealthy:",
            "readonly property bool topBrightnessHealthy:",
            "readonly property bool bottomBrightnessHealthy:",
            "readonly property bool topBrightnessWritable:",
            "readonly property bool bottomBrightnessWritable:",
            "readonly property bool volumeHealthy:",
            "readonly property bool powerHealthy:",
            "readonly property bool inputHealthy:",
            "readonly property bool displayHealthy:",
            "readonly property bool quotaHealthy:",
            "readonly property bool batteryHealthy:",
            "readonly property bool systemPowerHealthy:",
            "readonly property bool topFpsHealthy:",
            'return "— FPS"',
            'return "Moonlight 串流 · 实时"',
            'return "当前游戏 · Gamescope"',
            'return "等待上屏游戏"',
            'lines.push("FPS 来自 Moonlight 客户端实际呈现帧率")',
            'lines.push("不等同于远端游戏引擎 FPS")',
        ):
            self.assertIn(token, QML)
        for label in ("键盘与语音", "触摸板", "实体按键"):
            self.assertIn(f'text: "{label}"', QML)
        self.assertNotIn('text: "返回桌面"', QML)
        self.assertNotIn("root.panelExpanded = false", QML)
        self.assertNotIn('text: "全屏"', QML)
        self.assertNotIn('text: "恢复"', QML)
        self.assertIn('text: "性能"', QML)
        self.assertIn('text: "亮度与声音"', QML)
        self.assertIn('"控制服务未就绪"', QML)
        self.assertIn('"控制状态异常"', QML)
        self.assertIn('text: "风扇"', QML)
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
            self.assertNotIn(fine_print, QML)
        self.assertNotIn("fanSlider", QML)
        self.assertNotIn('"PWM "', QML)

    def test_game_fps_limit_is_one_global_touch_control(self) -> None:
        for token in (
            "property int gameFpsLimit: 0",
            "readonly property bool gameFpsLimitWritable:",
            'id: fpsLimitButton',
            'id: fpsLimitPopup',
            'text: "游戏限帧"',
            '"game-limit", "game-limit " + argument',
            'text: root.gameFpsRuntimeActive',
        ):
            self.assertIn(token, QML)
        self.assertIn('{"value": 0, "label": "自动"}', QML)
        for value in (24, 30, 40, 60, 120):
            self.assertIn(f'{{"value": {value}, "label": "{value}"}}', QML)

    def test_connected_lid_standby_is_clear_compact_and_repairable(self) -> None:
        for token in (
            "property int lidAutoPoweroffMinutes: 0",
            "property string lidAutoPoweroffStatus:",
            "function validLidAutoPoweroffFields(s)",
            'if (domain === "standby") return lidAutoPoweroffWritable',
            'lidAutoPoweroffStatus === "ok"',
            'labelText: "合盖"',
            'id: standbyDialog',
            'title: "合盖设置"',
            'text: "联网待机"',
            'text: "休眠"',
            'controlHealthy: root.lidModeWritable && root.lidSleepAvailable',
            'text: "联网待机 · 自动关机"',
            "Wi‑Fi 和后台任务继续运行",
            "仅在电池供电、持续合盖时计时",
            "开盖或充电即取消",
            'text: "不关机"',
            'text: "1 小时"',
            'text: "2 小时"',
            'text: "4 小时"',
            'text: "8 小时"',
            'root.beginAction("standby", "lid-auto-poweroff 480"',
            'return "设置异常 · 点按修复"',
        ):
            self.assertIn(token, QML)
        self.assertNotIn("大核全部关闭", QML)
        self.assertNotIn("GPU 完全休眠", QML)

    def test_status_refresh_is_single_flight_timed_and_backed_off(self) -> None:
        for token in (
            "property bool statusRequestInFlight: false",
            "readonly property int statusTimeoutMs: 5000",
            "readonly property int statusRetryCapMs: 10000",
            "function requestStatus(force)",
            "const pendingCount = pendingActionCount()",
            "const priority = force || hasPendingConfirmation()",
            "if ((!panelExpanded && pendingCount === 0) || statusRequestInFlight ||",
            "(now < nextStatusAttemptMs && !priority)",
            "Math.pow(2, statusFailureCount - 1)",
            "now - root.statusRequestStartedMs >= root.statusTimeoutMs",
            'root.recordStatusFailure("status 返回空数据")',
            'root.recordStatusFailure("status JSON 无效")',
            "running: root.panelExpanded || root.statusRequestInFlight ||",
            "root.pendingActionCount() > 0",
            "root.requestStatus(root.hasPendingConfirmation())",
        ):
            self.assertIn(token, QML)
        self.assertNotIn("refreshTimer.restart()", QML)
        self.assertNotIn('root.exec("status")', QML)

    def test_domain_schemas_fail_closed_without_minimum_value_placeholders(self) -> None:
        for token in (
            "function validStatusEnvelope(s)",
            "function validFanFields(s)",
            "function validBrightnessField(value)",
            "function validVolumeFields(s)",
            "function validPowerFields(s)",
            "function validDisplayFields(s)",
            "function validQuotaFields(quota)",
            "function validBatteryFields(s)",
            "function validSystemPowerFields(s)",
            'if (displayTelemetryValid && displayStatus === "ok")',
            "resetDisplayReadings()",
            "resetBatteryReadings()",
            "resetSystemPowerReadings()",
            "visible: sliderControl.valueVisible",
            "enabled: controlHealthy && !actionPending",
            "color: valid ? root.dividerColor : \"transparent\"",
            "meterValid: root.quotaHealthy &&",
            "!root.batteryHealthy || root.batteryPercent < 0",
            "meterValid: root.systemPowerHealthy",
        ):
            self.assertIn(token, QML)
        for forbidden in (
            "Boolean(quota.available)",
            "Boolean(quota.fresh)",
            "Boolean(s.battery_external_power)",
            "refreshTimer.restart()",
        ):
            self.assertNotIn(forbidden, QML)

    def test_system_power_ui_names_its_measurement_source_and_limits(self) -> None:
        for token in (
            'label: "POWER"',
            'return "USB 输入"',
            'return "电池放电"',
            "包含整机运行与电池充电，不等同于仅 SoC 功耗",
            "USB 输入与电池放电均无可信样本",
            's.system_power_source === "unavailable"',
            "s.system_power_w === null",
            "isNumberBetween(s.system_power_w, 0.01, 200)",
            "component TelemetryLine: Item",
            'text: "风扇"',
        ):
            self.assertIn(token, QML)
        self.assertEqual(QML.count("TelemetryLine {"), 5)
        self.assertNotIn('title: "风扇"', QML)
        self.assertNotIn("valueSize: 22", QML)

    def test_controller_test_owns_input_without_mapping_notes(self) -> None:
        for text in (
            'text: "实体按键"',
            'title: "手柄测试"',
            'requested: shortcutDialog.visible && fullRoot.visible',
            'buttons: controllerSession.buttons',
            'axes: controllerSession.axes',
            'active: controllerSession.active',
            'onClicked: shortcutDialog.close()',
            'onClicked: controllerSession.retry()',
        ):
            self.assertIn(text, QML)
        for removed in (
            "shortcutGuidePage", "按键名称依据", "首次提示", "待确认",
            'text: "查看手柄映射"', 'text: "查看鼠标映射"',
        ):
            self.assertNotIn(removed, QML)

    def test_slider_and_profile_actions_wait_for_backend_truth(self) -> None:
        for token in (
            "when: !sliderControl.userInteraction && !sliderControl.actionPending",
            "restoreMode: Binding.RestoreNone",
            "if (controlHealthy && telemetryValid && !actionPending &&",
            "function beginAction(domain, args, expected)",
            "function confirmPendingActions()",
            "action.commandComplete && actionMatchesTelemetry",
            "function expirePendingActions(now)",
            '"后端未在时限内确认，已回退到遥测值"',
            '"deadlineMs": now + (domain === "lid-mode" ? 25000 : actionCommandTimeoutMs)',
            "current.deadlineMs = completedAt + root.actionConfirmationTimeoutMs",
            "checkable: false",
            "required property bool selected",
            "enabled: controlHealthy && !pending",
        ):
            self.assertIn(token, QML)
        release = QML.index("onPressedChanged:")
        commit = QML.index("committed(candidate)", release)
        release_end = QML.index("userInteraction = false", commit)
        self.assertLess(commit, release_end)
        self.assertNotIn("checkable: true", QML)
        self.assertNotIn("checked: root.", QML)

    def test_backspace_is_repeatable_on_alpha_and_symbol_pages(self) -> None:
        self.assertIn("def backspace_button(self):", KEYBOARD)
        self.assertEqual(KEYBOARD.count("grid.attach(self.backspace_button()"), 2)
        for placement in (
            "grid.attach(self.backspace_button(), 50, 3, 10, 1)",
            "grid.attach(self.backspace_button(), 47, 3, 13, 1)",
        ):
            self.assertIn(placement, KEYBOARD)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Static contract for the Pocket DS handheld desktop mouse layout."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROFILE = (ROOT / "components/inputplumber/pocketds-joymouse.yaml").read_text(
    encoding="utf-8"
)
GAMEPAD_PROFILE = (
    ROOT / "components/inputplumber/pocketds-gamepad.yaml"
).read_text(encoding="utf-8")
TOGGLE = (ROOT / "components/inputplumber/pocketds-toggle-joymouse").read_text(
    encoding="utf-8"
)
CANONICAL = (ROOT / "components/emulation/pocketds-input-mode").read_text(
    encoding="utf-8"
)
LISTENER = (ROOT / "components/inputplumber/pocketds-mode-listener.py").read_text(
    encoding="utf-8"
)
CAPABILITY_MAP = (
    ROOT / "components/inputplumber/ayaneo_mcu_xbox.yaml"
).read_text(encoding="utf-8")


def mapping_block(text: str, name: str) -> str:
    marker = f"  - name: {name}"
    start = text.find(marker)
    if start < 0:
        raise AssertionError(f"missing mapping: {name}")
    end = text.find("\n  - name:", start + len(marker))
    return text[start:] if end < 0 else text[start:end]


def block(name: str) -> str:
    return mapping_block(PROFILE, name)


contracts = {
    "Right Bumper (R1) → Mouse Left Click": "button: Left",
    "Right Trigger (R2) → Mouse Right Click": "button: Right",
    "Left Bumper (L1) → Scroll Up": "button: WheelUp",
    "Left Trigger (L2) → Scroll Down": "button: WheelDown",
}
for mapping, action in contracts.items():
    section = block(mapping)
    assert action in section, (mapping, action)

assert "button: RightBumper" in block("Right Bumper (R1) → Mouse Left Click")
right_trigger = block("Right Trigger (R2) → Mouse Right Click")
assert "name: RightTrigger" in right_trigger
assert "deadzone: 0.15" in right_trigger
left_trigger = block("Left Trigger (L2) → Scroll Down")
assert "name: LeftTrigger" in left_trigger
assert "deadzone: 0.15" in left_trigger

for obsolete in (
    "Right Bumper (R1) → Scroll Down",
    "Right Trigger (R2) → Mouse Left Click",
    "Left Trigger (L2) → Mouse Right Click",
):
    assert obsolete not in PROFILE

window_actions = {
    "North (Y) → task switcher (dbus)": "dbus: ui_task_switcher",
    "Start → close focused window (dbus)": "dbus: ui_window_close",
    "View → deferred chord input": "dbus: ui_mode_view",
    "MCU BTN_3 (RightPaddle2 slot / physical LC) → fullscreen (dbus)": (
        "dbus: ui_window_fullscreen"
    ),
    "MCU BTN_4 (LeftPaddle2 slot / physical RC) → toggle custom OSK (dbus ui_osk)": (
        "dbus: ui_osk"
    ),
}
for mapping, action in window_actions.items():
    section = block(mapping)
    assert action in section, (mapping, action)
    assert "keyboard:" not in section, mapping

for forbidden in (
    "KeyLeftCtrl",
    "KeyRightCtrl",
    "KeyLeftShift",
    "KeyRightShift",
    "KeyLeftAlt",
    "KeyRightAlt",
    "KeyLeftMeta",
    "KeyRightMeta",
):
    assert f"- keyboard: {forbidden}" not in PROFILE, forbidden

assert "SetTargetDevices as 1 dbus" in CANONICAL
assert CANONICAL.index("SetTargetDevices as 1 dbus") < CANONICAL.index(
    "LoadProfilePath"
)
assert "restore-token" in CANONICAL
assert "transitioning" in CANONICAL
assert "org.shadowblip.Input.Target" in CANONICAL
assert "DeviceType" in CANONICAL
assert "xbox-elite:Microsoft X-Box One Elite pad" in CANONICAL
assert "xbox-series:Microsoft Xbox Series S|X Controller" in CANONICAL
assert "Microsoft X-Box One Elite pad" in CANONICAL
assert "bootstrap" in CANONICAL
assert "rollback" in CANONICAL.lower()
assert "SetTargetDevices" not in TOGGLE
assert "LoadProfilePath" not in TOGGLE
assert 'toggle --nonblocking' in TOGGLE
assert "POCKETDS_GAME_SESSION_STATE" in TOGGLE
assert '"phase":"running"' in TOGGLE
assert '"name":"es-de"' in TOGGLE
assert "POCKETDS_GAME_SESSION_SUPERVISOR" in TOGGLE
assert 'flatpak kill "$melonds_app_id"' in TOGGLE
assert "net.kuribo64.melonDS" in TOGGLE
assert "/home/pocketds/.local/bin/retroarch" in TOGGLE
assert "/home/pocketds/.config/retroarch/pocketds\\.cfg" in TOGGLE
assert "/usr/bin/pgrep -u 1000 -f" in TOGGLE
assert "/usr/bin/pkill -TERM -u 1000 -f" in TOGGLE
assert "--exit-game" in TOGGLE
assert "--exit-game-active" in TOGGLE
assert "request == exit-game" in TOGGLE
assert "request == exit-game-active" in TOGGLE
assert "Menu (Start) → gamepad + exit chord" in GAMEPAD_PROFILE
assert "View (Select) → deferred chord input" in GAMEPAD_PROFILE
for token in (
    "button: Start",
    "dbus: ui_game_menu",
    "dbus: ui_mode_view",
    "dbus: ui_mode_equals",
    "Equals (Guide) → deferred chord input",
):
    assert token in GAMEPAD_PROFILE
aya_gamepad = mapping_block(
    GAMEPAD_PROFILE, "AYA internal slot → Steam Quick Access"
)
assert "button: Keyboard" in aya_gamepad
assert "button: QuickAccess" in aya_gamepad
for name, action in (
    ("Brightness Up", "dbus: ui_brightness_up"),
    ("Brightness Down", "dbus: ui_brightness_down"),
):
    assert action in mapping_block(GAMEPAD_PROFILE, name)
aya_joymouse = block("AYA internal slot → inert in joymouse")
assert "button: Keyboard" in aya_joymouse
assert "dbus: ui_aya_qam_joymouse" in aya_joymouse
assert "keyboard:" not in aya_joymouse
assert 'VIEW_ACTION = "ui_mode_view"' in LISTENER
assert 'EQUALS_ACTION = "ui_mode_equals"' in LISTENER
assert 'EXIT_ACTIONS = ("ui_game_menu", VIEW_ACTION)' in LISTENER
assert "EXIT_CONFIRM_WINDOW = 5.0" in LISTENER
assert "再次同时按 Menu + View 退出游戏" in LISTENER
assert "org.kde.osdService.showText" in LISTENER
assert 'send_gamepad_click("Select")' in LISTENER
assert 'send_gamepad_click("Guide")' in LISTENER
assert 'action == "ui_aya_qam_joymouse"' in LISTENER
assert "Keyboard:Key" not in LISTENER
assert "def clear_mode_chord(*, cancel_timeout):" in LISTENER
assert "clear_mode_chord(cancel_timeout=True)" in LISTENER
assert "clear_mode_chord(cancel_timeout=False)" in LISTENER
assert "toggle_input_mode()" in LISTENER
assert '"--exit-game-active"' in LISTENER
assert '["/usr/bin/pocketds-toggle-joymouse", "--exit-game"]' in LISTENER
assert '["/usr/bin/pocketds-toggle-joymouse"], check=False' in LISTENER

equals_map = mapping_block(
    CAPABILITY_MAP, "Physical equals key → Steam/Xbox Guide"
)
assert "event_code: BTN6" in equals_map
assert "button: Guide" in equals_map
assert "event_code: BTN2" not in equals_map
assert "event_code: BTN7" not in CAPABILITY_MAP
aya_map = mapping_block(CAPABILITY_MAP, "AYA logo → internal profile slot")
assert "event_code: BTN5" in aya_map
assert "button: Keyboard" in aya_map
assert "KeyLeftMeta" not in aya_map
brightness_up_map = mapping_block(
    CAPABILITY_MAP, "MCU BTN_MODE (scan 0x9000d) → Brightness Up"
)
assert "event_code: BTN_MODE" in brightness_up_map
assert "button: QuickAccess" in brightness_up_map
brightness_down_map = mapping_block(
    CAPABILITY_MAP, "MCU BTN_2 (scan 0x90003) → Brightness Down"
)
assert "event_code: BTN2" in brightness_down_map
assert "button: QuickAccess2" in brightness_down_map
assert "button: RightPaddle2" in mapping_block(
    CAPABILITY_MAP, "MCU BTN_3 → RightPaddle2"
)
assert "button: LeftPaddle2" in mapping_block(
    CAPABILITY_MAP, "MCU BTN_4 → LeftPaddle2"
)
assert "keyboard: KeyLeftMeta" not in CAPABILITY_MAP

writers = []
for path in (ROOT / "components").rglob("*"):
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "LoadProfilePath" in text or "SetTargetDevices as" in text:
            writers.append(path.relative_to(ROOT).as_posix())
assert writers == ["components/emulation/pocketds-input-mode"], writers

print("  [OK] joymouse clicks, scrolling and KWin actions cannot strand modifiers")

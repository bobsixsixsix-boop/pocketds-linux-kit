#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Route Pocket DS hardware actions emitted by InputPlumber.

AYA is forwarded as Steam Quick Access. View and the physical '=' / Guide key
arrive here as press/release signals: gamepad mode keeps their normal actions,
while View + '=' switches joymouse/gamepad without leaking Select or
Steam/Home. During managed melonDS or RetroArch play,
Menu + View retains the existing two-stage exit confirmation.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/usr/local/lib/pocketds")
try:
    from controller_test_gate import hardware_actions_blocked
except (ImportError, SyntaxError, OSError):
    def hardware_actions_blocked():
        # Older installs have neither the module nor the journal. A journal
        # without its trusted reader must never silently reopen input actions.
        try:
            Path("/run/pocketds-controller-test/state.json").lstat()
        except FileNotFoundError:
            return False
        except OSError:
            pass
        return True

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

DBusGMainLoop(set_as_default=True)

VIEW_ACTION = "ui_mode_view"
EQUALS_ACTION = "ui_mode_equals"
MODE_ACTIONS = (VIEW_ACTION, EQUALS_ACTION)
MODE_HELD = {action: False for action in MODE_ACTIONS}
MODE_CHORD_LATCHED = False
MODE_RESET_SOURCE = None
MODE_RESET_DELAY_MS = 10000

EXIT_ACTIONS = ("ui_game_menu", VIEW_ACTION)
EXIT_HELD = {action: False for action in EXIT_ACTIONS}
EXIT_CHORD_LATCHED = False
EXIT_VIEW_CONSUMED = False
EXIT_CONFIRM_UNTIL = 0.0
EXIT_CONFIRM_WINDOW = 5.0
EXIT_PROMPT = "再次同时按 Menu + View 退出游戏"
EXITING_TEXT = "正在退出游戏…"
USER_COMMAND_PREFIX = [
    "/usr/bin/systemd-run",
    "--machine=pocketds@.host",
    "--user",
    "--quiet",
    "--collect",
    "--",
]
COMPOSITE_PATH = "/org/shadowblip/InputPlumber/CompositeDevice0"
COMPOSITE_INTERFACE = "org.shadowblip.Input.CompositeDevice"

def bootstrap_initial_profile():
    """Move InputPlumber's vendor default into the managed profile once.

    The device YAML does not pin a profile. This read-only probe calls the
    compatibility wrapper only for default.yaml; stable joymouse/gamepad
    selections remain untouched.
    """
    for _ in range(20):
        try:
            result = subprocess.run(
                [
                    "busctl",
                    "get-property",
                    "org.shadowblip.InputPlumber",
                    "/org/shadowblip/InputPlumber/CompositeDevice0",
                    "org.shadowblip.Input.CompositeDevice",
                    "ProfilePath",
                ],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                current = ""
                if '"' in result.stdout:
                    current = result.stdout.split('"', 2)[1]
                if current.endswith("/default.yaml") or current == "":
                    subprocess.run(
                        ["/usr/bin/pocketds-toggle-joymouse"], check=False
                    )
                return
        except Exception:
            pass
        time.sleep(0.5)


def show_osd(text):
    """Show a Plasma OSD in the pocketds graphical session."""
    subprocess.Popen(
        USER_COMMAND_PREFIX
        + [
            "/usr/bin/qdbus-qt6",
            "org.kde.plasmashell",
            "/org/kde/osdService",
            "org.kde.osdService.showText",
            "input-gaming",
            text,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def invoke_kwin_shortcut(shortcut):
    """Invoke one KWin action in the graphical session without held modifiers."""
    if hardware_actions_blocked():
        clear_controller_test_chords()
        return
    subprocess.Popen(
        USER_COMMAND_PREFIX
        + [
            "/usr/bin/qdbus-qt6",
            "org.kde.kglobalaccel",
            "/component/kwin",
            "org.kde.kglobalaccel.Component.invokeShortcut",
            shortcut,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def current_input_mode():
    """Return the canonical mode, or None while its state is unavailable."""
    try:
        result = subprocess.run(
            ["/usr/local/libexec/pocketds-input-mode", "status"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    mode = result.stdout.strip()
    return mode if result.returncode == 0 and mode in ("gamepad", "joymouse") else None


def send_button_chord(capabilities):
    """Inject one bounded chord directly to the active InputPlumber targets."""
    if hardware_actions_blocked():
        clear_controller_test_chords()
        return
    try:
        composite = dbus.Interface(
            bus.get_object("org.shadowblip.InputPlumber", COMPOSITE_PATH),
            COMPOSITE_INTERFACE,
        )
        composite.SendButtonChord(dbus.Array(capabilities, signature="s"))
    except dbus.DBusException as exc:
        print(f"InputPlumber chord injection failed: {exc}", flush=True)


def send_gamepad_click(button):
    """Inject one gamepad click directly to InputPlumber targets."""
    send_button_chord([f"Gamepad:Button:{button}"])


def handle_view_single():
    mode = current_input_mode()
    if mode == "gamepad":
        send_gamepad_click("Select")
    elif mode == "joymouse":
        invoke_kwin_shortcut("Window to Next Screen")


def handle_equals_single():
    if current_input_mode() == "gamepad":
        send_gamepad_click("Guide")
    # In joymouse mode there is intentionally no single-key fallback. Sending
    # a Steam-like keyboard chord globally would affect the focused browser,
    # terminal or editor. View + '=' remains available to enter gamepad mode.


def clear_mode_chord(*, cancel_timeout):
    global MODE_CHORD_LATCHED, MODE_RESET_SOURCE
    source = MODE_RESET_SOURCE
    MODE_RESET_SOURCE = None
    if cancel_timeout and source is not None:
        GLib.source_remove(source)
    for action in MODE_ACTIONS:
        MODE_HELD[action] = False
    MODE_CHORD_LATCHED = False


def expire_mode_chord():
    clear_mode_chord(cancel_timeout=False)
    return GLib.SOURCE_REMOVE


def arm_mode_reset():
    global MODE_RESET_SOURCE
    if MODE_RESET_SOURCE is not None:
        GLib.source_remove(MODE_RESET_SOURCE)
    MODE_RESET_SOURCE = GLib.timeout_add(MODE_RESET_DELAY_MS, expire_mode_chord)


def toggle_input_mode():
    if hardware_actions_blocked():
        clear_controller_test_chords()
        return
    subprocess.Popen(
        ["/usr/bin/pocketds-toggle-joymouse", "--guide"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def managed_game_is_active():
    """Ask the bounded helper whether a supported managed game is active."""
    try:
        result = subprocess.run(
            ["/usr/bin/pocketds-toggle-joymouse", "--exit-game-active"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def handle_exit_chord():
    """Require a second distinct chord before closing the current game."""
    global EXIT_CONFIRM_UNTIL
    if hardware_actions_blocked():
        clear_controller_test_chords()
        return
    now = time.monotonic()
    if now <= EXIT_CONFIRM_UNTIL:
        EXIT_CONFIRM_UNTIL = 0.0
        show_osd(EXITING_TEXT)
        subprocess.Popen(
            ["/usr/bin/pocketds-toggle-joymouse", "--exit-game"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return
    EXIT_CONFIRM_UNTIL = 0.0
    if managed_game_is_active():
        EXIT_CONFIRM_UNTIL = now + EXIT_CONFIRM_WINDOW
        show_osd(EXIT_PROMPT)


def update_exit_chord(action, pressed):
    """Update Menu+View and report whether View's single action is consumed."""
    global EXIT_CHORD_LATCHED, EXIT_VIEW_CONSUMED
    EXIT_HELD[action] = pressed

    if all(EXIT_HELD.values()):
        if not EXIT_CHORD_LATCHED:
            EXIT_CHORD_LATCHED = True
            EXIT_VIEW_CONSUMED = True
            handle_exit_chord()
        return action == VIEW_ACTION and EXIT_VIEW_CONSUMED

    consumed = action == VIEW_ACTION and EXIT_VIEW_CONSUMED
    if not any(EXIT_HELD.values()):
        EXIT_CHORD_LATCHED = False
        EXIT_VIEW_CONSUMED = False
    return consumed


def update_mode_chord(action, pressed, *, suppress_single=False):
    """Defer View/'=' until release, consuming both when the chord forms."""
    global MODE_CHORD_LATCHED
    # The Pocket DS can expose redundant Guide paths on both MCU and composite
    # controller devices. Collapse duplicate press/release samples so one '='
    # tap cannot inject Guide twice.
    if MODE_HELD[action] == pressed:
        return
    MODE_HELD[action] = pressed
    arm_mode_reset()

    if all(MODE_HELD.values()):
        if not MODE_CHORD_LATCHED:
            MODE_CHORD_LATCHED = True
            toggle_input_mode()
        return

    if pressed or MODE_CHORD_LATCHED or suppress_single:
        if not any(MODE_HELD.values()):
            clear_mode_chord(cancel_timeout=True)
        return

    if action == VIEW_ACTION:
        handle_view_single()
    elif action == EQUALS_ACTION:
        handle_equals_single()


def clear_controller_test_chords():
    global EXIT_CHORD_LATCHED, EXIT_VIEW_CONSUMED, EXIT_CONFIRM_UNTIL
    clear_mode_chord(cancel_timeout=True)
    for action in EXIT_ACTIONS:
        EXIT_HELD[action] = False
    EXIT_CHORD_LATCHED = False
    EXIT_VIEW_CONSUMED = False
    EXIT_CONFIRM_UNTIL = 0.0


def observe_controller_test_gate():
    # A held key can enter and leave the test without emitting another event.
    # Clear deferred single-key/chord state even during that quiet interval.
    if hardware_actions_blocked():
        clear_controller_test_chords()
    return GLib.SOURCE_CONTINUE


def on_input_event(action, value):
    if hardware_actions_blocked():
        clear_controller_test_chords()
        return
    action = str(action)
    pressed = value >= 0.5

    if action == "ui_game_menu":
        update_exit_chord(action, pressed)
        return

    if action == VIEW_ACTION:
        consumed = update_exit_chord(action, pressed)
        update_mode_chord(action, pressed, suppress_single=consumed)
        return

    if action == EQUALS_ACTION:
        update_mode_chord(action, pressed)
        return

    if action == "ui_aya_qam_joymouse":
        # Consumed deliberately: joymouse has no virtual gamepad target, and
        # a global keyboard imitation would leak into whichever app is focused.
        return

    if not pressed:
        return

    if action in ("ui_brightness_up", "ui_brightness_down"):
        direction = "+" if action == "ui_brightness_up" else "-"
        subprocess.Popen(
            ["/usr/bin/pocketds-brightness", direction],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


bus = dbus.SystemBus()
bus.add_signal_receiver(
    on_input_event,
    signal_name="InputEvent",
    dbus_interface="org.shadowblip.Input.DBusDevice",
    bus_name="org.shadowblip.InputPlumber",
)

bootstrap_initial_profile()
GLib.timeout_add(50, observe_controller_test_gate)
GLib.MainLoop().run()

#!/usr/bin/env python3
"""Opt-in GTK/XWayland geometry regression; no input or audio is generated.

Default invocation skips before loading GTK or accessing a desktop session.
--live-session temporarily displays a test keyboard under the existing KWin
rules: hide the daily keyboard first and leave the test window untouched.
The source's window configuration and view methods are executed, but its module
and application constructor are not. Device, input, audio and settings callbacks
are replaced with no-ops. This does not validate physical touch or typing.

Example (in a matching Pocket DS Plasma session):
  python3 tests/keyboard-layout-geometry.py --live-session --output /new/result.json
Use --source /path/to/pocketds-keyboard.py to compare an older source revision.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "components/keyboard"
VIEW_METHODS = frozenset({
    "install_css", "button", "utility_button", "quick_toolbar",
    "navigation_button", "navigation_cluster", "feedback_nav_button",
    "feedback_label", "feedback_scale", "feedback_card",
    "open_feedback_settings", "close_feedback_settings", "build_layout",
    "shift_is_active", "primary_character_label", "primary_character_button",
    "shift_button", "update_shift_visuals", "cancel_shift_press",
    "space_voice_button", "voice_status_content", "backspace_button",
    "toggle_symbols", "render_backspace_clear_state", "set_mic",
    "render_voice_snapshot", "render_window_transfer_button",
    "apply_visibility_snapshot",
})
VIEW_CONSTANTS = frozenset({
    "NAVIGATION_KEYS", "SHIFTED_CHARACTERS", "VOICE_STATE_LABELS",
    "CUSTOM_VOICE_SAFETY_ENABLED",
})
GUI_ENVIRONMENT = frozenset({
    "DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_TYPE",
})


def noop(*_args, **_kwargs):
    return False


def is_self_attribute(node, name=None):
    return (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "self" and (name is None or node.attr == name))


def extract_view(source, namespace):
    """Keep production GTK configuration; never import its side-effectful module."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    original = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == "PocketDSKeyboard")
    methods = {node.name: node for node in original.body
               if isinstance(node, ast.FunctionDef)}
    missing = VIEW_METHODS - methods.keys()
    if missing:
        raise RuntimeError("production view methods changed: " + ", ".join(sorted(missing)))
    defaults = {}
    window_statements = []
    for statement in methods["__init__"].body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if is_self_attribute(target, "window"):
                window_statements.append(statement)
            elif is_self_attribute(target):
                try:
                    defaults[target.attr] = ast.literal_eval(statement.value)
                except (ValueError, TypeError):
                    pass
        elif (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
              and isinstance(statement.value.func, ast.Attribute)
              and is_self_attribute(statement.value.func.value, "window")):
            window_statements.append(statement)
    if not window_statements or not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "set_resizable"
        for statement in window_statements for node in ast.walk(statement)
    ):
        raise RuntimeError("production window setup could not be extracted")
    constants = [node for node in tree.body if isinstance(node, ast.Assign)
                 and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id in VIEW_CONSTANTS]
    if len(constants) != len(VIEW_CONSTANTS):
        raise RuntimeError("production view constants changed")
    clone = ast.ClassDef(name="KeyboardViewFixture", bases=[], keywords=[],
                         body=[methods[name] for name in sorted(VIEW_METHODS)],
                         decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=constants + [clone], type_ignores=[]))
    exec(compile(module, str(source), "exec"), namespace)
    view = namespace["KeyboardViewFixture"]()
    for name, value in defaults.items():
        setattr(view, name, value)
    # Every non-view callback is inert, including callbacks connected by the
    # extracted window setup. No UInput, recorder, config store or bus is built.
    for name in methods.keys() - VIEW_METHODS:
        setattr(view, name, noop)
    snapshot = SimpleNamespace(state=namespace["VoiceState"].IDLE, generation=0)
    view.voice = SimpleNamespace(snapshot=lambda: snapshot)
    view.visibility = SimpleNamespace(visible=True)
    view.visibility.snapshot = lambda: view.visibility
    view.custom_voice_enabled = namespace["CUSTOM_VOICE_SAFETY_ENABLED"]
    view.feedback_defaults = namespace["KeyboardFeedbackSettings"]()
    view.feedback_settings = view.feedback_defaults
    # Execute the exact production self.window assignment and configuration,
    # including set_resizable. The test does not substitute its expected value.
    setup = ast.fix_missing_locations(ast.Module(body=window_statements, type_ignores=[]))
    exec(compile(setup, str(source), "exec"), namespace, {"self": view})
    return view


def load_gui_environment():
    result = subprocess.run(["systemctl", "--user", "show-environment"],
                            capture_output=True, text=True, timeout=3, check=True)
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in GUI_ENVIRONMENT:
            os.environ[key] = value
    os.environ["GDK_BACKEND"] = "x11"


def run_live(source, report):
    load_gui_environment()
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("GdkX11", "3.0")
    from gi.repository import Gdk, GdkX11, Gtk  # noqa: F401
    from evdev import ecodes
    sys.path.insert(0, str(COMPONENT))
    from keyboard_adapter import (
        BackspaceClearGestureController, KeyboardFeedbackSettings, VoiceState,
    )
    from gi.repository import GLib

    display = Gdk.Display.get_default()
    if display is None or not isinstance(display, GdkX11.X11Display):
        raise RuntimeError("a live GTK X11 display is required")
    monitors = [display.get_monitor(index) for index in range(display.get_n_monitors())]
    lower = [monitor for monitor in monitors if monitor.get_model() == "DSI-2"]
    if len(lower) != 1:
        raise RuntimeError("exactly one DSI-2 monitor is required")
    rect = lower[0].get_geometry()
    expected = [rect.x, rect.y, rect.width, rect.height]
    report["monitor_geometry"] = expected
    report["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    report["adapter_sha256"] = hashlib.sha256((COMPONENT / "keyboard_adapter.py").read_bytes()).hexdigest()
    namespace = dict(Gtk=Gtk, Gdk=Gdk, GLib=GLib, ecodes=ecodes,
                     VoiceState=VoiceState, KeyboardFeedbackSettings=KeyboardFeedbackSettings,
                     BackspaceClearGestureController=BackspaceClearGestureController)
    view = extract_view(source, namespace)
    deadline = time.monotonic() + 20

    def pump(seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if time.monotonic() >= deadline:
                raise TimeoutError("geometry test exceeded its 20 second budget")
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
                if time.monotonic() >= deadline:
                    raise TimeoutError("GTK event processing exceeded the test budget")
            time.sleep(0.01)

    def geometry():
        window = view.window.get_window()
        if window is None:
            raise RuntimeError("test window is not mapped")
        origin = window.get_origin()
        if len(origin) != 3 or not origin[0]:
            raise RuntimeError("test window origin is unavailable")
        width, height = view.window.get_size()
        return [origin[1], origin[2], width, height]

    def capture(stage):
        actual = geometry()
        minimum, natural = view.window.get_preferred_size()
        record = {"stage": stage, "geometry": actual,
                  "minimum": [minimum.width, minimum.height],
                  "natural": [natural.width, natural.height]}
        report["records"].append(record)
        return actual

    try:
        view.install_css()
        view.build_layout()
        # Give KWin time to apply its rule. A naturally undersized initial
        # window must not become a falsely passing reference. Allow at most
        # two pixels per edge/dimension for mixed-scale XWayland rounding.
        baseline = None
        for _ in range(10):
            pump(0.3)
            current = geometry()
            if all(abs(a - b) <= 2 for a, b in zip(current, expected)):
                baseline = current
                break
        if baseline is None:
            capture("letters-baseline-rejected")
            raise AssertionError("initial window does not cover the configured DSI-2 geometry")
        report["baseline"] = baseline
        report["resizable"] = bool(view.window.get_resizable())
        capture("letters")
        steps = [
            ("symbols", lambda: view.toggle_symbols(None)),
            ("letters-return", lambda: view.toggle_symbols(None)),
            ("symbols-again", lambda: view.toggle_symbols(None)),
            ("settings", lambda: view.open_feedback_settings()),
            ("symbols-settings-return", lambda: view.close_feedback_settings()),
            ("letters-final", lambda: view.toggle_symbols(None)),
        ]
        steps.extend(("backspace-" + state,
                      lambda state=state: view.render_backspace_clear_state(state))
                     for state in (BackspaceClearGestureController.FEEDBACK_HELD,
                                   BackspaceClearGestureController.FEEDBACK_READY,
                                   BackspaceClearGestureController.FEEDBACK_IDLE))
        steps.extend(("voice-" + state.value,
                      lambda state=state: view.render_voice_snapshot(
                          SimpleNamespace(state=state, generation=0)))
                     for state in VoiceState)
        for stage, action in steps:
            action()
            pump(0.75)
            actual = capture(stage)
            if actual != baseline:
                raise AssertionError(f"{stage}: geometry changed from {baseline} to {actual}")
        report["status"] = "pass"
    finally:
        view.window.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-session", action="store_true",
                        help="show a temporary test keyboard; hide the daily keyboard first")
    parser.add_argument("--source", type=Path, default=COMPONENT / "pocketds-keyboard.py")
    parser.add_argument("--output", type=Path, help="new result JSON path; never overwritten")
    args = parser.parse_args()
    if not args.live_session:
        print(json.dumps({"status": "skip", "reason": "requires explicit --live-session"}))
        return 0
    if args.output is None:
        parser.error("--live-session requires --output pointing to a new JSON file")
    if sys.platform != "linux":
        parser.error("--live-session requires the matching Linux desktop")
    report = {"schema": 1, "status": "fail", "pid": os.getpid(), "records": [],
              "scope": "production GTK view only; no physical input/audio validation"}

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("live geometry test interrupted")

    try:
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as error:
        print(f"result file unavailable: {type(error).__name__}", file=sys.stderr)
        return 1
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            run_live(args.source, report)
        except BaseException as error:
            report["error"] = f"{type(error).__name__}: {error}"
        finally:
            signal.signal(signal.SIGTERM, previous)
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps({"status": report["status"], "stages": len(report["records"])}))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

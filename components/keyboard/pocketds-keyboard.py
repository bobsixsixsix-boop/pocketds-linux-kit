#!/usr/bin/python3
"""Large-key lower-screen keyboard for AYANEO Pocket DS."""

import signal

from keyboard_adapter import (
    BoundedRepeatController,
    BackspaceClearGestureController,
    CaptureStartupController,
    CaptureStartupFailure,
    GLibScheduler,
    KeyboardFeedbackSettings,
    KeyboardFeedbackSettingsStore,
    KeyHapticFeedback,
    KeySoundFeedback,
    HoldTapController,
    KeyboardVisibilityAdapter,
    MicrophoneSessionMonitor,
    RecorderStopController,
    SignalActionReadinessGate,
    RecorderStopFailure,
    SignalToggleReadinessGate,
    VoiceSessionController,
    VoiceState,
    accessible_process_id,
    move_lower_windows_to_upper,
    trusted_accessible_id,
)


# Install a minimal handler before importing D-Bus, evdev, GI, or pyatspi.
# Signals received while the real keyboard is still initializing are queued as
# toggle parity and drained only after the window/controller stack is ready.
_SIGUSR1_GATE = SignalToggleReadinessGate()
_SIGUSR2_GATE = SignalActionReadinessGate()
_SIGHUP_GATE = SignalActionReadinessGate()


def _early_sigusr1_handler(_signum, _frame):
    _SIGUSR1_GATE.request_toggle()


def _early_sigusr2_handler(_signum, _frame):
    _SIGUSR2_GATE.request()


def _early_sighup_handler(_signum, _frame):
    _SIGHUP_GATE.request()


signal.signal(signal.SIGUSR1, _early_sigusr1_handler)
signal.signal(signal.SIGUSR2, _early_sigusr2_handler)
signal.signal(signal.SIGHUP, _early_sighup_handler)


from contextlib import contextmanager  # noqa: E402
import fcntl  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import select  # noqa: E402
import re  # noqa: E402
import socket  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from collections import deque  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, "/usr/local/lib/pocketds")
try:
    from controller_test_gate import hardware_actions_blocked  # noqa: E402
except (ImportError, SyntaxError, OSError):
    def hardware_actions_blocked():
        try:
            Path("/run/pocketds-controller-test/state.json").lstat()
        except FileNotFoundError:
            return False
        except OSError:
            pass
        return True

from voice_artifacts import (  # noqa: E402
    AsrApiClient,
    AsrApiError,
    AsrApiCancelled,
    VoiceArtifactError,
    disable_process_dumpability,
    finalize_private_arecord_wav,
    normalize_transcript,
    parse_sensevoice_transcript,
    private_wav_has_pcm_payload,
    read_private_wav,
    remove_owned_artifact,
    run_bounded_command,
    sensevoice_backend_available,
    sensevoice_command,
)

# The cloud credential lives only in this process; never allow it into a
# systemd-coredump pipe even though piped dumps ignore RLIMIT_CORE.
disable_process_dumpability()

# Speech and transcript artifacts must never inherit a public process umask.
os.umask(0o077)

import dbus  # noqa: E402
from dbus.mainloop.glib import DBusGMainLoop, threads_init  # noqa: E402
from evdev import InputDevice, UInput, ecodes  # noqa: E402
from touchpad_raw import RawTouchAction, TypeBTouchFrame, resync_type_b  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
import pyatspi  # noqa: E402


threads_init()
DBusGMainLoop(set_as_default=True)

FOCUS_REVALIDATE_MS = 500
WINDOW_ACTION_DEBOUNCE_S = 0.25
QUICK_APP_DEBOUNCE_S = 0.9
FEEDBACK_SAVE_DELAY_MS = 300
TERMINAL_PLACEMENT_LIFETIME_MS = 8_000
QUICK_APP_COMMANDS = {
    "terminal": ("/usr/bin/konsole", "--separate"),
}
SOUND_LEVEL_DB = (-30.0, -24.0, -18.0, -12.0, -6.0)
HAPTIC_LEVELS = (
    (0.30, 50),
    (0.40, 50),
    (0.50, 50),
    (0.60, 50),
    (0.70, 50),
)
CAPTURE_RETRY_COOLDOWN_S = 8.0
INTERNAL_MICROPHONE_SOURCE = "alsa_input.platform-sound.HiFi__Mic__source"
BACKSPACE_CLEAR_HOLD_MS = 420
BACKSPACE_CLEAR_DISTANCE = 72.0
SPACE_HOLD_MS = 450
A11Y_FOCUS_RECOVERY_TIMEOUT_S = 0.9
A11Y_FOCUS_CALL_TIMEOUT_S = 0.1
A11Y_FOCUS_RECOVERY_MAX_NODES = 512
A11Y_BUS_ADDRESS = (
    f"unix:path={os.environ.get('XDG_RUNTIME_DIR', '/run/user/1000')}"
    "/at-spi/bus_0"
)
A11Y_ACTIVE_MASK = 1 << int(getattr(pyatspi, "STATE_ACTIVE", 1))
A11Y_EDITABLE_MASK = 1 << int(getattr(pyatspi, "STATE_EDITABLE", 7))
A11Y_FOCUSED_MASK = 1 << int(getattr(pyatspi, "STATE_FOCUSED", 12))
A11Y_EDITABLE_ROLES = frozenset(
    int(role)
    for role in (
        getattr(pyatspi, "ROLE_ENTRY", 79),
        getattr(pyatspi, "ROLE_PASSWORD_TEXT", 40),
        getattr(pyatspi, "ROLE_TEXT", 61),
        getattr(pyatspi, "ROLE_TERMINAL", 60),
    )
)
# Direct ALSA avoids the observed PipeWire/Q6APM failure, but the first
# integrated attempt coincided with an independent GMU/display hang. The
# separately deployed GMU runtime-PM workaround now prevents that wake path
# while preserving simple_ondemand frequency scaling.
CUSTOM_VOICE_SAFETY_ENABLED = True
VOICE_STATE_LABELS = {
    VoiceState.IDLE: "按住说话",
    VoiceState.CONNECTING: "准备中",
    VoiceState.RECORDING: "请说话 · 松手识别",
    VoiceState.STOPPING: "收音中",
    VoiceState.RECOGNIZING: "识别中",
    VoiceState.PENDING_PASTE: "正在输入",
    VoiceState.SUCCESS: "已输入",
    VoiceState.ERROR: "识别失败",
    VoiceState.DISCARDED: "已取消",
}
VOICE_FAILURE_LABELS = {
    "语音 API 尚未配置": "语音未配置",
    "麦克风已静音": "麦克风已静音",
    "麦克风音量为零": "麦克风音量为零",
    "麦克风不可用": "麦克风不可用",
    "麦克风状态检查失败": "麦克风检查失败",
}
WINDOW_SHORTCUTS = {
    "ui_task_switcher": "Walk Through Windows",
    "ui_window_close": "Window Close",
    "ui_window_fullscreen": "Window Fullscreen",
    "ui_window_next_screen": "Window to Next Screen",
}
LOWER_TOUCH_WIDTH = 1024.0
LOWER_TOUCH_HEIGHT = 768.0
RUNTIME = (
    Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"))
    / "pocketds-keyboard"
)
TOUCHPAD_VISIBLE_STATE = (
    Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"))
    / "pocketds-touchpad/visible"
)
FEEDBACK_SETTINGS_PATH = (
    Path.home() / ".config/pocketds-keyboard/feedback.json"
)
INPUT_MODE_LOCK = Path("/run/pocketds-input-mode.lock")
INPUT_MODE_STATE = Path("/run/pocketds-input-mode/state")
QDBUS_BIN = "/usr/bin/qdbus-qt6"
TERMINAL_KWIN_SCRIPT = """// Place the newly launched Konsole on DSI-1.
const targetOutputName = "DSI-1";
const allowedIds = ["org.kde.konsole", "konsole"];
let placed = false;

function outputNamed(name) {
    const outputs = workspace.screens;
    for (let i = 0; i < outputs.length; ++i) {
        if (outputs[i].name === name) return outputs[i];
    }
    return null;
}

function isTerminal(window) {
    const resourceClass = String(window.resourceClass || "").toLowerCase();
    const resourceName = String(window.resourceName || "").toLowerCase();
    return allowedIds.indexOf(resourceClass) >= 0 ||
           allowedIds.indexOf(resourceName) >= 0;
}

function arrange(window) {
    if (placed || !isTerminal(window)) return;
    const target = outputNamed(targetOutputName);
    if (!target) return;
    if (!window.output || window.output.name !== target.name) {
        workspace.sendClientToScreen(window, target);
    }
    placed = true;
}

workspace.windowAdded.connect(function(window) { arrange(window); });
"""
VOICE_WAV = RUNTIME / "pocketds-voice.wav"
WHISPER_BIN = Path.home() / ".local/libexec/pocketds-keyboard/whisper-cli"
WHISPER_MODEL = (
    Path.home()
    / ".local/share/pocketds-keyboard/models/ggml-base-q5_1.bin"
)
SENSEVOICE_DIR = Path.home() / ".local/share/pocketds-keyboard/sensevoice"
SENSEVOICE_BIN = Path.home() / ".local/libexec/pocketds-keyboard/sherpa-onnx-offline"
SENSEVOICE_LIBRARY = Path.home() / ".local/libexec/pocketds-keyboard/libonnxruntime.so"
SENSEVOICE_MODEL = SENSEVOICE_DIR / "model.int8.onnx"
SENSEVOICE_TOKENS = SENSEVOICE_DIR / "tokens.txt"

LETTER_CODES = {c: getattr(ecodes, f"KEY_{c.upper()}") for c in "abcdefghijklmnopqrstuvwxyz"}
DIGIT_CODES = {
    "1": ecodes.KEY_1,
    "2": ecodes.KEY_2,
    "3": ecodes.KEY_3,
    "4": ecodes.KEY_4,
    "5": ecodes.KEY_5,
    "6": ecodes.KEY_6,
    "7": ecodes.KEY_7,
    "8": ecodes.KEY_8,
    "9": ecodes.KEY_9,
    "0": ecodes.KEY_0,
}
NAVIGATION_KEYS = {
    "up": ("go-up-symbolic", "上", ecodes.KEY_UP),
    "left": ("go-previous-symbolic", "左", ecodes.KEY_LEFT),
    "down": ("go-down-symbolic", "下", ecodes.KEY_DOWN),
    "right": ("go-next-symbolic", "右", ecodes.KEY_RIGHT),
}
SHIFTED_CHARACTERS = {
    "1": "!",
    "2": "@",
    "3": "#",
    "4": "$",
    "5": "%",
    "6": "^",
    "7": "&",
    "8": "*",
    "9": "(",
    "0": ")",
    ",": "<",
    ".": ">",
    "/": "?",
    "'": '"',
}
CHAR_KEYS = {
    **{c: (code, False) for c, code in LETTER_CODES.items()},
    **{c: (code, False) for c, code in DIGIT_CODES.items()},
    "-": (ecodes.KEY_MINUS, False),
    "_": (ecodes.KEY_MINUS, True),
    "=": (ecodes.KEY_EQUAL, False),
    "+": (ecodes.KEY_EQUAL, True),
    "[": (ecodes.KEY_LEFTBRACE, False),
    "]": (ecodes.KEY_RIGHTBRACE, False),
    "{": (ecodes.KEY_LEFTBRACE, True),
    "}": (ecodes.KEY_RIGHTBRACE, True),
    "\\": (ecodes.KEY_BACKSLASH, False),
    ";": (ecodes.KEY_SEMICOLON, False),
    ":": (ecodes.KEY_SEMICOLON, True),
    "'": (ecodes.KEY_APOSTROPHE, False),
    '"': (ecodes.KEY_APOSTROPHE, True),
    ",": (ecodes.KEY_COMMA, False),
    "<": (ecodes.KEY_COMMA, True),
    ".": (ecodes.KEY_DOT, False),
    ">": (ecodes.KEY_DOT, True),
    "/": (ecodes.KEY_SLASH, False),
    "?": (ecodes.KEY_SLASH, True),
    "`": (ecodes.KEY_GRAVE, False),
    "~": (ecodes.KEY_GRAVE, True),
    "!": (ecodes.KEY_1, True),
    "@": (ecodes.KEY_2, True),
    "#": (ecodes.KEY_3, True),
    "$": (ecodes.KEY_4, True),
    "%": (ecodes.KEY_5, True),
    "^": (ecodes.KEY_6, True),
    "&": (ecodes.KEY_7, True),
    "*": (ecodes.KEY_8, True),
    "(": (ecodes.KEY_9, True),
    ")": (ecodes.KEY_0, True),
}


def _run_kwin_scripting(arguments, *, runner=None):
    execute = subprocess.run if runner is None else runner
    return execute(
        [QDBUS_BIN, "org.kde.KWin", "/Scripting", *arguments],
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=1.0,
        check=False,
    )


def unload_terminal_placement(script_name, script_path, *, runner=None):
    try:
        _run_kwin_scripting(
            ["org.kde.kwin.Scripting.unloadScript", script_name],
            runner=runner,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        Path(script_path).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def prepare_terminal_placement(*, runtime=RUNTIME, runner=None, nonce=None):
    """Load a short-lived, exact-identity KWin placement script."""

    runtime = Path(runtime)
    try:
        metadata = runtime.lstat()
    except OSError:
        return None
    if (
        not runtime.is_dir()
        or runtime.is_symlink()
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o022
    ):
        return None
    token = time.monotonic_ns() if nonce is None else int(nonce)
    script_name = f"pocketds-konsole-top-{os.getpid()}-{token}"
    script_path = runtime / f".{script_name}.js"
    descriptor = -1
    loaded = False
    try:
        descriptor = os.open(
            script_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(TERMINAL_KWIN_SCRIPT.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        load = _run_kwin_scripting(
            [
                "org.kde.kwin.Scripting.loadScript",
                str(script_path),
                script_name,
            ],
            runner=runner,
        )
        script_id = load.stdout.strip()
        if load.returncode != 0 or not script_id.isdecimal():
            return None
        loaded = True
        execute = subprocess.run if runner is None else runner
        started = execute(
            [
                QDBUS_BIN,
                "org.kde.KWin",
                f"/Scripting/Script{script_id}",
                "org.kde.kwin.Script.run",
            ],
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=1.0,
            check=False,
        )
        if started.returncode != 0:
            return None
        return script_name, script_path
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not loaded:
            try:
                script_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        elif "started" not in locals() or started.returncode != 0:
            unload_terminal_placement(
                script_name,
                script_path,
                runner=runner,
            )


def notify_systemd_ready():
    """Mark the notify service active only after the toggle owner is ready."""

    notify_socket = os.environ.get("NOTIFY_SOCKET")
    if not notify_socket:
        return False
    address = (
        "\0" + notify_socket[1:]
        if notify_socket.startswith("@")
        else notify_socket
    )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.connect(address)
            client.sendall(b"READY=1\nSTATUS=Keyboard visibility controller ready")
    except OSError as exc:
        raise RuntimeError(f"systemd readiness notification failed: {exc}") from exc
    return True


class RawKeyboardTouchReader:
    """Observe lower-screen Type-B contacts without grabbing the touchscreen."""

    DEVICE = "/dev/input/by-path/platform-a88000.i2c-event"
    FULL_SCREEN = (0.0, 0.0, LOWER_TOUCH_WIDTH, LOWER_TOUCH_HEIGHT)

    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.tracker = TypeBTouchFrame()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.enabled = False
        self.ready = False
        self.generation = 0
        self.dropping = False
        self.device = None
        self.thread = threading.Thread(
            target=self._read_loop,
            name="pocketds-keyboard-raw-touch",
            daemon=True,
        )
        self.thread.start()

    def is_current(self, generation):
        with self.lock:
            return generation == self.generation

    def _cancel(self):
        self.tracker.reset()
        self.generation += 1
        self.dispatch(self.generation, [RawTouchAction("cancel")])

    def set_enabled(self, enabled):
        with self.lock:
            self._cancel()
            self.enabled = bool(enabled)
            self.dropping = False
            if enabled and self.device is not None:
                try:
                    self.ready = False
                    resync_type_b(self.device, self.tracker)
                    self.ready = True
                except (OSError, ValueError):
                    self._disconnect()
            return self.generation

    def _disconnect(self):
        with self.lock:
            self._cancel()
            self.ready = False
            self.dropping = False
            device = self.device
            self.device = None
            if device is not None:
                try:
                    device.close()
                except OSError:
                    pass

    def _open(self):
        with self.lock:
            device = InputDevice(self.DEVICE)
            if device.name != "Goodix Capacitive TouchScreen":
                device.close()
                raise OSError(f"unexpected lower touchscreen: {device.name}")
            self.device = device
            self.ready = False
            try:
                self._cancel()
                resync_type_b(device, self.tracker)
                self.dropping = False
                self.ready = True
            except (OSError, ValueError):
                self._disconnect()
                raise
        print("keyboard-touch: raw reader ready", flush=True)
        return device

    def _process(self, event):
        with self.lock:
            if not self.enabled:
                return False
            if self.dropping:
                if event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                    resync_type_b(self.device, self.tracker)
                    self.dropping = False
                    self.ready = True
                    return True  # Discard the remainder of the caller's batch.
                return False
            actions = []
            if event.type == ecodes.EV_ABS:
                if event.code == ecodes.ABS_MT_SLOT:
                    self.tracker.set_slot(event.value)
                elif event.code == ecodes.ABS_MT_TRACKING_ID:
                    self.tracker.set_tracking_id(event.value)
                elif event.code == ecodes.ABS_MT_POSITION_X:
                    self.tracker.set_x(event.value)
                elif event.code == ecodes.ABS_MT_POSITION_Y:
                    self.tracker.set_y(event.value)
            elif event.type == ecodes.EV_SYN:
                if event.code == ecodes.SYN_DROPPED:
                    self._cancel()
                    self.dropping = True
                    self.ready = False
                elif event.code == ecodes.SYN_REPORT:
                    actions = self.tracker.sync(self.FULL_SCREEN)
            if actions:
                self.dispatch(self.generation, actions)
            return False

    def _read_loop(self):
        while not self.stop_event.is_set():
            try:
                with self.lock:
                    device = self.device or self._open()
                readable, _, _ = select.select([device.fd], [], [], 0.4)
                if not readable:
                    continue
                with self.lock:
                    if device is not self.device:
                        continue
                    try:
                        events = list(device.read())
                    except BlockingIOError:
                        continue
                    for event in events:
                        if self._process(event):
                            break
            except (OSError, ValueError):
                self._disconnect()
                self.stop_event.wait(1.0)
        self._disconnect()

    def close(self):
        self.stop_event.set()
        self.set_enabled(False)
        self._disconnect()
        self.thread.join(timeout=1.5)


class PocketDSKeyboard:
    def __init__(self):
        self.session_bus = dbus.SessionBus()
        self.system_bus = dbus.SystemBus()
        self.shifted = False
        self.shift_held = False
        self.shift_consumed = False
        self.shift_latched_before_press = False
        self.shift_release_source = 0
        self.symbols = False
        self.settings_visible = False
        self.feedback_ui_updating = False
        self.backspace_buttons = []
        self.backspace_status_stacks = []
        self.primary_buttons = []
        self.shift_buttons = []
        self.raw_touch_contacts = {}
        self.raw_touch_generation = 0
        self.raw_touch_enabled = False
        self.raw_touch = None
        self.voice_process = None
        self.voice_recorder_route = None
        self.microphone_session = None
        self.microphone_pending_result = None
        self.voice_thread = None
        self.voice_timeout = 0
        self.voice_feedback_source = 0
        self.voice_retry_not_before = 0.0
        self.space_voice_generation = 0
        self.space_voice_release_generation = 0
        self.custom_voice_enabled = CUSTOM_VOICE_SAFETY_ENABLED
        self.last_ignored_voice_activation = None
        self.voice_fallback_window = None
        self.mic_button = None
        self.mic_status_stack = None
        self.mic_error_label = None
        self.voice_error_feedback = None
        self.last_window_action = {}
        self.quick_action_not_before = {}
        self.window_transfer_button = None
        self.window_transfer_busy = False
        self.window_transfer_failed = False
        self.window_transfer_feedback_source = 0
        self.window_transfer_closing = threading.Event()
        self.feedback_save_source = 0
        self.sound_toggle = None
        self.haptic_toggle = None
        self.sound_scale = None
        self.haptic_scale = None
        self.sound_preview = None
        self.haptic_preview = None
        self.sound_level_label = None
        self.haptic_level_label = None
        self.feedback_touch_released_at = {"sound": 0.0, "haptic": 0.0}
        self.terminal_placements = {}
        self.terminal_pending_placements = {}
        self.terminal_placement_lock = threading.Lock()
        self.terminal_closing = threading.Event()
        self.asr_api = AsrApiClient()
        self.a11y_bus = None
        sound_enabled = os.environ.get(
            "POCKETDS_KEYBOARD_SOUND", "1"
        ).strip().lower() not in {"0", "false", "no", "off"}
        haptic_enabled = os.environ.get(
            "POCKETDS_KEYBOARD_HAPTIC", "1"
        ).strip().lower() not in {"0", "false", "no", "off"}
        self.feedback_defaults = KeyboardFeedbackSettings(
            sound_enabled=sound_enabled,
            sound_level=5,
            haptic_enabled=haptic_enabled,
            haptic_level=2,
        )
        self.feedback_store = KeyboardFeedbackSettingsStore(
            FEEDBACK_SETTINGS_PATH,
            self.feedback_defaults,
        )
        self.feedback_settings = self.feedback_store.load()
        self.key_sound = KeySoundFeedback(
            enabled=self.feedback_settings.sound_enabled,
            volume_db=SOUND_LEVEL_DB[self.feedback_settings.sound_level - 1],
        )
        haptic_intensity, haptic_duration_ms = HAPTIC_LEVELS[
            self.feedback_settings.haptic_level - 1
        ]
        self.key_haptic = KeyHapticFeedback(
            enabled=self.feedback_settings.haptic_enabled,
            intensity=haptic_intensity,
            duration_ms=haptic_duration_ms,
            permission_guard=self.joymouse_haptic_permission,
        )

        capabilities = {
            ecodes.EV_KEY: sorted(
                set(CHAR_KEYS[c][0] for c in CHAR_KEYS)
                | {
                    ecodes.KEY_LEFTSHIFT,
                    ecodes.KEY_LEFTCTRL,
                    ecodes.KEY_LEFTALT,
                    ecodes.KEY_SPACE,
                    ecodes.KEY_ENTER,
                    ecodes.KEY_ESC,
                    ecodes.KEY_TAB,
                    ecodes.KEY_F4,
                    ecodes.KEY_BACKSPACE,
                }
                | {spec[2] for spec in NAVIGATION_KEYS.values()}
            )
        }
        self.uinput = UInput(capabilities, name="Pocket DS Touch Keyboard")

        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_title("Pocket DS Touch Keyboard")
        self.window.set_wmclass("pocketds-keyboard", "pocketds-keyboard")
        self.window.set_decorated(False)
        # Let KWin's forced DSI-2 rule own the size. A non-resizable GTK3
        # window publishes its natural request as both minimum and maximum;
        # rebuilding the symbol layout then shrinks the mixed-scale surface
        # to that request, despite the compositor's forced geometry.
        self.window.set_resizable(True)
        self.window.set_keep_above(True)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_accept_focus(False)
        self.window.set_focus_on_map(False)
        self.window.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        # KWin's forced DSI-2 rule is the sole geometry owner. GTK3 reports
        # sizes in the upper output's 1.5x coordinate space on this mixed-scale
        # Wayland desktop; calling resize()/move() here would turn the desired
        # 820x615 lower-screen window into 546x409 before KWin forces it back.
        self.window.connect("delete-event", self.on_delete)

        scheduler = GLibScheduler(GLib)
        self.visibility = KeyboardVisibilityAdapter(
            scheduler,
            self.accessible_source_is_valid,
            self.apply_visibility_snapshot,
        )
        self.backspace_repeat = BoundedRepeatController(
            scheduler,
            lambda: self.visibility.visible,
            lambda: self.emit_key(ecodes.KEY_BACKSPACE),
        )
        self.backspace_clear = BackspaceClearGestureController(
            scheduler,
            lambda: self.visibility.visible,
            self.render_backspace_clear_state,
            self.clear_current_input,
            hold_delay_ms=BACKSPACE_CLEAR_HOLD_MS,
            upward_distance=BACKSPACE_CLEAR_DISTANCE,
        )
        self.space_hold = HoldTapController(
            scheduler,
            lambda: self.visibility.visible,
            self.space_tapped,
            self.space_held,
            self.space_voice_released,
            hold_delay_ms=SPACE_HOLD_MS,
        )
        self.voice = VoiceSessionController(self.on_voice_state_changed)
        self.recorder_stop = RecorderStopController(scheduler)
        self.capture_startup = CaptureStartupController(
            scheduler,
            self.stop_startup_recorder,
            lambda: private_wav_has_pcm_payload(VOICE_WAV),
            lambda: remove_owned_artifact(VOICE_WAV),
            self.on_capture_ready,
            self.on_capture_failed,
        )

        self.install_css()
        self.build_layout()
        self.raw_touch = RawKeyboardTouchReader(self.queue_raw_touch)
        self.enable_accessibility()
        self.install_focus_listener()
        self.focus_watchdog_source = GLib.timeout_add(
            FOCUS_REVALIDATE_MS, self.revalidate_accessible_focus
        )
        self.install_hardware_toggle()
        _SIGUSR1_GATE.mark_ready(self.queue_manual_toggle)
        _SIGUSR2_GATE.mark_ready(self.queue_manual_hide)
        _SIGHUP_GATE.mark_ready(self.queue_manual_show)
        notify_systemd_ready()
        if os.environ.get("POCKETDS_KEYBOARD_START_VISIBLE") == "1":
            GLib.idle_add(self.manual_show)

    def install_css(self):
        css = b"""
        window { background: #090c0d; }
        button {
          color: #f3f0e9;
          background: #1d2425;
          background-image: none;
          border: 1px solid #344041;
          border-radius: 11px;
          font-family: Noto Sans CJK SC;
          font-size: 30px;
          font-weight: 500;
          text-shadow: none;
          box-shadow: none;
          padding: 2px;
          min-height: 78px;
        }
        button:hover {
          border-color: #708b88;
        }
        button.action {
          color: #f3f0e9;
          background: #273132;
          border-color: #3c4949;
          font-size: 20px;
          font-weight: 600;
          min-height: 60px;
        }
        button.enter {
          color: #9debe7;
          background: #16302f;
          border-color: #3a6e6c;
        }
        button.utility {
          color: #b5c1bf;
          background: #111617;
          border-color: #2e3a3a;
          border-radius: 10px;
          font-size: 18px;
          font-weight: 600;
          min-height: 72px;
          padding: 0 6px;
        }
        button.utility-mode {
          color: #8ce8e4;
          background: #17302f;
          border-color: #39716f;
        }
        button.utility-close {
          color: #ff9a70;
          background: #251915;
          border-color: #5c3020;
        }
        button.direction {
          color: #d3dedb;
          background: #1d2728;
          padding: 0;
        }
        button.direction image { -gtk-icon-shadow: none; }
        button.direction-half {
          border-radius: 10px;
          min-height: 0;
          padding: 0;
        }
        button.shift-on {
          color: #090c0d;
          background: #62d8d5;
          border-color: #b9f4f1;
        }
        button.backspace-held {
          color: #b9f4f1;
          background: #16302f;
          border-color: #62d8d5;
        }
        button.backspace-ready {
          color: #090c0d;
          background: #62d8d5;
          border-color: #b9f4f1;
        }
        button.mic-live {
          color: #ffe3b7;
          background: #493018;
          border-color: #dda458;
        }
        button.mic-busy {
          color: #b9f4f1;
          background: #17302f;
          border-color: #579e9a;
        }
        button.mic-error {
          color: #ffc6c1;
          background: #472627;
          border-color: #dc8983;
        }
        button.mic-success {
          color: #090c0d;
          background: #8ce8e4;
          border-color: #d1fffc;
        }
        button.mic-disabled {
          color: #a3afac;
          background: #111617;
          border-color: #2a3233;
        }
        button.space { font-size: 20px; }
        button.space image { -gtk-icon-shadow: none; }
        button.settings-nav,
        button.feedback-toggle,
        button.feedback-preview {
          min-height: 64px;
          font-size: 20px;
        }
        button.feedback-toggle {
          min-width: 92px;
          min-height: 52px;
          color: #b5c1bf;
          background: #111617;
          border-color: #303839;
        }
        button.feedback-toggle:checked {
          color: #090c0d;
          background: #62d8d5;
          border-color: #b9f4f1;
        }
        button.feedback-preview {
          min-width: 112px;
          color: #9debe7;
          background: #16302f;
          border-color: #3a6e6c;
        }
        button.settings-reset {
          color: #b5c1bf;
          background: #111617;
          font-size: 18px;
        }
        button:active:not(.space):not(.backspace-held):not(.backspace-ready),
        button.raw-pressed:not(.space):not(.backspace-held):not(.backspace-ready) {
          color: #090c0d;
          background: #62d8d5;
          border-color: #b9f4f1;
        }
        button:disabled {
          color: #7c8b88;
          background: #111617;
          border-color: #283333;
        }
        frame.feedback-card {
          background: #151a1b;
          border: 1px solid #344041;
          border-radius: 14px;
        }
        frame.feedback-card > border { border: none; }
        label.feedback-page-title {
          color: #f3f0e9;
          font-family: Noto Sans CJK SC;
          font-size: 26px;
          font-weight: 700;
        }
        label.feedback-title {
          color: #f3f0e9;
          font-family: Noto Sans CJK SC;
          font-size: 24px;
          font-weight: 700;
        }
        label.feedback-value {
          color: #d3dedb;
          font-family: Noto Sans CJK SC;
          font-size: 20px;
          font-weight: 600;
        }
        label.feedback-edge {
          color: #a5b5b1;
          font-family: Noto Sans CJK SC;
          font-size: 18px;
          font-weight: 600;
        }
        scale.feedback-scale { min-height: 56px; }
        scale.feedback-scale trough {
          min-height: 8px;
          border: none;
          border-radius: 4px;
          background: #273031;
          box-shadow: none;
        }
        scale.feedback-scale highlight {
          background: #62d8d5;
          border: none;
          border-radius: 4px;
        }
        scale.feedback-scale slider {
          min-width: 32px;
          min-height: 32px;
          border-radius: 16px;
          background: #f3f0e9;
          border: 2px solid #62d8d5;
          box-shadow: none;
        }
        scale.feedback-scale slider:active {
          background: #62d8d5;
          border-color: #b9f4f1;
        }
        scale.feedback-scale marks { color: #708580; }
        scale.feedback-scale:disabled highlight { background: #475955; }
        scale.feedback-scale:disabled slider {
          background: #7c8b88;
          border-color: #475955;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    @staticmethod
    @contextmanager
    def joymouse_haptic_permission():
        lock_stream = None
        allowed = False
        try:
            lock_stream = INPUT_MODE_LOCK.open("rb")
            fcntl.flock(
                lock_stream.fileno(),
                fcntl.LOCK_SH | fcntl.LOCK_NB,
            )
            with INPUT_MODE_STATE.open(encoding="ascii") as stream:
                state = stream.read(129)
            fields = state.split()
            if len(state) <= 128 and len(fields) == 3:
                protocol, token, mode = fields
                allowed = (
                    protocol == "pds-input-v1"
                    and mode == "joymouse"
                    and re.fullmatch(
                        r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
                        r"[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                        token,
                    )
                    is not None
                )
        except (OSError, UnicodeError):
            allowed = False
        try:
            yield allowed
        finally:
            if lock_stream is not None:
                try:
                    fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                lock_stream.close()

    def play_key_sound(self, *, modifier=False):
        feedback = getattr(self, "key_sound", None)
        if feedback is not None:
            feedback.play(modifier=modifier)
        haptic = getattr(self, "key_haptic", None)
        if haptic is not None:
            haptic.play(modifier=modifier)

    def button(self, label, callback, css_class=None, *, vexpand=True):
        button = Gtk.Button(label=label)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_hexpand(True)
        button.set_vexpand(vexpand)
        button.set_focus_on_click(False)
        button.set_can_focus(False)
        if css_class:
            for name in css_class.split():
                button.get_style_context().add_class(name)
        button.connect("clicked", callback)
        return button

    def button_contains_window_point(self, button, x, y):
        try:
            origin = button.translate_coordinates(self.window, 0, 0)
        except (TypeError, ValueError):
            return False
        if origin is None:
            return False
        button_x, button_y = origin
        return (
            button_x <= x < button_x + button.get_allocated_width()
            and button_y <= y < button_y + button.get_allocated_height()
        )

    def raw_to_window_point(self, x, y):
        width = self.window.get_allocated_width()
        height = self.window.get_allocated_height()
        if width <= 0 or height <= 0:
            return None
        return (
            x * float(width) / LOWER_TOUCH_WIDTH,
            y * float(height) / LOWER_TOUCH_HEIGHT,
        )

    def raw_target_at(self, x, y):
        for button in self.shift_buttons:
            if self.button_contains_window_point(button, x, y):
                return "shift", button, None
        for button in self.backspace_buttons:
            if self.button_contains_window_point(button, x, y):
                return "backspace", button, None
        for button, char in self.primary_buttons:
            if self.button_contains_window_point(button, x, y):
                return "primary", button, char
        return "ignored", None, None

    def refresh_raw_pressed_visuals(self):
        active_button_ids = {
            id(target[1])
            for target in getattr(self, "raw_touch_contacts", {}).values()
            if target[0] == "primary"
        }
        for button, _char in getattr(self, "primary_buttons", ()):
            context = button.get_style_context()
            if id(button) in active_button_ids:
                context.add_class("raw-pressed")
            else:
                context.remove_class("raw-pressed")

    @staticmethod
    def shifted_primary_character(char):
        return (
            char.upper()
            if char.isalpha()
            else SHIFTED_CHARACTERS.get(char, char)
        )

    def queue_raw_touch(self, generation, actions):
        GLib.idle_add(self.on_raw_touch, generation, tuple(actions))

    def on_raw_touch(self, generation, actions):
        if (
            not self.raw_touch.is_current(generation)
            or not self.raw_touch_enabled
            or not self.visibility.visible
        ):
            return False

        anchors_at_frame_start = {
            contact
            for contact, target in self.raw_touch_contacts.items()
            if target[0] == "shift"
        }
        for action in actions:
            if not self.raw_touch.is_current(generation):
                return False
            if action.kind == "cancel":
                self.cancel_backspace_interaction()
                self.cancel_raw_touches()
                continue
            point = self.raw_to_window_point(action.x, action.y)
            if point is None:
                if action.kind == "end":
                    target = self.raw_touch_contacts.pop(action.contact, None)
                    self.refresh_raw_pressed_visuals()
                    if target is not None and target[0] == "backspace":
                        self.backspace_clear.cancel()
                continue
            if action.kind == "begin":
                if any(
                    target[0] == "backspace"
                    for target in self.raw_touch_contacts.values()
                ):
                    self.backspace_clear.cancel()
                kind, button, char = self.raw_target_at(*point)
                if kind == "backspace":
                    if not self.raw_touch_contacts and self.backspace_clear.begin(
                        action.contact, *point
                    ):
                        self.raw_touch_contacts[action.contact] = (
                            kind,
                            button,
                            None,
                        )
                    else:
                        self.raw_touch_contacts[action.contact] = (
                            "ignored",
                            None,
                            None,
                        )
                    continue
                if kind == "shift":
                    self.raw_touch_contacts[action.contact] = (kind, button, None)
                    print("keyboard-touch: raw shift begin", flush=True)
                    continue
                anchor_active = any(
                    contact in self.raw_touch_contacts
                    and self.raw_touch_contacts[contact][0] == "shift"
                    for contact in anchors_at_frame_start
                )
                if kind == "primary" and anchor_active and self.shift_held:
                    resolved = self.shifted_primary_character(char)
                    self.raw_touch_contacts[action.contact] = (
                        kind,
                        button,
                        resolved,
                    )
                    self.refresh_raw_pressed_visuals()
                    self.consume_raw_shift()
                    print("keyboard-touch: raw secondary begin", flush=True)
                else:
                    self.raw_touch_contacts[action.contact] = (
                        "ignored",
                        None,
                        None,
                    )
                continue
            if action.kind == "update":
                target = self.raw_touch_contacts.get(action.contact)
                if target is not None and target[0] == "backspace":
                    self.backspace_clear.update(action.contact, *point)
                continue
            if action.kind != "end":
                continue
            target = self.raw_touch_contacts.pop(action.contact, None)
            self.refresh_raw_pressed_visuals()
            if target is None:
                continue
            if target[0] == "backspace":
                self.backspace_clear.end(action.contact, *point)
                continue
            if target[0] != "primary":
                continue
            _kind, button, resolved = target
            if self.button_contains_window_point(button, *point):
                self.type_character(resolved)
                print("keyboard-touch: raw secondary committed", flush=True)
        return False

    def consume_raw_shift(self):
        if self.shift_held:
            self.shift_consumed = True
        if self.shifted:
            self.shifted = False
        self.update_shift_visuals()

    def cancel_raw_touches(self):
        contacts = getattr(self, "raw_touch_contacts", None)
        if contacts is not None:
            contacts.clear()
        self.refresh_raw_pressed_visuals()
        controller = getattr(self, "backspace_clear", None)
        if controller is not None:
            controller.cancel()

    def set_raw_touch_enabled(self, enabled):
        raw_touch = getattr(self, "raw_touch", None)
        if raw_touch is None:
            self.cancel_raw_touches()
            self.raw_touch_enabled = False
            return
        if enabled == self.raw_touch_enabled:
            return
        self.cancel_raw_touches()
        self.raw_touch_generation = raw_touch.set_enabled(enabled)
        self.raw_touch_enabled = enabled

    def navigation_button(self, direction, *, half=False):
        icon_name, accessible_name, code = NAVIGATION_KEYS[direction]
        css_class = "action direction direction-half" if half else "action direction"
        button = self.button(
            "",
            lambda _b, key_code=code: self.emit_key_with_feedback(key_code),
            css_class,
        )
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
        image.set_pixel_size(24)
        button.set_image(image)
        button.set_always_show_image(True)
        button.get_accessible().set_name(accessible_name)
        return button

    def navigation_cluster(self):
        cluster = Gtk.Grid()
        cluster.set_hexpand(True)
        cluster.set_vexpand(True)
        cluster.set_column_homogeneous(True)
        cluster.set_row_homogeneous(True)
        cluster.set_column_spacing(8)
        cluster.set_row_spacing(8)
        cluster.attach(self.navigation_button("left"), 0, 0, 1, 2)
        cluster.attach(self.navigation_button("up", half=True), 1, 0, 1, 1)
        cluster.attach(self.navigation_button("down", half=True), 1, 1, 1, 1)
        cluster.attach(self.navigation_button("right"), 2, 0, 1, 2)
        return cluster

    def utility_button(
        self, label, callback, accessible_name, style_class="utility"
    ):
        def activate(button):
            self.play_key_sound()
            return callback(button)

        button = self.button(label, activate, style_class, vexpand=False)
        button.get_accessible().set_name(accessible_name)
        return button

    def quick_toolbar(self):
        toolbar = Gtk.Grid()
        toolbar.set_hexpand(True)
        toolbar.set_vexpand(False)
        toolbar.set_column_homogeneous(True)
        toolbar.set_column_spacing(10)
        toolbar.set_margin_top(10)
        toolbar.set_margin_start(10)
        toolbar.set_margin_end(10)
        self.window_transfer_button = self.utility_button(
            "移上屏",
            self.move_app_windows_up,
            "将下屏所有应用窗口移到上屏",
        )
        self.render_window_transfer_button()
        actions = (
            self.utility_button(
                "Esc",
                lambda _b: self.emit_key(ecodes.KEY_ESC),
                "Escape",
            ),
            self.utility_button(
                "Tab",
                lambda _b: self.emit_key(ecodes.KEY_TAB),
                "Tab",
            ),
            self.utility_button(
                "面板",
                self.manual_hide,
                "收起键盘，返回下屏面板",
                "utility utility-mode",
            ),
            self.utility_button(
                "触摸板",
                self.open_touchpad,
                "切换到下屏触摸板",
                "utility utility-mode",
            ),
            self.utility_button(
                "桌面", self.show_desktop, "收起键盘并显示桌面"
            ),
            self.utility_button(
                "设置", self.open_feedback_settings, "键盘反馈设置"
            ),
            self.utility_button(
                "终端",
                lambda _b: self.launch_quick_app("terminal"),
                "打开终端",
            ),
            self.window_transfer_button,
            self.utility_button(
                "Alt+F4",
                self.close_foreground_window,
                "关闭当前窗口（Alt+F4）",
                "utility utility-close",
            ),
        )
        for column, button in enumerate(actions):
            toolbar.attach(button, column, 0, 1, 1)
        return toolbar

    def feedback_nav_button(self, label, callback, accessible_name):
        def activate(button):
            self.play_key_sound()
            return callback(button)

        button = self.button(label, activate, "settings-nav", vexpand=False)
        button.get_accessible().set_name(accessible_name)
        return button

    @staticmethod
    def feedback_label(text, css_class, *, xalign=0.0):
        label = Gtk.Label(label=text)
        label.set_xalign(xalign)
        label.get_style_context().add_class(css_class)
        return label

    def feedback_scale(self, channel, level):
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 5, 1)
        scale.set_value(level)
        scale.set_draw_value(False)
        scale.set_round_digits(0)
        scale.set_hexpand(True)
        scale.set_can_focus(False)
        for mark in range(1, 6):
            scale.add_mark(mark, Gtk.PositionType.BOTTOM, None)
        scale.get_accessible().set_name(
            "按键音强度" if channel == "sound" else "键盘震动强度"
        )
        scale.get_style_context().add_class("feedback-scale")
        if channel == "sound":
            scale.connect("value-changed", self.on_sound_level_changed)
        else:
            scale.connect("value-changed", self.on_haptic_level_changed)
        scale.connect(
            "button-release-event",
            self.on_feedback_scale_released,
            channel,
        )
        scale.connect("touch-event", self.on_feedback_scale_touch, channel)
        return scale

    def feedback_card(self, channel, title, level, enabled):
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.NONE)
        frame.set_hexpand(True)
        frame.set_vexpand(True)
        frame.get_style_context().add_class("feedback-card")

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        card.set_margin_top(20)
        card.set_margin_bottom(20)
        card.set_margin_start(22)
        card.set_margin_end(22)

        header = Gtk.Grid()
        header.set_hexpand(True)
        header.set_column_spacing(14)
        title_label = self.feedback_label(title, "feedback-title")
        title_label.set_hexpand(True)
        value_label = self.feedback_label(
            f"{level}/5", "feedback-value", xalign=0.5
        )
        toggle = Gtk.ToggleButton(label="开" if enabled else "关")
        toggle.set_relief(Gtk.ReliefStyle.NONE)
        toggle.set_focus_on_click(False)
        toggle.set_can_focus(False)
        toggle.set_hexpand(False)
        toggle.set_active(enabled)
        toggle.get_style_context().add_class("feedback-toggle")
        if channel == "sound":
            toggle.get_accessible().set_name("按键音开关")
            toggle.connect("toggled", self.on_sound_toggled)
            self.sound_toggle = toggle
            self.sound_level_label = value_label
        else:
            toggle.get_accessible().set_name("键盘震动开关")
            toggle.connect("toggled", self.on_haptic_toggled)
            self.haptic_toggle = toggle
            self.haptic_level_label = value_label
        header.attach(title_label, 0, 0, 1, 1)
        header.attach(toggle, 1, 0, 1, 1)

        controls = Gtk.Grid()
        controls.set_hexpand(True)
        controls.set_valign(Gtk.Align.CENTER)
        controls.set_column_spacing(20)
        controls.set_row_spacing(4)
        scale = self.feedback_scale(channel, level)
        controls.attach(scale, 0, 0, 3, 1)
        controls.attach(self.feedback_label("轻", "feedback-edge"), 0, 1, 1, 1)
        value_label.set_hexpand(True)
        controls.attach(value_label, 1, 1, 1, 1)
        controls.attach(
            self.feedback_label("强", "feedback-edge", xalign=1.0), 2, 1, 1, 1
        )
        if channel == "sound":
            self.sound_scale = scale
            preview = self.button(
                "试听", self.preview_key_sound, "feedback-preview", vexpand=False
            )
            preview.get_accessible().set_name("试听当前按键音")
            self.sound_preview = preview
        else:
            self.haptic_scale = scale
            preview = self.button(
                "试震", self.preview_key_haptic, "feedback-preview", vexpand=False
            )
            preview.get_accessible().set_name("试震当前键盘震动")
            self.haptic_preview = preview
        scale.set_sensitive(enabled)
        preview.set_sensitive(enabled)
        preview.set_hexpand(False)
        preview.set_valign(Gtk.Align.CENTER)
        controls.attach(preview, 3, 0, 1, 2)

        card.pack_start(header, False, False, 0)
        card.pack_start(controls, True, True, 0)
        frame.add(card)
        return frame

    def open_feedback_settings(self, _button=None):
        if self.settings_visible:
            return False
        self.cancel_backspace_interaction()
        self.cancel_space_hold()
        self.cancel_shift_press()
        self.set_raw_touch_enabled(False)
        self.settings_visible = True
        old = self.window.get_child()
        if old is not None:
            self.window.remove(old)

        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        shell.set_hexpand(True)
        shell.set_vexpand(True)
        shell.set_margin_top(10)
        shell.set_margin_bottom(10)
        shell.set_margin_start(10)
        shell.set_margin_end(10)

        header = Gtk.Grid()
        header.set_hexpand(True)
        header.set_column_homogeneous(True)
        header.set_column_spacing(10)
        back = self.feedback_nav_button(
            "返回", self.close_feedback_settings, "返回键盘"
        )
        title = self.feedback_label(
            "按键反馈", "feedback-page-title", xalign=0.5
        )
        reset = self.button(
            "恢复默认",
            self.reset_feedback_settings,
            "settings-nav settings-reset",
            vexpand=False,
        )
        reset.get_accessible().set_name("恢复按键反馈默认值")
        header.attach(back, 0, 0, 2, 1)
        header.attach(title, 2, 0, 4, 1)
        header.attach(reset, 6, 0, 2, 1)

        settings = self.feedback_settings
        shell.pack_start(header, False, False, 0)
        shell.pack_start(
            self.feedback_card(
                "sound",
                "按键音",
                settings.sound_level,
                settings.sound_enabled,
            ),
            True,
            True,
            0,
        )
        shell.pack_start(
            self.feedback_card(
                "haptic",
                "键盘震动",
                settings.haptic_level,
                settings.haptic_enabled,
            ),
            True,
            True,
            0,
        )
        if old is None:
            self.window.add(shell)
        else:
            # Keep the mature keyboard layout as an invisible size keeper.
            # Without it GTK republishes the settings page's narrow natural
            # size and KWin leaves the lower-screen Panel exposed on the right.
            old.set_sensitive(False)
            old.set_opacity(0.0)
            overlay = Gtk.Overlay()
            overlay.set_hexpand(True)
            overlay.set_vexpand(True)
            overlay.add(old)
            shell.set_halign(Gtk.Align.FILL)
            shell.set_valign(Gtk.Align.FILL)
            overlay.add_overlay(shell)
            self.window.add(overlay)
        if self.visibility.visible:
            self.apply_visibility_snapshot(self.visibility.snapshot())
        self.set_raw_touch_enabled(False)
        return False

    def close_feedback_settings(self, _button=None):
        if self.settings_visible:
            self.build_layout()
        return False

    def replace_feedback_settings(self, **changes):
        current = self.feedback_settings
        values = {
            "sound_enabled": current.sound_enabled,
            "sound_level": current.sound_level,
            "haptic_enabled": current.haptic_enabled,
            "haptic_level": current.haptic_level,
        }
        values.update(changes)
        self.feedback_settings = KeyboardFeedbackSettings(**values)
        return self.feedback_settings

    def schedule_feedback_settings_save(self):
        source = self.feedback_save_source
        if source:
            GLib.source_remove(source)
        self.feedback_save_source = GLib.timeout_add(
            FEEDBACK_SAVE_DELAY_MS,
            self.flush_feedback_settings,
        )

    def flush_feedback_settings(self):
        self.feedback_save_source = 0
        if not self.feedback_store.save(self.feedback_settings):
            print("keyboard feedback settings save failed", flush=True)
        return False

    def on_sound_toggled(self, button):
        if self.feedback_ui_updating:
            return
        enabled = bool(button.get_active())
        button.set_label("开" if enabled else "关")
        settings = self.replace_feedback_settings(sound_enabled=enabled)
        self.key_sound.configure(enabled=settings.sound_enabled)
        if self.sound_scale is not None:
            self.sound_scale.set_sensitive(enabled)
        if self.sound_preview is not None:
            self.sound_preview.set_sensitive(enabled)
        self.schedule_feedback_settings_save()
        if enabled:
            self.preview_key_sound()

    def on_haptic_toggled(self, button):
        if self.feedback_ui_updating:
            return
        enabled = bool(button.get_active())
        button.set_label("开" if enabled else "关")
        settings = self.replace_feedback_settings(haptic_enabled=enabled)
        self.key_haptic.configure(enabled=settings.haptic_enabled)
        if self.haptic_scale is not None:
            self.haptic_scale.set_sensitive(enabled)
        if self.haptic_preview is not None:
            self.haptic_preview.set_sensitive(enabled)
        self.schedule_feedback_settings_save()
        if enabled:
            self.preview_key_haptic()

    def on_sound_level_changed(self, scale):
        if self.feedback_ui_updating:
            return
        level = max(1, min(5, int(round(scale.get_value()))))
        if self.sound_level_label is not None:
            self.sound_level_label.set_text(f"{level}/5")
        settings = self.replace_feedback_settings(sound_level=level)
        self.key_sound.configure(volume_db=SOUND_LEVEL_DB[level - 1])
        self.schedule_feedback_settings_save()
        return settings

    def on_haptic_level_changed(self, scale):
        if self.feedback_ui_updating:
            return
        level = max(1, min(5, int(round(scale.get_value()))))
        if self.haptic_level_label is not None:
            self.haptic_level_label.set_text(f"{level}/5")
        settings = self.replace_feedback_settings(haptic_level=level)
        intensity, duration_ms = HAPTIC_LEVELS[level - 1]
        self.key_haptic.configure(
            intensity=intensity,
            duration_ms=duration_ms,
        )
        self.schedule_feedback_settings_save()
        return settings

    def on_feedback_scale_released(self, _scale, _event, channel):
        if time.monotonic() - self.feedback_touch_released_at[channel] < 0.20:
            return False
        if channel == "sound":
            self.preview_key_sound()
        else:
            self.preview_key_haptic()
        return False

    def on_feedback_scale_touch(self, _scale, event, channel):
        if event.type == Gdk.EventType.TOUCH_END:
            self.feedback_touch_released_at[channel] = time.monotonic()
            if channel == "sound":
                self.preview_key_sound()
            else:
                self.preview_key_haptic()
        return False

    def preview_key_sound(self, _button=None):
        self.key_sound.play()
        return False

    def preview_key_haptic(self, _button=None):
        self.key_haptic.play()
        return False

    def reset_feedback_settings(self, _button=None):
        settings = self.feedback_defaults
        self.feedback_settings = settings
        self.feedback_ui_updating = True
        try:
            if self.sound_toggle is not None:
                self.sound_toggle.set_active(settings.sound_enabled)
                self.sound_toggle.set_label("开" if settings.sound_enabled else "关")
            if self.haptic_toggle is not None:
                self.haptic_toggle.set_active(settings.haptic_enabled)
                self.haptic_toggle.set_label("开" if settings.haptic_enabled else "关")
            if self.sound_scale is not None:
                self.sound_scale.set_value(settings.sound_level)
            if self.haptic_scale is not None:
                self.haptic_scale.set_value(settings.haptic_level)
            if self.sound_level_label is not None:
                self.sound_level_label.set_text(f"{settings.sound_level}/5")
            if self.haptic_level_label is not None:
                self.haptic_level_label.set_text(f"{settings.haptic_level}/5")
            if self.sound_scale is not None:
                self.sound_scale.set_sensitive(settings.sound_enabled)
            if self.sound_preview is not None:
                self.sound_preview.set_sensitive(settings.sound_enabled)
            if self.haptic_scale is not None:
                self.haptic_scale.set_sensitive(settings.haptic_enabled)
            if self.haptic_preview is not None:
                self.haptic_preview.set_sensitive(settings.haptic_enabled)
        finally:
            self.feedback_ui_updating = False
        self.key_sound.configure(
            enabled=settings.sound_enabled,
            volume_db=SOUND_LEVEL_DB[settings.sound_level - 1],
        )
        haptic_intensity, haptic_duration_ms = HAPTIC_LEVELS[
            settings.haptic_level - 1
        ]
        self.key_haptic.configure(
            enabled=settings.haptic_enabled,
            intensity=haptic_intensity,
            duration_ms=haptic_duration_ms,
        )
        self.schedule_feedback_settings_save()
        self.play_key_sound()
        return False

    def shift_is_active(self):
        return getattr(self, "shifted", False) or getattr(self, "shift_held", False)

    def primary_character_label(self, char):
        if not self.shift_is_active():
            return char
        return (
            char.upper()
            if char.isalpha()
            else SHIFTED_CHARACTERS.get(char, char)
        )

    def primary_character_button(self, char):
        button = self.button(
            self.primary_character_label(char),
            lambda _b, value=char: self.type_primary_character(value),
            "key",
        )
        self.primary_buttons.append((button, char))
        return button

    def shift_button(self):
        css_class = "key action shift-on" if self.shift_is_active() else "key action"
        button = self.button("Shift", self.shift_clicked, css_class)
        button.connect("pressed", self.shift_pressed)
        button.connect("released", self.shift_released)
        button.connect("grab-broken-event", self.shift_cancelled)
        button.connect("touch-event", self.shift_touch_event)
        button.connect("unmap", self.shift_cancelled)
        self.shift_buttons.append(button)
        return button

    def update_shift_visuals(self):
        active = self.shift_is_active()
        for button, char in getattr(self, "primary_buttons", ()):
            button.set_label(self.primary_character_label(char))
        for button in getattr(self, "shift_buttons", ()):
            context = button.get_style_context()
            if active:
                context.add_class("shift-on")
            else:
                context.remove_class("shift-on")

    def space_voice_button(self):
        button = self.button("", self.space_clicked, "action space")
        original_label = button.get_child()
        if original_label is not None:
            button.remove(original_label)
        button.connect("pressed", self.space_pressed)
        button.connect("released", self.space_released)
        button.connect("grab-broken-event", self.space_cancelled)
        button.connect("touch-event", self.space_touch_event)
        button.connect("unmap", self.space_cancelled)
        button.get_accessible().set_name("空格；按住说话；松手识别")
        self.mic_button = button
        self.mic_status_stack = Gtk.Stack()
        self.mic_status_stack.set_homogeneous(True)
        self.mic_status_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        icons = {
            VoiceState.IDLE: "audio-input-microphone-symbolic",
            VoiceState.CONNECTING: "view-refresh-symbolic",
            VoiceState.RECORDING: "audio-input-microphone-symbolic",
            VoiceState.STOPPING: "view-refresh-symbolic",
            VoiceState.RECOGNIZING: "view-refresh-symbolic",
            VoiceState.PENDING_PASTE: "edit-paste-symbolic",
            VoiceState.SUCCESS: "emblem-ok-symbolic",
            VoiceState.ERROR: "dialog-warning-symbolic",
            VoiceState.DISCARDED: "process-stop-symbolic",
        }
        for state, label_text in VOICE_STATE_LABELS.items():
            content = self.voice_status_content(label_text, icons[state])
            if state is VoiceState.ERROR:
                self.mic_error_label = content.get_children()[-1]
            self.mic_status_stack.add_named(content, state.value)
        self.mic_status_stack.add_named(
            self.voice_status_content(
                "空格 · 语音暂停", "media-playback-pause-symbolic"
            ),
            "disabled",
        )
        button.add(self.mic_status_stack)
        self.render_voice_snapshot(self.voice.snapshot())
        return button

    @staticmethod
    def voice_status_content(label_text, icon_name):
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        content.set_halign(Gtk.Align.CENTER)
        content.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
        icon.set_pixel_size(20)
        content.pack_start(icon, False, False, 0)
        content.pack_start(Gtk.Label(label=label_text), False, False, 0)
        return content

    def backspace_button(self):
        button = self.button("Backspace", lambda _b: None, "key action")
        original_label = button.get_child()
        if original_label is not None:
            button.remove(original_label)
        status_stack = Gtk.Stack()
        status_stack.set_homogeneous(True)
        status_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        labels = {
            BackspaceClearGestureController.FEEDBACK_IDLE: "Backspace",
            BackspaceClearGestureController.FEEDBACK_HELD: "Swipe ↑",
            BackspaceClearGestureController.FEEDBACK_READY: "Release",
        }
        for state, label in labels.items():
            status_stack.add_named(Gtk.Label(label=label), state)
        status_stack.set_visible_child_name(
            BackspaceClearGestureController.FEEDBACK_IDLE
        )
        button.add(status_stack)
        button.get_accessible().set_name(
            "Backspace; hold, swipe up, and release to clear the current input"
        )
        button.connect("pressed", self.backspace_pressed)
        button.connect("released", self.backspace_released)
        button.connect("grab-broken-event", self.backspace_cancelled)
        button.connect("touch-event", self.backspace_touch_event)
        button.connect("unmap", self.backspace_cancelled)
        self.backspace_buttons.append(button)
        self.backspace_status_stacks.append(status_stack)
        return button

    def build_layout(self):
        self.settings_visible = False
        self.sound_toggle = None
        self.haptic_toggle = None
        self.sound_scale = None
        self.haptic_scale = None
        self.sound_level_label = None
        self.haptic_level_label = None
        self.cancel_backspace_interaction()
        self.cancel_space_hold()
        self.cancel_shift_press()
        self.set_raw_touch_enabled(False)
        old = self.window.get_child()
        if old is not None:
            self.window.remove(old)

        self.backspace_buttons = []
        self.backspace_status_stacks = []
        self.primary_buttons = []
        self.shift_buttons = []
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        shell.set_hexpand(True)
        shell.set_vexpand(True)
        grid = Gtk.Grid()
        grid.set_hexpand(True)
        grid.set_vexpand(True)
        grid.set_column_homogeneous(True)
        grid.set_row_homogeneous(False)
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        grid.set_margin_top(0)
        grid.set_margin_bottom(10)
        grid.set_margin_start(10)
        grid.set_margin_end(10)

        if self.symbols:
            rows = [
                list("1234567890"),
                ["!", "@", "#", "$", "%", "^", "&", "*", "(", ")"],
                ["-", "_", "=", "+", "[", "]", "{", "}", "\\", "/"],
                ["~", ":", ";", "'", '"', ",", ".", "<", ">", "?"],
            ]
            for row_index, chars in enumerate(rows):
                column = 0
                for col, char in enumerate(chars):
                    column_width = (
                        (5 if col < 7 else 4) if row_index == 3 else 6
                    )
                    grid.attach(
                        self.button(
                            char,
                            lambda _b, c=char: self.type_character(c),
                            "key",
                        ),
                        column,
                        row_index,
                        column_width,
                        1,
                    )
                    column += column_width
                if row_index == 3:
                    grid.attach(self.backspace_button(), 47, 3, 13, 1)
        else:
            rows = [list("qwertyuiop"), list("asdfghjkl"), list("zxcvbnm")]
            for col, char in enumerate("1234567890"):
                grid.attach(
                    self.primary_character_button(char),
                    col * 6,
                    0,
                    6,
                    1,
                )
            for col, char in enumerate(rows[0]):
                grid.attach(
                    self.primary_character_button(char),
                    col * 6,
                    1,
                    6,
                    1,
                )
            for col, char in enumerate(rows[1]):
                grid.attach(
                    self.primary_character_button(char),
                    3 + col * 5,
                    2,
                    5,
                    1,
                )
            grid.attach(self.primary_character_button(":"), 48, 2, 6, 1)
            enter = self.button("Enter", self.send_enter, "key action")
            enter.get_style_context().add_class("enter")
            grid.attach(enter, 54, 2, 6, 1)
            grid.attach(
                self.shift_button(),
                0,
                3,
                7,
                1,
            )
            for col, char in enumerate(rows[2]):
                grid.attach(
                    self.primary_character_button(char),
                    7 + col * 5,
                    3,
                    5,
                    1,
                )
            for col, char in enumerate((".", "/")):
                grid.attach(
                    self.primary_character_button(char),
                    42 + col * 4,
                    3,
                    4,
                    1,
                )
            grid.attach(self.backspace_button(), 50, 3, 10, 1)

        symbol_class = "action shift-on" if self.symbols else "action"
        grid.attach(self.button("#+=", self.toggle_symbols, symbol_class), 0, 4, 7, 1)
        grid.attach(self.button("中/英", self.toggle_language, "action"), 7, 4, 7, 1)
        grid.attach(self.space_voice_button(), 14, 4, 30, 1)
        grid.attach(self.navigation_cluster(), 44, 4, 16, 1)

        shell.pack_start(self.quick_toolbar(), False, False, 0)
        shell.pack_start(grid, True, True, 0)
        self.window.add(shell)
        if self.visibility.visible:
            self.apply_visibility_snapshot(self.visibility.snapshot())

    def emit_key(self, code, shifted=False):
        if shifted:
            self.uinput.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 1)
        self.uinput.write(ecodes.EV_KEY, code, 1)
        self.uinput.syn()
        self.uinput.write(ecodes.EV_KEY, code, 0)
        if shifted:
            self.uinput.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 0)
        self.uinput.syn()

    def emit_key_with_feedback(self, code, shifted=False):
        self.play_key_sound()
        self.emit_key(code, shifted)

    def emit_combo(self, modifier, key):
        self.emit_hotkey((modifier,), key)

    def emit_hotkey(self, modifiers, key):
        for modifier in modifiers:
            self.uinput.write(ecodes.EV_KEY, modifier, 1)
        self.uinput.write(ecodes.EV_KEY, key, 1)
        self.uinput.syn()
        self.uinput.write(ecodes.EV_KEY, key, 0)
        for modifier in reversed(modifiers):
            self.uinput.write(ecodes.EV_KEY, modifier, 0)
        self.uinput.syn()

    def type_character(self, char):
        self.play_key_sound()
        lookup = char.lower() if char.isalpha() else char
        code, needs_shift = CHAR_KEYS[lookup]
        shifted = needs_shift or (char.isalpha() and char.isupper())
        self.emit_key(code, shifted)

    def type_primary_character(self, char):
        self.type_character(self.primary_character_label(char))
        if self.shift_held:
            self.shift_consumed = True
        if self.shifted:
            self.shifted = False
        self.update_shift_visuals()

    def shift_pressed(self, _button):
        if self.shift_held:
            return
        self.play_key_sound(modifier=True)
        if self.shift_release_source:
            GLib.source_remove(self.shift_release_source)
            self.shift_release_source = 0
        self.shift_latched_before_press = self.shifted
        self.shift_consumed = False
        self.shift_held = True
        self.update_shift_visuals()

    def shift_released(self, _button):
        if self.shift_held and not self.shift_release_source:
            self.shift_release_source = GLib.idle_add(
                self.finish_unclicked_shift_release
            )

    def shift_clicked(self, _button):
        if self.shift_release_source:
            GLib.source_remove(self.shift_release_source)
            self.shift_release_source = 0
        if not self.shift_held:
            self.shifted = not self.shifted
        elif self.shift_consumed:
            self.shifted = False
        else:
            self.shifted = not self.shift_latched_before_press
        self.shift_held = False
        self.shift_consumed = False
        self.update_shift_visuals()

    def finish_unclicked_shift_release(self):
        self.shift_release_source = 0
        if self.shift_held:
            self.shifted = (
                False if self.shift_consumed else self.shift_latched_before_press
            )
            self.shift_held = False
            self.shift_consumed = False
            self.update_shift_visuals()
        return False

    def shift_cancelled(self, *_args):
        self.cancel_shift_press()
        return False

    def shift_touch_event(self, _button, event):
        if event.type == Gdk.EventType.TOUCH_CANCEL:
            self.cancel_shift_press()
        return False

    def cancel_shift_press(self):
        release_source = getattr(self, "shift_release_source", 0)
        if release_source:
            GLib.source_remove(release_source)
            self.shift_release_source = 0
        if getattr(self, "shift_held", False):
            self.shifted = (
                False
                if getattr(self, "shift_consumed", False)
                else getattr(self, "shift_latched_before_press", False)
            )
        self.shift_held = False
        self.shift_consumed = False
        self.update_shift_visuals()

    def reset_shift_state(self):
        self.cancel_shift_press()
        self.shifted = False
        self.update_shift_visuals()

    def toggle_symbols(self, _button):
        self.play_key_sound(modifier=True)
        self.cancel_shift_press()
        self.symbols = not self.symbols
        self.shifted = False
        self.build_layout()

    def toggle_language(self, _button):
        self.play_key_sound(modifier=True)
        self.emit_combo(ecodes.KEY_LEFTCTRL, ecodes.KEY_SPACE)

    def space_tapped(self):
        snapshot = self.voice.snapshot()
        if snapshot.busy:
            self.note_ignored_voice_activation(snapshot)
        else:
            self.play_key_sound()
            self.emit_key(ecodes.KEY_SPACE)

    def space_held(self):
        generation = self.start_recording()
        self.space_voice_generation = generation or 0
        self.space_voice_release_generation = 0

    def space_voice_released(self):
        generation = self.space_voice_generation
        if not generation:
            return
        snapshot = self.voice.snapshot()
        if snapshot.generation != generation:
            self.clear_space_voice_session(generation)
            return
        if snapshot.state is VoiceState.CONNECTING:
            if getattr(self, "voice_recorder_route", None) is None:
                # No recorder exists during source discovery. Releasing now
                # must not let a late lookup start capturing after the hold.
                self.cancel_voice("space released during microphone lookup")
                return
            self.space_voice_release_generation = generation
            return
        if snapshot.state is VoiceState.RECORDING:
            self.clear_space_voice_session(generation)
            self.stop_recording()
            return
        self.clear_space_voice_session(generation)

    def finish_deferred_space_voice(self, generation):
        if (
            self.space_voice_generation != generation
            or self.space_voice_release_generation != generation
        ):
            return False
        snapshot = self.voice.snapshot()
        if (
            snapshot.generation != generation
            or snapshot.state is not VoiceState.RECORDING
            or self.voice_process is None
        ):
            self.clear_space_voice_session(generation)
            return False
        self.clear_space_voice_session(generation)
        self.stop_recording()
        return False

    def clear_space_voice_session(self, generation=None):
        for name in ("space_voice_generation", "space_voice_release_generation"):
            value = getattr(self, name, 0)
            if generation is None or value == generation:
                setattr(self, name, 0)

    def space_pressed(self, _button):
        self.space_hold.press()

    def space_released(self, _button):
        self.space_hold.release()

    def space_clicked(self, _button):
        self.space_hold.click()

    def space_cancelled(self, *_args):
        self.cancel_space_hold()
        return False

    def space_touch_event(self, _button, event):
        if event.type == Gdk.EventType.TOUCH_CANCEL:
            self.cancel_space_hold()
        return False

    def cancel_space_hold(self):
        if not self.space_hold.cancel():
            return
        generation = self.space_voice_generation
        snapshot = self.voice.snapshot()
        if (
            generation
            and snapshot.generation == generation
            and snapshot.state in {VoiceState.CONNECTING, VoiceState.RECORDING}
        ):
            self.cancel_voice("space hold cancelled")
        elif generation:
            self.clear_space_voice_session(generation)

    def send_enter(self, _button):
        self.play_key_sound()
        self.emit_key(ecodes.KEY_ENTER)

    def open_touchpad(self, _button=None):
        self.manual_hide()
        GLib.spawn_async(["/usr/local/bin/pocketds-panelctl", "touchpad"])
        return False

    def close_foreground_window(self, _button=None):
        self.emit_hotkey((ecodes.KEY_LEFTALT,), ecodes.KEY_F4)
        return False

    def show_desktop(self, _button=None):
        self.manual_hide()
        try:
            kwin = self.session_bus.get_object("org.kde.KWin", "/KWin")
            interface = dbus.Interface(kwin, "org.kde.KWin")
            interface.showDesktop(dbus.Boolean(True))
        except dbus.DBusException as exc:
            print(f"show desktop failed: {exc}", flush=True)
        return False

    def render_window_transfer_button(self):
        button = self.window_transfer_button
        if button is None:
            return
        if self.window_transfer_busy:
            label, accessible_name = "移动中", "正在将下屏应用窗口移到上屏"
        elif self.window_transfer_failed:
            label, accessible_name = "重试", "移动请求失败，点击重试将下屏应用窗口移到上屏"
        else:
            label, accessible_name = "移上屏", "将下屏所有应用窗口移到上屏"
        button.set_label(label)
        button.get_accessible().set_name(accessible_name)
        button.set_sensitive(not self.window_transfer_busy)

    def move_app_windows_up(self, _button=None):
        if (
            self.window_transfer_busy
            or self.window_transfer_closing.is_set()
        ):
            return False
        if self.window_transfer_feedback_source:
            GLib.source_remove(self.window_transfer_feedback_source)
            self.window_transfer_feedback_source = 0
        self.window_transfer_busy = True
        self.window_transfer_failed = False
        self.render_window_transfer_button()
        try:
            threading.Thread(
                target=self.request_window_transfer,
                name="pocketds-windows-to-top",
                daemon=True,
            ).start()
        except RuntimeError:
            self.finish_window_transfer(False)
        return False

    def request_window_transfer(self):
        # Keep the keyboard visible and responsive while KWin handles the move.
        submitted = False
        try:
            if not self.window_transfer_closing.is_set():
                submitted = move_lower_windows_to_upper(runtime=RUNTIME)
        except Exception as exc:
            print(f"window transfer failed: {type(exc).__name__}", flush=True)
        if not self.window_transfer_closing.is_set():
            GLib.idle_add(self.finish_window_transfer, submitted)

    def finish_window_transfer(self, submitted):
        if self.window_transfer_closing.is_set():
            return False
        self.window_transfer_busy = False
        # A normal D-Bus reply acknowledges execution, not a moved-window count.
        self.window_transfer_failed = not submitted
        self.render_window_transfer_button()
        if not submitted:
            print("window transfer: KWin request failed", flush=True)
            self.window_transfer_feedback_source = GLib.timeout_add(
                2500, self.clear_window_transfer_feedback
            )
        return False

    def clear_window_transfer_feedback(self):
        self.window_transfer_feedback_source = 0
        self.window_transfer_failed = False
        if not self.window_transfer_closing.is_set():
            self.render_window_transfer_button()
        return False

    def launch_quick_app(self, app_name):
        command = QUICK_APP_COMMANDS.get(app_name)
        if command is None:
            return False
        now = time.monotonic()
        if now < self.quick_action_not_before.get(app_name, 0.0):
            return False
        self.quick_action_not_before[app_name] = now + QUICK_APP_DEBOUNCE_S
        if app_name == "terminal":
            # Hide immediately even if KWin scripting is unavailable, so a
            # lower-screen fallback can never be covered by this keyboard.
            self.manual_hide()
            try:
                worker = threading.Thread(
                    target=self.prepare_and_launch_terminal,
                    args=(command,),
                    name="pocketds-konsole-placement",
                    daemon=True,
                )
                worker.start()
            except RuntimeError:
                self.finish_quick_app_launch(app_name, command, None)
            return False
        return self.finish_quick_app_launch(app_name, command, None)

    def prepare_and_launch_terminal(self, command):
        placement = prepare_terminal_placement()
        if placement is not None:
            script_name, script_path = placement
            with self.terminal_placement_lock:
                if self.terminal_closing.is_set():
                    cleanup_now = True
                else:
                    cleanup_now = False
                    self.terminal_pending_placements[script_name] = script_path
            if cleanup_now:
                unload_terminal_placement(script_name, script_path)
                return
        elif self.terminal_closing.is_set():
            return
        GLib.idle_add(
            self.finish_quick_app_launch,
            "terminal",
            command,
            placement,
        )

    def finish_quick_app_launch(self, app_name, command, placement):
        if placement is not None:
            script_name, script_path = placement
            with self.terminal_placement_lock:
                self.terminal_pending_placements.pop(script_name, None)
                closing = self.terminal_closing.is_set()
            if closing:
                self.start_terminal_cleanup(script_name, script_path)
                return False
        launched = False
        try:
            # Delegate to the user manager so the terminal does not inherit
            # this service's private user/mount namespace. --separate also
            # prevents reusing a Konsole process from that old namespace.
            GLib.spawn_async([
                "/usr/bin/systemd-run", "--user", "--quiet", "--collect",
                "--service-type=exec", "--", *command,
            ])
            launched = True
        except Exception as exc:
            self.quick_action_not_before.pop(app_name, None)
            print(f"quick app {app_name} failed: {type(exc).__name__}", flush=True)
        if placement is not None:
            script_name, script_path = placement
            if launched:
                source = GLib.timeout_add(
                    TERMINAL_PLACEMENT_LIFETIME_MS,
                    self.expire_terminal_placement,
                    script_name,
                )
                self.terminal_placements[script_name] = (script_path, source)
            else:
                self.start_terminal_cleanup(script_name, script_path)
        return False

    def expire_terminal_placement(self, script_name):
        placement = self.terminal_placements.pop(script_name, None)
        if placement is not None:
            script_path, _source = placement
            self.start_terminal_cleanup(script_name, script_path)
        return False

    @staticmethod
    def start_terminal_cleanup(script_name, script_path):
        try:
            threading.Thread(
                target=unload_terminal_placement,
                args=(script_name, script_path),
                name="pocketds-konsole-cleanup",
                daemon=True,
            ).start()
        except RuntimeError:
            unload_terminal_placement(script_name, script_path)

    def cleanup_terminal_placements(self):
        self.terminal_closing.set()
        with self.terminal_placement_lock:
            pending = tuple(self.terminal_pending_placements.items())
            self.terminal_pending_placements.clear()
        placements = tuple(self.terminal_placements.items())
        self.terminal_placements.clear()
        for script_name, script_path in pending:
            unload_terminal_placement(script_name, script_path)
        for script_name, (script_path, source) in placements:
            if source:
                try:
                    GLib.source_remove(source)
                except Exception:
                    pass
            unload_terminal_placement(script_name, script_path)

    def render_backspace_clear_state(self, state):
        if state == BackspaceClearGestureController.FEEDBACK_READY:
            self.cancel_backspace_repeat()
        feedback_states = {
            BackspaceClearGestureController.FEEDBACK_IDLE,
            BackspaceClearGestureController.FEEDBACK_HELD,
            BackspaceClearGestureController.FEEDBACK_READY,
        }
        visible_state = (
            state
            if state in feedback_states
            else BackspaceClearGestureController.FEEDBACK_IDLE
        )
        for button, status_stack in zip(
            self.backspace_buttons, self.backspace_status_stacks
        ):
            context = button.get_style_context()
            context.remove_class("backspace-held")
            context.remove_class("backspace-ready")
            if state == BackspaceClearGestureController.FEEDBACK_HELD:
                context.add_class("backspace-held")
            elif state == BackspaceClearGestureController.FEEDBACK_READY:
                context.add_class("backspace-ready")
            status_stack.set_visible_child_name(visible_state)

    def clear_current_input(self):
        self.cancel_backspace_repeat()
        self.emit_hotkey((ecodes.KEY_LEFTCTRL,), ecodes.KEY_A)
        self.emit_key(ecodes.KEY_BACKSPACE)

    def backspace_pressed(self, _button):
        if self.backspace_repeat.start():
            self.play_key_sound()

    def backspace_released(self, _button):
        self.cancel_backspace_repeat()

    def backspace_cancelled(self, *_args):
        self.cancel_backspace_interaction()
        return False

    def backspace_touch_event(self, _button, event):
        if event.type == Gdk.EventType.TOUCH_CANCEL:
            self.cancel_backspace_interaction()
        return False

    def cancel_backspace_repeat(self):
        self.backspace_repeat.cancel()

    def cancel_backspace_interaction(self):
        repeat = getattr(self, "backspace_repeat", None)
        if repeat is not None:
            repeat.cancel()
        clear_gesture = getattr(self, "backspace_clear", None)
        if clear_gesture is not None:
            clear_gesture.cancel()

    @staticmethod
    def a11y_state_bits(values):
        return int(values[0]) if values else 0

    @staticmethod
    def a11y_call_timeout(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("AT-SPI focus recovery deadline expired")
        return min(A11Y_FOCUS_CALL_TIMEOUT_S, remaining)

    def a11y_accessible_interface(self, bus_name, object_path):
        if self.a11y_bus is None:
            self.a11y_bus = dbus.bus.BusConnection(A11Y_BUS_ADDRESS)
        obj = self.a11y_bus.get_object(
            str(bus_name),
            str(object_path),
            introspect=False,
        )
        return dbus.Interface(obj, "org.a11y.atspi.Accessible")

    def find_focused_accessible_chain(self):
        """Find one focused editable without walking stale application proxies.

        libatspi's generic desktop traversal waits several seconds on a dead
        Flatpak accessibility proxy before reaching the live application.
        Query the shared bus directly, restrict traversal to the active window,
        and retain only child indexes. The indexes are resolved back to a real
        pyatspi object before they can authorize recording or paste.
        """

        deadline = time.monotonic() + A11Y_FOCUS_RECOVERY_TIMEOUT_S
        try:
            registry = self.a11y_accessible_interface(
                "org.a11y.atspi.Registry",
                "/org/a11y/atspi/accessible/root",
            )
            applications = registry.GetChildren(
                timeout=self.a11y_call_timeout(deadline)
            )
        except TimeoutError:
            return None
        except (dbus.DBusException, OSError):
            self.a11y_bus = None
            return None

        active_windows = deque()
        for app_bus, app_path in applications:
            if time.monotonic() >= deadline:
                return None
            try:
                app = self.a11y_accessible_interface(app_bus, app_path)
                windows = app.GetChildren(
                    timeout=self.a11y_call_timeout(deadline)
                )
            except TimeoutError:
                return None
            except (dbus.DBusException, OSError):
                continue
            for window_index, (node_bus, node_path) in enumerate(windows):
                try:
                    node = self.a11y_accessible_interface(node_bus, node_path)
                    state_bits = self.a11y_state_bits(
                        node.GetState(timeout=self.a11y_call_timeout(deadline))
                    )
                except TimeoutError:
                    return None
                except (dbus.DBusException, OSError):
                    continue
                if state_bits & (A11Y_ACTIVE_MASK | A11Y_FOCUSED_MASK):
                    active_windows.append(
                        (
                            str(app_bus),
                            str(node_bus),
                            str(node_path),
                            (window_index,),
                        )
                    )

        visited = 0
        while active_windows and visited < A11Y_FOCUS_RECOVERY_MAX_NODES:
            if time.monotonic() >= deadline:
                return None
            app_bus, node_bus, node_path, chain = active_windows.popleft()
            visited += 1
            try:
                node = self.a11y_accessible_interface(node_bus, node_path)
                state_bits = self.a11y_state_bits(
                    node.GetState(timeout=self.a11y_call_timeout(deadline))
                )
                if state_bits & A11Y_FOCUSED_MASK:
                    role = int(
                        node.GetRole(timeout=self.a11y_call_timeout(deadline))
                    )
                    if (
                        state_bits & A11Y_EDITABLE_MASK
                        or role in A11Y_EDITABLE_ROLES
                    ):
                        return app_bus, chain
                children = node.GetChildren(
                    timeout=self.a11y_call_timeout(deadline)
                )
            except TimeoutError:
                return None
            except (dbus.DBusException, OSError):
                continue
            for child_index, (child_bus, child_path) in enumerate(children):
                active_windows.append(
                    (
                        app_bus,
                        str(child_bus),
                        str(child_path),
                        chain + (child_index,),
                    )
                )
        return None

    def recover_current_accessible_focus(self):
        result = self.find_focused_accessible_chain()
        if result is None:
            return False
        app_bus, chain = result
        try:
            desktop = pyatspi.Registry.getDesktop(0)
            source = next(
                app
                for app in desktop
                if str(getattr(getattr(app, "app", None), "bus_name", ""))
                == app_bus
            )
            for child_index in chain:
                source = source.getChildAtIndex(child_index)
        except (StopIteration, AttributeError, IndexError, RuntimeError):
            return False
        if not self.accessible_source_is_valid(source):
            return False
        self.visibility.editable_focus_gained(source)
        return True

    def find_active_accessible_window_target(self):
        """Bind voice to one active window when no editable proxy exists.

        Some Electron/Chromium editors accept ordinary keyboard input but do
        not expose their contenteditable node as the focused AT-SPI object.
        This fallback is deliberately application-agnostic: it records the
        one active top-level accessibility window without reading its title or
        text. The exact window must remain active through record, recognition,
        and paste; standard editable targets continue to use the stronger
        object-level path.
        """

        deadline = time.monotonic() + A11Y_FOCUS_RECOVERY_TIMEOUT_S
        try:
            registry = self.a11y_accessible_interface(
                "org.a11y.atspi.Registry",
                "/org/a11y/atspi/accessible/root",
            )
            applications = registry.GetChildren(
                timeout=self.a11y_call_timeout(deadline)
            )
        except TimeoutError:
            return None
        except (dbus.DBusException, OSError):
            self.a11y_bus = None
            return None

        candidates = []
        for app_bus, app_path in applications:
            if time.monotonic() >= deadline:
                break
            try:
                app = self.a11y_accessible_interface(app_bus, app_path)
                windows = app.GetChildren(
                    timeout=self.a11y_call_timeout(deadline)
                )
            except (TimeoutError, dbus.DBusException, OSError):
                continue
            for node_bus, node_path in windows:
                if time.monotonic() >= deadline:
                    break
                try:
                    node = self.a11y_accessible_interface(node_bus, node_path)
                    state_bits = self.a11y_state_bits(
                        node.GetState(timeout=self.a11y_call_timeout(deadline))
                    )
                except (TimeoutError, dbus.DBusException, OSError):
                    continue
                if not state_bits & (A11Y_ACTIVE_MASK | A11Y_FOCUSED_MASK):
                    continue
                rank = 2 if state_bits & A11Y_FOCUSED_MASK else 1
                candidates.append(
                    (
                        rank,
                        (
                            "atspi-window",
                            str(app_bus),
                            str(node_bus),
                            str(node_path),
                        ),
                    )
                )

        if not candidates:
            return None
        highest_rank = max(rank for rank, _target in candidates)
        best = [target for rank, target in candidates if rank == highest_rank]
        return best[0] if len(best) == 1 else None

    def active_accessible_window_target_is_valid(self, target):
        if (
            not isinstance(target, tuple)
            or len(target) != 4
            or target[0] != "atspi-window"
        ):
            return False
        _kind, _app_bus, node_bus, node_path = target
        deadline = time.monotonic() + A11Y_FOCUS_RECOVERY_TIMEOUT_S
        try:
            node = self.a11y_accessible_interface(node_bus, node_path)
            state_bits = self.a11y_state_bits(
                node.GetState(timeout=self.a11y_call_timeout(deadline))
            )
        except (TimeoutError, dbus.DBusException, OSError):
            return False
        return bool(state_bits & (A11Y_ACTIVE_MASK | A11Y_FOCUSED_MASK))

    def enable_accessibility(self):
        try:
            obj = self.session_bus.get_object("org.a11y.Bus", "/org/a11y/bus")
            props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
            props.Set("org.a11y.Status", "IsEnabled", dbus.Boolean(True))
            props.Set("org.a11y.Status", "ScreenReaderEnabled", dbus.Boolean(True))
        except dbus.DBusException as exc:
            print(f"accessibility status: {exc}", flush=True)

    def install_focus_listener(self):
        pyatspi.Registry.registerEventListener(
            self.on_accessible_focus, "object:state-changed:focused"
        )

    @staticmethod
    def accessible_source_is_editable(source):
        try:
            state = source.getState()
            role = source.getRole()
            return state.contains(pyatspi.STATE_EDITABLE) or role in {
                pyatspi.ROLE_ENTRY,
                pyatspi.ROLE_PASSWORD_TEXT,
                pyatspi.ROLE_TEXT,
                pyatspi.ROLE_TERMINAL,
            }
        except Exception:
            return False

    def accessible_source_is_valid(self, source):
        try:
            state = source.getState()
            return (
                state.contains(pyatspi.STATE_FOCUSED)
                and state.contains(pyatspi.STATE_SHOWING)
                and state.contains(pyatspi.STATE_VISIBLE)
                and self.accessible_source_is_editable(source)
            )
        except Exception:
            return False

    def on_accessible_focus(self, event):
        focused = bool(event.detail1)
        if focused and accessible_process_id(event.source) == os.getpid():
            # Pressing a key or the mic button focuses a GTK accessible in this
            # process without taking desktop focus.  It must not revoke the
            # browser/editor target captured before voice recording began.
            return
        if focused and self.touchpad_overlay_visible():
            # Touchpad mode owns the lower screen until the user explicitly
            # switches away. Window dragging can churn desktop focus, but must
            # never replace the touchpad with the keyboard.
            if self.visibility.visible:
                self.manual_hide()
            return
        if focused and self.accessible_source_is_editable(event.source):
            self.visibility.editable_focus_gained(event.source)
        elif focused:
            self.visibility.noneditable_focus_gained(event.source)
        else:
            self.visibility.editable_focus_lost(event.source)
        self.cancel_voice_if_target_changed("accessible target changed")

    def revalidate_accessible_focus(self):
        if self.touchpad_overlay_visible():
            if self.visibility.visible:
                self.manual_hide()
            return True
        self.cancel_voice_if_target_changed("accessible target became invalid")
        self.visibility.revalidate_focus()
        return True

    def cancel_voice_if_target_changed(self, reason):
        snapshot = self.voice.snapshot()
        if not snapshot.busy:
            return False
        target_id, target_is_valid = self.current_voice_target()
        if target_is_valid and target_id == snapshot.target_id:
            return False
        self.cancel_voice(reason)
        return True

    def install_hardware_toggle(self):
        self.last_hardware_toggle = 0.0
        self.system_bus.add_signal_receiver(
            self.on_hardware_event,
            signal_name="InputEvent",
            dbus_interface="org.shadowblip.Input.DBusDevice",
            bus_name="org.shadowblip.InputPlumber",
        )

    def on_hardware_event(self, action, value):
        if hardware_actions_blocked():
            return
        if value < 0.5:
            return
        now = time.monotonic()
        if action == "ui_osk":
            if now - self.last_hardware_toggle < 0.25:
                return
            self.last_hardware_toggle = now
            GLib.idle_add(self.apply_hardware_toggle)
            return

        shortcut = WINDOW_SHORTCUTS.get(str(action))
        if shortcut is None:
            return
        previous = self.last_window_action.get(str(action), 0.0)
        if now - previous < WINDOW_ACTION_DEBOUNCE_S:
            return
        self.last_window_action[str(action)] = now
        GLib.idle_add(self.invoke_hardware_shortcut, shortcut)

    def apply_hardware_toggle(self):
        if not hardware_actions_blocked():
            self.manual_toggle()
        return False

    def invoke_hardware_shortcut(self, shortcut):
        if not hardware_actions_blocked():
            self.invoke_kwin_shortcut(shortcut)
        return False

    def invoke_kwin_shortcut(self, shortcut):
        try:
            component = self.session_bus.get_object(
                "org.kde.kglobalaccel", "/component/kwin"
            )
            interface = dbus.Interface(
                component, "org.kde.kglobalaccel.Component"
            )
            interface.invokeShortcut(shortcut)
        except dbus.DBusException as exc:
            print(f"KWin shortcut {shortcut!r} failed: {exc}", flush=True)
        return False

    def queue_manual_toggle(self):
        GLib.idle_add(self.manual_toggle)

    def queue_manual_hide(self):
        GLib.idle_add(self.manual_hide)

    def queue_manual_show(self):
        GLib.idle_add(self.manual_show)

    def manual_toggle(self):
        self.visibility.manual_toggle()
        return False

    def manual_show(self):
        self.visibility.manual_show()
        return False

    @staticmethod
    def touchpad_overlay_visible():
        try:
            metadata = os.stat(TOUCHPAD_VISIBLE_STATE, follow_symlinks=False)
        except (FileNotFoundError, OSError):
            return False
        return (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == os.getuid()
            and metadata.st_nlink == 1
            and (metadata.st_mode & 0o022) == 0
            and 0 < metadata.st_size <= 16
        )

    def apply_visibility_snapshot(self, snapshot):
        if not snapshot.visible:
            if getattr(self, "settings_visible", False):
                self.build_layout()
            self.reset_shift_state()
            self.set_raw_touch_enabled(False)
            self.cancel_backspace_interaction()
            self.cancel_space_hold()
            self.cancel_voice("keyboard hidden")
            self.window.hide()
            return

        self.window.show_all()
        self.set_raw_touch_enabled(
            not getattr(self, "settings_visible", False)
        )

    def manual_hide(self, _button=None):
        self.reset_shift_state()
        self.set_raw_touch_enabled(False)
        self.cancel_backspace_interaction()
        self.cancel_space_hold()
        self.visibility.manual_hide()
        return False

    def on_delete(self, *_args):
        self.cancel_backspace_interaction()
        self.manual_hide()
        return True

    def set_mic(self, state):
        if not self.mic_button:
            return False
        context = self.mic_button.get_style_context()
        for name in (
            "mic-live",
            "mic-busy",
            "mic-error",
            "mic-success",
            "mic-disabled",
        ):
            context.remove_class(name)
        if not self.custom_voice_enabled:
            context.add_class("mic-disabled")
            if self.mic_status_stack:
                self.mic_status_stack.set_visible_child_name("disabled")
            return False
        if self.mic_status_stack:
            self.mic_status_stack.set_visible_child_name(state.value)
        if state is VoiceState.RECORDING:
            context.add_class("mic-live")
        elif state in {
            VoiceState.CONNECTING,
            VoiceState.STOPPING,
            VoiceState.RECOGNIZING,
            VoiceState.PENDING_PASTE,
        }:
            context.add_class("mic-busy")
        elif state is VoiceState.ERROR:
            context.add_class("mic-error")
        elif state is VoiceState.SUCCESS:
            context.add_class("mic-success")
        return False

    def render_voice_snapshot(self, snapshot):
        # Gtk.Stack reserves the largest status label up front, so feedback can
        # be explicit without publishing changing mixed-scale window hints.
        feedback = getattr(self, "voice_error_feedback", None)
        if snapshot.state is not VoiceState.ERROR or not feedback or feedback[0] != snapshot.generation:
            self.voice_error_feedback = None
            label = VOICE_STATE_LABELS[VoiceState.ERROR]
        else:
            label = feedback[1]
        error_label = getattr(self, "mic_error_label", None)
        if error_label is not None:
            error_label.set_text(label)
        self.set_mic(snapshot.state)

    def on_voice_state_changed(self, snapshot):
        if self.voice_feedback_source:
            GLib.source_remove(self.voice_feedback_source)
            self.voice_feedback_source = 0
        if not snapshot.busy:
            self.voice_fallback_window = None
            self.stop_microphone_session()
        self.visibility.set_voice_busy(snapshot.busy)
        self.render_voice_snapshot(snapshot)
        if snapshot.state in {
            VoiceState.SUCCESS,
            VoiceState.ERROR,
            VoiceState.DISCARDED,
        }:
            self.voice_feedback_source = GLib.timeout_add(
                1_800,
                self.reset_voice_state,
                snapshot.generation,
            )

    def reset_voice_state(self, generation):
        self.voice_feedback_source = 0
        self.voice.reset(generation)
        return False

    def microphone_source_status(self, source=None, cancelled=lambda: False):
        # Request UTF-8 only for these children. Some pactl versions still emit
        # null descriptions; routing uses the stable name/monitor fields only.
        def query(arguments):
            return_code, output = run_bounded_command(
                ["/usr/bin/env", "LC_ALL=C.UTF-8", "pactl", *arguments],
                timeout=2, maximum=262_144, cancelled=cancelled,
            )
            if return_code != 0:
                raise VoiceArtifactError("microphone query failed")
            return output.decode("utf-8")

        if source is None:
            try:
                source = query(["get-default-source"]).strip()
            except (OSError, UnicodeError, VoiceArtifactError):
                return None, "麦克风状态检查失败"
        if (
            not source
            or len(source) > 1024
            or "\x00" in source
            or source.startswith("@")
            or source.endswith(".monitor")
            or "auto_null" in source
        ):
            return None, "麦克风不可用"
        try:
            sources = json.loads(query(["--format=json", "list", "sources"]))
        except (OSError, UnicodeError, ValueError, VoiceArtifactError):
            return None, "麦克风状态检查失败"
        if not isinstance(sources, list):
            return None, "麦克风不可用"
        matches = [entry for entry in sources
                   if isinstance(entry, dict) and entry.get("name") == source]
        if len(matches) != 1:
            return None, "麦克风不可用"
        # A monitor may have a custom name: reject its actual source metadata,
        # not only the conventional .monitor suffix. SUSPENDED is a valid mic.
        entry = matches[0]
        non_monitor = (
            ("monitor_source" in entry and entry["monitor_source"] == "")
            or ("monitor_of_sink" in entry and entry["monitor_of_sink"] is None)
        )
        if (not non_monitor or entry.get("monitor_source") not in (None, "")
                or entry.get("monitor_of_sink") is not None
                or entry.get("monitor_of_sink_name")):
            return None, "麦克风不可用"
        # Direct ALSA does not inherit PipeWire's software mute/volume. Fail
        # closed on absent permission metadata instead of assuming unmuted.
        if type(entry.get("mute")) is not bool:
            return None, "麦克风状态检查失败"
        if entry["mute"]:
            return None, "麦克风已静音"
        volume = entry.get("volume")
        if not isinstance(volume, dict) or not volume:
            return None, "麦克风状态检查失败"
        values = [channel.get("value") if isinstance(channel, dict) else None
                  for channel in volume.values()]
        if any(type(value) is not int or value < 0 for value in values):
            return None, "麦克风状态检查失败"
        if not any(values):
            return None, "麦克风音量为零"
        return source, None

    def start_microphone_session(self, generation):
        self.stop_microphone_session()
        session = MicrophoneSessionMonitor(
            generation, self.microphone_source_status, GLib.idle_add,
            self.on_microphone_selected, self.on_microphone_denied,
            self.on_microphone_finalized, guarded_source=INTERNAL_MICROPHONE_SOURCE,
        )
        self.microphone_session = session
        session.start()

    def stop_microphone_session(self):
        session = getattr(self, "microphone_session", None)
        self.microphone_session = None
        self.microphone_pending_result = None
        if session is not None:
            session.cancel()

    def on_microphone_selected(self, generation, source):
        snapshot = self.voice.snapshot()
        if snapshot.generation != generation or snapshot.state is not VoiceState.CONNECTING:
            return False
        target_id, valid = self.current_voice_target()
        if not valid or target_id != snapshot.target_id:
            self.cancel_voice("target changed during microphone lookup")
            return False
        recorder_kind = "alsa" if source == INTERNAL_MICROPHONE_SOURCE else "pulse"
        self.voice_recorder_route = (generation, recorder_kind, source)
        try:
            self.capture_startup.start(lambda: self.spawn_voice_recorder(recorder_kind, source))
        except (RuntimeError, ValueError):
            self.on_microphone_denied(generation, "麦克风不可用")
        return False

    def on_microphone_denied(self, generation, message):
        snapshot = self.voice.snapshot()
        if snapshot.generation != generation or not snapshot.busy:
            return False
        # Cancel the sole recorder owner and remove the WAV before showing the
        # reason. A muted/unknown source never reaches the network-capable path.
        self.cancel_voice(message)
        self.voice.flash_error()
        current = self.voice.snapshot()
        self.voice_error_feedback = (current.generation, VOICE_FAILURE_LABELS.get(message, "识别失败"))
        self.render_voice_snapshot(current)
        return False

    def on_microphone_finalized(self, generation):
        pending = getattr(self, "microphone_pending_result", None)
        snapshot = self.voice.snapshot()
        if (pending is None or pending[0] != generation
                or snapshot.generation != generation or snapshot.state is not VoiceState.STOPPING):
            return False
        self.stop_microphone_session()
        return self.begin_voice_recognition(generation)

    @property
    def voice_busy(self):
        return self.voice.snapshot().busy

    def note_ignored_voice_activation(self, snapshot):
        key = (snapshot.generation, snapshot.state)
        if key == self.last_ignored_voice_activation:
            return False
        self.last_ignored_voice_activation = key
        print(
            f"voice: activation ignored while {snapshot.state.value}",
            flush=True,
        )
        return False

    def toggle_voice(self, _button):
        snapshot = self.voice.snapshot()
        if snapshot.state is VoiceState.RECORDING:
            self.stop_recording()
        elif snapshot.state in {
            VoiceState.CONNECTING,
            VoiceState.STOPPING,
            VoiceState.RECOGNIZING,
            VoiceState.PENDING_PASTE,
        }:
            self.note_ignored_voice_activation(snapshot)
        elif not snapshot.busy:
            return self.start_recording()

    def current_voice_target(self):
        voice_snapshot = self.voice.snapshot()
        if voice_snapshot.busy and self.voice_fallback_window is not None:
            target = self.voice_fallback_window
            valid = (
                self.visibility.snapshot().visible
                and self.active_accessible_window_target_is_valid(target)
            )
            return target, valid

        snapshot = self.visibility.snapshot()
        source = self.visibility.focused_editable
        source_id = trusted_accessible_id(source) if source is not None else None
        valid = (
            snapshot.visible
            and source is not None
            and source_id is not None
            and snapshot.editable_focus_id == source_id
            and self.accessible_source_is_valid(source)
        )
        target = (
            (snapshot.generation, source_id)
            if source_id is not None
            else None
        )
        if valid:
            return target, True
        if not snapshot.visible:
            return None, False
        target = self.find_active_accessible_window_target()
        self.voice_fallback_window = target
        return target, target is not None

    def start_recording(self):
        # UCM/WirePlumber owns the mixer route. Never mutate unstable ALSA
        # numids from an unprivileged UI and never record the speaker monitor.
        # The Pulse/PipeWire recorder can leave this Qualcomm capture node in
        # ERROR after APM_CMD_GRAPH_START times out. Keep direct ALSA for that
        # internal source; external microphones must use their actual source.
        if not self.custom_voice_enabled:
            self.flash_mic_error("自制语音暂时停用；ChatGPT 语音不受影响")
            return
        if self.voice_thread is not None and self.voice_thread.is_alive():
            self.flash_mic_error("上一段语音仍在安全清理；请稍后重试")
            return
        retry_remaining = self.voice_retry_not_before - time.monotonic()
        if retry_remaining > 0:
            seconds = max(1, int(retry_remaining) + 1)
            self.flash_mic_error(f"麦克风恢复中；请在 {seconds} 秒后重试")
            return
        if (
            self.capture_startup.active
            or self.recorder_stop.active
            or self.voice_process is not None
        ):
            self.flash_mic_error("上一段录音仍在安全回收；请稍后重试")
            return
        target_id, target_is_valid = self.current_voice_target()
        if target_id is None or not target_is_valid:
            self.recover_current_accessible_focus()
            target_id, target_is_valid = self.current_voice_target()
        if target_id is None or not target_is_valid:
            self.flash_mic_error("未找到当前输入框；语音不会跨应用粘贴")
            return
        try:
            generation = self.voice.begin_connecting(target_id)
            self.voice_recorder_route = None
            self.start_microphone_session(generation)
            return generation
        except (RuntimeError, ValueError) as exc:
            self.cancel_voice("capture startup rejected")
            self.flash_mic_error(f"录音失败: {exc}")
        return None

    @staticmethod
    def spawn_voice_recorder(recorder_kind, microphone_source):
        if recorder_kind == "alsa" and microphone_source == INTERNAL_MICROPHONE_SOURCE:
            command = [
                "arecord",
                "-q",
                "-D",
                "plughw:0,2",
                "-f",
                "S16_LE",
                "-r",
                "16000",
                "-c",
                "1",
                str(VOICE_WAV),
            ]
        elif recorder_kind == "pulse" and microphone_source != INTERNAL_MICROPHONE_SOURCE:
            command = [
                "parecord",
                f"--device={microphone_source}",
                "--client-name=PocketDS Keyboard",
                "--stream-name=Voice input",
                "--file-format=wav",
                "--format=s16le",
                "--rate=16000",
                "--channels=1",
                # The server's default 2-second capture fragment exceeds our
                # unchanged 1.2-second first-PCM readiness window.
                "--latency-msec=50",
                # PipeWire preserves Pulse stream properties. Pin the target
                # through default changes and fail if it vanishes; never let
                # WirePlumber reconnect this recording to the internal mic.
                "--property=node.dont-fallback=true",
                "--property=node.dont-reconnect=true",
                "--property=node.dont-move=true",
                str(VOICE_WAV),
            ]
        else:
            raise ValueError("invalid microphone route")
        return subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def cleanup_voice_artifact():
        try:
            remove_owned_artifact(VOICE_WAV)
        except (OSError, VoiceArtifactError):
            print("voice: private artifact cleanup refused", flush=True)

    def stop_startup_recorder(self, process, cancelled):
        """Hand a recorder to the sole non-blocking process owner."""

        self.recorder_stop.request(
            process,
            cancelled=bool(cancelled),
            on_reaped=lambda _result: None,
            on_failed=self.on_discarded_recorder_stop_failed,
        )

    def on_discarded_recorder_stop_failed(self, reason):
        print(f"voice: discarded recorder quarantine ({reason.value})", flush=True)
        self.cleanup_voice_artifact()

    def on_capture_ready(self, process):
        snapshot = self.voice.snapshot()
        if snapshot.state is not VoiceState.CONNECTING:
            # Returning normally would make CaptureStartupController relinquish
            # this still-live child.  Raise while it remains the sole owner so
            # its one failure path performs the stop handoff and cleanup.
            raise RuntimeError("capture became stale before runtime handoff")
        target_id, target_is_valid = self.current_voice_target()
        if not target_is_valid or target_id != snapshot.target_id:
            self.voice_failed(
                "输入框已变化；语音已取消",
                snapshot.generation,
            )
            raise RuntimeError("capture target changed before runtime handoff")
        if not self.voice.capture_ready(snapshot.generation):
            raise RuntimeError("capture state changed before runtime handoff")
        self.voice_retry_not_before = 0.0

        # The speaking window starts only after the startup controller observes
        # real PCM. This is normally near-immediate, but a wedged Qualcomm graph
        # now fails before the UI can claim that it is recording.
        timeout_source = GLib.timeout_add_seconds(15, self.stop_recording)
        if type(timeout_source) is not int or timeout_source <= 0:
            raise RuntimeError("capture timeout source was not created")

        if (
            getattr(self, "space_voice_generation", 0) == snapshot.generation
            and getattr(self, "space_voice_release_generation", 0)
            == snapshot.generation
        ):
            GLib.idle_add(
                self.finish_deferred_space_voice,
                snapshot.generation,
            )

        # These plain attribute stores are the final, non-callback portion of
        # the handoff.  Nothing that can invoke user code follows publication;
        # when this method returns, CaptureStartupController releases its
        # matching reference.
        self.voice_timeout = timeout_source
        self.voice_process = process

    def on_capture_failed(self, reason):
        messages = {
            CaptureStartupFailure.CLEANUP_FAILED: "录音文件安全清理失败",
            CaptureStartupFailure.SPAWN_FAILED: "录音进程无法启动",
            CaptureStartupFailure.RECORDER_EXITED: "录音未成功写入",
            CaptureStartupFailure.INVALID_ARTIFACT: "录音文件安全检查失败",
            CaptureStartupFailure.CONNECT_TIMEOUT: "麦克风连接超时",
            CaptureStartupFailure.SCHEDULER_FAILED: "录音调度器不可用",
            CaptureStartupFailure.CALLBACK_FAILED: "录音状态切换失败",
        }
        self.voice_retry_not_before = max(
            self.voice_retry_not_before,
            time.monotonic() + CAPTURE_RETRY_COOLDOWN_S,
        )
        snapshot = self.voice.snapshot()
        if snapshot.state in {VoiceState.CONNECTING, VoiceState.RECORDING}:
            self.voice_failed(messages[reason], snapshot.generation)

    def stop_recording(self):
        self.cancel_voice_timeout()
        snapshot = self.voice.snapshot()
        if snapshot.state is not VoiceState.RECORDING:
            return False
        self.clear_space_voice_session(snapshot.generation)
        process = self.voice_process
        if process is None:
            self.voice_failed("录音进程已退出", snapshot.generation)
            return False
        # This is the first hard target gate. It applies identically to a
        # button stop and the 15-second auto-stop and runs before STOPPING,
        # process handoff, ASR thread creation, or any network-capable code.
        target_id, target_is_valid = self.current_voice_target()
        if not self.voice.begin_stopping(
            snapshot.generation,
            target_id,
            target_is_valid=target_is_valid,
        ):
            try:
                self.stop_startup_recorder(process, True)
            except (RuntimeError, ValueError) as exc:
                # Keep voice_process as an explicit owner so a new capture
                # cannot reuse the global WAV while this child is still live.
                print(f"voice: recorder handoff refused ({exc})", flush=True)
            else:
                self.voice_process = None
            self.cleanup_voice_artifact()
            print("voice: target changed before recorder stop; discarded", flush=True)
            return False
        try:
            self.recorder_stop.request(
                process,
                cancelled=False,
                on_reaped=lambda result: self.on_recording_reaped(
                    result,
                    snapshot.generation,
                ),
                on_failed=lambda reason: self.on_recording_stop_failed(
                    reason,
                    snapshot.generation,
                ),
            )
        except (RuntimeError, ValueError) as exc:
            # request() raises only before accepting ownership. Preserve the
            # process reference so cancel/shutdown can retry safely.
            self.voice_failed(f"录音回收无法启动: {exc}", snapshot.generation)
            return False
        self.voice_process = None
        return False

    def on_recording_stop_failed(self, reason, generation):
        messages = {
            RecorderStopFailure.POLL_FAILED: "录音进程状态检查失败",
            RecorderStopFailure.SIGNAL_FAILED: "录音进程无法安全停止",
            RecorderStopFailure.REAP_TIMEOUT: "录音进程回收超时",
            RecorderStopFailure.SCHEDULER_FAILED: "录音回收调度器不可用",
        }
        self.cleanup_voice_artifact()
        snapshot = self.voice.snapshot()
        if snapshot.generation == generation and snapshot.state is VoiceState.STOPPING:
            self.voice_failed(messages[reason], generation)

    def on_recording_reaped(self, result, generation):
        snapshot = self.voice.snapshot()
        if snapshot.generation != generation or snapshot.state is not VoiceState.STOPPING:
            self.cleanup_voice_artifact()
            return False
        recording_is_eligible = result.eligible_for_recognition
        route = getattr(self, "voice_recorder_route", None)
        if (route == (generation, "alsa", INTERNAL_MICROPHONE_SOURCE)
                and result.may_finalize_interrupted_arecord):
            try:
                finalize_private_arecord_wav(VOICE_WAV)
            except VoiceArtifactError:
                recording_is_eligible = False
            else:
                recording_is_eligible = True
        if not recording_is_eligible:
            self.cleanup_voice_artifact()
            self.voice_failed("录音未成功写入", generation)
            return False

        if route == (generation, "alsa", INTERNAL_MICROPHONE_SOURCE):
            session = getattr(self, "microphone_session", None)
            if session is None or session.generation != generation:
                self.on_microphone_denied(generation, "麦克风状态检查失败")
                return False
            self.microphone_pending_result = (generation, result)
            session.finish()
            return False
        return self.begin_voice_recognition(generation)

    def begin_voice_recognition(self, generation):
        self.stop_microphone_session()
        # Revalidate again after the asynchronous SIGINT/finalization interval.
        # No worker thread or cloud client exists until this exact-target gate
        # succeeds on the GTK main thread.
        target_id, target_is_valid = self.current_voice_target()
        if not self.voice.begin_recognition(
            generation,
            target_id,
            target_is_valid=target_is_valid,
        ):
            self.cleanup_voice_artifact()
            print("voice: target changed before ASR; discarded", flush=True)
            return False
        try:
            thread = threading.Thread(
                target=self.recognize_voice,
                args=(generation,),
                daemon=True,
            )
            self.voice_thread = thread
            thread.start()
        except Exception as exc:
            self.voice_thread = None
            self.cleanup_voice_artifact()
            self.voice_failed(
                f"识别线程无法启动: {type(exc).__name__}",
                generation,
            )
        return False

    def cancel_voice_timeout(self):
        if self.voice_timeout:
            GLib.source_remove(self.voice_timeout)
            self.voice_timeout = 0

    def cancel_voice(self, reason):
        self.cancel_voice_timeout()
        self.clear_space_voice_session()
        self.stop_microphone_session()
        snapshot = self.voice.snapshot()
        self.voice.cancel()
        self.capture_startup.cancel()
        process = self.voice_process
        if process is not None:
            try:
                self.stop_startup_recorder(process, True)
            except (RuntimeError, ValueError) as exc:
                # Keep ownership reachable and keep subsequent capture blocked.
                print(f"voice: recorder handoff refused ({exc})", flush=True)
            else:
                self.voice_process = None
        if snapshot.busy:
            print(f"voice: cancelled ({reason})", flush=True)
        self.cleanup_voice_artifact()

    def voice_recognition_cancelled(self, generation):
        return not self.voice.recognition_is_current(generation)

    def recognize_voice(self, generation):
        try:
            self._recognize_voice(generation)
        except Exception as exc:
            print(f"voice: unexpected {type(exc).__name__}", flush=True)
            if not self.voice_recognition_cancelled(generation):
                GLib.idle_add(self.voice_failed, "识别后端异常", generation)
        finally:
            try:
                remove_owned_artifact(VOICE_WAV)
            except (OSError, VoiceArtifactError):
                print("voice: private artifact cleanup refused", flush=True)
            if self.voice_thread is threading.current_thread():
                self.voice_thread = None

    def _recognize_voice(self, generation):
        cancelled = lambda: self.voice_recognition_cancelled(generation)
        if cancelled():
            return
        try:
            read_private_wav(VOICE_WAV)
        except VoiceArtifactError:
            GLib.idle_add(self.voice_failed, "录音未成功写入", generation)
            return
        cloud_error = None
        try:
            text = self.asr_api.transcribe(VOICE_WAV, cancelled=cancelled)
            if cancelled():
                return
            if text is None:
                GLib.idle_add(
                    self.voice_failed,
                    "未检测到可识别语音",
                    generation,
                )
                return
            GLib.idle_add(self.commit_voice_text, text, generation)
            return
        except AsrApiCancelled:
            return
        except AsrApiError as exc:
            if cancelled():
                return
            cloud_error = exc.public_message

        # Local engines are bounded offline fallbacks only. They never invoke
        # a cloud directory, shell, environment credential, or detached child.
        sensevoice_error = None
        if sensevoice_backend_available(
            SENSEVOICE_BIN,
            SENSEVOICE_LIBRARY,
            SENSEVOICE_MODEL,
            SENSEVOICE_TOKENS,
        ):
            try:
                return_code, output = run_bounded_command(
                    sensevoice_command(
                        SENSEVOICE_BIN,
                        SENSEVOICE_MODEL,
                        SENSEVOICE_TOKENS,
                        VOICE_WAV,
                    ),
                    timeout=45,
                    maximum=262_144,
                    cancelled=cancelled,
                )
                if cancelled():
                    return
                if return_code == 0:
                    text = parse_sensevoice_transcript(output)
                    GLib.idle_add(self.commit_voice_text, text, generation)
                    return
                sensevoice_error = "SenseVoice 未返回有效文本"
            except (OSError, VoiceArtifactError, subprocess.TimeoutExpired) as exc:
                if cancelled():
                    return
                sensevoice_error = f"SenseVoice {type(exc).__name__}"

        if cancelled():
            return
        if not WHISPER_BIN.is_file() or not WHISPER_MODEL.is_file():
            message = sensevoice_error or cloud_error or "语音识别后端未安装"
            GLib.idle_add(self.voice_failed, message, generation)
            return
        try:
            return_code, output = run_bounded_command(
                [
                    str(WHISPER_BIN),
                    "-m",
                    str(WHISPER_MODEL),
                    "-f",
                    str(VOICE_WAV),
                    "-l",
                    "zh",
                    "-t",
                    "6",
                    "-nt",
                    "-np",
                    "-sns",
                ],
                timeout=90,
                maximum=65_536,
                cancelled=cancelled,
            )
            if cancelled():
                return
            if return_code != 0:
                GLib.idle_add(
                    self.voice_failed,
                    "Whisper 未返回有效文本",
                    generation,
                )
                return
            text = output.decode("utf-8", errors="strict").strip()
            text = re.sub(r"\[[^\]]+\]", "", text)
            text = normalize_transcript(text)
            GLib.idle_add(self.commit_voice_text, text, generation)
        except (OSError, UnicodeError, VoiceArtifactError) as exc:
            if not cancelled():
                GLib.idle_add(
                    self.voice_failed,
                    f"Whisper {type(exc).__name__}",
                    generation,
                )

    def commit_voice_text(self, text, generation):
        target_id, target_is_valid = self.current_voice_target()
        if not self.voice.prepare_paste(
            generation,
            target_id,
            target_is_valid=target_is_valid,
        ):
            print("voice: target changed; transcript discarded", flush=True)
            return False
        try:
            obj = self.session_bus.get_object("org.kde.klipper", "/klipper")
            klipper = dbus.Interface(obj, "org.kde.klipper.klipper")
            klipper.setClipboardContents(text)
            GLib.timeout_add(120, self.paste_voice_text, generation)
        except dbus.DBusException as exc:
            self.voice_failed(str(exc), generation)
        return False

    def paste_voice_text(self, generation):
        target_id, target_is_valid = self.current_voice_target()
        modifiers = (ecodes.KEY_LEFTCTRL,)
        try:
            if (
                self.visibility.focused_editable is not None
                and self.visibility.focused_editable.getRole()
                == pyatspi.ROLE_TERMINAL
            ):
                modifiers = (ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTSHIFT)
        except Exception:
            pass
        if not self.voice.consume_paste(
            generation,
            target_id,
            target_is_valid=target_is_valid,
        ):
            print("voice: paste target changed; transcript discarded", flush=True)
            return False
        self.emit_hotkey(modifiers, ecodes.KEY_V)
        return False

    def voice_failed(self, message, generation):
        print(f"voice: {message}", flush=True)
        self.clear_space_voice_session(generation)
        if self.voice.fail(generation):
            snapshot = self.voice.snapshot()
            if snapshot.generation == generation and snapshot.state is VoiceState.ERROR:
                label = VOICE_FAILURE_LABELS.get(message, VOICE_STATE_LABELS[VoiceState.ERROR])
                self.voice_error_feedback = (generation, label)
                error_label = getattr(self, "mic_error_label", None)
                if error_label is not None:
                    error_label.set_text(label)
        return False

    def flash_mic_error(self, message):
        print(message, flush=True)
        self.voice.flash_error()

    def run(self):
        try:
            Gtk.main()
        finally:
            self.window_transfer_closing.set()
            if self.window_transfer_feedback_source:
                GLib.source_remove(self.window_transfer_feedback_source)
                self.window_transfer_feedback_source = 0
            self.set_raw_touch_enabled(False)
            self.reset_shift_state()
            self.cancel_backspace_interaction()
            self.cancel_space_hold()
            self.cancel_voice("shutdown")
            feedback_save_source = getattr(self, "feedback_save_source", 0)
            if feedback_save_source:
                try:
                    GLib.source_remove(feedback_save_source)
                except Exception:
                    pass
                self.feedback_save_source = 0
                self.flush_feedback_settings()
            self.cleanup_terminal_placements()
            self.recorder_stop.shutdown()
            self.visibility.shutdown()
            if self.raw_touch is not None:
                self.raw_touch.close()
            key_haptic = getattr(self, "key_haptic", None)
            if key_haptic is not None:
                key_haptic.close()
            key_sound = getattr(self, "key_sound", None)
            if key_sound is not None:
                key_sound.close()
            _SIGUSR1_GATE.clear()
            _SIGUSR2_GATE.clear()
            _SIGHUP_GATE.clear()


if __name__ == "__main__":
    PocketDSKeyboard().run()

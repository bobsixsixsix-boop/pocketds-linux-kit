#!/usr/bin/python3
"""Lower-screen multi-touch trackpad for AYANEO Pocket DS."""

import signal

from keyboard_adapter import SignalActionReadinessGate, SignalToggleReadinessGate


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


import os  # noqa: E402
import select  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

from evdev import InputDevice, UInput, ecodes  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from touchpad_gestures import GestureAction, TouchpadGestureEngine  # noqa: E402
from touchpad_raw import (  # noqa: E402
    ContextMenuTouchGuard,
    ExclusiveTouchGuard,
    PointerButtonLatch,
    RawTouchAction,
    RawTouchRouter,
    TypeBTouchFrame,
    resync_type_b,
)


os.umask(0o077)
TOUCHPAD_RUNTIME = (
    Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"))
    / "pocketds-touchpad"
)
TOUCHPAD_VISIBLE_STATE = TOUCHPAD_RUNTIME / "visible"
FULL_TOUCH_REGION = (0.0, 0.0, 1024.0, 768.0)
RAW_CONTROL_TARGETS = ("keyboard", "panel", "left", "right")


def publish_touchpad_visibility(visible):
    if not visible:
        try:
            TOUCHPAD_VISIBLE_STATE.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(
                f"touchpad: could not clear visibility state: {exc}",
                flush=True,
            )
        return True

    descriptor = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(TOUCHPAD_VISIBLE_STATE, flags, 0o600)
        payload = b"visible\n"
        if os.write(descriptor, payload) != len(payload):
            raise OSError("short visibility state write")
        return True
    except OSError as exc:
        print(f"touchpad: could not publish visibility state: {exc}", flush=True)
        publish_touchpad_visibility(False)
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def notify_systemd_ready():
    notify_socket = os.environ.get("NOTIFY_SOCKET")
    if not notify_socket:
        return False
    address = "\0" + notify_socket[1:] if notify_socket.startswith("@") else notify_socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.connect(address)
        client.sendall(b"READY=1\nSTATUS=Touchpad gesture controller ready")
    return True


class RawTouchscreenReader:
    """Own DSI-2 while touchpad mode is visible and decode its contacts."""

    DEVICE = "/dev/input/by-path/platform-a88000.i2c-event"

    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.tracker = TypeBTouchFrame()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.enabled = False
        self.ready = False
        self.generation = 0
        self.dropping = False
        self.region = (0.0, 0.0, 0.0, 0.0)
        self.device = None
        self.exclusive_guard = ExclusiveTouchGuard()
        self.context_guard = ContextMenuTouchGuard()
        self.thread = threading.Thread(
            target=self.run,
            name="pocketds-raw-touchscreen",
            daemon=True,
        )
        self.thread.start()

    def is_active(self):
        with self.lock:
            return (
                self.enabled
                and self.ready
                and self.exclusive_guard.active
            )

    def arm_context_menu_guard(self):
        with self.lock:
            if not self.enabled or not self.ready:
                return False
            if self.exclusive_guard.active:
                return True
            return self.context_guard.arm(self.device)

    def release_context_menu_guard(self):
        with self.lock:
            self.context_guard.release()

    def _expire_context_menu_guard(self):
        with self.lock:
            self.context_guard.expire(
                active_contacts=bool(self.tracker.committed)
            )

    def is_current(self, generation):
        with self.lock:
            return generation == self.generation

    def _cancel(self):
        self.tracker.reset()
        self.generation += 1
        # Also releases GTK fallback ownership when there were no raw contacts.
        self.dispatch(self.generation, [RawTouchAction("cancel")])

    def set_enabled(self, enabled, region=None):
        with self.lock:
            self._cancel()
            self.enabled = bool(enabled)
            self.dropping = False
            if region is not None:
                self.region = region
            if not enabled:
                context_released = self.context_guard.release()
                exclusive_released = self.exclusive_guard.release()
                if not context_released or not exclusive_released:
                    # Descriptor close drops EVIOCGRAB even if ungrab failed.
                    self._disconnect()
                return True
            if self.device is None:
                self.enabled = False
                return False
            try:
                self.ready = False
                active = self._acquire_neutral(self.device)
                self.ready = active
                self.enabled = active
                return active
            except (OSError, ValueError):
                self._disconnect()
                self.enabled = False
                return False

    def _acquire_neutral(self, device):
        # KWin must receive the end of any contact that began before the grab.
        resync_type_b(device, self.tracker)
        if self.tracker.committed:
            return False
        if not self.exclusive_guard.acquire(device):
            return False
        resync_type_b(device, self.tracker)
        if self.tracker.committed:
            if not self.exclusive_guard.release():
                raise OSError("lower-touchscreen handover ungrab failed")
            return False
        return True

    def _disconnect(self):
        with self.lock:
            self._cancel()
            self.ready = False
            self.dropping = False
            device = self.device
            self.device = None
            if device is not None:
                self.context_guard.release()
                self.exclusive_guard.release()
                try:
                    device.close()
                except OSError:
                    pass
                self.context_guard.device_closed(device)
                self.exclusive_guard.device_closed(device)

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
                if self.enabled:
                    if not self._acquire_neutral(device):
                        raise OSError("lower-touchscreen handover awaits release")
                else:
                    resync_type_b(device, self.tracker)
                self.dropping = False
                self.ready = True
            except (OSError, ValueError):
                self._disconnect()
                raise
            return device

    def _process(self, event):
        with self.lock:
            if not self.enabled or not self.exclusive_guard.active:
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
                    actions = self.tracker.sync(self.region)
            if actions:
                self.dispatch(self.generation, actions)
            return False

    def run(self):
        while not self.stop_event.is_set():
            try:
                self._expire_context_menu_guard()
                with self.lock:
                    device = self.device or self._open()
                readable, _, _ = select.select([device.fd], [], [], 0.4)
                if not readable:
                    continue
                # Serialize the entire batch with enable-time queue resync.
                with self.lock:
                    if device is not self.device:
                        continue
                    try:
                        events = list(device.read())
                    except BlockingIOError:
                        # Enable-time resync may have drained the ready fd.
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


class PocketDSTouchpad:
    def __init__(self):
        capabilities = {
            ecodes.EV_KEY: [
                ecodes.BTN_LEFT,
                ecodes.BTN_RIGHT,
                ecodes.BTN_MIDDLE,
                ecodes.KEY_LEFTCTRL,
            ],
            ecodes.EV_REL: [
                ecodes.REL_X,
                ecodes.REL_Y,
                ecodes.REL_WHEEL,
                ecodes.REL_HWHEEL,
            ],
        }
        self.uinput = UInput(capabilities, name="Pocket DS Touchpad")
        self.left_button_latch = PointerButtonLatch(
            self.press_virtual_left,
            self.release_virtual_left,
        )
        self.gestures = TouchpadGestureEngine()
        self.input_lock = threading.RLock()
        self.visible = False
        self.pointer_fallback_active = False
        self.gtk_fallback_active = False
        self.show_retry_source = 0
        self.touch_surface_source = 0
        self.raw_surface_ready = False
        self.raw_router = RawTouchRouter()
        self.raw_control_contact = None
        self.raw_control_target = None
        self.raw_left_chord_contacts = set()
        self.raw_touch = RawTouchscreenReader(self.queue_raw_touch)

        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_title("Pocket DS Touchpad")
        self.window.set_wmclass("pocketds-touchpad", "pocketds-touchpad")
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_keep_above(True)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_accept_focus(False)
        self.window.set_focus_on_map(False)
        self.window.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        self.window.connect("delete-event", self.on_delete)
        self.window.connect("unmap", self.on_unmap)

        self.install_css()
        self.build_layout()
        _SIGUSR1_GATE.mark_ready(self.queue_toggle)
        _SIGUSR2_GATE.mark_ready(self.queue_hide)
        _SIGHUP_GATE.mark_ready(self.queue_show)
        notify_systemd_ready()

    @staticmethod
    def label(text, style_class):
        label = Gtk.Label(label=text)
        label.get_style_context().add_class(style_class)
        label.set_xalign(0.0)
        return label

    def button(self, text, callback, style_class="secondary"):
        button = Gtk.Button(label=text)
        button.set_can_focus(False)
        button.set_relief(Gtk.ReliefStyle.NONE)
        for name in style_class.split():
            button.get_style_context().add_class(name)
        button.connect("clicked", callback)
        return button

    @staticmethod
    def draw_navigation_icon(widget, context, button, icon_name):
        color = button.get_style_context().get_color(button.get_state_flags())
        Gdk.cairo_set_source_rgba(context, color)
        context.translate(
            (widget.get_allocated_width() - 20.0) / 2.0,
            (widget.get_allocated_height() - 20.0) / 2.0,
        )
        context.set_line_width(1.6)
        context.set_line_join(1)
        context.set_line_cap(1)
        if icon_name == "keyboard":
            context.rectangle(1.5, 4.0, 17.0, 12.0)
            context.stroke()
            for x in (4.5, 8.0, 11.5, 15.0):
                context.move_to(x, 8.0)
                context.line_to(x, 8.5)
            context.move_to(5.0, 12.0)
            context.line_to(15.0, 12.0)
            context.stroke()
        else:
            for x, y in ((2.5, 2.5), (11.5, 2.5), (2.5, 11.5), (11.5, 11.5)):
                context.rectangle(x, y, 6.0, 6.0)
            context.stroke()
        return False

    def set_button_icon(self, button, icon_name):
        icon = Gtk.DrawingArea()
        icon.set_size_request(20, 20)
        icon.connect("draw", self.draw_navigation_icon, button, icon_name)
        button.set_image(icon)
        button.set_always_show_image(True)

    @staticmethod
    def draw_mouse_button_icon(widget, context, button, side):
        color = button.get_style_context().get_color(button.get_state_flags())
        Gdk.cairo_set_source_rgba(context, color)
        context.translate(
            (widget.get_allocated_width() - 18.0) / 2.0,
            (widget.get_allocated_height() - 24.0) / 2.0,
        )
        context.set_line_width(1.6)
        context.set_line_join(1)
        context.move_to(1.0, 8.0)
        context.curve_to(1.0, -1.0, 17.0, -1.0, 17.0, 8.0)
        context.line_to(17.0, 16.0)
        context.curve_to(17.0, 25.0, 1.0, 25.0, 1.0, 16.0)
        context.close_path()
        context.stroke()
        context.move_to(9.0, 2.0)
        context.line_to(9.0, 10.0)
        context.move_to(2.0, 10.0)
        context.line_to(16.0, 10.0)
        context.stroke()
        context.rectangle(4.0 if side == "left" else 11.0, 5.0, 3.0, 3.0)
        context.fill()
        return False

    def set_mouse_button_icon(self, button, side):
        icon = Gtk.DrawingArea()
        icon.set_size_request(18, 24)
        icon.connect("draw", self.draw_mouse_button_icon, button, side)
        button.set_image(icon)
        button.set_always_show_image(True)

    def install_css(self):
        css = b"""
        window { background: #090c0d; color: #f3f0e9; }
        .header {
          background: #0f1415;
          border-top: 3px solid #62d8d5;
          border-bottom: 1px solid #303839;
          padding: 10px 16px 11px;
        }
        .title {
          color: #f3f0e9;
          font-family: Noto Sans CJK SC;
          font-size: 24px;
          font-weight: 700;
        }
        .track {
          background: #101617;
          border: 1px solid #354142;
          border-radius: 14px;
        }
        button {
          color: #f3f0e9;
          background: #1d2425;
          border: 1px solid #303839;
          border-radius: 10px;
          font-family: Noto Sans CJK SC;
          font-size: 17px;
          font-weight: 600;
          min-height: 48px;
          padding: 3px 18px;
          -GtkButton-image-spacing: 8;
          background-image: none;
          box-shadow: none;
          text-shadow: none;
          transition: none;
        }
        button image { -gtk-icon-shadow: none; }
        button:hover {
          background: #273536;
          border-color: #557170;
        }
        button.mode {
          color: #8ce8e4;
          background: #17302f;
          border-color: #39716f;
        }
        button.click-button {
          color: #e5e9e5;
          background: #202829;
          border-color: #3d4b4c;
          border-top-color: #526060;
          min-height: 64px;
          font-size: 18px;
        }
        button.mode:hover {
          background: #20423f;
          border-color: #5da5a1;
        }
        button.click-button:hover {
          background: #2a3738;
          border-color: #647d7b;
        }
        button:active,
        button.mode:active,
        button.click-button:active,
        button.raw-active,
        button.mode.raw-active,
        button.click-button.raw-active {
          color: #090c0d;
          background: #62d8d5;
          border-color: #b9f4f1;
        }
        button:disabled,
        button.mode:disabled,
        button.click-button:disabled {
          color: #667473;
          background: #141b1c;
          border-color: #293333;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def build_layout(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.get_style_context().add_class("header")
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title_box.set_hexpand(True)
        title_box.set_valign(Gtk.Align.CENTER)
        title_box.pack_start(self.label("触摸板", "title"), False, False, 0)
        header.pack_start(title_box, True, True, 0)
        self.keyboard_button = self.button(
            "键盘", self.open_keyboard, "secondary mode"
        )
        self.panel_button = self.button("面板", self.manual_hide)
        self.set_button_icon(self.keyboard_button, "keyboard")
        self.set_button_icon(self.panel_button, "panel")
        header.pack_start(self.keyboard_button, False, False, 0)
        header.pack_start(self.panel_button, False, False, 0)
        root.pack_start(header, False, False, 0)

        track_margin = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        track_margin.set_margin_top(12)
        track_margin.set_margin_bottom(12)
        track_margin.set_margin_start(12)
        track_margin.set_margin_end(12)
        track_margin.set_vexpand(True)
        self.track = Gtk.EventBox()
        self.track.set_visible_window(True)
        self.track.set_above_child(False)
        self.track.set_hexpand(True)
        self.track.set_vexpand(True)
        self.track.get_style_context().add_class("track")
        self.track.add_events(
            Gdk.EventMask.TOUCH_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.BUTTON_MOTION_MASK
        )
        self.track.connect("touch-event", self.on_touch_event)
        self.track.connect("button-press-event", self.on_pointer_press)
        self.track.connect("motion-notify-event", self.on_pointer_motion)
        self.track.connect("button-release-event", self.on_pointer_release)

        track_art = Gtk.DrawingArea()
        track_art.connect("draw", self.draw_track_surface)
        self.track.add(track_art)
        track_margin.pack_start(self.track, True, True, 0)

        click_row = Gtk.Grid()
        click_row.set_hexpand(True)
        click_row.set_column_homogeneous(True)
        click_row.set_column_spacing(10)
        click_row.set_margin_top(10)
        self.left_button = self.button("左键", self.click_left, "click-button")
        self.right_button = self.button("右键", self.click_right, "click-button")
        self.set_mouse_button_icon(self.left_button, "left")
        self.set_mouse_button_icon(self.right_button, "right")
        click_row.attach(self.left_button, 0, 0, 1, 1)
        click_row.attach(self.right_button, 1, 0, 1, 1)
        track_margin.pack_start(click_row, False, False, 0)
        root.pack_start(track_margin, True, True, 0)

        self.window.add(root)

    @staticmethod
    def draw_track_surface(widget, context):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        center_x = width / 2.0
        center_y = height / 2.0
        context.set_line_cap(1)
        context.set_line_width(1.5)
        context.set_source_rgba(0.384, 0.847, 0.835, 0.14)
        # Quiet corner guides identify the entire touch surface without copy.
        inset = 18.0
        for x, y, dx, dy in (
            (inset, inset, 1.0, 1.0),
            (width - inset, inset, -1.0, 1.0),
            (inset, height - inset, 1.0, -1.0),
            (width - inset, height - inset, -1.0, -1.0),
        ):
            context.move_to(x, y + dy * 16.0)
            context.line_to(x, y)
            context.line_to(x + dx * 16.0, y)
        context.stroke()
        context.set_source_rgba(0.384, 0.847, 0.835, 0.22)
        context.arc(center_x, center_y, 20.0, 0.0, 6.283185)
        context.stroke()
        for dx, dy in ((-1.0, 0.0), (1.0, 0.0), (0.0, -1.0), (0.0, 1.0)):
            context.move_to(center_x + dx * 31.0, center_y + dy * 31.0)
            context.line_to(center_x + dx * 43.0, center_y + dy * 43.0)
        context.stroke()
        context.set_source_rgba(0.384, 0.847, 0.835, 0.46)
        context.arc(center_x, center_y, 2.5, 0.0, 6.283185)
        context.fill()
        return False

    def click_left(self, _button=None):
        self.wake_pointer()
        self.click(ecodes.BTN_LEFT)
        return False

    def click_right(self, _button=None):
        self.wake_pointer()
        if self.raw_touch.arm_context_menu_guard():
            self.click(ecodes.BTN_RIGHT)
        return False

    @staticmethod
    def sequence_key(event):
        sequence = event.get_event_sequence()
        try:
            return hash(sequence)
        except TypeError:
            return str(sequence)

    def cancel_fallback_input(self):
        if self.gtk_fallback_active or self.pointer_fallback_active:
            self.emit_actions(self.gestures.cancel())
        self.gtk_fallback_active = False
        self.pointer_fallback_active = False

    def on_touch_event(self, _widget, event):
        if self.raw_touch.is_active():
            self.cancel_fallback_input()
            return True
        if not self.visible or not self.raw_surface_ready:
            return True
        contact = self.sequence_key(event)
        if event.type == Gdk.EventType.TOUCH_BEGIN:
            self.gtk_fallback_active = True
            if self.pointer_fallback_active:
                self.emit_actions(self.gestures.cancel())
                self.pointer_fallback_active = False
            if not self.gestures.points:
                self.wake_pointer()
            actions = self.gestures.touch_begin(contact, event.x, event.y, event.time)
        elif event.type == Gdk.EventType.TOUCH_UPDATE:
            actions = self.gestures.touch_update(contact, event.x, event.y, event.time)
        elif event.type == Gdk.EventType.TOUCH_END:
            actions = self.gestures.touch_end(contact, event.x, event.y, event.time)
        elif event.type == Gdk.EventType.TOUCH_CANCEL:
            actions = self.gestures.cancel()
        else:
            return False
        self.emit_actions(actions)
        if not self.gestures.points:
            self.gtk_fallback_active = False
        return True

    def on_pointer_press(self, _widget, event):
        if self.raw_touch.is_active():
            self.cancel_fallback_input()
            return True
        if not self.visible or not self.raw_surface_ready:
            return True
        if event.button != 1 or self.gestures.points:
            return False
        self.pointer_fallback_active = True
        self.wake_pointer()
        self.emit_actions(
            self.gestures.touch_begin("pointer-fallback", event.x, event.y, event.time)
        )
        return True

    def on_pointer_motion(self, _widget, event):
        if self.raw_touch.is_active():
            self.cancel_fallback_input()
            return True
        if not self.visible or not self.raw_surface_ready:
            return True
        if not self.pointer_fallback_active:
            return False
        self.emit_actions(
            self.gestures.touch_update("pointer-fallback", event.x, event.y, event.time)
        )
        return True

    def on_pointer_release(self, _widget, event):
        if self.raw_touch.is_active():
            self.cancel_fallback_input()
            return True
        if not self.visible or not self.raw_surface_ready:
            return True
        if event.button != 1 or not self.pointer_fallback_active:
            return False
        self.pointer_fallback_active = False
        self.emit_actions(
            self.gestures.touch_end("pointer-fallback", event.x, event.y, event.time)
        )
        return True

    def queue_raw_touch(self, generation, raw_actions: list[RawTouchAction]):
        GLib.idle_add(self.on_raw_touch, generation, tuple(raw_actions))

    def on_raw_touch(self, generation, raw_actions: list[RawTouchAction]):
        if not self.raw_touch.is_current(generation):
            return False
        now_ms = int(time.monotonic() * 1000)
        actions: list[GestureAction] = []
        with self.input_lock:
            if not self.visible:
                return False
            if self.raw_touch.is_active():
                self.cancel_fallback_input()
            for raw in raw_actions:
                # A control release can hide/switch the overlay mid-batch.
                if not self.visible or not self.raw_touch.is_current(generation):
                    return False
                # The previous layout remains cached across hides. Do not use
                # it until this show's geometry has been measured; configuring
                # the router clears contacts and would otherwise lose their ends.
                if raw.kind != "cancel" and not self.raw_surface_ready:
                    continue
                routed = self.raw_router.route(raw)
                if routed is None:
                    continue
                if routed.kind == "cancel":
                    actions.extend(self.gestures.cancel())
                    self.gtk_fallback_active = False
                    self.pointer_fallback_active = False
                    self.cancel_raw_control()
                    self.raw_left_chord_contacts.clear()
                    continue
                if routed.target != "track":
                    self.handle_raw_control(routed)
                    continue
                if self.raw_control_target not in {None, "left"}:
                    continue
                if routed.kind == "begin":
                    if self.left_button_latch.held:
                        self.raw_left_chord_contacts.add(routed.contact)
                    if not self.gestures.points:
                        self._wake_pointer_unlocked()
                    actions.extend(
                        self.gestures.touch_begin(
                            routed.contact, routed.x, routed.y, now_ms
                        )
                    )
                elif routed.kind == "update":
                    actions.extend(
                        self.gestures.touch_update(
                            routed.contact, routed.x, routed.y, now_ms
                        )
                    )
                elif routed.kind == "end":
                    ended = self.gestures.touch_end(
                        routed.contact, routed.x, routed.y, now_ms
                    )
                    if routed.contact in self.raw_left_chord_contacts:
                        ended = [
                            action
                            for action in ended
                            if action.kind != "left_click"
                        ]
                        self.raw_left_chord_contacts.discard(routed.contact)
                    actions.extend(ended)
            if self.visible and self.raw_touch.is_current(generation):
                self._emit_actions_unlocked(actions)
        return False

    def handle_raw_control(self, routed):
        target = routed.target
        if target not in RAW_CONTROL_TARGETS:
            return
        if routed.kind == "begin":
            if self.raw_control_contact is not None:
                return
            if target != "left" and self.gestures.points:
                return
            self.raw_control_contact = routed.contact
            self.raw_control_target = target
            if target == "left":
                self.raw_left_chord_contacts.update(self.gestures.points)
                self.left_button_latch.acquire("raw-control")
                self.set_raw_control_active(target, True)
            else:
                self.set_raw_control_active(target, routed.inside)
            return
        if routed.contact != self.raw_control_contact:
            return
        if routed.kind == "update":
            if target != "left":
                self.set_raw_control_active(target, routed.inside)
            return
        if routed.kind != "end":
            return
        should_activate = target != "left" and routed.inside
        self.cancel_raw_control()
        if should_activate:
            self.activate_raw_control(target)

    def raw_control_button(self, target):
        return {
            "keyboard": self.keyboard_button,
            "panel": self.panel_button,
            "left": self.left_button,
            "right": self.right_button,
        }.get(target)

    def set_raw_control_active(self, target, active):
        button = self.raw_control_button(target)
        if button is None:
            return
        context = button.get_style_context()
        if active:
            context.add_class("raw-active")
        else:
            context.remove_class("raw-active")

    def cancel_raw_control(self):
        target = self.raw_control_target
        if target is not None:
            self.set_raw_control_active(target, False)
        self.left_button_latch.relinquish("raw-control")
        self.raw_control_contact = None
        self.raw_control_target = None

    def activate_raw_control(self, target):
        if target == "keyboard":
            self.open_keyboard()
        elif target == "panel":
            self.manual_hide()
        elif target == "right":
            self.click_right()

    def press_virtual_left(self):
        self.uinput.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 1)
        self.uinput.syn()

    def release_virtual_left(self):
        self.uinput.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 0)
        self.uinput.syn()

    def emit_actions(self, actions: list[GestureAction]):
        with self.input_lock:
            self._emit_actions_unlocked(actions)

    def _emit_actions_unlocked(self, actions: list[GestureAction]):
        for action in actions:
            if action.kind == "move":
                if action.x:
                    self.uinput.write(ecodes.EV_REL, ecodes.REL_X, action.x)
                if action.y:
                    self.uinput.write(ecodes.EV_REL, ecodes.REL_Y, action.y)
                self.uinput.syn()
            elif action.kind == "scroll":
                if action.x:
                    self.uinput.write(ecodes.EV_REL, ecodes.REL_HWHEEL, action.x)
                if action.y:
                    self.uinput.write(ecodes.EV_REL, ecodes.REL_WHEEL, action.y)
                self.uinput.syn()
            elif action.kind == "zoom":
                self.uinput.write(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 1)
                self.uinput.write(ecodes.EV_REL, ecodes.REL_WHEEL, action.y)
                self.uinput.syn()
                self.uinput.write(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 0)
                self.uinput.syn()
            elif action.kind == "left_click":
                if not self.left_button_latch.held:
                    self.click(ecodes.BTN_LEFT)
                self.raw_touch.release_context_menu_guard()
            elif action.kind == "right_click":
                if self.raw_touch.arm_context_menu_guard():
                    self.click(ecodes.BTN_RIGHT)
            elif action.kind == "left_down":
                self.left_button_latch.acquire("gesture")
            elif action.kind == "left_up":
                self.left_button_latch.relinquish("gesture")
                self.raw_touch.release_context_menu_guard()

    def click(self, button):
        with self.input_lock:
            self._click_unlocked(button)

    def _click_unlocked(self, button):
        self.uinput.write(ecodes.EV_KEY, button, 1)
        self.uinput.syn()
        self.uinput.write(ecodes.EV_KEY, button, 0)
        self.uinput.syn()

    def wake_pointer(self):
        # KWin hides the cursor after a physical touchscreen event. Two tiny
        # relative mouse frames make it visible without changing its position.
        with self.input_lock:
            self._wake_pointer_unlocked()
        return False

    def _wake_pointer_unlocked(self):
        self.uinput.write(ecodes.EV_REL, ecodes.REL_X, 1)
        self.uinput.syn()
        self.uinput.write(ecodes.EV_REL, ecodes.REL_X, -1)
        self.uinput.syn()

    def widget_touch_region(self, widget):
        allocation = widget.get_allocation()
        translated = widget.translate_coordinates(self.window, 0, 0)
        if translated is None or allocation.width <= 0 or allocation.height <= 0:
            return None
        return (
            float(translated[-2]),
            float(translated[-1]),
            float(allocation.width),
            float(allocation.height),
        )

    def activate_touch_surface(self):
        self.touch_surface_source = 0
        if not self.visible:
            return False
        window = self.window.get_allocation()
        widgets = (
            ("keyboard", self.keyboard_button),
            ("panel", self.panel_button),
            ("left", self.left_button),
            ("right", self.right_button),
            ("track", self.track),
        )
        regions = []
        for target, widget in widgets:
            region = self.widget_touch_region(widget)
            if region is None:
                print("touchpad: raw control geometry unavailable", flush=True)
                self.manual_hide()
                return False
            regions.append((target, region))
        # Reconfiguration is an input ownership boundary, even if invoked
        # again while already mapped. Release before forgetting routed contacts.
        self.emit_actions(self.gestures.cancel())
        self.cancel_raw_control()
        self.left_button_latch.clear()
        self.gtk_fallback_active = False
        self.pointer_fallback_active = False
        self.raw_left_chord_contacts.clear()
        if not self.raw_router.configure(window.width, window.height, regions):
            print("touchpad: invalid raw control geometry", flush=True)
            self.manual_hide()
            return False
        self.raw_surface_ready = True
        self.wake_pointer()
        return False

    def cancel_touch_surface_activation(self):
        self.raw_surface_ready = False
        if self.touch_surface_source:
            GLib.source_remove(self.touch_surface_source)
            self.touch_surface_source = 0

    def queue_toggle(self):
        GLib.idle_add(self.manual_toggle)

    def queue_hide(self):
        GLib.idle_add(self.manual_hide)

    def queue_show(self):
        GLib.idle_add(self.manual_show)

    def manual_toggle(self, _button=None):
        if self.visible:
            return self.manual_hide()
        return self.manual_show()

    def manual_show(self, _button=None):
        if self.visible:
            return False
        if not self.raw_touch.set_enabled(True, FULL_TOUCH_REGION):
            if not self.show_retry_source:
                self.show_retry_source = GLib.timeout_add(
                    100, self.retry_manual_show
                )
            return False
        if self.show_retry_source:
            GLib.source_remove(self.show_retry_source)
            self.show_retry_source = 0
        if not publish_touchpad_visibility(True):
            self.raw_touch.set_enabled(False)
            return False
        self.raw_surface_ready = False
        self.visible = True
        self.window.show_all()
        self.touch_surface_source = GLib.timeout_add(80, self.activate_touch_surface)
        return False

    def retry_manual_show(self):
        self.show_retry_source = 0
        if not self.visible:
            self.manual_show()
        return False

    def manual_hide(self, _button=None):
        self.cancel_touch_surface_activation()
        self.gtk_fallback_active = False
        if self.show_retry_source:
            GLib.source_remove(self.show_retry_source)
            self.show_retry_source = 0
        publish_touchpad_visibility(False)
        self.emit_actions(self.gestures.cancel())
        self.raw_router.reset()
        self.cancel_raw_control()
        self.left_button_latch.clear()
        self.raw_left_chord_contacts.clear()
        self.pointer_fallback_active = False
        self.visible = False
        self.window.hide()
        self.raw_touch.set_enabled(False)
        return False

    def open_keyboard(self, _button=None):
        self.manual_hide()
        GLib.spawn_async(["/usr/local/bin/pocketds-panelctl", "keyboard"])
        return False

    def on_unmap(self, *_args):
        self.cancel_touch_surface_activation()
        self.gtk_fallback_active = False
        self.pointer_fallback_active = False
        publish_touchpad_visibility(False)
        self.emit_actions(self.gestures.cancel())
        self.raw_router.reset()
        self.cancel_raw_control()
        self.left_button_latch.clear()
        self.raw_left_chord_contacts.clear()
        self.visible = False
        self.raw_touch.set_enabled(False)
        return False

    def on_delete(self, *_args):
        self.manual_hide()
        return True

    def run(self):
        try:
            Gtk.main()
        finally:
            self.cancel_touch_surface_activation()
            publish_touchpad_visibility(False)
            self.emit_actions(self.gestures.cancel())
            self.cancel_raw_control()
            self.left_button_latch.clear()
            self.raw_left_chord_contacts.clear()
            self.raw_touch.close()
            _SIGUSR1_GATE.clear()
            _SIGUSR2_GATE.clear()
            _SIGHUP_GATE.clear()
            self.uinput.close()


if __name__ == "__main__":
    PocketDSTouchpad().run()

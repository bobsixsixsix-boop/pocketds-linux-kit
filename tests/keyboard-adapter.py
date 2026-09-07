#!/usr/bin/python3
"""Unit and static tests for the PDS-004 GTK/GLib/AT-SPI adapter draft."""

from __future__ import annotations

import ast
from contextlib import contextmanager, nullcontext
import heapq
import importlib.util
import json
import os
import queue
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
KEYBOARD_COMPONENT = ROOT / "components" / "keyboard"
SERVICE = KEYBOARD_COMPONENT / "pocketds-keyboard.service"
PANELCTL = ROOT / "components" / "control-panel" / "pocketds-panelctl.cpp"
INSTALLER = ROOT / "scripts" / "install.sh"
sys.path.insert(0, str(KEYBOARD_COMPONENT))

from keyboard_adapter import (  # noqa: E402
    BoundedRepeatController,
    BackspaceClearGestureController,
    CaptureStartupController,
    CaptureStartupFailure,
    GLibScheduler,
    HoldTapController,
    KeyHapticFeedback,
    KeyboardFeedbackSettings,
    KeyboardFeedbackSettingsStore,
    KeySoundFeedback,
    KeyboardVisibilityAdapter,
    MicrophoneSessionMonitor,
    RecorderStopController,
    RecorderStopFailure,
    RecorderStopResult,
    SignalToggleReadinessGate,
    VoiceSessionController,
    VoiceState,
    accessible_process_id,
    stable_accessible_application_id,
    stable_accessible_id,
    stop_recorder_process,
    trusted_accessible_id,
)
from visibility_state import VisibilityState  # noqa: E402
import keyboard_adapter  # noqa: E402
import voice_artifacts  # noqa: E402
from voice_artifacts import (  # noqa: E402
    VoiceArtifactError,
    finalize_private_arecord_wav,
    private_wav_has_pcm_payload,
    read_private_wav,
)


@dataclass
class FakeTimerHandle:
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeScheduler:
    def __init__(self) -> None:
        self.now_ms = 0
        self.sequence = 0
        self.events: list[tuple[int, int, FakeTimerHandle, Callable[[], None]]] = []

    def call_later(
        self, delay_ms: int, callback: Callable[[], None]
    ) -> FakeTimerHandle:
        self.sequence += 1
        handle = FakeTimerHandle()
        heapq.heappush(
            self.events,
            (self.now_ms + delay_ms, self.sequence, handle, callback),
        )
        return handle

    def advance(self, delta_ms: int, *, deliver_cancelled: bool = False) -> None:
        target = self.now_ms + delta_ms
        while self.events and self.events[0][0] <= target:
            deadline, _sequence, handle, callback = heapq.heappop(self.events)
            self.now_ms = deadline
            if not handle.cancelled or deliver_cancelled:
                callback()
        self.now_ms = target


class FailingScheduler(FakeScheduler):
    def __init__(self, *, fail_on_call: int) -> None:
        super().__init__()
        self.fail_on_call = fail_on_call
        self.calls = 0

    def call_later(
        self, delay_ms: int, callback: Callable[[], None]
    ) -> FakeTimerHandle:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("synthetic scheduler failure")
        return super().call_later(delay_ms, callback)


class FakeGLib:
    def __init__(self) -> None:
        self.next_id = 1
        self.sources: dict[int, Callable[[], bool]] = {}
        self.removed: list[int] = []

    def timeout_add(self, _delay_ms: int, callback: Callable[[], bool]) -> int:
        source_id = self.next_id
        self.next_id += 1
        self.sources[source_id] = callback
        return source_id

    def source_remove(self, source_id: int) -> None:
        self.removed.append(source_id)
        self.sources.pop(source_id, None)

    def fire(self, source_id: int) -> bool:
        callback = self.sources.pop(source_id)
        return callback()


class FakeCanberraLibrary:
    def __init__(self, *, create_result=0, open_result=0, play_result=0) -> None:
        self.create_result = create_result
        self.open_result = open_result
        self.play_result = play_result
        self.drivers = []
        self.context_properties = []
        self.plays = []
        self.destroy_calls = 0

    def ca_context_create(self, output) -> int:
        if self.create_result >= 0:
            output._obj.value = 41
        return self.create_result

    def ca_context_set_driver(self, _context, driver) -> int:
        self.drivers.append(driver)
        return 0

    def ca_context_change_props(self, _context, *properties) -> int:
        normalized = []
        for value in properties:
            normalized.append(getattr(value, "value", value))
        self.context_properties.append(tuple(normalized))
        return 0

    def ca_context_open(self, _context) -> int:
        return self.open_result

    def ca_context_play(self, _context, _event_number, *properties) -> int:
        normalized = []
        for value in properties:
            value = getattr(value, "value", value)
            normalized.append(value)
        self.plays.append(tuple(normalized))
        return self.play_result

    def ca_context_destroy(self, _context) -> int:
        self.destroy_calls += 1
        return 0


class KeySoundFeedbackTests(unittest.TestCase):
    def test_reuses_one_pulse_context_and_rate_limits_fast_taps(self) -> None:
        clock = [10.0]
        library = FakeCanberraLibrary()
        feedback = KeySoundFeedback(
            monotonic=lambda: clock[0],
            library_finder=lambda _name: "fake-canberra",
            library_loader=lambda _name: library,
        )

        self.assertTrue(feedback.available)
        self.assertEqual(library.drivers, [b"pulse"])
        self.assertIn(b"state.restore-props", library.context_properties[0])
        self.assertIn(b"false", library.context_properties[0])
        self.assertTrue(feedback.play())
        self.assertFalse(feedback.play())
        clock[0] += 0.051
        self.assertTrue(feedback.play(modifier=True))
        self.assertEqual(len(library.plays), 2)
        self.assertIn(b"button-pressed", library.plays[0])
        self.assertIn(b"button-pressed-modifier", library.plays[1])
        self.assertIn(b"never", library.plays[0])
        self.assertIn(b"-36.0", library.plays[0])
        self.assertIn(b"media.role", library.plays[0])
        self.assertIn(b"PocketDSKeyboardFeedback360", library.plays[0])
        self.assertIn(b"state.restore-props", library.plays[0])
        self.assertIn(b"false", library.plays[0])

        feedback.close()
        feedback.close()
        self.assertFalse(feedback.available)
        self.assertEqual(library.destroy_calls, 1)

    def test_audio_failure_is_contained_then_retries_after_cooldown(self) -> None:
        clock = [20.0]
        failed = FakeCanberraLibrary(play_result=-1)
        recovered = FakeCanberraLibrary()
        libraries = iter((failed, recovered))
        feedback = KeySoundFeedback(
            monotonic=lambda: clock[0],
            library_finder=lambda _name: "fake-canberra",
            library_loader=lambda _name: next(libraries),
        )

        self.assertFalse(feedback.play())
        self.assertFalse(feedback.available)
        self.assertFalse(feedback.play())
        self.assertEqual(len(failed.plays), 1)
        self.assertEqual(failed.destroy_calls, 1)
        self.assertEqual(recovered.plays, [])

        clock[0] += 5.001
        self.assertTrue(feedback.play())
        self.assertTrue(feedback.available)
        self.assertEqual(len(recovered.plays), 1)
        feedback.close()
        self.assertEqual(recovered.destroy_calls, 1)

    def test_runtime_volume_and_opt_out_apply_without_reopening_context(self) -> None:
        clock = [30.0]
        library = FakeCanberraLibrary()
        feedback = KeySoundFeedback(
            monotonic=lambda: clock[0],
            library_finder=lambda _name: "fake-canberra",
            library_loader=lambda _name: library,
        )

        feedback.configure(enabled=False, volume_db=-54.0)
        self.assertFalse(feedback.enabled)
        self.assertEqual(feedback.volume_db, -54.0)
        self.assertFalse(feedback.play())
        feedback.configure(enabled=True)
        self.assertTrue(feedback.play())
        self.assertIn(b"-54.0", library.plays[0])
        self.assertIn(b"PocketDSKeyboardFeedback540", library.plays[0])
        with self.assertRaises(ValueError):
            feedback.configure(volume_db=-5.0)
        with self.assertRaises(ValueError):
            feedback.configure(volume_db=-61.0)
        feedback.close()

    def test_missing_library_and_explicit_opt_out_are_fail_open(self) -> None:
        def missing_library(_name):
            raise OSError("synthetic missing library")

        missing = KeySoundFeedback(
            library_finder=lambda _name: "missing-canberra",
            library_loader=missing_library,
        )
        disabled = KeySoundFeedback(enabled=False)

        self.assertFalse(missing.available)
        self.assertFalse(missing.play())
        self.assertFalse(disabled.available)
        self.assertFalse(disabled.play())


class KeyboardFeedbackSettingsStoreTests(unittest.TestCase):
    def test_atomic_round_trip_permissions_and_corrupt_fallback(self) -> None:
        defaults = KeyboardFeedbackSettings()
        selected = KeyboardFeedbackSettings(
            sound_enabled=False,
            sound_level=3,
            haptic_enabled=True,
            haptic_level=5,
        )
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "keyboard" / "feedback.json"
            store = KeyboardFeedbackSettingsStore(config, defaults)

            self.assertEqual(store.load(), defaults)
            self.assertTrue(store.save(selected))
            self.assertEqual(store.load(), selected)
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(config.parent.glob("*.new")), [])

            config.write_text("{broken", encoding="utf-8")
            self.assertEqual(store.load(), defaults)

    def test_feedback_levels_are_bounded(self) -> None:
        with self.assertRaises(ValueError):
            KeyboardFeedbackSettings(sound_level=0)
        with self.assertRaises(ValueError):
            KeyboardFeedbackSettings(haptic_level=6)


class FakeHapticBus:
    def __init__(self, *, blocked: bool = False, failure: bool = False) -> None:
        self.blocked = blocked
        self.failure = failure
        self.calls = []
        self.thread_ids = []
        self.called = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.close_calls = 0

    def call_blocking(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.thread_ids.append(threading.get_ident())
        self.called.set()
        try:
            if self.blocked:
                self.release.wait(timeout=1.0)
            if self.failure:
                raise RuntimeError("synthetic D-Bus failure")
        finally:
            self.finished.set()

    def close(self) -> None:
        self.close_calls += 1


class KeyHapticFeedbackTests(unittest.TestCase):
    @staticmethod
    def wait_idle(feedback: KeyHapticFeedback) -> None:
        deadline = time.monotonic() + 1.0
        while feedback.busy and time.monotonic() < deadline:
            time.sleep(0.005)
        if feedback.busy:
            raise AssertionError("haptic worker did not become idle")

    def test_pulse_runs_off_thread_and_busy_taps_are_dropped(self) -> None:
        clock = [10.0]
        bus = FakeHapticBus(blocked=True)
        feedback = KeyHapticFeedback(
            monotonic=lambda: clock[0],
            bus_factory=lambda: bus,
        )
        caller_thread = threading.get_ident()

        self.assertTrue(feedback.play())
        self.assertTrue(bus.called.wait(timeout=0.5))
        self.assertFalse(feedback.play())
        clock[0] += 1.0
        self.assertFalse(feedback.play())
        self.assertEqual(bus.thread_ids, [bus.thread_ids[0]])
        self.assertNotEqual(bus.thread_ids[0], caller_thread)
        args, kwargs = bus.calls[0]
        self.assertEqual(
            args,
            (
                "org.shadowblip.InputPlumber",
                "/org/shadowblip/InputPlumber/CompositeDevice0",
                "org.shadowblip.Output.ForceFeedback",
                "Pulse",
                "du",
                (0.10, 20),
            ),
        )
        self.assertEqual(kwargs, {"timeout": 0.20})

        bus.release.set()
        self.assertTrue(bus.finished.wait(timeout=0.5))
        self.wait_idle(feedback)
        feedback.close()
        feedback.close()
        self.assertEqual(bus.close_calls, 1)

    def test_failure_cools_down_without_blocking_key_path(self) -> None:
        clock = [20.0]
        failed = FakeHapticBus(failure=True)
        recovered = FakeHapticBus()
        buses = iter((failed, recovered))
        feedback = KeyHapticFeedback(
            monotonic=lambda: clock[0],
            bus_factory=lambda: next(buses),
        )

        self.assertTrue(feedback.play())
        self.assertTrue(failed.finished.wait(timeout=0.5))
        self.wait_idle(feedback)
        self.assertFalse(feedback.play())
        self.assertEqual(recovered.calls, [])

        clock[0] += 5.001
        self.assertTrue(feedback.play())
        self.assertTrue(recovered.finished.wait(timeout=0.5))
        self.wait_idle(feedback)
        feedback.close()
        self.assertEqual(failed.close_calls, 1)
        self.assertEqual(recovered.close_calls, 1)

    def test_hardware_limits_and_opt_out_are_explicit(self) -> None:
        with self.assertRaises(ValueError):
            KeyHapticFeedback(intensity=0.71)
        with self.assertRaises(ValueError):
            KeyHapticFeedback(duration_ms=51)
        disabled = KeyHapticFeedback(enabled=False)
        self.assertFalse(disabled.play())
        disabled.close()

    def test_runtime_strength_and_duration_stay_bounded(self) -> None:
        feedback = KeyHapticFeedback(enabled=False)
        feedback.configure(intensity=0.70, duration_ms=50)
        self.assertEqual(feedback.intensity, 0.70)
        with self.assertRaises(ValueError):
            feedback.configure(duration_ms=51)
        feedback.close()

    def test_mode_gate_drops_haptic_without_opening_system_bus(self) -> None:
        clock = [30.0]
        allowed = [False]
        bus = FakeHapticBus()
        factory_calls = []

        def factory():
            factory_calls.append(True)
            return bus

        feedback = KeyHapticFeedback(
            monotonic=lambda: clock[0],
            bus_factory=factory,
            permission_guard=lambda: nullcontext(allowed[0]),
        )
        self.assertTrue(feedback.play())
        self.wait_idle(feedback)
        self.assertEqual(bus.calls, [])
        self.assertEqual(factory_calls, [])
        clock[0] += 0.056
        allowed[0] = True
        self.assertTrue(feedback.play())
        self.assertTrue(bus.finished.wait(timeout=0.5))
        self.wait_idle(feedback)
        feedback.close()
        self.assertEqual(len(bus.calls), 1)

    def test_close_during_bus_factory_never_sends_a_late_pulse(self) -> None:
        bus = FakeHapticBus()
        factory_entered = threading.Event()
        factory_release = threading.Event()

        def factory():
            factory_entered.set()
            factory_release.wait(timeout=1.0)
            return bus

        feedback = KeyHapticFeedback(
            bus_factory=factory,
            call_timeout_s=0.01,
        )
        self.assertTrue(feedback.play())
        self.assertTrue(factory_entered.wait(timeout=0.5))
        feedback.close()
        factory_release.set()
        feedback.close()
        self.assertEqual(bus.calls, [])
        self.assertEqual(bus.close_calls, 1)

    def test_mode_gate_failure_is_fail_closed(self) -> None:
        @contextmanager
        def broken_guard():
            raise OSError("state read")
            yield True

        bus = FakeHapticBus()
        feedback = KeyHapticFeedback(
            bus_factory=lambda: bus,
            permission_guard=broken_guard,
        )
        self.assertTrue(feedback.play())
        self.wait_idle(feedback)
        feedback.close()
        self.assertEqual(bus.calls, [])

    def test_permission_guard_is_held_through_pulse(self) -> None:
        guard_active = [False]

        class GuardCheckingBus(FakeHapticBus):
            def call_blocking(self, *args, **kwargs) -> None:
                if not guard_active[0]:
                    raise AssertionError("permission guard released before Pulse")
                super().call_blocking(*args, **kwargs)

        @contextmanager
        def guard():
            guard_active[0] = True
            try:
                yield True
            finally:
                guard_active[0] = False

        bus = GuardCheckingBus()
        feedback = KeyHapticFeedback(
            bus_factory=lambda: bus,
            permission_guard=guard,
        )
        self.assertTrue(feedback.play())
        self.assertTrue(bus.finished.wait(timeout=0.5))
        self.wait_idle(feedback)
        feedback.close()
        self.assertEqual(len(bus.calls), 1)
        self.assertFalse(guard_active[0])


class FakeApplication:
    def __init__(self, bus_name: str) -> None:
        self.bus_name = bus_name


class FakeAccessible:
    def __init__(self, bus_name: str, path: str, *, valid: bool = True) -> None:
        self.app = FakeApplication(bus_name)
        self.path = path
        self.valid = valid


class FakeProcessAccessible:
    def __init__(self, process_id) -> None:
        self.process_id = process_id

    def get_process_id(self):
        return self.process_id


class FakeRecorderProcess:
    def __init__(
        self,
        returncode=None,
        *,
        timeout_once: bool = False,
        timeout_always: bool = False,
        exit_on_kill: bool = False,
        poll_error: bool = False,
        signal_error: bool = False,
        kill_error: bool = False,
    ) -> None:
        self.returncode = returncode
        self.timeout_once = timeout_once
        self.timeout_always = timeout_always
        self.exit_on_kill = exit_on_kill
        self.poll_error = poll_error
        self.signal_error = signal_error
        self.kill_error = kill_error
        self.signals = []
        self.killed = False
        self.kill_calls = 0
        self.wait_calls = []

    def poll(self):
        if self.poll_error:
            raise OSError("synthetic poll failure")
        return self.returncode

    def send_signal(self, requested_signal) -> None:
        if self.signal_error:
            raise OSError("synthetic signal failure")
        self.signals.append(requested_signal)

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.timeout_always:
            raise subprocess.TimeoutExpired("parecord", timeout)
        if self.timeout_once:
            self.timeout_once = False
            raise subprocess.TimeoutExpired("parecord", timeout)
        if self.returncode is None:
            self.returncode = -9 if self.killed else 0
        return self.returncode

    def kill(self) -> None:
        self.kill_calls += 1
        if self.kill_error:
            raise OSError("synthetic kill failure")
        self.killed = True
        if self.exit_on_kill:
            self.returncode = -9


def load_keyboard_runtime_for_capture_tests():
    """Import the GTK runtime with inert desktop dependencies.

    These tests exercise the real PocketDSKeyboard capture callbacks without
    requiring a display server, D-Bus, evdev, GI, or AT-SPI on the build host.
    The production helper modules remain real; only import-time platform edges
    and unrelated ASR backends are replaced.
    """

    class FakeEcodes:
        EV_KEY = 1

        def __getattr__(self, name):
            return sum(name.encode("utf-8")) + len(name) * 10_000

    dbus = types.ModuleType("dbus")
    dbus.__path__ = []
    dbus.SessionBus = lambda: None
    dbus.SystemBus = lambda: None
    dbus.DBusException = RuntimeError
    dbus.Interface = lambda *_args, **_kwargs: None
    dbus.Boolean = bool
    dbus_mainloop = types.ModuleType("dbus.mainloop")
    dbus_mainloop.__path__ = []
    dbus_glib = types.ModuleType("dbus.mainloop.glib")
    dbus_glib.DBusGMainLoop = lambda **_kwargs: None
    dbus_glib.threads_init = lambda: None

    evdev = types.ModuleType("evdev")
    evdev.InputDevice = object
    evdev.UInput = object
    evdev.ecodes = FakeEcodes()
    touchpad_raw = types.ModuleType("touchpad_raw")
    touchpad_raw.RawTouchAction = object
    touchpad_raw.TypeBTouchFrame = object
    touchpad_raw.resync_type_b = lambda device, tracker: None

    repository = types.ModuleType("gi.repository")
    repository.Gdk = types.SimpleNamespace()
    repository.GLib = types.SimpleNamespace()
    repository.Gtk = types.SimpleNamespace()
    gi = types.ModuleType("gi")
    gi.__path__ = []
    gi.require_version = lambda *_args: None
    gi.repository = repository

    pyatspi = types.ModuleType("pyatspi")
    pyatspi.ROLE_TERMINAL = 1

    fake_artifacts = types.ModuleType("voice_artifacts")

    class FakeArtifactError(RuntimeError):
        pass

    class FakeAsrApiError(FakeArtifactError):
        public_message = "synthetic ASR failure"

    class FakeAsrApiCancelled(FakeAsrApiError):
        pass

    fake_artifacts.AsrApiClient = object
    fake_artifacts.AsrApiError = FakeAsrApiError
    fake_artifacts.AsrApiCancelled = FakeAsrApiCancelled
    fake_artifacts.VoiceArtifactError = FakeArtifactError
    fake_artifacts.disable_process_dumpability = lambda: True
    fake_artifacts.finalize_private_arecord_wav = lambda _path: None
    fake_artifacts.normalize_transcript = lambda value: value
    fake_artifacts.parse_sensevoice_transcript = lambda _value: ""
    fake_artifacts.private_wav_has_pcm_payload = lambda _path: False
    fake_artifacts.read_private_wav = lambda _path: b""
    fake_artifacts.remove_owned_artifact = lambda _path: False
    fake_artifacts.run_bounded_command = lambda *_args, **_kwargs: (1, b"")
    fake_artifacts.sensevoice_backend_available = lambda *_args: False
    fake_artifacts.sensevoice_command = lambda *_args: []

    stubs = {
        "dbus": dbus,
        "dbus.mainloop": dbus_mainloop,
        "dbus.mainloop.glib": dbus_glib,
        "evdev": evdev,
        "gi": gi,
        "touchpad_raw": touchpad_raw,
        "gi.repository": repository,
        "pyatspi": pyatspi,
        "voice_artifacts": fake_artifacts,
    }
    spec = importlib.util.spec_from_file_location(
        f"pocketds_keyboard_capture_test_{time.monotonic_ns()}",
        KEYBOARD_COMPONENT / "pocketds-keyboard.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("keyboard runtime test module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    previous_umask = os.umask(0o077)
    os.umask(previous_umask)
    try:
        with (
            mock.patch.dict(sys.modules, stubs),
            mock.patch.object(signal, "signal", return_value=None),
        ):
            spec.loader.exec_module(module)
    finally:
        os.umask(previous_umask)
    return module


class RuntimeVoiceFailureLabelTests(unittest.TestCase):
    NOT_CONFIGURED = "语音 API 尚未配置"

    def make_app(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        app.mic_button = None
        app.mic_error_label = types.SimpleNamespace(text="识别失败")
        app.mic_error_label.set_text = lambda value: setattr(app.mic_error_label, "text", value)
        app.voice_error_feedback = None
        app.voice_feedback_source = 0
        app.visibility = types.SimpleNamespace(set_voice_busy=lambda _value: None)
        app.clear_space_voice_session = lambda _generation: None
        timers = []
        def timeout(delay, callback, generation):
            timers.append((delay, callback, generation))
            return len(timers)
        runtime.GLib = types.SimpleNamespace(timeout_add=timeout, source_remove=lambda _source: None)
        app.voice = runtime.VoiceSessionController(app.on_voice_state_changed)
        return app, timers

    def fail_voice(self, app, message, generation):
        with mock.patch("builtins.print"):
            app.voice_failed(message, generation)

    def test_confirmed_failures_show_short_labels_with_original_feedback_duration(self):
        for message, expected in ((self.NOT_CONFIGURED, "语音未配置"),
                                  ("语音 API 尚未配置", "语音未配置")):
            with self.subTest(expected=expected):
                app, timers = self.make_app()
                generation = app.voice.begin_connecting("field")
                self.fail_voice(app, message, generation)
                self.assertEqual(app.mic_error_label.text, expected)
                self.assertEqual(app.voice.state, VoiceState.ERROR)
                self.assertEqual(len(timers), 1)
                self.assertEqual(timers[0][0], 1_800)

    def test_arbitrary_or_similar_server_text_stays_generic(self):
        for message in ("server-account-value", self.NOT_CONFIGURED + " extra", "HTTP 403"):
            app, _timers = self.make_app()
            generation = app.voice.begin_recording("field")
            self.fail_voice(app, message, generation)
            self.assertEqual(app.mic_error_label.text, "识别失败")
            self.assertEqual(app.voice_error_feedback, (generation, "识别失败"))

    def test_stale_error_cannot_replace_a_new_recording_or_its_error(self):
        app, _timers = self.make_app()
        old = app.voice.begin_connecting("field")
        self.fail_voice(app, self.NOT_CONFIGURED, old)
        current = app.voice.begin_connecting("field")
        self.fail_voice(app, self.NOT_CONFIGURED, old)
        self.assertEqual(app.voice.state, VoiceState.CONNECTING)
        self.assertEqual(app.mic_error_label.text, "识别失败")
        self.assertIsNone(app.voice_error_feedback)
        self.fail_voice(app, "语音 API 尚未配置", current)
        self.fail_voice(app, self.NOT_CONFIGURED, old)
        self.assertEqual(app.mic_error_label.text, "语音未配置")
        self.assertEqual(app.voice_error_feedback, (current, "语音未配置"))

    def test_cancel_timeout_and_next_success_clear_specific_error(self):
        for action in ("cancel", "timeout", "success"):
            with self.subTest(action=action):
                app, timers = self.make_app()
                generation = app.voice.begin_recording("field")
                self.fail_voice(app, self.NOT_CONFIGURED, generation)
                if action == "cancel":
                    app.voice.cancel()
                elif action == "timeout":
                    timers[-1][1](generation)
                else:
                    current = app.voice.begin_recording("field")
                    self.assertTrue(app.voice.begin_stopping(current, "field", target_is_valid=True))
                    self.assertTrue(app.voice.begin_recognition(current, "field", target_is_valid=True))
                    self.assertTrue(app.voice.prepare_paste(current, "field", target_is_valid=True))
                    self.assertTrue(app.voice.consume_paste(current, "field", target_is_valid=True))
                    self.assertEqual(app.voice.state, VoiceState.SUCCESS)
                self.assertIsNone(app.voice_error_feedback)
                self.assertEqual(app.mic_error_label.text, "识别失败")
                app.voice.flash_error()
                self.assertEqual(app.mic_error_label.text, "识别失败")


class VanishingAccessible:
    """Proxy whose stable AT-SPI metadata disappears with its application."""

    def __init__(self, bus_name: str, path: str) -> None:
        self._application = FakeApplication(bus_name)
        self._path = path
        self.valid = True
        self.vanished = False

    @property
    def app(self):
        if self.vanished:
            raise RuntimeError("application exited")
        return self._application

    @property
    def path(self):
        if self.vanished:
            raise RuntimeError("accessible became defunct")
        return self._path


class AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeScheduler()
        self.snapshots = []
        self.adapter = KeyboardVisibilityAdapter(
            self.clock,
            lambda source: source.valid,
            self.snapshots.append,
        )

    def test_fifty_manual_cycles_cross_old_900ms_window(self) -> None:
        for cycle in range(50):
            with self.subTest(cycle=cycle):
                self.adapter.manual_show()
                self.clock.advance(1_200, deliver_cancelled=True)
                self.assertEqual(self.adapter.state, VisibilityState.MANUAL_VISIBLE)
                self.adapter.manual_hide()
                self.assertEqual(self.adapter.state, VisibilityState.HIDDEN_BY_USER)

    def test_new_focus_wins_over_stale_old_focus_loss(self) -> None:
        first = FakeAccessible(":1.10", "/field/1")
        second = FakeAccessible(":1.10", "/field/2")
        self.adapter.editable_focus_gained(first)
        self.clock.advance(100)
        self.adapter.editable_focus_gained(second)

        first.valid = False
        self.adapter.editable_focus_lost(first)
        self.clock.advance(1_000, deliver_cancelled=True)

        self.assertEqual(self.adapter.state, VisibilityState.AUTO_VISIBLE)
        self.assertIs(self.adapter.focused_editable, second)

    def test_valid_editable_survives_noneditable_chromium_event(self) -> None:
        field = FakeAccessible(":1.20", "/chromium/form/7")
        document = FakeAccessible(":1.20", "/chromium/document/1")
        self.adapter.editable_focus_gained(field)
        self.adapter.noneditable_focus_gained(document)
        self.clock.advance(900)

        self.assertEqual(self.adapter.state, VisibilityState.AUTO_VISIBLE)
        self.assertIs(self.adapter.focused_editable, field)

    def test_accessible_process_id_is_positive_and_fail_closed(self) -> None:
        self.assertEqual(accessible_process_id(FakeProcessAccessible("123")), 123)
        self.assertIsNone(accessible_process_id(FakeProcessAccessible(0)))
        self.assertIsNone(accessible_process_id(object()))

    def test_cross_application_focus_releases_stale_qt_terminal(self) -> None:
        terminal = FakeAccessible(":1.22", "/konsole/terminal/1")
        probe = FakeAccessible(":1.24", "/probe/label/1")
        self.adapter.editable_focus_gained(terminal)

        # The inactive Qt terminal still advertises FOCUSED in the live tree.
        self.assertTrue(terminal.valid)
        self.adapter.noneditable_focus_gained(probe)
        self.clock.advance(379)
        self.assertEqual(self.adapter.state, VisibilityState.AUTO_VISIBLE)
        self.clock.advance(1)

        self.assertEqual(self.adapter.state, VisibilityState.HIDDEN_AUTO)
        self.assertIsNone(self.adapter.focused_editable)

    def test_voice_busy_revokes_paste_target_but_holds_window_until_finish(self) -> None:
        field = FakeAccessible(":1.30", "/terminal/1")
        self.adapter.editable_focus_gained(field)
        self.adapter.set_voice_busy(True)
        self.adapter.editable_focus_lost(field)

        self.clock.advance(2_000, deliver_cancelled=True)
        self.assertEqual(self.adapter.state, VisibilityState.AUTO_VISIBLE)
        self.assertIsNone(self.adapter.focused_editable)
        self.assertIsNone(self.adapter.snapshot().editable_focus_id)

        self.adapter.set_voice_busy(False)
        self.clock.advance(379)
        self.assertEqual(self.adapter.state, VisibilityState.AUTO_VISIBLE)
        self.clock.advance(1)
        self.assertEqual(self.adapter.state, VisibilityState.HIDDEN_AUTO)
        self.assertIsNone(self.adapter.focused_editable)

    def test_voice_finish_rechecks_when_atspi_lost_the_event(self) -> None:
        field = FakeAccessible(":1.31", "/terminal/2")
        self.adapter.editable_focus_gained(field)
        self.adapter.set_voice_busy(True)
        field.valid = False

        self.clock.advance(2_000)
        self.assertEqual(self.adapter.state, VisibilityState.AUTO_VISIBLE)
        self.adapter.set_voice_busy(False)
        self.clock.advance(380)
        self.assertEqual(self.adapter.state, VisibilityState.HIDDEN_AUTO)

    def test_cross_application_event_invalidates_voice_target_while_busy(self) -> None:
        field = FakeAccessible(":1.31", "/terminal/voice")
        other_application = FakeAccessible(":1.41", "/document/1")
        self.adapter.editable_focus_gained(field)
        self.adapter.set_voice_busy(True)

        self.adapter.noneditable_focus_gained(other_application)

        self.assertIsNone(self.adapter.focused_editable)
        self.assertIsNone(self.adapter.snapshot().editable_focus_id)
        self.assertEqual(self.adapter.state, VisibilityState.AUTO_VISIBLE)

        # The first validation is consumed by the voice visibility hold. Once
        # voice ends with no target, a fresh bounded validation must hide it.
        self.clock.advance(380)
        self.adapter.set_voice_busy(False)
        self.clock.advance(379)
        self.assertEqual(self.adapter.state, VisibilityState.AUTO_VISIBLE)
        self.clock.advance(1)
        self.assertEqual(self.adapter.state, VisibilityState.HIDDEN_AUTO)

    def test_same_application_noneditable_focus_revokes_voice_target(self) -> None:
        field = FakeAccessible(":1.31", "/chromium/form/voice")
        document = FakeAccessible(":1.31", "/chromium/document/1")
        self.adapter.editable_focus_gained(field)
        self.adapter.set_voice_busy(True)

        # Chromium can leave the old field proxy apparently valid. The real
        # focused non-editable event must still revoke paste authority.
        self.adapter.noneditable_focus_gained(document)

        self.assertIsNone(self.adapter.focused_editable)
        self.assertIsNone(self.adapter.snapshot().editable_focus_id)
        self.assertEqual(self.adapter.state, VisibilityState.AUTO_VISIBLE)

    def test_watchdog_releases_focus_when_application_vanishes_silently(self) -> None:
        field = VanishingAccessible(":1.32", "/chromium/form/9")
        self.adapter.editable_focus_gained(field)
        self.clock.advance(900)
        field.valid = False
        field.vanished = True

        self.adapter.revalidate_focus()
        self.clock.advance(379)
        self.assertEqual(self.adapter.state, VisibilityState.AUTO_VISIBLE)
        self.clock.advance(1)
        self.assertEqual(self.adapter.state, VisibilityState.HIDDEN_AUTO)
        self.assertIsNone(self.adapter.focused_editable)

    def test_loss_from_exact_defunct_proxy_uses_captured_identity(self) -> None:
        field = VanishingAccessible(":1.34", "/chromium/form/11")
        self.adapter.editable_focus_gained(field)
        field.valid = False
        field.vanished = True

        self.adapter.editable_focus_lost(field)
        self.clock.advance(380)
        self.assertEqual(self.adapter.state, VisibilityState.HIDDEN_AUTO)

    def test_watchdog_preserves_voice_hold(self) -> None:
        field = FakeAccessible(":1.33", "/chromium/form/10")
        self.adapter.editable_focus_gained(field)
        self.adapter.set_voice_busy(True)
        field.valid = False

        self.adapter.revalidate_focus()
        self.clock.advance(2_000)
        self.assertEqual(self.adapter.state, VisibilityState.AUTO_VISIBLE)
        self.assertIs(self.adapter.focused_editable, field)


class GLibSchedulerTests(unittest.TestCase):
    def test_dispatch_is_one_shot(self) -> None:
        glib = FakeGLib()
        scheduler = GLibScheduler(glib)
        calls = []
        scheduler.call_later(900, lambda: calls.append("fired"))

        self.assertFalse(glib.fire(1))
        self.assertEqual(calls, ["fired"])

    def test_cancel_is_idempotent(self) -> None:
        glib = FakeGLib()
        scheduler = GLibScheduler(glib)
        handle = scheduler.call_later(380, lambda: None)

        handle.cancel()
        handle.cancel()
        self.assertEqual(glib.removed, [1])
        self.assertNotIn(1, glib.sources)


class RecorderProcessTests(unittest.TestCase):
    def test_already_exited_recorder_status_is_preserved(self) -> None:
        process = FakeRecorderProcess(returncode=7)
        self.assertEqual(stop_recorder_process(process, cancelled=False), 7)
        self.assertEqual(process.signals, [])

    def test_sigint_recorder_is_reaped_and_returns_success(self) -> None:
        process = FakeRecorderProcess()
        self.assertEqual(stop_recorder_process(process, cancelled=False), 0)
        self.assertEqual(len(process.signals), 1)
        self.assertFalse(process.killed)

    def test_unresponsive_recorder_is_killed_and_reported(self) -> None:
        process = FakeRecorderProcess(timeout_once=True)
        self.assertEqual(stop_recorder_process(process, cancelled=False), -9)
        self.assertTrue(process.killed)

    def test_even_post_kill_wait_has_a_finite_budget(self) -> None:
        process = FakeRecorderProcess(timeout_always=True)
        with self.assertRaisesRegex(RuntimeError, "after SIGKILL"):
            stop_recorder_process(process, cancelled=False)
        self.assertEqual(process.wait_calls, [3, 1.0])
        self.assertEqual(process.kill_calls, 1)


class RecorderStopControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeScheduler()
        self.reaped: list[RecorderStopResult] = []
        self.failures: list[RecorderStopFailure] = []
        self.controller = self.make_controller(self.clock)

    def make_controller(self, scheduler) -> RecorderStopController:
        return RecorderStopController(
            scheduler,
            poll_interval_ms=10,
            cancelled_grace_ms=30,
            finalize_grace_ms=50,
            kill_grace_ms=20,
            stuck_poll_ms=25,
            monotonic=lambda: scheduler.now_ms / 1_000,
        )

    @staticmethod
    def wait_until(predicate, *, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.005)
        if not predicate():
            raise AssertionError("background recorder ownership did not settle")

    def request(self, process, *, cancelled=False) -> None:
        self.controller.request(
            process,
            cancelled=cancelled,
            on_reaped=self.reaped.append,
            on_failed=self.failures.append,
        )

    def test_already_reaped_success_finishes_without_signal_or_wait(self) -> None:
        process = FakeRecorderProcess(returncode=0)
        self.request(process)

        self.assertFalse(self.controller.active)
        self.assertEqual(process.signals, [])
        self.assertEqual(process.wait_calls, [])
        self.assertEqual(
            self.reaped,
            [RecorderStopResult(returncode=0, forced=False, failure=None)],
        )
        self.assertTrue(self.reaped[0].eligible_for_recognition)

    def test_natural_nonzero_exit_is_reaped_but_never_asr_eligible(self) -> None:
        process = FakeRecorderProcess(returncode=7)
        self.request(process)

        self.assertFalse(self.reaped[0].eligible_for_recognition)
        self.assertEqual(self.reaped[0].returncode, 7)
        self.assertFalse(self.reaped[0].forced)

    def test_sigint_success_uses_only_nonblocking_poll_on_main_path(self) -> None:
        process = FakeRecorderProcess()
        self.request(process)
        self.assertEqual(len(process.signals), 1)
        process.returncode = 0
        self.clock.advance(10)

        self.assertFalse(self.controller.active)
        self.assertEqual(process.wait_calls, [])
        self.assertTrue(self.reaped[0].eligible_for_recognition)

    def test_sigint_eintr_exit_is_only_a_live_arecord_finalize_candidate(self) -> None:
        process = FakeRecorderProcess()
        self.request(process)
        process.returncode = 1
        self.clock.advance(10)

        self.assertFalse(self.reaped[0].eligible_for_recognition)
        self.assertTrue(self.reaped[0].sigint_sent)
        self.assertTrue(self.reaped[0].may_finalize_interrupted_arecord)

        natural = FakeRecorderProcess(returncode=1)
        self.request(natural)
        self.assertFalse(self.reaped[1].sigint_sent)
        self.assertFalse(self.reaped[1].may_finalize_interrupted_arecord)

    def test_grace_expiry_kills_once_and_forced_exit_is_ineligible(self) -> None:
        process = FakeRecorderProcess(exit_on_kill=True)
        self.request(process)
        self.clock.advance(60)

        self.assertEqual(process.kill_calls, 1)
        self.assertEqual(process.wait_calls, [])
        self.assertFalse(self.controller.active)
        self.assertTrue(self.reaped[0].forced)
        self.assertFalse(self.reaped[0].eligible_for_recognition)

    def test_post_kill_timeout_fails_closed_and_keeps_exclusive_owner(self) -> None:
        process = FakeRecorderProcess()
        self.request(process)
        self.clock.advance(70)

        self.assertTrue(self.controller.active)
        self.assertEqual(self.failures, [RecorderStopFailure.REAP_TIMEOUT])
        self.assertEqual(process.wait_calls, [])
        with self.assertRaisesRegex(RuntimeError, "still being reaped"):
            self.request(FakeRecorderProcess(returncode=0))

        process.returncode = -9
        self.clock.advance(25)
        self.assertFalse(self.controller.active)
        self.assertEqual(len(self.reaped), 1)
        self.assertEqual(self.reaped[0].failure, RecorderStopFailure.REAP_TIMEOUT)
        self.assertFalse(self.reaped[0].eligible_for_recognition)

    def test_late_timer_cannot_extend_frozen_sigint_or_kill_deadline(self) -> None:
        process = FakeRecorderProcess(exit_on_kill=True)
        self.request(process)
        _deadline, _sequence, _handle, callback = heapq.heappop(
            self.clock.events
        )
        self.clock.now_ms = 100
        callback()

        self.assertEqual(process.kill_calls, 1)
        self.assertEqual(self.failures, [RecorderStopFailure.REAP_TIMEOUT])
        self.clock.advance(25)
        self.assertFalse(self.controller.active)
        self.assertEqual(self.reaped[0].failure, RecorderStopFailure.REAP_TIMEOUT)

    def test_stale_timer_cannot_signal_or_finish_new_process(self) -> None:
        first = FakeRecorderProcess()
        self.request(first)
        _deadline, _sequence, _handle, stale_callback = heapq.heappop(
            self.clock.events
        )
        first.returncode = 0
        stale_callback()
        self.assertFalse(self.controller.active)

        second = FakeRecorderProcess()
        self.request(second)
        stale_callback()
        self.assertTrue(self.controller.active)
        self.assertEqual(second.kill_calls, 0)
        self.assertEqual(len(second.signals), 1)

        second.returncode = 0
        self.clock.advance(10)
        self.assertEqual(len(self.reaped), 2)

    def test_scheduler_failure_hands_child_to_background_owner(self) -> None:
        scheduler = FailingScheduler(fail_on_call=1)
        controller = self.make_controller(scheduler)
        process = FakeRecorderProcess(exit_on_kill=True)
        failures = []

        controller.request(
            process,
            cancelled=True,
            on_reaped=lambda _result: self.fail("fallback must not call GTK callback"),
            on_failed=failures.append,
        )

        self.wait_until(lambda: not controller.active)
        self.assertEqual(failures, [RecorderStopFailure.SCHEDULER_FAILED])
        self.assertGreaterEqual(process.kill_calls, 1)

    def test_throwing_failure_callback_does_not_lose_or_duplicate_child(self) -> None:
        process = FakeRecorderProcess()

        def fail_callback(_reason) -> None:
            raise RuntimeError("synthetic callback failure")

        self.controller.request(
            process,
            cancelled=False,
            on_reaped=self.reaped.append,
            on_failed=fail_callback,
        )
        self.clock.advance(70)
        self.assertTrue(self.controller.active)
        process.returncode = -9
        self.clock.advance(25)

        self.assertFalse(self.controller.active)
        self.assertEqual(len(self.reaped), 1)
        self.assertEqual(self.reaped[0].failure, RecorderStopFailure.REAP_TIMEOUT)

    def test_throwing_completion_callback_runs_after_ownership_is_cleared(self) -> None:
        callback_saw_active = []

        def reaped_callback(_result) -> None:
            callback_saw_active.append(self.controller.active)
            raise RuntimeError("synthetic completion failure")

        self.controller.request(
            FakeRecorderProcess(returncode=0),
            cancelled=False,
            on_reaped=reaped_callback,
            on_failed=self.failures.append,
        )

        self.assertEqual(callback_saw_active, [False])
        self.assertFalse(self.controller.active)

    def test_signal_and_poll_failures_are_ineligible_but_still_owned(self) -> None:
        signal_process = FakeRecorderProcess(
            signal_error=True,
            exit_on_kill=True,
        )
        self.request(signal_process)
        self.clock.advance(10)
        self.assertEqual(self.failures, [RecorderStopFailure.SIGNAL_FAILED])
        self.assertFalse(self.reaped[0].eligible_for_recognition)

        poll_process = FakeRecorderProcess(
            poll_error=True,
            exit_on_kill=True,
        )
        self.request(poll_process)
        self.assertTrue(self.controller.active)
        poll_process.poll_error = False
        self.clock.advance(10)
        self.assertFalse(self.controller.active)
        self.assertEqual(self.failures[-1], RecorderStopFailure.POLL_FAILED)
        self.assertFalse(self.reaped[-1].eligible_for_recognition)

    def test_scheduler_and_failure_callback_exceptions_still_reap(self) -> None:
        scheduler = FailingScheduler(fail_on_call=1)
        controller = self.make_controller(scheduler)
        process = FakeRecorderProcess(exit_on_kill=True)

        controller.request(
            process,
            cancelled=True,
            on_reaped=lambda _result: None,
            on_failed=lambda _reason: (_ for _ in ()).throw(
                RuntimeError("synthetic failure callback")
            ),
        )

        self.wait_until(lambda: not controller.active)
        self.assertGreaterEqual(process.kill_calls, 1)

    def test_transient_fallback_thread_start_failure_keeps_retriable_owner(self) -> None:
        scheduler = FailingScheduler(fail_on_call=1)
        controller = self.make_controller(scheduler)
        process = FakeRecorderProcess(exit_on_kill=True)
        failures = []

        with mock.patch.object(
            keyboard_adapter.threading,
            "Thread",
            side_effect=RuntimeError("synthetic thread start failure"),
        ):
            controller.request(
                process,
                cancelled=True,
                on_reaped=lambda _result: None,
                on_failed=failures.append,
            )

        self.assertTrue(controller.active)
        self.assertEqual(failures, [RecorderStopFailure.SCHEDULER_FAILED])
        controller.shutdown()
        self.wait_until(lambda: not controller.active)
        self.assertGreaterEqual(process.kill_calls, 1)

    @unittest.skipUnless(os.name == "posix", "signal fixture requires POSIX")
    def test_real_sigint_ignoring_child_never_blocks_main_path(self) -> None:
        process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-c",
                (
                    "import signal,time; "
                    "signal.signal(signal.SIGINT, signal.SIG_IGN); "
                    "print('ready', flush=True); "
                    "time.sleep(30)"
                ),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            assert process.stdout is not None
            self.assertEqual(process.stdout.readline().strip(), "ready")
            started = time.monotonic()
            self.request(process)
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 0.25)
            self.clock.advance(50)
            deadline = time.monotonic() + 1.0
            while self.controller.active and time.monotonic() < deadline:
                time.sleep(0.005)
                self.clock.advance(10)

            self.assertFalse(self.controller.active)
            self.assertIsNotNone(process.poll())
            self.assertTrue(self.reaped[0].forced)
            self.assertFalse(self.reaped[0].eligible_for_recognition)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            if process.stdout is not None:
                process.stdout.close()


class CaptureStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeScheduler()
        self.processes = []
        self.payload_ready = False
        self.probe_error = False
        self.cleanup_error = False
        self.cleanup_calls = 0
        self.stop_calls = []
        self.ready_processes = []
        self.failures = []

        def spawn():
            process = FakeRecorderProcess()
            self.processes.append(process)
            return process

        def stop(process, cancelled):
            self.stop_calls.append((process, cancelled))
            if process.returncode is None:
                process.returncode = -9
            return process.returncode

        def probe():
            if self.probe_error:
                raise VoiceArtifactError("unsafe fixture")
            return self.payload_ready

        def cleanup():
            self.cleanup_calls += 1
            if self.cleanup_error:
                raise VoiceArtifactError("synthetic cleanup refusal")
            self.payload_ready = False

        self.spawn = spawn
        self.controller = CaptureStartupController(
            self.clock,
            stop,
            probe,
            cleanup,
            self.ready_processes.append,
            self.failures.append,
            poll_interval_ms=10,
            attempt_timeout_ms=100,
            monotonic=lambda: self.clock.now_ms / 1_000,
        )

    def test_first_pcm_poll_marks_ready_and_relinquishes_process(self) -> None:
        self.assertTrue(self.controller.start(self.spawn))
        self.payload_ready = True
        self.clock.advance(9)
        self.assertEqual(self.ready_processes, [])
        self.clock.advance(1)

        self.assertEqual(self.ready_processes, [self.processes[0]])
        self.assertEqual(self.controller.attempt_count, 1)
        self.assertFalse(self.controller.active)
        self.assertEqual(self.stop_calls, [])
        self.assertEqual(self.failures, [])

    def test_nonzero_without_pcm_is_terminal_and_never_retries(self) -> None:
        self.controller.start(self.spawn)
        self.processes[0].returncode = 110
        self.clock.advance(10)

        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.ready_processes, [])
        self.assertEqual(self.controller.attempt_count, 1)
        self.assertEqual(self.failures, [CaptureStartupFailure.RECORDER_EXITED])

    def test_live_process_without_pcm_times_out_and_is_stopped(self) -> None:
        self.controller.start(self.spawn)
        self.clock.advance(100)

        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.ready_processes, [])
        self.assertEqual(self.stop_calls, [(self.processes[0], True)])
        self.assertEqual(
            self.failures,
            [CaptureStartupFailure.CONNECT_TIMEOUT],
        )
        self.assertFalse(self.controller.active)

    def test_zero_exit_without_pcm_does_not_retry(self) -> None:
        self.controller.start(self.spawn)
        self.processes[0].returncode = 0
        self.clock.advance(10)

        self.assertEqual(len(self.processes), 1)
        self.assertEqual(
            self.failures,
            [CaptureStartupFailure.RECORDER_EXITED],
        )

    def test_nonzero_after_pcm_does_not_retry_or_upload(self) -> None:
        self.controller.start(self.spawn)
        self.payload_ready = True
        self.processes[0].returncode = 110
        self.clock.advance(10)

        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.ready_processes, [])
        self.assertEqual(
            self.failures,
            [CaptureStartupFailure.RECORDER_EXITED],
        )

    def test_live_process_without_pcm_is_not_reported_ready_early(self) -> None:
        self.controller.start(self.spawn)
        self.clock.advance(10)

        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.ready_processes, [])
        self.assertEqual(self.stop_calls, [])
        self.assertEqual(self.failures, [])
        self.assertTrue(self.controller.active)

    def test_cancel_stops_and_reaps_before_stale_poll_can_run(self) -> None:
        self.controller.start(self.spawn)
        self.controller.cancel()
        self.clock.advance(1_000, deliver_cancelled=True)

        self.assertEqual(self.stop_calls, [(self.processes[0], True)])
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.ready_processes, [])
        self.assertEqual(self.failures, [])
        self.assertFalse(self.controller.active)

    def test_stale_cancelled_poll_cannot_clear_new_session_timer(self) -> None:
        self.controller.start(self.spawn)
        first = self.processes[0]
        self.controller.cancel()
        self.controller.start(self.spawn)
        self.payload_ready = True
        self.clock.advance(10, deliver_cancelled=True)

        self.assertEqual(self.stop_calls, [(first, True)])
        self.assertEqual(self.ready_processes, [self.processes[1]])
        self.assertEqual(self.failures, [])

    def test_cancel_before_pcm_timeout_prevents_late_failure(self) -> None:
        self.controller.start(self.spawn)
        self.clock.advance(10)
        self.controller.cancel()
        self.clock.advance(1_000, deliver_cancelled=True)

        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.ready_processes, [])
        self.assertEqual(self.failures, [])

    def test_unsafe_live_artifact_is_terminal_without_retry(self) -> None:
        self.controller.start(self.spawn)
        self.probe_error = True
        self.clock.advance(10)

        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.stop_calls, [(self.processes[0], True)])
        self.assertEqual(
            self.failures,
            [CaptureStartupFailure.INVALID_ARTIFACT],
        )

    def test_initial_cleanup_refusal_never_spawns(self) -> None:
        self.cleanup_error = True
        self.assertFalse(self.controller.start(self.spawn))

        self.assertEqual(self.processes, [])
        self.assertEqual(
            self.failures,
            [CaptureStartupFailure.CLEANUP_FAILED],
        )

    def test_timeout_cleanup_refusal_is_reported(self) -> None:
        self.controller.start(self.spawn)
        self.cleanup_error = True
        self.clock.advance(100)

        self.assertEqual(len(self.processes), 1)
        self.assertEqual(
            self.failures,
            [CaptureStartupFailure.CLEANUP_FAILED],
        )

    def test_spawn_error_is_terminal_without_retry(self) -> None:
        def fail_spawn():
            raise OSError("synthetic parecord absence")

        self.assertFalse(self.controller.start(fail_spawn))
        self.clock.advance(1_000, deliver_cancelled=True)

        self.assertEqual(self.controller.attempt_count, 1)
        self.assertEqual(self.processes, [])
        self.assertEqual(
            self.failures,
            [CaptureStartupFailure.SPAWN_FAILED],
        )

    def test_late_main_loop_delivery_cannot_extend_startup_bound(self) -> None:
        self.controller.start(self.spawn)
        self.processes[0].returncode = 110
        _deadline, _sequence, _handle, callback = heapq.heappop(
            self.clock.events
        )
        self.clock.now_ms = 1_000
        callback()

        self.assertEqual(len(self.processes), 1)
        self.assertEqual(
            self.failures,
            [CaptureStartupFailure.RECORDER_EXITED],
        )

    def test_scheduler_failure_after_spawn_stops_owned_child(self) -> None:
        scheduler = FailingScheduler(fail_on_call=1)
        process = FakeRecorderProcess()
        stops = []
        failures = []

        def stop(owned, cancelled):
            stops.append((owned, cancelled))
            owned.returncode = -9

        controller = CaptureStartupController(
            scheduler,
            stop,
            lambda: False,
            lambda: None,
            lambda _process: None,
            failures.append,
            poll_interval_ms=10,
            attempt_timeout_ms=100,
            monotonic=lambda: scheduler.now_ms / 1_000,
        )

        self.assertFalse(controller.start(lambda: process))
        self.assertEqual(stops, [(process, True)])
        self.assertEqual(failures, [CaptureStartupFailure.SCHEDULER_FAILED])
        self.assertFalse(controller.active)

    def test_scheduler_failure_during_pcm_poll_never_spawns_again(self) -> None:
        scheduler = FailingScheduler(fail_on_call=2)
        process = FakeRecorderProcess()
        failures = []
        spawns = []

        def spawn():
            spawns.append(process)
            return process

        controller = CaptureStartupController(
            scheduler,
            lambda _process, _cancelled: None,
            lambda: False,
            lambda: None,
            lambda _process: None,
            failures.append,
            poll_interval_ms=10,
            attempt_timeout_ms=100,
            monotonic=lambda: scheduler.now_ms / 1_000,
        )
        controller.start(spawn)
        scheduler.advance(10)

        self.assertEqual(spawns, [process])
        self.assertEqual(failures, [CaptureStartupFailure.SCHEDULER_FAILED])
        self.assertFalse(controller.active)

    def test_ready_callback_exception_stops_before_reporting_failure(self) -> None:
        process = FakeRecorderProcess()
        stops = []
        failures = []

        def on_ready(_process):
            raise RuntimeError("synthetic ready callback failure")

        controller = CaptureStartupController(
            self.clock,
            lambda owned, cancelled: stops.append((owned, cancelled)),
            lambda: True,
            lambda: None,
            on_ready,
            failures.append,
            poll_interval_ms=10,
            attempt_timeout_ms=100,
            monotonic=lambda: self.clock.now_ms / 1_000,
        )
        controller.start(lambda: process)
        self.clock.advance(10)

        self.assertEqual(stops, [(process, True)])
        self.assertEqual(failures, [CaptureStartupFailure.CALLBACK_FAILED])
        self.assertFalse(controller.active)

    def test_failed_callback_exception_does_not_restore_false_ownership(self) -> None:
        process = FakeRecorderProcess()
        stops = []

        def on_failed(_reason):
            raise RuntimeError("synthetic failed callback failure")

        def unsafe_probe():
            raise VoiceArtifactError("synthetic unsafe artifact")

        controller = CaptureStartupController(
            self.clock,
            lambda owned, cancelled: stops.append((owned, cancelled)),
            unsafe_probe,
            lambda: None,
            lambda _process: None,
            on_failed,
            poll_interval_ms=10,
            attempt_timeout_ms=20,
            monotonic=lambda: self.clock.now_ms / 1_000,
        )
        controller.start(lambda: process)
        self.clock.advance(10)

        self.assertEqual(stops, [(process, True)])
        self.assertFalse(controller.active)

    def test_stop_handoff_exception_is_owned_until_emergency_reap_finishes(self) -> None:
        process = FakeRecorderProcess()
        failures = []

        def stop_refuses(_process, _cancelled):
            raise RuntimeError("synthetic ownership handoff refusal")

        controller = CaptureStartupController(
            self.clock,
            stop_refuses,
            lambda: True,
            lambda: None,
            lambda _process: (_ for _ in ()).throw(
                RuntimeError("synthetic ready callback failure")
            ),
            failures.append,
            poll_interval_ms=10,
            attempt_timeout_ms=100,
            monotonic=lambda: self.clock.now_ms / 1_000,
        )
        controller.start(lambda: process)
        self.clock.advance(10)

        deadline = time.monotonic() + 1.0
        while controller.active and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertFalse(controller.active)
        self.assertEqual(failures, [CaptureStartupFailure.CALLBACK_FAILED])
        self.assertGreaterEqual(len(process.signals), 1)
        self.assertGreaterEqual(len(process.wait_calls), 1)

    def test_emergency_thread_start_failure_retains_primary_owner_and_reports(self) -> None:
        process = FakeRecorderProcess()
        failures = []
        cleanup_calls = []

        def stop_refuses(_process, _cancelled):
            raise RuntimeError("synthetic ownership handoff refusal")

        def unsafe_probe():
            raise VoiceArtifactError("synthetic unsafe artifact")

        controller = CaptureStartupController(
            self.clock,
            stop_refuses,
            unsafe_probe,
            lambda: cleanup_calls.append(True),
            lambda _process: None,
            failures.append,
            poll_interval_ms=10,
            attempt_timeout_ms=20,
            monotonic=lambda: self.clock.now_ms / 1_000,
        )
        with mock.patch.object(
            keyboard_adapter.threading,
            "Thread",
            side_effect=RuntimeError("synthetic thread start failure"),
        ):
            controller.start(lambda: process)
            self.clock.advance(10)

        self.assertTrue(controller.active)
        self.assertEqual(failures, [CaptureStartupFailure.INVALID_ARTIFACT])
        self.assertGreaterEqual(len(cleanup_calls), 2)

        controller.cancel()
        deadline = time.monotonic() + 1.0
        while controller.active and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertFalse(controller.active)
        self.assertGreaterEqual(len(process.wait_calls), 1)


class RuntimeVoiceActivationTests(unittest.TestCase):
    class StartupOwner:
        def __init__(self, process) -> None:
            self.active = False
            self.process = process
            self.start_calls = 0
            self.cancel_calls = 0

        def start(self, spawn) -> bool:
            self.start_calls += 1
            self.active = True
            self.process = spawn()
            return True

        def cancel(self) -> None:
            self.cancel_calls += 1
            self.active = False
            self.process = None

    class StopOwner:
        def __init__(self) -> None:
            self.active = False
            self.process = None
            self.requests = []
            self.on_reaped = None

        def request(self, process, **kwargs) -> None:
            self.active = True
            self.process = process
            self.requests.append((process, kwargs["cancelled"]))
            self.on_reaped = kwargs["on_reaped"]

    class HiddenWindow:
        def __init__(self) -> None:
            self.hide_calls = 0

        def hide(self) -> None:
            self.hide_calls += 1

    class RuntimeGLib:
        def __init__(self) -> None:
            self.next_id = 1
            self.sources = {}
            self.removed = []

        def timeout_add(self, _delay_ms, callback, *args):
            source_id = self.next_id
            self.next_id += 1
            self.sources[source_id] = lambda: callback(*args)
            return source_id

        def source_remove(self, source_id):
            self.removed.append(source_id)
            self.sources.pop(source_id, None)
            return True

    class RecognitionThread:
        instances = []

        def __init__(self, *, target, args, daemon) -> None:
            self.target = target
            self.args = args
            self.daemon = daemon
            self.start_calls = 0
            self.__class__.instances.append(self)

        def start(self) -> None:
            self.start_calls += 1

    def make_app(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        target = (23, ("atspi", ":1.90", "/field/voice"))
        process = FakeRecorderProcess()
        app.voice = runtime.VoiceSessionController()
        app.last_ignored_voice_activation = None
        app.voice_thread = None
        app.voice_process = None
        app.voice_timeout = 0
        app.voice_retry_not_before = 0.0
        app.custom_voice_enabled = True
        app.capture_startup = self.StartupOwner(process)
        app.recorder_stop = types.SimpleNamespace(active=False)
        app.current_voice_target = lambda: (target, True)
        app.start_microphone_session = lambda generation: app.on_microphone_selected(generation, "physical-mic")
        app.spawn_voice_recorder = lambda _kind, _source: process
        app.flash_mic_error = lambda message: self.fail(message)
        app.cleanup_voice_artifact = lambda: None
        return runtime, app, target, process

    def test_capture_cooldown_blocks_repeated_audio_graph_open(self) -> None:
        runtime, app, _target, _process = self.make_app()
        messages = []
        app.flash_mic_error = messages.append
        app.voice_retry_not_before = 108.0

        with mock.patch.object(runtime.time, "monotonic", return_value=100.0):
            app.start_recording()

        self.assertEqual(app.capture_startup.start_calls, 0)
        self.assertEqual(app.voice.state, runtime.VoiceState.IDLE)
        self.assertEqual(messages, ["麦克风恢复中；请在 9 秒后重试"])

    def test_capture_failure_starts_bounded_retry_cooldown(self) -> None:
        runtime, app, target, _process = self.make_app()
        generation = app.voice.begin_connecting(target)

        with mock.patch.object(runtime.time, "monotonic", return_value=50.0):
            app.on_capture_failed(runtime.CaptureStartupFailure.CONNECT_TIMEOUT)

        self.assertEqual(app.voice.state, runtime.VoiceState.ERROR)
        self.assertEqual(app.voice.snapshot().generation, generation)
        self.assertEqual(
            app.voice_retry_not_before,
            50.0 + runtime.CAPTURE_RETRY_COOLDOWN_S,
        )

    def test_repeated_activation_is_idempotent_across_every_busy_state(self) -> None:
        runtime, app, target, process = self.make_app()
        stop_owner = self.StopOwner()
        logged = []

        with mock.patch("builtins.print") as printed:
            app.toggle_voice(None)
            connecting = app.voice.snapshot()
            startup_process = app.capture_startup.process

            for _index in range(5):
                app.toggle_voice(None)

            self.assertEqual(app.voice.snapshot(), connecting)
            self.assertEqual(app.capture_startup.start_calls, 1)
            self.assertEqual(app.capture_startup.cancel_calls, 0)
            self.assertIs(app.capture_startup.process, startup_process)
            self.assertIs(startup_process, process)
            self.assertIsNone(app.voice_process)

            self.assertTrue(app.voice.capture_ready(connecting.generation))
            app.capture_startup.active = False
            app.capture_startup.process = None
            app.voice_process = process
            app.recorder_stop = stop_owner
            app.cancel_voice_timeout = lambda: None

            def recognize_after_reap(result, generation):
                logged.append((result, generation))
                app.voice.begin_recognition(
                    generation,
                    target,
                    target_is_valid=True,
                )

            app.on_recording_reaped = recognize_after_reap

            # RECORDING is the sole busy state where another activation acts:
            # it starts exactly one orderly recorder-finalization handoff.
            app.toggle_voice(None)
            stopping = app.voice.snapshot()
            self.assertEqual(stopping.state, runtime.VoiceState.STOPPING)
            self.assertEqual(stopping.generation, connecting.generation)
            self.assertEqual(stop_owner.requests, [(process, False)])
            self.assertIs(stop_owner.process, process)
            self.assertIsNone(app.voice_process)

            for _index in range(5):
                app.toggle_voice(None)

            self.assertEqual(app.voice.snapshot(), stopping)
            self.assertEqual(stop_owner.requests, [(process, False)])
            self.assertIs(stop_owner.process, process)

            result = RecorderStopResult(
                returncode=0,
                forced=False,
                failure=None,
            )
            stop_owner.on_reaped(result)
            self.assertEqual(logged, [(result, connecting.generation)])
            self.assertEqual(app.voice.state, runtime.VoiceState.RECOGNIZING)

            for _index in range(5):
                app.toggle_voice(None)
            self.assertEqual(app.voice.state, runtime.VoiceState.RECOGNIZING)

            self.assertTrue(
                app.voice.prepare_paste(
                    connecting.generation,
                    target,
                    target_is_valid=True,
                )
            )
            pending = app.voice.snapshot()
            for _index in range(5):
                app.toggle_voice(None)
            self.assertEqual(app.voice.snapshot(), pending)

        self.assertEqual(
            printed.call_args_list,
            [
                mock.call(
                    "voice: activation ignored while connecting",
                    flush=True,
                ),
                mock.call(
                    "voice: activation ignored while stopping",
                    flush=True,
                ),
                mock.call(
                    "voice: activation ignored while recognizing",
                    flush=True,
                ),
                mock.call(
                    "voice: activation ignored while pending-paste",
                    flush=True,
                ),
            ],
        )
        self.assertEqual(app.capture_startup.start_calls, 1)
        self.assertEqual(app.capture_startup.cancel_calls, 0)

    def test_stopping_repeated_activation_preserves_real_reap_chain(self) -> None:
        runtime, app, target, process = self.make_app()
        scheduler = FakeScheduler()
        stop_owner = RecorderStopController(
            scheduler,
            poll_interval_ms=10,
            finalize_grace_ms=100,
            kill_grace_ms=100,
            monotonic=lambda: scheduler.now_ms / 1_000,
        )
        app.recorder_stop = stop_owner
        app.cancel_voice_timeout = lambda: None
        generation = app.voice.begin_recording(target)
        app.voice_process = process
        self.RecognitionThread.instances = []

        self.assertIs(
            app.on_recording_reaped.__func__,
            runtime.PocketDSKeyboard.on_recording_reaped,
        )
        with (
            mock.patch.object(
                stop_owner,
                "request",
                wraps=stop_owner.request,
            ) as requested,
            mock.patch.object(
                runtime.threading,
                "Thread",
                self.RecognitionThread,
            ),
            mock.patch("builtins.print"),
        ):
            app.toggle_voice(None)
            stopping = app.voice.snapshot()
            owner_generation = stop_owner._generation
            owner_process = stop_owner._process
            owner_request = stop_owner._on_reaped
            owner_handle = stop_owner._handle
            recorder_signals = list(process.signals)

            for _index in range(5):
                app.toggle_voice(None)

            self.assertEqual(app.voice.snapshot(), stopping)
            self.assertEqual(stopping.state, runtime.VoiceState.STOPPING)
            self.assertEqual(stopping.generation, generation)
            self.assertEqual(stopping.target_id, target)
            self.assertEqual(requested.call_count, 1)
            self.assertTrue(stop_owner.active)
            self.assertEqual(stop_owner._generation, owner_generation)
            self.assertIs(stop_owner._process, owner_process)
            self.assertIs(owner_process, process)
            self.assertIs(stop_owner._on_reaped, owner_request)
            self.assertIs(stop_owner._handle, owner_handle)
            self.assertEqual(process.signals, recorder_signals)
            self.assertEqual(process.signals, [signal.SIGINT])
            self.assertIsNone(app.voice_process)
            self.assertEqual(self.RecognitionThread.instances, [])

            process.returncode = 0
            scheduler.advance(10)

            recognizing = app.voice.snapshot()
            self.assertFalse(stop_owner.active)
            self.assertIsNone(stop_owner._process)
            self.assertIsNone(stop_owner._on_reaped)
            self.assertIsNone(stop_owner._handle)
            self.assertEqual(recognizing.state, runtime.VoiceState.RECOGNIZING)
            self.assertEqual(recognizing.generation, generation)
            self.assertEqual(recognizing.target_id, target)
            self.assertEqual(len(self.RecognitionThread.instances), 1)
            recognition_thread = self.RecognitionThread.instances[0]
            self.assertEqual(recognition_thread.args, (generation,))
            self.assertTrue(recognition_thread.daemon)
            self.assertEqual(recognition_thread.start_calls, 1)
            self.assertIs(app.voice_thread, recognition_thread)
            self.assertIs(
                recognition_thread.target.__func__,
                runtime.PocketDSKeyboard.recognize_voice,
            )

            for _index in range(5):
                app.toggle_voice(None)

            self.assertEqual(app.voice.snapshot(), recognizing)
            self.assertEqual(requested.call_count, 1)
            self.assertEqual(len(self.RecognitionThread.instances), 1)
            self.assertEqual(recognition_thread.start_calls, 1)

    def test_connecting_log_dedupe_is_scoped_to_generation(self) -> None:
        runtime, app, target, _process = self.make_app()

        with mock.patch("builtins.print") as printed:
            app.toggle_voice(None)
            first = app.voice.snapshot()
            for _index in range(5):
                app.toggle_voice(None)

            self.assertTrue(app.voice.fail(first.generation))
            app.capture_startup.active = False
            app.capture_startup.process = None

            app.toggle_voice(None)
            second = app.voice.snapshot()
            for _index in range(5):
                app.toggle_voice(None)

        self.assertEqual(first.state, runtime.VoiceState.CONNECTING)
        self.assertEqual(second.state, runtime.VoiceState.CONNECTING)
        self.assertEqual(second.generation, first.generation + 1)
        self.assertEqual(first.target_id, target)
        self.assertEqual(second.target_id, target)
        self.assertEqual(app.capture_startup.start_calls, 2)
        self.assertEqual(app.capture_startup.cancel_calls, 0)
        self.assertEqual(
            printed.call_args_list,
            [
                mock.call(
                    "voice: activation ignored while connecting",
                    flush=True,
                ),
                mock.call(
                    "voice: activation ignored while connecting",
                    flush=True,
                ),
            ],
        )
        self.assertEqual(
            app.last_ignored_voice_activation,
            (second.generation, runtime.VoiceState.CONNECTING),
        )

    def test_terminal_activation_starts_new_session_and_stale_timer_is_inert(
        self,
    ) -> None:
        terminal_states = (
            VoiceState.IDLE,
            VoiceState.ERROR,
            VoiceState.SUCCESS,
            VoiceState.DISCARDED,
        )

        for terminal_state in terminal_states:
            with self.subTest(terminal_state=terminal_state.value):
                runtime, app, target, _process = self.make_app()
                glib = self.RuntimeGLib()
                rendered = []
                busy = []
                runtime.GLib = glib
                app.voice_feedback_source = 0
                app.visibility = types.SimpleNamespace(
                    set_voice_busy=busy.append,
                )
                app.render_voice_snapshot = rendered.append
                app.voice = runtime.VoiceSessionController(
                    app.on_voice_state_changed,
                )

                if terminal_state is VoiceState.ERROR:
                    generation = app.voice.begin_connecting(target)
                    self.assertTrue(app.voice.fail(generation))
                elif terminal_state is VoiceState.SUCCESS:
                    generation = app.voice.begin_recording(target)
                    self.assertTrue(
                        app.voice.begin_stopping(
                            generation,
                            target,
                            target_is_valid=True,
                        )
                    )
                    self.assertTrue(
                        app.voice.begin_recognition(
                            generation,
                            target,
                            target_is_valid=True,
                        )
                    )
                    self.assertTrue(
                        app.voice.prepare_paste(
                            generation,
                            target,
                            target_is_valid=True,
                        )
                    )
                    self.assertTrue(
                        app.voice.consume_paste(
                            generation,
                            target,
                            target_is_valid=True,
                        )
                    )
                elif terminal_state is VoiceState.DISCARDED:
                    generation = app.voice.begin_recording(target)
                    self.assertFalse(
                        app.voice.begin_stopping(
                            generation,
                            (target[0] + 1, target[1]),
                            target_is_valid=True,
                        )
                    )

                terminal = app.voice.snapshot()
                self.assertEqual(terminal.state, terminal_state)
                old_feedback_source = app.voice_feedback_source
                old_feedback = None
                if terminal_state is not VoiceState.IDLE:
                    self.assertGreater(old_feedback_source, 0)
                    old_feedback = glib.sources[old_feedback_source]
                else:
                    self.assertEqual(old_feedback_source, 0)

                app.toggle_voice(None)
                connecting = app.voice.snapshot()

                self.assertEqual(connecting.state, VoiceState.CONNECTING)
                self.assertEqual(connecting.generation, terminal.generation + 1)
                self.assertEqual(connecting.target_id, target)
                self.assertEqual(app.capture_startup.start_calls, 1)
                self.assertEqual(app.capture_startup.cancel_calls, 0)
                if old_feedback is not None:
                    self.assertIn(old_feedback_source, glib.removed)
                    self.assertNotIn(old_feedback_source, glib.sources)
                    rendered_before_stale_timer = list(rendered)
                    busy_before_stale_timer = list(busy)
                    self.assertFalse(old_feedback())
                    self.assertEqual(app.voice.snapshot(), connecting)
                    self.assertEqual(rendered, rendered_before_stale_timer)
                    self.assertEqual(busy, busy_before_stale_timer)

    def test_hide_and_target_change_still_cancel_connecting_owner(self) -> None:
        _runtime, app, _target, _process = self.make_app()
        app.toggle_voice(None)
        before_hide = app.voice.snapshot()
        app.window = self.HiddenWindow()
        app.cancel_backspace_repeat = lambda: None
        app.cancel_space_hold = lambda: None

        with mock.patch("builtins.print"):
            app.apply_visibility_snapshot(types.SimpleNamespace(visible=False))

        self.assertFalse(app.voice.snapshot().busy)
        self.assertGreater(app.voice.snapshot().generation, before_hide.generation)
        self.assertEqual(app.capture_startup.cancel_calls, 1)
        self.assertIsNone(app.capture_startup.process)
        self.assertEqual(app.window.hide_calls, 1)

        _runtime, app, target, _process = self.make_app()
        app.toggle_voice(None)
        before_target_change = app.voice.snapshot()
        app.current_voice_target = lambda: (
            (target[0] + 1, target[1]),
            True,
        )

        with mock.patch("builtins.print"):
            self.assertTrue(app.cancel_voice_if_target_changed("target changed"))

        self.assertFalse(app.voice.snapshot().busy)
        self.assertGreater(
            app.voice.snapshot().generation,
            before_target_change.generation,
        )
        self.assertEqual(app.capture_startup.cancel_calls, 1)
        self.assertIsNone(app.capture_startup.process)


class RuntimeMicrophoneRoutingTests(unittest.TestCase):
    BLUETOOTH = "bluez_input.11:22:33:44:55:66"
    USB = "alsa_input.usb-USB_Audio-00.mono-fallback"

    def source_result(self, name, **changes):
        entry = {"name": name, "state": "SUSPENDED", "monitor_source": "",
                 "description": None, "properties": {"device.class": "sound"},
                 "mute": False, "volume": {"mono": {"value": 65536}}}
        entry.update(changes)
        return entry

    def select(self, runtime, source, entries, *, returncode=0):
        app = object.__new__(runtime.PocketDSKeyboard)
        responses = [
            (0, (source + "\n").encode()),
            (returncode, json.dumps(entries).encode()),
        ]
        with mock.patch.object(runtime, "run_bounded_command", side_effect=responses) as run:
            source, problem = app.microphone_source_status()
        return source, run, problem

    def test_present_internal_bluetooth_and_usb_sources_are_resolved_once(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        for source in (runtime.INTERNAL_MICROPHONE_SOURCE, self.BLUETOOTH, self.USB):
            with self.subTest(source=source):
                result, run, problem = self.select(runtime, source, [self.source_result(source)])
                self.assertEqual(result, source)
                self.assertIsNone(problem)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args_list[0].args[0], ["/usr/bin/env", "LC_ALL=C.UTF-8", "pactl", "get-default-source"])
                self.assertEqual(run.call_args_list[1].args[0],
                                 ["/usr/bin/env", "LC_ALL=C.UTF-8", "pactl", "--format=json", "list", "sources"])
                for call in run.call_args_list:
                    self.assertEqual(call.kwargs["timeout"], 2)
                    self.assertEqual(call.kwargs["maximum"], 262_144)
                    self.assertTrue(callable(call.kwargs["cancelled"]))

    def test_missing_duplicate_monitor_and_invalid_source_metadata_fail_closed(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        source = self.BLUETOOTH
        valid = self.source_result(source)
        cases = [[], [valid, valid], {}, [None], [{"name": source}],
                 [self.source_result(source, monitor_source="speaker")],
                 [self.source_result(source, monitor_of_sink=17)],
                 [self.source_result(source, monitor_of_sink_name="speaker")],
                 [self.source_result(runtime.INTERNAL_MICROPHONE_SOURCE)]]
        for entries in cases:
            with self.subTest(entries=entries):
                self.assertIsNone(self.select(runtime, source, entries)[0])
        self.assertIsNone(self.select(runtime, source, [valid], returncode=1)[0])
        for source in ("speaker.monitor", "auto_null", "", "@DEFAULT_SOURCE@"):
            with self.subTest(source=source):
                result, run, _problem = self.select(runtime, source, [])
                self.assertIsNone(result)
                self.assertEqual(run.call_count, 1)

    def test_alternate_null_monitor_schema_is_accepted_but_contradictions_are_not(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        entry = {"name": self.USB, "monitor_of_sink": None, "mute": False,
                 "volume": {"mono": {"value": 65536}}}
        self.assertEqual(self.select(runtime, self.USB, [entry])[0], self.USB)
        entry["monitor_source"] = "speaker"
        self.assertIsNone(self.select(runtime, self.USB, [entry])[0])

    def test_source_query_failures_do_not_select_another_device(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        first = (0, self.BLUETOOTH.encode())
        for failure in (OSError("missing pactl"), runtime.VoiceArtifactError("timeout"),
                        (0, b"invalid-json")):
            with self.subTest(failure=failure):
                with mock.patch.object(runtime, "run_bounded_command", side_effect=[first, failure]) as run:
                    self.assertIsNone(app.microphone_source_status()[0])
                    self.assertEqual(run.call_count, 2)

    def test_muted_zero_volume_and_unknown_permission_cannot_start_capture(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        for source in (runtime.INTERNAL_MICROPHONE_SOURCE, self.BLUETOOTH):
            for changes, expected in (
                ({"mute": True}, "麦克风已静音"),
                ({"volume": {"left": {"value": 0}, "right": {"value": 0}}}, "麦克风音量为零"),
                ({"mute": None}, "麦克风状态检查失败"),
                ({"mute": "false"}, "麦克风状态检查失败"),
                ({"volume": {}}, "麦克风状态检查失败"),
                ({"volume": {"mono": {"value": True}}}, "麦克风状态检查失败"),
                ({"volume": {"mono": {"value": -1}}}, "麦克风状态检查失败"),
            ):
                with self.subTest(source=source, changes=changes):
                    selected, _run, problem = self.select(runtime, source, [self.source_result(source, **changes)])
                    self.assertIsNone(selected)
                    self.assertEqual(problem, expected)

    def test_monitor_queries_pinned_source_and_forwards_cancel_to_bounded_process(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        cancelled = threading.Event().is_set
        source = runtime.INTERNAL_MICROPHONE_SOURCE
        entries = [self.source_result(source)]
        with mock.patch.object(runtime, "run_bounded_command", return_value=(0, json.dumps(entries).encode())) as run:
            self.assertEqual(app.microphone_source_status(source, cancelled), (source, None))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][-3:], ["--format=json", "list", "sources"])
        self.assertIs(run.call_args.kwargs["cancelled"], cancelled)

    def test_recorder_commands_preserve_internal_alsa_and_pin_external_source(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        for kind, source in (("alsa", runtime.INTERNAL_MICROPHONE_SOURCE),
                             ("pulse", self.BLUETOOTH), ("pulse", self.USB)):
            with self.subTest(kind=kind, source=source):
                with mock.patch.object(runtime.subprocess, "Popen") as spawn:
                    runtime.PocketDSKeyboard.spawn_voice_recorder(kind, source)
                command = spawn.call_args.args[0]
                self.assertEqual(command[-1], str(runtime.VOICE_WAV))
                self.assertEqual(spawn.call_args.kwargs["stderr"], subprocess.DEVNULL)
                if kind == "alsa":
                    self.assertEqual(command, ["arecord", "-q", "-D", "plughw:0,2",
                                              "-f", "S16_LE", "-r", "16000", "-c", "1",
                                              str(runtime.VOICE_WAV)])
                else:
                    self.assertEqual(command[0], "parecord")
                    for argument in (f"--device={source}", "--file-format=wav", "--format=s16le",
                                     "--rate=16000", "--channels=1", "--latency-msec=50", "--property=node.dont-fallback=true",
                                     "--property=node.dont-reconnect=true", "--property=node.dont-move=true"):
                        self.assertIn(argument, command)
                    self.assertNotIn("plughw:0,2", command)

    def test_route_is_frozen_before_deferred_spawn_and_not_reselected(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        app.voice = runtime.VoiceSessionController()
        app.voice_thread = app.voice_process = None
        app.voice_retry_not_before = 0
        app.custom_voice_enabled = True
        app.recorder_stop = types.SimpleNamespace(active=False)
        spawns = []
        app.capture_startup = types.SimpleNamespace(active=False, start=spawns.append)
        app.current_voice_target = lambda: ("original-field", True)
        app.start_microphone_session = mock.Mock()
        app.flash_mic_error = self.fail
        generation = app.start_recording()
        self.assertIsNone(app.voice_recorder_route)
        self.assertEqual(spawns, [])
        app.on_microphone_selected(generation, self.BLUETOOTH)
        self.assertEqual(app.voice_recorder_route, (generation, "pulse", self.BLUETOOTH))
        with mock.patch.object(runtime.subprocess, "Popen") as spawn:
            spawns[0]()
        app.start_microphone_session.assert_called_once_with(generation)
        self.assertIn(f"--device={self.BLUETOOTH}", spawn.call_args.args[0])
        self.assertEqual(app.voice_recorder_route, (generation, "pulse", self.BLUETOOTH))

    def test_external_spawn_failure_never_falls_back_to_alsa(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        with mock.patch.object(runtime.subprocess, "Popen", side_effect=OSError("source vanished")) as spawn:
            with self.assertRaises(OSError):
                runtime.PocketDSKeyboard.spawn_voice_recorder("pulse", self.BLUETOOTH)
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(spawn.call_args.args[0][0], "parecord")

    def test_only_current_alsa_capture_may_repair_interrupted_wav(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        for route_kind, generation_offset, returncode, repaired, recognized in (
            ("alsa", 0, 1, True, True), ("pulse", 0, 1, False, False),
            ("alsa", -1, 1, False, False), ("pulse", 0, 0, False, True),
        ):
            with self.subTest(kind=route_kind, stale=generation_offset, returncode=returncode):
                app = object.__new__(runtime.PocketDSKeyboard)
                app.voice = runtime.VoiceSessionController()
                generation = app.voice.begin_recording("field")
                app.voice.begin_stopping(generation, "field", target_is_valid=True)
                source = runtime.INTERNAL_MICROPHONE_SOURCE if route_kind == "alsa" else self.BLUETOOTH
                app.voice_recorder_route = (generation + generation_offset, route_kind, source)
                app.current_voice_target = lambda: ("field", True)
                app.cleanup_voice_artifact = mock.Mock()
                app.voice_failed = mock.Mock()
                app.microphone_session = types.SimpleNamespace(
                    generation=generation, cancel=lambda: None,
                    finish=lambda: app.on_microphone_finalized(generation),
                )
                result = RecorderStopResult(returncode, False, None, sigint_sent=True)
                with mock.patch.object(runtime, "finalize_private_arecord_wav") as repair, \
                     mock.patch.object(runtime.threading, "Thread") as thread:
                    app.on_recording_reaped(result, generation)
                self.assertEqual(repair.called, repaired)
                self.assertEqual(thread.called, recognized)
                self.assertEqual(app.voice_failed.called, not recognized)


class RuntimeMicrophoneSessionTests(unittest.TestCase):
    def make_app(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        app.voice = runtime.VoiceSessionController()
        app.custom_voice_enabled = True
        app.voice_thread = app.voice_process = None
        app.voice_timeout = app.voice_retry_not_before = 0
        app.microphone_session = app.microphone_pending_result = None
        app.recorder_stop = types.SimpleNamespace(active=False)
        app.current_voice_target = lambda: ("original-field", True)
        app.capture_startup = types.SimpleNamespace(active=False, start=mock.Mock(), cancel=mock.Mock())
        app.cleanup_voice_artifact = mock.Mock()
        app.render_voice_snapshot = mock.Mock()
        app.flash_mic_error = mock.Mock()
        callbacks = queue.Queue()
        runtime.GLib.idle_add = lambda callback, *args: callbacks.put(lambda: callback(*args)) or 1
        return runtime, app, callbacks

    def test_worker_start_failure_cleans_session_without_recording(self):
        runtime, app, _callbacks = self.make_app()
        with mock.patch.object(runtime.threading, "Thread", side_effect=RuntimeError("thread unavailable")):
            app.start_recording()
        self.assertIsNone(app.microphone_session)
        self.assertFalse(app.voice.snapshot().busy)
        app.capture_startup.start.assert_not_called()
        app.flash_mic_error.assert_called_once()

    def test_release_during_lookup_cancels_queued_result_without_late_capture(self):
        runtime, app, callbacks = self.make_app()
        app.microphone_source_status = lambda source, cancelled: ("bluez_input.fixture", None)
        app.space_held()
        generation = app.space_voice_generation
        app.microphone_session._thread.join(1)
        app.space_voice_released()
        self.assertFalse(app.voice.snapshot().busy)
        self.assertGreater(app.voice.snapshot().generation, generation)
        self.assertEqual(app.space_voice_generation, 0)
        self.assertIsNone(app.microphone_session)
        callbacks.get(timeout=1)()
        app.capture_startup.start.assert_not_called()

    def test_release_after_recorder_launch_still_stops_once_when_pcm_is_ready(self):
        runtime, app, callbacks = self.make_app()
        app.microphone_source_status = lambda source, cancelled: ("bluez_input.fixture", None)
        app.space_held()
        generation = app.space_voice_generation
        app.microphone_session._thread.join(1)
        callbacks.get(timeout=1)()
        app.capture_startup.start.assert_called_once()
        app.space_voice_released()
        self.assertEqual(app.space_voice_release_generation, generation)
        runtime.GLib.timeout_add_seconds = lambda *_args: 99
        app.stop_recording = mock.Mock()
        process = FakeRecorderProcess()
        app.on_capture_ready(process)
        self.assertIs(app.voice_process, process)
        callbacks.get(timeout=1)()
        app.stop_recording.assert_called_once_with()
        self.assertEqual(app.space_voice_generation, 0)

    def test_lookup_runs_off_main_thread_and_cancel_does_not_wait_for_it(self):
        runtime, app, callbacks = self.make_app()
        entered, release = threading.Event(), threading.Event()
        caller = threading.get_ident()
        threads = []

        def lookup(source, cancelled):
            threads.append(threading.get_ident())
            entered.set()
            self.assertTrue(release.wait(1))
            return runtime.INTERNAL_MICROPHONE_SOURCE, None

        app.microphone_source_status = lookup
        generation = app.start_recording()
        session = app.microphone_session
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(app.voice.state, runtime.VoiceState.CONNECTING)
            self.assertEqual(app.voice.snapshot().generation, generation)
            self.assertNotEqual(threads, [caller])
            app.capture_startup.start.assert_not_called()
            app.cancel_voice("keyboard hidden during lookup")
            self.assertFalse(app.voice.snapshot().busy)
            self.assertIsNone(app.microphone_session)
        finally:
            release.set()
            session._thread.join(1)
        self.assertFalse(session._thread.is_alive())
        self.assertTrue(callbacks.empty())
        app.capture_startup.start.assert_not_called()

    def test_cancelled_queued_result_cannot_start_new_generation(self):
        runtime, app, callbacks = self.make_app()
        app.microphone_source_status = lambda source, cancelled: ("bluez_input.fixture", None)
        old = app.start_recording()
        old_session = app.microphone_session
        old_session._thread.join(1)
        queued = callbacks.get(timeout=1)
        app.cancel_voice("old session hidden")
        current = app.voice.begin_connecting("new-field")
        self.assertGreater(current, old)
        queued()
        app.on_microphone_selected(old, "bluez_input.fixture")
        app.on_microphone_denied(old, "麦克风已静音")
        self.assertEqual(app.voice.snapshot().generation, current)
        app.capture_startup.start.assert_not_called()

    def test_muted_lookup_shows_reason_without_starting_recorder(self):
        runtime, app, callbacks = self.make_app()
        app.microphone_source_status = lambda source, cancelled: (None, "麦克风已静音")
        app.start_recording()
        app.microphone_session._thread.join(1)
        callbacks.get(timeout=1)()
        app.capture_startup.start.assert_not_called()
        self.assertEqual(app.voice.state, runtime.VoiceState.ERROR)
        self.assertEqual(app.voice_error_feedback[1], "麦克风已静音")
        app.cleanup_voice_artifact.assert_called_once()

    def test_target_change_after_query_discards_before_recorder_spawn(self):
        runtime, app, callbacks = self.make_app()
        app.microphone_source_status = lambda source, cancelled: ("bluez_input.fixture", None)
        app.start_recording()
        app.microphone_session._thread.join(1)
        app.current_voice_target = lambda: ("another-field", True)
        callbacks.get(timeout=1)()
        app.capture_startup.start.assert_not_called()
        self.assertFalse(app.voice.snapshot().busy)

    def test_mid_capture_mute_releases_recorder_and_discards_artifact(self):
        runtime, app, _callbacks = self.make_app()
        generation = app.voice.begin_recording("original-field")
        process = FakeRecorderProcess()
        app.voice_process = process
        stopped = []
        app.stop_startup_recorder = lambda owned, cancelled: stopped.append((owned, cancelled))
        session = types.SimpleNamespace(cancel=mock.Mock())
        app.microphone_session = session
        app.on_microphone_denied(generation, "麦克风音量为零")
        self.assertEqual(stopped, [(process, True)])
        self.assertIsNone(app.voice_process)
        self.assertIsNone(app.voice_thread)
        self.assertEqual(app.voice.state, runtime.VoiceState.ERROR)
        self.assertEqual(app.voice_error_feedback[1], "麦克风音量为零")
        session.cancel.assert_called_once()
        app.cleanup_voice_artifact.assert_called_once()

    def test_internal_asr_waits_for_post_reap_permission_and_new_target_gate(self):
        for outcome in ("allowed", "muted", "target-changed", "cancelled"):
            with self.subTest(outcome=outcome):
                runtime, app, _callbacks = self.make_app()
                generation = app.voice.begin_recording("original-field")
                app.voice.begin_stopping(generation, "original-field", target_is_valid=True)
                app.voice_recorder_route = (generation, "alsa", runtime.INTERNAL_MICROPHONE_SOURCE)
                session = types.SimpleNamespace(generation=generation, finish=mock.Mock(), cancel=mock.Mock())
                app.microphone_session = session
                with mock.patch.object(runtime.threading, "Thread") as thread:
                    app.on_recording_reaped(RecorderStopResult(0, False, None), generation)
                    session.finish.assert_called_once()
                    self.assertEqual(app.voice.state, runtime.VoiceState.STOPPING)
                    thread.assert_not_called()
                    if outcome == "muted":
                        app.on_microphone_denied(generation, "麦克风已静音")
                    elif outcome == "cancelled":
                        app.cancel_voice("hidden after reap")
                    elif outcome == "target-changed":
                        app.current_voice_target = lambda: ("another-field", True)
                    app.on_microphone_finalized(generation)
                    self.assertEqual(thread.called, outcome == "allowed")
                if outcome == "allowed":
                    self.assertEqual(app.voice.state, runtime.VoiceState.RECOGNIZING)
                else:
                    self.assertFalse(app.voice.snapshot().busy)
                    app.cleanup_voice_artifact.assert_called()


class MicrophoneMonitorTests(unittest.TestCase):
    def make_monitor(self, query):
        callbacks = queue.Queue()
        selected, denied, finalized = [], [], []
        monitor = MicrophoneSessionMonitor(
            17, query, callbacks.put,
            lambda *args: selected.append(args),
            lambda *args: denied.append(args),
            lambda *args: finalized.append(args),
            guarded_source="internal", poll_interval=0.01,
        )
        return monitor, callbacks, selected, denied, finalized

    def test_mute_or_lost_source_is_reported_after_initial_selection(self):
        for response in ((None, "麦克风已静音"), (None, "麦克风音量为零"), (None, "麦克风状态检查失败")):
            with self.subTest(response=response):
                requests = []
                def query(source, cancelled):
                    requests.append(source)
                    return ("internal", None) if source is None else response
                monitor, callbacks, selected, denied, finalized = self.make_monitor(query)
                monitor.start()
                monitor._thread.join(1)
                self.assertFalse(monitor._thread.is_alive())
                while not callbacks.empty():
                    callbacks.get_nowait()()
                self.assertEqual(requests, [None, "internal"])
                self.assertEqual(selected, [(17, "internal")])
                self.assertEqual(denied, [(17, response[1])])
                self.assertEqual(finalized, [])

    def test_post_reap_check_is_fresh_even_when_previous_poll_is_in_flight(self):
        polling, release, checked = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def query(source, cancelled):
            calls.append(source)
            if len(calls) == 2:
                polling.set()
                self.assertTrue(release.wait(1))
            elif len(calls) == 3:
                checked.set()
            return "internal", None
        monitor, callbacks, selected, denied, finalized = self.make_monitor(query)
        monitor.start()
        try:
            self.assertTrue(polling.wait(1))
            monitor.finish()
            release.set()
            self.assertTrue(checked.wait(1))
        finally:
            release.set()
            monitor._thread.join(1)
            monitor.cancel()
        self.assertFalse(monitor._thread.is_alive())
        # Cancel also invalidates all callbacks already queued on the UI loop.
        while not callbacks.empty():
            callbacks.get_nowait()()
        self.assertEqual(calls, [None, "internal", "internal"])
        self.assertEqual(selected + denied + finalized, [])

    def test_final_permission_success_and_failure_are_mutually_exclusive(self):
        for problem in (None, "麦克风已静音"):
            with self.subTest(problem=problem):
                def query(source, cancelled):
                    return ("internal", None) if source is None or problem is None else (None, problem)
                monitor, callbacks, selected, denied, finalized = self.make_monitor(query)
                monitor.finish()
                monitor.start()
                monitor._thread.join(1)
                self.assertFalse(monitor._thread.is_alive())
                while not callbacks.empty():
                    callbacks.get_nowait()()
                self.assertEqual(selected, [(17, "internal")])
                self.assertEqual(finalized, [(17,)] if problem is None else [])
                self.assertEqual(denied, [] if problem is None else [(17, problem)])


class RuntimeCaptureHandoffTests(unittest.TestCase):
    class RuntimeGLib:
        def __init__(self, outcome) -> None:
            self.outcome = outcome
            self.timeout_calls = 0
            self.removed = []

        def timeout_add_seconds(self, _seconds, _callback):
            self.timeout_calls += 1
            if isinstance(self.outcome, BaseException):
                raise self.outcome
            return self.outcome

        def source_remove(self, source_id):
            self.removed.append(source_id)
            return True

        @staticmethod
        def idle_add(_callback, *_args):
            return 1

    class DeferredStopOwner:
        def __init__(self, *, error=None) -> None:
            self.error = error
            self.requests = []
            self.active = False
            self.process = None
            self.on_reaped = None

        def request(self, process, **kwargs) -> None:
            self.requests.append((process, kwargs["cancelled"]))
            if self.error is not None:
                raise self.error
            if self.active:
                raise RuntimeError("synthetic duplicate stop request")
            self.active = True
            self.process = process
            self.on_reaped = kwargs["on_reaped"]

        def reap(self, returncode=-9) -> None:
            callback = self.on_reaped
            self.active = False
            self.process = None
            self.on_reaped = None
            if callback is not None:
                callback(
                    RecorderStopResult(
                        returncode=returncode,
                        forced=True,
                        failure=None,
                    )
                )

        def shutdown(self) -> None:
            return None

    class HeldRecorderProcess:
        """A fake D-state child released explicitly by the test."""

        def __init__(self) -> None:
            self.returncode = None
            self.signals = []
            self.kill_calls = 0
            self.released = threading.Event()

        def poll(self):
            return self.returncode

        def send_signal(self, requested_signal) -> None:
            self.signals.append(requested_signal)

        def kill(self) -> None:
            self.kill_calls += 1

        def wait(self, timeout=None):
            if not self.released.wait(timeout):
                raise subprocess.TimeoutExpired("held-parecord", timeout)
            self.returncode = -9
            return self.returncode

        def release(self) -> None:
            self.returncode = -9
            self.released.set()

    @staticmethod
    def wait_until(predicate, *, timeout=2.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.005)
        if not predicate():
            raise AssertionError("capture ownership did not settle")

    def make_runtime(
        self,
        glib,
        stop_owner,
        *,
        process=None,
        before_poll=None,
    ):
        runtime = load_keyboard_runtime_for_capture_tests()
        runtime.GLib = glib
        app = object.__new__(runtime.PocketDSKeyboard)
        app.voice = runtime.VoiceSessionController()
        target = (17, ("atspi", ":1.80", "/field/voice"))
        app.voice.begin_connecting(target)
        app.current_voice_target = lambda: (target, True)
        app.voice_process = None
        app.voice_thread = None
        app.voice_timeout = 313
        app.voice_retry_not_before = 0.0
        app.custom_voice_enabled = True
        app.recorder_stop = stop_owner
        app.cleanup_voice_artifact = lambda: None

        scheduler = FakeScheduler()
        owned = process or FakeRecorderProcess()
        app.capture_startup = runtime.CaptureStartupController(
            scheduler,
            app.stop_startup_recorder,
            lambda: True,
            lambda: None,
            app.on_capture_ready,
            app.on_capture_failed,
            poll_interval_ms=10,
            attempt_timeout_ms=100,
            monotonic=lambda: scheduler.now_ms / 1_000,
        )
        self.assertTrue(app.capture_startup.start(lambda: owned))
        if before_poll is not None:
            before_poll(runtime, app)
        scheduler.advance(10)
        return runtime, app, scheduler, owned, target

    def assert_new_recording_can_start(self, runtime, app, target) -> None:
        next_process = FakeRecorderProcess()
        app.current_voice_target = lambda: (target, True)
        app.start_microphone_session = lambda generation: app.on_microphone_selected(generation, "physical-mic")
        app.spawn_voice_recorder = lambda _kind, _source: next_process
        app.voice_thread = None
        app.voice_timeout = 0
        app.voice_retry_not_before = 0.0

        app.start_recording()

        self.assertEqual(app.voice.state, runtime.VoiceState.CONNECTING)
        self.assertTrue(app.capture_startup.active)
        self.assertIs(app.capture_startup._process, next_process)

    def test_timeout_api_throw_keeps_one_stop_owner_and_does_not_pollute_fields(self) -> None:
        glib = self.RuntimeGLib(RuntimeError("synthetic timeout API failure"))
        stop_owner = self.DeferredStopOwner()
        runtime, app, _scheduler, process, target = self.make_runtime(
            glib,
            stop_owner,
        )

        self.assertEqual(app.voice.state, runtime.VoiceState.ERROR)
        self.assertIsNone(app.voice_process)
        self.assertEqual(app.voice_timeout, 313)
        self.assertFalse(app.capture_startup.active)
        self.assertTrue(stop_owner.active)
        self.assertEqual(stop_owner.requests, [(process, True)])

        # Cancel while the sole stop owner is still active must only downgrade
        # session intent. It must not issue a second stop request.
        app.cancel_voice("synthetic immediate cancel")
        self.assertEqual(stop_owner.requests, [(process, True)])
        self.assertIsNone(app.voice_process)
        self.assertEqual(app.voice_timeout, 0)
        self.assertEqual(glib.removed, [313])

        stop_owner.reap()
        self.assertFalse(stop_owner.active)
        self.assert_new_recording_can_start(runtime, app, target)

    def test_zero_timeout_source_is_failure_before_runtime_publication(self) -> None:
        glib = self.RuntimeGLib(0)
        stop_owner = self.DeferredStopOwner()
        runtime, app, _scheduler, process, _target = self.make_runtime(
            glib,
            stop_owner,
        )

        self.assertEqual(app.voice.state, runtime.VoiceState.ERROR)
        self.assertIsNone(app.voice_process)
        self.assertEqual(app.voice_timeout, 313)
        self.assertEqual(stop_owner.requests, [(process, True)])

    def test_capture_ready_reject_or_exception_uses_only_controller_handoff(self) -> None:
        cases = (
            ("rejected", lambda _generation: False),
            (
                "exception",
                lambda _generation: (_ for _ in ()).throw(
                    RuntimeError("synthetic voice transition failure")
                ),
            ),
        )
        for label, capture_ready in cases:
            with self.subTest(label=label):
                glib = self.RuntimeGLib(71)
                stop_owner = self.DeferredStopOwner()
                runtime, app, _scheduler, process, _target = self.make_runtime(
                    glib,
                    stop_owner,
                    before_poll=lambda _runtime, keyboard: setattr(
                        keyboard.voice,
                        "capture_ready",
                        capture_ready,
                    ),
                )

                self.assertEqual(app.voice.state, runtime.VoiceState.ERROR)
                self.assertIsNone(app.voice_process)
                self.assertEqual(app.voice_timeout, 313)
                self.assertEqual(glib.timeout_calls, 0)
                self.assertEqual(stop_owner.requests, [(process, True)])

    def test_changed_target_rejects_before_timer_and_stops_exactly_once(self) -> None:
        glib = self.RuntimeGLib(72)
        stop_owner = self.DeferredStopOwner()
        changed = (99, ("atspi", ":1.99", "/other"))
        runtime, app, _scheduler, process, _target = self.make_runtime(
            glib,
            stop_owner,
            before_poll=lambda _runtime, keyboard: setattr(
                keyboard,
                "current_voice_target",
                lambda: (changed, True),
            ),
        )

        self.assertEqual(app.voice.state, runtime.VoiceState.ERROR)
        self.assertIsNone(app.voice_process)
        self.assertEqual(app.voice_timeout, 313)
        self.assertEqual(glib.timeout_calls, 0)
        self.assertEqual(stop_owner.requests, [(process, True)])

    def test_stop_request_throw_uses_emergency_owner_without_cancel_retry(self) -> None:
        glib = self.RuntimeGLib(RuntimeError("synthetic timeout API failure"))
        stop_owner = self.DeferredStopOwner(
            error=RuntimeError("synthetic stop-owner rejection")
        )
        process = self.HeldRecorderProcess()
        runtime, app, _scheduler, _owned, target = self.make_runtime(
            glib,
            stop_owner,
            process=process,
        )

        self.assertEqual(app.voice.state, runtime.VoiceState.ERROR)
        self.assertIsNone(app.voice_process)
        self.assertEqual(app.voice_timeout, 313)
        self.assertTrue(app.capture_startup.active)
        self.assertEqual(stop_owner.requests, [(process, True)])

        app.cancel_voice("synthetic cancel during emergency reap")
        self.assertEqual(stop_owner.requests, [(process, True)])
        self.assertTrue(app.capture_startup.active)
        self.assertIsNone(app.voice_process)
        self.assertEqual(app.voice_timeout, 0)
        self.assertEqual(glib.removed, [313])

        process.release()
        self.wait_until(lambda: not app.capture_startup.active)
        self.assert_new_recording_can_start(runtime, app, target)

    def test_positive_timeout_is_published_with_process_then_cancelled_once(self) -> None:
        glib = self.RuntimeGLib(44)
        stop_owner = self.DeferredStopOwner()
        runtime, app, _scheduler, process, _target = self.make_runtime(
            glib,
            stop_owner,
        )

        self.assertEqual(app.voice.state, runtime.VoiceState.RECORDING)
        self.assertIs(app.voice_process, process)
        self.assertEqual(app.voice_timeout, 44)
        self.assertFalse(app.capture_startup.active)
        self.assertEqual(stop_owner.requests, [])

        app.cancel_voice("synthetic success cancellation")
        self.assertIsNone(app.voice_process)
        self.assertEqual(app.voice_timeout, 0)
        self.assertEqual(glib.removed, [44])
        self.assertEqual(stop_owner.requests, [(process, True)])


class LiveWavReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.wav = self.root / "voice.wav"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def live_wav(
        payload=b"",
        *,
        sample_rate=16_000,
        riff_size=0,
        data_size=0,
    ):
        fmt = b"fmt " + struct.pack(
            "<IHHIIHH",
            16,
            1,
            1,
            sample_rate,
            sample_rate * 2,
            2,
            16,
        )
        # Live libsndfile headers may not have their final RIFF/data lengths.
        return (
            b"RIFF"
            + struct.pack("<I", riff_size)
            + b"WAVE"
            + fmt
            + b"data"
            + struct.pack("<I", data_size)
            + payload
        )

    def write_private(self, content) -> None:
        self.wav.write_bytes(content)
        self.wav.chmod(0o600)

    def test_missing_empty_and_header_only_are_not_ready(self) -> None:
        self.assertFalse(private_wav_has_pcm_payload(self.wav))
        self.write_private(b"")
        self.assertFalse(private_wav_has_pcm_payload(self.wav))
        self.write_private(self.live_wav())
        self.assertFalse(private_wav_has_pcm_payload(self.wav))

    def test_one_complete_s16le_frame_is_ready_before_header_finalize(self) -> None:
        self.write_private(self.live_wav(b"\x00\x00"))
        self.assertTrue(private_wav_has_pcm_payload(self.wav))

    def test_partial_one_byte_sample_is_not_ready(self) -> None:
        self.write_private(self.live_wav(b"\x00"))
        self.assertFalse(private_wav_has_pcm_payload(self.wav))

    def test_wrong_pcm_format_fails_closed(self) -> None:
        self.write_private(self.live_wav(b"\x00\x00", sample_rate=48_000))
        with self.assertRaises(VoiceArtifactError):
            private_wav_has_pcm_payload(self.wav)

    def test_public_hardlinked_and_symlink_artifacts_fail_closed(self) -> None:
        self.write_private(self.live_wav(b"\x00\x00"))
        self.wav.chmod(0o644)
        with self.assertRaises(VoiceArtifactError):
            private_wav_has_pcm_payload(self.wav)

        self.wav.chmod(0o600)
        linked = self.root / "voice-hardlink.wav"
        os.link(self.wav, linked)
        with self.assertRaises(VoiceArtifactError):
            private_wav_has_pcm_payload(self.wav)
        linked.unlink()

        target = self.root / "target.wav"
        self.wav.rename(target)
        self.wav.symlink_to(target)
        with self.assertRaises(VoiceArtifactError):
            private_wav_has_pcm_payload(self.wav)

    def test_known_live_and_finalized_lengths_are_accepted(self) -> None:
        self.write_private(self.live_wav(b"\x00\x00", riff_size=8))
        self.assertTrue(private_wav_has_pcm_payload(self.wav))

        self.write_private(
            self.live_wav(b"\x00\x00", riff_size=38, data_size=2)
        )
        self.assertTrue(private_wav_has_pcm_payload(self.wav))

        # alsa-utils arecord writes its maximum unbounded recording lengths
        # until end_wave() replaces them with the real file sizes.
        self.write_private(
            self.live_wav(
                b"\x00\x00",
                riff_size=0x7FFFFF24,
                data_size=0x7FFFFF00,
            )
        )
        self.assertTrue(private_wav_has_pcm_payload(self.wav))

        self.write_private(
            self.live_wav(
                b"\x00\x00",
                riff_size=0x80000024,
                data_size=0x80000000,
            )
        )
        self.assertTrue(private_wav_has_pcm_payload(self.wav))

        self.write_private(
            self.live_wav(
                b"\x00\x00",
                riff_size=0xFFFFFFFF,
                data_size=0xFFFFFFFF,
            )
        )
        self.assertTrue(private_wav_has_pcm_payload(self.wav))

    def test_arecord_live_length_pair_must_match(self) -> None:
        self.write_private(
            self.live_wav(
                b"\x00\x00",
                riff_size=0x7FFFFF24,
                data_size=0,
            )
        )
        with self.assertRaises(VoiceArtifactError):
            private_wav_has_pcm_payload(self.wav)

        self.write_private(
            self.live_wav(
                b"\x00\x00",
                riff_size=0,
                data_size=0x7FFFFF00,
            )
        )
        with self.assertRaises(VoiceArtifactError):
            private_wav_has_pcm_payload(self.wav)

        self.write_private(
            self.live_wav(
                b"\x00\x00",
                riff_size=0x7FFFFF24,
                data_size=0x80000000,
            )
        )
        with self.assertRaises(VoiceArtifactError):
            private_wav_has_pcm_payload(self.wav)

    def test_interrupted_arecord_placeholder_is_finalized_in_place(self) -> None:
        payload = b"\x12\x34" * 80
        self.write_private(
            self.live_wav(
                payload,
                riff_size=0x80000024,
                data_size=0x80000000,
            )
        )
        before = self.wav.stat()

        finalize_private_arecord_wav(self.wav)

        after = self.wav.stat()
        content = read_private_wav(self.wav)
        self.assertEqual(after.st_ino, before.st_ino)
        self.assertEqual(after.st_size, before.st_size)
        self.assertEqual(struct.unpack_from("<I", content, 4)[0], len(content) - 8)
        self.assertEqual(struct.unpack_from("<I", content, 40)[0], len(payload))
        self.assertEqual(content[44:], payload)

    def test_finalize_rejects_nonplaceholder_and_linked_files(self) -> None:
        self.write_private(self.live_wav(b"\x00\x00", riff_size=38, data_size=2))
        with self.assertRaises(VoiceArtifactError):
            finalize_private_arecord_wav(self.wav)

        self.write_private(
            self.live_wav(
                b"\x00\x00",
                riff_size=0x80000024,
                data_size=0x80000000,
            )
        )
        linked = self.root / "voice-finalize-hardlink.wav"
        os.link(self.wav, linked)
        try:
            with self.assertRaises(VoiceArtifactError):
                finalize_private_arecord_wav(self.wav)
        finally:
            linked.unlink()

    def test_obviously_wrong_riff_and_data_lengths_fail_closed(self) -> None:
        self.write_private(
            self.live_wav(b"\x00\x00", riff_size=123_456, data_size=2)
        )
        with self.assertRaises(VoiceArtifactError):
            private_wav_has_pcm_payload(self.wav)

        self.write_private(
            self.live_wav(b"\x00\x00", riff_size=38, data_size=4)
        )
        with self.assertRaises(VoiceArtifactError):
            private_wav_has_pcm_payload(self.wav)

        self.write_private(
            self.live_wav(b"\x00\x00", riff_size=38, data_size=1)
        )
        with self.assertRaises(VoiceArtifactError):
            private_wav_has_pcm_payload(self.wav)

    def test_truncation_during_live_probe_fails_closed(self) -> None:
        self.write_private(self.live_wav(b"\x00\x00"))
        real_read = voice_artifacts.os.read
        truncated = False

        def read_and_truncate(descriptor, maximum):
            nonlocal truncated
            content = real_read(descriptor, maximum)
            if content and not truncated:
                truncated = True
                os.truncate(self.wav, 0)
            return content

        voice_artifacts.os.read = read_and_truncate
        try:
            with self.assertRaises(VoiceArtifactError):
                private_wav_has_pcm_payload(self.wav)
        finally:
            voice_artifacts.os.read = real_read

    def test_fifo_is_rejected_without_blocking_live_or_final_reader(self) -> None:
        os.mkfifo(self.wav, 0o600)
        component = str(KEYBOARD_COMPONENT)
        code = f"""
import sys
from pathlib import Path
sys.path.insert(0, {component!r})
from voice_artifacts import (
    AsrApiError,
    VoiceArtifactError,
    private_wav_has_pcm_payload,
    read_private_wav,
)
path = Path(sys.argv[2])
try:
    if sys.argv[1] == "live":
        private_wav_has_pcm_payload(path)
    else:
        read_private_wav(path)
except (AsrApiError, VoiceArtifactError):
    raise SystemExit(0)
raise SystemExit(3)
"""
        for reader in ("live", "final"):
            process = subprocess.Popen(
                [sys.executable, "-c", code, reader, str(self.wav)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                returncode = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
                self.fail(f"{reader} WAV reader blocked on FIFO")
            self.assertEqual(returncode, 0)


class BoundedRepeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeScheduler()
        self.allowed = True
        self.emissions: list[int] = []
        self.repeat = BoundedRepeatController(
            self.clock,
            lambda: self.allowed,
            lambda: self.emissions.append(self.clock.now_ms),
            initial_delay_ms=50,
            interval_ms=40,
            maximum_ms=200,
            monotonic=lambda: self.clock.now_ms / 1_000,
        )

    def test_hidden_keyboard_never_starts_repeat(self) -> None:
        self.allowed = False
        self.assertFalse(self.repeat.start())
        self.clock.advance(1_000, deliver_cancelled=True)
        self.assertEqual(self.emissions, [])

    def test_every_tick_rechecks_visibility(self) -> None:
        self.assertTrue(self.repeat.start())
        self.clock.advance(50)
        self.allowed = False
        self.clock.advance(40)
        self.clock.advance(1_000, deliver_cancelled=True)
        self.assertEqual(self.emissions, [0, 50])
        self.assertFalse(self.repeat.active)

    def test_wall_clock_deadline_is_a_hard_cutoff(self) -> None:
        self.repeat.start()
        self.clock.advance(1_000, deliver_cancelled=True)
        self.assertEqual(self.emissions, [0, 50, 90, 130, 170])
        self.assertTrue(all(at < 200 for at in self.emissions))
        self.assertFalse(self.repeat.active)

    def test_cancelled_generation_rejects_late_callback(self) -> None:
        self.repeat.start()
        self.repeat.cancel()
        self.clock.advance(1_000, deliver_cancelled=True)
        self.assertEqual(self.emissions, [0])


class BackspaceClearGestureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeScheduler()
        self.allowed = True
        self.feedback = []
        self.clears = []
        self.gesture = BackspaceClearGestureController(
            self.clock,
            lambda: self.allowed,
            self.feedback.append,
            lambda: self.clears.append("clear"),
            hold_delay_ms=100,
            upward_distance=50,
        )

    def test_hold_swipe_and_release_clears_exactly_once(self) -> None:
        self.assertTrue(self.gesture.begin("finger", 100, 100))
        self.clock.advance(100)
        self.assertEqual(self.feedback, ["held"])

        self.assertTrue(self.gesture.update("finger", 108, 40))
        self.assertEqual(self.feedback, ["held", "ready"])
        self.assertTrue(self.gesture.end("finger", 108, 40))
        self.assertFalse(self.gesture.end("finger", 108, 40))

        self.assertEqual(self.clears, ["clear"])
        self.assertEqual(self.feedback, ["held", "ready", "idle"])
        self.assertFalse(self.gesture.active)

    def test_upward_motion_before_hold_becomes_ready_when_hold_fires(self) -> None:
        self.gesture.begin("finger", 100, 100)
        self.assertFalse(self.gesture.update("finger", 100, 40))
        self.assertEqual(self.feedback, [])

        self.clock.advance(100)
        self.assertEqual(self.feedback, ["held", "ready"])
        self.assertTrue(self.gesture.end("finger", 100, 40))
        self.assertEqual(self.clears, ["clear"])

    def test_sliding_back_down_before_release_cancels_clear(self) -> None:
        self.gesture.begin("finger", 100, 100)
        self.clock.advance(100)
        self.assertTrue(self.gesture.update("finger", 100, 40))
        self.assertFalse(self.gesture.update("finger", 100, 90))

        self.assertFalse(self.gesture.end("finger", 100, 90))
        self.assertEqual(self.clears, [])
        self.assertEqual(
            self.feedback,
            ["held", "ready", "held", "idle"],
        )

    def test_horizontal_or_short_motion_never_arms_clear(self) -> None:
        self.gesture.begin("finger", 100, 100)
        self.clock.advance(100)
        self.assertFalse(self.gesture.update("finger", 180, 40))
        self.assertFalse(self.gesture.end("finger", 180, 40))
        self.assertEqual(self.clears, [])

    def test_cancel_and_late_timer_never_clear(self) -> None:
        self.gesture.begin("finger", 100, 100)
        self.gesture.update("finger", 100, 20)
        self.gesture.cancel()
        self.clock.advance(1_000, deliver_cancelled=True)

        self.assertFalse(self.gesture.end("finger", 100, 20))
        self.assertEqual(self.feedback, [])
        self.assertEqual(self.clears, [])

    def test_scheduler_failure_leaves_the_destructive_gesture_disabled(self) -> None:
        gesture = BackspaceClearGestureController(
            FailingScheduler(fail_on_call=1),
            lambda: True,
            self.feedback.append,
            lambda: self.clears.append("clear"),
        )
        self.assertFalse(gesture.begin("finger", 100, 100))
        self.assertFalse(gesture.update("finger", 100, 0))
        self.assertFalse(gesture.end("finger", 100, 0))
        self.assertEqual(self.feedback, [])
        self.assertEqual(self.clears, [])


class HoldTapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeScheduler()
        self.allowed = True
        self.actions = []
        self.controller = HoldTapController(
            self.clock,
            lambda: self.allowed,
            lambda: self.actions.append("tap"),
            lambda: self.actions.append("hold"),
            lambda: self.actions.append("hold-release"),
            hold_delay_ms=450,
        )

    def test_short_release_and_click_emit_one_tap(self) -> None:
        self.assertTrue(self.controller.press())
        self.clock.advance(200)
        self.controller.release()
        self.assertTrue(self.controller.click())
        self.clock.advance(1_000, deliver_cancelled=True)
        self.assertEqual(self.actions, ["tap"])

    def test_clicked_before_release_is_also_one_tap(self) -> None:
        self.controller.press()
        self.assertTrue(self.controller.click())
        self.controller.release()
        self.assertEqual(self.actions, ["tap"])

    def test_hold_fires_once_and_consumes_trailing_click(self) -> None:
        self.controller.press()
        self.clock.advance(450)
        self.controller.release()
        self.assertFalse(self.controller.click())
        self.clock.advance(1_000)
        self.assertEqual(self.actions, ["hold", "hold-release"])

    def test_clicked_before_release_also_finishes_hold_exactly_once(self) -> None:
        self.controller.press()
        self.clock.advance(450)
        self.assertFalse(self.controller.click())
        self.assertFalse(self.controller.release())
        self.assertFalse(self.controller.click())
        self.assertEqual(self.actions, ["hold", "hold-release"])

    def test_cancelled_hold_discards_without_release_callback(self) -> None:
        self.controller.press()
        self.clock.advance(450)
        self.assertTrue(self.controller.cancel())
        self.assertFalse(self.controller.release())
        self.assertEqual(self.actions, ["hold"])

    def test_cancel_and_visibility_loss_reject_late_actions(self) -> None:
        self.controller.press()
        self.controller.cancel()
        self.clock.advance(1_000, deliver_cancelled=True)
        self.assertFalse(self.controller.click())
        self.controller.press()
        self.allowed = False
        self.clock.advance(450)
        self.controller.release()
        self.controller.click()
        self.assertEqual(self.actions, [])

    def test_scheduler_failure_keeps_short_tap_available(self) -> None:
        controller = HoldTapController(
            FailingScheduler(fail_on_call=1),
            lambda: True,
            lambda: self.actions.append("tap"),
            lambda: self.actions.append("hold"),
        )
        self.assertTrue(controller.press())
        controller.release()
        self.assertTrue(controller.click())
        self.assertEqual(self.actions, ["tap"])


class PushToTalkInteractionTests(unittest.TestCase):
    @staticmethod
    def make_app():
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        app.voice = runtime.VoiceSessionController()
        app.space_voice_generation = 0
        app.space_voice_release_generation = 0
        app.voice_process = None
        stops = []
        app.stop_recording = lambda: stops.append("stop")
        return runtime, app, stops

    @staticmethod
    def begin_connecting(app):
        return app.voice.begin_connecting(
            (19, ("atspi", ":1.95", "/field/push-to-talk"))
        )

    def test_hold_records_the_generation_started_by_this_gesture(self) -> None:
        _runtime, app, _stops = self.make_app()
        app.start_recording = lambda: 23
        app.space_voice_release_generation = 11

        app.space_held()

        self.assertEqual(app.space_voice_generation, 23)
        self.assertEqual(app.space_voice_release_generation, 0)

    def test_release_while_recording_stops_immediately_once(self) -> None:
        runtime, app, stops = self.make_app()
        generation = self.begin_connecting(app)
        self.assertTrue(app.voice.capture_ready(generation))
        app.voice_process = object()
        app.space_voice_generation = generation

        app.space_voice_released()
        app.space_voice_released()

        self.assertEqual(stops, ["stop"])
        self.assertEqual(app.space_voice_generation, 0)
        self.assertEqual(app.space_voice_release_generation, 0)
        self.assertEqual(app.voice.state, runtime.VoiceState.RECORDING)

    def test_release_while_connecting_stops_after_ready_handoff(self) -> None:
        runtime, app, stops = self.make_app()
        generation = self.begin_connecting(app)
        app.space_voice_generation = generation
        app.voice_recorder_route = (generation, "pulse", "physical-mic")

        app.space_voice_released()
        self.assertEqual(app.space_voice_release_generation, generation)
        self.assertEqual(stops, [])

        self.assertTrue(app.voice.capture_ready(generation))
        app.voice_process = object()
        self.assertFalse(app.finish_deferred_space_voice(generation))
        self.assertEqual(stops, ["stop"])
        self.assertEqual(app.space_voice_generation, 0)
        self.assertEqual(app.space_voice_release_generation, 0)
        self.assertEqual(app.voice.state, runtime.VoiceState.RECORDING)

    def test_stale_release_cannot_stop_a_new_voice_generation(self) -> None:
        _runtime, app, stops = self.make_app()
        old_generation = self.begin_connecting(app)
        app.space_voice_generation = old_generation
        app.voice.cancel()
        app.voice.begin_connecting(
            (20, ("atspi", ":1.95", "/field/new-voice"))
        )

        app.space_voice_released()

        self.assertEqual(stops, [])
        self.assertEqual(app.space_voice_generation, 0)
        self.assertEqual(app.space_voice_release_generation, 0)

    def test_deferred_release_is_discarded_after_session_cancel(self) -> None:
        _runtime, app, stops = self.make_app()
        generation = self.begin_connecting(app)
        app.space_voice_generation = generation
        app.space_voice_release_generation = generation
        app.voice.cancel()

        self.assertFalse(app.finish_deferred_space_voice(generation))
        self.assertEqual(stops, [])
        self.assertEqual(app.space_voice_generation, 0)
        self.assertEqual(app.space_voice_release_generation, 0)

    def test_cancelled_hold_cancels_the_voice_generation_it_started(self) -> None:
        runtime, app, _stops = self.make_app()
        generation = self.begin_connecting(app)
        app.space_voice_generation = generation
        app.space_hold = types.SimpleNamespace(cancel=lambda: True)
        cancellations = []
        app.cancel_voice = cancellations.append

        app.cancel_space_hold()

        self.assertEqual(cancellations, ["space hold cancelled"])
        self.assertEqual(app.voice.state, runtime.VoiceState.CONNECTING)

    def test_cancelled_hold_does_not_cancel_an_unowned_voice_session(self) -> None:
        runtime, app, _stops = self.make_app()
        self.begin_connecting(app)
        app.space_hold = types.SimpleNamespace(cancel=lambda: True)
        cancellations = []
        app.cancel_voice = cancellations.append

        app.cancel_space_hold()

        self.assertEqual(cancellations, [])
        self.assertEqual(app.space_voice_generation, 0)
        self.assertEqual(app.voice.state, runtime.VoiceState.CONNECTING)


class VoiceSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshots = []
        self.voice = VoiceSessionController(self.snapshots.append)
        self.target = ("atspi", ":1.50", "/field/1")

    def test_one_state_machine_owns_record_recognize_and_paste(self) -> None:
        generation = self.voice.begin_recording(self.target)
        self.assertEqual(self.voice.state, VoiceState.RECORDING)
        self.assertTrue(self.voice.snapshot().busy)
        self.assertTrue(
            self.voice.begin_stopping(
                generation,
                self.target,
                target_is_valid=True,
            )
        )
        self.assertEqual(self.voice.state, VoiceState.STOPPING)
        self.assertTrue(
            self.voice.begin_recognition(
                generation,
                self.target,
                target_is_valid=True,
            )
        )
        self.assertTrue(
            self.voice.prepare_paste(
                generation,
                self.target,
                target_is_valid=True,
            )
        )
        self.assertTrue(
            self.voice.consume_paste(
                generation,
                self.target,
                target_is_valid=True,
            )
        )
        self.assertEqual(self.voice.state, VoiceState.SUCCESS)
        self.assertFalse(self.voice.snapshot().busy)

    def test_connecting_is_busy_but_cannot_recognize_until_pcm_ready(self) -> None:
        generation = self.voice.begin_connecting(self.target)
        self.assertEqual(self.voice.state, VoiceState.CONNECTING)
        self.assertTrue(self.voice.snapshot().busy)
        self.assertFalse(
            self.voice.begin_recognition(
                generation,
                self.target,
                target_is_valid=True,
            )
        )

        self.assertTrue(self.voice.capture_ready(generation))
        self.assertEqual(self.voice.state, VoiceState.RECORDING)
        self.assertTrue(
            self.voice.begin_stopping(
                generation,
                self.target,
                target_is_valid=True,
            )
        )
        self.assertTrue(
            self.voice.begin_recognition(
                generation,
                self.target,
                target_is_valid=True,
            )
        )

    def test_cancel_connecting_invalidates_late_ready_callback(self) -> None:
        generation = self.voice.begin_connecting(self.target)
        self.voice.cancel()

        self.assertEqual(self.voice.state, VoiceState.IDLE)
        self.assertFalse(self.voice.capture_ready(generation))
        self.assertFalse(self.voice.fail(generation))

    def test_target_change_before_clipboard_discards_transcript(self) -> None:
        generation = self.voice.begin_recording(self.target)
        self.voice.begin_stopping(
            generation,
            self.target,
            target_is_valid=True,
        )
        self.voice.begin_recognition(
            generation,
            self.target,
            target_is_valid=True,
        )
        self.assertFalse(
            self.voice.prepare_paste(
                generation,
                ("atspi", ":1.51", "/field/2"),
                target_is_valid=True,
            )
        )
        self.assertEqual(self.voice.state, VoiceState.DISCARDED)
        self.assertNotEqual(self.voice.snapshot().generation, generation)

    def test_target_change_during_clipboard_delay_discards_paste(self) -> None:
        generation = self.voice.begin_recording(self.target)
        self.voice.begin_stopping(
            generation,
            self.target,
            target_is_valid=True,
        )
        self.voice.begin_recognition(
            generation,
            self.target,
            target_is_valid=True,
        )
        self.assertTrue(
            self.voice.prepare_paste(
                generation,
                self.target,
                target_is_valid=True,
            )
        )
        self.assertFalse(
            self.voice.consume_paste(
                generation,
                None,
                target_is_valid=False,
            )
        )
        self.assertEqual(self.voice.state, VoiceState.DISCARDED)

    def test_hide_cancel_invalidates_inflight_recognition(self) -> None:
        generation = self.voice.begin_recording(self.target)
        self.voice.begin_stopping(
            generation,
            self.target,
            target_is_valid=True,
        )
        self.voice.begin_recognition(
            generation,
            self.target,
            target_is_valid=True,
        )
        self.assertTrue(self.voice.recognition_is_current(generation))
        self.voice.cancel()
        self.assertEqual(self.voice.state, VoiceState.IDLE)
        self.assertFalse(self.voice.recognition_is_current(generation))
        self.assertFalse(self.voice.fail(generation))

    def test_target_change_during_recording_discards_before_stop_handoff(self) -> None:
        generation = self.voice.begin_recording(self.target)
        changed = ("atspi", ":1.51", "/field/2")

        self.assertFalse(
            self.voice.begin_stopping(
                generation,
                changed,
                target_is_valid=True,
            )
        )
        self.assertEqual(self.voice.state, VoiceState.DISCARDED)
        self.assertFalse(self.voice.recognition_is_current(generation))

    def test_invalid_target_during_auto_stop_discards_before_recognition(self) -> None:
        generation = self.voice.begin_recording(self.target)

        self.assertFalse(
            self.voice.begin_stopping(
                generation,
                self.target,
                target_is_valid=False,
            )
        )
        self.assertEqual(self.voice.state, VoiceState.DISCARDED)

    def test_target_change_while_reaping_discards_before_asr_thread(self) -> None:
        generation = self.voice.begin_recording(self.target)
        self.assertTrue(
            self.voice.begin_stopping(
                generation,
                self.target,
                target_is_valid=True,
            )
        )
        changed = ("atspi", ":1.51", "/field/2")

        self.assertFalse(
            self.voice.begin_recognition(
                generation,
                changed,
                target_is_valid=True,
            )
        )
        self.assertEqual(self.voice.state, VoiceState.DISCARDED)
        self.assertFalse(self.voice.recognition_is_current(generation))

    def test_cancel_stopping_invalidates_late_reaper_callback(self) -> None:
        generation = self.voice.begin_recording(self.target)
        self.assertTrue(
            self.voice.begin_stopping(
                generation,
                self.target,
                target_is_valid=True,
            )
        )
        self.voice.cancel()

        self.assertFalse(
            self.voice.begin_recognition(
                generation,
                self.target,
                target_is_valid=True,
            )
        )
        self.assertEqual(self.voice.state, VoiceState.IDLE)


class PrimaryShiftInteractionTests(unittest.TestCase):
    @staticmethod
    def make_app():
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        app.shifted = False
        app.shift_held = False
        app.shift_consumed = False
        app.shift_latched_before_press = False
        app.shift_release_source = 0
        app.primary_buttons = []
        app.shift_buttons = []
        app.raw_touch_contacts = {}
        app.raw_touch_generation = 7
        app.raw_touch = types.SimpleNamespace(is_current=lambda generation: generation == 7)
        app.raw_touch_enabled = True
        emitted = []
        app.type_character = emitted.append
        return app, emitted

    def test_tapped_shift_latches_one_symbol_then_resets(self) -> None:
        app, emitted = self.make_app()
        app.shift_pressed(None)
        app.shift_clicked(None)
        self.assertTrue(app.shifted)
        app.type_primary_character("1")
        self.assertEqual(emitted, ["!"])
        self.assertFalse(app.shifted)

    def test_held_shift_modifies_second_touch_until_release(self) -> None:
        app, emitted = self.make_app()
        app.shift_pressed(None)
        self.assertTrue(app.shift_held)
        app.type_primary_character("2")
        self.assertEqual(emitted, ["@"])
        self.assertTrue(app.shift_consumed)
        app.shift_clicked(None)
        self.assertFalse(app.shift_held)
        self.assertFalse(app.shifted)

    def test_shift_updates_letters_and_punctuation_from_same_map(self) -> None:
        app, _emitted = self.make_app()
        app.shifted = True
        self.assertEqual(app.primary_character_label("a"), "A")
        self.assertEqual(app.primary_character_label("/"), "?")
        self.assertEqual(app.primary_character_label("'"), '"')

    def configure_raw_touch_runtime(self, app):
        app.window = types.SimpleNamespace(
            get_allocated_width=lambda: 820,
            get_allocated_height=lambda: 615,
        )
        shift_button = types.SimpleNamespace(
            translate_coordinates=lambda *_args: (0, 20),
            get_allocated_width=lambda: 100,
            get_allocated_height=lambda: 50,
        )
        primary_style_classes = set()
        primary_button = types.SimpleNamespace(
            translate_coordinates=lambda *_args: (200, 20),
            get_allocated_width=lambda: 100,
            get_allocated_height=lambda: 50,
            set_label=lambda _label: None,
            get_style_context=lambda: types.SimpleNamespace(
                add_class=primary_style_classes.add,
                remove_class=primary_style_classes.discard,
            ),
            style_classes=primary_style_classes,
        )
        app.visibility = types.SimpleNamespace(visible=True)
        app.raw_touch_generation = 7
        app.raw_touch = types.SimpleNamespace(is_current=lambda generation: generation == 7)
        app.raw_touch_enabled = True
        app.raw_touch_contacts = {}
        app.backspace_buttons = []
        app.backspace_repeat = types.SimpleNamespace(cancel=lambda: None)
        app.backspace_clear = types.SimpleNamespace(cancel=lambda: None)
        app.shifted = False
        app.shift_held = False
        app.shift_consumed = False
        app.shift_latched_before_press = False
        app.shift_release_source = 0
        app.shift_buttons = [shift_button]
        app.primary_buttons = [(primary_button, "2")]
        app.update_shift_visuals = lambda: None
        emitted = []
        app.type_character = emitted.append
        return emitted

    @staticmethod
    def raw_action(kind, contact=None, x=0.0, y=0.0):
        return types.SimpleNamespace(
            kind=kind,
            contact=contact,
            x=x,
            y=y,
        )

    def test_raw_coordinates_scale_to_actual_window_allocation(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        self.configure_raw_touch_runtime(app)
        self.assertEqual(app.raw_to_window_point(1024, 768), (820.0, 615.0))

    def test_raw_single_touch_never_duplicates_gtk_click(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        emitted = self.configure_raw_touch_runtime(app)
        app.on_raw_touch(
            7,
            [self.raw_action("begin", "digit", 300, 40)],
        )
        app.on_raw_touch(
            7,
            [self.raw_action("end", "digit", 300, 40)],
        )
        self.assertEqual(emitted, [])

    def test_raw_shift_then_secondary_touch_commits_once(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        emitted = self.configure_raw_touch_runtime(app)
        app.shift_held = True
        app.on_raw_touch(7, [self.raw_action("begin", "shift", 40, 40)])
        app.on_raw_touch(7, [self.raw_action("begin", "digit", 300, 40)])
        self.assertIn("raw-pressed", app.primary_buttons[0][0].style_classes)
        self.assertTrue(app.shift_consumed)
        app.on_raw_touch(7, [self.raw_action("end", "digit", 300, 40)])
        self.assertNotIn("raw-pressed", app.primary_buttons[0][0].style_classes)
        app.on_raw_touch(7, [self.raw_action("end", "digit", 300, 40)])
        self.assertEqual(emitted, ["@"])
        app.on_raw_touch(7, [self.raw_action("end", "shift", 40, 40)])
        app.shift_clicked(None)
        self.assertFalse(app.shifted)
        self.assertFalse(app.shift_held)
        self.assertFalse(app.shift_consumed)
        self.assertEqual(app.raw_touch_contacts, {})

    def configure_raw_backspace_runtime(self, app):
        self.configure_raw_touch_runtime(app)
        backspace_button = types.SimpleNamespace(
            translate_coordinates=lambda *_args: (500, 300),
            get_allocated_width=lambda: 100,
            get_allocated_height=lambda: 60,
        )
        app.backspace_buttons = [backspace_button]
        clock = FakeScheduler()
        feedback = []
        clears = []
        app.backspace_clear = BackspaceClearGestureController(
            clock,
            lambda: True,
            feedback.append,
            lambda: clears.append("clear"),
            hold_delay_ms=100,
            upward_distance=50,
        )
        return clock, feedback, clears

    def test_raw_backspace_hold_swipe_release_commits_one_clear(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        clock, feedback, clears = self.configure_raw_backspace_runtime(app)

        app.on_raw_touch(
            7,
            [self.raw_action("begin", "backspace", 650, 400)],
        )
        clock.advance(100)
        app.on_raw_touch(
            7,
            [self.raw_action("update", "backspace", 650, 300)],
        )
        app.on_raw_touch(
            7,
            [self.raw_action("end", "backspace", 650, 300)],
        )

        self.assertEqual(clears, ["clear"])
        self.assertEqual(feedback, ["held", "ready", "idle"])
        self.assertEqual(app.raw_touch_contacts, {})

    def test_second_raw_contact_cancels_backspace_clear_fail_closed(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        clock, feedback, clears = self.configure_raw_backspace_runtime(app)

        app.on_raw_touch(
            7,
            [self.raw_action("begin", "backspace", 650, 400)],
        )
        clock.advance(100)
        app.on_raw_touch(
            7,
            [self.raw_action("update", "backspace", 650, 300)],
        )
        app.on_raw_touch(
            7,
            [self.raw_action("begin", "second", 300, 40)],
        )
        app.on_raw_touch(
            7,
            [self.raw_action("end", "backspace", 650, 300)],
        )

        self.assertEqual(clears, [])
        self.assertEqual(feedback, ["held", "ready", "idle"])

    def test_clear_current_input_selects_all_then_deletes(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        actions = []
        app.cancel_backspace_repeat = lambda: actions.append("cancel-repeat")
        app.emit_hotkey = lambda modifiers, key: actions.append(
            ("hotkey", modifiers, key)
        )
        app.emit_key = lambda key: actions.append(("key", key))

        app.clear_current_input()

        self.assertEqual(
            actions,
            [
                "cancel-repeat",
                ("hotkey", (runtime.ecodes.KEY_LEFTCTRL,), runtime.ecodes.KEY_A),
                ("key", runtime.ecodes.KEY_BACKSPACE),
            ],
        )

    def test_raw_secondary_keeps_resolved_symbol_when_shift_releases_first(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        emitted = self.configure_raw_touch_runtime(app)
        app.shift_held = True
        app.on_raw_touch(7, [self.raw_action("begin", "shift", 40, 40)])
        app.on_raw_touch(7, [self.raw_action("begin", "digit", 300, 40)])
        app.on_raw_touch(7, [self.raw_action("end", "shift", 40, 40)])
        app.shift_clicked(None)
        app.on_raw_touch(7, [self.raw_action("end", "digit", 300, 40)])
        self.assertEqual(emitted, ["@"])
        self.assertFalse(app.shifted)
        self.assertFalse(app.shift_held)
        self.assertFalse(app.shift_consumed)
        self.assertEqual(app.raw_touch_contacts, {})

    def test_raw_same_frame_and_release_outside_fail_closed(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        emitted = self.configure_raw_touch_runtime(app)
        app.shift_held = True
        app.on_raw_touch(
            7,
            [
                self.raw_action("begin", "shift", 40, 40),
                self.raw_action("begin", "same-frame", 300, 40),
            ],
        )
        app.on_raw_touch(7, [self.raw_action("end", "same-frame", 300, 40)])
        self.assertEqual(emitted, [])
        app.on_raw_touch(7, [self.raw_action("begin", "digit", 300, 40)])
        self.assertIn("raw-pressed", app.primary_buttons[0][0].style_classes)
        app.on_raw_touch(7, [self.raw_action("end", "digit", 420, 40)])
        self.assertNotIn("raw-pressed", app.primary_buttons[0][0].style_classes)
        self.assertEqual(emitted, [])
        self.assertTrue(app.shift_consumed)

    def test_raw_stale_generation_and_cancel_never_commit(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        emitted = self.configure_raw_touch_runtime(app)
        app.shift_held = True
        app.on_raw_touch(6, [self.raw_action("begin", "stale", 40, 40)])
        self.assertEqual(app.raw_touch_contacts, {})
        app.on_raw_touch(7, [self.raw_action("begin", "shift", 40, 40)])
        app.on_raw_touch(7, [self.raw_action("begin", "digit", 300, 40)])
        self.assertIn("raw-pressed", app.primary_buttons[0][0].style_classes)
        app.on_raw_touch(7, [self.raw_action("cancel")])
        self.assertNotIn("raw-pressed", app.primary_buttons[0][0].style_classes)
        app.on_raw_touch(7, [self.raw_action("end", "digit", 300, 40)])
        self.assertEqual(emitted, [])
        self.assertEqual(app.raw_touch_contacts, {})

    def test_reset_shift_state_drops_latched_modifier(self):
        app, _emitted = self.make_app()
        app.shifted = True
        app.reset_shift_state()
        self.assertFalse(app.shifted)
        self.assertFalse(app.shift_held)


class ActiveWindowVoiceFallbackTests(unittest.TestCase):
    @staticmethod
    def make_app():
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        app.voice = runtime.VoiceSessionController()
        app.voice_fallback_window = None
        app.visibility = types.SimpleNamespace(
            focused_editable=None,
            snapshot=lambda: types.SimpleNamespace(
                visible=True,
                editable_focus_id=None,
                generation=17,
            ),
        )
        return runtime, app

    def test_missing_editable_uses_one_application_agnostic_active_window(self):
        _runtime, app = self.make_app()
        window = ("atspi-window", ":1.74", ":1.74", "/window/1")
        app.find_active_accessible_window_target = lambda: window
        app.active_accessible_window_target_is_valid = lambda target: target == window

        target, valid = app.current_voice_target()

        self.assertEqual(target, window)
        self.assertTrue(valid)
        self.assertEqual(app.voice_fallback_window, window)

    def test_busy_fallback_revalidates_exact_window_without_rediscovery(self):
        _runtime, app = self.make_app()
        window = ("atspi-window", ":1.74", ":1.74", "/window/1")
        app.voice_fallback_window = window
        app.voice.begin_connecting(window)
        app.find_active_accessible_window_target = lambda: self.fail(
            "busy sessions must not switch to a newly discovered window"
        )
        active = True
        app.active_accessible_window_target_is_valid = lambda target: (
            target == window and active
        )

        target, valid = app.current_voice_target()
        self.assertEqual(target, window)
        self.assertTrue(valid)

        active = False
        target, valid = app.current_voice_target()
        self.assertEqual(target, window)
        self.assertFalse(valid)

    def test_hidden_keyboard_never_authorizes_fallback(self):
        _runtime, app = self.make_app()
        app.visibility.snapshot = lambda: types.SimpleNamespace(
            visible=False, editable_focus_id=None, generation=18
        )
        app.find_active_accessible_window_target = lambda: self.fail(
            "hidden keyboard must not inspect an active window"
        )

        self.assertEqual(app.current_voice_target(), (None, False))


class AccessibleIdentityTests(unittest.TestCase):
    def test_application_bus_name_is_stable_identity(self) -> None:
        source = FakeAccessible(":1.42", "/org/a11y/atspi/accessible/9")
        self.assertEqual(
            stable_accessible_application_id(source),
            ("atspi-app", ":1.42"),
        )

    def test_bus_name_and_object_path_are_primary_identity(self) -> None:
        source = FakeAccessible(":1.42", "/org/a11y/atspi/accessible/9")
        self.assertEqual(
            stable_accessible_id(source),
            ("atspi", ":1.42", "/org/a11y/atspi/accessible/9"),
        )
        self.assertEqual(
            trusted_accessible_id(source),
            ("atspi", ":1.42", "/org/a11y/atspi/accessible/9"),
        )

    def test_hash_fallback_is_safe_when_proxy_metadata_is_unavailable(self) -> None:
        class BrokenMetadata:
            @property
            def app(self):
                raise RuntimeError("proxy disappeared")

            def __hash__(self):
                return 73

        identity = stable_accessible_id(BrokenMetadata())
        self.assertEqual(identity[0], "atspi-hash")
        self.assertEqual(identity[-1], 73)
        self.assertIsNone(trusted_accessible_id(BrokenMetadata()))

    def test_process_object_fallback_handles_unhashable_proxy(self) -> None:
        class UnhashableProxy:
            __hash__ = None
            app = None
            path = None

        source = UnhashableProxy()
        identity = stable_accessible_id(source)
        self.assertEqual(identity[0], "process-object")
        self.assertEqual(identity[-1], id(source))
        self.assertIsNone(trusted_accessible_id(source))

    def test_application_identity_fails_closed_without_metadata(self) -> None:
        self.assertIsNone(stable_accessible_application_id(object()))


class SignalReadinessTests(unittest.TestCase):
    def test_one_pre_ready_signal_drains_once(self) -> None:
        gate = SignalToggleReadinessGate()
        calls = []
        gate.request_toggle()
        gate.mark_ready(lambda: calls.append("toggle"))
        self.assertEqual(calls, ["toggle"])

    def test_two_pre_ready_signals_cancel_by_toggle_parity(self) -> None:
        gate = SignalToggleReadinessGate()
        calls = []
        gate.request_toggle()
        gate.request_toggle()
        gate.mark_ready(lambda: calls.append("toggle"))
        self.assertEqual(calls, [])

    def test_ready_signal_is_queued_immediately(self) -> None:
        gate = SignalToggleReadinessGate()
        calls = []
        gate.mark_ready(lambda: calls.append("toggle"))
        gate.request_toggle()
        gate.request_toggle()
        self.assertEqual(calls, ["toggle", "toggle"])


class QuickToolbarInteractionTests(unittest.TestCase):
    def make_window_transfer_app(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        app.window_transfer_button = mock.Mock()
        app.window_transfer_busy = False
        app.window_transfer_failed = False
        app.window_transfer_feedback_source = 0
        app.window_transfer_closing = threading.Event()
        # The toolbar is a touch/mouse action, like Desktop and Terminal.
        # A root-owned hardware gate is unreadable in the keyboard's private
        # user namespace and must not silently disable this toolbar action.
        runtime.hardware_actions_blocked = lambda: True
        app.manual_hide = mock.Mock()
        return runtime, app

    def test_window_transfer_is_single_flight_and_keeps_keyboard_visible(self):
        runtime, app = self.make_window_transfer_app()
        workers, completions = [], []
        runtime.GLib.idle_add = lambda *args: completions.append(args)
        runtime.move_lower_windows_to_upper = mock.Mock(return_value=True)
        def make_thread(**kwargs):
            return types.SimpleNamespace(start=lambda: workers.append(kwargs["target"]))
        with mock.patch.object(runtime.threading, "Thread", side_effect=make_thread):
            app.move_app_windows_up()
            app.move_app_windows_up()
        self.assertEqual(len(workers), 1)
        runtime.move_lower_windows_to_upper.assert_not_called()
        app.window_transfer_button.set_sensitive.assert_called_with(False)
        workers.pop()()
        self.assertTrue(app.window_transfer_busy)
        callback, submitted = completions.pop()
        # A keyboard/symbol layout rebuild can replace the button while waiting.
        old_button = app.window_transfer_button
        app.window_transfer_button = mock.Mock()
        callback(submitted)
        self.assertFalse(app.window_transfer_busy)
        old_button.set_sensitive.assert_called_with(False)
        app.window_transfer_button.set_sensitive.assert_called_with(True)
        app.window_transfer_button.set_label.assert_called_with("移上屏")
        app.manual_hide.assert_not_called()

    def test_window_transfer_failure_allows_retry_and_clears_stale_feedback(self):
        runtime, app = self.make_window_transfer_app()
        runtime.GLib.timeout_add = mock.Mock(return_value=77)
        runtime.GLib.source_remove = mock.Mock()
        app.window_transfer_busy = True
        app.finish_window_transfer(False)
        app.window_transfer_button.set_label.assert_called_with("重试")
        self.assertFalse(app.window_transfer_busy)
        with mock.patch.object(runtime.threading, "Thread"):
            app.move_app_windows_up()
        runtime.GLib.source_remove.assert_called_once_with(77)
        self.assertEqual(app.window_transfer_feedback_source, 0)
        self.assertTrue(app.window_transfer_busy)
        self.assertFalse(app.window_transfer_failed)

    def test_window_transfer_cancels_queued_work_and_ui_after_shutdown(self):
        runtime, app = self.make_window_transfer_app()
        runtime.move_lower_windows_to_upper = mock.Mock()
        runtime.GLib.idle_add = mock.Mock()
        with mock.patch.object(runtime.threading, "Thread") as thread:
            app.window_transfer_closing.set()
            app.move_app_windows_up()
            thread.assert_not_called()
        app.request_window_transfer()
        app.finish_window_transfer(True)
        runtime.move_lower_windows_to_upper.assert_not_called()
        runtime.GLib.idle_add.assert_not_called()
        app.window_transfer_button.set_label.assert_not_called()

    def test_terminal_launch_uses_user_manager_and_a_separate_process(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        launches = []
        runtime.GLib.spawn_async = lambda command: launches.append(tuple(command))

        self.assertFalse(app.finish_quick_app_launch(
            "terminal", runtime.QUICK_APP_COMMANDS["terminal"], None
        ))

        self.assertEqual(launches, [(
            "/usr/bin/systemd-run", "--user", "--quiet", "--collect",
            "--service-type=exec", "--", "/usr/bin/konsole", "--separate",
        )])

    def test_terminal_launch_failure_cleans_placement_without_direct_fallback(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        placement = ("test-terminal", Path("/run/user/1000/test-terminal.js"))
        app.terminal_placement_lock = threading.Lock()
        app.terminal_closing = threading.Event()
        app.terminal_pending_placements = {placement[0]: placement[1]}
        app.quick_action_not_before = {"terminal": 10}
        cleanup = []
        app.start_terminal_cleanup = lambda *args: cleanup.append(args)
        launch = mock.Mock(side_effect=OSError("launcher unavailable"))
        runtime.GLib.spawn_async = launch

        self.assertFalse(app.finish_quick_app_launch(
            "terminal", runtime.QUICK_APP_COMMANDS["terminal"], placement
        ))

        self.assertEqual(launch.call_count, 1)
        self.assertEqual(cleanup, [placement])
        self.assertEqual(app.terminal_pending_placements, {})
        self.assertNotIn("terminal", app.quick_action_not_before)

    def test_touchpad_mode_suppresses_editable_focus_popup(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        gained = []
        hidden = []
        app.visibility = types.SimpleNamespace(
            visible=False,
            editable_focus_gained=lambda source: gained.append(source),
        )
        app.touchpad_overlay_visible = lambda: True
        app.accessible_source_is_editable = lambda _source: True
        app.manual_hide = lambda: hidden.append(True)
        source = types.SimpleNamespace(get_process_id=lambda: os.getpid() + 1)

        app.on_accessible_focus(types.SimpleNamespace(detail1=1, source=source))

        self.assertEqual(gained, [])
        self.assertEqual(hidden, [])

    def test_touchpad_mode_hides_already_visible_keyboard(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        hidden = []
        app.visibility = types.SimpleNamespace(visible=True)
        app.touchpad_overlay_visible = lambda: True
        app.manual_hide = lambda: hidden.append(True)
        source = types.SimpleNamespace(get_process_id=lambda: os.getpid() + 1)

        app.on_accessible_focus(types.SimpleNamespace(detail1=1, source=source))

        self.assertEqual(hidden, [True])

    def test_touchpad_mode_suppresses_focus_watchdog_popup(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        calls = []
        app.visibility = types.SimpleNamespace(
            visible=False,
            revalidate_focus=lambda: calls.append("revalidate"),
        )
        app.touchpad_overlay_visible = lambda: True
        app.manual_hide = lambda: calls.append("hide")
        app.cancel_voice_if_target_changed = lambda reason: calls.append(reason)

        self.assertTrue(app.revalidate_accessible_focus())
        self.assertEqual(calls, [])

    def test_removed_browser_shortcut_does_not_launch_an_application(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        app.quick_action_not_before = {}
        launches = []
        runtime.GLib.spawn_async = lambda command: launches.append(tuple(command))

        with mock.patch.object(
            runtime.time,
            "monotonic",
            side_effect=(10.0, 10.2, 11.0),
        ):
            self.assertFalse(app.launch_quick_app("browser"))
            self.assertFalse(app.launch_quick_app("browser"))
            self.assertFalse(app.launch_quick_app("browser"))

        self.assertEqual(launches, [])

    def test_alt_f4_emits_one_complete_hotkey(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        emissions = []
        app.emit_hotkey = lambda modifiers, key: emissions.append(
            (tuple(modifiers), key)
        )

        self.assertFalse(app.close_foreground_window())

        self.assertEqual(
            emissions,
            [((runtime.ecodes.KEY_LEFTALT,), runtime.ecodes.KEY_F4)],
        )

    def test_show_desktop_hides_keyboard_then_requests_one_way_desktop(self):
        runtime = load_keyboard_runtime_for_capture_tests()
        app = object.__new__(runtime.PocketDSKeyboard)
        calls = []

        class FakeKWin:
            def showDesktop(self, visible):
                calls.append(("show-desktop", bool(visible)))

        app.manual_hide = lambda: calls.append(("hide-keyboard", True))
        app.session_bus = types.SimpleNamespace(
            get_object=lambda service, path: (service, path)
        )
        runtime.dbus.Interface = lambda _obj, _name: FakeKWin()

        self.assertFalse(app.show_desktop())

        self.assertEqual(
            calls,
            [("hide-keyboard", True), ("show-desktop", True)],
        )


class KeyboardSourceStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = KEYBOARD_COMPONENT / "pocketds-keyboard.py"
        cls.source = cls.path.read_text()
        cls.tree = ast.parse(cls.source)

    def test_old_competing_visibility_fields_are_deleted(self) -> None:
        for old_name in (
            "manual_hidden",
            "hide_source",
            "focus_validation_source",
        ):
            self.assertNotIn(old_name, self.source)

    def test_signal_gate_is_installed_before_heavy_external_imports(self) -> None:
        signal_line = None
        external_import_lines = []
        for node in self.tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                call = node.value.func
                if isinstance(call, ast.Attribute) and call.attr == "signal":
                    signal_line = node.lineno
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                if any(name.split(".")[0] in {"dbus", "evdev", "gi", "pyatspi"} for name in names):
                    external_import_lines.append(node.lineno)

        self.assertIsNotNone(signal_line)
        self.assertLess(signal_line, min(external_import_lines))

    def test_window_visibility_and_geometry_have_single_owners(self) -> None:
        calls: dict[str, list[str]] = {
            "show_all": [],
            "hide": [],
            "resize": [],
            "move": [],
        }
        for function in (
            node for node in ast.walk(self.tree) if isinstance(node, ast.FunctionDef)
        ):
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr in calls:
                        calls[node.func.attr].append(function.name)

        self.assertEqual(calls["show_all"], ["apply_visibility_snapshot"])
        self.assertEqual(calls["hide"], ["apply_visibility_snapshot"])
        self.assertEqual(calls["resize"], [])
        self.assertEqual(calls["move"], [])
        self.assertIn("KWin's forced DSI-2 rule is the sole geometry owner", self.source)

    def test_runtime_passes_noneditable_event_source_to_adapter(self) -> None:
        self.assertIn(
            "self.visibility.noneditable_focus_gained(event.source)",
            self.source,
        )
        self.assertIn(
            "accessible_process_id(event.source) == os.getpid()",
            self.source,
        )

    def test_runtime_periodically_revalidates_silent_focus_loss(self) -> None:
        self.assertIn("FOCUS_REVALIDATE_MS = 500", self.source)
        self.assertIn("self.visibility.revalidate_focus()", self.source)
        self.assertIn(
            "FOCUS_REVALIDATE_MS, self.revalidate_accessible_focus",
            self.source,
        )

    def test_touch_layout_uses_dynamic_geometry_and_distinct_row_density(self) -> None:
        for stale_constant in ("WINDOW_W", "WINDOW_H", "WINDOW_X", "WINDOW_Y"):
            self.assertNotIn(stale_constant, self.source)
        self.assertNotIn("set_size_request", self.source)
        self.assertNotIn("GEOMETRY_REVALIDATE_MS", self.source)
        self.assertNotIn("load_lower_screen_geometry", self.source)
        self.assertNotIn("self.window.resize", self.source)
        self.assertNotIn("self.window.move", self.source)
        self.assertIn("self.window.set_resizable(False)", self.source)
        self.assertNotIn("self.window.set_resizable(True)", self.source)
        self.assertIn("Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)", self.source)
        self.assertIn("grid.set_row_homogeneous(False)", self.source)
        self.assertIn("button.set_vexpand(vexpand)", self.source)
        self.assertIn(
            "self.button(label, activate, style_class, vexpand=False)", self.source
        )
        self.assertIn("toolbar.set_vexpand(False)", self.source)
        self.assertIn("min-height: 78px", self.source)
        self.assertIn("button.action", self.source)
        self.assertIn("min-height: 60px", self.source)
        self.assertIn("button.utility", self.source)
        self.assertIn("min-height: 72px", self.source)
        self.assertIn("toolbar.set_column_spacing(10)", self.source)
        self.assertIn("grid.set_column_spacing(10)", self.source)
        self.assertIn("grid.set_row_spacing(10)", self.source)
        self.assertNotIn("button.function", self.source)
        for label in (
            '"Shift",',
            'self.button("Backspace"',
            '"面板",',
            "self.manual_hide,",
            '"触摸板",',
            "self.open_touchpad,",
        ):
            self.assertIn(label, self.source)

    def test_daily_layout_keeps_punctuation_and_compact_navigation_reachable(self) -> None:
        for token in (
            '"up": ("go-up-symbolic", "上", ecodes.KEY_UP)',
            '"left": ("go-previous-symbolic", "左", ecodes.KEY_LEFT)',
            '"down": ("go-down-symbolic", "下", ecodes.KEY_DOWN)',
            '"right": ("go-next-symbolic", "右", ecodes.KEY_RIGHT)',
            "| {spec[2] for spec in NAVIGATION_KEYS.values()}",
            'SHIFTED_CHARACTERS = {',
            "def shift_pressed(self, _button):",
            "def shift_released(self, _button):",
            "def shift_clicked(self, _button):",
            "self.type_character(self.primary_character_label(char))",
            "self.shift_consumed = True",
            "self.update_shift_visuals()",
            "class RawKeyboardTouchReader:",
            "/dev/input/by-path/platform-a88000.i2c-event",
            'device.name != "Goodix Capacitive TouchScreen"',
            "TypeBTouchFrame()",
            "GLib.idle_add(self.on_raw_touch, generation, tuple(actions))",
            "x * float(width) / LOWER_TOUCH_WIDTH",
            "y * float(height) / LOWER_TOUCH_HEIGHT",
            "button.translate_coordinates(self.window, 0, 0)",
            "button.get_allocated_width()",
            "anchors_at_frame_start",
            'target[0] == "shift"',
            'kind == "primary" and anchor_active and self.shift_held',
            "self.type_character(resolved)",
            "def cancel_raw_touches(self):",
            "def set_raw_touch_enabled(self, enabled):",
            "def reset_shift_state(self):",
            'grid.attach(self.primary_character_button(":"), 48, 2, 6, 1)',
            'self.button("Enter", self.send_enter, "key action")',
            "7 + col * 5",
            'for col, char in enumerate((".", "/"))',
            'grid.attach(self.backspace_button(), 50, 3, 10, 1)',
            'grid.attach(self.backspace_button(), 47, 3, 13, 1)',
            "def navigation_cluster(self):",
            'cluster.attach(self.navigation_button("left"), 0, 0, 1, 2)',
            'cluster.attach(self.navigation_button("up", half=True), 1, 0, 1, 1)',
            'cluster.attach(self.navigation_button("down", half=True), 1, 1, 1, 1)',
            'cluster.attach(self.navigation_button("right"), 2, 0, 1, 2)',
            "button.direction-half",
            'grid.attach(self.space_voice_button(), 14, 4, 30, 1)',
            'grid.attach(self.navigation_cluster(), 44, 4, 16, 1)',
        ):
            self.assertIn(token, self.source)
        self.assertNotIn('grid.attach(self.navigation_button("up")', self.source)
        self.assertNotIn(
            'self.button(".", lambda _b: self.type_character("."), "action")',
            self.source,
        )
        self.assertNotIn(
            'self.button("/", lambda _b: self.type_character("/"), "action")',
            self.source,
        )
        self.assertNotIn(".grab()", self.source)
        self.assertNotIn(
            'grid.attach(self.primary_character_button("\'"),', self.source
        )
        self.assertNotIn(
            'for col, char in enumerate((",", ".", "/"))', self.source
        )
        self.assertNotIn('self.button("语音", self.toggle_voice', self.source)
        for number, symbol in zip("1234567890", "!@#$%^&*()"):
            self.assertIn(f'"{number}": "{symbol}"', self.source)

    def test_quick_toolbar_keeps_system_actions_bounded_and_reachable(self) -> None:
        for token in (
            '"terminal": ("/usr/bin/konsole", "--separate")',
            '"Esc"',
            '"Tab"',
            '"面板"',
            '"触摸板"',
            '"桌面"',
            '"设置"',
            '"终端"',
            '"Alt+F4"',
            "def open_feedback_settings(self, _button=None):",
            "self.terminal_pending_placements = {}",
            "self.terminal_closing = threading.Event()",
            "self.terminal_pending_placements[script_name] = script_path",
            "self.terminal_closing.set()",
            "pending = tuple(self.terminal_pending_placements.items())",
            "FEEDBACK_SETTINGS_PATH",
            "ecodes.KEY_ESC",
            "ecodes.KEY_TAB",
            "ecodes.KEY_LEFTALT",
            "ecodes.KEY_F4",
            "def close_foreground_window(self, _button=None):",
            "self.emit_hotkey((ecodes.KEY_LEFTALT,), ecodes.KEY_F4)",
            "QUICK_APP_DEBOUNCE_S = 0.9",
            'self.session_bus.get_object("org.kde.KWin", "/KWin")',
            'interface.showDesktop(dbus.Boolean(True))',
            "shell.pack_start(self.quick_toolbar(), False, False, 0)",
        ):
            self.assertIn(token, self.source)
        self.assertNotIn('"browser": "chromium-browser"', self.source)
        self.assertNotIn('"浏览器"', self.source)
        self.assertIn('allowedIds = ["org.kde.konsole", "konsole"]', self.source)
        self.assertNotIn("workspace.activeWindow", self.source)
        self.assertNotIn("FUNCTION_KEYS", self.source)
        self.assertNotIn("for col, number in enumerate(range(1, 13))", self.source)
        self.assertNotIn(
            'GLib.spawn_async(["/usr/bin/chromium-browser"',
            self.source,
        )
        self.assertNotIn("set_tooltip_text", self.source)
        for accessible_label in (
            'button.get_accessible().set_name(accessible_name)',
            'button.get_accessible().set_name("空格；按住说话；松手识别")',
        ):
            self.assertIn(accessible_label, self.source)

    def test_space_long_press_reuses_voice_state_without_accidental_space(self) -> None:
        for token in (
            "HoldTapController(",
            "hold_delay_ms=SPACE_HOLD_MS",
            "self.space_voice_released,",
            "self.space_hold.press()",
            "self.space_hold.release()",
            "self.space_hold.click()",
            "self.space_hold.cancel()",
            'VoiceState.IDLE: "按住说话"',
            'VoiceState.RECORDING: "请说话 · 松手识别"',
            'button.get_accessible().set_name("空格；按住说话；松手识别")',
            "generation = self.start_recording()",
            "self.space_voice_release_generation = generation",
            "GLib.idle_add(",
            "self.finish_deferred_space_voice,",
            "self.clear_space_voice_session(snapshot.generation)",
        ):
            self.assertIn(token, self.source)
        self.assertNotIn("if snapshot.state is VoiceState.RECORDING:\n            self.toggle_voice(None)", self.source)
        for function_name in ("build_layout", "manual_hide", "run"):
            start = self.source.index(f"    def {function_name}(")
            following = self.source.find("\n    def ", start + 1)
            body = self.source[start : following if following >= 0 else None]
            self.assertIn("self.cancel_space_hold()", body)

    def test_backspace_repeat_and_clear_share_cancel_paths(self) -> None:
        self.assertNotIn("repeat_source", self.source)
        for token in (
            "BackspaceClearGestureController(",
            "hold_delay_ms=BACKSPACE_CLEAR_HOLD_MS",
            "upward_distance=BACKSPACE_CLEAR_DISTANCE",
            'return "backspace", button, None',
            'action.kind == "update"',
            "self.backspace_clear.begin(",
            "self.backspace_clear.update(action.contact, *point)",
            "self.backspace_clear.end(action.contact, *point)",
            'button.connect("grab-broken-event", self.backspace_cancelled)',
            'button.connect("touch-event", self.backspace_touch_event)',
            '"Backspace; hold, swipe up, and release to clear the current input"',
            "def clear_current_input(self):",
            "self.emit_hotkey((ecodes.KEY_LEFTCTRL,), ecodes.KEY_A)",
            "self.emit_key(ecodes.KEY_BACKSPACE)",
            "if event.type == Gdk.EventType.TOUCH_CANCEL:",
            "def cancel_backspace_repeat(self):",
            "def cancel_backspace_interaction(self):",
            'repeat = getattr(self, "backspace_repeat", None)',
            'clear_gesture = getattr(self, "backspace_clear", None)',
        ):
            self.assertIn(token, self.source)
        for function_name in ("build_layout", "manual_hide", "on_delete", "run"):
            start = self.source.index(f"    def {function_name}(")
            following = self.source.find("\n    def ", start + 1)
            body = self.source[start : following if following >= 0 else None]
            self.assertIn("self.cancel_backspace_interaction()", body)

    def test_key_sound_is_user_feedback_not_synthetic_output_noise(self) -> None:
        for token in (
            "KeySoundFeedback(",
            "def play_key_sound(self, *, modifier=False):",
            "def emit_key_with_feedback(self, code, shifted=False):",
            "self.play_key_sound(modifier=True)",
            "if self.backspace_repeat.start():",
            "KeyHapticFeedback(",
            '"POCKETDS_KEYBOARD_SOUND", "1"',
            '"POCKETDS_KEYBOARD_HAPTIC", "1"',
            "haptic.play(modifier=modifier)",
            "key_haptic.close()",
            "threads_init()",
            "permission_guard=self.joymouse_haptic_permission",
            'mode == "joymouse"',
            "fcntl.LOCK_SH | fcntl.LOCK_NB",
            'INPUT_MODE_LOCK = Path("/run/pocketds-input-mode.lock")',
        ):
            self.assertIn(token, self.source)
        self.assertNotIn("/dev/hidraw", self.source)
        self.assertNotIn(
            "/dev/hidraw", (KEYBOARD_COMPONENT / "keyboard_adapter.py").read_text()
        )

        for function_name in (
            "emit_key",
            "emit_hotkey",
            "clear_current_input",
            "paste_voice_text",
        ):
            start = self.source.index(f"    def {function_name}(")
            following = self.source.find("\n    def ", start + 1)
            body = self.source[start : following if following >= 0 else None]
            self.assertNotIn("play_key_sound", body)

        repeat_setup = self.source[
            self.source.index("self.backspace_repeat = BoundedRepeatController(") :
            self.source.index("self.backspace_clear = BackspaceClearGestureController(")
        ]
        self.assertIn("lambda: self.emit_key(ecodes.KEY_BACKSPACE)", repeat_setup)
        self.assertNotIn("emit_key_with_feedback", repeat_setup)
        self.assertIn(
            'BackspaceClearGestureController.FEEDBACK_HELD: "Swipe ↑"',
            self.source,
        )
        self.assertIn(
            'BackspaceClearGestureController.FEEDBACK_READY: "Release"',
            self.source,
        )
        self.assertIn("status_stack.set_homogeneous(True)", self.source)
        self.assertIn(
            "status_stack.set_transition_type(Gtk.StackTransitionType.NONE)",
            self.source,
        )
        render_at = self.source.index("    def render_backspace_clear_state(")
        render_end = self.source.index("\n    def ", render_at + 1)
        render_body = self.source[render_at:render_end]
        self.assertIn(
            "status_stack.set_visible_child_name(visible_state)", render_body
        )
        self.assertNotIn("button.set_label", render_body)

    def test_voice_state_survives_layout_rebuild_and_guards_paste_target(self) -> None:
        self.assertIn("VoiceSessionController(self.on_voice_state_changed)", self.source)
        self.assertIn("self.render_voice_snapshot(self.voice.snapshot())", self.source)
        self.assertIn('self.cancel_voice("keyboard hidden")', self.source)
        self.assertIn('self.cancel_voice("shutdown")', self.source)
        self.assertIn("cancelled=cancelled", self.source)
        self.assertIn("source_id = trusted_accessible_id(source)", self.source)
        self.assertIn("snapshot.editable_focus_id == source_id", self.source)
        self.assertIn("(snapshot.generation, source_id)", self.source)
        self.assertIn("def find_active_accessible_window_target(self):", self.source)
        self.assertIn("def active_accessible_window_target_is_valid", self.source)
        self.assertNotIn("/usr/lib/chatgpt", self.source)
        self.assertNotIn("Telegram", self.source)
        self.assertEqual(self.source.count("self.voice.prepare_paste("), 1)
        self.assertEqual(self.source.count("self.voice.consume_paste("), 1)

    def test_capture_requires_pcm_and_is_cancellable(self) -> None:
        self.assertIn("CaptureStartupController(", self.source)
        self.assertIn("private_wav_has_pcm_payload(VOICE_WAV)", self.source)
        self.assertIn("self.voice.begin_connecting(target_id)", self.source)
        self.assertNotIn('VoiceState.CONNECTING: "开始录音"', self.source)
        self.assertIn("self.set_mic(snapshot.state)", self.source)
        self.assertNotIn("self.mic_button.set_label", self.source)
        self.assertIn("CAPTURE_RETRY_COOLDOWN_S = 8.0", self.source)
        self.assertIn("CUSTOM_VOICE_SAFETY_ENABLED = True", self.source)
        self.assertIn("VOICE_STATE_LABELS", self.source)
        self.assertIn('VoiceState.RECORDING: "请说话 · 松手识别"', self.source)
        self.assertIn('VoiceState.RECOGNIZING: "识别中"', self.source)
        self.assertIn("self.mic_status_stack.set_homogeneous(True)", self.source)
        self.assertIn("self.voice_retry_not_before", self.source)
        self.assertIn("self.note_ignored_voice_activation(snapshot)", self.source)
        self.assertNotIn("microphone connection cancelled by user", self.source)
        self.assertNotIn("recording finalization cancelled by user", self.source)
        self.assertIn("self.capture_startup.cancel()", self.source)
        self.assertEqual(
            self.source.count(
                "GLib.timeout_add_seconds(15, self.stop_recording)"
            ),
            1,
        )
        ready_at = self.source.index("    def on_capture_ready(self, process):")
        timeout_at = self.source.index(
            "GLib.timeout_add_seconds(15, self.stop_recording)"
        )
        stop_at = self.source.index("    def stop_recording(self):")
        self.assertLess(ready_at, timeout_at)
        self.assertLess(timeout_at, stop_at)
        self.assertNotIn("amixer", self.source)

    def test_capture_ready_publishes_only_after_valid_timer_transaction(self) -> None:
        ready = self.source[
            self.source.index("    def on_capture_ready(self, process):") :
            self.source.index("    def on_capture_failed(self, reason):")
        ]
        transition_at = ready.index("self.voice.capture_ready(")
        timer_create_at = ready.index("timeout_source = GLib.timeout_add_seconds(")
        timer_validate_at = ready.index("type(timeout_source) is not int")
        timer_publish_at = ready.index("self.voice_timeout = timeout_source")
        process_publish_at = ready.index("self.voice_process = process")

        self.assertLess(transition_at, timer_create_at)
        self.assertLess(timer_create_at, timer_validate_at)
        self.assertLess(timer_validate_at, timer_publish_at)
        self.assertLess(timer_publish_at, process_publish_at)
        self.assertTrue(ready.rstrip().endswith("self.voice_process = process"))
        self.assertNotIn("self.stop_startup_recorder(", ready)

    def test_recording_stop_and_pre_asr_both_revalidate_exact_target(self) -> None:
        stop = self.source[
            self.source.index("    def stop_recording(self):") :
            self.source.index("    def on_recording_stop_failed(")
        ]
        stop_target = stop.index("self.current_voice_target()")
        stop_gate = stop.index("self.voice.begin_stopping(")
        stop_handoff = stop.index("self.recorder_stop.request(")
        self.assertLess(stop_target, stop_gate)
        self.assertLess(stop_gate, stop_handoff)
        self.assertNotIn("threading.Thread(", stop)
        self.assertNotIn("transcribe(", stop)

        reaped = self.source[
            self.source.index("    def on_recording_reaped(") :
            self.source.index("    def cancel_voice_timeout(")
        ]
        reaped_target = reaped.index("self.current_voice_target()")
        recognition_gate = reaped.index("self.voice.begin_recognition(")
        thread_create = reaped.index("threading.Thread(")
        thread_start = reaped.index("thread.start()")
        self.assertLess(reaped_target, recognition_gate)
        self.assertLess(recognition_gate, thread_create)
        self.assertLess(thread_create, thread_start)
        self.assertIn("result.may_finalize_interrupted_arecord", reaped)
        self.assertIn("finalize_private_arecord_wav(VOICE_WAV)", reaped)
        self.assertIn("if not recording_is_eligible:", reaped)

        # Button stop and the 15-second auto-stop deliberately share the same
        # target-gated method rather than maintaining two subtly different paths.
        self.assertEqual(
            self.source.count("GLib.timeout_add_seconds(15, self.stop_recording)"),
            1,
        )

    def test_runtime_recorder_reap_never_waits_on_gtk_main_loop(self) -> None:
        self.assertIn("RecorderStopController(scheduler)", self.source)
        self.assertIn("self.recorder_stop.request(", self.source)
        self.assertNotIn("stop_recorder_process", self.source)
        # The evdev worker may wait interruptibly between reconnect attempts;
        # GTK callbacks must still never wait for a recorder or worker.
        ui_source = self.source.split("class PocketDSKeyboard:", 1)[1]
        self.assertNotIn(".wait(", ui_source)

    def test_service_and_panel_wait_for_readiness_before_show(self) -> None:
        service = SERVICE.read_text(encoding="utf-8")
        panel = PANELCTL.read_text(encoding="utf-8")
        self.assertIn("Type=notify", service)
        self.assertIn("NotifyAccess=main", service)
        self.assertIn("ExecReload=/bin/kill -USR1 $MAINPID", service)
        self.assertIn(
            "ReadOnlyPaths=-%h/.config/pocketds-keyboard/asr-api",
            service,
        )
        self.assertNotIn("Authorization=", service)
        self.assertIn("LimitCORE=0", service)
        self.assertIn("CoredumpFilter=0x0", service)
        self.assertIn("RuntimeDirectory=pocketds-keyboard", service)
        self.assertIn("RuntimeDirectoryMode=0700", service)
        self.assertIn("RuntimeDirectoryPreserve=no", service)
        self.assertIn('/ "pocketds-keyboard"', self.source)
        self.assertIn('VOICE_WAV = RUNTIME / "pocketds-voice.wav"', self.source)
        self.assertIn('TOUCHPAD_VISIBLE_STATE = (', self.source)
        self.assertIn("def touchpad_overlay_visible():", self.source)
        self.assertIn("_SIGHUP_GATE.mark_ready(self.queue_manual_show)", self.source)
        self.assertIn("disable_process_dumpability()", self.source)
        self.assertIn('"start", "pocketds-keyboard.service"', panel)
        self.assertIn('show_overlay("pocketds-keyboard.service")', panel)
        self.assertIn('"--signal=HUP"', panel)
        self.assertIn('"kill", "--kill-whom=main"', panel)
        self.assertIn('"--signal=USR2"', panel)

    def test_installer_deploys_all_keyboard_modules_before_restart(self) -> None:
        installer = INSTALLER.read_text(encoding="utf-8")
        transaction = (
            ROOT / "scripts" / "install-asr-api.sh"
        ).read_text(encoding="utf-8")
        apply_at = installer.index("--apply \"$asr_transaction\" --activate")
        verify_at = installer.index("--verify \"$asr_transaction\"")
        enable_at = installer.index("enable pocketds-keyboard.service")
        self.assertLess(apply_at, verify_at)
        self.assertLess(verify_at, enable_at)
        self.assertNotIn("restart pocketds-keyboard.service", installer)
        for source in (
            "components/keyboard/pocketds-keyboard.py",
            "components/keyboard/visibility_state.py",
            "components/keyboard/keyboard_adapter.py",
            "components/keyboard/screen_geometry.py",
            "components/keyboard/voice_artifacts.py",
            "scripts/pocketds-asr-api-provision.py",
            "components/keyboard/pocketds-keyboard.service",
        ):
            self.assertIn(source, transaction)
            self.assertNotIn(source, installer)

    def test_installer_retires_onboard_and_owns_touch_input_window_rule(self) -> None:
        installer = INSTALLER.read_text(encoding="utf-8")
        rules = (
            ROOT / "components/system/kwinrulesrc.current"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "systemctl --user mask --now pocketds-osk-listener.service",
            installer,
        )
        self.assertIn('"$HOME/.config/kwinrulesrc"', installer)
        self.assertIn("/etc/skel/.config/kwinrulesrc", installer)
        self.assertIn("Description=Pocket DS Touch Input", rules)
        self.assertIn("wmclass=[Pp]ocketds-(keyboard|touchpad)", rules)
        self.assertNotIn("Onboard", rules)
        self.assertNotIn("onboard", rules)

    def test_voice_runtime_is_private_cancellable_and_cleanup_bounded(self) -> None:
        self.assertIn("os.umask(0o077)", self.source)
        self.assertIn("remove_owned_artifact(VOICE_WAV)", self.source)
        self.assertIn('"arecord",', self.source)
        self.assertIn('"plughw:0,2",', self.source)
        self.assertNotIn("@DEFAULT_SOURCE@", self.source)
        self.assertIn('"S16_LE",', self.source)
        self.assertIn('"parecord",', self.source)
        self.assertIn('f"--device={microphone_source}"', self.source)
        self.assertIn('"--property=node.dont-fallback=true"', self.source)
        self.assertIn('"--property=node.dont-reconnect=true"', self.source)
        self.assertIn("stderr=subprocess.DEVNULL", self.source)
        self.assertIn("def _recognize_voice(self, generation):", self.source)
        self.assertLess(
            self.source.index("read_private_wav(VOICE_WAV)"),
            self.source.index("self.asr_api.transcribe(VOICE_WAV"),
        )
        self.assertIn("if not recording_is_eligible:", self.source)
        self.assertIn("self.asr_api.transcribe(VOICE_WAV, cancelled=cancelled)", self.source)
        self.assertNotIn("VOICE_WAV.unlink", self.source)

    def test_asr_api_is_primary_and_local_fallbacks_remain_bounded(self) -> None:
        asr_api_at = self.source.index("self.asr_api.transcribe(")
        sensevoice_at = self.source.index("if sensevoice_backend_available(")
        whisper_at = self.source.index("if not WHISPER_BIN.is_file()")
        recognition = self.source[
            self.source.index("    def _recognize_voice(self, generation):") :
            self.source.index("    def commit_voice_text(self, text, generation):")
        ]
        self.assertLess(asr_api_at, sensevoice_at)
        self.assertLess(sensevoice_at, whisper_at)
        self.assertIn("sensevoice_command(", self.source)
        self.assertIn("parse_sensevoice_transcript(output)", self.source)
        self.assertEqual(self.source.count("run_bounded_command("), 3)
        self.assertIn("stdout=subprocess.DEVNULL", self.source)
        self.assertIn("stderr=subprocess.DEVNULL", self.source)
        self.assertNotIn("capture_output=True", recognition)
        self.assertNotIn("wx" + "asr", self.source.lower())


if __name__ == "__main__":
    unittest.main()

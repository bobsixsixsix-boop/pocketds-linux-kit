"""Testable adapters around the Pocket DS keyboard visibility controller.

The GTK application passes GLib and AT-SPI objects into this module.  Keeping
the module itself free of GI imports lets the ordering, timer, focus, voice,
and signal-readiness behavior run in ordinary unit tests.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from contextlib import nullcontext
from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import secrets
import signal
import stat
import subprocess
import threading
import time
from typing import Callable, Hashable

from visibility_state import (
    Scheduler,
    VisibilityController,
    VisibilitySnapshot,
    VisibilityState,
)


QDBUS_BIN = "/usr/bin/qdbus-qt6"
LOWER_WINDOWS_TO_UPPER_SCRIPT = """// One snapshot, with no persistent windowAdded handler.
(function () {
    const outputs = workspace.screens;
    const source = outputs.find(output => output.name === "DSI-2");
    const target = outputs.find(output => output.name === "DSI-1");
    if (!source || !target || source === target) return;

    const excludedIds = ["pocketds-keyboard", "pocketds-touchpad",
                         "plasmashell", "org.kde.plasmashell", "pocketds-panel-shell",
                         "xwaylandvideobridge", "org.kde.xwaylandvideobridge"];
    function isPocketSurface(window) {
        return [window.resourceClass, window.resourceName, window.desktopFileName].some(
            value => excludedIds.indexOf(String(value || "").toLowerCase()) >= 0);
    }
    const windows = workspace.windowList().slice();
    function candidate(window) {
        return window.managed && !window.deleted && window.moveableAcrossScreens &&
            (window.normalWindow || window.dialog || window.utility || window.toolbar) &&
            (!window.specialWindow || window.toolbar) &&
            !window.desktopWindow && !window.dock && !window.popupWindow && !window.inputMethod &&
            !isPocketSurface(window) && window.output && window.output.name === source.name;
    }
    function ancestors(window) {
        const seen = [];
        for (let parent = window.transientFor; parent && seen.indexOf(parent) < 0;
             parent = parent.transientFor) seen.push(parent);
        return seen;
    }
    // sendClientToScreen follows transients. Never move a protected overlay
    // indirectly by selecting its parent either.
    const selected = windows.filter(candidate).filter(window => !windows.some(
        child => isPocketSurface(child) && ancestors(child).indexOf(window) >= 0))
        .sort((left, right) => ancestors(left).length - ancestors(right).length);
    // KWin may switch per-output desktops during sendToOutput. setDesktops also
    // follows transients and modal parents, so snapshot all managed windows
    // before any move, then restore changed entries parent-first, child-last.
    const desktops = windows.filter(window => window.managed && !window.deleted).map(window => ({
        window: window, desktops: window.desktops.slice(), depth: ancestors(window).length
    })).sort((left, right) => left.depth - right.depth);
    let requested = 0;
    let errors = 0;
    try {
        for (const window of selected) {
            if (window.deleted || !window.output || window.output.name === target.name) continue;
            try {
                // KWin preserves normal/maximized/fullscreen/minimized state
                // and maintains its restore geometry. Do not activate windows.
                ++requested;
                workspace.sendClientToScreen(window, target);
            } catch (error) {
                ++errors;
            }
        }
    } finally {
        for (const saved of desktops) {
            if (saved.window.deleted) continue;
            try {
                const current = saved.window.desktops;
                if (current.length !== saved.desktops.length ||
                    current.some((desktop, index) => desktop !== saved.desktops[index])) {
                    saved.window.desktops = saved.desktops;
                }
            } catch (error) {
                ++errors;
            }
        }
    }
    const moved = selected.filter(window => !window.deleted && window.output &&
                                           window.output.name === target.name).length;
    console.log("Pocket DS window transfer: requested=" + requested + " moved=" + moved +
                " remaining=" + (selected.length - moved) + " errors=" + errors);
})();
"""


def move_lower_windows_to_upper(*, runtime: Path, runner=None) -> bool:
    """Run a one-shot KWin request; call from a worker, never the GTK thread.

    True means D-Bus execution and cleanup succeeded, not a count of moved
    windows: KWin's Script.run also replies normally after JavaScript errors.
    """
    runtime = Path(runtime)
    execute = subprocess.run if runner is None else runner
    try:
        metadata = runtime.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700):
            return False
    except OSError:
        return False

    name = f"pocketds-windows-top-{os.getpid()}-{secrets.token_hex(12)}"
    script = runtime / f".{name}.js"
    descriptor = -1
    created = False
    load_attempted = False
    okay = False

    def call(path, method, *arguments):
        return execute([QDBUS_BIN, "org.kde.KWin", path, method, *arguments],
                       text=True, capture_output=True, stdin=subprocess.DEVNULL,
                       timeout=1.0, check=False)

    try:
        descriptor = os.open(script, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(LOWER_WINDOWS_TO_UPPER_SCRIPT)
            stream.flush()
        # A timeout may arrive after KWin has loaded the script. Always unload
        # by our unguessable name once load was attempted, even without its ID.
        load_attempted = True
        loaded = call("/Scripting", "org.kde.kwin.Scripting.loadScript", str(script), name)
        script_id = loaded.stdout.strip()
        if (loaded.returncode == 0 and len(script_id) <= 10 and script_id.isascii()
                and script_id.isdecimal() and int(script_id) <= 2147483647):
            run = call(f"/Scripting/Script{int(script_id)}", "org.kde.kwin.Script.run")
            okay = run.returncode == 0
    except (OSError, subprocess.TimeoutExpired, ValueError):
        okay = False
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if load_attempted:
            try:
                unloaded = call("/Scripting", "org.kde.kwin.Scripting.unloadScript", name)
                if unloaded.returncode != 0 or unloaded.stdout.strip() != "true":
                    okay = False
            except (OSError, subprocess.TimeoutExpired):
                okay = False
        if created:
            try:
                script.unlink()
            except OSError:
                okay = False
    return okay


class GLibTimerHandle:
    """Idempotent cancellation wrapper for a GLib source id."""

    def __init__(self, glib) -> None:
        self._glib = glib
        self._source_id = 0
        self._cancelled = False

    def bind(self, source_id: int) -> None:
        self._source_id = source_id
        if self._cancelled:
            self._remove_source()

    def mark_fired(self) -> None:
        self._source_id = 0

    def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        self._remove_source()

    def _remove_source(self) -> None:
        source_id = self._source_id
        self._source_id = 0
        if not source_id:
            return
        try:
            self._glib.source_remove(source_id)
        except Exception:
            # A GLib source may have been dispatched between generation
            # invalidation and source_remove(). The controller's token remains
            # the authoritative stale-callback guard.
            pass


class GLibScheduler(Scheduler):
    """Visibility Scheduler implemented with GLib.timeout_add()."""

    def __init__(self, glib) -> None:
        self._glib = glib

    def call_later(
        self, delay_ms: int, callback: Callable[[], None]
    ) -> GLibTimerHandle:
        handle = GLibTimerHandle(self._glib)

        def dispatch() -> bool:
            handle.mark_fired()
            callback()
            return False

        handle.bind(self._glib.timeout_add(delay_ms, dispatch))
        return handle


@dataclass(frozen=True)
class KeyboardFeedbackSettings:
    """Small persisted preference set for lower-keyboard feedback."""

    sound_enabled: bool = True
    sound_level: int = 5
    haptic_enabled: bool = True
    haptic_level: int = 2

    def __post_init__(self) -> None:
        if type(self.sound_enabled) is not bool or type(self.haptic_enabled) is not bool:
            raise ValueError("feedback enabled values must be booleans")
        if type(self.sound_level) is not int or not 1 <= self.sound_level <= 5:
            raise ValueError("sound level must be between 1 and 5")
        if type(self.haptic_level) is not int or not 1 <= self.haptic_level <= 5:
            raise ValueError("haptic level must be between 1 and 5")


class KeyboardFeedbackSettingsStore:
    """Bounded, fail-open, atomic storage for non-secret feedback settings."""

    _SCHEMA = "pocketds.keyboard-feedback.v1"
    _MAXIMUM_BYTES = 4 * 1024
    _KEYS = frozenset(
        {
            "schema",
            "sound_enabled",
            "sound_level",
            "haptic_enabled",
            "haptic_level",
        }
    )

    def __init__(
        self,
        path: Path,
        defaults: KeyboardFeedbackSettings | None = None,
    ) -> None:
        self.path = Path(path)
        self.defaults = defaults or KeyboardFeedbackSettings()

    def load(self) -> KeyboardFeedbackSettings:
        try:
            metadata = self.path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) & 0o022
                or metadata.st_size > self._MAXIMUM_BYTES
            ):
                return self.defaults
            with self.path.open("rb") as stream:
                payload = stream.read(self._MAXIMUM_BYTES + 1)
            if len(payload) > self._MAXIMUM_BYTES:
                return self.defaults
            document = json.loads(payload.decode("utf-8"))
            if (
                not isinstance(document, dict)
                or set(document) != self._KEYS
                or document.get("schema") != self._SCHEMA
            ):
                return self.defaults
            return KeyboardFeedbackSettings(
                sound_enabled=document["sound_enabled"],
                sound_level=document["sound_level"],
                haptic_enabled=document["haptic_enabled"],
                haptic_level=document["haptic_level"],
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
            return self.defaults

    def save(self, settings: KeyboardFeedbackSettings) -> bool:
        if not isinstance(settings, KeyboardFeedbackSettings):
            raise TypeError("settings must be KeyboardFeedbackSettings")
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.new"
        )
        descriptor = -1
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            parent_metadata = self.path.parent.lstat()
            if (
                not stat.S_ISDIR(parent_metadata.st_mode)
                or stat.S_ISLNK(parent_metadata.st_mode)
                or parent_metadata.st_uid != os.getuid()
                or stat.S_IMODE(parent_metadata.st_mode) & 0o022
            ):
                return False
            try:
                target_metadata = self.path.lstat()
            except FileNotFoundError:
                target_metadata = None
            if target_metadata is not None and (
                not stat.S_ISREG(target_metadata.st_mode)
                or target_metadata.st_uid != os.getuid()
                or target_metadata.st_nlink != 1
                or stat.S_IMODE(target_metadata.st_mode) & 0o022
            ):
                return False
            payload = json.dumps(
                {
                    "schema": self._SCHEMA,
                    "sound_enabled": settings.sound_enabled,
                    "sound_level": settings.sound_level,
                    "haptic_enabled": settings.haptic_enabled,
                    "haptic_level": settings.haptic_level,
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            if len(payload) > self._MAXIMUM_BYTES:
                return False
            descriptor = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(
                self.path.parent,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return True
        except OSError:
            return False
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


class KeySoundFeedback:
    """Low-latency, fail-open key feedback backed by libcanberra.

    One process-wide context lets PipeWire remain idle between short sounds;
    callers never spawn an audio player in the input path.  Every failure is
    contained here so audio feedback can never prevent a key from being sent.
    """

    _EVENT_ID = b"event.id"
    _EVENT_DESCRIPTION = b"event.description"
    _THEME_NAME = b"canberra.xdg-theme.name"
    _CACHE_CONTROL = b"canberra.cache-control"
    _VOLUME = b"canberra.volume"
    _RESTORE_PROPS = b"state.restore-props"
    _MEDIA_ROLE = b"media.role"

    def __init__(
        self,
        *,
        enabled: bool = True,
        volume_db: float = -36.0,
        minimum_interval_s: float = 0.05,
        retry_interval_s: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
        library_loader=ctypes.CDLL,
        library_finder=ctypes.util.find_library,
    ) -> None:
        if minimum_interval_s < 0:
            raise ValueError("minimum sound interval must not be negative")
        if retry_interval_s <= 0:
            raise ValueError("sound retry interval must be positive")
        if not -60.0 <= volume_db <= -6.0:
            raise ValueError("sound volume must be between -60 and -6 dB")
        self._library = None
        self._context = ctypes.c_void_p()
        self._enabled = bool(enabled)
        self._volume_db = float(volume_db)
        self._available = False
        self._minimum_interval_s = float(minimum_interval_s)
        self._retry_interval_s = float(retry_interval_s)
        self._monotonic = monotonic
        self._library_loader = library_loader
        self._library_finder = library_finder
        self._last_played_at = float("-inf")
        self._retry_not_before = 0.0
        if self._enabled:
            self._open_context()

    def _open_context(self, *, now: float | None = None) -> bool:
        self._drop_context()
        try:
            library_name = (
                self._library_finder("canberra") or "libcanberra.so.0"
            )
            library = self._library_loader(library_name)
            self._library = library
            self._configure_function(
                library.ca_context_create,
                [ctypes.POINTER(ctypes.c_void_p)],
            )
            self._configure_function(
                library.ca_context_set_driver,
                [ctypes.c_void_p, ctypes.c_char_p],
            )
            self._configure_function(
                library.ca_context_change_props,
                [ctypes.c_void_p],
            )
            self._configure_function(
                library.ca_context_open,
                [ctypes.c_void_p],
            )
            self._configure_function(
                library.ca_context_destroy,
                [ctypes.c_void_p],
            )
            self._configure_function(
                library.ca_context_play,
                [ctypes.c_void_p, ctypes.c_uint32],
            )

            context = ctypes.c_void_p()
            if library.ca_context_create(ctypes.byref(context)) < 0:
                self._defer_retry(now)
                return False
            self._context = context
            if not context.value:
                self._defer_retry(now)
                return False
            if library.ca_context_change_props(
                context,
                ctypes.c_char_p(self._RESTORE_PROPS),
                ctypes.c_char_p(b"false"),
                ctypes.c_char_p(),
            ) < 0:
                self._defer_retry(now)
                return False
            if library.ca_context_set_driver(context, b"pulse") < 0:
                self._defer_retry(now)
                return False
            if library.ca_context_open(context) < 0:
                self._defer_retry(now)
                return False
            self._available = True
            self._retry_not_before = 0.0
            return True
        except Exception:
            self._defer_retry(now)
            return False

    def _defer_retry(self, now: float | None = None) -> None:
        self._drop_context()
        if now is None:
            now = self._monotonic()
        self._retry_not_before = now + self._retry_interval_s

    @staticmethod
    def _configure_function(function, argtypes) -> None:
        try:
            function.argtypes = argtypes
            function.restype = ctypes.c_int
        except (AttributeError, TypeError):
            # Python fakes used by tests do not expose ctypes metadata.
            pass

    @property
    def available(self) -> bool:
        return self._available

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def volume_db(self) -> float:
        return self._volume_db

    def configure(
        self,
        *,
        enabled: bool | None = None,
        volume_db: float | None = None,
    ) -> None:
        if volume_db is not None and not -60.0 <= volume_db <= -6.0:
            raise ValueError("sound volume must be between -60 and -6 dB")
        if enabled is not None:
            self._enabled = bool(enabled)
        if volume_db is not None:
            self._volume_db = float(volume_db)

    def play(self, *, modifier: bool = False) -> bool:
        if not self._enabled:
            return False
        now = self._monotonic()
        if not self._available:
            if now < self._retry_not_before:
                return False
            if not self._open_context(now=now):
                return False
        if now - self._last_played_at < self._minimum_interval_s:
            return False
        event_id = b"button-pressed-modifier" if modifier else b"button-pressed"
        # WirePlumber restores one volume per stream identity. Give each
        # discrete dB slot its own stable role so that restoration preserves,
        # rather than flattens, the five keyboard volume levels.
        volume_slot = int(round(-self._volume_db * 10.0))
        media_role = f"PocketDSKeyboardFeedback{volume_slot}".encode("ascii")
        try:
            result = self._library.ca_context_play(
                self._context,
                ctypes.c_uint32(0),
                ctypes.c_char_p(self._EVENT_ID),
                ctypes.c_char_p(event_id),
                ctypes.c_char_p(self._EVENT_DESCRIPTION),
                ctypes.c_char_p(b"Keyboard key"),
                ctypes.c_char_p(self._THEME_NAME),
                ctypes.c_char_p(b"ocean"),
                ctypes.c_char_p(self._CACHE_CONTROL),
                # Permanent libcanberra sample caching drops per-play volume
                # on the PipeWire Pulse server. The tiny event is decoded in
                # process, so uncached playback is the reliable bounded path.
                ctypes.c_char_p(b"never"),
                ctypes.c_char_p(self._VOLUME),
                ctypes.c_char_p(f"{self._volume_db:.1f}".encode("ascii")),
                ctypes.c_char_p(self._MEDIA_ROLE),
                ctypes.c_char_p(media_role),
                ctypes.c_char_p(self._RESTORE_PROPS),
                ctypes.c_char_p(b"false"),
                ctypes.c_char_p(),
            )
        except Exception:
            self._defer_retry(now)
            return False
        if result < 0:
            self._defer_retry(now)
            return False
        self._last_played_at = now
        return True

    def _drop_context(self) -> None:
        library = self._library
        context = self._context
        self._library = None
        self._available = False
        self._context = ctypes.c_void_p()
        if library is None or not context.value:
            return
        try:
            library.ca_context_destroy(context)
        except Exception:
            pass

    def close(self) -> None:
        self._enabled = False
        self._drop_context()


class KeyHapticFeedback:
    """Non-blocking, fail-open keyboard haptics routed through InputPlumber.

    GTK only marks one pulse pending.  A private system-bus connection owned by
    the worker performs the bounded Pulse call, so a slow or missing haptics
    backend can never delay key injection.  Busy requests are deliberately
    dropped instead of queued.
    """

    _SERVICE = "org.shadowblip.InputPlumber"
    _PATH = "/org/shadowblip/InputPlumber/CompositeDevice0"
    _INTERFACE = "org.shadowblip.Output.ForceFeedback"

    def __init__(
        self,
        *,
        enabled: bool = True,
        intensity: float = 0.10,
        duration_ms: int = 20,
        minimum_interval_s: float = 0.055,
        retry_interval_s: float = 5.0,
        call_timeout_s: float = 0.20,
        monotonic: Callable[[], float] = time.monotonic,
        bus_factory=None,
        permission_guard=None,
    ) -> None:
        if not 0.0 < intensity <= 0.70:
            raise ValueError("haptic intensity must be in (0, 0.70]")
        if not 5 <= duration_ms <= 50:
            raise ValueError("haptic duration must be between 5 and 50 ms")
        if minimum_interval_s < 0:
            raise ValueError("minimum haptic interval must not be negative")
        if retry_interval_s <= 0 or call_timeout_s <= 0:
            raise ValueError("haptic retry and timeout must be positive")

        self._enabled = bool(enabled)
        self._intensity = float(intensity)
        self._duration_ms = int(duration_ms)
        self._minimum_interval_s = float(minimum_interval_s)
        self._retry_interval_s = float(retry_interval_s)
        self._call_timeout_s = float(call_timeout_s)
        self._monotonic = monotonic
        self._bus_factory = bus_factory or self._open_private_system_bus
        self._permission_guard = permission_guard or (lambda: nullcontext(True))
        self._lock = threading.Lock()
        self._call_lock = threading.Lock()
        self._wake = threading.Event()
        self._shutdown = threading.Event()
        self._thread = None
        self._busy = False
        self._closed = False
        self._last_started_at = float("-inf")
        self._retry_not_before = 0.0

    @staticmethod
    def _open_private_system_bus():
        import dbus

        return dbus.SystemBus(private=True)

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def intensity(self) -> float:
        with self._lock:
            return self._intensity

    def configure(
        self,
        *,
        enabled: bool | None = None,
        intensity: float | None = None,
        duration_ms: int | None = None,
    ) -> None:
        if intensity is not None and not 0.0 < intensity <= 0.70:
            raise ValueError("haptic intensity must be in (0, 0.70]")
        if duration_ms is not None and not 5 <= duration_ms <= 50:
            raise ValueError("haptic duration must be between 5 and 50 ms")
        with self._lock:
            if self._closed:
                return
            if enabled is not None:
                self._enabled = bool(enabled)
            if intensity is not None:
                self._intensity = float(intensity)
            if duration_ms is not None:
                self._duration_ms = int(duration_ms)

    def play(self, *, modifier: bool = False) -> bool:
        del modifier
        now = self._monotonic()
        with self._lock:
            if (
                self._closed
                or not self._enabled
                or self._busy
                or now < self._retry_not_before
                or now - self._last_started_at < self._minimum_interval_s
            ):
                return False
            self._busy = True
            self._last_started_at = now
            if self._thread is None:
                try:
                    self._thread = threading.Thread(
                        target=self._run,
                        name="pocketds-key-haptic",
                        daemon=True,
                    )
                    self._thread.start()
                except RuntimeError:
                    self._thread = None
                    self._busy = False
                    self._retry_not_before = now + self._retry_interval_s
                    return False
            self._wake.set()
        return True

    @staticmethod
    def _close_bus(bus) -> None:
        if bus is None:
            return
        try:
            bus.close()
        except Exception:
            pass

    def _run(self) -> None:
        bus = None
        try:
            while True:
                self._wake.wait()
                self._wake.clear()
                if self._shutdown.is_set():
                    break
                failed = False
                try:
                    with self._permission_guard() as allowed:
                        if not allowed or self._shutdown.is_set():
                            continue
                        if bus is None:
                            bus = self._bus_factory()
                        with self._call_lock:
                            if self._shutdown.is_set():
                                break
                            with self._lock:
                                if self._closed or not self._enabled:
                                    continue
                                intensity = self._intensity
                                duration_ms = self._duration_ms
                            bus.call_blocking(
                                self._SERVICE,
                                self._PATH,
                                self._INTERFACE,
                                "Pulse",
                                "du",
                                (intensity, duration_ms),
                                timeout=self._call_timeout_s,
                            )
                except Exception:
                    failed = True
                    self._close_bus(bus)
                    bus = None
                finally:
                    with self._lock:
                        if failed:
                            self._retry_not_before = (
                                self._monotonic() + self._retry_interval_s
                            )
                        self._busy = False
        finally:
            self._close_bus(bus)
            with self._lock:
                self._busy = False

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._enabled = False
                self._shutdown.set()
                self._wake.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            with self._call_lock:
                pass
            thread.join(timeout=self._call_timeout_s + 0.25)


class BoundedRepeatController:
    """One cancellable, visibility-gated key-repeat sequence.

    The wall-clock deadline is authoritative even when the main loop delivers
    a timer late.  Every callback also rechecks the UI-owned permission probe,
    so a queued callback cannot keep typing after the keyboard is hidden.
    """

    def __init__(
        self,
        scheduler: Scheduler,
        is_allowed: Callable[[], bool],
        emit: Callable[[], None],
        *,
        initial_delay_ms: int = 420,
        interval_ms: int = 65,
        maximum_ms: int = 3_000,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            initial_delay_ms < 0
            or interval_ms <= 0
            or maximum_ms <= 0
            or initial_delay_ms >= maximum_ms
        ):
            raise ValueError("repeat timing is invalid")
        self._scheduler = scheduler
        self._is_allowed = is_allowed
        self._emit = emit
        self._initial_delay_ms = initial_delay_ms
        self._interval_ms = interval_ms
        self._maximum_ms = maximum_ms
        self._monotonic = monotonic
        self._generation = 0
        self._deadline = 0.0
        self._handle = None

    @property
    def active(self) -> bool:
        return self._handle is not None

    def start(self) -> bool:
        self.cancel()
        if not self._is_allowed():
            return False
        self._emit()
        token = self._generation
        self._deadline = self._monotonic() + self._maximum_ms / 1_000
        self._handle = self._scheduler.call_later(
            self._initial_delay_ms,
            lambda: self._tick(token),
        )
        return True

    def cancel(self) -> None:
        self._generation += 1
        self._deadline = 0.0
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    def _tick(self, token: int) -> None:
        if token != self._generation:
            return
        self._handle = None
        now = self._monotonic()
        if not self._is_allowed() or now >= self._deadline:
            self.cancel()
            return
        self._emit()
        remaining_ms = max(1, int((self._deadline - now) * 1_000))
        delay_ms = min(self._interval_ms, remaining_ms)
        self._handle = self._scheduler.call_later(
            delay_ms,
            lambda: self._tick(token),
        )


class BackspaceClearGestureController:
    """Recognize a deliberate hold, upward swipe, and release gesture.

    Ordinary backspace delivery remains owned by Gtk.Button and
    BoundedRepeatController.  This observer only commits the destructive
    select-all/delete action after the gesture reaches READY and the same
    contact ends there.
    """

    FEEDBACK_IDLE = "idle"
    FEEDBACK_HELD = "held"
    FEEDBACK_READY = "ready"

    _IDLE = "idle"
    _TRACKING = "tracking"
    _HELD = "held"
    _READY = "ready"

    def __init__(
        self,
        scheduler: Scheduler,
        is_allowed: Callable[[], bool],
        on_feedback: Callable[[str], None],
        on_clear: Callable[[], None],
        *,
        hold_delay_ms: int = 420,
        upward_distance: float = 72.0,
        maximum_horizontal_ratio: float = 1.25,
    ) -> None:
        if hold_delay_ms <= 0:
            raise ValueError("hold delay must be positive")
        if upward_distance <= 0:
            raise ValueError("upward distance must be positive")
        if maximum_horizontal_ratio <= 0:
            raise ValueError("horizontal ratio must be positive")
        self._scheduler = scheduler
        self._is_allowed = is_allowed
        self._on_feedback = on_feedback
        self._on_clear = on_clear
        self._hold_delay_ms = hold_delay_ms
        self._upward_distance = float(upward_distance)
        self._maximum_horizontal_ratio = float(maximum_horizontal_ratio)
        self._generation = 0
        self._state = self._IDLE
        self._contact: Hashable | None = None
        self._origin = (0.0, 0.0)
        self._latest = (0.0, 0.0)
        self._handle = None

    @property
    def active(self) -> bool:
        return self._state != self._IDLE

    def begin(self, contact: Hashable, x: float, y: float) -> bool:
        self.cancel()
        if contact is None or not self._is_allowed():
            return False
        self._generation += 1
        token = self._generation
        self._state = self._TRACKING
        self._contact = contact
        self._origin = (float(x), float(y))
        self._latest = self._origin
        try:
            self._handle = self._scheduler.call_later(
                self._hold_delay_ms,
                lambda: self._arm(token),
            )
        except Exception:
            self._handle = None
            self._state = self._IDLE
            self._contact = None
            return False
        return True

    def update(self, contact: Hashable, x: float, y: float) -> bool:
        if contact != self._contact or self._state == self._IDLE:
            return False
        if not self._is_allowed():
            self.cancel()
            return False
        self._latest = (float(x), float(y))
        self._refresh_ready_state()
        return self._state == self._READY

    def end(self, contact: Hashable, x: float, y: float) -> bool:
        if contact != self._contact or self._state == self._IDLE:
            return False
        if not self._is_allowed():
            self.cancel()
            return False
        self._latest = (float(x), float(y))
        self._refresh_ready_state()
        should_clear = self._state == self._READY
        had_feedback = self._state in {self._HELD, self._READY}
        self._generation += 1
        self._cancel_timer()
        self._state = self._IDLE
        self._contact = None
        if had_feedback:
            self._on_feedback(self.FEEDBACK_IDLE)
        if should_clear:
            self._on_clear()
        return should_clear

    def cancel(self) -> None:
        had_feedback = self._state in {self._HELD, self._READY}
        self._generation += 1
        self._cancel_timer()
        self._state = self._IDLE
        self._contact = None
        if had_feedback:
            self._on_feedback(self.FEEDBACK_IDLE)

    def _arm(self, token: int) -> None:
        if token != self._generation or self._state != self._TRACKING:
            return
        self._handle = None
        if not self._is_allowed():
            self.cancel()
            return
        self._state = self._HELD
        self._on_feedback(self.FEEDBACK_HELD)
        self._refresh_ready_state()

    def _refresh_ready_state(self) -> None:
        if self._state not in {self._HELD, self._READY}:
            return
        origin_x, origin_y = self._origin
        x, y = self._latest
        upward = origin_y - y
        horizontal = abs(x - origin_x)
        ready = (
            upward >= self._upward_distance
            and horizontal <= upward * self._maximum_horizontal_ratio
        )
        next_state = self._READY if ready else self._HELD
        if next_state == self._state:
            return
        self._state = next_state
        self._on_feedback(
            self.FEEDBACK_READY
            if next_state == self._READY
            else self.FEEDBACK_HELD
        )

    def _cancel_timer(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None


class HoldTapController:
    """Resolve one touch into either a valid click or a cancellable hold.

    Gtk.Button owns click validation, including the finger leaving the key.
    This controller only delays the hold action and remembers whether the
    following release/click signals must finish the hold exactly once.
    """

    _IDLE = "idle"
    _PRESSED = "pressed"
    _SHORT_RELEASED = "short-released"
    _HELD = "held"
    _HELD_RELEASED = "held-released"
    _CANCELLED = "cancelled"

    def __init__(
        self,
        scheduler: Scheduler,
        is_allowed: Callable[[], bool],
        on_tap: Callable[[], None],
        on_hold: Callable[[], None],
        on_hold_release: Callable[[], None] | None = None,
        *,
        hold_delay_ms: int = 450,
    ) -> None:
        if hold_delay_ms <= 0:
            raise ValueError("hold delay must be positive")
        self._scheduler = scheduler
        self._is_allowed = is_allowed
        self._on_tap = on_tap
        self._on_hold = on_hold
        self._on_hold_release = (
            on_hold_release if on_hold_release is not None else lambda: None
        )
        self._hold_delay_ms = hold_delay_ms
        self._generation = 0
        self._state = self._IDLE
        self._handle = None

    @property
    def active(self) -> bool:
        return self._state in {self._PRESSED, self._HELD}

    def press(self) -> bool:
        if self.active:
            return False
        self._generation += 1
        self._cancel_timer()
        if not self._is_allowed():
            self._state = self._CANCELLED
            return False
        self._state = self._PRESSED
        token = self._generation
        try:
            self._handle = self._scheduler.call_later(
                self._hold_delay_ms,
                lambda: self._fire_hold(token),
            )
        except Exception:
            # Scheduling failure must not disable ordinary space input.
            self._handle = None
        return True

    def release(self) -> bool:
        if self._state == self._PRESSED:
            self._cancel_timer()
            self._state = self._SHORT_RELEASED
            return True
        if self._state == self._HELD:
            self._cancel_timer()
            self._state = self._HELD_RELEASED
            self._on_hold_release()
            return True
        return False

    def click(self) -> bool:
        if self._state in {self._PRESSED, self._SHORT_RELEASED}:
            self._cancel_timer()
            self._state = self._IDLE
            if not self._is_allowed():
                return False
            self._on_tap()
            return True
        if self._state == self._HELD:
            self._cancel_timer()
            self._state = self._HELD_RELEASED
            self._on_hold_release()
            return False
        if self._state in {self._HELD_RELEASED, self._CANCELLED}:
            self._cancel_timer()
            self._state = self._IDLE
            return False
        if self._state == self._IDLE and self._is_allowed():
            self._on_tap()
            return True
        return False

    def cancel(self) -> bool:
        held = self._state == self._HELD
        self._generation += 1
        self._cancel_timer()
        self._state = self._CANCELLED
        return held

    def _cancel_timer(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    def _fire_hold(self, token: int) -> None:
        if token != self._generation or self._state != self._PRESSED:
            return
        self._handle = None
        if not self._is_allowed():
            self._state = self._CANCELLED
            return
        self._state = self._HELD
        self._on_hold()


def stop_recorder_process(process, *, cancelled: bool) -> int:
    """Synchronously stop one recorder with a strict, finite wait budget.

    The GTK runtime uses :class:`RecorderStopController` instead.  This helper
    remains useful for non-main-loop callers and tests, but it must never turn a
    failed SIGKILL into an unbounded ``wait()``.
    """

    returncode = process.poll()
    if returncode is not None:
        return int(returncode)
    try:
        process.send_signal(signal.SIGINT)
        process.wait(timeout=0.75 if cancelled else 3)
    except ProcessLookupError:
        returncode = process.poll()
        return int(returncode) if returncode is not None else 1
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("recorder did not exit after SIGKILL") from exc
    returncode = process.poll()
    return int(returncode) if returncode is not None else 1


class RecorderStopFailure(str, Enum):
    """Public reasons why asynchronous recorder cleanup could not finish normally."""

    POLL_FAILED = "poll-failed"
    SIGNAL_FAILED = "signal-failed"
    REAP_TIMEOUT = "reap-timeout"
    SCHEDULER_FAILED = "scheduler-failed"


@dataclass(frozen=True)
class RecorderStopResult:
    """One fully reaped recorder and whether its WAV is ASR-eligible."""

    returncode: int
    forced: bool
    failure: RecorderStopFailure | None
    sigint_sent: bool = False

    @property
    def eligible_for_recognition(self) -> bool:
        return self.returncode == 0 and not self.forced and self.failure is None

    @property
    def may_finalize_interrupted_arecord(self) -> bool:
        """Whether device arecord's verified EINTR exit may be repaired.

        Qualcomm's direct ALSA capture returns 1 after our orderly SIGINT and
        leaves arecord's correlated live RIFF/data length placeholders behind.
        A process that exited by itself, needed SIGKILL, or hit any controller
        failure must never enter that compatibility path.
        """

        return (
            self.returncode == 1
            and self.sigint_sent
            and not self.forced
            and self.failure is None
        )


class RecorderStopController:
    """Signal and reap one recorder without blocking the UI main loop.

    Normal operation is a GLib-compatible poll state machine: send SIGINT,
    poll until a finite grace deadline, send SIGKILL, then poll through a
    second finite deadline.  A still-unreaped process remains explicitly owned
    and is polled at low frequency, so a D-state child cannot become an
    untracked zombie and a second capture cannot start over it.

    If the main-loop scheduler itself fails, a private fallback reaper thread
    takes ownership.  The failure callback runs before that handoff; the
    completion callback is deliberately suppressed because GTK callbacks are
    not safe from the fallback thread.
    """

    def __init__(
        self,
        scheduler: Scheduler,
        *,
        poll_interval_ms: int = 50,
        cancelled_grace_ms: int = 750,
        finalize_grace_ms: int = 3_000,
        kill_grace_ms: int = 1_000,
        stuck_poll_ms: int = 250,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            poll_interval_ms <= 0
            or cancelled_grace_ms <= 0
            or finalize_grace_ms <= 0
            or kill_grace_ms <= 0
            or stuck_poll_ms <= 0
        ):
            raise ValueError("recorder stop timing is invalid")
        self._scheduler = scheduler
        self._poll_interval_ms = poll_interval_ms
        self._cancelled_grace_ms = cancelled_grace_ms
        self._finalize_grace_ms = finalize_grace_ms
        self._kill_grace_ms = kill_grace_ms
        self._stuck_poll_ms = stuck_poll_ms
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._generation = 0
        self._process = None
        self._handle = None
        self._on_reaped: Callable[[RecorderStopResult], None] | None = None
        self._on_failed: Callable[[RecorderStopFailure], None] | None = None
        self._sigint_deadline = 0.0
        self._kill_deadline = 0.0
        self._kill_sent = False
        self._sigint_sent = False
        self._failure_reported = False
        self._failure_reason: RecorderStopFailure | None = None
        self._fallback_active = False

    @property
    def active(self) -> bool:
        with self._lock:
            return self._process is not None

    def request(
        self,
        process,
        *,
        cancelled: bool,
        on_reaped: Callable[[RecorderStopResult], None],
        on_failed: Callable[[RecorderStopFailure], None],
    ) -> None:
        if process is None or not callable(on_reaped) or not callable(on_failed):
            raise ValueError("recorder stop request is invalid")
        with self._lock:
            if self._process is not None:
                raise RuntimeError("another recorder is still being reaped")
            self._generation += 1
            token = self._generation
            self._process = process
            self._on_reaped = on_reaped
            self._on_failed = on_failed
            self._handle = None
            self._kill_sent = False
            self._sigint_sent = False
            self._failure_reported = False
            self._failure_reason: RecorderStopFailure | None = None
            self._fallback_active = False
            grace_ms = (
                self._cancelled_grace_ms
                if cancelled
                else self._finalize_grace_ms
            )
            self._sigint_deadline = self._monotonic() + grace_ms / 1_000
            # Freeze both absolute deadlines at request time. A delayed GTK
            # callback must not extend the advertised stop/reap wall-clock cap.
            self._kill_deadline = (
                self._sigint_deadline + self._kill_grace_ms / 1_000
            )

        try:
            returncode = process.poll()
        except Exception:
            returncode = None
            self._notify_failed(token, RecorderStopFailure.POLL_FAILED)
            self._send_kill(token, process)
        if returncode is not None:
            self._finish(token, process, int(returncode))
            return

        with self._lock:
            kill_sent = self._kill_sent
        if not kill_sent:
            try:
                process.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
            except Exception:
                self._notify_failed(token, RecorderStopFailure.SIGNAL_FAILED)
                self._send_kill(token, process)
            else:
                with self._lock:
                    if token == self._generation and process is self._process:
                        self._sigint_sent = True
        self._schedule(token, process, self._poll_interval_ms)

    def shutdown(self) -> None:
        """Move any outstanding child to the scheduler-independent reaper."""

        with self._lock:
            process = self._process
            token = self._generation
            handle = self._handle
            self._handle = None
        if handle is not None:
            self._cancel_handle(handle)
        if process is not None:
            self._notify_failed(token, RecorderStopFailure.SCHEDULER_FAILED)
            self._start_fallback_reaper(token, process)

    def _schedule(self, token: int, process, delay_ms: int) -> None:
        try:
            handle = self._scheduler.call_later(
                delay_ms,
                lambda: self._poll(token, process),
            )
        except Exception:
            self._notify_failed(token, RecorderStopFailure.SCHEDULER_FAILED)
            self._start_fallback_reaper(token, process)
            return
        with self._lock:
            stale = (
                token != self._generation
                or process is not self._process
                or self._fallback_active
            )
            if not stale:
                self._handle = handle
        if stale:
            self._cancel_handle(handle)

    def _poll(self, token: int, process) -> None:
        with self._lock:
            if (
                token != self._generation
                or process is not self._process
                or self._fallback_active
            ):
                return
            self._handle = None
        try:
            returncode = process.poll()
        except Exception:
            returncode = None
            self._notify_failed(token, RecorderStopFailure.POLL_FAILED)
            self._send_kill(token, process)
        if returncode is not None:
            self._finish(token, process, int(returncode))
            return

        now = self._monotonic()
        with self._lock:
            kill_sent = self._kill_sent
            sigint_deadline = self._sigint_deadline
            kill_deadline = self._kill_deadline
        if not kill_sent and now >= sigint_deadline:
            self._send_kill(token, process)
            with self._lock:
                kill_sent = self._kill_sent
                kill_deadline = self._kill_deadline
        if kill_sent and now >= kill_deadline:
            self._notify_failed(token, RecorderStopFailure.REAP_TIMEOUT)
            delay_ms = self._stuck_poll_ms
        else:
            deadline = kill_deadline if kill_sent else sigint_deadline
            remaining_ms = max(1, int((deadline - now) * 1_000))
            delay_ms = min(self._poll_interval_ms, remaining_ms)
        self._schedule(token, process, delay_ms)

    def _send_kill(self, token: int, process) -> None:
        with self._lock:
            if (
                token != self._generation
                or process is not self._process
                or self._kill_sent
            ):
                return
            self._kill_sent = True
            self._kill_deadline = min(
                self._kill_deadline,
                self._monotonic() + self._kill_grace_ms / 1_000,
            )
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except Exception:
            self._notify_failed(token, RecorderStopFailure.SIGNAL_FAILED)

    def _notify_failed(self, token: int, reason: RecorderStopFailure) -> None:
        with self._lock:
            if token != self._generation or self._process is None:
                return
            if self._failure_reported:
                return
            self._failure_reported = True
            self._failure_reason = reason
            callback = self._on_failed
        if callback is not None:
            try:
                callback(reason)
            except Exception:
                pass

    def _finish(self, token: int, process, returncode: int) -> None:
        with self._lock:
            if token != self._generation or process is not self._process:
                return
            handle = self._handle
            callback = self._on_reaped
            result = RecorderStopResult(
                returncode=returncode,
                forced=self._kill_sent,
                failure=self._failure_reason,
                sigint_sent=self._sigint_sent,
            )
            self._process = None
            self._handle = None
            self._on_reaped = None
            self._on_failed = None
            self._sigint_deadline = 0.0
            self._kill_deadline = 0.0
            self._kill_sent = False
            self._sigint_sent = False
            self._failure_reason = None
            self._fallback_active = False
        if handle is not None:
            self._cancel_handle(handle)
        if callback is not None:
            try:
                callback(result)
            except Exception:
                pass

    def _start_fallback_reaper(self, token: int, process) -> None:
        with self._lock:
            if token != self._generation or process is not self._process:
                return
            if self._fallback_active:
                return
            self._fallback_active = True
            handle = self._handle
            self._handle = None
        if handle is not None:
            self._cancel_handle(handle)

        def reap() -> None:
            try:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                except Exception:
                    pass
                while True:
                    try:
                        returncode = process.wait(timeout=0.5)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                    except Exception:
                        try:
                            returncode = process.poll()
                        except Exception:
                            returncode = None
                        if returncode is not None:
                            break
                        time.sleep(0.05)
                with self._lock:
                    if token != self._generation or process is not self._process:
                        return
                    self._process = None
                    self._on_reaped = None
                    self._on_failed = None
                    self._sigint_deadline = 0.0
                    self._kill_deadline = 0.0
                    self._kill_sent = False
                    self._sigint_sent = False
                    self._failure_reason = None
                    self._fallback_active = False
            except Exception:
                # Retain explicit ownership if even the fallback supervisor
                # fails; active stays true and a later shutdown can retry.
                with self._lock:
                    if token == self._generation and process is self._process:
                        self._fallback_active = False
                return

        try:
            threading.Thread(
                target=reap,
                name="pocketds-recorder-reaper",
                daemon=True,
            ).start()
        except Exception:
            # Ownership intentionally remains in self._process.
            with self._lock:
                if token == self._generation and process is self._process:
                    self._fallback_active = False
            pass

    @staticmethod
    def _cancel_handle(handle) -> None:
        try:
            handle.cancel()
        except Exception:
            pass


class MicrophoneSessionMonitor:
    """Resolve a source off the UI thread and guard direct ALSA permission.

    The query must be bounded and observe its cancellation predicate. Results
    are dispatched through a second cancellation gate, so queued callbacks
    cannot revive a cancelled session. A final check starts *after* the caller
    has reaped the recorder; an earlier in-flight poll cannot authorize ASR.
    """

    def __init__(
        self, generation, query, dispatch, on_selected, on_failed, on_finalized,
        *, guarded_source, poll_interval=0.25,
    ):
        self.generation = generation
        self._query = query
        self._dispatch = dispatch
        self._on_selected = on_selected
        self._on_failed = on_failed
        self._on_finalized = on_finalized
        self._guarded_source = guarded_source
        self._poll_interval = poll_interval
        self._cancelled = threading.Event()
        self._finish_requested = threading.Event()
        self._wake = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("microphone session already started")
        self._thread = threading.Thread(
            target=self._run, name="pocketds-microphone-permission", daemon=True,
        )
        self._thread.start()

    def cancel(self):
        self._cancelled.set()
        self._wake.set()

    def finish(self):
        self._finish_requested.set()
        self._wake.set()

    def _publish(self, callback, *args):
        if self._cancelled.is_set():
            return

        def deliver():
            if not self._cancelled.is_set():
                callback(self.generation, *args)
            return False

        self._dispatch(deliver)

    def _run(self):
        try:
            source, problem = self._query(None, self._cancelled.is_set)
            if problem or source is None:
                self._publish(self._on_failed, problem or "麦克风不可用")
                return
            self._publish(self._on_selected, source)
            if source != self._guarded_source:
                return  # Pulse owns external-source mute and volume.
            while not self._cancelled.is_set():
                self._wake.wait(self._poll_interval)
                self._wake.clear()
                if self._cancelled.is_set():
                    return
                final_check = self._finish_requested.is_set()
                current, problem = self._query(source, self._cancelled.is_set)
                if problem or current != source:
                    self._publish(self._on_failed, problem or "麦克风不可用")
                    return
                if final_check:
                    self._publish(self._on_finalized)
                    return
        except Exception:
            self._publish(self._on_failed, "麦克风状态检查失败")


class CaptureStartupFailure(str, Enum):
    """Public, non-sensitive reasons why microphone startup did not become ready."""

    CLEANUP_FAILED = "cleanup-failed"
    SPAWN_FAILED = "spawn-failed"
    RECORDER_EXITED = "recorder-exited"
    INVALID_ARTIFACT = "invalid-artifact"
    CONNECT_TIMEOUT = "connect-timeout"
    SCHEDULER_FAILED = "scheduler-failed"
    CALLBACK_FAILED = "callback-failed"


class CaptureStartupController:
    """Bounded recorder launch that requires real PCM before handoff.

    A live ``parecord`` process does not prove that the Qualcomm capture graph
    started: it can remain alive for several seconds and still write no audio.
    Wait briefly for the first PCM payload, then fail the single attempt.  Do
    not immediately reopen a failed graph; the runtime applies a cooldown so a
    user cannot stack Q6APM timeouts by tapping the microphone repeatedly.

    The controller owns each process until ``on_ready`` is called.  Every
    timer is guarded by a generation token so cancellation remains final even
    if a main-loop callback was already queued.
    """

    def __init__(
        self,
        scheduler: Scheduler,
        stop_process: Callable[[object, bool], object],
        has_pcm_payload: Callable[[], bool],
        cleanup_artifact: Callable[[], None],
        on_ready: Callable[[object], None],
        on_failed: Callable[[CaptureStartupFailure], None],
        *,
        poll_interval_ms: int = 50,
        attempt_timeout_ms: int = 1_200,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            poll_interval_ms <= 0
            or attempt_timeout_ms <= poll_interval_ms
        ):
            raise ValueError("capture startup timing is invalid")
        self._scheduler = scheduler
        self._stop_process = stop_process
        self._has_pcm_payload = has_pcm_payload
        self._cleanup_artifact = cleanup_artifact
        self._on_ready = on_ready
        self._on_failed = on_failed
        self._poll_interval_ms = poll_interval_ms
        self._attempt_timeout_ms = attempt_timeout_ms
        self._monotonic = monotonic
        self._generation = 0
        self._active = False
        self._attempt_count = 0
        self._attempt_deadline = 0.0
        self._spawn: Callable[[], object] | None = None
        self._process = None
        self._handle = None
        self._emergency_lock = threading.Lock()
        self._emergency_processes: dict[int, object] = {}

    @property
    def active(self) -> bool:
        with self._emergency_lock:
            emergency_active = bool(self._emergency_processes)
        return self._active or emergency_active

    @property
    def attempt_count(self) -> int:
        return self._attempt_count

    def start(self, spawn: Callable[[], object]) -> bool:
        if self.active:
            raise RuntimeError("capture startup is already active")
        self._invalidate_handle()
        self._generation += 1
        self._active = True
        self._attempt_count = 0
        self._attempt_deadline = 0.0
        self._attempt_deadline = (
            self._monotonic() + self._attempt_timeout_ms / 1_000
        )
        self._spawn = spawn
        self._process = None
        token = self._generation
        try:
            self._cleanup_artifact()
        except Exception:
            self._finish_failed(
                CaptureStartupFailure.CLEANUP_FAILED,
                cleanup=False,
            )
            return False
        self._launch(token)
        return self._active

    def cancel(self) -> None:
        owned_startup = (
            self._active or self._process is not None or self._handle is not None
        )
        self._generation += 1
        self._active = False
        self._attempt_deadline = 0.0
        self._spawn = None
        self._invalidate_handle()
        if not owned_startup:
            return
        process = self._process
        if process is not None:
            if self._handoff_stop(process):
                self._process = None
            else:
                # Retain explicit ownership if even emergency supervision
                # cannot start. A later cancel can safely retry the handoff.
                self._process = process
                self._active = True
        try:
            self._cleanup_artifact()
        except Exception:
            pass

    def _launch(self, token: int) -> None:
        if token != self._generation or not self._active:
            return
        self._handle = None
        if self._monotonic() >= self._attempt_deadline:
            self._finish_failed(CaptureStartupFailure.CONNECT_TIMEOUT)
            return
        spawn = self._spawn
        if spawn is None:
            self._finish_failed(CaptureStartupFailure.SPAWN_FAILED)
            return
        self._attempt_count += 1
        try:
            process = spawn()
        except Exception:
            self._finish_failed(CaptureStartupFailure.SPAWN_FAILED)
            return
        self._process = process
        self._schedule_poll(token, process)

    def _schedule_poll(self, token: int, process) -> None:
        remaining_ms = max(
            1,
            int((self._attempt_deadline - self._monotonic()) * 1_000),
        )
        delay_ms = min(self._poll_interval_ms, remaining_ms)
        try:
            self._handle = self._scheduler.call_later(
                delay_ms,
                lambda: self._poll(token, process),
            )
        except Exception:
            self._handle = None
            self._finish_failed(CaptureStartupFailure.SCHEDULER_FAILED)

    def _poll(self, token: int, process) -> None:
        if (
            token != self._generation
            or not self._active
            or process is not self._process
        ):
            return
        self._handle = None
        try:
            returncode = process.poll()
        except Exception:
            self._finish_failed(CaptureStartupFailure.RECORDER_EXITED)
            return

        try:
            has_payload = self._has_pcm_payload()
        except Exception:
            self._finish_failed(CaptureStartupFailure.INVALID_ARTIFACT)
            return

        if returncode is not None:
            # poll() reaps a Popen child. Relinquish the completed object before
            # cleanup so the failure path cannot signal an unrelated process.
            self._process = None
            self._finish_failed(CaptureStartupFailure.RECORDER_EXITED)
            return

        if has_payload:
            self._generation += 1
            ready_token = self._generation
            self._active = False
            self._attempt_deadline = 0.0
            self._spawn = None
            # Retain ownership until the callback returns successfully.  A GTK
            # callback exception must not strand a live parecord after the
            # controller has already forgotten it.
            try:
                self._on_ready(process)
            except Exception:
                self._finish_failed(CaptureStartupFailure.CALLBACK_FAILED)
                return
            if ready_token == self._generation and process is self._process:
                self._process = None
            return

        if self._monotonic() >= self._attempt_deadline:
            self._finish_failed(CaptureStartupFailure.CONNECT_TIMEOUT)
            return
        self._schedule_poll(token, process)

    def _finish_failed(
        self,
        reason: CaptureStartupFailure,
        *,
        cleanup: bool = True,
    ) -> None:
        self._generation += 1
        self._active = False
        self._attempt_deadline = 0.0
        self._spawn = None
        self._invalidate_handle()
        process = self._process
        if process is not None:
            if self._handoff_stop(process):
                self._process = None
            else:
                self._process = process
                self._active = True
        if cleanup:
            try:
                self._cleanup_artifact()
            except Exception:
                reason = CaptureStartupFailure.CLEANUP_FAILED
        try:
            self._on_failed(reason)
        except Exception:
            pass

    def _handoff_stop(self, process) -> bool:
        try:
            self._stop_process(process, True)
            return True
        except Exception:
            # The runtime stop callback is a non-blocking ownership handoff. If
            # an injected callback fails before accepting the child, retain it
            # in a background reaper rather than blocking the UI thread.
            ownership_key = id(process)
            with self._emergency_lock:
                self._emergency_processes[ownership_key] = process

            def emergency_reap() -> None:
                reaped = False
                try:
                    try:
                        process.send_signal(signal.SIGINT)
                    except ProcessLookupError:
                        pass
                    except Exception:
                        pass
                    deadline = time.monotonic() + 0.75
                    kill_sent = False
                    while True:
                        try:
                            returncode = process.poll()
                        except Exception:
                            returncode = None
                        if returncode is not None:
                            reaped = True
                            return
                        if not kill_sent and time.monotonic() >= deadline:
                            try:
                                process.kill()
                            except ProcessLookupError:
                                pass
                            except Exception:
                                pass
                            kill_sent = True
                        try:
                            process.wait(timeout=0.25)
                            reaped = True
                            return
                        except subprocess.TimeoutExpired:
                            continue
                        except Exception:
                            time.sleep(0.05)
                finally:
                    if reaped:
                        with self._emergency_lock:
                            if (
                                self._emergency_processes.get(ownership_key)
                                is process
                            ):
                                self._emergency_processes.pop(ownership_key, None)

            try:
                threading.Thread(
                    target=emergency_reap,
                    name="pocketds-startup-emergency-reaper",
                    daemon=True,
                ).start()
            except Exception:
                with self._emergency_lock:
                    if self._emergency_processes.get(ownership_key) is process:
                        self._emergency_processes.pop(ownership_key, None)
                return False
            return True

    def _invalidate_handle(self) -> None:
        if self._handle is not None:
            try:
                self._handle.cancel()
            except Exception:
                pass
            self._handle = None


class VoiceState(str, Enum):
    """Complete user-visible lifecycle for one voice input attempt."""

    IDLE = "idle"
    CONNECTING = "connecting"
    RECORDING = "recording"
    STOPPING = "stopping"
    RECOGNIZING = "recognizing"
    PENDING_PASTE = "pending-paste"
    SUCCESS = "success"
    ERROR = "error"
    DISCARDED = "discarded"


@dataclass(frozen=True)
class VoiceSnapshot:
    state: VoiceState
    generation: int
    target_id: Hashable | None

    @property
    def busy(self) -> bool:
        return self.state in {
            VoiceState.CONNECTING,
            VoiceState.RECORDING,
            VoiceState.STOPPING,
            VoiceState.RECOGNIZING,
            VoiceState.PENDING_PASTE,
        }


class VoiceSessionController:
    """Thread-safe, fail-closed voice target and generation owner.

    Results are eligible for paste only while both the captured editable
    target and the session generation still match.  A mismatch is represented
    explicitly as DISCARDED instead of silently pasting into the new target.
    """

    def __init__(
        self,
        on_state_changed: Callable[[VoiceSnapshot], None] | None = None,
    ) -> None:
        self._on_state_changed = on_state_changed
        self._state = VoiceState.IDLE
        self._generation = 0
        self._target_id: Hashable | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> VoiceState:
        with self._lock:
            return self._state

    def snapshot(self) -> VoiceSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def begin_connecting(self, target_id: Hashable | None) -> int:
        return self._begin(target_id, VoiceState.CONNECTING)

    def begin_recording(self, target_id: Hashable | None) -> int:
        """Begin an already-ready capture (kept for non-runtime adapters/tests)."""

        return self._begin(target_id, VoiceState.RECORDING)

    def _begin(
        self,
        target_id: Hashable | None,
        state: VoiceState,
    ) -> int:
        if target_id is None:
            raise ValueError("voice input requires an editable target")
        with self._lock:
            if self._state in {
                VoiceState.CONNECTING,
                VoiceState.RECORDING,
                VoiceState.STOPPING,
                VoiceState.RECOGNIZING,
                VoiceState.PENDING_PASTE,
            }:
                raise RuntimeError("voice input is already busy")
            self._generation += 1
            self._state = state
            self._target_id = target_id
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot.generation

    def capture_ready(self, generation: int) -> bool:
        return self._transition_if_current(
            generation,
            VoiceState.CONNECTING,
            VoiceState.RECORDING,
        )

    def begin_stopping(
        self,
        generation: int,
        current_target_id: Hashable | None,
        *,
        target_is_valid: bool,
    ) -> bool:
        """Authorize recorder finalization against the still-focused target."""

        return self._target_transition(
            generation,
            VoiceState.RECORDING,
            VoiceState.STOPPING,
            current_target_id,
            target_is_valid=target_is_valid,
        )

    def begin_recognition(
        self,
        generation: int,
        current_target_id: Hashable | None,
        *,
        target_is_valid: bool,
    ) -> bool:
        """Authorize ASR only after finalization and a second target check."""

        return self._target_transition(
            generation,
            VoiceState.STOPPING,
            VoiceState.RECOGNIZING,
            current_target_id,
            target_is_valid=target_is_valid,
        )

    def prepare_paste(
        self,
        generation: int,
        current_target_id: Hashable | None,
        *,
        target_is_valid: bool,
    ) -> bool:
        return self._target_transition(
            generation,
            VoiceState.RECOGNIZING,
            VoiceState.PENDING_PASTE,
            current_target_id,
            target_is_valid=target_is_valid,
        )

    def consume_paste(
        self,
        generation: int,
        current_target_id: Hashable | None,
        *,
        target_is_valid: bool,
    ) -> bool:
        return self._target_transition(
            generation,
            VoiceState.PENDING_PASTE,
            VoiceState.SUCCESS,
            current_target_id,
            target_is_valid=target_is_valid,
            clear_target=True,
        )

    def fail(self, generation: int) -> bool:
        with self._lock:
            if generation != self._generation or self._state not in {
                VoiceState.CONNECTING,
                VoiceState.RECORDING,
                VoiceState.STOPPING,
                VoiceState.RECOGNIZING,
                VoiceState.PENDING_PASTE,
            }:
                return False
            self._state = VoiceState.ERROR
            self._target_id = None
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return True

    def flash_error(self) -> int:
        with self._lock:
            self._generation += 1
            self._state = VoiceState.ERROR
            self._target_id = None
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot.generation

    def cancel(self) -> int:
        with self._lock:
            self._generation += 1
            self._state = VoiceState.IDLE
            self._target_id = None
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot.generation

    def reset(self, generation: int) -> bool:
        with self._lock:
            if generation != self._generation or self._state in {
                VoiceState.CONNECTING,
                VoiceState.RECORDING,
                VoiceState.STOPPING,
                VoiceState.RECOGNIZING,
                VoiceState.PENDING_PASTE,
            }:
                return False
            self._state = VoiceState.IDLE
            self._target_id = None
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return True

    def recognition_is_current(self, generation: int) -> bool:
        with self._lock:
            return (
                generation == self._generation
                and self._state is VoiceState.RECOGNIZING
            )

    def _transition_if_current(
        self,
        generation: int,
        old_state: VoiceState,
        new_state: VoiceState,
    ) -> bool:
        with self._lock:
            if generation != self._generation or self._state is not old_state:
                return False
            self._state = new_state
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return True

    def _target_transition(
        self,
        generation: int,
        old_state: VoiceState,
        new_state: VoiceState,
        current_target_id: Hashable | None,
        *,
        target_is_valid: bool,
        clear_target: bool = False,
    ) -> bool:
        with self._lock:
            if generation != self._generation or self._state is not old_state:
                return False
            if (
                not target_is_valid
                or current_target_id is None
                or current_target_id != self._target_id
            ):
                self._generation += 1
                self._state = VoiceState.DISCARDED
                self._target_id = None
                accepted = False
            else:
                self._state = new_state
                if clear_target:
                    self._target_id = None
                accepted = True
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return accepted

    def _snapshot_locked(self) -> VoiceSnapshot:
        return VoiceSnapshot(
            state=self._state,
            generation=self._generation,
            target_id=self._target_id,
        )

    def _notify(self, snapshot: VoiceSnapshot) -> None:
        if self._on_state_changed is not None:
            self._on_state_changed(snapshot)


def trusted_accessible_id(source) -> Hashable | None:
    """Return the non-fallback identity allowed to authorize voice paste.

    Visibility can tolerate a process-local fallback while an accessibility
    proxy is being torn down.  Voice paste cannot: it must prove that the same
    AT-SPI application bus and object path still own focus.
    """

    try:
        application = getattr(source, "app", None)
        bus_name = getattr(application, "bus_name", None)
        object_path = getattr(source, "path", None)
        if bus_name and object_path:
            return ("atspi", str(bus_name), str(object_path))
    except Exception:
        pass
    return None


def accessible_process_id(source) -> int | None:
    """Return one trustworthy positive AT-SPI application process id.

    GTK keyboard controls emit their own non-editable focus events even though
    the window is configured not to take desktop focus.  The runtime uses this
    identity to ignore only events from its own process while retaining the
    cross-application target revocation enforced by the adapter.
    """

    try:
        process_id = int(source.get_process_id())
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None
    return process_id if process_id > 0 else None


def stable_accessible_id(source) -> Hashable:
    """Return an AT-SPI identity without using mutable label/role text.

    pyatspi.Accessible normally exposes ``app.bus_name`` and ``path`` and its
    own hash is derived from those values.  Defensive fallbacks avoid merging
    unrelated objects if a proxy is partially torn down during an event.
    """

    identity = trusted_accessible_id(source)
    if identity is not None:
        return identity

    class_name = f"{type(source).__module__}.{type(source).__qualname__}"
    try:
        return ("atspi-hash", class_name, hash(source))
    except Exception:
        return ("process-object", class_name, id(source))


def stable_accessible_application_id(source) -> Hashable | None:
    """Return the AT-SPI application identity when the proxy exposes one."""

    try:
        application = getattr(source, "app", None)
        bus_name = getattr(application, "bus_name", None)
        if bus_name:
            return ("atspi-app", str(bus_name))
    except Exception:
        pass
    return None


class SignalToggleReadinessGate:
    """Queue SIGUSR1 toggle parity until the GTK/controller stack is ready."""

    def __init__(self) -> None:
        self._ready_callback: Callable[[], None] | None = None
        self._pending_toggle = False

    @property
    def ready(self) -> bool:
        return self._ready_callback is not None

    def request_toggle(self) -> None:
        callback = self._ready_callback
        if callback is None:
            self._pending_toggle = not self._pending_toggle
            return
        callback()

    def mark_ready(self, callback: Callable[[], None]) -> None:
        if self._ready_callback is not None:
            raise RuntimeError("SIGUSR1 readiness gate already has an owner")
        self._ready_callback = callback
        if self._pending_toggle:
            self._pending_toggle = False
            callback()

    def clear(self) -> None:
        self._ready_callback = None
        self._pending_toggle = False


class SignalActionReadinessGate:
    """Coalesce one idempotent signal action until its UI owner is ready."""

    def __init__(self) -> None:
        self._ready_callback: Callable[[], None] | None = None
        self._pending = False

    @property
    def ready(self) -> bool:
        return self._ready_callback is not None

    def request(self) -> None:
        callback = self._ready_callback
        if callback is None:
            self._pending = True
            return
        callback()

    def mark_ready(self, callback: Callable[[], None]) -> None:
        if self._ready_callback is not None:
            raise RuntimeError("signal readiness gate already has an owner")
        self._ready_callback = callback
        if self._pending:
            self._pending = False
            callback()

    def clear(self) -> None:
        self._ready_callback = None
        self._pending = False


class KeyboardVisibilityAdapter:
    """Bridge AT-SPI and voice lifecycle events to VisibilityController."""

    def __init__(
        self,
        scheduler: Scheduler,
        is_source_valid: Callable[[object], bool],
        on_state_changed: Callable[[VisibilitySnapshot], None],
        *,
        identity: Callable[[object], Hashable] = stable_accessible_id,
        application_identity: Callable[[object], Hashable | None] = (
            stable_accessible_application_id
        ),
        settle_ms: int = 900,
        focus_loss_ms: int = 380,
    ) -> None:
        self._is_source_valid = is_source_valid
        self._identity = identity
        self._application_identity = application_identity
        self.focused_editable = None
        self.voice_busy = False
        self._controller = VisibilityController(
            scheduler,
            self._current_focus_is_valid,
            on_state_changed,
            settle_ms=settle_ms,
            focus_loss_ms=focus_loss_ms,
        )

    @property
    def state(self) -> VisibilityState:
        return self._controller.state

    @property
    def visible(self) -> bool:
        return self._controller.visible

    def snapshot(self) -> VisibilitySnapshot:
        return self._controller.snapshot()

    def manual_show(self) -> None:
        self._controller.manual_show()

    def manual_hide(self) -> None:
        self._controller.manual_hide()

    def manual_toggle(self) -> None:
        self._controller.manual_toggle()

    def editable_focus_gained(self, source) -> None:
        self.focused_editable = source
        self._controller.editable_focus_gained(self._identity(source))

    def editable_focus_lost(self, source) -> None:
        current_id = self.snapshot().editable_focus_id
        # A proxy can lose its bus/path metadata as the application exits.
        # Preserve the identity captured on focus gain when this is the exact
        # stored proxy; recomputing it would fall back to an unrelated hash.
        focus_id = (
            current_id
            if current_id is not None and source is self.focused_editable
            else self._identity(source)
        )
        if current_id is not None and focus_id != current_id:
            self._controller.editable_focus_lost(focus_id)
            return
        self.focused_editable = None
        self._controller.editable_focus_lost(focus_id)

    def noneditable_focus_gained(self, source=None) -> None:
        if self.focused_editable is None:
            return
        if source is not None:
            old_application = self._application_identity(self.focused_editable)
            new_application = self._application_identity(source)
            if (
                old_application is not None
                and new_application is not None
                and old_application != new_application
            ):
                # Qt 6 can leave an inactive terminal proxy marked FOCUSED.
                # A real focus event from another AT-SPI application is
                # stronger evidence than that stale state bit.
                self._release_current_focus()
                return
        if self.voice_busy:
            # A focused non-editable is enough to revoke paste authority even
            # when a toolkit leaves the old editable proxy marked FOCUSED.
            # Voice keeps the window visible through _current_focus_is_valid;
            # it must not keep the old injection target alive.
            self._release_current_focus()
            return
        # Chromium may focus a document object immediately after a legitimate
        # form field. Do not discard the field while its own state remains
        # focused, visible, showing, and editable.
        if self._is_source_valid(self.focused_editable):
            return
        self._release_current_focus()

    def set_voice_busy(self, busy: bool) -> None:
        if busy == self.voice_busy:
            return
        self.voice_busy = busy
        if busy:
            return

        # Revalidate even if AT-SPI dropped the loss event while ASR was busy.
        if self.focused_editable is None:
            # A validation queued at focus loss may already have fired while
            # the voice visibility hold was active. Schedule one fresh bounded
            # validation now that the hold is gone.
            self._controller.editable_focus_lost()
        elif not self._current_focus_is_valid():
            self._release_current_focus()

    def revalidate_focus(self) -> None:
        """Release a vanished editable even when AT-SPI omits focus loss.

        Application shutdown can remove its accessibility tree without
        delivering object:state-changed:focused(false).  The GTK runtime calls
        this from a low-frequency watchdog; the normal focus-loss debounce
        remains authoritative, and voice input keeps its existing hold.
        """

        if self.focused_editable is None or self.voice_busy:
            return
        if not self._current_focus_is_valid():
            self._release_current_focus()

    def shutdown(self) -> None:
        self._controller.shutdown()

    def _current_focus_is_valid(self) -> bool:
        # The existing keyboard deliberately stays visible while recording or
        # recognizing. Treat voice as a temporary focus-validity hold.
        if self.voice_busy:
            return True
        source = self.focused_editable
        return source is not None and self._is_source_valid(source)

    def _release_current_focus(self) -> None:
        source = self.focused_editable
        focus_id = self.snapshot().editable_focus_id
        self.focused_editable = None
        if focus_id is None and source is not None:
            focus_id = self._identity(source)
        self._controller.editable_focus_lost(focus_id)

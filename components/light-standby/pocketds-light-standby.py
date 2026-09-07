#!/usr/bin/env python3
"""Keep a closed Pocket DS dark; native KDE/logind alone request system sleep."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from typing import Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402


LOGIN1_SERVICE = "org.freedesktop.login1"
LOGIN1_PATH = "/org/freedesktop/login1"
LOGIN1_MANAGER = "org.freedesktop.login1.Manager"
DBUS_PROPERTIES = "org.freedesktop.DBus.Properties"
POWERDEVIL_SERVICE = "org.kde.Solid.PowerManagement"
POWERDEVIL_PATH = "/org/kde/Solid/PowerManagement"
POWERDEVIL_INTERFACE = "org.kde.Solid.PowerManagement"
PROFILE_FILE = Path("/etc/tuned/active_profile")
PROFILE_NAMES = {
    "pocketds-powersave": "powersave",
    "pocketds-balanced": "balanced",
    "pocketds-performance": "performance",
}
MANAGED_PROFILES = frozenset(PROFILE_NAMES.values())
VALID_PHASES = frozenset(
    {"pending", "owned", "baseline", "external", "unmanaged", "restore-pending"}
)
AUTO_POWEROFF_MINUTES = frozenset({0, 60, 120, 240, 480})
# The deployed logind delay budget is five seconds. This four-second bound
# starts when our callback runs; earlier main-loop delays can consume logind's
# budget already. The independent root precheck must still reject unsafe sleep.
SLEEP_DISPLAY_PREPARE_SECONDS = 4.0


def emit(event: str, **fields: Any) -> None:
    payload = {"event": event, **fields}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


class StateFileError(RuntimeError):
    pass


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def exists(self) -> bool:
        try:
            self.path.lstat()
            return True
        except FileNotFoundError:
            return False

    def load(self) -> dict[str, Any] | None:
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise StateFileError("state file is not a regular file owned by this user")
        if metadata.st_mode & 0o077:
            raise StateFileError("state file permissions are wider than 0600")
        if metadata.st_size > 4096:
            raise StateFileError("state file is too large")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise StateFileError(f"state file cannot be read: {error}") from error
        if not isinstance(data, dict) or data.get("schema") != 1 or data.get("active") is not True:
            raise StateFileError("state file has an invalid schema")
        restore_profile = data.get("restore_profile")
        if restore_profile is not None and restore_profile not in MANAGED_PROFILES:
            raise StateFileError("state file has an invalid restore profile")
        if data.get("power_phase") not in VALID_PHASES:
            raise StateFileError("state file has an invalid power phase")
        timeout = data.get("auto_poweroff_minutes")
        deadline = data.get("auto_poweroff_deadline_boottime_us")
        boot_id = data.get("auto_poweroff_boot_id")
        if timeout is not None and (
            not isinstance(timeout, int)
            or isinstance(timeout, bool)
            or timeout not in AUTO_POWEROFF_MINUTES - {0}
        ):
            raise StateFileError("state file has an invalid auto poweroff timeout")
        if deadline is not None and (
            not isinstance(deadline, int) or isinstance(deadline, bool) or deadline <= 0
        ):
            raise StateFileError("state file has an invalid auto poweroff deadline")
        if any(value is None for value in (timeout, deadline, boot_id)) != all(
            value is None for value in (timeout, deadline, boot_id)
        ):
            raise StateFileError("state file has an incomplete auto poweroff deadline")
        if boot_id is not None and (
            not isinstance(boot_id, str) or not boot_id or len(boot_id) > 64
        ):
            raise StateFileError("state file has an invalid auto poweroff boot id")
        return data

    def save(self, data: dict[str, Any]) -> None:
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = parent / f".{self.path.name}.{os.getpid()}.{time.time_ns()}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def remove(self) -> None:
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise StateFileError("refusing to remove an unsafe state path")
        self.path.unlink()


class AutoPoweroffSettings:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> int:
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return 0
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise StateFileError("auto poweroff setting is not a regular file owned by this user")
        if metadata.st_mode & 0o077:
            raise StateFileError("auto poweroff setting permissions are wider than 0600")
        if metadata.st_size > 32:
            raise StateFileError("auto poweroff setting is too large")
        try:
            text = self.path.read_text(encoding="ascii").strip()
            value = int(text, 10)
        except (OSError, UnicodeError, ValueError) as error:
            raise StateFileError(f"auto poweroff setting cannot be read: {error}") from error
        if str(value) != text or value not in AUTO_POWEROFF_MINUTES:
            raise StateFileError("auto poweroff setting is invalid")
        return value

    def save(self, minutes: int) -> None:
        if minutes not in AUTO_POWEROFF_MINUTES:
            raise ValueError("unsupported auto poweroff timeout")
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = parent / f".{self.path.name}.{os.getpid()}.{time.time_ns()}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(f"{minutes}\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


class LiveAdapter:
    def __init__(self) -> None:
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.session_bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._mode_sample: tuple[float, str] = (0.0, "unknown")
        self._off_requested_at: float | None = None

    def get_lid_closed(self, timeout_seconds: float = 2.0) -> bool:
        reply = self.bus.call_sync(
            LOGIN1_SERVICE,
            LOGIN1_PATH,
            DBUS_PROPERTIES,
            "Get",
            GLib.Variant("(ss)", (LOGIN1_MANAGER, "LidClosed")),
            GLib.VariantType.new("(v)"),
            Gio.DBusCallFlags.NONE,
            max(1, int(timeout_seconds * 1000)),
            None,
        )
        value = reply.get_child_value(0).get_variant()
        if not value.is_of_type(GLib.VariantType.new("b")):
            raise RuntimeError("login1 returned a non-boolean LidClosed value")
        return value.get_boolean()

    def get_lid_mode(self, refresh: bool = False) -> str:
        now = time.monotonic()
        if not refresh and now - self._mode_sample[0] < 5:
            return self._mode_sample[1]
        # Only an explicit connected mode may start its profile/shutdown logic.
        # Native sleep failures must not silently select connected auto-poweroff.
        helper = Path.home() / ".local/libexec/pocketds/pocketds-lid-mode"
        mode = "unknown"
        try:
            reply = subprocess.run(
                [str(helper), 'owner'], capture_output=True, text=True,
                timeout=1.5, check=False,
            )
            if reply.returncode == 0 and len(reply.stdout) <= 16384:
                status = json.loads(reply.stdout)
                if (isinstance(status, dict) and isinstance(status.get("mode"), str)
                        and status["mode"] in {"connected", "sleep"}):
                    mode = status["mode"]
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
        self._mode_sample = (now, mode)
        return mode

    def acquire_sleep_delay(self) -> int:
        reply, descriptors = self.bus.call_with_unix_fd_list_sync(
            LOGIN1_SERVICE, LOGIN1_PATH, LOGIN1_MANAGER, "Inhibit",
            GLib.Variant("(ssss)", ("sleep", "Pocket DS display standby",
                                   "Blank both internal displays before sleep", "delay")),
            GLib.VariantType.new("(h)"), Gio.DBusCallFlags.NONE, 1500, None, None,
        )
        return descriptors.get(reply.unpack()[0])

    def get_preparing_for_sleep(self) -> bool:
        reply = self.bus.call_sync(
            LOGIN1_SERVICE, LOGIN1_PATH, DBUS_PROPERTIES, "Get",
            GLib.Variant("(ss)", (LOGIN1_MANAGER, "PreparingForSleep")),
            GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 1000, None,
        )
        value = reply.get_child_value(0).get_variant()
        if not value.is_of_type(GLib.VariantType.new("b")):
            raise RuntimeError("login1 returned a non-boolean PreparingForSleep value")
        return value.get_boolean()

    def subscribe_sleep(self, callback: Any) -> int:
        return self.bus.signal_subscribe(
            LOGIN1_SERVICE, LOGIN1_MANAGER, "PrepareForSleep", LOGIN1_PATH,
            None, Gio.DBusSignalFlags.NONE, callback,
        )

    def subscribe(self, callback: Any) -> int:
        return self.bus.signal_subscribe(
            None,
            DBUS_PROPERTIES,
            "PropertiesChanged",
            LOGIN1_PATH,
            LOGIN1_MANAGER,
            Gio.DBusSignalFlags.NONE,
            callback,
        )

    def get_external_power(self) -> bool:
        reply = self.bus.call_sync(
            LOGIN1_SERVICE,
            LOGIN1_PATH,
            DBUS_PROPERTIES,
            "Get",
            GLib.Variant("(ss)", (LOGIN1_MANAGER, "OnExternalPower")),
            GLib.VariantType.new("(v)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        )
        value = reply.get_child_value(0).get_variant()
        if not value.is_of_type(GLib.VariantType.new("b")):
            raise RuntimeError("login1 returned a non-boolean OnExternalPower value")
        return value.get_boolean()

    @staticmethod
    def current_boot_id() -> str:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        if not value or len(value) > 64:
            raise RuntimeError("kernel returned an invalid boot id")
        return value

    def power_off(self) -> bool:
        self.bus.call_sync(
            LOGIN1_SERVICE,
            LOGIN1_PATH,
            LOGIN1_MANAGER,
            "PowerOff",
            GLib.Variant("(b)", (False,)),
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
        )
        return True

    @staticmethod
    def current_profile() -> str | None:
        try:
            value = PROFILE_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return PROFILE_NAMES.get(value)

    @staticmethod
    def _graphical_environment() -> dict[str, str] | None:
        required = ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY")
        if any(not os.environ.get(name) for name in required):
            return None
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = "wayland"
        environment["QT_ACCESSIBILITY"] = "0"
        # The Plasma session exports this as 1 for interactive applications.
        # kscreen-doctor is a short-lived control helper, and registering each
        # probe on AT-SPI makes the keyboard observe a stale application proxy.
        environment["QT_LINUX_ACCESSIBILITY_ALWAYS_ON"] = "0"
        return environment

    @staticmethod
    def _run(
        command: list[str],
        timeout_seconds: float,
        environment: dict[str, str] | None = None,
    ) -> bool:
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            emit("command-failed", command=command[0], error=str(error)[:240])
            return False
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:240]
            emit("command-failed", command=command[0], returncode=result.returncode, detail=detail)
            return False
        return True

    def dpms_status(self, timeout_seconds: float = 4.0) -> dict[str, str] | None:
        environment = self._graphical_environment()
        if environment is None:
            return None
        try:
            result = subprocess.run(
                ["/usr/bin/kscreen-doctor", "--dpms", "show"],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0 or len(result.stdout) > 4096:
            return None
        states: dict[str, str] = {}
        prefix = "dpms mode for screen "
        for line in result.stdout.splitlines():
            if not line.startswith(prefix) or ": " not in line:
                return None
            name, mode = line[len(prefix) :].rsplit(": ", 1)
            if not name or name in states or mode not in {"on", "off"}:
                return None
            states[name] = mode
        return states or None

    def forget_pending_off(self) -> None:
        self._off_requested_at = None

    def request_dpms(self, mode: str, timeout_seconds: float, reuse_off: bool = False) -> bool:
        if mode not in {"on", "off"}:
            return False
        pending = getattr(self, "_off_requested_at", None)
        # A normal lid callback may have queued Off just before PrepareForSleep.
        # Reuse only that recent request, never evidence that the output is dark.
        if mode == "off" and reuse_off and pending is not None and time.monotonic() - pending < 1:
            return True
        self.forget_pending_off()
        environment = self._graphical_environment()
        if environment is None:
            emit("dpms-skipped", reason="wayland-session-environment-unavailable", mode=mode)
            return False
        if not self._run(
            ["/usr/bin/kscreen-doctor", "--dpms", mode],
            timeout_seconds,
            environment,
        ):
            return False
        if mode == "off":
            self._off_requested_at = time.monotonic()
        return True

    def set_dpms(self, mode: str, timeout_seconds: float = 4.0) -> bool:
        if not self.request_dpms(mode, timeout_seconds):
            return False
        states = self.dpms_status(timeout_seconds)
        if not self.dpms_state_matches(mode, states):
            emit("dpms-verification-failed", requested=mode, states=states)
            return False
        emit("dpms", mode=mode, displays=sorted(states))
        return True

    @staticmethod
    def displays_dark(strict: bool = False) -> bool:
        try:
            top = Path("/sys/class/backlight/ae94000.dsi.0")
            bottom = Path("/sys/class/backlight/sy7758-backlight")
            allowed = {4} if strict else {1, 2, 3, 4}
            return (all(int((path / "bl_power").read_text().strip()) in allowed
                        for path in (top, bottom))
                    and all(int((bottom / field).read_text().strip()) == 0
                            for field in ("brightness", "actual_brightness")))
        except (OSError, ValueError):
            return False

    def blank_bottom(self, timeout_seconds: float) -> bool:
        return self._run(["sudo", "-n", "/usr/local/libexec/pocketds-panel-root",
                          "backlight-blank", "bottom"], timeout_seconds)

    def blank_displays(self) -> bool:
        # Never change enabled outputs or rewrite KWin's lid-specific topology.
        # Sleep preparation separately waits for KWin's asynchronous transition.
        timeout = 2.0
        if not self.set_dpms("off", timeout):
            return False
        if not self.displays_dark():
            if not self.blank_bottom(timeout):
                return False
        okay = self.displays_dark()
        if not okay:
            emit("physical-blank-verification-failed")
        return okay

    def wake_display(self) -> bool:
        self.forget_pending_off()
        if not self._call_powerdevil("wakeup"):
            return False

        # PowerDevil's wakeup method resets KIdleTime as well as requesting
        # DPMS on. On 6.7.3 it does not re-register an idle action that has
        # already fired, so force-reload the current profile before returning.
        if not self._call_powerdevil("refreshStatus"):
            return False

        # Keep the existing exact dual-panel verification and retry path for
        # the upper panel's hinge-time reconnect window.
        states = self.dpms_status()
        if self.dpms_state_matches("on", states):
            emit("dpms", mode="on", displays=sorted(states), source="powerdevil-wakeup")
            return True
        emit("dpms-wakeup-retry", states=states)
        return self.set_dpms("on")

    def _call_powerdevil(self, method: str) -> bool:
        try:
            self.session_bus.call_sync(
                POWERDEVIL_SERVICE,
                POWERDEVIL_PATH,
                POWERDEVIL_INTERFACE,
                method,
                None,
                None,
                Gio.DBusCallFlags.NONE,
                4000,
                None,
            )
        except GLib.Error as error:
            emit("powerdevil-call-failed", method=method, error=str(error)[:240])
            return False
        return True

    @staticmethod
    def dpms_state_matches(mode: str, states: dict[str, str] | None) -> bool:
        if states is None or mode not in {"on", "off"}:
            return False
        if mode == "on":
            # Opening must restore both internal panels. A temporarily absent
            # upper panel is retried while the hinge/display stack reconnects.
            return all(states.get(name) == "on" for name in ("DSI-1", "DSI-2"))

        # Closing the lid can physically disable DSI-1 before KWin reports the
        # global DPMS result, in which case it disappears from ``--dpms show``.
        # DSI-2 must always be present and off; DSI-1 may be absent, but if it
        # is still listed it must also be off.
        return states.get("DSI-2") == "off" and states.get("DSI-1", "off") == "off"

    def set_profile(self, profile: str) -> bool:
        if profile not in MANAGED_PROFILES:
            return False
        return self._run(
            ["/usr/local/bin/pocketds-panelctl", "power", profile],
            4.0,
        )


class LightStandbyController:
    def __init__(self, adapter: Any, store: StateStore, settings: AutoPoweroffSettings) -> None:
        self.adapter = adapter
        self.store = store
        self.settings = settings

    def _safe_load(self) -> tuple[dict[str, Any] | None, bool]:
        try:
            return self.store.load(), True
        except StateFileError as error:
            emit("state-invalid", error=str(error))
            return None, False

    @staticmethod
    def _clear_auto_poweroff(state: dict[str, Any]) -> bool:
        changed = False
        for key in (
            "auto_poweroff_minutes",
            "auto_poweroff_deadline_boottime_us",
            "auto_poweroff_boot_id",
        ):
            if key in state:
                del state[key]
                changed = True
        return changed

    def sync_auto_poweroff(self, lid_closed: bool, now_us: int | None = None) -> bool:
        state, state_safe = self._safe_load()
        if not state_safe or state is None:
            return state_safe
        now = (
            time.clock_gettime_ns(time.CLOCK_BOOTTIME) // 1000
            if now_us is None
            else now_us
        )
        try:
            minutes = self.settings.load()
        except StateFileError as error:
            emit("auto-poweroff-setting-invalid", error=str(error))
            if self._clear_auto_poweroff(state):
                self.store.save(state)
            return True

        if not lid_closed or minutes == 0:
            if self._clear_auto_poweroff(state):
                self.store.save(state)
            return True

        try:
            external_power = self.adapter.get_external_power()
        except Exception as error:
            emit("external-power-read-failed", error=str(error)[:240])
            if self._clear_auto_poweroff(state):
                self.store.save(state)
            return True

        if external_power:
            if self._clear_auto_poweroff(state):
                self.store.save(state)
            return True

        try:
            boot_id = self.adapter.current_boot_id()
        except Exception as error:
            emit("boot-id-read-failed", error=str(error)[:240])
            if self._clear_auto_poweroff(state):
                self.store.save(state)
            return True

        deadline = state.get("auto_poweroff_deadline_boottime_us")
        if (
            deadline is None
            or state.get("auto_poweroff_minutes") != minutes
            or state.get("auto_poweroff_boot_id") != boot_id
        ):
            deadline = now + minutes * 60 * 1_000_000
            state["auto_poweroff_minutes"] = minutes
            state["auto_poweroff_deadline_boottime_us"] = deadline
            state["auto_poweroff_boot_id"] = boot_id
            self.store.save(state)
            emit("auto-poweroff-armed", minutes=minutes, deadline_boottime_us=deadline)
            return True
        if now < deadline:
            return True

        # The timeout is self-owned state, not login1's global shutdown slot.
        # Re-check every condition immediately before requesting poweroff.
        try:
            still_closed = self.adapter.get_lid_closed()
            still_external_power = self.adapter.get_external_power()
            confirmed_minutes = self.settings.load()
        except Exception as error:
            emit("auto-poweroff-final-check-failed", error=str(error)[:240])
            return False
        if not still_closed or still_external_power or confirmed_minutes == 0:
            if self._clear_auto_poweroff(state):
                self.store.save(state)
            return True
        if confirmed_minutes != minutes:
            deadline = now + confirmed_minutes * 60 * 1_000_000
            state["auto_poweroff_minutes"] = confirmed_minutes
            state["auto_poweroff_deadline_boottime_us"] = deadline
            state["auto_poweroff_boot_id"] = boot_id
            self.store.save(state)
            emit(
                "auto-poweroff-rearmed",
                minutes=confirmed_minutes,
                deadline_boottime_us=deadline,
            )
            return True
        try:
            if not self.adapter.power_off():
                raise RuntimeError("login1 rejected the poweroff request")
        except Exception as error:
            emit("auto-poweroff-request-failed", error=str(error)[:240])
            return False
        emit("auto-poweroff-requested", minutes=minutes, deadline_boottime_us=deadline)
        return True

    def keep_display_off(self) -> bool:
        return self.adapter.displays_dark() or self.adapter.blank_displays()

    def close(self, connected: bool = True) -> bool:
        if not connected:
            # Illumination is independent of permission to suspend. Keep the
            # lid dark even if native sleep is inhibited, rejected or aborted.
            self.sync_auto_poweroff(lid_closed=False)
            if not self.adapter.blank_displays():
                return False
            # Release a previous connected-mode timer/profile without waking.
            return self.open(wake_display=False)
        state, state_safe = self._safe_load()
        if state is None and state_safe:
            previous = self.adapter.current_profile()
            phase = "pending" if previous in MANAGED_PROFILES and previous != "powersave" else "baseline"
            if previous not in MANAGED_PROFILES:
                previous = None
                phase = "unmanaged"
            state = {
                "schema": 1,
                "active": True,
                "restore_profile": previous,
                "power_phase": phase,
                "closed_unix_ms": int(time.time() * 1000),
            }
            self.store.save(state)

        # PowerDevil's generic screen-off action only reaches one DSI panel on
        # the current image. Own one verified global KWin DPMS transition here.
        if not self.adapter.blank_displays():
            return False

        # Re-check the lid before changing the profile after a fast reopen.
        try:
            if not self.adapter.get_lid_closed():
                return self.open(wake_display=True)
        except Exception as error:  # D-Bus failure is retried by the daemon loop.
            emit("lid-read-failed", error=str(error)[:240])
            return False

        if not state_safe or state is None:
            emit("closed", profile_action="skipped-invalid-state")
            return True

        phase = state["power_phase"]
        current = self.adapter.current_profile()
        if phase == "pending":
            if current == "powersave":
                state["power_phase"] = "owned"
            elif current == state.get("restore_profile"):
                if not self.adapter.set_profile("powersave"):
                    return False
                if self.adapter.current_profile() != "powersave":
                    emit("profile-verification-failed", requested="powersave")
                    return False
                state["power_phase"] = "owned"
            else:
                state["power_phase"] = "external"
            self.store.save(state)
        elif phase == "owned" and current != "powersave":
            state["power_phase"] = "external"
            self.store.save(state)

        emit("closed", power_phase=state["power_phase"], restore_profile=state.get("restore_profile"))
        return self.sync_auto_poweroff(lid_closed=True)

    def open(self, wake_display: bool = True) -> bool:
        state, state_safe = self._safe_load()
        if state_safe and state is not None and self._clear_auto_poweroff(state):
            # Clear the deadline before profile recovery. A failed profile
            # restore must never leave an open-lid shutdown deadline behind.
            self.store.save(state)

        if wake_display and not self.adapter.wake_display():
            return False

        if not state_safe:
            emit("opened", profile_action="skipped-invalid-state")
            return True
        if state is None:
            emit("opened", profile_action="none")
            return True

        phase = state["power_phase"]
        restore_profile = state.get("restore_profile")
        current = self.adapter.current_profile()
        should_restore = (
            phase in {"pending", "owned", "restore-pending"}
            and restore_profile in MANAGED_PROFILES
            and restore_profile != "powersave"
            and current == "powersave"
        )
        if should_restore:
            state["power_phase"] = "restore-pending"
            self.store.save(state)
            if not self.adapter.set_profile(restore_profile):
                return False
            if self.adapter.current_profile() != restore_profile:
                emit("profile-verification-failed", requested=restore_profile)
                return False
        self.store.remove()
        emit("opened", profile_action="restored" if should_restore else "preserved", profile=restore_profile)
        return True


class LidDaemon:
    def __init__(self, adapter: LiveAdapter, controller: LightStandbyController) -> None:
        self.adapter = adapter
        self.controller = controller
        self.loop = GLib.MainLoop()
        self.last_lid: bool | None = None
        self.last_mode = "unknown"
        self.retry = False
        self.debounce_source = 0
        self.preparing = False
        self.sleep_delay_fd: int | None = None
        self.sleep_delay_retry_at = 0.0
        self.next_check_at = 0.0
        self.resume_recheck_until = 0.0
        self.prepare_generation = 0
        self.prepare_source = 0
        self.prepare_deadline = 0.0
        self.prepare_commands_started = False
        self.prepare_bottom_blanked = False
        self.prepare_finished = False

    def _acquire_sleep_delay(self) -> None:
        if (self.sleep_delay_fd is not None or self.preparing
                or time.monotonic() < self.sleep_delay_retry_at):
            return
        try:
            self.sleep_delay_fd = self.adapter.acquire_sleep_delay()
        except Exception as error:
            self.sleep_delay_retry_at = time.monotonic() + 5
            emit("sleep-delay-unavailable", error=str(error)[:240])

    def _release_sleep_delay(self) -> None:
        if self.sleep_delay_fd is not None:
            descriptor, self.sleep_delay_fd = self.sleep_delay_fd, None
            os.close(descriptor)

    def _cancel_prepare(self) -> None:
        self.prepare_generation += 1
        if self.prepare_source:
            GLib.source_remove(self.prepare_source)
            self.prepare_source = 0

    def _finish_prepare(self, generation: int, okay: bool, reason: str) -> None:
        if generation != self.prepare_generation or not self.preparing or self.prepare_finished:
            return
        self.prepare_finished = True
        emit("sleep-display-prepared", okay=okay, reason=reason)
        # Failure is explicit; the independent root precheck still refuses
        # non-dark hardware. A delay inhibitor cannot veto system sleep.
        self._release_sleep_delay()

    def _prepare_timeout(self, maximum: float) -> float:
        remaining = self.prepare_deadline - time.monotonic()
        if remaining <= 0.002:
            raise TimeoutError("display preparation deadline reached")
        return min(maximum, remaining)

    def _prepare_step(self, generation: int) -> bool:
        if generation != self.prepare_generation or not self.preparing or self.prepare_finished:
            return GLib.SOURCE_REMOVE
        self.prepare_source = 0
        try:
            if not self.adapter.get_lid_closed(self._prepare_timeout(0.2)):
                self._finish_prepare(generation, False, "lid-open")
                return GLib.SOURCE_REMOVE
            if not self.prepare_commands_started:
                # Mark before calling: a timeout can follow a successfully
                # queued request. Do not enqueue another Off on each poll.
                self.prepare_commands_started = True
                self.adapter.request_dpms("off", self._prepare_timeout(0.5), reuse_off=True)
            states = self.adapter.dpms_status(self._prepare_timeout(0.4))
            if self.adapter.dpms_state_matches("off", states):
                # The root helper deliberately refuses raw-zero writes while
                # DPMS is on. Wait for KWin's transition before invoking it;
                # a lagging kernel bl_power may still refuse, so retry only
                # this narrow operation, never requeue the DPMS transition.
                if not self.adapter.displays_dark(strict=True) and not self.prepare_bottom_blanked:
                    if not self.adapter.get_lid_closed(self._prepare_timeout(0.2)):
                        self._finish_prepare(generation, False, "lid-open")
                        return GLib.SOURCE_REMOVE
                    self.prepare_bottom_blanked = self.adapter.blank_bottom(self._prepare_timeout(0.5))
            if self.adapter.dpms_state_matches("off", states) and self.adapter.displays_dark(strict=True):
                # Opening during a control/query call cancels preparation; it
                # must not turn an old lid close into a successful readiness.
                closed = self.adapter.get_lid_closed(self._prepare_timeout(0.2))
                self._prepare_timeout(0.001)
                self._finish_prepare(generation, closed, "dark" if closed else "lid-open")
                return GLib.SOURCE_REMOVE
            remaining = self._prepare_timeout(0.075)
            self.prepare_source = GLib.timeout_add(max(1, int(remaining * 1000)),
                                                  self._prepare_step, generation)
        except TimeoutError:
            self._finish_prepare(generation, False, "deadline")
        except Exception as error:
            emit("sleep-display-prepare-failed", error=str(error)[:240])
            self._finish_prepare(generation, False, "query-failed")
        return GLib.SOURCE_REMOVE

    def prepare_sleep(self, active: bool) -> None:
        if active:
            if self.preparing:
                return
            self.preparing = True
            self.resume_recheck_until = 0.0
            self._cancel_prepare()
            if self.debounce_source:
                GLib.source_remove(self.debounce_source)
                self.debounce_source = 0
            self.prepare_deadline = time.monotonic() + SLEEP_DISPLAY_PREPARE_SECONDS
            self.prepare_commands_started = False
            self.prepare_bottom_blanked = False
            self.prepare_finished = False
            self._prepare_step(self.prepare_generation)
            return
        # Invalidate queued work before a new inhibitor is acquired. A stale
        # callback must never blank an opened device or release the next FD.
        self._cancel_prepare()
        self._release_sleep_delay()
        self.adapter.forget_pending_off()
        self.preparing = False
        self._acquire_sleep_delay()
        self.retry = True
        self._apply()
        # PowerDevil may issue DPMS On later in the same resume burst. Check
        # briefly at 250 ms, then return to ordinary one-second maintenance.
        self.resume_recheck_until = time.monotonic() + 2
        self.next_check_at = time.monotonic() + 0.25

    def _tick(self) -> bool:
        if time.monotonic() >= self.next_check_at:
            self._apply()
            now = time.monotonic()
            fast = self.last_lid is not False and now < self.resume_recheck_until
            self.next_check_at = now + (0.25 if fast else 1)
        return True

    def _on_sleep_signal(self, _connection: Any, _sender: str, _path: str,
                         _interface: str, _signal: str, parameters: Any) -> None:
        values = parameters.unpack()
        if len(values) == 1 and type(values[0]) is bool:
            self.prepare_sleep(values[0])

    def _apply(self) -> bool:
        if self.preparing:
            if not self.prepare_finished and self.prepare_source:
                # The preparation transaction owns bounded queries until it
                # completes. Do not spend its budget on the normal 1 s getter.
                return True
            # Reconcile a missed completion signal, including an aborted unit.
            try:
                if not self.adapter.get_preparing_for_sleep():
                    self.prepare_sleep(False)
            except Exception as error:
                emit("sleep-state-read-failed", error=str(error)[:240])
            return True
        self._acquire_sleep_delay()
        try:
            closed = self.adapter.get_lid_closed()
            mode = self.adapter.get_lid_mode(refresh=self.last_lid is not True) if closed else self.last_mode
            # Native ownership is a bounded external query. A lid opened while
            # it was in flight must follow the open/recovery path immediately.
            if closed:
                closed = self.adapter.get_lid_closed()
        except Exception as error:
            emit("lid-read-failed", error=str(error)[:240])
            self.retry = True
            return True

        changed = self.last_lid is not None and closed != self.last_lid
        first = self.last_lid is None
        needs_recovery = self.retry or (not closed and self.controller.store.exists())
        self.last_lid = closed
        owner_changed = mode != self.last_mode
        self.last_mode = mode
        if first and not closed and not needs_recovery:
            emit("ready", lid_closed=False)
            self.retry = False
            return True
        if not (first or changed or needs_recovery or (closed and owner_changed)):
            if closed:
                # Resume or unrelated input can relight an unchanged closed lid.
                # Verify physical blanking even without a lid transition.
                okay = self.controller.keep_display_off()
                if mode == "connected":
                    okay = self.controller.sync_auto_poweroff(lid_closed=True) and okay
                else:
                    okay = self.controller.sync_auto_poweroff(lid_closed=False) and okay
                self.retry = not okay
            else:
                self.retry = False
            return True

        try:
            okay = self.controller.close(connected=mode == "connected") if closed else self.controller.open()
        except (OSError, StateFileError) as error:
            emit("transition-failed", lid_closed=closed, error=str(error)[:240])
            okay = False
        self.retry = not okay
        return True

    def _on_signal(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _path: str,
        _interface: str,
        _signal: str,
        parameters: GLib.Variant,
    ) -> None:
        try:
            _manager, changed, _invalidated = parameters.unpack()
        except (TypeError, ValueError):
            return
        if "LidClosed" not in changed:
            return
        if self.preparing:
            return  # The preparation poll reads the current lid, without waking.
        if self.debounce_source:
            GLib.source_remove(self.debounce_source)
        self.debounce_source = GLib.timeout_add(250, self._after_debounce)

    def _after_debounce(self) -> bool:
        self.debounce_source = 0
        self._apply()
        return GLib.SOURCE_REMOVE

    def run(self) -> int:
        self.adapter.subscribe(self._on_signal)
        self.adapter.subscribe_sleep(self._on_sleep_signal)
        try:
            self.preparing = self.adapter.get_preparing_for_sleep()
            self._apply()
            GLib.timeout_add(250, self._tick)
            self.loop.run()
        finally:
            self._cancel_prepare()
            self._release_sleep_delay()
        return 0


def default_state_path() -> Path:
    configured = os.environ.get("POCKETDS_LIGHT_STANDBY_STATE")
    if configured:
        return Path(configured)
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return state_home / "pocketds-linux-kit/light-standby.json"


def default_auto_poweroff_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "pocketds-linux-kit/light-standby-auto-poweroff-minutes"


def check_live(adapter: LiveAdapter, store: StateStore, settings: AutoPoweroffSettings) -> int:
    try:
        lid_closed = adapter.get_lid_closed()
    except Exception as error:
        emit("check", okay=False, error=f"lid: {str(error)[:200]}")
        return 1
    state: dict[str, Any] | None = None
    state_error: str | None = None
    try:
        state = store.load()
    except StateFileError as error:
        state_error = str(error)
    setting_error: str | None = None
    try:
        auto_poweroff_minutes = settings.load()
    except StateFileError as error:
        auto_poweroff_minutes = None
        setting_error = str(error)
    external_power: bool | None = None
    external_power_error: str | None = None
    try:
        external_power = adapter.get_external_power()
    except Exception as error:
        external_power_error = str(error)[:200]
    environment_ready = adapter._graphical_environment() is not None
    dpms = adapter.dpms_status() if environment_ready else None
    dual_display_ready = (
        dpms is not None
        and dpms.get("DSI-1") in {"on", "off"}
        and dpms.get("DSI-2") in {"on", "off"}
    )
    payload = {
        "okay": (
            state_error is None
            and setting_error is None
            and external_power_error is None
            and environment_ready
            and dual_display_ready
        ),
        "lid_closed": lid_closed,
        "external_power": external_power,
        "external_power_error": external_power_error,
        "profile": adapter.current_profile(),
        "state": state,
        "state_error": state_error,
        "auto_poweroff_minutes": auto_poweroff_minutes,
        "auto_poweroff_setting_error": setting_error,
        "display_owner": "pocketds-light-standby",
        "wayland_environment": environment_ready,
        "dual_display_ready": dual_display_ready,
        "dpms": dpms,
    }
    emit("check", **payload)
    return 0 if payload["okay"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("daemon", "check", "configure-auto-poweroff"))
    parser.add_argument("value", nargs="?")
    arguments = parser.parse_args()
    settings = AutoPoweroffSettings(default_auto_poweroff_path())
    if arguments.command == "configure-auto-poweroff":
        try:
            minutes = int(arguments.value or "", 10)
        except ValueError:
            parser.error("configure-auto-poweroff requires a supported minute value")
        if str(minutes) != arguments.value or minutes not in AUTO_POWEROFF_MINUTES:
            parser.error("unsupported auto poweroff timeout")
        settings.save(minutes)
        emit("auto-poweroff-configured", minutes=minutes)
        return 0
    if arguments.value is not None:
        parser.error("this command takes no value")
    adapter = LiveAdapter()
    store = StateStore(default_state_path())
    if arguments.command == "check":
        return check_live(adapter, store, settings)
    controller = LightStandbyController(adapter, store, settings)
    return LidDaemon(adapter, controller).run()


if __name__ == "__main__":
    sys.exit(main())

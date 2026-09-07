#!/usr/bin/env python3
"""Fail-closed RTC and logind lifecycle guard for attended deep suspend."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any, Callable, Sequence

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402


SCHEMA = "pocketds.deep-suspend-guard.v1"
MODEL = "AYANEO Pocket DS"
LOGIN1_SERVICE = "org.freedesktop.login1"
LOGIN1_PATH = "/org/freedesktop/login1"
LOGIN1_MANAGER = "org.freedesktop.login1.Manager"
DBUS_PROPERTIES = "org.freedesktop.DBus.Properties"
RTC_ALARM = Path("/sys/class/rtc/rtc0/wakealarm")
RTC_EPOCH = Path("/sys/class/rtc/rtc0/since_epoch")
RTC_WAKE = Path("/sys/class/rtc/rtc0/device/power/wakeup")
RTCTOOL = Path("/usr/bin/rtcwake")
SYSTEMD_ANALYZE = Path("/usr/bin/systemd-analyze")
LOCK_PATH = Path("/run/pocketds-deep-suspend.lock")
MIN_RTC_SECONDS = 60
MAX_RTC_SECONDS = 300
ROOT_CHECK_INHIBITORS = 1
MAX_OUTPUT = 65_536


class GuardError(RuntimeError):
    """A fail-closed preflight or lifecycle failure."""


def emit(event: str, **fields: Any) -> None:
    print(
        json.dumps({"schema": SCHEMA, "event": event, **fields}, sort_keys=True),
        flush=True,
    )


def read_small(path: Path, maximum: int = 16_384) -> str:
    try:
        value = path.read_bytes()
    except OSError as error:
        raise GuardError(f"required state unavailable: {path}") from error
    if len(value) > maximum:
        raise GuardError(f"required state oversized: {path}")
    return value.decode("utf-8", errors="strict").strip("\x00\r\n ")


def run_command(
    command: Sequence[str],
    *,
    timeout: float,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> bytes:
    try:
        result = runner(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GuardError(f"command failed: {command[0]}") from error
    if result.returncode != 0 or len(result.stdout) > MAX_OUTPUT or len(result.stderr) > MAX_OUTPUT:
        raise GuardError(f"command rejected: {command[0]}")
    return result.stdout


def parse_sleep_policy(text: str) -> dict[str, str]:
    current = ""
    policy: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            continue
        if current != "Sleep" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in {"AllowSuspend", "SuspendState", "MemorySleepMode"}:
            policy[key] = value.strip()
    return policy


def effective_sleep_policy(
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> dict[str, str]:
    output = run_command(
        [str(SYSTEMD_ANALYZE), "cat-config", "systemd/sleep.conf"],
        timeout=5,
        runner=runner,
    )
    try:
        text = output.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise GuardError("effective sleep policy is not UTF-8") from error
    return parse_sleep_policy(text)


def executable_root_file(path: Path) -> bool:
    try:
        metadata = path.stat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == 0
        and metadata.st_nlink == 1
        and not metadata.st_mode & 0o022
        and os.access(path, os.X_OK)
    )


class Lifecycle:
    """Pure state machine: only true→false authorizes post-request RTC cleanup."""

    def __init__(self) -> None:
        self.request_sent = False
        self.saw_prepare = False
        self.saw_resume = False
        self.method_replied = False
        self.method_succeeded: bool | None = None

    def mark_request_sent(self) -> None:
        if self.request_sent:
            raise GuardError("suspend request was already sent")
        self.request_sent = True

    def mark_method_reply(self, succeeded: bool) -> str:
        self.method_replied = True
        self.method_succeeded = succeeded
        return "wait-for-prepare-false"

    def prepare_for_sleep(self, active: bool) -> str:
        if not self.request_sent:
            return "ignore"
        if active:
            if self.saw_resume:
                return "ignore"
            if not self.saw_prepare:
                self.saw_prepare = True
                return "release-delay-inhibitor"
            return "ignore"
        if not self.saw_prepare or self.saw_resume:
            return "ignore"
        self.saw_resume = True
        return "cleanup-rtc"


class LiveAdapter:
    def __init__(self) -> None:
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def can_suspend(self) -> str:
        reply = self.bus.call_sync(
            LOGIN1_SERVICE,
            LOGIN1_PATH,
            LOGIN1_MANAGER,
            "CanSuspend",
            None,
            GLib.VariantType.new("(s)"),
            Gio.DBusCallFlags.NONE,
            3000,
            None,
        )
        return str(reply.unpack()[0])

    def preparing_for_sleep(self) -> bool:
        reply = self.bus.call_sync(
            LOGIN1_SERVICE,
            LOGIN1_PATH,
            DBUS_PROPERTIES,
            "Get",
            GLib.Variant("(ss)", (LOGIN1_MANAGER, "PreparingForSleep")),
            GLib.VariantType.new("(v)"),
            Gio.DBusCallFlags.NONE,
            3000,
            None,
        )
        value = reply.get_child_value(0).get_variant()
        if not value.is_of_type(GLib.VariantType.new("b")):
            raise GuardError("login1 PreparingForSleep has the wrong type")
        return bool(value.get_boolean())

    def take_delay_inhibitor(self) -> int:
        reply, descriptor_list = self.bus.call_with_unix_fd_list_sync(
            LOGIN1_SERVICE,
            LOGIN1_PATH,
            LOGIN1_MANAGER,
            "Inhibit",
            GLib.Variant(
                "(ssss)",
                (
                    "sleep",
                    "Pocket DS deep suspend guard",
                    "Keep the RTC rescue armed before kernel suspend",
                    "delay",
                ),
            ),
            GLib.VariantType.new("(h)"),
            Gio.DBusCallFlags.NONE,
            3000,
            None,
            None,
        )
        handle = int(reply.unpack()[0])
        descriptors = descriptor_list.steal_fds()
        if handle < 0 or handle >= len(descriptors):
            for descriptor in descriptors:
                os.close(descriptor)
            raise GuardError("login1 returned an invalid inhibitor descriptor")
        kept = descriptors[handle]
        for index, descriptor in enumerate(descriptors):
            if index != handle:
                os.close(descriptor)
        return kept

    def subscribe(self, callback: Callable[..., None]) -> int:
        return self.bus.signal_subscribe(
            LOGIN1_SERVICE,
            LOGIN1_MANAGER,
            "PrepareForSleep",
            LOGIN1_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            callback,
        )

    def unsubscribe(self, subscription: int) -> None:
        self.bus.signal_unsubscribe(subscription)

    def request_suspend(self, callback: Callable[..., None]) -> None:
        self.bus.call(
            LOGIN1_SERVICE,
            LOGIN1_PATH,
            LOGIN1_MANAGER,
            "SuspendWithFlags",
            GLib.Variant("(t)", (ROOT_CHECK_INHIBITORS,)),
            GLib.VariantType.new("()"),
            Gio.DBusCallFlags.NONE,
            15_000,
            None,
            callback,
            None,
        )

    def finish_request(self, result: Gio.AsyncResult) -> None:
        self.bus.call_finish(result)

    @staticmethod
    def alarm() -> int | None:
        value = read_small(RTC_ALARM)
        if not value:
            return None
        try:
            return int(value)
        except ValueError as error:
            raise GuardError("RTC alarm readback is not numeric") from error

    @staticmethod
    def rtc_epoch() -> int:
        value = read_small(RTC_EPOCH)
        try:
            epoch = int(value)
        except ValueError as error:
            raise GuardError("RTC epoch readback is not numeric") from error
        if epoch < 0:
            raise GuardError("RTC epoch readback is negative")
        return epoch

    @staticmethod
    def arm_rtc(seconds: int) -> int:
        started = LiveAdapter.rtc_epoch()
        run_command(
            [
                str(RTCTOOL),
                "--utc",
                "--device",
                "/dev/rtc0",
                "--mode",
                "no",
                "--seconds",
                str(seconds),
            ],
            timeout=5,
        )
        alarm = LiveAdapter.alarm()
        finished = LiveAdapter.rtc_epoch()
        if alarm is None or not started + seconds - 3 <= alarm <= finished + seconds + 5:
            raise GuardError("RTC alarm readback is outside the requested window")
        return alarm

    @staticmethod
    def clear_rtc() -> bool:
        try:
            run_command(
                [str(RTCTOOL), "--utc", "--device", "/dev/rtc0", "--mode", "disable"],
                timeout=5,
            )
            return LiveAdapter.alarm() is None
        except GuardError:
            return False


def collect_preflight(adapter: LiveAdapter) -> dict[str, Any]:
    try:
        policy = effective_sleep_policy()
    except GuardError:
        policy = {}
    try:
        can_suspend = adapter.can_suspend()
    except (GuardError, GLib.Error):
        can_suspend = "unavailable"
    try:
        preparing = adapter.preparing_for_sleep()
    except (GuardError, GLib.Error):
        preparing = True
    try:
        alarm_clear = adapter.alarm() is None
    except GuardError:
        alarm_clear = False

    gates = {
        "running_as_root": os.geteuid() == 0,
        "model_exact": read_small(Path("/proc/device-tree/model")) == MODEL,
        "mem_supported": "mem" in read_small(Path("/sys/power/state")).split(),
        "deep_selected": "[deep]" in read_small(Path("/sys/power/mem_sleep")).split(),
        "callbacks_serial": read_small(Path("/sys/power/pm_async")) == "0",
        "deep_only_policy": (
            policy.get("SuspendState") == "mem"
            and policy.get("MemorySleepMode") == "deep"
        ),
        "rtc_wake_enabled": read_small(RTC_WAKE) == "enabled",
        "rtc_alarm_clear": alarm_clear,
        "rtcwake_trusted": executable_root_file(RTCTOOL),
        "not_already_preparing": not preparing,
    }
    base_ready = all(gates.values())
    allow_suspend = policy.get("AllowSuspend", "")
    execution_ready = base_ready and allow_suspend == "yes" and can_suspend == "yes"
    safely_blocked = base_ready and allow_suspend == "no" and can_suspend == "no"
    return {
        "gates": gates,
        "policy": {
            "allow_suspend": allow_suspend or "unavailable",
            "suspend_state": policy.get("SuspendState", "unavailable"),
            "memory_sleep_mode": policy.get("MemorySleepMode", "unavailable"),
        },
        "can_suspend": can_suspend,
        "execution_ready": execution_ready,
        "safely_blocked": safely_blocked,
    }


def acquire_lock() -> Any:
    descriptor = os.open(
        LOCK_PATH,
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    stream = os.fdopen(descriptor, "r+", encoding="ascii")
    metadata = os.fstat(stream.fileno())
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        stream.close()
        raise GuardError("runtime lock is unsafe")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        stream.close()
        raise GuardError("another deep suspend guard is active") from error
    return stream


class SuspendSession:
    def __init__(self, adapter: LiveAdapter, seconds: int) -> None:
        self.adapter = adapter
        self.seconds = seconds
        self.lifecycle = Lifecycle()
        self.loop = GLib.MainLoop()
        self.subscription = 0
        self.inhibitor_fd = -1
        self.timeout_source = 0
        self.armed = False
        self.result = 3

    def _release_inhibitor(self) -> None:
        if self.inhibitor_fd >= 0:
            os.close(self.inhibitor_fd)
            self.inhibitor_fd = -1

    def _on_prepare(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _path: str,
        _interface: str,
        _signal: str,
        parameters: GLib.Variant,
    ) -> None:
        values = parameters.unpack()
        if len(values) != 1 or not isinstance(values[0], bool):
            return
        active = bool(values[0])
        action = self.lifecycle.prepare_for_sleep(active)
        emit("prepare-for-sleep", active=active, action=action)
        if action == "release-delay-inhibitor":
            self._release_inhibitor()
        elif action == "cleanup-rtc":
            cleared = self.adapter.clear_rtc()
            emit("resume-cleanup", rtc_alarm_clear=cleared)
            self.result = 0 if cleared else 4
            self.loop.quit()

    def _on_request_done(
        self,
        _source: Gio.DBusConnection,
        result: Gio.AsyncResult,
        _user_data: Any,
    ) -> None:
        succeeded = True
        try:
            self.adapter.finish_request(result)
        except GLib.Error:
            succeeded = False
        action = self.lifecycle.mark_method_reply(succeeded)
        emit("request-reply", succeeded=succeeded, action=action)

    def _on_timeout(self) -> bool:
        self.timeout_source = 0
        emit(
            "lifecycle-timeout",
            saw_prepare=self.lifecycle.saw_prepare,
            saw_resume=self.lifecycle.saw_resume,
            rtc_cleanup="deferred-until-prepare-false",
        )
        self.result = 6
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def _clear_before_request(self) -> bool:
        if not self.armed or self.lifecycle.request_sent:
            return True
        cleared = self.adapter.clear_rtc()
        emit("pre-request-cleanup", rtc_alarm_clear=cleared)
        self.armed = not cleared
        return cleared

    def run(self) -> int:
        try:
            self.inhibitor_fd = self.adapter.take_delay_inhibitor()
            self.subscription = self.adapter.subscribe(self._on_prepare)
            if self.adapter.preparing_for_sleep():
                raise GuardError("login1 is already preparing for sleep")
            if self.adapter.alarm() is not None:
                raise GuardError("an RTC alarm already exists")
            os.sync()
            if self.adapter.alarm() is not None:
                raise GuardError("an RTC alarm appeared during pre-request sync")
            self.armed = True
            alarm = self.adapter.arm_rtc(self.seconds)
            emit("rtc-armed", remaining_seconds=max(0, alarm - self.adapter.rtc_epoch()))
            self.adapter.request_suspend(self._on_request_done)
            self.lifecycle.mark_request_sent()
            emit("request-sent", method="SuspendWithFlags", flags=ROOT_CHECK_INHIBITORS)
            self.timeout_source = GLib.timeout_add_seconds(
                self.seconds + 30, self._on_timeout
            )
            self.loop.run()
            return self.result
        except (GuardError, GLib.Error, OSError) as error:
            if not self._clear_before_request():
                emit("failed", reason="pre-request-rtc-cleanup-failed")
                return 7
            emit("failed", reason=str(error)[:200])
            return 2
        finally:
            if self.timeout_source:
                GLib.source_remove(self.timeout_source)
                self.timeout_source = 0
            self._release_inhibitor()
            if self.subscription:
                self.adapter.unsubscribe(self.subscription)
                self.subscription = 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="read-only policy and lifecycle preflight")
    run = subparsers.add_parser("run", help="perform one guarded attended suspend")
    run.add_argument("seconds", type=int)
    arguments = parser.parse_args(argv)
    if arguments.command == "run" and not MIN_RTC_SECONDS <= arguments.seconds <= MAX_RTC_SECONDS:
        parser.error(
            f"seconds must be between {MIN_RTC_SECONDS} and {MAX_RTC_SECONDS}"
        )
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        adapter = LiveAdapter()
        snapshot = collect_preflight(adapter)
    except (GuardError, GLib.Error, UnicodeError) as error:
        emit("preflight-failed", reason=str(error)[:200])
        return 2

    if arguments.command == "check":
        emit("check", **snapshot)
        return 0 if snapshot["safely_blocked"] or snapshot["execution_ready"] else 1

    if not snapshot["execution_ready"]:
        emit("refused", reason="execution-preflight-not-ready", **snapshot)
        return 1

    try:
        lock = acquire_lock()
    except (GuardError, OSError) as error:
        emit("refused", reason=str(error)[:200])
        return 2
    try:
        second = collect_preflight(adapter)
        if not second["execution_ready"]:
            emit("refused", reason="locked-preflight-not-ready", **second)
            return 1
        return SuspendSession(adapter, arguments.seconds).run()
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())

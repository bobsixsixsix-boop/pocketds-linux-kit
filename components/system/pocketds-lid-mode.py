#!/usr/bin/env python3
"""Configure only PowerDevil's lid action; never enable or request system sleep."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time

PROFILES = ("AC", "Battery", "LowBattery")
GROUP = "SuspendAndShutdown"
MAX_CONFIG = 256 * 1024
LOGIN = ("org.freedesktop.login1", "/org/freedesktop/login1",
         "org.freedesktop.login1.Manager")


class ModeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def read_config(path: Path) -> tuple[bytes, int]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o022):
            raise ModeError("config_unavailable", "PowerDevil configuration is not a safe user file")
        data = stream.read(MAX_CONFIG + 1)
        if len(data) > MAX_CONFIG:
            raise ModeError("config_unavailable", "PowerDevil configuration is too large")
        data.decode("utf-8")
        return data, stat.S_IMODE(info.st_mode)


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ModeError("config_unavailable", "Lid settings directory is not safe")


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    fd, name = tempfile.mkstemp(prefix=".lid-mode-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.lexists(name):
            os.unlink(name)


class LiveAdapter:
    def __init__(self, budget: float = 5) -> None:
        self.deadline = time.monotonic() + budget

    def run(self, args: list[str]) -> subprocess.CompletedProcess:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ModeError("query_failed", "Lid settings operation timed out")
        try:
            result = subprocess.run(args, capture_output=True, text=True,
                                    timeout=min(2, remaining), check=False,
                                    env={**os.environ, "LC_ALL": "C.UTF-8"})
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ModeError("query_failed", "Lid settings service did not respond") from error
        if len(result.stdout) + len(result.stderr) > 16384:
            raise ModeError("query_failed", "Unexpected lid settings response")
        return result

    def key(self, path: Path, profile: str, key: str) -> str:
        result = self.run(["/usr/bin/kreadconfig6", "--file", str(path), "--group", profile,
                           "--group", GROUP, "--key", key])
        if result.returncode:
            raise ModeError("config_unavailable", "PowerDevil settings could not be read")
        return result.stdout.strip()

    def write_key(self, path: Path, profile: str, value: str) -> None:
        result = self.run(["/usr/bin/kwriteconfig6", "--file", str(path), "--group", profile,
                           "--group", GROUP, "--key", "LidAction", "--type", "int", value])
        if result.returncode:
            raise ModeError("config_unavailable", "PowerDevil lid action could not be saved")

    def bus_value(self, operation: str, member: str, kind: str):
        result = self.run(["/usr/bin/busctl", "--timeout=2s", "--json=short",
                           operation, *LOGIN, member])
        try:
            payload = json.loads(result.stdout)
            data = payload["data"]
            if result.returncode or payload["type"] != kind:
                raise ValueError("invalid reply")
            # busctl serializes a property as its scalar value; method returns
            # are a tuple of out parameters, even for a single string result.
            if operation == "get-property":
                value = data
            else:
                if not isinstance(data, list) or len(data) != 1:
                    raise ValueError("invalid method return tuple")
                value = data[0]
            if (kind == "b" and type(value) is not bool) or (kind == "s" and not isinstance(value, str)):
                raise ValueError("invalid value")
            return value
        except (ValueError, KeyError, TypeError) as error:
            raise ModeError("query_failed", "System power status could not be read") from error

    def lid_open(self) -> bool:
        return not self.bus_value("get-property", "LidClosed", "b")

    def sleep_available(self, path: Path) -> None:
        try:
            result = self.run(["/usr/local/libexec/pocketds-daily-suspend", "check"])
        except ModeError as error:
            raise ModeError("guard_unavailable", "Daily sleep verification is unavailable") from error
        if result.returncode:
            try:
                reason = json.loads(result.stderr).get("reason", "")
            except (ValueError, AttributeError):
                reason = ""
            if "kernel" in reason or "wrong device" in reason:
                code = "kernel_unverified"
            elif "opted in" in reason or "daily-suspend.enabled" in reason:
                code = "not_enabled"
            elif "failed cycle" in reason:
                code = "previous_cycle_failed"
            else:
                code = "guard_unavailable"
            raise ModeError(code, "Daily sleep verification did not pass")
        try:
            if json.loads(result.stdout).get("eligible") is not True:
                raise ValueError("not eligible")
        except (ValueError, AttributeError) as error:
            raise ModeError("guard_unavailable", "Daily sleep verification returned an invalid reply") from error
        # SleepMode is shared with idle/power-button sleep. Refuse unsupported
        # existing modes instead of changing those unrelated behaviours.
        if any(self.key(path, profile, "SleepMode") != "1" for profile in PROFILES):
            raise ModeError("unsupported_sleep_mode", "Existing sleep mode is not suspend to RAM")
        if self.bus_value("call", "CanSuspend", "s") != "yes":
            raise ModeError("suspend_denied", "The current session is not permitted to suspend")

    def refresh(self) -> None:
        result = self.run(["/usr/bin/busctl", "--user", "--timeout=2s", "call",
                           "org.kde.Solid.PowerManagement", "/org/kde/Solid/PowerManagement",
                           "org.kde.Solid.PowerManagement", "refreshStatus"])
        if result.returncode:
            raise ModeError("refresh_failed", "PowerDevil could not reload its settings")

    def rollback_adapter(self):
        return LiveAdapter(3)


def mode_for(path: Path, adapter: LiveAdapter) -> str:
    values = [adapter.key(path, profile, "LidAction") for profile in PROFILES]
    return "connected" if values == ["0"] * 3 else "sleep" if values == ["1"] * 3 else "unknown"


def status(path: Path, adapter: LiveAdapter, include_lid: bool = True) -> dict:
    result = {"mode": "unknown", "sleep_available": False, "reason_code": "ready",
              "reason": "", "lid_open": None}
    try:
        read_config(path)
        result["mode"] = mode_for(path, adapter)
        # The display daemon only needs the selected action. It never requests
        # sleep and must not change mode after a failed native sleep check.
        if not include_lid:
            return result
        if include_lid:
            result["lid_open"] = adapter.lid_open()
        adapter.sleep_available(path)
        result["sleep_available"] = True
    except (OSError, UnicodeError) as error:
        result.update(reason_code="config_unavailable", reason="PowerDevil settings are unavailable")
    except ModeError as error:
        result.update(reason_code=error.code, reason=str(error))
    return result


def set_mode(path: Path, mode: str, adapter: LiveAdapter, state_path: Path) -> dict:
    if mode not in {"connected", "sleep"}:
        raise ModeError("invalid_mode", "Unsupported lid mode")
    private_directory(state_path)
    descriptor = os.open(state_path / "lid-mode.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ModeError("config_unavailable", "Lid settings lock is not safe")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ModeError("busy", "Another lid settings change is in progress") from error
        original, permissions = read_config(path)
        if not adapter.lid_open():
            raise ModeError("lid_closed", "Open the lid before changing its action")
        if mode == "sleep":
            adapter.sleep_available(path)
        current = mode_for(path, adapter)
        if current == mode:
            if not adapter.lid_open():
                raise ModeError("lid_closed", "Lid closed before PowerDevil could reload")
            adapter.refresh()
            return {"mode": mode, "changed": False}
        fd, name = tempfile.mkstemp(prefix=".lid-mode-edit-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(original)
            for profile in PROFILES:
                adapter.write_key(temporary, profile, "0" if mode == "connected" else "1")
            if mode_for(temporary, adapter) != mode:
                raise ModeError("config_unavailable", "PowerDevil lid settings did not accept the change")
            proposed, _ = read_config(temporary)
            if read_config(path)[0] != original:
                raise ModeError("config_changed", "PowerDevil settings changed during this operation")
            if mode == "sleep":
                adapter.sleep_available(temporary)
            # refreshStatus(force) re-triggers an already closed lid in KDE.
            if not adapter.lid_open():
                raise ModeError("lid_closed", "Lid closed during the settings change")
            atomic_write(state_path / "powerdevilrc.before-lid-mode", original)
            atomic_write(path, proposed, permissions)
            try:
                if not adapter.lid_open():
                    raise ModeError("lid_closed", "Lid closed before PowerDevil could reload")
                adapter.refresh()
                if read_config(path)[0] != proposed:
                    raise ModeError("config_changed", "PowerDevil settings changed during reload")
            except Exception as failure:
                # Never overwrite a concurrent KDE settings edit on rollback.
                if read_config(path)[0] != proposed:
                    raise ModeError("rollback_conflict", "Settings changed externally; the backup was preserved") from failure
                atomic_write(path, original, permissions)
                try:
                    recovery = adapter.rollback_adapter()
                    if not recovery.lid_open():
                        raise ModeError("lid_closed", "Open the lid before reloading restored settings")
                    recovery.refresh()
                except Exception as rollback_error:
                    raise ModeError("rollback_reload_failed", "Previous settings restored; PowerDevil reload needs retry") from rollback_error
                raise failure
            return {"mode": mode, "changed": True}
        finally:
            temporary.unlink(missing_ok=True)


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "owner", "set"))
    parser.add_argument("mode", nargs="?", choices=("connected", "sleep"))
    args = parser.parse_args()
    if (args.command == "set") != (args.mode is not None):
        parser.error("set requires one mode; status takes no mode")
    home = config_home()
    path = home / "powerdevilrc"
    if args.command in {"status", "owner"}:
        full = args.command == "status"
        print(json.dumps(status(path, LiveAdapter(5 if full else 1.25), include_lid=full), sort_keys=True))
        return 0
    try:
        result = set_mode(path, args.mode, LiveAdapter(8), home / "pocketds-linux-kit")
        # Keep writes bounded: the Panel's next independent status poll obtains
        # fresh capability state, rather than re-running checks after commit.
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ModeError, OSError, UnicodeError) as error:
        print(json.dumps({"error": getattr(error, "code", "config_unavailable"),
                          "reason": str(error)}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

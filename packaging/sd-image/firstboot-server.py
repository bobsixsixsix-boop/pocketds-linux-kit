#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""One-use, local-only password setup for the image's fixed pocketds user."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import struct
import subprocess
import unicodedata


ACCOUNT = "pocketds"
STATE_DIR = Path("/var/lib/pocketds-firstboot")
SOCKET_PATH = Path("/run/pocketds-firstboot/password.sock")
MAX_REQUEST = 2048


class SetupError(Exception):
    """Deliberately carries no request content or backend output."""


def check_root_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise SetupError()


def checked_file(path: Path, *, create: bool = False) -> int:
    flags = os.O_CLOEXEC | os.O_NOFOLLOW
    flags |= os.O_RDWR | os.O_CREAT if create else os.O_RDONLY
    fd = os.open(path, flags, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_nlink != 1 or info.st_mode & 0o022):
        os.close(fd)
        raise SetupError()
    return fd


def password_is_locked(shadow_path: Path = Path("/etc/shadow")) -> bool:
    fd = checked_file(shadow_path)
    with os.fdopen(fd, "r", encoding="utf-8") as source:
        if os.fstat(source.fileno()).st_size > 4 * 1024 * 1024:
            raise SetupError()
        rows = [line.rstrip("\n").split(":") for line in source
                if line.startswith(ACCOUNT + ":")]
    if len(rows) != 1 or len(rows[0]) != 9 or not rows[0][1]:
        raise SetupError()
    return rows[0][1].startswith(("!", "*"))


def valid_password(value: object) -> bool:
    return (isinstance(value, str) and 8 <= len(value) <= 128
            and not any(unicodedata.category(char) in {"Cc", "Cf", "Cs"}
                        for char in value))


def parse_request(data: bytes) -> str:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SetupError()
            result[key] = value
        return result

    if len(data) > MAX_REQUEST or not data.endswith(b"\n") or b"\n" in data[:-1]:
        raise SetupError()
    try:
        request = json.loads(data, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise SetupError() from None
    if (not isinstance(request, dict) or set(request) != {"password"}
            or not valid_password(request["password"])):
        raise SetupError()
    return request["password"]


def change_password(password: str) -> None:
    try:
        subprocess.run(
            ["/usr/sbin/chpasswd"], input=(ACCOUNT + ":" + password + "\n").encode("utf-8"),
            # Keep the request serialized until PAM and its helpers have exited.
            # Killing only chpasswd on a timer could leave a helper still writing.
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
            env={"PATH": "/usr/sbin:/usr/bin", "LANG": "C.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError):
        raise SetupError() from None


class PasswordSetup:
    def __init__(self, state_dir: Path, expected_uid: int, *,
                 locked=password_is_locked, setter=change_password):
        self.state_dir = state_dir
        self.expected_uid = expected_uid
        self.locked = locked
        self.setter = setter

    def pending(self) -> bool:
        check_root_directory(self.state_dir)
        try:
            fd = checked_file(self.state_dir / "pending")
        except FileNotFoundError:
            return False
        try:
            if os.fstat(fd).st_size > 64:
                raise SetupError()
        finally:
            os.close(fd)
        return True

    def complete(self) -> None:
        # A crash after chpasswd succeeds is recovered without resetting it.
        (self.state_dir / "pending").unlink(missing_ok=True)
        fd = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def reconcile(self) -> bool:
        """Return True only while a valid pending marker and locked user coexist."""
        if not self.pending():
            return False
        if not self.locked():
            self.complete()
            return False
        return True

    def handle(self, peer_uid: int, request: bytes) -> bytes:
        if peer_uid != self.expected_uid:
            return b'{"status":"error"}\n'
        try:
            if not self.reconcile():
                return b'{"status":"complete"}\n'
            password = parse_request(request)
            try:
                self.setter(password)
            except SetupError:
                # Backend failure may occur after the password was set.
                # Reading shadow decides recovery; never attempt a second reset.
                if self.locked():
                    return b'{"status":"error"}\n'
            if self.locked():
                raise SetupError()
            self.complete()
            return b'{"status":"complete"}\n'
        except (SetupError, OSError):
            return b'{"status":"error"}\n'


def read_request(connection: socket.socket) -> bytes:
    data = b""
    while len(data) <= MAX_REQUEST:
        part = connection.recv(min(512, MAX_REQUEST + 1 - len(data)))
        if not part:
            raise SetupError()
        data += part
        if b"\n" in part:
            return data
    raise SetupError()


def main() -> int:
    if os.geteuid() != 0:
        return 1
    try:
        account = pwd.getpwnam(ACCOUNT)
        if account.pw_uid == 0 or account.pw_dir != "/home/pocketds":
            raise SetupError()
        check_root_directory(STATE_DIR)
        lock_fd = checked_file(STATE_DIR / "lock", create=True)
        with os.fdopen(lock_fd, "r+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            setup = PasswordSetup(STATE_DIR, account.pw_uid)
            if not setup.reconcile():
                return 0
            check_root_directory(SOCKET_PATH.parent)
            try:
                info = SOCKET_PATH.lstat()
            except FileNotFoundError:
                pass
            else:
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0:
                    raise SetupError()
                SOCKET_PATH.unlink()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                old_mask = os.umask(0o077)
                try:
                    server.bind(str(SOCKET_PATH))
                finally:
                    os.umask(old_mask)
                os.chown(SOCKET_PATH, 0, account.pw_gid)
                os.chmod(SOCKET_PATH, 0o660)
                server.listen(4)
                while setup.reconcile():
                    connection, _ = server.accept()
                    with connection:
                        connection.settimeout(5)
                        peer_uid = struct.unpack("3i", connection.getsockopt(
                            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))[1]
                        try:
                            request = read_request(connection) if peer_uid == account.pw_uid else b""
                            reply = setup.handle(peer_uid, request)
                        except (SetupError, OSError):
                            reply = b'{"status":"error"}\n'
                        try:
                            connection.sendall(reply)
                        except OSError:
                            pass
                SOCKET_PATH.unlink(missing_ok=True)
        return 0
    except (SetupError, OSError, KeyError):
        # Never print exception details: credentials and backend output stay private.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

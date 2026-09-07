#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Graphical password onboarding; account and service paths are fixed."""

import json
import os
from pathlib import Path
import pwd
import socket
import stat
import struct
import subprocess
import time
import unicodedata


PENDING = Path("/var/lib/pocketds-firstboot/pending")
SOCKET = Path("/run/pocketds-firstboot/password.sock")
TITLE = "欢迎使用 Pocket DS"


def dialog(kind: str, message: str) -> str | None:
    try:
        result = subprocess.run(
            ["/usr/bin/kdialog", "--title", TITLE, kind, message],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    try:
        return result.stdout.decode("utf-8").removesuffix("\n")
    except UnicodeError:
        return None


def submit(password: str) -> bool:
    parent = SOCKET.parent.lstat()
    info = SOCKET.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022
            or not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o007):
        return False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(20)
        connection.connect(str(SOCKET))
        peer_uid = struct.unpack("3i", connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))[1]
        if peer_uid != 0:
            return False
        connection.sendall(json.dumps({"password": password}, ensure_ascii=False).encode("utf-8") + b"\n")
        response = b""
        while len(response) < 128 and not response.endswith(b"\n"):
            part = connection.recv(128 - len(response))
            if not part:
                break
            response += part
        return response == b'{"status":"complete"}\n'


def desktop_ready() -> bool:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["/usr/bin/systemctl", "--user", "is-active", "--quiet",
                 "pocketds-image-desktop.service"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=False,
            )
            if result.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(1)
    return False


def main() -> int:
    try:
        if os.geteuid() != pwd.getpwnam("pocketds").pw_uid:
            return 1
        if PENDING.exists() and not desktop_ready():
            return 1
        while PENDING.exists():
            password = dialog("--password", "请为本机账户设置密码（8–128 个字符）。\n用于解锁和管理系统。可使用下屏键盘输入。")
            if password is None:
                return 0
            if (not 8 <= len(password) <= 128
                    or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in password)):
                dialog("--error", "请使用 8–128 个字符，且不要包含控制字符。")
                continue
            confirmation = dialog("--password", "请再次输入密码。")
            if confirmation is None:
                return 0
            if password != confirmation:
                dialog("--error", "两次输入不一致，请重新输入。")
                continue
            try:
                success = submit(password)
            except OSError:
                success = False
            password = confirmation = None
            if success or not PENDING.exists():
                dialog("--msgbox", "密码已设置，可以开始使用。\n语音输入需按随附说明配置你自己的 API。")
                return 0
            dialog("--error", "暂时无法设置密码，请重试。\n也可以取消，在下次登录时继续。")
        return 0
    except (OSError, KeyError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

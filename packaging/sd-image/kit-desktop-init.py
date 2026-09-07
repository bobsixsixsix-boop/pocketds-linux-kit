#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create the public image's lower-screen desktop once, in its user session."""
import os
from pathlib import Path
import pwd
import subprocess
import sys
import time

import dbus


def main():
    user = pwd.getpwuid(os.getuid())
    if user.pw_name != "pocketds" or user.pw_dir != "/home/pocketds":
        raise RuntimeError("Image desktop setup requires the pocketds account")
    state = Path(user.pw_dir) / ".local/state/pocketds-linux-kit"
    marker = state / "image-desktop-v1.ready"
    already_configured = marker.is_file() and not marker.is_symlink()
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    script = Path("/usr/share/pocketds-linux-kit/image/desktop-layout.js").read_text()
    # Match the tested Kit geometry on the two built-in outputs only. This is
    # a one-time default; subsequent logins retain the owner's display settings.
    configured = False
    for _attempt in range(30):
        try:
            subprocess.run(["/usr/bin/systemctl", "--user", "is-active", "--quiet", "pocketds-keyboard.service"], check=True, timeout=5)
            if already_configured:
                return 0
            if not configured:
                subprocess.run([
                    "/usr/bin/kscreen-doctor", "output.DSI-1.enable",
                    "output.DSI-1.scale.1.5", "output.DSI-1.position.0,0",
                    "output.DSI-1.priority.1", "output.DSI-2.enable",
                    "output.DSI-2.scale.1.25", "output.DSI-2.position.283,720",
                    "output.DSI-2.priority.2",
                ], check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                configured = True
            shell = dbus.SessionBus().get_object("org.kde.plasmashell", "/PlasmaShell")
            result = shell.evaluateScript(script, dbus_interface="org.kde.PlasmaShell", timeout=10)
            if "POCKETDS-DESKTOP-READY" not in str(result):
                raise RuntimeError("Panel layout is not ready")
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write("pocketds.image-desktop.v1\n")
            return 0
        except (dbus.DBusException, subprocess.SubprocessError, RuntimeError):
            time.sleep(1)
    raise RuntimeError("The lower-screen desktop could not be initialized; log in again to retry")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

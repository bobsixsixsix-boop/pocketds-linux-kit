#!/usr/bin/env python3
"""Install/restore the Pocket DS lid quirk without changing the live desktop.

The standalone file leaves local-overrides.quirks untouched. A private copy of
the complete quirk database, including those final overrides, is tested before
publication. Every check creates a new read-only libinput context. No service
restart, input grab, udev trigger, sleep request, or output change is performed.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


REPO = Path(__file__).resolve().parents[1]
NAME = "99-pocketds-lid.quirks"
DATA_DIR = Path("/usr/share/libinput")
OVERRIDE = Path("/etc/libinput/local-overrides.quirks")
BACKUPS = Path("/var/lib/pocketds-linux-kit/libinput-lid")
CHECKER = REPO / "scripts/check-libinput-lid.py"
SOURCE = REPO / "components/system" / NAME


def snapshot(path: Path) -> dict | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 1024 * 1024:
        raise RuntimeError(f"not a bounded regular file: {path}")
    return {"data": path.read_bytes(), "mode": stat.S_IMODE(metadata.st_mode),
            "uid": metadata.st_uid, "gid": metadata.st_gid}


def database() -> dict:
    if DATA_DIR.is_symlink() or not DATA_DIR.is_dir():
        raise RuntimeError("system libinput quirk directory is unavailable or a symlink")
    files = {path.name: snapshot(path) for path in DATA_DIR.glob("*.quirks")}
    if not files:
        raise RuntimeError("system libinput quirk database is empty")
    return {"files": files, "override": snapshot(OVERRIDE)}


def atomic_write(path: Path, content: bytes, mode: int = 0o644,
                 uid: int | None = None, gid: int | None = None) -> None:
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            if uid is not None and gid is not None:
                os.fchown(stream.fileno(), uid, gid)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore_snapshot(path: Path, previous: dict | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write(path, previous["data"], previous["mode"], previous["uid"], previous["gid"])
    if snapshot(path) != previous:
        raise RuntimeError("rollback did not restore original bytes and metadata")


def stage(directory: Path, state: dict, content: bytes | None) -> None:
    # All accepted names sort before this final override in versionsort too.
    # Reject unfamiliar names rather than silently changing override precedence.
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", name) for name in state["files"]):
        raise RuntimeError("unsupported quirk filename for ordered staging")
    override_name = "z" * (max(map(len, state["files"])) + 1) + ".quirks"
    for name, previous in state["files"].items():
        if name != NAME:
            (directory / name).write_bytes(previous["data"])
    if content is not None:
        (directory / NAME).write_bytes(content)
    if state["override"] is not None:
        # libinput applies local overrides last. Keep the entire file and its
        # precedence so a user's conflicting override cannot silently be ignored.
        (directory / override_name).write_bytes(state["override"]["data"])


def check(quirks_dir: Path | None = None, expected: str = "filtered") -> dict:
    command = [sys.executable, str(CHECKER), "--expect", expected]
    if quirks_dir is not None:
        command.extend(["--quirks-dir", str(quirks_dir)])
    reply = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    if reply.returncode:
        raise RuntimeError("libinput validation failed: " + reply.stdout.strip() + " " + reply.stderr.strip())
    result = json.loads(reply.stdout)
    if result.get("ok") is not True:
        raise RuntimeError("libinput check returned an invalid result")
    return result


def metadata(previous: dict | None) -> dict | None:
    if previous is None:
        return None
    return {key: value for key, value in previous.items() if key != "data"} | {
        "sha256": hashlib.sha256(previous["data"]).hexdigest()}


def install() -> dict:
    state = database()
    current = check(expected="any")
    source = snapshot(SOURCE)
    if source is None:
        raise RuntimeError("Pocket DS lid quirk source is missing")
    candidate = source["data"]
    with tempfile.TemporaryDirectory(prefix="pocketds-libinput-preflight-") as temporary:
        stage(Path(temporary), state, candidate)
        check(Path(temporary))
    if database() != state:
        raise RuntimeError("libinput configuration changed during preflight; retry")
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    if BACKUPS.is_symlink() or stat.S_IMODE(BACKUPS.stat().st_mode) & 0o077:
        raise RuntimeError("libinput backup directory is not private")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
    backup = Path(tempfile.mkdtemp(prefix=stamp, dir=BACKUPS))
    previous = state["files"].get(NAME)
    if previous is not None:
        atomic_write(backup / "before.quirks", previous["data"], 0o600)
    manifest = {"schema": 1, "target": NAME, "before": metadata(previous),
                "previous_lid": current["lid"],
                "installed_sha256": hashlib.sha256(candidate).hexdigest()}
    atomic_write(backup / "manifest.json", json.dumps(manifest, sort_keys=True).encode() + b"\n", 0o600)
    target = DATA_DIR / NAME
    try:
        atomic_write(target, candidate)
        result = check()
    except BaseException:
        restore_snapshot(target, previous)
        raise
    return {"ok": True, "backup": str(backup), "new_context": result,
            "live_kwin_changed": False, "requires_new_kwin_context": True}


def restore(backup: Path) -> dict:
    resolved = backup.resolve(strict=True)
    if backup.is_symlink() or resolved.parent != BACKUPS.resolve(strict=True):
        raise RuntimeError("restore requires a direct backup directory from this installer")
    if stat.S_IMODE(resolved.stat().st_mode) & 0o077:
        raise RuntimeError("backup directory permissions are not private")
    raw = snapshot(resolved / "manifest.json")
    if raw is None or raw["uid"] != os.geteuid():
        raise RuntimeError("backup manifest owner is invalid")
    manifest = json.loads(raw["data"])
    if manifest.get("schema") != 1 or manifest.get("target") != NAME:
        raise RuntimeError("backup manifest is invalid")
    before = manifest["before"]
    if before is not None:
        saved = snapshot(resolved / "before.quirks")
        if saved is None or hashlib.sha256(saved["data"]).hexdigest() != before["sha256"]:
            raise RuntimeError("backup preimage checksum differs")
        before = {key: before[key] for key in ("mode", "uid", "gid")} | {"data": saved["data"]}
    state = database()
    current = state["files"].get(NAME)
    if current is None or hashlib.sha256(current["data"]).hexdigest() != manifest["installed_sha256"]:
        raise RuntimeError("installed quirk changed since backup; refusing to overwrite")
    expected = "native" if manifest["previous_lid"] else "filtered"
    with tempfile.TemporaryDirectory(prefix="pocketds-libinput-restore-") as temporary:
        stage(Path(temporary), state, before["data"] if before else None)
        check(Path(temporary), expected)
    if database() != state:
        raise RuntimeError("libinput configuration changed during restore preflight")
    try:
        restore_snapshot(DATA_DIR / NAME, before)
        check(expected=expected)
    except BaseException:
        restore_snapshot(DATA_DIR / NAME, current)
        raise
    return {"ok": True, "restored": str(resolved), "requires_new_kwin_context": True}


def interrupted(signum, _frame):
    raise InterruptedError(f"interrupted by signal {signum}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--install", action="store_true")
    mode.add_argument("--restore", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("run this bounded installer with sudo")
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)
    lock = os.open("/run/lock/pocketds-libinput-lid.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = install() if args.install else restore(args.restore)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    finally:
        os.close(lock)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

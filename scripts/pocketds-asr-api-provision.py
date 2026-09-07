#!/usr/bin/env python3
"""Configure a user's own transcription API; never accept secrets in argv/env."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import secrets
import stat
import sys
import termios
from typing import Sequence

# Works both from the source tree and beside the installed runtime module.
SOURCE_MODULES = Path(__file__).resolve().parent.parent / "components/keyboard"
if SOURCE_MODULES.is_dir():
    sys.path.insert(0, str(SOURCE_MODULES))
import voice_artifacts as voice

PRIVATE_DIR = voice.ASR_API_PRIVATE_DIR
CONFIG_NAME = "config.json"
LOCK_NAME = ".config.lock"


class ProvisionError(RuntimeError):
    """Sanitized local configuration error."""


def _disable_process_dumpability() -> None:
    try:
        voice.disable_process_dumpability()
    except voice.VoiceArtifactError:
        raise ProvisionError("process core-dump suppression is unavailable") from None


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise ProvisionError("private file write did not complete")
        view = view[written:]


def _read_line_from_fd(descriptor: int, maximum: int) -> bytes:
    """Read one bounded line and disable terminal echo whenever applicable."""

    saved = None
    if os.isatty(descriptor):
        saved = termios.tcgetattr(descriptor)
        private = list(saved)
        private[3] &= ~termios.ECHO
        termios.tcsetattr(descriptor, termios.TCSAFLUSH, private)
    try:
        content = bytearray()
        while len(content) <= maximum:
            block = os.read(descriptor, 1)
            if not block or block == b"\n":
                break
            content.extend(block)
        if len(content) > maximum:
            raise ProvisionError("configuration input exceeds the accepted bound")
        return bytes(content)
    finally:
        if saved is not None:
            termios.tcsetattr(descriptor, termios.TCSAFLUSH, saved)
            os.write(2, b"\n")


def _open_private_directory(path: Path = PRIVATE_DIR) -> int:
    owner = os.getuid()
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = os.lstat(path)
    except OSError:
        raise ProvisionError("private credential directory is unavailable") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != owner
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ProvisionError("private credential directory is unsafe")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ProvisionError("private credential directory cannot be opened") from None
    opened = os.fstat(descriptor)
    if opened.st_dev != metadata.st_dev or opened.st_ino != metadata.st_ino:
        os.close(descriptor)
        raise ProvisionError("private credential directory changed")
    return descriptor


def _acquire_private_lock(directory_fd: int) -> int:
    """Serialize recovery, provisioning, checks, and rollback on one inode."""

    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(LOCK_NAME, flags, 0o600, dir_fd=directory_fd)
        metadata = os.fstat(descriptor)
        canonical = os.stat(LOCK_NAME, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        raise ProvisionError("private credential lock is unavailable") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (metadata.st_dev, metadata.st_ino)
        != (canonical.st_dev, canonical.st_ino)
    ):
        os.close(descriptor)
        raise ProvisionError("private credential lock identity is unsafe")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        after = os.stat(LOCK_NAME, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        os.close(descriptor)
        raise ProvisionError("private credential lock cannot be acquired") from None
    if (metadata.st_dev, metadata.st_ino) != (after.st_dev, after.st_ino):
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        raise ProvisionError("private credential lock changed identity")
    return descriptor


def _release_private_lock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _read_private_file(
    directory_fd: int,
    name: str,
    *,
    maximum: int,
    allow_missing: bool = False,
    accepted_links: tuple[int, ...] = (1,),
    minimum: int = 1,
) -> tuple[bytes, os.stat_result] | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise ProvisionError("required private credential file is missing") from None
    except OSError:
        raise ProvisionError("private credential file is unavailable or linked") from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink not in accepted_links
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not minimum <= metadata.st_size <= maximum
        ):
            raise ProvisionError("private credential file metadata is unsafe")
        content = bytearray()
        remaining = metadata.st_size
        while remaining:
            block = os.read(descriptor, min(4_096, remaining))
            if not block:
                raise ProvisionError("private credential file changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise ProvisionError("private credential file grew while reading")
        after = os.fstat(descriptor)
        if (
            after.st_dev != metadata.st_dev
            or after.st_ino != metadata.st_ino
            or after.st_size != metadata.st_size
            or after.st_mtime_ns != metadata.st_mtime_ns
        ):
            raise ProvisionError("private credential file changed while reading")
        return bytes(content), after
    finally:
        os.close(descriptor)


def _create_exact_private_file(
    directory_fd: int, name: str, content: bytes
) -> os.stat_result:
    """Create one pre-journaled private name without an untracked temp file."""

    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    created = False
    identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
        created = True
        initial = os.fstat(descriptor)
        identity = (initial.st_dev, initial.st_ino)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size != len(content)
            or identity != (metadata.st_dev, metadata.st_ino)
        ):
            raise ProvisionError("fresh private stage metadata is unsafe")
        os.close(descriptor)
        descriptor = -1
        os.fsync(directory_fd)
        return metadata
    except FileExistsError:
        raise ProvisionError("private credential stage already exists") from None
    except OSError:
        raise ProvisionError("private credential stage creation failed") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if created and identity is not None and sys.exc_info()[0] is not None:
            try:
                canonical = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if identity == (canonical.st_dev, canonical.st_ino):
                    os.unlink(name, dir_fd=directory_fd)
                    os.fsync(directory_fd)
            except OSError:
                pass


def _read_config(directory_fd: int, *, allow_missing: bool = False):
    record = _read_private_file(directory_fd, CONFIG_NAME,
        maximum=voice.ASR_API_MAX_CONFIG_BYTES, allow_missing=allow_missing)
    if record is not None:
        voice.parse_asr_api_config(record[0])
    return record


def provision(config: voice.AsrApiConfig, *, private_dir: Path = PRIVATE_DIR) -> None:
    """Replace one complete config atomically under the runtime's shared lock."""
    _disable_process_dumpability()
    content = voice.encode_asr_api_config(config)
    directory_fd = _open_private_directory(private_dir)
    lock_fd = -1
    stage = None
    try:
        lock_fd = _acquire_private_lock(directory_fd)
        # Unsafe pre-existing credentials must not be silently overwritten.
        before = _read_config(directory_fd, allow_missing=True)
        stage = ".config-stage-" + secrets.token_hex(16)
        _create_exact_private_file(directory_fd, stage, content)
        if before is not None:
            current = os.stat(CONFIG_NAME, dir_fd=directory_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (before[1].st_dev, before[1].st_ino):
                raise ProvisionError("configuration changed during update")
        else:
            try:
                os.stat(CONFIG_NAME, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ProvisionError("configuration appeared during update")
        os.replace(stage, CONFIG_NAME, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        stage = None
        os.fsync(directory_fd)
    finally:
        try:
            if stage is not None:
                try:
                    os.unlink(stage, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
            if lock_fd >= 0:
                _release_private_lock(lock_fd)
        finally:
            os.close(directory_fd)


def check(*, private_dir: Path = PRIVATE_DIR) -> bool:
    """Validate locally without sending a request or printing configuration."""
    _disable_process_dumpability()
    if not private_dir.exists() and not private_dir.is_symlink():
        return False
    directory_fd = _open_private_directory(private_dir)
    lock_fd = -1
    try:
        lock_fd = _acquire_private_lock(directory_fd)
        return _read_config(directory_fd, allow_missing=True) is not None
    finally:
        if lock_fd >= 0:
            _release_private_lock(lock_fd)
        os.close(directory_fd)


def remove(*, private_dir: Path = PRIVATE_DIR) -> None:
    """Disable only this API config, waiting for any active request to finish."""
    _disable_process_dumpability()
    if not private_dir.exists() and not private_dir.is_symlink():
        return
    directory_fd = _open_private_directory(private_dir)
    lock_fd = -1
    try:
        lock_fd = _acquire_private_lock(directory_fd)
        record = _read_config(directory_fd, allow_missing=True)
        if record is not None:
            current = os.stat(CONFIG_NAME, dir_fd=directory_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (record[1].st_dev, record[1].st_ino):
                raise ProvisionError("configuration changed during removal")
            os.unlink(CONFIG_NAME, dir_fd=directory_fd)
            os.fsync(directory_fd)
    finally:
        if lock_fd >= 0:
            _release_private_lock(lock_fd)
        os.close(directory_fd)


def _read_configuration(source: str) -> voice.AsrApiConfig:
    if source != "tty":
        descriptor = 0 if source == "stdin" else 3
        raw = _read_line_from_fd(descriptor, voice.ASR_API_MAX_CONFIG_BYTES)
        return voice.parse_asr_api_config(raw)
    try:
        descriptor = os.open("/dev/tty", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                             | getattr(os, "O_NOCTTY", 0))
    except OSError:
        raise ProvisionError("an interactive TTY is required") from None
    try:
        values = []
        for label, maximum in (("Full HTTPS transcription endpoint", 2048),
                               ("Model", 256), ("API Key", 4096)):
            os.write(2, (label + " (input hidden): ").encode("ascii"))
            values.append(_read_line_from_fd(descriptor, maximum).decode("ascii"))
        return voice.AsrApiConfig(endpoint=values[0], model=values[1], api_key=values[2])
    finally:
        os.close(descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    # Reject extra argv without echoing it: an accidental key must stay private.
    argv = list(sys.argv[1:] if argv is None else argv)
    valid = (["provision"], ["provision", "--confirm-stdin"],
             ["provision", "--fd3"], ["check"], ["remove"])
    if argv in (["--help"], ["-h"]):
        print("Usage: pocketds-asr-api-provision {provision [--confirm-stdin|--fd3],check,remove}")
        print("Provision prompts for endpoint, model and API Key; pipe modes accept one JSON line.")
        return 0
    if argv not in valid:
        print("Invalid arguments; use --help. Credentials are never accepted as arguments.", file=sys.stderr)
        return 2
    try:
        _disable_process_dumpability()
        if argv[0] == "provision":
            source = "stdin" if "--confirm-stdin" in argv else "fd3" if "--fd3" in argv else "tty"
            provision(_read_configuration(source), private_dir=PRIVATE_DIR)
            print("Transcription API configured locally; no request was sent.")
        elif argv[0] == "check":
            print("configured" if check(private_dir=PRIVATE_DIR) else "not_configured")
        else:
            remove(private_dir=PRIVATE_DIR)
            print("Transcription API configuration removed.")
    except (ProvisionError, voice.VoiceArtifactError, OSError, UnicodeError):
        print("API configuration operation refused; check input and private file permissions.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

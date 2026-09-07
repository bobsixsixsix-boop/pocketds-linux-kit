#!/usr/bin/python3
"""Private, bounded lifecycle helpers for Pocket DS voice artifacts."""

from __future__ import annotations

import ctypes
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import fcntl
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import resource
import secrets
import selectors
import signal
import socket
import ssl
import stat
import struct
import subprocess
import sys
import threading
import time
import unicodedata
from typing import BinaryIO, Callable, Iterator, Sequence
from urllib.parse import urlsplit


MAX_TRANSCRIPT_CHARS = 4_096
MAX_SENSEVOICE_OUTPUT_BYTES = 262_144
MAX_BACKEND_BINARY_BYTES = 128 * 1024 * 1024
MAX_BACKEND_LIBRARY_BYTES = 128 * 1024 * 1024
MAX_MODEL_BYTES = 1024 * 1024 * 1024
MAX_TOKENS_BYTES = 4 * 1024 * 1024
PROCESS_GROUP_TERM_GRACE_SECONDS = 0.35
PROCESS_GROUP_KILL_GRACE_SECONDS = 1.0

ASR_API_MAX_CONFIG_BYTES = 8192


ASR_API_MAX_WAV_BYTES = 25 * 1024 * 1024
ASR_API_MAX_RESPONSE_BYTES = 65_536
ASR_API_MAX_HEADER_BYTES = 16_384
ASR_API_TOTAL_TIMEOUT_SECONDS = 75.0
ASR_API_CONNECT_TIMEOUT_SECONDS = 10.0
ASR_API_CANCEL_POLL_SECONDS = 0.05
ASR_API_MAX_RETRIES = 2
ASR_API_MAX_RETRY_AFTER_SECONDS = 8.0
ASR_API_RETRY_DELAYS_SECONDS = (2.0, 5.0)



LIVE_WAV_HEADER_MAX_BYTES = 65_536
# With no fixed duration, alsa-utils arecord writes a roughly 2-GiB ceiling
# into the live header and replaces both fields with observed lengths on exit.
# Fedora builds have emitted both the signed-limit and unsigned-boundary forms.
ARECORD_LIVE_LENGTH_PAIRS = frozenset(
    (
        (0x7FFFFF24, 0x7FFFFF00),
        (0x80000024, 0x80000000),
    )
)
ARECORD_LIVE_RIFF_SIZES = frozenset(
    riff_size for riff_size, _data_size in ARECORD_LIVE_LENGTH_PAIRS
)
ARECORD_LIVE_DATA_SIZES = frozenset(
    data_size for _riff_size, data_size in ARECORD_LIVE_LENGTH_PAIRS
)
LIVE_WAV_RIFF_SIZE_PLACEHOLDERS = frozenset((0, 8, 0xFFFFFFFF)).union(
    ARECORD_LIVE_RIFF_SIZES
)
LIVE_WAV_DATA_SIZE_PLACEHOLDERS = frozenset((0, 0xFFFFFFFF)).union(
    ARECORD_LIVE_DATA_SIZES
)

_ACCOUNT_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
ASR_API_PRIVATE_DIR = _ACCOUNT_HOME / ".config/pocketds-keyboard/asr-api"
ASR_API_CONFIG_FILE = ASR_API_PRIVATE_DIR / "config.json"
ASR_API_LOCK_FILE = ASR_API_PRIVATE_DIR / ".config.lock"

_API_KEY_RE = re.compile(r"[!-~]{1,4096}")
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}")


_ASR_API_SINGLE_FLIGHT = threading.Lock()
_ASR_API_TRANSPORT_FLIGHT = threading.Lock()
_PR_GET_DUMPABLE = 3
_PR_SET_DUMPABLE = 4
_PR_GET_SECCOMP = 21
_PR_SET_NO_NEW_PRIVS = 38
_PR_GET_NO_NEW_PRIVS = 39
_SECCOMP_MODE_FILTER = 2
_SCMP_ACT_ALLOW = 0x7FFF0000
_SCMP_ACT_ERRNO = 0x00050000
_ASR_API_WORKER_REQUEST_MAGIC = b"PDSASQ02"
_ASR_API_WORKER_RESPONSE_MAGIC = b"PDSASO02"
_ASR_API_WORKER_REQUEST = struct.Struct("!8sIId")
_ASR_API_WORKER_RESPONSE = struct.Struct("!8sBHIH")
_ASR_API_WORKER_OK = 0
_ASR_API_WORKER_NETWORK_ERROR = 1
_ASR_API_WORKER_PROTOCOL_ERROR = 2
_ASR_API_WORKER_DUMPABILITY_EXIT = 70
_ASR_API_WORKER_CONFINEMENT_EXIT = 71
_ASR_API_MAX_WORKER_RESPONSE_BYTES = (
    _ASR_API_WORKER_RESPONSE.size
    + ASR_API_MAX_HEADER_BYTES
    + ASR_API_MAX_RESPONSE_BYTES
    + 4_096
)


class VoiceArtifactError(RuntimeError):
    """A stale, unsafe, malformed or oversized ASR artifact."""


class AsrApiError(VoiceArtifactError):
    """A sanitized cloud-ASR configuration, protocol or availability error."""

    def __init__(self, public_message: str, *, status: int | None = None) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.status = status


class AsrApiCancelled(AsrApiError):
    """The current voice generation was revoked while cloud ASR was active."""


class _AsrApiNetworkError(AsrApiError):
    """A retryable, already-sanitized network-stage failure."""


def disable_process_dumpability() -> bool:
    """Fail closed if the Linux keyboard process cannot suppress core dumps."""

    if not sys.platform.startswith("linux"):
        return False
    try:
        library = ctypes.CDLL(None, use_errno=True)
        prctl = library.prctl
        prctl.argtypes = (
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        )
        prctl.restype = ctypes.c_int
        if prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_DUMPABLE failed")
        if prctl(_PR_GET_DUMPABLE, 0, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_GET_DUMPABLE did not verify")
    except (AttributeError, OSError):
        raise VoiceArtifactError("process core-dump suppression is unavailable") from None
    return True


def _confine_asr_api_worker_before_secret() -> bool:
    """Deny every process/thread creation path before reading the pipe."""

    if not sys.platform.startswith("linux") or os.getuid() == 0:
        raise VoiceArtifactError("worker process confinement is unavailable")
    context = None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.argtypes = (
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        )
        prctl.restype = ctypes.c_int
        if prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS failed")
        if prctl(_PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1:
            raise OSError(ctypes.get_errno(), "PR_GET_NO_NEW_PRIVS did not verify")

        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
        if resource.getrlimit(resource.RLIMIT_NPROC) != (0, 0):
            raise OSError("RLIMIT_NPROC did not verify")

        seccomp = ctypes.CDLL("libseccomp.so.2", use_errno=True)
        seccomp.seccomp_init.argtypes = (ctypes.c_uint32,)
        seccomp.seccomp_init.restype = ctypes.c_void_p
        seccomp.seccomp_syscall_resolve_name.argtypes = (ctypes.c_char_p,)
        seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
        seccomp.seccomp_rule_add.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint,
        )
        seccomp.seccomp_rule_add.restype = ctypes.c_int
        seccomp.seccomp_load.argtypes = (ctypes.c_void_p,)
        seccomp.seccomp_load.restype = ctypes.c_int
        seccomp.seccomp_release.argtypes = (ctypes.c_void_p,)
        seccomp.seccomp_release.restype = None

        context = seccomp.seccomp_init(_SCMP_ACT_ALLOW)
        if not context:
            raise OSError("seccomp filter allocation failed")
        blocked = 0
        required = {"clone", "clone3"}
        resolved_required: set[str] = set()
        deny_action = _SCMP_ACT_ERRNO | errno.EPERM
        for name in ("clone", "clone3", "fork", "vfork", "unshare", "setns"):
            syscall_number = seccomp.seccomp_syscall_resolve_name(
                name.encode("ascii")
            )
            if syscall_number < 0:
                continue
            if name in required:
                resolved_required.add(name)
            if (
                seccomp.seccomp_rule_add(
                    context,
                    deny_action,
                    syscall_number,
                    0,
                )
                != 0
            ):
                raise OSError("seccomp process rule installation failed")
            blocked += 1
        if resolved_required != required or blocked < 2:
            raise OSError("seccomp process syscall coverage is incomplete")
        if seccomp.seccomp_load(context) != 0:
            raise OSError("seccomp process filter load failed")
        if prctl(_PR_GET_SECCOMP, 0, 0, 0, 0) != _SECCOMP_MODE_FILTER:
            raise OSError("seccomp process filter did not verify")
    except (AttributeError, OSError, ValueError):
        raise VoiceArtifactError("worker process confinement is unavailable") from None
    finally:
        if context is not None:
            try:
                seccomp.seccomp_release(context)
            except (AttributeError, OSError):
                pass

    try:
        child = os.fork()
    except OSError as exc:
        if exc.errno in (errno.EAGAIN, errno.EPERM):
            return True
        raise VoiceArtifactError("worker process confinement did not verify") from None
    if child == 0:
        os._exit(_ASR_API_WORKER_CONFINEMENT_EXIT)
    try:
        os.kill(child, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.waitpid(child, 0)
    except OSError:
        pass
    raise VoiceArtifactError("worker process confinement did not verify")


@dataclass(frozen=True, repr=False)
class AsrApiConfig:
    endpoint: str
    api_key: str
    model: str

    def __repr__(self) -> str:
        return "AsrApiConfig(<redacted>)"


def validate_asr_api_config(config: AsrApiConfig) -> None:
    """Require an explicit HTTPS destination; never infer a provider or model."""
    if (type(config) is not AsrApiConfig or type(config.api_key) is not str
            or not _API_KEY_RE.fullmatch(config.api_key)
            or type(config.model) is not str or not _MODEL_RE.fullmatch(config.model)
            or type(config.endpoint) is not str or not 1 <= len(config.endpoint) <= 2048
            or any(ord(c) < 33 or ord(c) > 126 for c in config.endpoint)
            or "\\" in config.endpoint):
        raise AsrApiError("语音 API 配置格式无效")
    try:
        url = urlsplit(config.endpoint)
        valid = (url.scheme == "https" and bool(url.hostname)
                 and url.username is None and url.password is None
                 and not url.query and not url.fragment
                 and "?" not in config.endpoint and "#" not in config.endpoint
                 and url.path.startswith("/") and not url.path.startswith("//")
                 and (url.port is None or 1 <= url.port <= 65535)
                 and bool(re.fullmatch(r"[A-Za-z0-9.:-]+", url.hostname)))
    except ValueError:
        valid = False
    if not valid:
        raise AsrApiError("语音 API 地址须为不含账号、查询参数或片段的完整 HTTPS 转写地址")


def encode_asr_api_config(config: AsrApiConfig) -> bytes:
    validate_asr_api_config(config)
    content = json.dumps({"schema": 1, "endpoint": config.endpoint,
                          "api_key": config.api_key, "model": config.model},
                         ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
    if len(content) > ASR_API_MAX_CONFIG_BYTES:
        raise AsrApiError("语音 API 配置过大")
    return content


def parse_asr_api_config(content: bytes) -> AsrApiConfig:
    if type(content) is not bytes or not 0 < len(content) <= ASR_API_MAX_CONFIG_BYTES:
        raise AsrApiError("语音 API 配置为空或过大")
    payload = _parse_asr_api_json(content)
    if (set(payload) != {"schema", "endpoint", "api_key", "model"}
            or type(payload["schema"]) is not int or payload["schema"] != 1):
        raise AsrApiError("语音 API 配置结构无效")
    config = AsrApiConfig(payload["endpoint"], payload["api_key"], payload["model"])
    validate_asr_api_config(config)
    return config


@dataclass(frozen=True, repr=False)
class AsrApiHttpResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def __repr__(self) -> str:
        return f"AsrApiHttpResponse(status={self.status}, body_bytes={len(self.body)})"


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The backend was started in our own new session. Treat an unexpected
        # permission failure as still alive so the hard-kill path is attempted.
        return True
    return True


def _signal_process_group(process_group: int, requested_signal: int) -> None:
    try:
        os.killpg(process_group, requested_signal)
    except ProcessLookupError:
        pass


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Boundedly terminate and reap one isolated backend process tree."""

    process_group = process.pid
    process.poll()
    if not _process_group_exists(process_group):
        return

    _signal_process_group(process_group, signal.SIGTERM)
    deadline = time.monotonic() + PROCESS_GROUP_TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        process.poll()
        if not _process_group_exists(process_group):
            return
        time.sleep(0.01)

    _signal_process_group(process_group, signal.SIGKILL)
    try:
        process.wait(timeout=PROCESS_GROUP_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise VoiceArtifactError("local ASR process group did not exit") from exc


def normalize_transcript(value: str) -> str:
    """Normalize one backend result and reject control or invisible payloads."""

    if type(value) is not str:
        raise VoiceArtifactError("ASR transcript is not text")
    transcript = unicodedata.normalize("NFKC", value)
    transcript = " ".join(transcript.split()).strip()
    if (
        not transcript
        or len(transcript) > MAX_TRANSCRIPT_CHARS
        or any(unicodedata.category(character).startswith("C") for character in transcript)
    ):
        raise VoiceArtifactError("ASR transcript is empty or unsafe")
    return transcript


def _read_private_config_bytes(
    path: Path,
    *,
    directory_fd: int,
    label: str,
    maximum: int,
    expected_uid: int,
) -> bytes:
    """Read one fixed private configuration without following the final link."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path.name, flags, dir_fd=directory_fd)
    except OSError:
        raise AsrApiError(f"语音 API {label}配置不可用") from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 0 < metadata.st_size <= maximum + 1
        ):
            raise AsrApiError(f"语音 API {label}配置不安全")
        content = bytearray()
        remaining = metadata.st_size
        while remaining:
            block = os.read(descriptor, min(4_096, remaining))
            if not block:
                raise AsrApiError(f"语音 API {label}配置读取失败")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise AsrApiError(f"语音 API {label}配置发生变化")
        after = os.fstat(descriptor)
        canonical = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_uid != expected_uid
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != 0o600
            or after.st_dev != metadata.st_dev
            or after.st_ino != metadata.st_ino
            or after.st_size != metadata.st_size
            or after.st_mtime_ns != metadata.st_mtime_ns
            or (canonical.st_dev, canonical.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise AsrApiError(f"语音 API {label}配置发生变化")
    finally:
        os.close(descriptor)

    return bytes(content)


def _open_asr_api_private_directory() -> int:
    """Bind credential reads to one owned 0700 non-symlink directory inode."""

    if (ASR_API_CONFIG_FILE.parent != ASR_API_PRIVATE_DIR
            or ASR_API_LOCK_FILE.parent != ASR_API_PRIVATE_DIR
            or ASR_API_CONFIG_FILE.name != "config.json"
            or ASR_API_LOCK_FILE.name != ".config.lock"):
        raise AsrApiError("语音 API 固定配置路径无效")


    try:
        metadata = os.lstat(ASR_API_PRIVATE_DIR)
    except FileNotFoundError:
        raise AsrApiError("语音 API 尚未配置") from None
    except OSError:
        raise AsrApiError("语音 API 私有配置目录不可用") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise AsrApiError("语音 API 私有配置目录不安全")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(ASR_API_PRIVATE_DIR, flags)
    except OSError:
        raise AsrApiError("语音 API 私有配置目录不可打开") from None
    try:
        opened = os.fstat(descriptor)
    except OSError:
        os.close(descriptor)
        raise AsrApiError("语音 API 私有配置目录不可检查") from None
    if (
        opened.st_dev != metadata.st_dev
        or opened.st_ino != metadata.st_ino
        or not stat.S_ISDIR(opened.st_mode)
        or opened.st_uid != os.getuid()
        or stat.S_IMODE(opened.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise AsrApiError("语音 API 私有配置目录发生变化")
    return descriptor


def _asr_api_credential_files_absent(directory_fd: int) -> bool:
    """Recognize an unconfigured account using metadata only, before any read."""

    for path in (ASR_API_CONFIG_FILE,):
        try:
            os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            return False
        return False
    return True


def _open_asr_api_shared_lock(
    directory_fd: int,
    *,
    expected_uid: int,
    cancelled: Callable[[], bool] | None = None,
    deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Acquire the provisioner's existing fixed lock inode in shared mode."""

    flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    descriptor = -1
    try:
        descriptor = os.open(ASR_API_LOCK_FILE.name, flags, dir_fd=directory_fd)
        metadata = os.fstat(descriptor)
        canonical = os.stat(
            ASR_API_LOCK_FILE.name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        unconfigured = (
            descriptor < 0
            and isinstance(exc, FileNotFoundError)
            and _asr_api_credential_files_absent(directory_fd)
        )
        if descriptor >= 0:
            os.close(descriptor)
        if unconfigured:
            raise AsrApiError("语音 API 尚未配置") from None
        raise AsrApiError("语音 API 凭据锁不可用") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (metadata.st_dev, metadata.st_ino)
        != (canonical.st_dev, canonical.st_ino)
    ):
        os.close(descriptor)
        raise AsrApiError("语音 API 凭据锁身份不安全")

    try:
        while True:
            if cancelled is not None and cancelled():
                raise AsrApiCancelled("语音识别已取消")
            if deadline is not None and monotonic() >= deadline:
                raise AsrApiError("语音 API 请求达到总时限")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise AsrApiError("语音 API 凭据锁不可获取") from None
                sleep(ASR_API_CANCEL_POLL_SECONDS)
        try:
            after_fd = os.fstat(descriptor)
            after_path = os.stat(
                ASR_API_LOCK_FILE.name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except OSError:
            raise AsrApiError("语音 API 凭据锁发生变化") from None
        if (
            not stat.S_ISREG(after_fd.st_mode)
            or after_fd.st_uid != expected_uid
            or after_fd.st_nlink != 1
            or stat.S_IMODE(after_fd.st_mode) != 0o600
            or (after_fd.st_dev, after_fd.st_ino)
            != (metadata.st_dev, metadata.st_ino)
            or (after_path.st_dev, after_path.st_ino)
            != (metadata.st_dev, metadata.st_ino)
        ):
            raise AsrApiError("语音 API 凭据锁发生变化")
        return descriptor
    except Exception:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
        raise


@contextmanager
def _locked_asr_api_credentials(
    *,
    expected_uid: int | None = None,
    cancelled: Callable[[], bool] | None = None,
    deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[tuple[AsrApiConfig, int]]:
    """Hold LOCK_SH across the credential snapshot and its entire use."""

    owner = os.getuid() if expected_uid is None else expected_uid
    directory_fd = _open_asr_api_private_directory()
    lock_fd = -1
    try:
        lock_fd = _open_asr_api_shared_lock(
            directory_fd,
            expected_uid=owner,
            cancelled=cancelled,
            deadline=deadline,
            monotonic=monotonic,
            sleep=sleep,
        )
        if _asr_api_credential_files_absent(directory_fd):
            raise AsrApiError("语音 API 尚未配置")
        content = _read_private_config_bytes(
            ASR_API_CONFIG_FILE, directory_fd=directory_fd, label="转写",
            maximum=ASR_API_MAX_CONFIG_BYTES, expected_uid=owner,
        )
        yield parse_asr_api_config(content), lock_fd


    finally:
        try:
            if lock_fd >= 0:
                # Never issue LOCK_UN here. The HTTPS worker inherits this
                # same open-file-description; if cleanup cannot prove that
                # worker exited, closing only the parent's descriptor keeps
                # rollback blocked until the last inherited descriptor dies.
                os.close(lock_fd)
        finally:
            os.close(directory_fd)


def load_asr_api_config(*, expected_uid: int | None = None) -> AsrApiConfig:
    """Load one shared-lock-protected credential snapshot, never env or argv."""

    with _locked_asr_api_credentials(expected_uid=expected_uid) as (credentials, _lock):
        return credentials


def private_wav_has_pcm_payload(
    path: Path,
    *,
    expected_uid: int | None = None,
) -> bool:
    """Probe a live private WAV for at least one complete PCM sample.

    A recorder cannot finalize RIFF and data lengths until it closes the file,
    so this deliberately does not call the final-upload validator.  It still
    binds the path to one owned, private, single-link regular inode; validates
    the requested 16-kHz mono 16-bit PCM format; and requires bytes beyond the
    data-chunk header.  Missing, empty, or incomplete files are simply not
    ready. Unsafe identities and malformed complete headers fail closed.
    """

    owner = os.getuid() if expected_uid is None else expected_uid
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return False
    except OSError:
        raise VoiceArtifactError("live recording file is unsafe") from None

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise VoiceArtifactError("live recording file type is unsafe")
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            metadata.st_uid != owner
            or metadata.st_nlink != 1
            or mode & 0o077
            or not mode & 0o400
            or metadata.st_size > ASR_API_MAX_WAV_BYTES
        ):
            raise VoiceArtifactError("live recording file identity is unsafe")
        if metadata.st_size == 0:
            return False

        maximum = min(metadata.st_size, LIVE_WAV_HEADER_MAX_BYTES)
        content = bytearray()
        while len(content) < maximum:
            block = os.read(descriptor, maximum - len(content))
            if not block:
                break
            content.extend(block)
        after = os.fstat(descriptor)
        try:
            canonical = os.stat(path, follow_symlinks=False)
        except OSError:
            raise VoiceArtifactError("live recording path changed") from None
        if not stat.S_ISREG(after.st_mode):
            raise VoiceArtifactError("live recording file type changed")
        if (
            after.st_dev != metadata.st_dev
            or after.st_ino != metadata.st_ino
            or after.st_uid != owner
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) & 0o077
            or not stat.S_IMODE(after.st_mode) & 0o400
            or after.st_size < metadata.st_size
            or after.st_size > ASR_API_MAX_WAV_BYTES
            or not stat.S_ISREG(canonical.st_mode)
            or canonical.st_dev != metadata.st_dev
            or canonical.st_ino != metadata.st_ino
            or canonical.st_uid != owner
            or canonical.st_nlink != 1
            or stat.S_IMODE(canonical.st_mode) & 0o077
            or not stat.S_IMODE(canonical.st_mode) & 0o400
            or canonical.st_size < after.st_size
            or canonical.st_size > ASR_API_MAX_WAV_BYTES
            or len(content) != maximum
        ):
            raise VoiceArtifactError("live recording path changed")
    finally:
        os.close(descriptor)

    snapshot = bytes(content)
    if len(snapshot) < 12:
        return False
    if snapshot[:4] != b"RIFF" or snapshot[8:12] != b"WAVE":
        raise VoiceArtifactError("live recording is not RIFF/WAVE")
    riff_size = struct.unpack_from("<I", snapshot, 4)[0]
    observed_file_sizes = {
        metadata.st_size,
        after.st_size,
        canonical.st_size,
    }
    if (
        riff_size not in LIVE_WAV_RIFF_SIZE_PLACEHOLDERS
        and riff_size + 8 not in observed_file_sizes
    ):
        raise VoiceArtifactError("live recording RIFF length is invalid")

    offset = 12
    chunk_count = 0
    format_seen = False
    while offset < len(snapshot):
        if offset + 8 > len(snapshot):
            return False
        chunk_id = snapshot[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", snapshot, offset + 4)[0]
        offset += 8
        chunk_count += 1
        if chunk_count > 64:
            raise VoiceArtifactError("live recording has too many chunks")

        if chunk_id == b"data":
            if not format_seen:
                raise VoiceArtifactError("live recording data precedes format")
            uses_arecord_placeholder = (
                riff_size in ARECORD_LIVE_RIFF_SIZES
                or chunk_size in ARECORD_LIVE_DATA_SIZES
            )
            if (
                uses_arecord_placeholder
                and (riff_size, chunk_size) not in ARECORD_LIVE_LENGTH_PAIRS
            ):
                raise VoiceArtifactError("live arecord lengths do not match")
            # The declared size can be zero or a placeholder while libsndfile
            # or arecord is still recording. The bytes already present on this
            # inode are authoritative; require one complete 16-bit frame.
            payload_bytes = len(snapshot) - offset
            available_payload_bytes = max(observed_file_sizes) - offset
            if (
                chunk_size not in LIVE_WAV_DATA_SIZE_PLACEHOLDERS
                and (chunk_size % 2 or chunk_size > available_payload_bytes)
            ):
                raise VoiceArtifactError("live recording data length is invalid")
            return payload_bytes >= 2

        chunk_end = offset + chunk_size
        padded_end = chunk_end + (chunk_size & 1)
        if padded_end > len(snapshot):
            if metadata.st_size > len(snapshot):
                raise VoiceArtifactError("live recording header is oversized")
            return False
        if chunk_id == b"fmt ":
            if format_seen or not 16 <= chunk_size <= 40:
                raise VoiceArtifactError("live recording format chunk is invalid")
            audio_format, channels, sample_rate, byte_rate, block_align, bits = (
                struct.unpack_from("<HHIIHH", snapshot, offset)
            )
            if (
                audio_format != 1
                or channels != 1
                or sample_rate != 16_000
                or byte_rate != 32_000
                or block_align != 2
                or bits != 16
            ):
                raise VoiceArtifactError("live recording PCM format is invalid")
            format_seen = True
        offset = padded_end

    return False


def finalize_private_arecord_wav(
    path: Path,
    *,
    expected_uid: int | None = None,
) -> None:
    """Finalize the one live-WAV shape left by device arecord after SIGINT.

    Direct Qualcomm ALSA capture exits with EINTR before alsa-utils can replace
    its correlated live RIFF/data length placeholders.  The recorder is fully
    reaped before this helper runs.  Keep the private inode in place, accept
    only the exact 16-kHz mono S16_LE arecord shape, and make the two bounded
    four-byte length repairs that arecord itself would normally perform.
    """

    owner = os.getuid() if expected_uid is None else expected_uid
    flags = (
        os.O_RDWR
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise VoiceArtifactError("interrupted recording file is unsafe") from None

    try:
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != owner
            or metadata.st_nlink != 1
            or mode != 0o600
            or not 46 <= metadata.st_size <= ASR_API_MAX_WAV_BYTES
        ):
            raise VoiceArtifactError("interrupted recording identity is unsafe")

        header_size = min(metadata.st_size, LIVE_WAV_HEADER_MAX_BYTES)
        header = os.pread(descriptor, header_size, 0)
        if len(header) != header_size:
            raise VoiceArtifactError("interrupted recording header is incomplete")
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise VoiceArtifactError("interrupted recording is not RIFF/WAVE")

        riff_size = struct.unpack_from("<I", header, 4)[0]
        offset = 12
        chunk_count = 0
        format_seen = False
        data_size_offset = None
        data_offset = None
        data_placeholder = None
        while offset < len(header):
            if offset + 8 > len(header):
                raise VoiceArtifactError("interrupted WAV chunk is incomplete")
            chunk_id = header[offset : offset + 4]
            chunk_size = struct.unpack_from("<I", header, offset + 4)[0]
            chunk_size_offset = offset + 4
            offset += 8
            chunk_count += 1
            if chunk_count > 64:
                raise VoiceArtifactError("interrupted WAV has too many chunks")

            if chunk_id == b"data":
                if not format_seen:
                    raise VoiceArtifactError("interrupted WAV data precedes format")
                data_size_offset = chunk_size_offset
                data_offset = offset
                data_placeholder = chunk_size
                break

            chunk_end = offset + chunk_size
            padded_end = chunk_end + (chunk_size & 1)
            if padded_end > len(header):
                raise VoiceArtifactError("interrupted WAV header is oversized")
            if chunk_id == b"fmt ":
                if format_seen or not 16 <= chunk_size <= 40:
                    raise VoiceArtifactError("interrupted WAV format is invalid")
                audio_format, channels, sample_rate, byte_rate, block_align, bits = (
                    struct.unpack_from("<HHIIHH", header, offset)
                )
                if (
                    audio_format != 1
                    or channels != 1
                    or sample_rate != 16_000
                    or byte_rate != 32_000
                    or block_align != 2
                    or bits != 16
                ):
                    raise VoiceArtifactError("interrupted WAV PCM format is invalid")
                format_seen = True
            offset = padded_end

        if data_size_offset is None or data_offset is None or data_placeholder is None:
            raise VoiceArtifactError("interrupted WAV has no data chunk")
        if (riff_size, data_placeholder) not in ARECORD_LIVE_LENGTH_PAIRS:
            raise VoiceArtifactError("interrupted arecord lengths do not match")

        payload_size = metadata.st_size - data_offset
        if payload_size <= 0 or payload_size % 2:
            raise VoiceArtifactError("interrupted WAV payload is invalid")
        final_riff_size = metadata.st_size - 8
        if final_riff_size > 0xFFFFFFFF or payload_size > 0xFFFFFFFF:
            raise VoiceArtifactError("interrupted WAV is too large")

        try:
            canonical = os.lstat(path)
            before = os.fstat(descriptor)
        except OSError:
            raise VoiceArtifactError("interrupted recording path changed") from None
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_dev != metadata.st_dev
            or before.st_ino != metadata.st_ino
            or before.st_uid != owner
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != metadata.st_size
            or before.st_mtime_ns != metadata.st_mtime_ns
            or before.st_ctime_ns != metadata.st_ctime_ns
            or not stat.S_ISREG(canonical.st_mode)
            or canonical.st_dev != metadata.st_dev
            or canonical.st_ino != metadata.st_ino
            or canonical.st_uid != owner
            or canonical.st_nlink != 1
            or stat.S_IMODE(canonical.st_mode) != 0o600
            or canonical.st_size != metadata.st_size
            or canonical.st_mtime_ns != metadata.st_mtime_ns
            or canonical.st_ctime_ns != metadata.st_ctime_ns
        ):
            raise VoiceArtifactError("interrupted recording path changed")

        if os.pwrite(descriptor, struct.pack("<I", payload_size), data_size_offset) != 4:
            raise VoiceArtifactError("interrupted WAV data length was not repaired")
        if os.pwrite(descriptor, struct.pack("<I", final_riff_size), 4) != 4:
            raise VoiceArtifactError("interrupted WAV RIFF length was not repaired")
        os.fsync(descriptor)

        after = os.fstat(descriptor)
        try:
            canonical_after = os.lstat(path)
        except OSError:
            raise VoiceArtifactError("finalized recording path changed") from None
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_dev != metadata.st_dev
            or after.st_ino != metadata.st_ino
            or after.st_uid != owner
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != 0o600
            or after.st_size != metadata.st_size
            or not stat.S_ISREG(canonical_after.st_mode)
            or canonical_after.st_dev != after.st_dev
            or canonical_after.st_ino != after.st_ino
            or canonical_after.st_uid != owner
            or canonical_after.st_nlink != 1
            or stat.S_IMODE(canonical_after.st_mode) != 0o600
            or canonical_after.st_size != after.st_size
        ):
            raise VoiceArtifactError("finalized recording identity changed")
    except OSError:
        raise VoiceArtifactError("interrupted recording could not be finalized") from None
    finally:
        os.close(descriptor)


def _validate_wav(content: bytes) -> None:
    """Accept only one bounded 16-kHz mono signed-PCM RIFF/WAVE recording."""

    if len(content) < 44 or content[:4] != b"RIFF" or content[8:12] != b"WAVE":
        raise AsrApiError("录音不是完整 WAV")
    if struct.unpack_from("<I", content, 4)[0] + 8 != len(content):
        raise AsrApiError("WAV 长度字段无效")

    offset = 12
    chunk_count = 0
    format_seen = False
    data_seen = False
    while offset < len(content):
        if offset + 8 > len(content):
            raise AsrApiError("WAV chunk 不完整")
        chunk_id = content[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", content, offset + 4)[0]
        offset += 8
        chunk_end = offset + chunk_size
        padded_end = chunk_end + (chunk_size & 1)
        if chunk_end > len(content) or padded_end > len(content):
            raise AsrApiError("WAV chunk 越界")
        chunk_count += 1
        if chunk_count > 64:
            raise AsrApiError("WAV chunk 过多")

        if chunk_id == b"fmt ":
            if format_seen or not 16 <= chunk_size <= 40:
                raise AsrApiError("WAV 格式块无效")
            format_seen = True
            audio_format, channels, sample_rate, byte_rate, block_align, bits = (
                struct.unpack_from("<HHIIHH", content, offset)
            )
            if (
                audio_format != 1
                or channels != 1
                or sample_rate != 16_000
                or byte_rate != 32_000
                or block_align != 2
                or bits != 16
            ):
                raise AsrApiError("WAV 必须是 16kHz 单声道 16-bit PCM")
        elif chunk_id == b"data":
            if data_seen or chunk_size == 0 or chunk_size % 2:
                raise AsrApiError("WAV 音频块无效")
            data_seen = True
        offset = padded_end

    if offset != len(content) or not format_seen or not data_seen:
        raise AsrApiError("WAV 缺少必要格式或音频块")


def read_private_wav(path: Path, *, expected_uid: int | None = None) -> bytes:
    """Read one owner-private single-link WAV into the bounded request body."""

    owner = os.getuid() if expected_uid is None else expected_uid
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise AsrApiError("录音文件不可用或为链接") from None
    try:
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != owner
            or metadata.st_nlink != 1
            or mode != 0o600
            or not 44 <= metadata.st_size <= ASR_API_MAX_WAV_BYTES
        ):
            raise AsrApiError("录音文件权限、类型或大小不安全")
        content = bytearray()
        remaining = metadata.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise AsrApiError("录音文件读取不完整")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise AsrApiError("录音文件在读取时增长")
        after = os.fstat(descriptor)
        try:
            canonical = os.lstat(path)
        except OSError:
            raise AsrApiError("录音文件路径在读取时发生变化") from None
        after_mode = stat.S_IMODE(after.st_mode)
        canonical_mode = stat.S_IMODE(canonical.st_mode)
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_dev != metadata.st_dev
            or after.st_ino != metadata.st_ino
            or after.st_uid != owner
            or after.st_nlink != 1
            or after_mode != 0o600
            or after.st_size != metadata.st_size
            or after.st_mtime_ns != metadata.st_mtime_ns
            or after.st_ctime_ns != metadata.st_ctime_ns
            or not stat.S_ISREG(canonical.st_mode)
            or canonical.st_dev != after.st_dev
            or canonical.st_ino != after.st_ino
            or canonical.st_uid != owner
            or canonical.st_nlink != 1
            or canonical_mode != 0o600
            or canonical.st_size != after.st_size
            or canonical.st_mtime_ns != after.st_mtime_ns
            or canonical.st_ctime_ns != after.st_ctime_ns
        ):
            raise AsrApiError("录音文件在读取时发生变化")
    finally:
        os.close(descriptor)
    result = bytes(content)
    _validate_wav(result)
    return result


def _strict_asr_api_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AsrApiError("语音 API 响应包含重复字段")
        result[key] = value
    return result


def _parse_asr_api_json(content: bytes) -> dict[str, object]:
    if type(content) is not bytes or not 0 < len(content) <= ASR_API_MAX_RESPONSE_BYTES:
        raise AsrApiError("语音 API 响应为空或过大")
    try:
        payload = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_asr_api_object,
            parse_constant=lambda _item: (_ for _ in ()).throw(
                AsrApiError("语音 API 响应含非有限数字")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise AsrApiError("语音 API 响应不是有效 UTF-8 JSON") from None
    if type(payload) is not dict:
        raise AsrApiError("语音 API 响应结构无效")
    return payload


def parse_asr_api_transcript(content: bytes) -> str | None:
    """Return only normalized transcript text; empty text means no result."""

    payload = _parse_asr_api_json(content)
    if type(payload.get("text")) is not str:
        raise AsrApiError("语音 API 成功响应结构无效")
    text = payload["text"]
    assert isinstance(text, str)
    if not text.strip():
        return None
    try:
        return normalize_transcript(text)
    except VoiceArtifactError:
        raise AsrApiError("语音 API 文本结果不安全") from None





def _validated_http_response(response: object) -> tuple[int, dict[str, str], bytes]:
    if type(response) is not AsrApiHttpResponse:
        raise AsrApiError("语音 API transport 返回结构无效")
    assert isinstance(response, AsrApiHttpResponse)
    if (
        type(response.status) is not int
        or not 100 <= response.status <= 599
        or type(response.headers) is not tuple
        or type(response.body) is not bytes
        or len(response.body) > ASR_API_MAX_RESPONSE_BYTES
    ):
        raise AsrApiError("语音 API HTTP 响应超出边界")
    headers: dict[str, str] = {}
    total = 0
    for pair in response.headers:
        if (
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not str
            or type(pair[1]) is not str
        ):
            raise AsrApiError("语音 API HTTP 头结构无效")
        name, value = pair
        try:
            encoded_name = name.encode("ascii", errors="strict")
            encoded_value = value.encode("ascii", errors="strict")
        except UnicodeEncodeError:
            raise AsrApiError("语音 API HTTP 头编码无效") from None
        total += len(encoded_name) + len(encoded_value) + 4
        if (
            not encoded_name
            or len(encoded_name) > 128
            or len(encoded_value) > 4_096
            or total > ASR_API_MAX_HEADER_BYTES
            or any(byte < 0x20 or byte == 0x7F for byte in encoded_name + encoded_value)
        ):
            raise AsrApiError("语音 API HTTP 头超出边界")
        lowered = name.lower()
        if lowered in headers:
            raise AsrApiError("语音 API HTTP 头重复")
        headers[lowered] = value.strip()
    return response.status, headers, response.body


def _require_json_content_type(headers: dict[str, str]) -> None:
    content_type = headers.get("content-type", "").lower()
    if content_type.split(";", 1)[0].strip() != "application/json":
        raise AsrApiError("语音 API HTTP 响应类型无效")


def _retry_after(headers: dict[str, str], fallback: float) -> float:
    value = headers.get("retry-after")
    if value is None or not value.isascii() or not value.isdigit():
        return fallback
    delay = float(value)
    if not 0 <= delay <= ASR_API_MAX_RETRY_AFTER_SECONDS:
        return fallback
    return delay


def _encode_asr_api_worker_request(
    config: AsrApiConfig, wav: bytes, timeout: float,
) -> bytes:
    """Pass the complete config snapshot only through the private pipe."""
    encoded = encode_asr_api_config(config)
    if (type(wav) is not bytes or not 44 <= len(wav) <= ASR_API_MAX_WAV_BYTES
            or type(timeout) not in (int, float)
            or not 0 < timeout <= ASR_API_TOTAL_TIMEOUT_SECONDS):
        raise AsrApiError("语音 API worker 请求无效")
    _validate_wav(wav)
    return (_ASR_API_WORKER_REQUEST.pack(_ASR_API_WORKER_REQUEST_MAGIC,
             len(encoded), len(wav), float(timeout)) + encoded + wav)


def _decode_asr_api_worker_request(content: bytes) -> tuple[AsrApiConfig, bytes, float]:
    if type(content) is not bytes or len(content) < _ASR_API_WORKER_REQUEST.size:
        raise AsrApiError("语音 API worker 请求损坏")
    magic, config_size, wav_size, timeout = _ASR_API_WORKER_REQUEST.unpack_from(content)
    offset = _ASR_API_WORKER_REQUEST.size
    if (magic != _ASR_API_WORKER_REQUEST_MAGIC
            or not 0 < config_size <= ASR_API_MAX_CONFIG_BYTES
            or not 44 <= wav_size <= ASR_API_MAX_WAV_BYTES
            or offset + config_size + wav_size != len(content)
            or not 0 < timeout <= ASR_API_TOTAL_TIMEOUT_SECONDS):
        raise AsrApiError("语音 API worker 请求损坏")
    config = parse_asr_api_config(content[offset:offset + config_size])
    wav = content[offset + config_size:]
    _validate_wav(wav)
    return config, wav, timeout


def _encode_asr_api_worker_response(
    kind: int,
    response: AsrApiHttpResponse | None = None,
) -> bytes:
    if kind != _ASR_API_WORKER_OK:
        if kind not in (_ASR_API_WORKER_NETWORK_ERROR, _ASR_API_WORKER_PROTOCOL_ERROR):
            raise AsrApiError("语音 API worker 结果无效")
        return _ASR_API_WORKER_RESPONSE.pack(
            _ASR_API_WORKER_RESPONSE_MAGIC, kind, 0, 0, 0
        )
    status, _headers, body = _validated_http_response(response)
    assert response is not None
    encoded_headers = bytearray()
    for name, value in response.headers:
        name_bytes = name.encode("ascii", errors="strict")
        value_bytes = value.encode("ascii", errors="strict")
        encoded_headers.extend(struct.pack("!HH", len(name_bytes), len(value_bytes)))
        encoded_headers.extend(name_bytes)
        encoded_headers.extend(value_bytes)
    result = (
        _ASR_API_WORKER_RESPONSE.pack(
            _ASR_API_WORKER_RESPONSE_MAGIC,
            kind,
            status,
            len(body),
            len(response.headers),
        )
        + bytes(encoded_headers)
        + body
    )
    if len(result) > _ASR_API_MAX_WORKER_RESPONSE_BYTES:
        raise AsrApiError("语音 API worker 结果过大")
    return result


def _decode_asr_api_worker_response(content: bytes) -> AsrApiHttpResponse:
    if (
        type(content) is not bytes
        or not _ASR_API_WORKER_RESPONSE.size <= len(content)
        <= _ASR_API_MAX_WORKER_RESPONSE_BYTES
    ):
        raise _AsrApiNetworkError("语音 API worker 无有效结果")
    magic, kind, status, body_size, header_count = (
        _ASR_API_WORKER_RESPONSE.unpack_from(content)
    )
    if magic != _ASR_API_WORKER_RESPONSE_MAGIC:
        raise _AsrApiNetworkError("语音 API worker 无有效结果")
    if kind == _ASR_API_WORKER_NETWORK_ERROR:
        if status or body_size or header_count or len(content) != _ASR_API_WORKER_RESPONSE.size:
            raise _AsrApiNetworkError("语音 API worker 无有效结果")
        raise _AsrApiNetworkError("语音 API 网络不可用")
    if kind == _ASR_API_WORKER_PROTOCOL_ERROR:
        if status or body_size or header_count or len(content) != _ASR_API_WORKER_RESPONSE.size:
            raise _AsrApiNetworkError("语音 API worker 无有效结果")
        raise AsrApiError("语音 API transport 协议失败")
    if kind != _ASR_API_WORKER_OK or not 100 <= status <= 599:
        raise _AsrApiNetworkError("语音 API worker 无有效结果")
    offset = _ASR_API_WORKER_RESPONSE.size
    headers: list[tuple[str, str]] = []
    for _index in range(header_count):
        if offset + 4 > len(content):
            raise _AsrApiNetworkError("语音 API worker 无有效结果")
        name_size, value_size = struct.unpack_from("!HH", content, offset)
        offset += 4
        end = offset + name_size + value_size
        if end > len(content):
            raise _AsrApiNetworkError("语音 API worker 无有效结果")
        try:
            name = content[offset : offset + name_size].decode("ascii", errors="strict")
            offset += name_size
            value = content[offset : offset + value_size].decode("ascii", errors="strict")
        except UnicodeDecodeError:
            raise _AsrApiNetworkError("语音 API worker 无有效结果") from None
        offset = end
        headers.append((name, value))
    if body_size > ASR_API_MAX_RESPONSE_BYTES or offset + body_size != len(content):
        raise _AsrApiNetworkError("语音 API worker 无有效结果")
    response = AsrApiHttpResponse(status, tuple(headers), content[offset:])
    _validated_http_response(response)
    return response


def _asr_api_multipart(model: str, wav: bytes) -> tuple[bytes, str]:
    # An independent boundary and a fixed filename reveal no local file path.
    boundary = "pocketds-" + secrets.token_hex(24)
    while boundary.encode("ascii") in wav:
        boundary = "pocketds-" + secrets.token_hex(24)
    prefix = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n'
        f'{model}\r\n--{boundary}\r\n'
        'Content-Disposition: form-data; name="response_format"\r\n\r\njson\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
        'Content-Type: audio/wav\r\n\r\n'
    ).encode("ascii")
    return (prefix + wav + f"\r\n--{boundary}--\r\n".encode("ascii"),
            f"multipart/form-data; boundary={boundary}")


def _perform_asr_api_https_request(
    config: AsrApiConfig,
    wav: bytes,
    timeout: float,
) -> AsrApiHttpResponse:
    """POST multipart audio to the configured HTTPS endpoint, without redirects."""

    validate_asr_api_config(config)
    url = urlsplit(config.endpoint)
    body, content_type = _asr_api_multipart(config.model, wav)

    deadline = time.monotonic() + timeout
    context = ssl.create_default_context()
    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
        raise AsrApiError("语音 API TLS 校验不可用")
    connection = http.client.HTTPSConnection(
        url.hostname,
        port=url.port or 443,
        timeout=min(ASR_API_CONNECT_TIMEOUT_SECONDS, timeout),
        context=context,
    )
    try:
        connection.connect()
        if time.monotonic() >= deadline or connection.sock is None:
            raise _AsrApiNetworkError("语音 API 请求达到总时限")
        connection.sock.settimeout(max(0.001, deadline - time.monotonic()))
        connection.request(
            "POST",
            url.path,
            body=body,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Accept": "application/json",
                "Content-Type": content_type,
                "Content-Length": str(len(body)),


                "Connection": "close",
            },
        )
        if time.monotonic() >= deadline or connection.sock is None:
            raise _AsrApiNetworkError("语音 API 请求达到总时限")
        connection.sock.settimeout(max(0.001, deadline - time.monotonic()))
        result = connection.getresponse()
        headers = tuple((str(name), str(value)) for name, value in result.getheaders())
        body = bytearray()
        while True:
            if time.monotonic() >= deadline:
                raise _AsrApiNetworkError("语音 API 请求达到总时限")
            if connection.sock is not None:
                connection.sock.settimeout(max(0.001, deadline - time.monotonic()))
            block = result.read(
                min(16_384, ASR_API_MAX_RESPONSE_BYTES + 1 - len(body))
            )
            if not block:
                break
            body.extend(block)
            if len(body) > ASR_API_MAX_RESPONSE_BYTES:
                raise AsrApiError("语音 API 响应过大")
        response = AsrApiHttpResponse(result.status, headers, bytes(body))
        _validated_http_response(response)
        return response
    finally:
        connection.close()


def _write_binary_stream(stream: BinaryIO, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = stream.write(view)
        if written is None:
            written = 0
        if written <= 0:
            raise OSError("worker result write failed")
        view = view[written:]
    stream.flush()


def _asr_api_https_worker_main(
    *,
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
    request: Callable[[AsrApiConfig, bytes, float], AsrApiHttpResponse] = (
        _perform_asr_api_https_request
    ),
) -> int:
    """Worker entry: disable dumps before reading any credential-bearing byte."""

    try:
        if not disable_process_dumpability():
            return _ASR_API_WORKER_DUMPABILITY_EXIT
    except VoiceArtifactError:
        return _ASR_API_WORKER_DUMPABILITY_EXIT
    try:
        if not _confine_asr_api_worker_before_secret():
            return _ASR_API_WORKER_CONFINEMENT_EXIT
    except VoiceArtifactError:
        return _ASR_API_WORKER_CONFINEMENT_EXIT
    source = sys.stdin.buffer if input_stream is None else input_stream
    destination = sys.stdout.buffer if output_stream is None else output_stream
    maximum = (
        _ASR_API_WORKER_REQUEST.size + ASR_API_MAX_CONFIG_BYTES + ASR_API_MAX_WAV_BYTES
    )
    try:
        content = source.read(maximum + 1)
        if type(content) is not bytes or len(content) > maximum:
            raise AsrApiError("语音 API worker 请求损坏")
        config, wav, timeout = _decode_asr_api_worker_request(content)
        try:
            result = request(config, wav, timeout)
        except _AsrApiNetworkError:
            encoded = _encode_asr_api_worker_response(_ASR_API_WORKER_NETWORK_ERROR)
        except (OSError, socket.timeout, ssl.SSLError, http.client.HTTPException):
            encoded = _encode_asr_api_worker_response(_ASR_API_WORKER_NETWORK_ERROR)
        except AsrApiError:
            encoded = _encode_asr_api_worker_response(_ASR_API_WORKER_PROTOCOL_ERROR)
        except Exception:
            encoded = _encode_asr_api_worker_response(_ASR_API_WORKER_NETWORK_ERROR)
        else:
            encoded = _encode_asr_api_worker_response(_ASR_API_WORKER_OK, result)
    except AsrApiError:
        encoded = _encode_asr_api_worker_response(_ASR_API_WORKER_PROTOCOL_ERROR)
    try:
        _write_binary_stream(destination, encoded)
    except (BrokenPipeError, OSError):
        return 74
    return 0


def _asr_api_worker_command() -> tuple[str, ...]:
    return (
        os.path.realpath(sys.executable),
        "-I",
        "-B",
        os.path.realpath(__file__),
        "--asr_api-https-worker",
    )


def _cleanup_asr_api_worker(
    process: subprocess.Popen[bytes] | None,
    selector: selectors.BaseSelector | None,
) -> None:
    """Close pipes, terminate the process group and reap despite cleanup faults."""

    first_error: BaseException | None = None
    if selector is not None:
        try:
            selector.close()
        except BaseException as exc:  # cleanup must continue before propagation
            first_error = exc
    if process is not None:
        for stream in (process.stdin, process.stdout):
            if stream is None or stream.closed:
                continue
            try:
                stream.close()
            except BaseException as exc:  # cleanup must continue before propagation
                if first_error is None:
                    first_error = exc
        try:
            _terminate_process_group(process)
        except BaseException as exc:  # still attempt a direct-child reap
            if first_error is None:
                first_error = exc
        try:
            process.wait(timeout=PROCESS_GROUP_KILL_GRACE_SECONDS)
        except BaseException as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


class _AsrApiHttpsTransport:
    """Cancellable HTTPS via one disposable, killable and reaped subprocess."""

    def post_wav(
        self,
        *,
        config: AsrApiConfig,
        wav: bytes,
        deadline: float,
        cancelled: Callable[[], bool],
        credential_lock_fd: int | None = None,
    ) -> AsrApiHttpResponse:
        if cancelled():
            raise AsrApiCancelled("语音识别已取消")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _AsrApiNetworkError("语音 API 请求达到总时限")
        request = _encode_asr_api_worker_request(
            config,
            wav,
            min(remaining, ASR_API_TOTAL_TIMEOUT_SECONDS),
        )
        pass_fds: tuple[int, ...] = ()
        if credential_lock_fd is not None:
            if type(credential_lock_fd) is not int or credential_lock_fd < 0:
                raise AsrApiError("语音 API 凭据锁描述符无效")
            try:
                os.fstat(credential_lock_fd)
            except OSError:
                raise AsrApiError("语音 API 凭据锁描述符无效") from None
            pass_fds = (credential_lock_fd,)
        if not _ASR_API_TRANSPORT_FLIGHT.acquire(blocking=False):
            raise AsrApiError("上一网络调用仍在安全清理")

        process: subprocess.Popen[bytes] | None = None
        selector: selectors.BaseSelector | None = None
        try:
            try:
                process = subprocess.Popen(
                    list(_asr_api_worker_command()),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env={
                        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                        "LANG": "C.UTF-8",
                        "LC_ALL": "C.UTF-8",
                    },
                    close_fds=True,
                    pass_fds=pass_fds,
                    start_new_session=True,
                )
            except OSError:
                raise _AsrApiNetworkError("语音 API worker 无法启动") from None
            assert process.stdin is not None and process.stdout is not None
            input_fd = process.stdin.fileno()
            output_fd = process.stdout.fileno()
            os.set_blocking(input_fd, False)
            os.set_blocking(output_fd, False)
            selector = selectors.DefaultSelector()
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            sent = 0
            output = bytearray()
            output_eof = False
            while not output_eof or process.poll() is None:
                if cancelled():
                    raise AsrApiCancelled("语音识别已取消")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _AsrApiNetworkError("语音 API 请求达到总时限")
                events = selector.select(
                    timeout=min(ASR_API_CANCEL_POLL_SECONDS, remaining)
                )
                for key, _mask in events:
                    if key.data == "stdin":
                        try:
                            written = os.write(input_fd, request[sent : sent + 65_536])
                        except OSError as exc:
                            if exc.errno not in (errno.EPIPE, errno.EBADF):
                                raise
                            written = 0
                        sent += written
                        if sent == len(request) or written == 0:
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        block = os.read(
                            output_fd,
                            min(
                                65_536,
                                _ASR_API_MAX_WORKER_RESPONSE_BYTES + 1 - len(output),
                            ),
                        )
                        if block:
                            output.extend(block)
                            if len(output) > _ASR_API_MAX_WORKER_RESPONSE_BYTES:
                                raise AsrApiError("语音 API worker 结果过大")
                        else:
                            selector.unregister(process.stdout)
                            process.stdout.close()
                            output_eof = True
            return_code = process.wait(timeout=PROCESS_GROUP_KILL_GRACE_SECONDS)
            if cancelled():
                raise AsrApiCancelled("语音识别已取消")
            if return_code == _ASR_API_WORKER_DUMPABILITY_EXIT:
                raise AsrApiError("语音 API worker 无法禁止核心转储")
            if return_code == _ASR_API_WORKER_CONFINEMENT_EXIT:
                raise AsrApiError("语音 API worker 无法禁止派生进程")
            if return_code != 0:
                raise _AsrApiNetworkError("语音 API worker 异常退出")
            return _decode_asr_api_worker_response(bytes(output))
        finally:
            cleanup_complete = False
            try:
                _cleanup_asr_api_worker(process, selector)
                cleanup_complete = True
            finally:
                # If termination/reaping cannot be proven, retain the flight
                # lock and fail closed against any overlapping credential use.
                if cleanup_complete:
                    _ASR_API_TRANSPORT_FLIGHT.release()


class AsrApiClient:
    """Single-flight, user-configured and bounded transcription client."""

    def __init__(
        self,
        *,
        transport: object | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if transport is not None and not callable(getattr(transport, "post_wav", None)):
            raise ValueError("语音 API transport must implement post_wav")
        if not callable(monotonic) or not callable(sleep):
            raise ValueError("语音 API clock functions are invalid")
        self._transport = transport
        self._monotonic = monotonic
        self._sleep = sleep

    def _cancelled(self, cancelled: Callable[[], bool]) -> None:
        if cancelled():
            raise AsrApiCancelled("语音识别已取消")

    def _wait(
        self,
        delay: float,
        *,
        deadline: float,
        cancelled: Callable[[], bool],
    ) -> None:
        started = self._monotonic()
        if started + delay > deadline:
            raise AsrApiError("语音 API 请求达到总时限")
        target = started + delay
        while True:
            self._cancelled(cancelled)
            remaining = target - self._monotonic()
            if remaining <= 0:
                return
            self._sleep(min(0.05, remaining))

    def transcribe(
        self,
        wav_path: Path,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> str | None:
        """Upload one complete WAV and return normalized text or explicit None."""

        if not isinstance(wav_path, Path) or (cancelled is not None and not callable(cancelled)):
            raise AsrApiError("语音 API 调用参数无效")
        is_cancelled = cancelled or (lambda: False)
        if not _ASR_API_SINGLE_FLIGHT.acquire(blocking=False):
            raise AsrApiError("已有语音识别正在进行")
        try:
            self._cancelled(is_cancelled)
            wav = read_private_wav(wav_path)
            deadline = self._monotonic() + ASR_API_TOTAL_TIMEOUT_SECONDS
            transport = self._transport or _AsrApiHttpsTransport()
            with _locked_asr_api_credentials(
                cancelled=is_cancelled,
                deadline=deadline,
                monotonic=self._monotonic,
                sleep=self._sleep,
            ) as (credentials, lock_fd):
                for attempt in range(ASR_API_MAX_RETRIES + 1):
                    self._cancelled(is_cancelled)
                    if self._monotonic() >= deadline:
                        raise AsrApiError("语音 API 请求达到总时限")
                    try:
                        response = transport.post_wav(
                            config=credentials,
                            wav=wav,
                            deadline=deadline,
                            cancelled=is_cancelled,
                            credential_lock_fd=lock_fd,
                        )
                    except AsrApiCancelled:
                        raise
                    except _AsrApiNetworkError:
                        self._cancelled(is_cancelled)
                        if attempt >= ASR_API_MAX_RETRIES:
                            raise AsrApiError("语音 API 网络重试已用尽") from None
                        self._wait(
                            ASR_API_RETRY_DELAYS_SECONDS[attempt],
                            deadline=deadline,
                            cancelled=is_cancelled,
                        )
                        continue
                    except (OSError, socket.timeout, ssl.SSLError, http.client.HTTPException):
                        self._cancelled(is_cancelled)
                        if attempt >= ASR_API_MAX_RETRIES:
                            raise AsrApiError("语音 API 网络重试已用尽") from None
                        self._wait(
                            ASR_API_RETRY_DELAYS_SECONDS[attempt],
                            deadline=deadline,
                            cancelled=is_cancelled,
                        )
                        continue
                    except AsrApiError:
                        raise
                    except Exception:
                        raise AsrApiError("语音 API transport 异常") from None

                    self._cancelled(is_cancelled)
                    status, headers, body = _validated_http_response(response)
                    if status == 200:
                        _require_json_content_type(headers)
                        return parse_asr_api_transcript(body)
                    if status in {502, 503} and attempt < ASR_API_MAX_RETRIES:
                        self._wait(
                            _retry_after(headers, ASR_API_RETRY_DELAYS_SECONDS[attempt]),
                            deadline=deadline,
                            cancelled=is_cancelled,
                        )
                        continue
                    if status in {502, 503}:
                        raise AsrApiError("语音 API 服务重试已用尽", status=status)
                    # Service error bodies may echo credentials; never surface them.
                    if status in {400, 401, 403, 404, 413, 422, 429}:
                        raise AsrApiError(
                            f"语音 API 请求被拒绝（HTTP {status}）", status=status
                        )
                    if 300 <= status < 400:
                        raise AsrApiError("语音 API 拒绝跟随重定向", status=status)
                    raise AsrApiError(f"语音 API 返回 HTTP {status}", status=status)


                raise AsrApiError("语音 API 重试状态异常")
        finally:
            _ASR_API_SINGLE_FLIGHT.release()


def _safe_backend_file(
    path: Path,
    *,
    maximum: int,
    executable: bool,
    expected_uid: int,
) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise VoiceArtifactError("local ASR backend artifact is unavailable or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= maximum
            or mode & 0o022
            or (executable and not mode & 0o100)
        ):
            raise VoiceArtifactError("local ASR backend artifact is unsafe")
    finally:
        os.close(descriptor)


def sensevoice_backend_available(
    binary: Path,
    library: Path,
    model: Path,
    tokens: Path,
    *,
    expected_uid: int | None = None,
) -> bool:
    """Return true only for an owned, single-link, bounded local backend."""

    owner = os.getuid() if expected_uid is None else expected_uid
    try:
        _safe_backend_file(
            binary,
            maximum=MAX_BACKEND_BINARY_BYTES,
            executable=True,
            expected_uid=owner,
        )
        _safe_backend_file(
            library,
            maximum=MAX_BACKEND_LIBRARY_BYTES,
            executable=False,
            expected_uid=owner,
        )
        _safe_backend_file(
            model,
            maximum=MAX_MODEL_BYTES,
            executable=False,
            expected_uid=owner,
        )
        _safe_backend_file(
            tokens,
            maximum=MAX_TOKENS_BYTES,
            executable=False,
            expected_uid=owner,
        )
    except VoiceArtifactError:
        return False
    return True


def sensevoice_command(
    binary: Path,
    model: Path,
    tokens: Path,
    wav: Path,
    *,
    threads: int = 4,
) -> list[str]:
    """Build the fixed local SenseVoice argv without a shell or network path."""

    if type(threads) is not int or not 1 <= threads <= 4:
        raise VoiceArtifactError("SenseVoice thread count is out of range")
    return [
        str(binary),
        f"--tokens={tokens}",
        f"--sense-voice-model={model}",
        f"--num-threads={threads}",
        "--sense-voice-language=zh",
        "--sense-voice-use-itn=1",
        "--debug=0",
        str(wav),
    ]


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise VoiceArtifactError("SenseVoice JSON contains duplicate keys")
        value[key] = item
    return value


def parse_sensevoice_transcript(content: bytes) -> str:
    """Extract only a bounded Chinese speech transcript from CLI stdout JSON."""

    if type(content) is not bytes or not 0 < len(content) <= MAX_SENSEVOICE_OUTPUT_BYTES:
        raise VoiceArtifactError("SenseVoice output is empty or oversized")
    try:
        payload = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _item: (_ for _ in ()).throw(
                VoiceArtifactError("SenseVoice JSON contains a non-finite number")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VoiceArtifactError("SenseVoice output is not one UTF-8 JSON object") from exc
    if (
        type(payload) is not dict
        or payload.get("lang") != "<|zh|>"
        or payload.get("event") != "<|Speech|>"
        or type(payload.get("text")) is not str
    ):
        raise VoiceArtifactError("SenseVoice result is not Chinese speech")
    return normalize_transcript(payload["text"])


def run_bounded_command(
    command: Sequence[str],
    *,
    timeout: float,
    maximum: int,
    cancelled: Callable[[], bool] | None = None,
    cwd: Path | str | None = None,
) -> tuple[int, bytes]:
    """Run one cancellable local backend with bounded stdout."""

    if (
        not command
        or isinstance(command, (str, bytes))
        or any(type(argument) is not str or not argument or "\x00" in argument for argument in command)
        or type(timeout) not in (int, float)
        or not 0 < timeout <= 120
        or type(maximum) is not int
        or not 0 < maximum <= MAX_SENSEVOICE_OUTPUT_BYTES
        or (cancelled is not None and not callable(cancelled))
        or (cwd is not None and not isinstance(cwd, (str, os.PathLike)))
    ):
        raise VoiceArtifactError("local ASR command bounds are invalid")
    if cancelled is not None and cancelled():
        raise VoiceArtifactError("local ASR command cancelled")
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=cwd,
        start_new_session=True,
    )
    assert process.stdout is not None
    selector = None
    try:
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        output = bytearray()
        while True:
            if cancelled is not None and cancelled():
                raise VoiceArtifactError("local ASR command cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VoiceArtifactError("local ASR command timed out")
            events = selector.select(timeout=min(0.1, remaining))
            if not events:
                continue
            block = os.read(process.stdout.fileno(), min(65_536, maximum + 1 - len(output)))
            if not block:
                break
            output.extend(block)
            if len(output) > maximum:
                raise VoiceArtifactError("local ASR command output overflowed")
        while process.poll() is None:
            if cancelled is not None and cancelled():
                raise VoiceArtifactError("local ASR command cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VoiceArtifactError("local ASR command timed out")
            try:
                process.wait(timeout=min(0.1, remaining))
            except subprocess.TimeoutExpired:
                continue
        return process.returncode, bytes(output)
    except subprocess.TimeoutExpired as exc:
        raise VoiceArtifactError("local ASR command did not exit") from exc
    finally:
        try:
            if selector is not None:
                selector.close()
            process.stdout.close()
        finally:
            _terminate_process_group(process)


def remove_owned_artifact(path: Path, *, expected_uid: int | None = None) -> bool:
    """Remove only an owned single-link regular file or the symlink itself."""

    owner = os.getuid() if expected_uid is None else expected_uid
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    if metadata.st_uid != owner:
        raise VoiceArtifactError("voice artifact has an unexpected owner")
    if stat.S_ISLNK(metadata.st_mode):
        path.unlink()
        return True
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise VoiceArtifactError("voice artifact has an unsafe type or link count")
    path.unlink()
    return True


if __name__ == "__main__":
    if sys.argv == [sys.argv[0], "--asr_api-https-worker"]:
        raise SystemExit(_asr_api_https_worker_main())
    raise SystemExit(64)

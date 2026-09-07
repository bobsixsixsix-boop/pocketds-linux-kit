#!/usr/bin/env python3
"""Install the complete Pocket DS game/input integration as one transaction.

This installer deliberately has one public operation: install the complete
stack.  It does not expose partial input, launcher, or desktop-file updates.
Every live file is hash-bound to a private transaction manifest before the
mode listener is stopped or the first target is replaced.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
from typing import Iterator, Sequence


SCHEMA = "pocketds.game-input-stack.v2"
COMMIT_SCHEMA = "pocketds.game-input-stack-commit.v1"
LISTENER = "pocketds-mode-listener.service"
CONTROLLER_TEST = "pocketds-controller-test.service"
APP_ID = "cn.xfangfang.wiliwili"
GLFW_GUID = "030000005e040000000b000001000000"
EXPECTED_GLFW_BINDINGS = {
    "a": "b0",
    "b": "b1",
    "x": "b2",
    "y": "b3",
    "back": "b6",
    "start": "b7",
    "guide": "b8",
    "leftshoulder": "b4",
    "rightshoulder": "b5",
    "leftstick": "b9",
    "rightstick": "b10",
    "lefttrigger": "a2",
    "righttrigger": "a5",
    "leftx": "a0",
    "lefty": "a1",
    "rightx": "a3",
    "righty": "a4",
    "dpup": "h0.1",
    "dpright": "h0.2",
    "dpdown": "h0.4",
    "dpleft": "h0.8",
    "platform": "Linux",
}
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
DIRECT_INPUT_MARKERS = (
    b"org.shadowblip.InputPlumber.Manager",
    b"LoadProfilePath",
    b"SetTargetDevices",
)
ROOT_FSYNC_PROGRAM = (
    "import os,sys;"
    "flags=os.O_RDONLY|getattr(os,'O_CLOEXEC',0)|getattr(os,'O_NOFOLLOW',0);"
    "fd=os.open(sys.argv[1],flags) if sys.argv[3]=='file' else -1;"
    "os.fsync(fd) if fd>=0 else None;"
    "os.close(fd) if fd>=0 else None;"
    "pfd=os.open(sys.argv[2],flags|getattr(os,'O_DIRECTORY',0));"
    "os.fsync(pfd);os.close(pfd)"
)


class InstallError(RuntimeError):
    """The transaction was unsafe, incomplete, stale, or could not roll back."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    source: Path | None
    target: Path
    uid: int
    gid: int
    mode: int
    privileged: bool
    phase: str
    preserve_existing: bool = False


@dataclass(frozen=True)
class Snapshot:
    state: str
    content: bytes | None
    sha256: str | None
    size: int | None
    mode: int | None
    uid: int | None
    gid: int | None


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _system_target(system_root: Path, absolute: str) -> Path:
    if not absolute.startswith("/"):
        raise InstallError("system target is not absolute")
    return system_root / absolute.removeprefix("/")


def artifact_plan(
    repo_root: Path,
    home: Path,
    system_root: Path = Path("/"),
    *,
    user_uid: int | None = None,
    user_gid: int | None = None,
    root_uid: int = 0,
    root_gid: int = 0,
) -> list[Artifact]:
    """Return the only supported whole-stack target set, in install order."""

    uid = os.getuid() if user_uid is None else user_uid
    gid = os.getgid() if user_gid is None else user_gid

    def root(
        artifact_id: str,
        source: str,
        target: str,
        mode: int,
        phase: str = "runtime",
    ) -> Artifact:
        return Artifact(
            artifact_id,
            repo_root / source,
            _system_target(system_root, target),
            root_uid,
            root_gid,
            mode,
            True,
            phase,
        )

    def user(
        artifact_id: str,
        source: str | None,
        target: str,
        mode: int,
        phase: str = "runtime",
        preserve_existing: bool = False,
    ) -> Artifact:
        return Artifact(
            artifact_id,
            None if source is None else repo_root / source,
            home / target,
            uid,
            gid,
            mode,
            False,
            phase,
            preserve_existing,
        )

    # Helpers and routing primitives come first.  Desktop files are the final
    # three entries, so no menu can publish a launcher from a partial generation.
    return [
        root(
            "controller-test-gate",
            "components/inputplumber/controller_test_gate.py",
            "/usr/local/lib/pocketds/controller_test_gate.py",
            0o644,
        ),
        root(
            "controller-test-helper",
            "components/controller-test/pocketds-controller-test.py",
            "/usr/local/libexec/pocketds-controller-test.py",
            0o755,
        ),
        root(
            "controller-test-service",
            "components/controller-test/pocketds-controller-test.service",
            "/etc/systemd/system/pocketds-controller-test.service",
            0o644,
        ),
        root(
            "input-mode-canonical",
            "components/emulation/pocketds-input-mode",
            "/usr/local/libexec/pocketds-input-mode",
            0o755,
        ),
        user(
            "input-mode-user-wrapper",
            "components/emulation/pocketds-input-mode-user",
            ".local/libexec/pocketds-input-mode",
            0o755,
        ),
        root(
            "input-toggle-root",
            "components/inputplumber/pocketds-toggle-joymouse",
            "/usr/bin/pocketds-toggle-joymouse",
            0o755,
        ),
        root(
            "mode-listener",
            "components/inputplumber/pocketds-mode-listener.py",
            "/usr/bin/pocketds-mode-listener",
            0o755,
        ),
        root(
            "input-held-modifiers",
            "scripts/pocketds-input-held-modifiers.py",
            "/usr/local/libexec/pocketds-input-held-modifiers",
            0o755,
        ),
        root(
            "input-capability-map",
            "components/inputplumber/ayaneo_mcu_xbox.yaml",
            "/usr/share/inputplumber/capability_maps/ayaneo_mcu_xbox.yaml",
            0o644,
        ),
        root(
            "input-profile-gamepad",
            "components/inputplumber/pocketds-gamepad.yaml",
            "/usr/share/inputplumber/profiles/pocketds-gamepad.yaml",
            0o644,
        ),
        root(
            "input-profile-joymouse",
            "components/inputplumber/pocketds-joymouse.yaml",
            "/usr/share/inputplumber/profiles/pocketds-joymouse.yaml",
            0o644,
        ),
        root(
            "input-systemd-dropin",
            "components/inputplumber/10-pocketds-manage-all-devices.conf",
            "/etc/systemd/system/inputplumber.service.d/10-pocketds-manage-all-devices.conf",
            0o644,
        ),
        root(
            "mode-listener-state-dropin",
            "components/inputplumber/20-pocketds-mode-listener-input-state.conf",
            "/etc/systemd/system/pocketds-mode-listener.service.d/20-input-state.conf",
            0o644,
        ),
        root(
            "input-tmpfiles",
            "components/inputplumber/pocketds-input-mode.conf",
            "/usr/lib/tmpfiles.d/pocketds-input-mode.conf",
            0o644,
        ),
        root(
            "game-session-supervisor",
            "components/emulation/pocketds-game-session.py",
            "/usr/local/libexec/pocketds-game-session",
            0o755,
        ),
        root(
            "gamescope-observer",
            "components/game-runtime/pocketds-gamescope-observer",
            "/usr/local/libexec/pocketds-gamescope-observer",
            0o755,
        ),
        user(
            "gamescope-inner",
            "components/game-runtime/pocketds-gamescope-inner",
            ".local/libexec/pocketds-gamescope-inner",
            0o755,
        ),
        user(
            "game-runtime",
            "components/game-runtime/pocketds-game-runtime",
            ".local/bin/pocketds-game-runtime",
            0o755,
        ),
        user(
            "game-limit",
            "components/game-runtime/pocketds-game-limit",
            ".local/bin/pocketds-game-limit",
            0o755,
        ),
        user(
            "gamescope-kwin-script",
            "components/game-runtime/kwin-gamescope-top-screen.js",
            ".local/share/pocketds-linux-kit/game-runtime/kwin-gamescope-top-screen.js",
            0o644,
        ),
        user(
            "wiliwili-mapping-tool",
            "scripts/pocketds-wiliwili-gamecontrollerdb.py",
            ".local/libexec/pocketds-wiliwili-gamecontrollerdb",
            0o755,
        ),
        user(
            "wiliwili-mapping-source",
            "components/wiliwili/gamecontrollerdb.txt",
            ".local/share/pocketds-linux-kit/wiliwili/gamecontrollerdb.txt",
            0o644,
        ),
        user(
            "esde-prepare",
            "components/emulation/pocketds-es-de-prepare.py",
            ".local/libexec/pocketds-es-de-prepare",
            0o755,
        ),
        user(
            "melonds-prepare",
            "components/emulation/pocketds-melonds-prepare.py",
            ".local/libexec/pocketds-melonds-prepare",
            0o755,
        ),
        user(
            "melonds-fullscreen",
            "components/emulation/pocketds-melonds-fullscreen.py",
            ".local/libexec/pocketds-melonds-fullscreen",
            0o755,
        ),
        user(
            "melonds-kwin-script",
            "components/emulation/kwin-melonds-dualscreen.js",
            ".local/share/pocketds-linux-kit/emulation/kwin-melonds-dualscreen.js",
            0o644,
        ),
        user(
            "retroarch-kwin-script",
            "components/emulation/kwin-retroarch-top-screen.js",
            ".local/share/pocketds-linux-kit/emulation/kwin-retroarch-top-screen.js",
            0o644,
        ),
        user(
            "retroarch-managed-config",
            "components/emulation/retroarch-pocketds.cfg",
            ".config/retroarch/pocketds.cfg",
            0o644,
            "configuration",
        ),
        user(
            "ppsspp-core-options",
            "components/emulation/PPSSPP.opt",
            ".config/retroarch/config/PPSSPP/PPSSPP.opt",
            0o644,
            "configuration",
        ),
        user(
            "wiliwili-live-database",
            None,
            ".var/app/cn.xfangfang.wiliwili/config/wiliwili/gamecontrollerdb.txt",
            0o644,
            "configuration",
        ),
        user(
            "esde-systems",
            "components/emulation/es_systems.xml",
            ".local/share/pocketds-linux-kit/emulation/es_systems.xml",
            0o644,
            "configuration",
        ),
        user(
            "esde-shared-systems",
            "components/emulation/es_systems-shared.xml",
            ".local/share/pocketds-linux-kit/emulation/es_systems-shared.xml",
            0o644,
            "configuration",
        ),
        user(
            "esde-settings",
            "components/emulation/es_settings.xml",
            ".local/share/pocketds-linux-kit/emulation/es_settings.xml",
            0o644,
            "configuration",
        ),
        user(
            "retroarch-config",
            "components/emulation/retroarch.cfg",
            ".config/retroarch/retroarch.cfg",
            0o644,
            "configuration",
            True,
        ),
        user(
            "wiliwili-launcher",
            "components/wiliwili/pocketds-wiliwili",
            ".local/bin/pocketds-wiliwili",
            0o755,
            "launcher",
        ),
        user(
            "esde-launcher",
            "components/emulation/pocketds-es-de",
            ".local/bin/pocketds-es-de",
            0o755,
            "launcher",
        ),
        user(
            "retroarch-launcher",
            "components/emulation/pocketds-retroarch",
            ".local/bin/pocketds-retroarch",
            0o755,
            "launcher",
        ),
        user(
            "melonds-launcher",
            "components/emulation/pocketds-melonds",
            ".local/bin/pocketds-melonds",
            0o755,
            "launcher",
        ),
        user(
            "steam-session-launcher",
            "components/steam/pocketds-steam-session",
            ".local/bin/pocketds-steam-session",
            0o755,
            "launcher",
        ),
        user(
            "steam-desktop-selector",
            "components/steam/steamos-session-select",
            ".local/bin/steamos-session-select",
            0o755,
            "launcher",
        ),
        user(
            "steam-unified-desktop",
            "components/steam/Steam.desktop",
            ".local/share/applications/Steam.desktop",
            0o755,
            "desktop",
        ),
        user(
            "wiliwili-desktop",
            "components/wiliwili/cn.xfangfang.wiliwili.desktop",
            ".local/share/applications/cn.xfangfang.wiliwili.desktop",
            0o644,
            "desktop",
        ),
        user(
            "esde-desktop",
            "components/emulation/ES-DE.desktop",
            ".local/share/applications/ES-DE.desktop",
            0o644,
            "desktop",
        ),
    ]


class HostOperations:
    """Bounded, shell-free host mutations. Tests inject a filesystem fake."""

    def __init__(self, *, uid: int, runtime_dir: Path) -> None:
        self.uid = uid
        self.runtime_dir = runtime_dir

    @staticmethod
    def _run(
        argv: tuple[str, ...],
        *,
        accepted: tuple[int, ...] = (0,),
        timeout: float = 20,
        capture: bool = False,
    ) -> CommandResult:
        if not argv or any(type(item) is not str or "\0" in item for item in argv):
            raise InstallError("command argv is invalid")
        process: subprocess.Popen[bytes] | None = None

        def terminate_group() -> tuple[bytes, bytes]:
            assert process is not None
            captured_stdout = b""
            captured_stderr = b""

            def group_exists() -> bool:
                try:
                    os.killpg(process.pid, 0)
                    return True
                except ProcessLookupError:
                    return False
                except PermissionError:
                    return True

            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                partial_stdout, partial_stderr = process.communicate(timeout=0.5)
                captured_stdout = partial_stdout or b""
                captured_stderr = partial_stderr or b""
            except subprocess.TimeoutExpired:
                pass

            # Reaping the group leader is not evidence that its descendants
            # exited: a TERM-ignoring grandchild may have closed all inherited
            # stdio. Probe the private PG explicitly and always escalate it.
            if group_exists():
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

            deadline = time.monotonic() + 2.0
            while group_exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            if group_exists():
                raise InstallError("bounded command process group did not terminate")

            if process.poll() is None:
                try:
                    final_stdout, final_stderr = process.communicate(timeout=0.5)
                    captured_stdout = final_stdout or captured_stdout
                    captured_stderr = final_stderr or captured_stderr
                except subprocess.TimeoutExpired:
                    # A process outside our private PG may have deliberately
                    # inherited a pipe. Close our ends and still reap the exact
                    # leader without allowing cleanup to block forever.
                    for stream in (process.stdout, process.stderr):
                        if stream is not None:
                            stream.close()
                    try:
                        process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        try:
                            process.wait(timeout=0.5)
                        except subprocess.TimeoutExpired as exc:
                            raise InstallError(
                                "bounded command leader could not be reaped"
                            ) from exc
            return captured_stdout, captured_stderr

        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "LC_ALL": "C",
                    "HOME": pwd.getpwuid(os.getuid()).pw_dir,
                    "XDG_RUNTIME_DIR": str(Path("/run/user") / str(os.getuid())),
                    "DBUS_SESSION_BUS_ADDRESS": (
                        f"unix:path=/run/user/{os.getuid()}/bus"
                    ),
                },
            )
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = terminate_group()
            raise InstallError(f"bounded command timed out: {argv[0]}") from exc
        except OSError as exc:
            if process is not None:
                terminate_group()
            raise InstallError(f"bounded command unavailable: {argv[0]}") from exc
        except BaseException:
            if process is not None:
                with _cleanup_signal_guard():
                    terminate_group()
            raise
        assert process is not None and process.returncode is not None
        stdout = stdout or b""
        stderr = stderr or b""
        if process.returncode not in accepted:
            raise InstallError(f"command failed ({process.returncode}): {argv[0]}")
        if len(stdout) > 128 * 1024 or len(stderr) > 128 * 1024:
            raise InstallError("command output exceeded the accepted bound")
        return CommandResult(process.returncode, stdout)

    def require_sudo(self) -> None:
        self._run(("/usr/bin/sudo", "-n", "/usr/bin/true"))

    def assert_quiescent(self) -> None:
        flatpaks = self._run(
            ("/usr/bin/flatpak", "ps", "--columns=application"), capture=True
        ).stdout.decode("utf-8", errors="strict").splitlines()
        if APP_ID in {line.strip() for line in flatpaks}:
            raise InstallError("Wiliwili is running")
        for name in ("wiliwili", "ES-DE", "retroarch", "melonDS", "steam"):
            result = self._run(
                ("/usr/bin/pgrep", "-u", str(self.uid), "-x", name),
                accepted=(0, 1),
            )
            if result.returncode == 0:
                raise InstallError(f"managed game process is running: {name}")
        for pattern in (
            r"(^|/)pocketds-wiliwili([[:space:]]|$)",
            r"(^|/)pocketds-es-de([[:space:]]|$)",
            r"(^|/)pocketds-retroarch([[:space:]]|$)",
            r"(^|/)pocketds-melonds([[:space:]]|$)",
            r"(^|/)pocketds-steam-session([[:space:]]|$)",
        ):
            result = self._run(
                ("/usr/bin/pgrep", "-u", str(self.uid), "-f", pattern),
                accepted=(0, 1),
            )
            if result.returncode == 0:
                raise InstallError("managed launcher process is running")

    def listener_state(self) -> str:
        result = self._run(
            (
                "/usr/bin/sudo",
                "-n",
                "/usr/bin/systemctl",
                "is-active",
                LISTENER,
            ),
            accepted=(0, 3),
            capture=True,
        )
        state = result.stdout.decode("ascii", errors="strict").strip()
        if state not in {"active", "inactive"}:
            raise InstallError("mode-listener state is unsupported")
        return state

    def stop_listener(self) -> None:
        self._run(
            ("/usr/bin/sudo", "-n", "/usr/bin/systemctl", "stop", LISTENER)
        )
        if self.listener_state() != "inactive":
            raise InstallError("mode listener did not stop")

    def start_listener(self) -> None:
        self._run(
            ("/usr/bin/sudo", "-n", "/usr/bin/systemctl", "start", LISTENER)
        )
        if self.listener_state() != "active":
            raise InstallError("mode listener did not resume")

    def controller_test_state(self) -> str:
        result = self._run(
            ("/usr/bin/sudo", "-n", "/usr/bin/systemctl", "is-active", CONTROLLER_TEST),
            accepted=(0, 3, 4),
            capture=True,
        )
        state = result.stdout.decode("ascii", errors="strict").strip()
        if state not in {"active", "inactive"}:
            raise InstallError("controller-test service requires recovery before install")
        return state

    def stop_controller_test(self) -> None:
        # ExecStopPost restores only after controls are neutral. Failed
        # recovery must stop the transaction before any helper is replaced.
        self._run(
            ("/usr/bin/sudo", "-n", "/usr/bin/systemctl", "stop", CONTROLLER_TEST),
            timeout=15,
        )
        if self.controller_test_state() != "inactive":
            raise InstallError("controller-test service did not restore and stop")

    def start_controller_test(self) -> None:
        self._run(
            ("/usr/bin/sudo", "-n", "/usr/bin/systemctl", "start", CONTROLLER_TEST),
            timeout=15,
        )
        if self.controller_test_state() != "active":
            raise InstallError("controller-test service did not become ready")

    def ensure_root_directory(self, directory: Path) -> None:
        self._run(
            (
                "/usr/bin/sudo",
                "-n",
                "/usr/bin/install",
                "-d",
                "-o",
                "0",
                "-g",
                "0",
                "-m",
                "0755",
                "--",
                str(directory),
            )
        )

    def replace_root(
        self,
        source: Path,
        target: Path,
        *,
        uid: int,
        gid: int,
        mode: int,
        token: str,
        expected_sha256: str,
        expected_bytes: int,
    ) -> None:
        temporary = target.parent / f".{target.name}.pds-{token}.new"
        created = False
        try:
            self._run(
                ("/usr/bin/sudo", "-n", "/usr/bin/test", "!", "-e", str(temporary))
            )
            self._run(
                ("/usr/bin/sudo", "-n", "/usr/bin/test", "!", "-L", str(temporary))
            )
            self._run(
                (
                    "/usr/bin/sudo",
                    "-n",
                    "/usr/bin/install",
                    "-o",
                    str(uid),
                    "-g",
                    str(gid),
                    "-m",
                    f"{mode:04o}",
                    "--",
                    str(source),
                    str(temporary),
                )
            )
            created = True
            staged, staged_metadata = _read_regular(
                temporary,
                expected_uid=uid,
                expected_mode=mode,
                allow_empty=True,
            )
            if (
                len(staged) != expected_bytes
                or _sha256(staged) != expected_sha256
                or staged_metadata.st_gid != gid
            ):
                raise InstallError("privileged same-filesystem stage did not verify")
            self._fsync_root(temporary, target.parent, include_file=True)
            self._run(
                (
                    "/usr/bin/sudo",
                    "-n",
                    "/usr/bin/mv",
                    "-f",
                    "-T",
                    "--",
                    str(temporary),
                    str(target),
                )
            )
            created = False
            self._fsync_root(target, target.parent, include_file=True)
        finally:
            if created:
                try:
                    self._run(
                        (
                            "/usr/bin/sudo",
                            "-n",
                            "/usr/bin/rm",
                            "-f",
                            "--",
                            str(temporary),
                        ),
                        accepted=(0, 1),
                    )
                except InstallError:
                    pass

    def remove_root(self, target: Path) -> None:
        self._run(
            ("/usr/bin/sudo", "-n", "/usr/bin/rm", "-f", "--", str(target))
        )
        self._fsync_root(target, target.parent, include_file=False)

    def _fsync_root(
        self, target: Path, parent: Path, *, include_file: bool
    ) -> None:
        """Durably publish the exact root file and its directory entry."""

        self._run(
            (
                "/usr/bin/sudo",
                "-n",
                "/usr/bin/python3",
                "-c",
                ROOT_FSYNC_PROGRAM,
                str(target),
                str(parent),
                "file" if include_file else "parent",
            )
        )

    def daemon_reload(self) -> None:
        # A daemon-reload does not restart InputPlumber.
        self._run(("/usr/bin/sudo", "-n", "/usr/bin/systemctl", "daemon-reload"))

    def create_tmpfiles(self, config: Path) -> None:
        self._run(
            (
                "/usr/bin/sudo",
                "-n",
                "/usr/bin/systemd-tmpfiles",
                "--create",
                str(config),
            )
        )

    def bootstrap(self, canonical_helper: Path) -> None:
        # Bootstrap is idempotent: it establishes the exact fresh default but
        # preserves an already stable joymouse/gamepad generation.
        self._run((str(canonical_helper), "bootstrap"))

    def update_desktop_database(self, applications: Path) -> None:
        self._run(("/usr/bin/update-desktop-database", str(applications)))

    def before_apply(self, _transaction: Path) -> None:
        """Test-only fault/tamper hook."""

    def after_validate(self, _transaction: Path) -> None:
        """Test-only race/fault hook after immutable bytes enter memory."""

    def after_replace(self, _artifact: Artifact) -> None:
        """Test-only post-rename fault hook."""


def _path_is_within(path: Path, anchor: Path) -> bool:
    try:
        path.relative_to(anchor)
    except ValueError:
        return False
    return True


def _validate_directory_chain(
    anchor: Path,
    leaf: Path,
    *,
    expected_uid: int,
    privileged: bool,
    allow_missing: bool = True,
) -> None:
    if not anchor.is_absolute() or not leaf.is_absolute() or not _path_is_within(leaf, anchor):
        raise InstallError("path escaped its deployment root")
    try:
        anchor_metadata = anchor.lstat()
    except OSError as exc:
        raise InstallError("deployment root is unavailable") from exc
    if not stat.S_ISDIR(anchor_metadata.st_mode) or stat.S_ISLNK(anchor_metadata.st_mode):
        raise InstallError("deployment root is linked or not a directory")
    if anchor_metadata.st_uid != expected_uid or stat.S_IMODE(anchor_metadata.st_mode) & 0o022:
        raise InstallError("deployment root owner or mode is unsafe")
    relative = leaf.relative_to(anchor)
    current = anchor
    missing_seen = False
    for part in relative.parts:
        if part in {"", ".", ".."}:
            raise InstallError("deployment path component is invalid")
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            missing_seen = True
            if not allow_missing:
                raise InstallError("required deployment directory is missing") from None
            continue
        except OSError as exc:
            raise InstallError("deployment directory is unavailable") from exc
        if missing_seen:
            raise InstallError("deployment path changed while validating")
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise InstallError("deployment path contains a symlink or non-directory")
        if metadata.st_uid != expected_uid:
            raise InstallError("deployment directory owner is unexpected")
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise InstallError("deployment directory is group/world writable")
        if privileged and metadata.st_uid != expected_uid:
            raise InstallError("privileged deployment directory is not root-owned")


def _read_regular(
    path: Path,
    *,
    expected_uid: int | None,
    expected_mode: int | None = None,
    allow_empty: bool = True,
) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise InstallError("file is unavailable or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (expected_uid is not None and metadata.st_uid != expected_uid)
            or (expected_mode is not None and mode != expected_mode)
            or metadata.st_size > MAX_FILE_BYTES
            or (not allow_empty and metadata.st_size == 0)
        ):
            raise InstallError("file owner, type, links, size, or mode is unsafe")
        content = bytearray()
        remaining = metadata.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise InstallError("file changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise InstallError("file grew while reading")
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
        ):
            raise InstallError("file identity changed while reading")
        return bytes(content), metadata
    finally:
        os.close(descriptor)


def _snapshot_target(artifact: Artifact) -> Snapshot:
    try:
        artifact.target.lstat()
    except FileNotFoundError:
        return Snapshot("MISSING", None, None, None, None, None, None)
    except OSError as exc:
        raise InstallError("target is unavailable") from exc
    content, metadata = _read_regular(
        artifact.target, expected_uid=artifact.uid, allow_empty=True
    )
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise InstallError("target file is group/world writable")
    return Snapshot(
        "PRESENT",
        content,
        _sha256(content),
        len(content),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
    )


def _snapshot_fields(snapshot: Snapshot) -> dict[str, object]:
    return {
        "state": snapshot.state,
        "sha256": snapshot.sha256,
        "bytes": snapshot.size,
        "mode": None if snapshot.mode is None else f"{snapshot.mode:04o}",
        "uid": snapshot.uid,
        "gid": snapshot.gid,
    }


def _source_payloads(artifacts: list[Artifact], repo_root: Path, uid: int) -> dict[str, bytes]:
    _validate_directory_chain(
        repo_root, repo_root, expected_uid=uid, privileged=False, allow_missing=False
    )
    payloads: dict[str, bytes] = {}
    for artifact in artifacts:
        if artifact.source is None:
            continue
        if not _path_is_within(artifact.source, repo_root):
            raise InstallError("source escaped the repository")
        _validate_directory_chain(
            repo_root,
            artifact.source.parent,
            expected_uid=uid,
            privileged=False,
            allow_missing=False,
        )
        content, _metadata = _read_regular(
            artifact.source,
            expected_uid=uid,
            expected_mode=artifact.mode,
            allow_empty=False,
        )
        payloads[artifact.artifact_id] = content
    return payloads


def _mapping_key(line: str) -> tuple[str, str | None] | None:
    fields = line.strip().split(",")
    if not fields or fields[0] != GLFW_GUID:
        return None
    platform = None
    for field in fields[2:]:
        if field.startswith("platform:"):
            platform = field.split(":", 1)[1]
            break
    return fields[0], platform


def _validate_mapping(mapping_source: bytes) -> str:
    try:
        lines = [
            line.strip()
            for line in mapping_source.decode("utf-8", errors="strict").splitlines()
            if line.strip()
        ]
    except UnicodeDecodeError:
        raise InstallError("Wiliwili mapping source is not UTF-8") from None
    if len(lines) != 1 or not lines[0].startswith(f"{GLFW_GUID},"):
        raise InstallError("Wiliwili mapping source identity is invalid")
    if "platform:Linux," not in lines[0] or not lines[0].endswith(","):
        raise InstallError("Wiliwili mapping is not an explicit Linux row")
    fields = lines[0].split(",")
    if len(fields) < 4 or fields[-1] != "" or not fields[1]:
        raise InstallError("Wiliwili mapping row structure is invalid")
    bindings: dict[str, str] = {}
    for field in fields[2:-1]:
        if field.count(":") != 1:
            raise InstallError("Wiliwili mapping field is invalid")
        key, value = field.split(":", 1)
        if not key or not value or key in bindings:
            raise InstallError("Wiliwili mapping binding is empty or duplicated")
        bindings[key] = value
    if bindings != EXPECTED_GLFW_BINDINGS:
        raise InstallError("Wiliwili mapping bindings are not the exact Pocket DS map")
    return lines[0]


def _merge_mapping(mapping_source: bytes, existing: bytes | None) -> bytes:
    mapping = _validate_mapping(mapping_source)
    try:
        old_lines = (
            []
            if existing is None
            else existing.decode("utf-8", errors="strict").splitlines()
        )
    except UnicodeDecodeError:
        raise InstallError("existing Wiliwili controller database is not UTF-8") from None
    output: list[str] = []
    inserted = False
    for line in old_lines:
        key = _mapping_key(line)
        if key is not None and key[1] in {None, "Linux"}:
            if not inserted:
                output.append(mapping)
                inserted = True
            continue
        output.append(line)
    if not inserted:
        output.append(mapping)
    merged = ("\n".join(output).rstrip("\n") + "\n").encode("utf-8")
    applicable = [
        line for line in merged.decode("utf-8").splitlines() if _mapping_key(line) is not None and _mapping_key(line)[1] in {None, "Linux"}
    ]
    if applicable != [mapping]:
        raise InstallError("merged Wiliwili database did not de-duplicate the GUID")
    return merged


def _validate_api_invariants(payloads: dict[str, bytes]) -> None:
    required = {
        "controller-test-gate",
        "controller-test-helper",
        "controller-test-service",
        "input-mode-canonical",
        "input-mode-user-wrapper",
        "input-toggle-root",
        "game-session-supervisor",
        "gamescope-observer",
        "gamescope-inner",
        "game-runtime",
        "game-limit",
        "gamescope-kwin-script",
        "steam-session-launcher",
        "steam-desktop-selector",
        "steam-unified-desktop",
        "wiliwili-launcher",
        "esde-launcher",
        "retroarch-kwin-script",
        "retroarch-managed-config",
        "ppsspp-core-options",
        "retroarch-launcher",
        "wiliwili-desktop",
        "esde-desktop",
        "wiliwili-mapping-source",
    }
    if not required.issubset(payloads):
        raise InstallError("API invariant payload set is incomplete")
    if (
        b"pds-controller-test-v1" not in payloads["controller-test-gate"]
        or b"hardware_actions_blocked" not in payloads["controller-test-gate"]
        or b"pds-controller-test-v1" not in payloads["controller-test-helper"]
        or b"--recover" not in payloads["controller-test-service"]
        or b"ExecStopPost=" not in payloads["controller-test-service"]
        or b"Type=notify" not in payloads["controller-test-service"]
        or b"RuntimeDirectory=pocketds-controller-test"
        not in payloads["controller-test-service"]
    ):
        raise InstallError("controller-test gate or crash recovery contract is incomplete")
    executable_ids = {
        "input-mode-canonical",
        "input-mode-user-wrapper",
        "input-toggle-root",
        "input-held-modifiers",
        "game-session-supervisor",
        "gamescope-observer",
        "gamescope-inner",
        "game-runtime",
        "game-limit",
        "steam-session-launcher",
        "steam-desktop-selector",
        "wiliwili-mapping-tool",
        "esde-prepare",
        "wiliwili-launcher",
        "esde-launcher",
        "retroarch-launcher",
    }
    writers = {
        artifact_id
        for artifact_id, content in payloads.items()
        if artifact_id in executable_ids
        and any(marker in content for marker in DIRECT_INPUT_MARKERS)
    }
    if writers != {"input-mode-canonical"}:
        raise InstallError("canonical input helper is not the unique D-Bus writer")
    canonical = b"/usr/local/libexec/pocketds-input-mode"
    canonical_helper = payloads["input-mode-canonical"]
    if b"bootstrap" not in canonical_helper or b"recover-token" not in canonical_helper:
        raise InstallError(
            "canonical input helper lacks bootstrap or retryable recovery"
        )
    for artifact_id in ("input-mode-user-wrapper", "input-toggle-root"):
        if canonical not in payloads[artifact_id]:
            raise InstallError("input compatibility wrapper bypasses the canonical helper")
    supervisor = payloads["game-session-supervisor"]
    if b"acquire" not in supervisor or b"recover-token" not in supervisor:
        raise InstallError("game-session supervisor lacks tokenized input ownership")
    if b'"steam"' not in supervisor or b"probe_native_session" not in supervisor:
        raise InstallError("game-session supervisor lacks managed Steam probing")
    if not payloads["gamescope-observer"].startswith(b"\x7fELF"):
        raise InstallError("Gamescope observer is not a native executable")
    runtime = payloads["game-runtime"]
    for marker in (
        b"/usr/bin/gamescope",
        b"pocketds-gamescope-inner",
        b"kwin-gamescope-top-screen.js",
        b"--force-windows-fullscreen",
        b"-W 1920 -H 1080",
        b"currentModeId",
        b'-r "$output_refresh"',
    ):
        if marker not in runtime:
            raise InstallError("Gamescope runtime contract is incomplete")
    gamescope_inner = payloads["gamescope-inner"]
    if (
        b"/usr/local/libexec/pocketds-gamescope-observer" not in gamescope_inner
        or b"mangoapp_use_output_timing" not in gamescope_inner
        or b"POCKETDS_GAMESCOPE_PARENT_PID" not in gamescope_inner
        or b"WAYLAND_DISPLAY=/nonexistent/pocketds-gamescope-wayland"
        not in gamescope_inner
    ):
        raise InstallError("Gamescope inner launcher bypasses the observer")
    if (
        b"pocketds-gamescope-limit" not in payloads["game-limit"]
        or b"game-fps-limit" not in payloads["game-limit"]
    ):
        raise InstallError("Gamescope limiter does not own the runtime control")
    gamescope_kwin = payloads["gamescope-kwin-script"]
    if b'"DSI-1"' not in gamescope_kwin or b'"gamescope"' not in gamescope_kwin:
        raise InstallError("Gamescope KWin placement contract is incomplete")
    steam_launcher = payloads["steam-session-launcher"]
    normalized_steam_launcher = b" ".join(
        steam_launcher.replace(b"\\\n", b" ").split()
    )
    for marker in (
        b"/usr/local/libexec/pocketds-game-session",
        b"pocketds-game-runtime",
        b"probe --name steam",
        b"run --backend native --name steam --",
        b'-u "$uid" -x steam',
        b'exec "$steam_client" -ifrunning "$@"',
        b'exec env STEAMDECK_MODE=true "$supervisor" run',
        b"--pocketds-gamepad-restart",
        b"--unit=pocketds-steam-gamepad",
        b"--property=SuccessExitStatus=143",
        b"pocketds-steam-desktop.service",
    ):
        if marker not in normalized_steam_launcher:
            raise InstallError("Steam launcher bypasses managed session routing")
    if b"--performance" in normalized_steam_launcher:
        raise InstallError("Steam launcher must preserve the selected power mode")
    steam_selector = payloads["steam-desktop-selector"]
    normalized_steam_selector = b" ".join(
        steam_selector.replace(b"\\\n", b" ").split()
    )
    for marker in (
        b"/usr/local/libexec/pocketds-game-session",
        b"/usr/bin/steamos-session-select",
        b"probe --name steam",
        b'exec "$supervisor" stop',
    ):
        if marker not in normalized_steam_selector:
            raise InstallError("Steam desktop selector bypasses managed cleanup")
    for forbidden in (b"terminate-user", b"systemctl --user exit", b"org.kde.Shutdown"):
        if forbidden in steam_selector:
            raise InstallError("Steam desktop selector must not terminate KDE")
    steam_desktop = payloads["steam-unified-desktop"]
    steam_desktop_lines = steam_desktop.splitlines()
    if (
        steam_desktop.count(b"pocketds-steam-session") != 3
        or b"TryExec=/home/pocketds/.local/bin/pocketds-steam-session"
        not in steam_desktop_lines
        or b"Exec=env STEAMDECK_MODE=true /home/pocketds/.local/bin/pocketds-steam-session %U"
        not in steam_desktop_lines
        or b"pocketds-game-runtime" in steam_desktop
    ):
        raise InstallError("Steam desktop bypasses its managed launcher")
    canonical_supervisor = b"/usr/local/libexec/pocketds-game-session"
    capability_map = payloads.get("input-capability-map", b"")
    for marker in (
        b"Physical equals key \xe2\x86\x92 Steam/Xbox Guide",
        b"event_code: BTN6",
        b"button: Guide",
        b"AYA logo \xe2\x86\x92 internal profile slot",
        b"event_code: BTN5",
        b"button: Keyboard",
        b"MCU BTN_MODE (scan 0x9000d) \xe2\x86\x92 Brightness Up",
        b"event_code: BTN_MODE",
        b"button: QuickAccess",
        b"MCU BTN_2 (scan 0x90003) \xe2\x86\x92 Brightness Down",
        b"event_code: BTN2",
        b"button: QuickAccess2",
        b"MCU BTN_3 \xe2\x86\x92 RightPaddle2",
        b"MCU BTN_4 \xe2\x86\x92 LeftPaddle2",
    ):
        if marker not in capability_map:
            raise InstallError("Pocket DS AYA/'=' capability map is incomplete")
    if b"event_code: BTN7" in capability_map:
        raise InstallError("Unverified BTN7 must not alias the physical '=' key")
    if b"keyboard: KeyLeftMeta" in capability_map:
        raise InstallError("AYA must not be mapped to Super in the capability map")
    listener_dropin = payloads.get("mode-listener-state-dropin", b"")
    listener_lines = listener_dropin.splitlines()
    if (
        b"After=systemd-tmpfiles-setup.service inputplumber.service"
        not in listener_lines
        or b"Requires=systemd-tmpfiles-setup.service" not in listener_lines
        or [line for line in listener_lines if line.startswith(b"ReadWritePaths=")]
        != [b"ReadWritePaths=/run/pocketds-input-mode"]
        or b"ExecStartPre=/usr/local/libexec/pocketds-input-mode bootstrap"
        not in listener_lines
        or b"Requires=pocketds-controller-test.service" not in listener_lines
        or b"After=pocketds-controller-test.service" not in listener_lines
    ):
        raise InstallError("mode-listener sandbox lacks the canonical state exception")
    tmpfiles = payloads.get("input-tmpfiles", b"")
    if (
        b"f /run/pocketds-input-mode.lock 0640 root pocketds -" not in tmpfiles
        or b"d /run/pocketds-input-mode 2770 root pocketds -" not in tmpfiles
    ):
        raise InstallError("input runtime ownership/mode invariant is invalid")
    launcher_contracts = {
        "wiliwili-launcher": (
            b"--backend flatpak",
            b"--name wiliwili",
            b"--app-id",
            APP_ID.encode("ascii"),
            b'-- "$@"',
        ),
        "esde-launcher": (
            b"run --backend native --name es-de --",
            b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1",
            b"POCKETDS_ESDE_SHARED_SYSTEMS",
            b"--esde-home",
            b"--force-managed-systems",
            b"--home",
        ),
        "retroarch-launcher": (
            b"join --token",
            b"run --backend native --name retroarch --performance",
            b"pocketds-game-runtime",
            b"--appendconfig",
        ),
        "melonds-launcher": (
            b"join --token",
            b"run --backend native --name melonds --performance",
            b"net.kuribo64.melonDS",
            b"kwin-melonds-dualscreen.js",
            b'--filesystem="$shared_root:ro"',
            b'--save-dir "$save_dir" --state-dir "$state_dir"',
        ),
    }
    for artifact_id, contract in launcher_contracts.items():
        launcher = payloads[artifact_id]
        if canonical_supervisor not in launcher:
            raise InstallError("a game launcher bypasses the common supervisor")
        if any(
            legacy in launcher
            for legacy in (
                b"pocketds-game-session.lock",
                b"restore-if-current",
                b"POCKETDS_GAME_SESSION_OWNER_PID",
            )
        ):
            raise InstallError("a launcher still contains legacy session ownership")
        normalized = b" ".join(launcher.replace(b"\\\n", b" ").split())
        if any(token not in normalized for token in contract):
            raise InstallError("a launcher does not match the supervisor CLI contract")
        if artifact_id == "retroarch-launcher" and normalized.count(b"--performance") != 1:
            raise InstallError(
                "RetroArch performance mode must be exclusive to standalone sessions"
            )
    retroarch_managed = payloads["retroarch-managed-config"].splitlines()
    for required_line in (
        b'aspect_ratio_index = "21"',
        b'video_fullscreen = "true"',
        b'video_windowed_fullscreen = "true"',
        b'input_quit_gamepad_combo = "0"',
        b'quit_press_twice = "true"',
    ):
        if required_line not in retroarch_managed:
            raise InstallError("managed RetroArch display/exit policy is incomplete")
    if b'ppsspp_internal_resolution = "1440x816"' not in payloads[
        "ppsspp-core-options"
    ].splitlines():
        raise InstallError("PPSSPP must default to the Pocket DS 3x rendering resolution")
    retroarch_kwin = payloads["retroarch-kwin-script"]
    if any(
        marker not in retroarch_kwin
        for marker in (b'"DSI-1"', b"com.libretro.retroarch", b"sendClientToScreen")
    ):
        raise InstallError("RetroArch KWin script does not target the top display")
    wiliwili_desktop_lines = payloads["wiliwili-desktop"].splitlines()
    if (
        b"Exec=/home/pocketds/.local/bin/pocketds-wiliwili"
        not in wiliwili_desktop_lines
        or b"TryExec=/home/pocketds/.local/bin/pocketds-wiliwili"
        not in wiliwili_desktop_lines
    ):
        raise InstallError("Wiliwili desktop entry bypasses its managed launcher")
    if (
        b"Exec=/home/pocketds/.local/bin/pocketds-es-de"
        not in payloads["esde-desktop"].splitlines()
    ):
        raise InstallError("ES-DE desktop entry bypasses its managed launcher")
    _validate_mapping(payloads["wiliwili-mapping-source"])


def _validate_target_boundaries(
    artifacts: list[Artifact], home: Path, system_root: Path, *, user_uid: int, root_uid: int
) -> dict[str, Snapshot]:
    snapshots: dict[str, Snapshot] = {}
    for artifact in artifacts:
        anchor = system_root if artifact.privileged else home
        expected_uid = root_uid if artifact.privileged else user_uid
        _validate_directory_chain(
            anchor,
            artifact.target.parent,
            expected_uid=expected_uid,
            privileged=artifact.privileged,
            allow_missing=True,
        )
        snapshots[artifact.artifact_id] = _snapshot_target(artifact)
    return snapshots


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise InstallError("private transaction write was short")
        view = view[written:]


def _write_private(path: Path, content: bytes) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        _write_all(descriptor, content)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_private_root(path: Path, home: Path, uid: int) -> None:
    if not _path_is_within(path, home):
        raise InstallError("transaction root escaped the account home")
    current = home
    for part in path.relative_to(home).parts:
        current /= part
        created = False
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            created = True
            metadata = current.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != uid
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise InstallError("transaction root contains an unsafe directory")
        if created:
            current.chmod(0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise InstallError("transaction root is not private")


def _create_transaction(state_root: Path, home: Path, uid: int) -> Path:
    _safe_private_root(state_root, home, uid)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(8)}"
    transaction = state_root / name
    transaction.mkdir(mode=0o700)
    for name in ("payload", "backup"):
        (transaction / name).mkdir(mode=0o700)
    return transaction


def _record_for(
    artifact: Artifact,
    source_content: bytes | None,
    payload: bytes,
    before: Snapshot,
) -> dict[str, object]:
    if artifact.preserve_existing and before.state == "PRESENT":
        action = "preserve"
        after_mode = before.mode
        after_uid = before.uid
        after_gid = before.gid
    elif (
        before.state == "PRESENT"
        and before.sha256 == _sha256(payload)
        and before.mode == artifact.mode
        and before.uid == artifact.uid
        and before.gid == artifact.gid
    ):
        action = "unchanged"
        after_mode = artifact.mode
        after_uid = artifact.uid
        after_gid = artifact.gid
    else:
        action = "replace"
        after_mode = artifact.mode
        after_uid = artifact.uid
        after_gid = artifact.gid
    return {
        "artifact_id": artifact.artifact_id,
        "target": str(artifact.target),
        "privileged": artifact.privileged,
        "phase": artifact.phase,
        "action": action,
        "source_sha256": None if source_content is None else _sha256(source_content),
        "payload_sha256": _sha256(payload),
        "payload_bytes": len(payload),
        "after_mode": f"{int(after_mode):04o}",
        "after_uid": after_uid,
        "after_gid": after_gid,
        "before": _snapshot_fields(before),
    }


def stage_transaction(
    artifacts: list[Artifact],
    source_payloads: dict[str, bytes],
    snapshots: dict[str, Snapshot],
    *,
    state_root: Path,
    home: Path,
    uid: int,
    listener_before: str,
    controller_test_before: str,
) -> Path:
    payloads = dict(source_payloads)
    live_before = snapshots["wiliwili-live-database"].content
    payloads["wiliwili-live-database"] = _merge_mapping(
        source_payloads["wiliwili-mapping-source"], live_before
    )
    retro_before = snapshots["retroarch-config"]
    if retro_before.state == "PRESENT":
        assert retro_before.content is not None
        payloads["retroarch-config"] = retro_before.content
    _validate_api_invariants(payloads)

    transaction = _create_transaction(state_root, home, uid)
    records: list[dict[str, object]] = []
    try:
        for artifact in artifacts:
            payload = payloads[artifact.artifact_id]
            source_content = (
                None if artifact.source is None else source_payloads[artifact.artifact_id]
            )
            _write_private(transaction / "payload" / artifact.artifact_id, payload)
            before = snapshots[artifact.artifact_id]
            if before.state == "PRESENT":
                assert before.content is not None
                _write_private(transaction / "backup" / artifact.artifact_id, before.content)
            records.append(_record_for(artifact, source_content, payload, before))
        manifest = {
            "schema": SCHEMA,
            "transaction_id": transaction.name,
            "listener_before": listener_before,
            "controller_test_before": controller_test_before,
            "artifact_count": len(artifacts),
            "artifact_order": [artifact.artifact_id for artifact in artifacts],
            "desktop_entries_last": all(
                item.phase == "desktop" for item in artifacts[-3:]
            )
            and all(item.phase != "desktop" for item in artifacts[:-3]),
            "artifacts": records,
        }
        content = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _write_private(transaction / "manifest.json", content)
        _write_private(transaction / "manifest.sha256", (_sha256(content) + "\n").encode("ascii"))
        for directory in (transaction / "payload", transaction / "backup", transaction):
            descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return transaction
    except Exception:
        # A partial transaction has not touched live targets; retain no
        # ambiguous manifest that could later be mistaken for install state.
        for directory_name in ("payload", "backup"):
            directory = transaction / directory_name
            if directory.exists():
                for child in directory.iterdir():
                    child.unlink()
                directory.rmdir()
        for child in transaction.iterdir():
            child.unlink()
        transaction.rmdir()
        raise


def _read_private(path: Path, *, maximum: int = MAX_FILE_BYTES) -> bytes:
    content, metadata = _read_regular(
        path, expected_uid=os.getuid(), expected_mode=0o600, allow_empty=True
    )
    if len(content) > maximum:
        raise InstallError("private transaction file exceeded its bound")
    return content


def validate_transaction(
    transaction: Path, artifacts: list[Artifact]
) -> tuple[
    dict[str, object],
    dict[str, dict[str, object]],
    dict[str, bytes],
    dict[str, bytes | None],
]:
    metadata = transaction.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise InstallError("transaction directory is unsafe")
    manifest_content = _read_private(transaction / "manifest.json", maximum=MAX_MANIFEST_BYTES)
    seal = _read_private(transaction / "manifest.sha256", maximum=128)
    if seal != (_sha256(manifest_content) + "\n").encode("ascii"):
        raise InstallError("transaction manifest seal does not match")
    try:
        manifest = json.loads(manifest_content.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InstallError("transaction manifest is invalid") from None
    expected_root = {
        "schema",
        "transaction_id",
        "listener_before",
        "controller_test_before",
        "artifact_count",
        "artifact_order",
        "desktop_entries_last",
        "artifacts",
    }
    if type(manifest) is not dict or set(manifest) != expected_root:
        raise InstallError("transaction manifest fields are invalid")
    if (
        manifest["schema"] != SCHEMA
        or manifest["transaction_id"] != transaction.name
        or manifest["listener_before"] not in {"active", "inactive"}
        or manifest["controller_test_before"] not in {"active", "inactive"}
        or manifest["artifact_count"] != len(artifacts)
        or manifest["artifact_order"] != [item.artifact_id for item in artifacts]
        or manifest["desktop_entries_last"] is not True
        or type(manifest["artifacts"]) is not list
        or len(manifest["artifacts"]) != len(artifacts)
    ):
        raise InstallError("transaction manifest identity is invalid")
    expected_record_fields = {
        "artifact_id",
        "target",
        "privileged",
        "phase",
        "action",
        "source_sha256",
        "payload_sha256",
        "payload_bytes",
        "after_mode",
        "after_uid",
        "after_gid",
        "before",
    }
    expected_before_fields = {"state", "sha256", "bytes", "mode", "uid", "gid"}
    records: dict[str, dict[str, object]] = {}
    payloads: dict[str, bytes] = {}
    backups: dict[str, bytes | None] = {}
    for artifact, raw in zip(artifacts, manifest["artifacts"]):
        if type(raw) is not dict or set(raw) != expected_record_fields:
            raise InstallError("transaction artifact fields are invalid")
        before = raw["before"]
        if type(before) is not dict or set(before) != expected_before_fields:
            raise InstallError("transaction preimage fields are invalid")
        if (
            raw["artifact_id"] != artifact.artifact_id
            or raw["target"] != str(artifact.target)
            or raw["privileged"] is not artifact.privileged
            or raw["phase"] != artifact.phase
            or raw["action"] not in {"replace", "preserve", "unchanged"}
            or type(raw["payload_sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", raw["payload_sha256"]) is None
            or type(raw["payload_bytes"]) is not int
            or not 0 <= raw["payload_bytes"] <= MAX_FILE_BYTES
            or type(raw["after_mode"]) is not str
            or re.fullmatch(r"[0-7]{4}", raw["after_mode"]) is None
            or type(raw["after_uid"]) is not int
            or type(raw["after_gid"]) is not int
        ):
            raise InstallError("transaction artifact binding is invalid")
        source_hash = raw["source_sha256"]
        if artifact.source is None:
            if source_hash is not None:
                raise InstallError("generated artifact unexpectedly names a source hash")
        else:
            if (
                type(source_hash) is not str
                or re.fullmatch(r"[0-9a-f]{64}", source_hash) is None
            ):
                raise InstallError("transaction source hash is invalid")
            live_source, _source_metadata = _read_regular(
                artifact.source,
                expected_uid=os.getuid(),
                expected_mode=artifact.mode,
                allow_empty=False,
            )
            if _sha256(live_source) != source_hash:
                raise InstallError("repository source changed after staging")
        if before["state"] == "MISSING":
            if any(before[key] is not None for key in expected_before_fields - {"state"}):
                raise InstallError("missing transaction preimage contains metadata")
        elif before["state"] == "PRESENT":
            if (
                type(before["sha256"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", before["sha256"]) is None
                or type(before["bytes"]) is not int
                or type(before["mode"]) is not str
                or re.fullmatch(r"[0-7]{4}", before["mode"]) is None
                or type(before["uid"]) is not int
                or type(before["gid"]) is not int
            ):
                raise InstallError("present transaction preimage metadata is invalid")
            backup = _read_private(transaction / "backup" / artifact.artifact_id)
            if len(backup) != before["bytes"] or _sha256(backup) != before["sha256"]:
                raise InstallError("transaction backup is not manifest-bound")
            backups[artifact.artifact_id] = backup
        else:
            raise InstallError("transaction preimage state is invalid")
        if before["state"] == "MISSING":
            backups[artifact.artifact_id] = None
        payload = _read_private(transaction / "payload" / artifact.artifact_id)
        if len(payload) != raw["payload_bytes"] or _sha256(payload) != raw["payload_sha256"]:
            raise InstallError("transaction payload is not manifest-bound")
        if raw["action"] == "preserve":
            if not artifact.preserve_existing or before["state"] != "PRESENT":
                raise InstallError("preserve action is not valid for this artifact")
            if (
                raw["payload_sha256"] != before["sha256"]
                or raw["payload_bytes"] != before["bytes"]
                or raw["after_mode"] != before["mode"]
                or raw["after_uid"] != before["uid"]
                or raw["after_gid"] != before["gid"]
            ):
                raise InstallError("preserved artifact is not bound to its preimage")
        elif raw["action"] == "unchanged":
            if (
                before["state"] != "PRESENT"
                or raw["payload_sha256"] != before["sha256"]
                or raw["payload_bytes"] != before["bytes"]
                or raw["after_mode"] != f"{artifact.mode:04o}"
                or raw["after_uid"] != artifact.uid
                or raw["after_gid"] != artifact.gid
            ):
                raise InstallError("unchanged artifact metadata is inconsistent")
        elif (
            raw["after_mode"] != f"{artifact.mode:04o}"
            or raw["after_uid"] != artifact.uid
            or raw["after_gid"] != artifact.gid
        ):
            raise InstallError("replacement target metadata is inconsistent")
        if artifact.source is not None and raw["action"] != "preserve":
            if raw["payload_sha256"] != source_hash:
                raise InstallError("ordinary artifact payload differs from its source")
        records[artifact.artifact_id] = raw
        payloads[artifact.artifact_id] = payload
    _validate_api_invariants(payloads)
    return manifest, records, payloads, backups


def _snapshot_matches_fields(snapshot: Snapshot, fields: dict[str, object]) -> bool:
    return _snapshot_fields(snapshot) == fields


def _after_fields(record: dict[str, object]) -> dict[str, object]:
    return {
        "state": "PRESENT",
        "sha256": record["payload_sha256"],
        "bytes": record["payload_bytes"],
        "mode": record["after_mode"],
        "uid": record["after_uid"],
        "gid": record["after_gid"],
    }


def _ensure_user_directory(directory: Path, home: Path, uid: int) -> None:
    if not _path_is_within(directory, home):
        raise InstallError("user target parent escaped HOME")
    current = home
    for part in directory.relative_to(home).parts:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o755)
            metadata = current.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != uid
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise InstallError("user target parent is unsafe")


def _atomic_user_replace(
    content: bytes,
    target: Path,
    *,
    uid: int,
    gid: int,
    mode: int,
    token: str,
) -> None:
    temporary = target.parent / f".{target.name}.pds-{token}.new"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, mode)
        _write_all(descriptor, content)
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, uid, gid)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, target)
        parent_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _replace_one(
    artifact: Artifact,
    source: Path,
    content: bytes,
    *,
    uid: int,
    gid: int,
    mode: int,
    token: str,
    home: Path,
    system_root: Path,
    ops: HostOperations,
) -> None:
    if artifact.privileged:
        if not artifact.target.parent.exists():
            ops.ensure_root_directory(artifact.target.parent)
        _validate_directory_chain(
            system_root,
            artifact.target.parent,
            expected_uid=artifact.uid,
            privileged=True,
            allow_missing=False,
        )
        ops.replace_root(
            source,
            artifact.target,
            uid=uid,
            gid=gid,
            mode=mode,
            token=token,
            expected_sha256=_sha256(content),
            expected_bytes=len(content),
        )
    else:
        _ensure_user_directory(artifact.target.parent, home, artifact.uid)
        _atomic_user_replace(
            content, artifact.target, uid=uid, gid=gid, mode=mode, token=token
        )


def _remove_one(artifact: Artifact, *, ops: HostOperations) -> None:
    if artifact.privileged:
        ops.remove_root(artifact.target)
    else:
        artifact.target.unlink()
        parent_fd = os.open(
            artifact.target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)


def _verify_preimages(
    artifacts: list[Artifact], records: dict[str, dict[str, object]]
) -> None:
    for artifact in artifacts:
        if not _snapshot_matches_fields(
            _snapshot_target(artifact), records[artifact.artifact_id]["before"]
        ):
            raise InstallError("live target changed after transaction staging")


def _verify_postimages(
    artifacts: list[Artifact], records: dict[str, dict[str, object]]
) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for artifact in artifacts:
        record = records[artifact.artifact_id]
        snapshot = _snapshot_target(artifact)
        if not _snapshot_matches_fields(snapshot, _after_fields(record)):
            raise InstallError("installed target failed hash/owner/mode verification")
        assert snapshot.content is not None
        payloads[artifact.artifact_id] = snapshot.content
    _validate_api_invariants(payloads)
    return payloads


def _rollback(
    artifacts: list[Artifact],
    records: dict[str, dict[str, object]],
    backups: dict[str, bytes | None],
    transaction: Path,
    *,
    home: Path,
    system_root: Path,
    ops: HostOperations,
) -> None:
    errors: list[str] = []
    for artifact in reversed(artifacts):
        record = records[artifact.artifact_id]
        try:
            current = _snapshot_target(artifact)
            before = record["before"]
            if _snapshot_matches_fields(current, before):
                continue
            if not _snapshot_matches_fields(current, _after_fields(record)):
                raise InstallError("rollback refused an unknown live target generation")
            if before["state"] == "MISSING":
                _remove_one(artifact, ops=ops)
            else:
                backup_content = backups.get(artifact.artifact_id)
                if backup_content is None:
                    raise InstallError("validated rollback preimage is unavailable")
                _replace_one(
                    artifact,
                    transaction / "backup" / artifact.artifact_id,
                    backup_content,
                    uid=int(before["uid"]),
                    gid=int(before["gid"]),
                    mode=int(str(before["mode"]), 8),
                    token=f"rollback-{artifact.artifact_id}",
                    home=home,
                    system_root=system_root,
                    ops=ops,
                )
            if not _snapshot_matches_fields(_snapshot_target(artifact), before):
                raise InstallError("rollback preimage verification failed")
        except Exception as exc:  # retain every rollback failure
            errors.append(f"{artifact.artifact_id}: {exc}")
    if errors:
        raise InstallError("rollback was incomplete: " + "; ".join(errors))


@contextmanager
def _game_session_lock(runtime_dir: Path, uid: int) -> Iterator[None]:
    try:
        metadata = runtime_dir.lstat()
    except OSError as exc:
        raise InstallError("game-session runtime directory is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != uid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise InstallError("game-session runtime directory is unsafe")
    path = runtime_dir / "pocketds-game-session.lock"
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise InstallError("game-session lock is unavailable or linked") from exc
    locked = False
    try:
        lock_metadata = os.fstat(descriptor)
        canonical = path.lstat()
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_uid != uid
            or lock_metadata.st_nlink != 1
            or stat.S_IMODE(lock_metadata.st_mode) not in {0o600, 0o640, 0o644}
            or lock_metadata.st_size != 0
            or (lock_metadata.st_dev, lock_metadata.st_ino)
            != (canonical.st_dev, canonical.st_ino)
        ):
            raise InstallError("game-session lock identity is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            raise InstallError("a managed game session is active") from None
        locked = True
        after = path.lstat()
        if (after.st_dev, after.st_ino) != (lock_metadata.st_dev, lock_metadata.st_ino):
            raise InstallError("game-session lock changed identity")
        yield
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _signal_guard() -> Iterator[None]:
    previous: dict[int, object] = {}

    def caught(signum: int, _frame: object) -> None:
        raise InstallError(f"install interrupted by signal {signum}")

    try:
        for item in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            previous[item] = signal.getsignal(item)
            signal.signal(item, caught)
        yield
    finally:
        for item, handler in previous.items():
            signal.signal(item, handler)


@contextmanager
def _cleanup_signal_guard() -> Iterator[None]:
    """A first signal starts cleanup; later signals cannot interrupt it."""

    previous: dict[int, object] = {}
    try:
        for item in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            previous[item] = signal.getsignal(item)
            signal.signal(item, signal.SIG_IGN)
        yield
    finally:
        for item, handler in previous.items():
            signal.signal(item, handler)


def _restore_listener_state(before: str, ops: HostOperations) -> None:
    try:
        current = ops.listener_state()
    except InstallError:
        # A failed system unit is neither supported steady state, but an exact
        # start/stop can still restore the captured active/inactive state.
        current = "unknown"
    if current == before:
        return
    if before == "active":
        ops.start_listener()
    else:
        ops.stop_listener()
    if ops.listener_state() != before:
        raise InstallError("mode-listener preinstall state was not restored")


def _write_status(transaction: Path, name: str, manifest_hash: str) -> None:
    content = (
        json.dumps(
            {
                "schema": COMMIT_SCHEMA,
                "state": name.upper(),
                "manifest_sha256": manifest_hash,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    _write_private(transaction / f"{name}.json", content)


def install_stack(
    repo_root: Path,
    home: Path,
    system_root: Path,
    runtime_dir: Path,
    *,
    ops: HostOperations,
    state_root: Path | None = None,
    user_uid: int | None = None,
    user_gid: int | None = None,
    root_uid: int = 0,
    root_gid: int = 0,
) -> Path:
    """Preflight, stage, atomically apply, verify, activate, or roll back."""

    uid = os.getuid() if user_uid is None else user_uid
    gid = os.getgid() if user_gid is None else user_gid
    state = (
        home / ".local/state/pocketds-linux-kit/game-input-transactions"
        if state_root is None
        else state_root
    )
    artifacts = artifact_plan(
        repo_root,
        home,
        system_root,
        user_uid=uid,
        user_gid=gid,
        root_uid=root_uid,
        root_gid=root_gid,
    )
    if [item.phase for item in artifacts[-3:]] != [
        "desktop",
        "desktop",
        "desktop",
    ]:
        raise InstallError("desktop artifacts are not last")

    # Every check before the lock is read-only.  In particular, a busy app or
    # unsafe source/target cannot create a transaction or pause the listener.
    ops.require_sudo()
    ops.assert_quiescent()
    source_payloads = _source_payloads(artifacts, repo_root, uid)
    _validate_api_invariants(source_payloads)
    snapshots = _validate_target_boundaries(
        artifacts, home, system_root, user_uid=uid, root_uid=root_uid
    )
    listener_before = ops.listener_state()
    controller_test_before = ops.controller_test_state()

    transaction: Path | None = None
    records: dict[str, dict[str, object]] = {}
    payloads: dict[str, bytes] = {}
    backups: dict[str, bytes | None] = {}
    manifest_hash = ""
    activation_started = False
    with _signal_guard(), _game_session_lock(runtime_dir, uid):
        try:
            # Close the process-query/lock-acquire race, including manually
            # launched apps that do not honor the managed session lock.
            ops.assert_quiescent()
            if ops.listener_state() != listener_before:
                raise InstallError("mode-listener state changed during preflight")
            if ops.controller_test_state() != controller_test_before:
                raise InstallError("controller-test state changed during preflight")
            snapshots = _validate_target_boundaries(
                artifacts, home, system_root, user_uid=uid, root_uid=root_uid
            )
            transaction = stage_transaction(
                artifacts,
                source_payloads,
                snapshots,
                state_root=state,
                home=home,
                uid=uid,
                listener_before=listener_before,
                controller_test_before=controller_test_before,
            )
            ops.before_apply(transaction)
            manifest, records, payloads, backups = validate_transaction(
                transaction, artifacts
            )
            manifest_hash = _sha256(
                _read_private(
                    transaction / "manifest.json", maximum=MAX_MANIFEST_BYTES
                )
            )
            _verify_preimages(artifacts, records)
            ops.after_validate(transaction)

            if listener_before == "active":
                ops.stop_listener()
            elif ops.listener_state() != "inactive":
                raise InstallError("inactive mode listener started before file commit")
            if controller_test_before == "active":
                ops.stop_controller_test()

            for artifact in artifacts:
                record = records[artifact.artifact_id]
                if record["action"] in {"preserve", "unchanged"}:
                    continue
                # Bind the target immediately before its same-filesystem stage.
                if not _snapshot_matches_fields(
                    _snapshot_target(artifact), record["before"]
                ):
                    raise InstallError("live target drifted during installation")
                # The private payload may be user-owned, but the bytes used by
                # this generation are the already validated in-memory copy.
                # Root staging independently compares its root-owned temp file
                # with this digest before the exact `mv -T` publication.
                current_payload = _read_private(
                    transaction / "payload" / artifact.artifact_id
                )
                if current_payload != payloads[artifact.artifact_id]:
                    raise InstallError("transaction payload changed after validation")
                _replace_one(
                    artifact,
                    transaction / "payload" / artifact.artifact_id,
                    payloads[artifact.artifact_id],
                    uid=int(record["after_uid"]),
                    gid=int(record["after_gid"]),
                    mode=int(str(record["after_mode"]), 8),
                    token=f"install-{artifact.artifact_id}",
                    home=home,
                    system_root=system_root,
                    ops=ops,
                )
                ops.after_replace(artifact)
                if not _snapshot_matches_fields(
                    _snapshot_target(artifact), _after_fields(record)
                ):
                    raise InstallError("post-rename artifact verification failed")

            _verify_postimages(artifacts, records)
            tmpfiles = next(
                item.target for item in artifacts if item.artifact_id == "input-tmpfiles"
            )
            canonical = next(
                item.target
                for item in artifacts
                if item.artifact_id == "input-mode-canonical"
            )
            ops.daemon_reload()
            # Desktop cache publication is deliberately after every launcher,
            # helper, config and desktop file has committed and verified, but
            # before the runtime-protocol commit boundary.
            ops.update_desktop_database(home / ".local/share/applications")
            _write_status(transaction, "activation-intent", manifest_hash)

            # Commit boundary: tmpfiles may alter the shared /run protocol and
            # bootstrap may alter InputPlumber mode/token. From this line on we
            # must keep the verified new files and recover forward; rolling old
            # files back would create a mixed old-code/new-runtime generation.
            activation_started = True
            ops.create_tmpfiles(tmpfiles)
            ops.bootstrap(canonical)
            ops.start_controller_test()
            _verify_postimages(artifacts, records)
            _write_status(transaction, "committed", manifest_hash)

            if listener_before == "active":
                ops.start_listener()
            return transaction
        except Exception as original:
            with _cleanup_signal_guard():
                if activation_started:
                    # Forward recovery is idempotent. In the active-listener
                    # case systemd also runs the same bootstrap as ExecStartPre.
                    forward_errors: list[str] = []
                    try:
                        ops.daemon_reload()
                        ops.create_tmpfiles(
                            next(
                                item.target
                                for item in artifacts
                                if item.artifact_id == "input-tmpfiles"
                            )
                        )
                        ops.bootstrap(
                            next(
                                item.target
                                for item in artifacts
                                if item.artifact_id == "input-mode-canonical"
                            )
                        )
                        ops.start_controller_test()
                        _verify_postimages(artifacts, records)
                    except Exception as exc:
                        forward_errors.append(f"runtime: {exc}")
                    try:
                        _restore_listener_state(listener_before, ops)
                    except Exception as exc:
                        forward_errors.append(f"listener: {exc}")
                    status_name = (
                        "forward-recovered" if not forward_errors else "forward-required"
                    )
                    try:
                        _write_status(transaction, status_name, manifest_hash)
                    except Exception as exc:
                        forward_errors.append(f"status: {exc}")
                    if not forward_errors:
                        return transaction
                    raise InstallError(
                        "new files/runtime were retained after the activation "
                        "boundary; forward recovery is still required: "
                        + "; ".join(forward_errors)
                    ) from original

                rollback_error: Exception | None = None
                if transaction is not None and records:
                    # No runtime-protocol mutation has happened yet. It is safe
                    # to restore the exact file preimages as one generation.
                    try:
                        _rollback(
                            artifacts,
                            records,
                            backups,
                            transaction,
                            home=home,
                            system_root=system_root,
                            ops=ops,
                        )
                        ops.daemon_reload()
                        ops.update_desktop_database(
                            home / ".local/share/applications"
                        )
                        _write_status(transaction, "rolled-back", manifest_hash)
                    except Exception as exc:
                        rollback_error = exc
                try:
                    if controller_test_before == "active":
                        ops.start_controller_test()
                    _restore_listener_state(listener_before, ops)
                except Exception as exc:
                    rollback_error = rollback_error or exc
                if rollback_error is not None:
                    raise InstallError(
                        f"install failed ({original}); rollback/listener "
                        f"restoration also failed: {rollback_error}"
                    ) from original
                if isinstance(original, InstallError):
                    raise
                raise InstallError(
                    f"game/input stack install failed: {original}"
                ) from original


def _production_context() -> tuple[Path, Path, Path, Path, int, int]:
    if os.geteuid() == 0:
        raise InstallError("run this installer as the desktop user, not root")
    account = pwd.getpwuid(os.getuid())
    if account.pw_name != "pocketds":
        raise InstallError("the Pocket DS desktop account must be named pocketds")
    try:
        private_group = grp.getgrnam("pocketds")
    except KeyError:
        raise InstallError("the pocketds private group does not exist") from None
    if account.pw_gid != private_group.gr_gid:
        raise InstallError("the pocketds user/private group identity is unavailable")
    home = Path(account.pw_dir)
    if home != Path("/home/pocketds"):
        raise InstallError("desktop payload APIs require HOME=/home/pocketds")
    repo_root = Path(__file__).resolve().parents[1]
    runtime_dir = Path("/run/user") / str(os.getuid())
    return repo_root, home, Path("/"), runtime_dir, os.getuid(), os.getgid()


def main(argv: Sequence[str]) -> int:
    if argv:
        print("usage: install-game-input-stack.py", file=sys.stderr)
        return 2
    try:
        repo, home, system, runtime, uid, gid = _production_context()
        ops = HostOperations(uid=uid, runtime_dir=runtime)
        transaction = install_stack(
            repo,
            home,
            system,
            runtime,
            ops=ops,
            user_uid=uid,
            user_gid=gid,
        )
    except InstallError as exc:
        print(f"install-game-input-stack: {exc}", file=sys.stderr)
        return 1
    print(f"[install-game-input-stack] complete; transaction: {transaction}")
    print("[install-game-input-stack] InputPlumber was not restarted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

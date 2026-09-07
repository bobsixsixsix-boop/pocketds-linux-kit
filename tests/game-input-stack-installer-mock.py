#!/usr/bin/env python3
"""Offline whole-transaction tests for install-game-input-stack.py."""

from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
# Mock-only format marker, not an executable. Source-only exports intentionally
# omit the built observer; API/transaction tests must not require that artifact.
MOCK_OBSERVER = b"\x7fELFfixture-gamescope-observer\n"
SCRIPT = ROOT / "scripts/install-game-input-stack.py"
SPEC = importlib.util.spec_from_file_location("install_game_input_stack", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)


def actual_source_fixture(root: Path) -> tuple[Path, list[MODULE.Artifact]]:
    """Copy the real source APIs, substituting only the mock observer bytes."""
    repo = root / "repo"
    home = root / "home"
    system = root / "system"
    repo.mkdir(mode=0o755)
    home.mkdir(mode=0o700)
    system.mkdir(mode=0o755)
    artifacts = MODULE.artifact_plan(
        repo,
        home,
        system,
        user_uid=os.getuid(),
        user_gid=os.getgid(),
        root_uid=os.getuid(),
        root_gid=os.getgid(),
    )
    for artifact in artifacts:
        if artifact.source is None:
            continue
        if artifact.artifact_id == "gamescope-observer":
            write(artifact.source, MOCK_OBSERVER, artifact.mode)
        else:
            original = ROOT / artifact.source.relative_to(repo)
            # Preserve real source modes so the source gate still checks them.
            write(
                artifact.source, original.read_bytes(),
                stat.S_IMODE(original.stat().st_mode),
            )
    return repo, artifacts


def file_state(path: Path) -> tuple[bytes | None, int | None, int | None, int | None]:
    if not path.exists() and not path.is_symlink():
        return None, None, None, None
    metadata = path.stat()
    return (
        path.read_bytes(),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
    )


class FakeOperations(MODULE.HostOperations):
    """Mock sudo, systemctl, Flatpak/pgrep, tmpfiles and desktop cache."""

    def __init__(self, *, uid: int, runtime_dir: Path) -> None:
        self.uid = uid
        self.runtime_dir = runtime_dir
        self.busy = False
        self.listener = "active"
        self.controller_test = "inactive"
        self.fail_controller_stop = False
        self.fail_controller_start_once = False
        self.events: list[tuple[str, str]] = []
        self.replaced = 0
        self.fail_after_replace: int | None = None
        self.tamper: str | None = None
        self.input_mode = "fresh"
        self.runtime_protocol = "legacy"
        self.fail_desktop_once = False
        self.fail_start_once = False
        self.fail_start_always = False
        self.fail_bootstrap_once = False
        self.fail_tmpfiles_always = False
        self.signal_bootstrap_once = False
        self.listener_state_calls = 0
        self.drift_listener_on_second_probe = False
        self.signal_after_replace: int | None = None
        self.signal_during_rollback = False
        self.rollback_signal_sent = False

    def require_sudo(self) -> None:
        self.events.append(("sudo", "true"))

    def assert_quiescent(self) -> None:
        self.events.append(("probe", "flatpak+pgrep"))
        if self.busy:
            raise MODULE.InstallError("managed game process is running")

    def listener_state(self) -> str:
        self.listener_state_calls += 1
        if self.drift_listener_on_second_probe and self.listener_state_calls == 2:
            self.listener = "active"
        self.events.append(("systemctl", f"is-active:{self.listener}"))
        return self.listener

    def stop_listener(self) -> None:
        self.events.append(("systemctl", "stop-listener"))
        self.listener = "inactive"

    def start_listener(self) -> None:
        self.events.append(("systemctl", "start-listener"))
        if self.fail_start_always:
            raise MODULE.InstallError("injected persistent listener start failure")
        if self.fail_start_once:
            self.fail_start_once = False
            raise MODULE.InstallError("injected one-shot listener start failure")
        self.listener = "active"

    def controller_test_state(self) -> str:
        self.events.append(("systemctl", f"controller-test-state:{self.controller_test}"))
        return self.controller_test

    def stop_controller_test(self) -> None:
        self.events.append(("systemctl", "stop-controller-test"))
        if self.fail_controller_stop:
            raise MODULE.InstallError("controller-test is still draining held controls")
        self.controller_test = "inactive"

    def start_controller_test(self) -> None:
        self.events.append(("systemctl", "start-controller-test"))
        if self.fail_controller_start_once:
            self.fail_controller_start_once = False
            raise MODULE.InstallError("injected controller-test start failure")
        self.controller_test = "active"

    def ensure_root_directory(self, directory: Path) -> None:
        self.events.append(("sudo", f"install-d:{directory.name}"))
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o755)

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
        self.events.append(("sudo", f"same-fs-replace:{target.name}"))
        temporary = target.parent / f".{target.name}.{token}.new"
        temporary.write_bytes(source.read_bytes())
        temporary.chmod(mode)
        os.chown(temporary, uid, gid)
        self.assert_stage = (expected_sha256, expected_bytes)
        if (
            hashlib.sha256(temporary.read_bytes()).hexdigest() != expected_sha256
            or temporary.stat().st_size != expected_bytes
        ):
            temporary.unlink()
            raise MODULE.InstallError("mock root stage digest mismatch")
        os.replace(temporary, target)
        if (
            token.startswith("rollback-")
            and self.signal_during_rollback
            and not self.rollback_signal_sent
        ):
            self.rollback_signal_sent = True
            os.kill(os.getpid(), signal.SIGTERM)

    def remove_root(self, target: Path) -> None:
        self.events.append(("sudo", f"remove:{target.name}"))
        target.unlink()

    def daemon_reload(self) -> None:
        self.events.append(("systemctl", "daemon-reload-no-restart"))

    def create_tmpfiles(self, config: Path) -> None:
        self.events.append(("sudo", f"tmpfiles:{config.name}"))
        self.runtime_protocol = "v1"
        if self.fail_tmpfiles_always:
            raise MODULE.InstallError("injected persistent tmpfiles failure")

    def bootstrap(self, canonical_helper: Path) -> None:
        self.events.append(("input", f"bootstrap:{canonical_helper.name}"))
        if self.input_mode == "fresh":
            self.input_mode = "gamepad"
        if self.signal_bootstrap_once:
            self.signal_bootstrap_once = False
            os.kill(os.getpid(), signal.SIGTERM)
        if self.fail_bootstrap_once:
            self.fail_bootstrap_once = False
            raise MODULE.InstallError("injected one-shot bootstrap failure")

    def update_desktop_database(self, applications: Path) -> None:
        self.events.append(("desktop", f"update:{applications.name}"))
        if self.fail_desktop_once:
            self.fail_desktop_once = False
            raise MODULE.InstallError("injected one-shot desktop cache failure")

    def before_apply(self, transaction: Path) -> None:
        if self.tamper == "payload":
            path = transaction / "payload/input-toggle-root"
            path.write_bytes(b"tampered payload\n")
            path.chmod(0o600)
        elif self.tamper == "manifest":
            path = transaction / "manifest.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["artifact_order"] = list(reversed(value["artifact_order"]))
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            path.chmod(0o600)
        elif self.tamper == "resigned-payload":
            payload = transaction / "payload/input-toggle-root"
            payload.write_bytes(payload.read_bytes() + b"# same-user rewrite\n")
            payload.chmod(0o600)
            manifest_path = transaction / "manifest.json"
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            record = next(
                item
                for item in value["artifacts"]
                if item["artifact_id"] == "input-toggle-root"
            )
            content = payload.read_bytes()
            record["payload_sha256"] = hashlib.sha256(content).hexdigest()
            record["payload_bytes"] = len(content)
            manifest_content = (
                json.dumps(value, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            manifest_path.write_bytes(manifest_content)
            manifest_path.chmod(0o600)
            seal = transaction / "manifest.sha256"
            seal.write_text(
                hashlib.sha256(manifest_content).hexdigest() + "\n",
                encoding="ascii",
            )
            seal.chmod(0o600)

    def after_validate(self, transaction: Path) -> None:
        if self.tamper == "after-validate":
            payload = transaction / "payload/input-mode-canonical"
            payload.write_bytes(payload.read_bytes() + b"# raced\n")
            payload.chmod(0o600)

    def after_replace(self, artifact: MODULE.Artifact) -> None:
        self.replaced += 1
        self.events.append(("replace", artifact.artifact_id))
        if self.fail_after_replace == self.replaced:
            raise MODULE.InstallError("injected failure after rename")
        if self.signal_after_replace == self.replaced:
            os.kill(os.getpid(), signal.SIGTERM)


class Fixture:
    def __init__(self, root: Path, *, existing_targets: bool = True) -> None:
        self.root = root
        self.repo = root / "repo"
        self.home = root / "home"
        self.system = root / "system"
        self.runtime = root / "runtime"
        self.uid = os.getuid()
        self.gid = os.getgid()
        for directory, mode in (
            (self.repo, 0o755),
            (self.home, 0o700),
            (self.system, 0o755),
            (self.runtime, 0o700),
        ):
            directory.mkdir(parents=True)
            directory.chmod(mode)
        self.artifacts = MODULE.artifact_plan(
            self.repo,
            self.home,
            self.system,
            user_uid=self.uid,
            user_gid=self.gid,
            root_uid=self.uid,
            root_gid=self.gid,
        )
        self._write_sources()
        self.before: dict[str, tuple[bytes | None, int | None, int | None, int | None]] = {}
        for index, artifact in enumerate(self.artifacts):
            if existing_targets:
                if artifact.artifact_id == "retroarch-config":
                    content = b"custom-user-retroarch-config\n"
                    mode = 0o600
                elif artifact.artifact_id == "wiliwili-live-database":
                    content = (
                        b"11111111111111111111111111111111,Other Pad,a:b0,platform:Linux,\n"
                        + MODULE.GLFW_GUID.encode("ascii")
                        + b",Stale Pocket DS,a:b9,platform:Linux,\n"
                    )
                    mode = 0o644
                else:
                    content = f"old-{artifact.artifact_id}-{index}\n".encode()
                    mode = artifact.mode
                write(artifact.target, content, mode)
            self.before[artifact.artifact_id] = file_state(artifact.target)
        self.state_root = (
            self.home
            / ".local/state/pocketds-linux-kit/game-input-transactions"
        )
        self.ops = FakeOperations(uid=self.uid, runtime_dir=self.runtime)

    def _write_sources(self) -> None:
        real_mapping = (ROOT / "components/wiliwili/gamecontrollerdb.txt").read_bytes()
        contents: dict[str, bytes] = {
            "controller-test-gate": (
                b'# pds-controller-test-v1\ndef hardware_actions_blocked(): return False\n'
            ),
            "controller-test-helper": b'# pds-controller-test-v1\n',
            "controller-test-service": (
                b'[Service]\nType=notify\nRuntimeDirectory=pocketds-controller-test\n'
                b'ExecStopPost=/usr/local/libexec/pocketds-controller-test.py --recover\n'
            ),
            "input-mode-canonical": (
                b"#!/bin/sh\n# org.shadowblip.InputPlumber.Manager\n"
                b"# LoadProfilePath SetTargetDevices bootstrap recover-token\n"
            ),
            "input-mode-user-wrapper": (
                b"#!/bin/sh\nexec /usr/local/libexec/pocketds-input-mode \"$@\"\n"
            ),
            "input-toggle-root": (
                b"#!/bin/sh\nexec /usr/local/libexec/pocketds-input-mode toggle --nonblocking\n"
            ),
            "game-session-supervisor": (
                b"#!/usr/bin/python3\n# acquire gamepad; recover-token receipt\n"
                b'# native session name "steam"; probe_native_session\n'
            ),
            "gamescope-observer": MOCK_OBSERVER,
            "gamescope-inner": (
                b"#!/bin/sh\n# mangoapp_use_output_timing false\n"
                b"POCKETDS_GAMESCOPE_PARENT_PID=$$ "
                b"/usr/local/libexec/pocketds-gamescope-observer &\n"
                b"export WAYLAND_DISPLAY=/nonexistent/pocketds-gamescope-wayland\n"
                b"exec \"$@\"\n"
            ),
            "game-runtime": (
                b"#!/bin/sh\ng=/usr/bin/gamescope\n"
                b"# pocketds-gamescope-inner kwin-gamescope-top-screen.js\n"
                b"# currentModeId\noutput_refresh=120\n"
                b"$g -W 1920 -H 1080 -r \"$output_refresh\" "
                b"--force-windows-fullscreen -- app\n"
            ),
            "game-limit": (
                b"#!/bin/sh\n# pocketds-gamescope-limit game-fps-limit\nexit 0\n"
            ),
            "gamescope-kwin-script": (
                b'const output = "DSI-1"; const app = "gamescope";\n'
            ),
            "steam-session-launcher": (
                b"#!/bin/sh\ns=/usr/local/libexec/pocketds-game-session\n"
                b"# pocketds-game-runtime; probe --name steam\n"
                b"# pgrep -u \"$uid\" -x steam\n"
                b"# exec \"$steam_client\" -ifrunning \"$@\"\n"
                b"# exec env STEAMDECK_MODE=true \"$supervisor\" run\n"
                b"# --pocketds-gamepad-restart --unit=pocketds-steam-gamepad\n"
                b"# --property=SuccessExitStatus=143 pocketds-steam-desktop.service\n"
                b"exec $s run --backend native --name steam -- app\n"
            ),
            "steam-desktop-selector": (
                b"#!/bin/sh\nsupervisor=/usr/local/libexec/pocketds-game-session\n"
                b"system_selector=/usr/bin/steamos-session-select\n"
                b"# probe --name steam\nexec \"$supervisor\" stop\n"
            ),
            "steam-unified-desktop": (
                b"[Desktop Entry]\n"
                b"TryExec=/home/pocketds/.local/bin/pocketds-steam-session\n"
                b"Exec=env STEAMDECK_MODE=true /home/pocketds/.local/bin/pocketds-steam-session %U\n"
                b"Actions=GamepadUI;\n"
                b"[Desktop Action GamepadUI]\n"
                b"Exec=/home/pocketds/.local/bin/pocketds-steam-session -gamepadui\n"
            ),
            "mode-listener-state-dropin": (
                b"[Unit]\nRequires=systemd-tmpfiles-setup.service\n"
                b"After=systemd-tmpfiles-setup.service inputplumber.service\n"
                b"Requires=pocketds-controller-test.service\n"
                b"After=pocketds-controller-test.service\n"
                b"[Service]\n"
                b"ReadWritePaths=/run/pocketds-input-mode\n"
                b"ExecStartPre=/usr/local/libexec/pocketds-input-mode bootstrap\n"
            ),
            "input-tmpfiles": (
                b"f /run/pocketds-input-mode.lock 0640 root pocketds -\n"
                b"d /run/pocketds-input-mode 2770 root pocketds -\n"
            ),
            "input-capability-map": (
                b"Physical equals key \xe2\x86\x92 Steam/Xbox Guide\n"
                b"event_code: BTN6\nbutton: Guide\n"
                b"AYA logo \xe2\x86\x92 internal profile slot\n"
                b"event_code: BTN5\nbutton: Keyboard\n"
                b"MCU BTN_MODE (scan 0x9000d) \xe2\x86\x92 Brightness Up\n"
                b"event_code: BTN_MODE\nbutton: QuickAccess\n"
                b"MCU BTN_2 (scan 0x90003) \xe2\x86\x92 Brightness Down\n"
                b"event_code: BTN2\nbutton: QuickAccess2\n"
                b"MCU BTN_3 \xe2\x86\x92 RightPaddle2\n"
                b"MCU BTN_4 \xe2\x86\x92 LeftPaddle2\n"
            ),
            "wiliwili-launcher": (
                b"#!/bin/sh\napp_id=cn.xfangfang.wiliwili\n"
                b"exec /usr/local/libexec/pocketds-game-session run "
                b"--backend flatpak --name wiliwili --app-id $app_id -- \"$@\"\n"
            ),
            "esde-launcher": (
                b"#!/bin/sh\nexport SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1\n"
                b"# POCKETDS_ESDE_SHARED_SYSTEMS --esde-home "
                b"--force-managed-systems --home\n"
                b"exec /usr/local/libexec/pocketds-game-session run "
                b"--backend native --name es-de -- app\n"
            ),
            "retroarch-launcher": (
                b"#!/bin/sh\ns=/usr/local/libexec/pocketds-game-session\n"
                b"# pocketds-game-runtime --appendconfig\n"
                b"$s join --token token -- app\n"
                b"exec $s run --backend native --name retroarch --performance -- app\n"
            ),
            "retroarch-kwin-script": (
                b'const target = "DSI-1"; // com.libretro.retroarch sendClientToScreen\n'
            ),
            "retroarch-managed-config": (
                b'aspect_ratio_index = "21"\nvideo_fullscreen = "true"\n'
                b'video_windowed_fullscreen = "true"\n'
                b'input_quit_gamepad_combo = "0"\nquit_press_twice = "true"\n'
            ),
            "ppsspp-core-options": b'ppsspp_internal_resolution = "1440x816"\n',
            "melonds-launcher": (
                b"#!/bin/sh\ns=/usr/local/libexec/pocketds-game-session\n"
                b"# net.kuribo64.melonDS kwin-melonds-dualscreen.js\n"
                b"# --filesystem=\"$shared_root:ro\"\n"
                b"# --save-dir \"$save_dir\" --state-dir \"$state_dir\"\n"
                b"$s join --token token -- app\n"
                b"exec $s run --backend native --name melonds --performance -- app\n"
            ),
            "wiliwili-desktop": (
                b"[Desktop Entry]\n"
                b"Exec=/home/pocketds/.local/bin/pocketds-wiliwili\n"
                b"TryExec=/home/pocketds/.local/bin/pocketds-wiliwili\n"
            ),
            "esde-desktop": b"[Desktop Entry]\nExec=/home/pocketds/.local/bin/pocketds-es-de\n",
            "wiliwili-mapping-source": real_mapping,
        }
        for artifact in self.artifacts:
            if artifact.source is None:
                continue
            content = contents.get(
                artifact.artifact_id,
                f"fixture-source-{artifact.artifact_id}\n".encode(),
            )
            write(artifact.source, content, artifact.mode)

    def install(self) -> Path:
        return MODULE.install_stack(
            self.repo,
            self.home,
            self.system,
            self.runtime,
            ops=self.ops,
            state_root=self.state_root,
            user_uid=self.uid,
            user_gid=self.gid,
            root_uid=self.uid,
            root_gid=self.gid,
        )

    def assert_preimages(self, testcase: unittest.TestCase) -> None:
        for artifact in self.artifacts:
            testcase.assertEqual(
                file_state(artifact.target),
                self.before[artifact.artifact_id],
                artifact.artifact_id,
            )


class UnifiedInstallerTests(unittest.TestCase):
    def test_actual_repository_whole_stack_passes_source_api_preflight(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-real-source-plan-") as temporary:
            repo, artifacts = actual_source_fixture(Path(temporary))
            payloads = MODULE._source_payloads(artifacts, repo, os.getuid())
            MODULE._validate_api_invariants(payloads)
            self.assertEqual(len(artifacts), 43)
            self.assertEqual(payloads["gamescope-observer"], MOCK_OBSERVER)
            self.assertEqual(
                payloads["input-mode-canonical"],
                (ROOT / "components/emulation/pocketds-input-mode").read_bytes(),
            )

    def test_capture_recovery_must_finish_before_any_file_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-capture-install-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.controller_test = "active"
            fixture.ops.fail_controller_stop = True
            with self.assertRaisesRegex(MODULE.InstallError, "draining"):
                fixture.install()
            self.assertEqual(fixture.ops.replaced, 0)
            fixture.assert_preimages(self)

    def test_capture_daemon_is_ready_before_listener_resumes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-capture-order-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.controller_test = "active"
            transaction = fixture.install()
            events = fixture.ops.events
            self.assertLess(events.index(("systemctl", "stop-listener")),
                            events.index(("systemctl", "stop-controller-test")))
            self.assertLess(events.index(("systemctl", "start-controller-test")),
                            events.index(("systemctl", "start-listener")))
            manifest = json.loads((transaction / "manifest.json").read_text())
            self.assertEqual(manifest["controller_test_before"], "active")

    def test_capture_start_failure_recovers_forward_instead_of_mixing_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-capture-start-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.fail_controller_start_once = True
            transaction = fixture.install()
            self.assertTrue((transaction / "forward-recovered.json").exists())
            self.assertEqual(fixture.ops.controller_test, "active")
            self.assertEqual(fixture.ops.listener, "active")

    def test_source_gate_rejects_canonical_helper_without_retryable_recovery(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-source-api-") as temporary:
            repo, artifacts = actual_source_fixture(Path(temporary))
            payloads = MODULE._source_payloads(artifacts, repo, os.getuid())
            payloads["input-mode-canonical"] = payloads[
                "input-mode-canonical"
            ].replace(b"recover-token", b"legacy-recovery")
            with self.assertRaisesRegex(MODULE.InstallError, "retryable recovery"):
                MODULE._validate_api_invariants(payloads)

    def test_success_is_one_private_hash_bound_generation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            transaction = fixture.install()

            self.assertEqual(fixture.ops.listener, "active")
            self.assertEqual(fixture.ops.input_mode, "gamepad")
            self.assertTrue((transaction / "committed.json").is_file())
            self.assertEqual(stat.S_IMODE(transaction.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((transaction / "manifest.json").stat().st_mode), 0o600
            )
            manifest_content = (transaction / "manifest.json").read_bytes()
            self.assertEqual(
                (transaction / "manifest.sha256").read_text(encoding="ascii"),
                hashlib.sha256(manifest_content).hexdigest() + "\n",
            )
            manifest = json.loads(manifest_content)
            self.assertTrue(manifest["desktop_entries_last"])
            for record in manifest["artifacts"]:
                before = record["before"]
                if before["state"] == "PRESENT":
                    self.assertIsInstance(before["uid"], int)
                    self.assertIsInstance(before["gid"], int)
                    self.assertRegex(before["mode"], r"^[0-7]{4}$")
                    self.assertRegex(before["sha256"], r"^[0-9a-f]{64}$")

            retro = next(
                item for item in fixture.artifacts if item.artifact_id == "retroarch-config"
            )
            self.assertEqual(retro.target.read_bytes(), b"custom-user-retroarch-config\n")
            self.assertEqual(stat.S_IMODE(retro.target.stat().st_mode), 0o600)
            live = next(
                item
                for item in fixture.artifacts
                if item.artifact_id == "wiliwili-live-database"
            ).target.read_text(encoding="utf-8")
            self.assertIn("Other Pad", live)
            self.assertEqual(live.count(MODULE.GLFW_GUID), 1)
            self.assertIn("platform:Linux,", live)

            replaced_ids = [value for kind, value in fixture.ops.events if kind == "replace"]
            steam_launcher = next(
                item
                for item in fixture.artifacts
                if item.artifact_id == "steam-session-launcher"
            )
            self.assertEqual(steam_launcher.phase, "launcher")
            self.assertEqual(
                replaced_ids[-3:],
                ["steam-unified-desktop", "wiliwili-desktop", "esde-desktop"],
            )
            event_names = [kind + ":" + value for kind, value in fixture.ops.events]
            bootstrap_index = next(
                index for index, value in enumerate(event_names) if value.startswith("input:bootstrap")
            )
            desktop_index = next(
                index for index, value in enumerate(event_names) if value.startswith("desktop:update")
            )
            resume_index = event_names.index("systemctl:start-listener")
            self.assertLess(desktop_index, bootstrap_index)
            self.assertLess(bootstrap_index, resume_index)
            self.assertFalse(
                any(
                    value.startswith("systemctl:restart")
                    for value in event_names
                )
            )
            self.assertTrue(any(value.startswith("sudo:same-fs-replace") for value in event_names))

    def test_bootstrap_preserves_stable_joymouse_before_listener_resume(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.input_mode = "joymouse"
            fixture.install()
            self.assertEqual(fixture.ops.input_mode, "joymouse")
            events = [kind + ":" + value for kind, value in fixture.ops.events]
            self.assertEqual(
                sum(value.startswith("input:bootstrap") for value in events), 1
            )
            self.assertLess(
                next(i for i, value in enumerate(events) if value.startswith("input:bootstrap")),
                events.index("systemctl:start-listener"),
            )

    def test_preexisting_inactive_listener_remains_inactive(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.listener = "inactive"
            fixture.install()
            self.assertEqual(fixture.ops.listener, "inactive")
            self.assertNotIn(("systemctl", "stop-listener"), fixture.ops.events)
            self.assertNotIn(("systemctl", "start-listener"), fixture.ops.events)

    def test_empty_legacy_readonly_session_lock_can_be_proven_free(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            lock = fixture.runtime / "pocketds-game-session.lock"
            lock.write_bytes(b"")
            lock.chmod(0o644)
            fixture.install()
            self.assertEqual(fixture.ops.listener, "active")

    def test_busy_preflight_writes_nothing_and_does_not_pause_listener(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.busy = True
            with self.assertRaisesRegex(MODULE.InstallError, "running"):
                fixture.install()
            fixture.assert_preimages(self)
            self.assertFalse(fixture.state_root.exists())
            self.assertFalse((fixture.runtime / "pocketds-game-session.lock").exists())
            self.assertEqual(fixture.ops.listener, "active")
            self.assertNotIn(("systemctl", "stop-listener"), fixture.ops.events)

    def test_mid_rename_failure_rolls_back_every_target_and_resumes_listener(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.fail_after_replace = 5
            with self.assertRaisesRegex(MODULE.InstallError, "injected failure"):
                fixture.install()
            fixture.assert_preimages(self)
            self.assertEqual(fixture.ops.listener, "active")
            self.assertIn(("systemctl", "stop-listener"), fixture.ops.events)
            self.assertIn(("systemctl", "start-listener"), fixture.ops.events)
            self.assertFalse(any(kind == "input" for kind, _value in fixture.ops.events))
            transactions = list(fixture.state_root.iterdir())
            self.assertEqual(len(transactions), 1)
            self.assertTrue((transactions[0] / "rolled-back.json").is_file())
            self.assertFalse((transactions[0] / "committed.json").exists())

    def test_symlink_target_is_refused_before_transaction_or_listener_stop(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            artifact = next(
                item for item in fixture.artifacts if item.artifact_id == "input-toggle-root"
            )
            sentinel = fixture.root / "sentinel"
            sentinel.write_bytes(b"do-not-touch\n")
            artifact.target.unlink()
            artifact.target.symlink_to(sentinel)
            with self.assertRaisesRegex(MODULE.InstallError, "linked"):
                fixture.install()
            self.assertEqual(sentinel.read_bytes(), b"do-not-touch\n")
            self.assertTrue(artifact.target.is_symlink())
            self.assertFalse(fixture.state_root.exists())
            self.assertNotIn(("systemctl", "stop-listener"), fixture.ops.events)

    def test_manifest_and_payload_tampering_never_reaches_live_targets(self) -> None:
        for tamper in ("manifest", "payload"):
            with self.subTest(tamper=tamper), tempfile.TemporaryDirectory(
                prefix="pds-game-stack-"
            ) as temporary:
                fixture = Fixture(Path(temporary))
                fixture.ops.tamper = tamper
                with self.assertRaisesRegex(
                    MODULE.InstallError, "manifest seal|manifest-bound"
                ):
                    fixture.install()
                fixture.assert_preimages(self)
                self.assertEqual(fixture.ops.listener, "active")
                self.assertNotIn(("systemctl", "stop-listener"), fixture.ops.events)

    def test_resigned_payload_still_must_equal_ordinary_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.tamper = "resigned-payload"
            with self.assertRaisesRegex(MODULE.InstallError, "differs from its source"):
                fixture.install()
            fixture.assert_preimages(self)
            self.assertNotIn(("systemctl", "stop-listener"), fixture.ops.events)

    def test_after_validation_payload_race_is_refused_before_root_publish(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.tamper = "after-validate"
            with self.assertRaisesRegex(MODULE.InstallError, "changed after validation"):
                fixture.install()
            fixture.assert_preimages(self)
            self.assertEqual(fixture.ops.listener, "active")

    def test_desktop_cache_failure_rolls_back_before_runtime_commit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.fail_desktop_once = True
            with self.assertRaisesRegex(MODULE.InstallError, "desktop cache"):
                fixture.install()
            fixture.assert_preimages(self)
            self.assertEqual(fixture.ops.runtime_protocol, "legacy")
            self.assertEqual(fixture.ops.input_mode, "fresh")
            self.assertEqual(fixture.ops.listener, "active")

    def test_listener_start_failure_recovers_forward_without_mixing_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.fail_start_once = True
            transaction = fixture.install()
            self.assertTrue((transaction / "forward-recovered.json").is_file())
            self.assertFalse((transaction / "rolled-back.json").exists())
            self.assertEqual(fixture.ops.runtime_protocol, "v1")
            self.assertEqual(fixture.ops.input_mode, "gamepad")
            self.assertEqual(fixture.ops.listener, "active")
            canonical = next(
                item
                for item in fixture.artifacts
                if item.artifact_id == "input-mode-canonical"
            )
            self.assertEqual(canonical.target.read_bytes(), canonical.source.read_bytes())

    def test_persistent_post_boundary_failure_retains_complete_new_generation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.fail_start_always = True
            with self.assertRaisesRegex(MODULE.InstallError, "forward recovery"):
                fixture.install()
            transactions = list(fixture.state_root.iterdir())
            self.assertEqual(len(transactions), 1)
            self.assertTrue((transactions[0] / "forward-required.json").is_file())
            self.assertFalse((transactions[0] / "rolled-back.json").exists())
            self.assertEqual(fixture.ops.runtime_protocol, "v1")
            self.assertEqual(fixture.ops.input_mode, "gamepad")
            self.assertEqual(fixture.ops.listener, "inactive")
            for artifact in fixture.artifacts:
                if artifact.artifact_id == "retroarch-config":
                    self.assertEqual(
                        file_state(artifact.target),
                        fixture.before[artifact.artifact_id],
                    )
                elif artifact.artifact_id == "wiliwili-live-database":
                    self.assertEqual(
                        artifact.target.read_text(encoding="utf-8").count(
                            MODULE.GLFW_GUID
                        ),
                        1,
                    )
                else:
                    assert artifact.source is not None
                    self.assertEqual(
                        artifact.target.read_bytes(), artifact.source.read_bytes()
                    )

    def test_one_shot_bootstrap_failure_recovers_new_tmpfiles_protocol_forward(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.fail_bootstrap_once = True
            transaction = fixture.install()
            self.assertEqual(fixture.ops.runtime_protocol, "v1")
            self.assertEqual(fixture.ops.input_mode, "gamepad")
            self.assertEqual(fixture.ops.listener, "active")
            self.assertTrue((transaction / "forward-recovered.json").is_file())

    def test_post_boundary_signal_recovers_forward_without_file_rollback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.signal_bootstrap_once = True
            transaction = fixture.install()
            self.assertTrue((transaction / "forward-recovered.json").is_file())
            self.assertFalse((transaction / "rolled-back.json").exists())
            self.assertEqual(fixture.ops.runtime_protocol, "v1")
            self.assertEqual(fixture.ops.input_mode, "gamepad")
            self.assertEqual(fixture.ops.listener, "active")

    def test_persistent_tmpfiles_failure_keeps_new_files_for_forward_recovery(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.fail_tmpfiles_always = True
            with self.assertRaisesRegex(MODULE.InstallError, "forward recovery"):
                fixture.install()
            transaction = next(fixture.state_root.iterdir())
            self.assertTrue((transaction / "forward-required.json").is_file())
            self.assertFalse((transaction / "rolled-back.json").exists())
            self.assertEqual(fixture.ops.runtime_protocol, "v1")
            canonical = next(
                item
                for item in fixture.artifacts
                if item.artifact_id == "input-mode-canonical"
            )
            self.assertEqual(canonical.target.read_bytes(), canonical.source.read_bytes())

    def test_listener_state_drift_is_rejected_before_transaction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.listener = "inactive"
            fixture.ops.drift_listener_on_second_probe = True
            with self.assertRaisesRegex(MODULE.InstallError, "changed during preflight"):
                fixture.install()
            fixture.assert_preimages(self)
            self.assertFalse(fixture.state_root.exists())

    def test_signal_after_renames_and_second_cleanup_signal_restore_everything(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            fixture.ops.signal_after_replace = 5
            fixture.ops.signal_during_rollback = True
            with self.assertRaisesRegex(MODULE.InstallError, "signal"):
                fixture.install()
            fixture.assert_preimages(self)
            self.assertTrue(fixture.ops.rollback_signal_sent)
            self.assertEqual(fixture.ops.listener, "active")

    def test_missing_targets_are_removed_on_mid_generation_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary), existing_targets=False)
            fixture.ops.fail_after_replace = 6
            with self.assertRaisesRegex(MODULE.InstallError, "injected failure"):
                fixture.install()
            for artifact in fixture.artifacts:
                self.assertFalse(artifact.target.exists(), artifact.artifact_id)
            self.assertEqual(fixture.ops.listener, "active")

    def test_busy_game_session_lock_refuses_install_before_transaction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-game-stack-") as temporary:
            fixture = Fixture(Path(temporary))
            lock_path = fixture.runtime / "pocketds-game-session.lock"
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(MODULE.InstallError, "session is active"):
                    fixture.install()
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            fixture.assert_preimages(self)
            self.assertFalse(fixture.state_root.exists())
            self.assertNotIn(("systemctl", "stop-listener"), fixture.ops.events)

    def test_bounded_runner_terminates_hung_grandchild_process_group(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-command-tree-") as temporary:
            root = Path(temporary)
            ready = root / "ready"
            escaped = root / "escaped"
            grandchild = (
                "import pathlib,sys,time;"
                "time.sleep(0.8);pathlib.Path(sys.argv[1]).write_text('escaped')"
            )
            parent = (
                "import pathlib,subprocess,sys,time;"
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[3]]);"
                "pathlib.Path(sys.argv[2]).write_text('ready');time.sleep(60)"
            )
            with self.assertRaisesRegex(MODULE.InstallError, "timed out"):
                MODULE.HostOperations._run(
                    (sys.executable, "-c", parent, grandchild, str(ready), str(escaped)),
                    timeout=0.3,
                )
            time.sleep(1.0)
            self.assertTrue(ready.is_file())
            self.assertFalse(escaped.exists())

    def test_bounded_runner_kills_term_ignoring_grandchild_with_closed_stdio(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-command-tree-") as temporary:
            root = Path(temporary)
            ready = root / "ready"
            escaped = root / "escaped"
            grandchild = (
                "import pathlib,signal,sys,time;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "time.sleep(0.8);pathlib.Path(sys.argv[1]).write_text('escaped');"
                "time.sleep(60)"
            )
            parent = (
                "import pathlib,subprocess,sys,time;"
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[3]],"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
                "stderr=subprocess.DEVNULL);"
                "pathlib.Path(sys.argv[2]).write_text('ready');time.sleep(60)"
            )
            with self.assertRaisesRegex(MODULE.InstallError, "timed out"):
                MODULE.HostOperations._run(
                    (sys.executable, "-c", parent, grandchild, str(ready), str(escaped)),
                    timeout=0.3,
                )
            time.sleep(1.0)
            self.assertTrue(ready.is_file())
            self.assertFalse(escaped.exists())

    def test_privileged_publish_uses_exact_mv_and_file_parent_fsync(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"-T",', source)
        self.assertIn("ROOT_FSYNC_PROGRAM", source)
        self.assertNotIn('"/usr/bin/sync", "-f"', source)


if __name__ == "__main__":
    unittest.main()

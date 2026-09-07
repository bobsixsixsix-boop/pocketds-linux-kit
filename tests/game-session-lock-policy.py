#!/usr/bin/env python3
"""Keep session/input ownership in one supervisor, never in thin launchers."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SUPERVISOR = (ROOT / "components/emulation/pocketds-game-session.py").read_text(
    encoding="utf-8"
)
STEAM_DESKTOP = (ROOT / "components/steam/Steam.desktop").read_text(
    encoding="utf-8"
)
STEAM_BOOTSTRAP = (ROOT / "components/steam/launch-steam.sh").read_text(
    encoding="utf-8"
)
LAUNCHERS = {
    "Wiliwili": (ROOT / "components/wiliwili/pocketds-wiliwili").read_text(
        encoding="utf-8"
    ),
    "ES-DE": (ROOT / "components/emulation/pocketds-es-de").read_text(
        encoding="utf-8"
    ),
    "RetroArch": (ROOT / "components/emulation/pocketds-retroarch").read_text(
        encoding="utf-8"
    ),
    "Steam": (ROOT / "components/steam/pocketds-steam-session").read_text(
        encoding="utf-8"
    ),
}

for name, launcher in LAUNCHERS.items():
    assert launcher.count("POCKETDS_GAME_SESSION_SUPERVISOR") == 1, name
    assert 'exec "$supervisor"' in launcher, name
    for forbidden in (
        "busctl ",
        "restore-if-current",
        "pocketds-game-session.lock",
        "flock_bin=",
        "exec 8>",
        '"$input_mode" set',
    ):
        assert forbidden not in launcher, (name, forbidden)

assert "--backend flatpak" in LAUNCHERS["Wiliwili"]
assert "--app-id \"$app_id\"" in LAUNCHERS["Wiliwili"]
assert 'WILIWILI_BRANCH = "stable"' in SUPERVISOR
assert 'f"--branch={WILIWILI_BRANCH}"' in SUPERVISOR
assert '"--branch=master"' not in SUPERVISOR
assert "--backend native --name es-de" in LAUNCHERS["ES-DE"]
assert 'app_command=("$game_runtime" -- "${app_command[@]}")' in LAUNCHERS["ES-DE"]
assert "POCKETDS_HOST_WAYLAND_DISPLAY" in LAUNCHERS["ES-DE"]
assert "POCKETDS_GAME_SESSION_TOKEN" in LAUNCHERS["RetroArch"]
assert 'exec "$supervisor" join' in LAUNCHERS["RetroArch"]
assert "--name retroarch" in LAUNCHERS["RetroArch"]
assert "--backend native" in LAUNCHERS["Steam"]
assert "--name steam" in LAUNCHERS["Steam"]
assert '"$game_runtime" --steam --' in LAUNCHERS["Steam"]
assert '"$supervisor" probe --name steam' in LAUNCHERS["Steam"]
assert '"$pgrep_bin" -u "$uid" -x steam' in LAUNCHERS["Steam"]
assert 'exec "$steam_client" -ifrunning "$@"' in LAUNCHERS["Steam"]
assert 'exec env STEAMDECK_MODE=true "$supervisor" run' in LAUNCHERS["Steam"]
for token in (
    "--pocketds-gamepad-restart",
    "--unit=pocketds-steam-gamepad",
    "--property=SuccessExitStatus=143",
    "pocketds-steam-desktop.service",
):
    assert token in LAUNCHERS["Steam"], token
assert "--performance" not in LAUNCHERS["Steam"]
assert STEAM_DESKTOP.count("pocketds-steam-session") == 3
assert "pocketds-game-runtime" not in STEAM_DESKTOP
assert "Exec=env STEAMDECK_MODE=true $HOME/.local/bin/pocketds-steam-session %U" in STEAM_BOOTSTRAP
assert 'pocketds-steam-arm64-workarounds' in STEAM_BOOTSTRAP
for token in (
    "host_system_path=${SYSTEM_PATH:-${PATH:-/usr/local/bin:/usr/bin:/bin}}",
    '*":$HOME/.local/bin:"*) ;;',
    'host_system_path="$HOME/.local/bin:$host_system_path"',
    "export SYSTEM_PATH=$host_system_path",
):
    assert token in STEAM_BOOTSTRAP, token
assert "Exec=$HOME/.local/bin/steam %U" not in STEAM_BOOTSTRAP
assert 'app_command=("${retroarch_command[@]}")' in LAUNCHERS["RetroArch"]

for required in (
    "fcntl.flock",
    'self.input_helper, "acquire", "gamepad"',
    '"recover-token"',
    "--instance-id-fd=",
    '"--die-with-parent"',
    '"--columns=instance,application,pid"',
    'self.flatpak, "kill", self.flatpak_instance',
    '"--property=KillMode=control-group"',
    "recover_stale_record",
    "POCKETDS_GAME_SESSION_TOKEN",
    'subparsers.add_parser("stop"',
    "request_session_stop",
    "probe_native_session",
    "os.kill(owner_pid, signal.SIGTERM)",
):
    assert required in SUPERVISOR, required

assert "killall" not in SUPERVISOR
assert "pkill" not in SUPERVISOR
assert "--performance" not in LAUNCHERS["RetroArch"].split(
    'exec "$supervisor" run', 1
)[0]

print("  [OK] one supervisor owns game-session lock, input lease, and cleanup")

#!/usr/bin/env python3
"""Prevent old split installers from mixing game/input protocol generations."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MAIN = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
UNIFIED = (ROOT / "scripts/install-game-input-stack.py").read_text(encoding="utf-8")
IMPORT = (ROOT / "scripts/import-live.sh").read_text(encoding="utf-8")

assert MAIN.count('python3 "$repo_root/scripts/install-game-input-stack.py"') == 1
assert '"$repo_root/scripts/install-emulation.sh"' not in MAIN
for duplicate in (
    "/usr/share/inputplumber/profiles/pocketds-gamepad.yaml",
    "/usr/share/inputplumber/profiles/pocketds-joymouse.yaml",
    "/usr/bin/pocketds-toggle-joymouse",
    "/usr/lib/tmpfiles.d/pocketds-input-mode.conf",
    ".local/share/applications/Steam.desktop",
):
    assert duplicate not in MAIN, duplicate

for wrapper_name in (
    "install-input.sh",
    "install-emulation.sh",
    "install-wiliwili-input.sh",
    "install-wiliwili-launcher.sh",
):
    wrapper = (ROOT / "scripts" / wrapper_name).read_text(encoding="utf-8")
    assert 'exec python3 "$repo_root/scripts/install-game-input-stack.py"' in wrapper

for installed in (
    ".local/bin/pocketds-es-de",
    ".local/bin/pocketds-retroarch",
    ".local/bin/pocketds-wiliwili",
    "/usr/local/libexec/pocketds-game-session",
    "/usr/local/libexec/pocketds-gamescope-observer",
    ".local/libexec/pocketds-gamescope-inner",
    ".local/bin/pocketds-game-runtime",
    ".local/bin/pocketds-game-limit",
    ".local/bin/pocketds-steam-session",
    ".local/bin/steamos-session-select",
    ".local/share/applications/Steam.desktop",
    "kwin-gamescope-top-screen.js",
    "/usr/local/libexec/pocketds-input-mode",
    ".local/bin/pocketds-melonds",
    ".local/share/pocketds-linux-kit/emulation/es_systems-shared.xml",
    ".local/libexec/pocketds-melonds-prepare",
    ".local/libexec/pocketds-melonds-fullscreen",
    "kwin-melonds-dualscreen.js",
    "kwin-retroarch-top-screen.js",
    ".config/retroarch/pocketds.cfg",
    ".config/retroarch/config/PPSSPP/PPSSPP.opt",
    ".local/libexec/pocketds-input-mode",
    "/usr/bin/pocketds-mode-listener",
):
    assert installed in UNIFIED, installed

assert 'components/emulation/pocketds-input-mode-user' in IMPORT
assert 'components/emulation/pocketds-input-mode"' in IMPORT
assert "/usr/local/libexec/pocketds-game-session" in IMPORT
assert '"$HOME/.local/bin/pocketds-steam-session"' in IMPORT
assert '"$HOME/.local/bin/steamos-session-select"' in IMPORT
assert "components/inputplumber/pocketds-mode-listener.py" in IMPORT
assert "/etc/systemd/system/pocketds-mode-listener.service.d/20-input-state.conf" in IMPORT
assert "$HOME/ES-DE/settings/es_settings.xml" not in IMPORT
assert "$HOME/ES-DE/custom_systems/es_systems.xml" not in IMPORT

print("  [OK] main, compatibility, and import paths cannot mix protocol generations")

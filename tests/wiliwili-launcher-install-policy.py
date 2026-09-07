#!/usr/bin/env python3
"""Require the desktop entry, mapping, and supervised launcher to commit together."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNIFIED = (ROOT / "scripts/install-game-input-stack.py").read_text(encoding="utf-8")
WRAPPER = (ROOT / "scripts/install-wiliwili-launcher.sh").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "components/wiliwili/pocketds-wiliwili").read_text(
    encoding="utf-8"
)
DESKTOP = (
    ROOT / "components/wiliwili/cn.xfangfang.wiliwili.desktop"
).read_text(encoding="utf-8")

assert 'exec python3 "$repo_root/scripts/install-game-input-stack.py"' in WRAPPER
assert 'exec "$supervisor" run' in LAUNCHER
assert "--backend flatpak" in LAUNCHER
assert "--app-id \"$app_id\"" in LAUNCHER
assert "--check-existing" in LAUNCHER
for forbidden in ("restore-if-current", "flock_bin=", '"$input_mode" set'):
    assert forbidden not in LAUNCHER, forbidden

for token in (
    '"wiliwili-launcher"',
    '"wiliwili-desktop"',
    '"game-session-supervisor"',
    'item.phase == "desktop" for item in artifacts[-3:]',
):
    assert token in UNIFIED, token

assert "Exec=/home/pocketds/.local/bin/pocketds-wiliwili" in DESKTOP
assert "TryExec=/home/pocketds/.local/bin/pocketds-wiliwili" in DESKTOP
assert "X-Flatpak=cn.xfangfang.wiliwili" in DESKTOP
assert "Terminal=false" in DESKTOP

print("  [OK] Wiliwili desktop publishes only the supervised whole-stack launcher")

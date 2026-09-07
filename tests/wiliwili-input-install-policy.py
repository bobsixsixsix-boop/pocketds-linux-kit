#!/usr/bin/env python3
"""Keep Wiliwili's exact GLFW mapping in the unified transaction."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNIFIED = (ROOT / "scripts/install-game-input-stack.py").read_text(encoding="utf-8")
WRAPPER = (ROOT / "scripts/install-wiliwili-input.sh").read_text(encoding="utf-8")

assert 'exec python3 "$repo_root/scripts/install-game-input-stack.py"' in WRAPPER
for forbidden in ("flatpak ", "flock ", "cp -a", "mv -f", "sudo "):
    assert forbidden not in WRAPPER, forbidden

for required in (
    '"wiliwili-mapping-tool"',
    '"wiliwili-mapping-source"',
    '"wiliwili-live-database"',
    "components/wiliwili/gamecontrollerdb.txt",
    ".var/app/cn.xfangfang.wiliwili/config/wiliwili/gamecontrollerdb.txt",
    "def _merge_mapping",
    "merged Wiliwili database did not de-duplicate the GUID",
    "EXPECTED_GLFW_BINDINGS",
):
    assert required in UNIFIED, required

print("  [OK] Wiliwili mapping is merge-safe and bound to the unified generation")

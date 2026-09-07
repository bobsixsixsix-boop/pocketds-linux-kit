#!/usr/bin/env python3
"""Fault-injection tests for independent brightness persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent.parent
PROGRAM = ROOT / "components" / "brightness" / "pocketds-brightness.py"


def run(env: dict[str, str], *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(PROGRAM), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == expected, (args, result.stdout, result.stderr)
    return result


with tempfile.TemporaryDirectory(prefix="pocketds-brightness-") as temp:
    root = Path(temp)
    top = root / "top"
    bottom = root / "bottom"
    for path, maximum, value in ((top, 4096, 2293), (bottom, 4080, 2774)):
        path.mkdir()
        (path / "max_brightness").write_text(f"{maximum}\n", encoding="utf-8")
        (path / "brightness").write_text(f"{value}\n", encoding="utf-8")

    helper = root / "fake-panel-root.py"
    helper.write_text(
        """#!/usr/bin/env python3
import os
from pathlib import Path
import sys
assert sys.argv[1] == "brightness"
screen, percent = sys.argv[2], int(sys.argv[3])
path = Path(os.environ["POCKETDS_TOP_BACKLIGHT" if screen == "top" else "POCKETDS_BOTTOM_BACKLIGHT"])
maximum = int((path / "max_brightness").read_text())
(path / "brightness").write_text(f"{maximum * percent // 100}\\n")
""",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    state = root / "config" / "brightness.json"
    env = os.environ.copy()
    env.update(
        {
            "POCKETDS_TOP_BACKLIGHT": str(top),
            "POCKETDS_BOTTOM_BACKLIGHT": str(bottom),
            "POCKETDS_BRIGHTNESS_STATE": str(state),
            "POCKETDS_BRIGHTNESS_ROOT_HELPER": str(helper),
        }
    )

    initialized = json.loads(run(env, "initialize", "--capture-current").stdout)
    assert initialized["state"]["top_percent"] == 56
    assert initialized["state"]["bottom_percent"] == 68
    assert stat.S_IMODE(state.stat().st_mode) == 0o600

    run(env, "set", "top", "64")
    assert int((top / "brightness").read_text()) == 4096 * 64 // 100
    assert json.loads(state.read_text())["top_percent"] == 64
    before = (top / "brightness").read_text()
    run(env, "set", "top", "4", expected=2)
    assert (top / "brightness").read_text() == before

    (top / "brightness").write_text("4096\n", encoding="utf-8")
    dry_run = json.loads(run(env, "reconcile", "--dry-run").stdout)
    assert dry_run["drift"]["top"]["target_percent"] == 64
    assert int((top / "brightness").read_text()) == 4096
    applied = json.loads(run(env, "reconcile").stdout)
    assert applied["applied"] == ["top"]
    assert int((top / "brightness").read_text()) == 4096 * 64 // 100

    state.write_text("not json\n", encoding="utf-8")
    recovered = json.loads(run(env, "initialize", "--capture-current").stdout)
    assert recovered["state"]["top_percent"] == 64
    assert list(state.parent.glob("brightness.json.invalid-*"))

    state.unlink()
    defaults = json.loads(run(env, "initialize").stdout)
    assert defaults["state"]["top_percent"] == 60
    assert defaults["state"]["bottom_percent"] == 60
    result = json.loads(run(env, "reconcile").stdout)
    assert result["applied"] == ["top", "bottom"]
    assert int((top / "brightness").read_text()) == 4096 * 60 // 100
    assert int((bottom / "brightness").read_text()) == 4080 * 60 // 100

print("  [OK] brightness capture/default/set/drift/corruption state machine")

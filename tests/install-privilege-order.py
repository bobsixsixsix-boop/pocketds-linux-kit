#!/usr/bin/env python3
"""Keep the interactive installer fail-closed around its root trust boundary."""

from pathlib import Path
import os
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")

preflight = installer.index("sudo -n true")
asset_preflight = installer.index("pds020-install-asset-preflight.py")
helper = installer.index(
    'install_root_file "$repo_root/components/control-panel/pocketds-panel-root"'
)
visudo = installer.index(
    'sudo visudo -cf "$repo_root/components/control-panel/90-pocketds-linux-kit"'
)
policy = installer.index(
    'install_root_file "$repo_root/components/control-panel/90-pocketds-linux-kit"'
)

assert preflight < asset_preflight
assert helper < visudo < policy
assert installer.count(
    'install_root_file "$repo_root/components/control-panel/pocketds-panel-root"'
) == 1
assert "for package in tuned plasma-milou" in installer

command_start = installer.index("for command in ")
command_end = installer.index("\ndone", command_start) + len("\ndone")
command_preflight = installer[command_start:command_end]
assert command_end < preflight < installer.index("install_apps\n")
mock_command = '''
command() {
    [[ $1 == -v ]] || return 99
    [[ $2 != "$TEST_MISSING_COMMAND" ]]
}
'''
for missing in ("pactl", "parecord", "arecord", ""):
    result = subprocess.run(
        ["/bin/bash", "-c", "set -Eeuo pipefail\n" + mock_command + command_preflight
         + "\nprintf 'preflight passed\\n'"],
        env={**os.environ, "TEST_MISSING_COMMAND": missing},
        text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == (1 if missing else 0), (missing, result)
    if missing:
        assert f"Missing required command: {missing}" in result.stderr
        assert "preflight passed" not in result.stdout

check = (ROOT / "scripts/check.sh").read_text(encoding="utf-8")
audio_start = check.index("for audio_command in ")
audio_end = check.index("\ndone", audio_start) + len("\ndone")
for missing, package in (("pactl", "pulseaudio-utils"), ("parecord", "pulseaudio-utils"),
                         ("arecord", "alsa-utils")):
    result = subprocess.run(
        ["/bin/bash", "-c", "set -Eeuo pipefail\n" + mock_command
         + '\npass() { :; }\nwarn() { printf "%s\\n" "$*"; }\n'
         + check[audio_start:audio_end]],
        env={**os.environ, "TEST_MISSING_COMMAND": missing},
        text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"voice input command {missing} is missing; install {package}"

codex_header = 'if python3 - "$repo_root/components/codex-quota/pocketds-codex-quota" <<\'PY\'\n'
codex_probe = check.split(codex_header, 1)[1].split("\nPY\n", 1)[0]
with tempfile.TemporaryDirectory(prefix="pds-codex-check-") as temporary:
    fake = Path(temporary) / "codex"
    fake.write_text("#!/bin/sh\nprintf 'UNEXPECTED_ACCOUNT_CALL'\n", encoding="utf-8")
    fake.chmod(0o755)
    for binary, expected in ((fake, 0), (Path(temporary) / "missing", 1)):
        result = subprocess.run(
            [sys.executable, "-c", codex_probe,
             str(ROOT / "components/codex-quota/pocketds-codex-quota")],
            env={**os.environ, "POCKETDS_CODEX_BIN": str(binary)},
            text=True, capture_output=True, timeout=5,
        )
        assert result.returncode == expected, result
        assert result.stdout == result.stderr == "", result

print("  [OK] installer verifies sudo and installs the validator before its policy")
print("  [OK] missing recording tools fail before install and have actionable diagnostics")
print("  [OK] Codex discovery reuses the collector without executing it or exposing account data")

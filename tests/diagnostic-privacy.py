#!/usr/bin/env python3
"""Test the diagnostic allowlist, policy and identifier redaction."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "pocketds_diagnostic_redact", ROOT / "scripts/pocketds-diagnostic-redact.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

canary = """\
home=/home/private-user/.config
ipv4=192.0.2.44 invalid=999.999.999.999
ipv6=2001:db8::44
mac=02:11:22:33:44:55
uuid=123e4567-e89b-12d3-a456-426614174000
machine=0123456789abcdef0123456789abcdef
serial: PRIVATE-SERIAL-42
usb=kernel: Serial Number: PRIVATE USB SERIAL WITH SPACES
host=private-pocket-host user=private-local-user
sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
"""
redacted = module.redact(canary, {"private-pocket-host", "private-local-user"})
for secret in (
    "private-user", "192.0.2.44", "2001:db8::44", "02:11:22:33:44:55",
    "123e4567-e89b-12d3-a456-426614174000",
    "0123456789abcdef0123456789abcdef", "PRIVATE-SERIAL-42",
    "PRIVATE USB SERIAL WITH SPACES", "private-pocket-host", "private-local-user",
):
    assert secret not in redacted
assert "999.999.999.999" in redacted
assert "a" * 64 in redacted

policy = json.loads(
    (ROOT / "components/system/diagnostic-policy.json").read_text(encoding="utf-8")
)
assert policy["default_deny"] is True
assert policy["local_only"] is True
assert policy["automatic_upload"] is False
excluded = set(policy["diagnostics"]["exclude"])
assert {
    "credentials", "tokens", "cookies", "browser_profiles",
    "network_configuration", "keyboard_asr_logs", "rom_content",
    "rom_filenames", "saves", "account_data",
}.issubset(excluded)

collector = (ROOT / "scripts/diagnostic-bundle.sh").read_text(encoding="utf-8")
for forbidden in (
    "upower -d", "nmcli", "iw dev", "pocketds-codex-quota",
    "-u pocketds-keyboard.service", "ROMs", "Saves", "browser profile",
):
    assert forbidden not in collector
assert "status --short" not in collector
assert "status --porcelain" in collector
assert "contents.sha256" in collector
for hardening in (
    "mktemp -d", "ulimit -f", "timeout --signal=TERM", "-size +8388608c",
    "--owner=0", "--group=0", "--numeric-owner", "--mtime=@0",
    'cd "$output_root"', 'sha256sum "$(basename "$archive")"',
    "mv -T", "set -o noclobber",
):
    assert hardening in collector
assert 'mkdir -p "$bundle_dir"' not in collector
assert collector.index('if [[ -L $output_root ]]') < collector.index(
    'mkdir -p "$output_root"'
)

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "canary.txt"
    path.write_text(canary, encoding="utf-8")
    module.redact_file(path, {"private-pocket-host", "private-local-user"})
    result = path.read_text(encoding="utf-8")
    assert "private-user" not in result
    assert "PRIVATE USB SERIAL WITH SPACES" not in result
    assert os.stat(path).st_mode & 0o777 == 0o644

    symlink = Path(temporary) / "symlink.txt"
    symlink.symlink_to(path)
    try:
        module.redact_file(symlink)
    except RuntimeError:
        pass
    else:
        raise AssertionError("symlinked diagnostic input was accepted")

    hardlink = Path(temporary) / "hardlink.txt"
    os.link(path, hardlink)
    try:
        module.redact_file(path)
    except RuntimeError:
        pass
    else:
        raise AssertionError("multiply linked diagnostic input was accepted")

with tempfile.TemporaryDirectory() as temporary:
    oversized = Path(temporary) / "oversized.txt"
    with oversized.open("wb") as stream:
        stream.truncate(module.MAX_FILE_BYTES + 1)
    try:
        module.redact_file(oversized)
    except RuntimeError:
        pass
    else:
        raise AssertionError("oversized diagnostic input was accepted")

print("  [OK] diagnostics are allowlisted, local-only and identifier-redacted")

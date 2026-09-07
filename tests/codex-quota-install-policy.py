#!/usr/bin/env python3
"""Exercise the focused quota installer's transactional failure paths."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts/install-codex-quota.sh"
INSTALLER_TEXT = INSTALLER.read_text(encoding="utf-8")

for token in (
    "--activate",
    "tests/codex-quota.py",
    "pocketds-codex-quota.service",
    "pocketds-codex-quota.timer",
    "pocketds-codex-quota-verify.py",
    "quota_cache",
    "ROLLBACK FAILED",
    "trap 'handle_failure 143' TERM",
    "installed but not activated",
    "--maximum-age 360",
):
    assert token in INSTALLER_TEXT, token
for forbidden in ("sudo", "plasmashell", "pocketds-keyboard", "gpu-telemetry"):
    assert forbidden not in INSTALLER_TEXT, forbidden

REAL_INSTALL = shutil.which("install")
assert REAL_INSTALL


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def fixture(
    root: Path,
) -> tuple[dict[str, str], list[Path], list[bytes], list[int], Path, bytes, int]:
    home = root / "home"
    mockbin = root / "mockbin"
    home.mkdir()
    mockbin.mkdir()
    targets = [
        home / ".local/bin/pocketds-codex-quota",
        home / ".config/systemd/user/pocketds-codex-quota.service",
        home / ".config/systemd/user/pocketds-codex-quota.timer",
    ]
    old = [b"old collector\n", b"old service\n", b"old timer\n"]
    modes = [0o711, 0o640, 0o600]
    for path, content, mode in zip(targets, old, modes):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(mode)

    runtime = root / "runtime"
    runtime.mkdir(mode=0o700)
    cache = runtime / "pocketds-codex-quota.json"
    old_cache = b"old quota cache\n"
    cache_mode = 0o640
    cache.write_bytes(old_cache)
    cache.chmod(cache_mode)

    state_path = root / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "timer_enabled": True,
                "timer_active": False,
                "service_enabled": False,
                "service_active": True,
            }
        ),
        encoding="utf-8",
    )

    write_executable(
        mockbin / "python3",
        f"""#!/usr/bin/env bash
case "${{1:-}}" in
  */tests/codex-quota.py) exit 0 ;;
  */scripts/pocketds-codex-quota-verify.py)
    [[ "${{MOCK_VERIFY_FAIL:-0}}" == 1 ]] && exit 74
    exit 0
    ;;
esac
exec {shlex_quote(sys.executable)} "$@"
""",
    )
    write_executable(
        mockbin / "install",
        f"""#!/usr/bin/env bash
count=0
[[ -f "$MOCK_INSTALL_COUNT" ]] && read -r count < "$MOCK_INSTALL_COUNT"
count=$((count + 1))
printf '%s\n' "$count" > "$MOCK_INSTALL_COUNT"
[[ "${{MOCK_INSTALL_FAIL_AT:-0}}" == "$count" ]] && exit 73
exec {shlex_quote(REAL_INSTALL)} "$@"
""",
    )
    systemctl_code = f"""#!{sys.executable}
import json, os, pathlib, sys, time
path = pathlib.Path(os.environ['MOCK_SYSTEMCTL_STATE'])
state = json.loads(path.read_text())
args = sys.argv[1:]
if args and args[0] == '--user': args = args[1:]
joined = ' '.join(args)
needle = os.environ.get('MOCK_SYSTEMCTL_FAIL_MATCH', '')
marker = pathlib.Path(os.environ['MOCK_SYSTEMCTL_FAIL_MARKER'])
if needle and needle in joined and (os.environ.get('MOCK_FAIL_ALWAYS') == '1' or not marker.exists()):
    marker.touch()
    raise SystemExit(75)
def key(unit, suffix):
    return ('timer' if unit.endswith('.timer') else 'service') + '_' + suffix
if args[:2] == ['is-enabled', '--quiet']:
    raise SystemExit(0 if state[key(args[2], 'enabled')] else 1)
if args[:2] == ['is-active', '--quiet']:
    raise SystemExit(0 if state[key(args[2], 'active')] else 1)
if args == ['daemon-reload']:
    raise SystemExit(0)
command = args[0]
rest = args[1:]
now = '--now' in rest
rest = [value for value in rest if value != '--now']
unit = rest[-1]
if command == 'enable':
    state[key(unit, 'enabled')] = True
    if now: state[key(unit, 'active')] = True
elif command == 'disable': state[key(unit, 'enabled')] = False
elif command in ('start', 'restart'): state[key(unit, 'active')] = True
elif command == 'stop': state[key(unit, 'active')] = False
else: raise SystemExit(64)
path.write_text(json.dumps(state))
cache = os.environ.get('MOCK_CACHE')
if cache and command in ('start', 'restart') and unit.endswith('.service'):
    cache_path = pathlib.Path(cache)
    cache_path.write_bytes(b'new quota cache\\n')
    cache_path.chmod(0o600)
block = os.environ.get('MOCK_SYSTEMCTL_BLOCK_MATCH', '')
block_marker = pathlib.Path(os.environ['MOCK_SYSTEMCTL_BLOCK_MARKER'])
if block and block in joined and not block_marker.exists():
    block_marker.touch()
    time.sleep(60)
"""
    write_executable(mockbin / "systemctl", systemctl_code)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "XDG_RUNTIME_DIR": str(runtime),
            "PATH": str(mockbin) + os.pathsep + env["PATH"],
            "MOCK_INSTALL_COUNT": str(root / "install-count"),
            "MOCK_SYSTEMCTL_STATE": str(state_path),
            "MOCK_SYSTEMCTL_FAIL_MARKER": str(root / "systemctl-failed"),
            "MOCK_SYSTEMCTL_BLOCK_MARKER": str(root / "systemctl-blocked"),
            "MOCK_CACHE": str(cache),
        }
    )
    return env, targets, old, modes, cache, old_cache, cache_mode


def assert_restored(targets: list[Path], old: list[bytes], modes: list[int]) -> None:
    for path, content, mode in zip(targets, old, modes):
        assert path.read_bytes() == content, path
        assert stat.S_IMODE(path.stat().st_mode) == mode, path


def run_failure(
    name: str,
    updates: dict[str, str],
    expected_status: int,
    *,
    missing_index: int | None = None,
    missing_cache: bool = False,
) -> None:
    with tempfile.TemporaryDirectory(prefix=f"quota-{name}-") as raw:
        root = Path(raw)
        env, targets, old, modes, cache, old_cache, cache_mode = fixture(root)
        if missing_index is not None:
            targets[missing_index].unlink()
        if missing_cache:
            cache.unlink()
        env.update(updates)
        result = subprocess.run(
            [str(INSTALLER), "--activate"], env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
        )
        assert result.returncode == expected_status, (name, result.returncode, result.stderr)
        if missing_index is None:
            assert_restored(targets, old, modes)
        else:
            assert not targets[missing_index].exists()
            assert_restored(
                [path for index, path in enumerate(targets) if index != missing_index],
                [value for index, value in enumerate(old) if index != missing_index],
                [value for index, value in enumerate(modes) if index != missing_index],
            )
        state = json.loads((root / "state.json").read_text(encoding="utf-8"))
        assert state == {
            "timer_enabled": True,
            "timer_active": False,
            "service_enabled": False,
            "service_active": True,
        }, (name, state)
        if missing_cache:
            assert not cache.exists(), name
        else:
            assert cache.read_bytes() == old_cache, name
            assert stat.S_IMODE(cache.stat().st_mode) == cache_mode, name
        if expected_status == 3:
            assert "ROLLBACK FAILED" in result.stderr
        else:
            assert "rollback complete" in result.stderr


run_failure("copy", {"MOCK_INSTALL_FAIL_AT": "2"}, 73)
run_failure("missing-copy", {"MOCK_INSTALL_FAIL_AT": "3"}, 73, missing_index=1)
run_failure("reload", {"MOCK_SYSTEMCTL_FAIL_MATCH": "daemon-reload"}, 75)
run_failure("enable", {"MOCK_SYSTEMCTL_FAIL_MATCH": "enable --now"}, 75)
run_failure("restart", {"MOCK_SYSTEMCTL_FAIL_MATCH": "restart pocketds"}, 75)
run_failure("verify", {"MOCK_VERIFY_FAIL": "1"}, 74)
run_failure("missing-cache", {"MOCK_VERIFY_FAIL": "1"}, 74, missing_cache=True)
run_failure(
    "rollback-failure",
    {"MOCK_SYSTEMCTL_FAIL_MATCH": "daemon-reload", "MOCK_FAIL_ALWAYS": "1"},
    3,
)


with tempfile.TemporaryDirectory(prefix="quota-signal-") as raw:
    root = Path(raw)
    env, targets, old, modes, cache, old_cache, cache_mode = fixture(root)
    env["MOCK_SYSTEMCTL_BLOCK_MATCH"] = "restart pocketds-codex-quota.service"
    process = subprocess.Popen(
        [str(INSTALLER), "--activate"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    marker = Path(env["MOCK_SYSTEMCTL_BLOCK_MARKER"])
    for _attempt in range(200):
        if marker.exists():
            break
        if process.poll() is not None:
            break
        time.sleep(0.02)
    assert marker.exists(), "installer never reached the interrupt gate"
    os.killpg(process.pid, signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode != 0, (process.returncode, stdout, stderr)
    assert "rollback complete" in stderr, stderr
    assert_restored(targets, old, modes)
    assert cache.read_bytes() == old_cache
    assert stat.S_IMODE(cache.stat().st_mode) == cache_mode
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    assert state == {
        "timer_enabled": True,
        "timer_active": False,
        "service_enabled": False,
        "service_active": True,
    }, state

print("  [OK] quota install restores files, cache and unit state across errors/signals")

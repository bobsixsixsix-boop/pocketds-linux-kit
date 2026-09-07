#!/usr/bin/env python3
"""Focused all-mock acceptance tests for pocketds-game-session.py."""

from __future__ import annotations

import json
import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import time
from unittest import mock
from pathlib import Path
from typing import Dict, List, Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = REPO_ROOT / "components/emulation/pocketds-game-session.py"
RECEIPT_TOKEN = "11111111-1111-4111-8111-111111111111"
APP_ID = "cn.xfangfang.wiliwili"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def wait_for(path: Path, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path}")


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_dead(pid: int, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_is_alive(pid):
            return
        time.sleep(0.02)
    raise AssertionError(f"process {pid} survived exact process-group cleanup")


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


class Fixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="pds-game-session-")
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.runtime = self.root / "runtime"
        self.proc = self.root / "proc"
        self.cgroup = self.root / "cgroup"
        self.logs = self.root / "logs"
        for directory in (self.bin, self.runtime, self.proc, self.cgroup, self.logs):
            directory.mkdir(mode=0o700)
        (self.cgroup / "mock/session").mkdir(parents=True)
        (self.cgroup / "mock/session/cgroup.events").write_text(
            "populated 0\n", encoding="ascii"
        )
        self.input_state = self.root / "input.state"
        self.input_state.write_text("joymouse\n", encoding="ascii")
        self.power_state = self.logs / "power.state"
        self.power_state.write_text("balanced\n", encoding="ascii")
        self.env = os.environ.copy()
        self.env.update(
            {
                "PYTHON": sys.executable,
                "MOCK_SUPERVISOR": str(SUPERVISOR),
                "MOCK_PROC_ROOT": str(self.proc),
                "MOCK_INPUT_STATE": str(self.input_state),
                "MOCK_INPUT_LOG": str(self.logs / "input.log"),
                "MOCK_INPUT_MARKER": str(self.logs / "input.marker"),
                "MOCK_INPUT_GRANDCHILD": str(self.logs / "input-grandchild.pid"),
                "MOCK_RESTORE_MARKER": str(self.logs / "restore.marker"),
                "MOCK_RESTORE_GRANDCHILD": str(
                    self.logs / "restore-grandchild.pid"
                ),
                "MOCK_SYSTEMD_LOG": str(self.logs / "systemd.log"),
                "MOCK_SYSTEMCTL_LOG": str(self.logs / "systemctl.log"),
                "MOCK_NATIVE_PID": str(self.logs / "native.pid"),
                "MOCK_NATIVE_MARKER": str(self.logs / "native.marker"),
                "MOCK_CGROUP_EVENTS": str(
                    self.cgroup / "mock/session/cgroup.events"
                ),
                "MOCK_RETROARCH_LOG": str(self.logs / "retroarch.log"),
                "MOCK_TREE_MARKER": str(self.logs / "tree.marker"),
                "MOCK_TREE_CHILD": str(self.logs / "tree-child.pid"),
                "MOCK_TREE_GRANDCHILD": str(
                    self.logs / "tree-grandchild.pid"
                ),
                "MOCK_FLATPAK_LOG": str(self.logs / "flatpak.log"),
                "MOCK_FLATPAK_PID": str(self.logs / "flatpak.pid"),
                "MOCK_FLATPAK_MARKER": str(self.logs / "flatpak.marker"),
                "MOCK_FLATPAK_AMBIGUOUS_MARKER": str(
                    self.logs / "flatpak-ambiguous.marker"
                ),
                "MOCK_POWER_LOG": str(self.logs / "power.log"),
                "MOCK_POWER_MARKER": str(self.logs / "power.marker"),
                "MOCK_POWER_GRANDCHILD": str(
                    self.logs / "power-grandchild.pid"
                ),
                "MOCK_POWER_STATE": str(self.logs / "power.state"),
                "XDG_RUNTIME_DIR": str(self.runtime),
                "POCKETDS_GAME_SESSION_LOCK": str(
                    self.runtime / "pocketds-game-session.lock"
                ),
                "POCKETDS_GAME_SESSION_DIR": str(
                    self.runtime / "pocketds-game-session"
                ),
                "POCKETDS_INPUT_MODE": str(self.bin / "input-mode"),
                "POCKETDS_SYSTEMD_RUN": str(self.bin / "systemd-run"),
                "POCKETDS_SYSTEMCTL": str(self.bin / "systemctl"),
                "POCKETDS_FLATPAK": str(self.bin / "flatpak"),
                "POCKETDS_TUNED_ADM": str(self.bin / "tuned-adm"),
                "POCKETDS_PANELCTL": str(self.bin / "panelctl"),
                "POCKETDS_PROC_ROOT": str(self.proc),
                "POCKETDS_CGROUP_ROOT": str(self.cgroup),
                "POCKETDS_CONTROL_TIMEOUT": "0.8",
                "POCKETDS_INPUT_ACQUIRE_TIMEOUT": "1.2",
                "POCKETDS_START_TIMEOUT": "1.2",
                "POCKETDS_TERM_TIMEOUT": "0.2",
            }
        )
        self._install_mocks()

    def close(self) -> None:
        self.temporary.cleanup()

    def _install_mocks(self) -> None:
        write_executable(
            self.bin / "make-proc",
            r'''#!/bin/sh
set -eu
pid=$1
cgroup=$2
directory="$MOCK_PROC_ROOT/$pid"
mkdir -p "$directory"
{
    printf '%s (mock) S' "$pid"
    count=0
    while [ "$count" -lt 18 ]; do
        printf ' 0'
        count=$((count + 1))
    done
    printf ' %s\n' "$((pid + 700000))"
} >"$directory/stat"
printf '0::%s\n' "$cgroup" >"$directory/cgroup"
''',
        )
        write_executable(
            self.bin / "supervisor-wrapper",
            r'''#!/bin/sh
set -eu
"$MOCK_MAKE_PROC" "$$" /mock/owner
exec "$PYTHON" "$MOCK_SUPERVISOR" "$@"
''',
        )
        write_executable(
            self.bin / "join-wrapper",
            r'''#!/bin/sh
set -eu
"$MOCK_MAKE_PROC" "$$" /mock/session
exec "$PYTHON" "$MOCK_SUPERVISOR" "$@"
''',
        )
        self.env["MOCK_MAKE_PROC"] = str(self.bin / "make-proc")
        write_executable(
            self.bin / "input-mode",
            f'''#!/bin/sh
set -eu
case "${{1:-}}" in
    acquire)
        [ "$#" -eq 2 ] && [ "$2" = gamepad ]
        printf 'acquire gamepad\n' >>"$MOCK_INPUT_LOG"
        previous=$(cat "$MOCK_INPUT_STATE")
        if [ "${{MOCK_ACQUIRE_MODE:-normal}}" = block-before ]; then
            printf 'before\n' >"$MOCK_INPUT_MARKER"
            sleep 30 &
            printf '%s\n' "$!" >"$MOCK_INPUT_GRANDCHILD"
            trap 'exit 143' TERM
            while :; do sleep 1; done
        fi
        printf 'pds-input-v1 %s {RECEIPT_TOKEN}\n' "$previous"
        printf 'gamepad\n' >"$MOCK_INPUT_STATE"
        if [ "${{MOCK_ACQUIRE_MODE:-normal}}" = block-after ]; then
            printf 'after\n' >"$MOCK_INPUT_MARKER"
            sleep 30 &
            printf '%s\n' "$!" >"$MOCK_INPUT_GRANDCHILD"
            trap 'exit 143' TERM
            while :; do sleep 1; done
        fi
        ;;
    recover-token)
        [ "$#" -eq 3 ]
        printf 'recover-token %s %s\n' "$2" "$3" >>"$MOCK_INPUT_LOG"
        [ "$2" = {RECEIPT_TOKEN} ]
        [ "$(cat "$MOCK_CGROUP_EVENTS")" = "populated 0" ]
        [ ! -s "$MOCK_FLATPAK_PID" ]
        if [ "${{MOCK_RESTORE_HANG:-0}}" = 1 ]; then
            sleep 30 &
            printf '%s\n' "$!" >"$MOCK_RESTORE_GRANDCHILD"
            printf 'hung\n' >"$MOCK_RESTORE_MARKER"
            while :; do sleep 1; done
        fi
        if [ "${{MOCK_FAIL_RESTORE:-0}}" = 1 ]; then
            exit 43
        fi
        if [ "${{MOCK_RECOVER_RECEIPT:-valid}}" = invalid ]; then
            printf 'pds-input-recover-v1 restored extra\n'
            exit 0
        fi
        if [ "${{MOCK_RECOVER_OUTCOME:-}}" = superseded ]; then
            printf 'pds-input-recover-v1 superseded\n'
            exit 0
        fi
        if [ "$(cat "$MOCK_INPUT_STATE")" = "$3" ]; then
            printf 'pds-input-recover-v1 already-target\n'
        else
            printf '%s\n' "$3" >"$MOCK_INPUT_STATE"
            printf 'pds-input-recover-v1 restored\n'
        fi
        ;;
    *) exit 2 ;;
esac
''',
        )
        write_executable(
            self.bin / "systemd-run",
            r'''#!/bin/sh
set -eu
printf '%s\n' "$*" >>"$MOCK_SYSTEMD_LOG"
if [ "${MOCK_SYSTEMD_NO_SCOPE:-0}" = 1 ]; then
    exit 7
fi
while [ "$#" -gt 0 ] && [ "$1" != -- ]; do shift; done
[ "$#" -gt 1 ]
shift
printf 'populated 1\n' >"$MOCK_CGROUP_EVENTS"
set +e
"$@" &
child=$!
set -e
"$MOCK_MAKE_PROC" "$child" /mock/session
printf '%s\n' "$child" >"$MOCK_NATIVE_PID"
if [ "${MOCK_SYSTEMD_DETACH:-0}" = 1 ]; then
    exit 0
fi
set +e
wait "$child"
status=$?
set -e
printf 'populated 0\n' >"$MOCK_CGROUP_EVENTS"
exit "$status"
''',
        )
        write_executable(
            self.bin / "systemctl",
            r'''#!/bin/sh
set -eu
printf '%s\n' "$*" >>"$MOCK_SYSTEMCTL_LOG"
[ "${1:-}" = --user ]
action=${2:-}
case "$action" in
    show)
        missing=0
        if [ "${MOCK_SYSTEMD_NO_SCOPE:-0}" = 1 ]; then
            missing=1
        elif [ -n "${MOCK_MISSING_UNIT:-}" ]; then
            case " $* " in *" $MOCK_MISSING_UNIT "*) missing=1 ;; esac
        fi
        case " $* " in
            *" --property=LoadState "*)
                if [ "$missing" = 1 ]; then
                    printf 'LoadState=not-found\nActiveState=inactive\nControlGroup=\n'
                else
                    printf 'LoadState=loaded\nActiveState=active\nControlGroup=/mock/session\n'
                fi
                ;;
            *)
                [ "$missing" != 1 ] || exit 1
                printf '/mock/session\n'
                ;;
        esac
        ;;
    stop)
        pid=$(cat "$MOCK_NATIVE_PID")
        kill -TERM "$pid" 2>/dev/null || true
        ;;
    kill)
        pid=$(cat "$MOCK_NATIVE_PID")
        kill -KILL "$pid" 2>/dev/null || true
        printf 'populated 0\n' >"$MOCK_CGROUP_EVENTS"
        ;;
    *) exit 2 ;;
esac
''',
        )
        write_executable(
            self.bin / "nested-entry",
            r'''#!/bin/sh
set -eu
exec "$PYTHON" "$MOCK_SUPERVISOR" join \
    --token "$POCKETDS_GAME_SESSION_TOKEN" -- "$MOCK_RETROARCH"
''',
        )
        self.env["MOCK_RETROARCH"] = str(self.bin / "retroarch")
        write_executable(
            self.bin / "retroarch",
            r'''#!/bin/sh
set -eu
[ "${POCKETDS_GAME_SESSION_JOINED:-}" = 1 ]
printf 'joined-retroarch\n' >>"$MOCK_RETROARCH_LOG"
''',
        )
        write_executable(
            self.bin / "native-ignore-term",
            r'''#!/usr/bin/env python3
import os
import signal
import time
from pathlib import Path
Path(os.environ["MOCK_NATIVE_MARKER"]).write_text("running\n")
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    time.sleep(0.05)
''',
        )
        write_executable(
            self.bin / "retroarch-tree",
            r'''#!/usr/bin/env python3
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

grandchild = subprocess.Popen([
    sys.executable,
    "-c",
    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
])
Path(os.environ["MOCK_TREE_CHILD"]).write_text(str(os.getpid()) + "\n")
Path(os.environ["MOCK_TREE_GRANDCHILD"]).write_text(str(grandchild.pid) + "\n")
Path(os.environ["MOCK_TREE_MARKER"]).write_text("running\n")
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    time.sleep(0.05)
''',
        )
        write_executable(
            self.bin / "native-quick",
            "#!/bin/sh\nexit 0\n",
        )
        write_executable(
            self.bin / "native-switch-power",
            "#!/bin/sh\nprintf 'powersave\\n' >\"$MOCK_POWER_STATE\"\n",
        )
        write_executable(
            self.bin / "flatpak",
            f'''#!/bin/sh
set -eu
printf '%s\n' "$*" >>"$MOCK_FLATPAK_LOG"
case "${{1:-}}" in
    ps)
        [ "${{MOCK_FLATPAK_PS_FAIL:-0}}" != 1 ] || exit 31
        if [ "${{MOCK_FLATPAK_PS_FAIL_AFTER_RUN:-0}}" = 1 ] && [ -s "$MOCK_FLATPAK_MARKER" ]; then
            exit 31
        fi
        printf 'Instance Application Pid\n'
        if [ -s "$MOCK_FLATPAK_PID" ]; then
            instance=${{MOCK_FLATPAK_INSTANCE:-1001}}
            application={APP_ID}
            [ "${{MOCK_FLATPAK_WRONG_APP:-0}}" != 1 ] || application=org.example.Other
            printf '%s %s %s\n' "$instance" "$application" "${{MOCK_FLATPAK_PS_PID:-0}}"
            if [ "${{MOCK_FLATPAK_MULTIPLE:-0}}" = 1 ] || [ -s "$MOCK_FLATPAK_AMBIGUOUS_MARKER" ]; then
                printf '%s %s %s\n' "$instance" "$application" "${{MOCK_FLATPAK_PS_PID:-0}}"
            fi
        fi
        ;;
    run)
        instance_fd=
        die_with_parent=0
        for argument in "$@"; do
            case "$argument" in
                --instance-id-fd=*) instance_fd=${{argument#*=}} ;;
                --die-with-parent) die_with_parent=1 ;;
            esac
        done
        [ "$die_with_parent" = 1 ] && [ -n "$instance_fd" ]
        "$MOCK_MAKE_PROC" "$$" /mock/flatpak
        printf '%s\n' "$$" >"$MOCK_FLATPAK_PID"
        printf 'running\n' >"$MOCK_FLATPAK_MARKER"
        instance=${{MOCK_FLATPAK_INSTANCE:-1001}}
        case "${{MOCK_FLATPAK_RECEIPT:-normal}}" in
            normal)
                eval "printf '%s\\n' \"\\$instance\" >&$instance_fd"
                ;;
            no-newline)
                eval "printf '%s' \"\\$instance\" >&$instance_fd"
                ;;
            malformed)
                eval "printf 'not-an-instance\\n' >&$instance_fd"
                ;;
            missing) ;;
            *) exit 8 ;;
        esac
        eval "exec $instance_fd>&-"
        if [ "${{MOCK_FLATPAK_WRAPPER_EXIT:-0}}" = 1 ]; then
            exit 0
        fi
        trap 'rm -f "$MOCK_FLATPAK_PID"; exit 143' TERM INT HUP
        while :; do sleep 1; done
        ;;
    kill)
        [ "$#" -eq 2 ] && [ "$2" = "${{MOCK_FLATPAK_INSTANCE:-1001}}" ]
        if [ -s "$MOCK_FLATPAK_PID" ]; then
            pid=$(cat "$MOCK_FLATPAK_PID")
            kill -TERM "$pid" 2>/dev/null || true
        fi
        rm -f "$MOCK_FLATPAK_PID"
        ;;
    *) exit 2 ;;
esac
''',
        )
        write_executable(
            self.bin / "tuned-adm",
            r'''#!/bin/sh
set -eu
[ "$#" -eq 1 ] && [ "$1" = active ]
printf 'tuned active\n' >>"$MOCK_POWER_LOG"
if [ "${MOCK_TUNED_HANG:-0}" = 1 ]; then
    sleep 30 &
    printf '%s\n' "$!" >"$MOCK_POWER_GRANDCHILD"
    printf 'hung\n' >"$MOCK_POWER_MARKER"
    while :; do sleep 1; done
fi
[ "${MOCK_TUNED_FAIL:-0}" != 1 ] || exit 51
if [ -n "${MOCK_TUNED_PROFILE:-}" ]; then
    profile=$MOCK_TUNED_PROFILE
else
    profile=pocketds-$(cat "$MOCK_POWER_STATE")
fi
printf 'Current active profile: %s\n' "$profile"
''',
        )
        write_executable(
            self.bin / "panelctl",
            r'''#!/bin/sh
set -eu
[ "$#" -eq 2 ] && [ "$1" = power ]
printf 'power %s\n' "$2" >>"$MOCK_POWER_LOG"
if [ "$2" = performance ]; then
    printf 'performance\n' >"$MOCK_POWER_STATE"
    if [ "${MOCK_PANEL_APPLY_HANG:-0}" = 1 ]; then
        sleep 30 &
        printf '%s\n' "$!" >"$MOCK_POWER_GRANDCHILD"
        printf 'hung\n' >"$MOCK_POWER_MARKER"
        while :; do sleep 1; done
    fi
    [ "${MOCK_PANEL_APPLY_FAIL:-0}" != 1 ] || exit 52
else
    [ "${MOCK_PANEL_RESTORE_FAIL:-0}" != 1 ] || exit 53
    printf '%s\n' "$2" >"$MOCK_POWER_STATE"
fi
''',
        )

    def command(self, *arguments: str) -> List[str]:
        return [str(self.bin / "supervisor-wrapper"), *arguments]

    def start(self, arguments: Sequence[str], extra: Optional[Dict[str, str]] = None):
        environment = self.env.copy()
        if extra:
            environment.update(extra)
        return subprocess.Popen(
            self.command(*arguments),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )

    def run(
        self,
        arguments: Sequence[str],
        extra: Optional[Dict[str, str]] = None,
        timeout: float = 6.0,
    ) -> subprocess.CompletedProcess[str]:
        environment = self.env.copy()
        if extra:
            environment.update(extra)
        return subprocess.run(
            self.command(*arguments),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            timeout=timeout,
            check=False,
        )

    def read_log(self, name: str) -> List[str]:
        path = self.logs / name
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()


def write_native_record(
    fixture: Fixture,
    *,
    token: str,
    owner_pid: int,
    owner_starttime: str,
    owner_cgroup: str = "/mock/owner",
    name: str = "retroarch",
) -> Path:
    record_dir = fixture.runtime / "pocketds-game-session"
    record_dir.mkdir(mode=0o700, exist_ok=True)
    record_path = record_dir / "session.json"
    record = {
        "protocol": "pds-game-session-v1",
        "token": token,
        "owner_pid": owner_pid,
        "owner_starttime": owner_starttime,
        "owner_cgroup": owner_cgroup,
        "backend": "native",
        "name": name,
        "phase": "running",
        "scope_cgroup": "/mock/session",
        "unit": f"pocketds-{name}-{token.replace('-', '')}.scope",
        "input_previous": "joymouse",
        "input_token": RECEIPT_TOKEN,
    }
    record_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    record_path.chmod(0o600)
    return record_path


def native_join_test() -> None:
    fixture = Fixture()
    try:
        result = fixture.run(
            [
                "run",
                "--backend",
                "native",
                "--name",
                "es-de",
                "--",
                str(fixture.bin / "nested-entry"),
            ]
        )
        require(result.returncode == 0, f"native join failed: {result.stderr}")
        require(
            fixture.read_log("input.log")
            == [
                "acquire gamepad",
                f"recover-token {RECEIPT_TOKEN} joymouse",
            ],
            "nested RetroArch acquired or restored input a second time",
        )
        require(
            fixture.read_log("retroarch.log") == ["joined-retroarch"],
            "joined RetroArch did not execute",
        )
        systemd = fixture.read_log("systemd.log")
        require(len(systemd) == 1, "native backend did not use one scope")
        require(
            "--user --scope --unit=pocketds-es-de-" in systemd[0]
            and "--slice=app.slice" in systemd[0]
            and "--property=KillMode=control-group" in systemd[0]
            and "--property=SendSIGKILL=yes" in systemd[0]
            and "--property=TimeoutStopSec=5s" in systemd[0]
            and "--expand-environment=no" in systemd[0]
            and "--collect --quiet --" in systemd[0]
            and "--wait" not in systemd[0],
            "native systemd-run invocation was not an exact user scope",
        )
        require(
            not (fixture.runtime / "pocketds-game-session" / "session.json").exists(),
            "runtime record survived a completed session",
        )
    finally:
        fixture.close()


def native_steam_session_test() -> None:
    fixture = Fixture()
    try:
        result = fixture.run(
            [
                "run",
                "--backend",
                "native",
                "--name",
                "steam",
                "--",
                str(fixture.bin / "native-quick"),
            ]
        )
        require(result.returncode == 0, f"managed Steam failed: {result.stderr}")
        require(
            fixture.read_log("input.log")
            == [
                "acquire gamepad",
                f"recover-token {RECEIPT_TOKEN} joymouse",
            ],
            "managed Steam did not own and restore the gamepad lease",
        )
        systemd = fixture.read_log("systemd.log")
        require(
            len(systemd) == 1
            and "--unit=pocketds-steam-" in systemd[0]
            and "--property=KillMode=control-group" in systemd[0],
            "managed Steam did not use its exact native scope",
        )
        require(not fixture.read_log("power.log"), "Steam changed the selected power mode")

        result = fixture.run(
            [
                "run",
                "--backend",
                "native",
                "--name",
                "steam",
                "--performance",
                "--",
                str(fixture.bin / "native-quick"),
            ]
        )
        require(result.returncode == 2, "Steam --performance was not rejected")
        require(
            len(fixture.read_log("systemd.log")) == 1,
            "rejected Steam performance launch entered a second scope",
        )
    finally:
        fixture.close()


def managed_session_probe_test() -> None:
    fixture = Fixture()
    try:
        missing = fixture.run(["probe", "--name", "steam"])
        require(missing.returncode == 3, "missing session was not reported idle")
        require(not fixture.read_log("input.log"), "probe touched input")

        subprocess.run(
            [str(fixture.bin / "make-proc"), str(os.getpid()), "/mock/owner"],
            env=fixture.env,
            check=True,
        )
        token = "88888888-8888-4888-8888-888888888888"
        record_path = write_native_record(
            fixture,
            token=token,
            owner_pid=os.getpid(),
            owner_starttime=str(os.getpid() + 700000),
            name="steam",
        )
        (fixture.cgroup / "mock/session/cgroup.events").write_text(
            "populated 1\n", encoding="ascii"
        )
        current = fixture.run(["probe", "--name", "steam"])
        require(current.returncode == 0, f"live Steam probe failed: {current.stderr}")

        other = fixture.run(["probe", "--name", "retroarch"])
        require(other.returncode == 75, "different live session was not busy")

        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["phase"] = "restoring"
        record_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        record_path.chmod(0o600)
        stopping = fixture.run(["probe", "--name", "steam"])
        require(stopping.returncode == 75, "restoring session was not reported busy")
        require(not fixture.read_log("systemd.log"), "probe entered a native scope")
        require(not fixture.read_log("power.log"), "probe changed performance")
    finally:
        fixture.close()


def signal_during_input_test(mode: str, expect_restore: bool) -> None:
    fixture = Fixture()
    try:
        process = fixture.start(
            [
                "run",
                "--backend",
                "native",
                "--name",
                "retroarch",
                "--",
                str(fixture.bin / "native-quick"),
            ],
            {"MOCK_ACQUIRE_MODE": mode},
        )
        wait_for(fixture.logs / "input.marker")
        process.send_signal(signal.SIGTERM)
        _stdout, stderr = process.communicate(timeout=5)
        require(process.returncode == 143, f"TERM returned {process.returncode}: {stderr}")
        require(not fixture.read_log("systemd.log"), "TERM still entered native app")
        input_log = fixture.read_log("input.log")
        require(input_log[0] == "acquire gamepad", "input acquisition was not attempted")
        restore_line = f"recover-token {RECEIPT_TOKEN} joymouse"
        require(
            (restore_line in input_log) is expect_restore,
            f"unexpected receipt restoration for {mode}: {input_log}",
        )
        grandchild = int(
            (fixture.logs / "input-grandchild.pid").read_text(encoding="ascii")
        )
        wait_dead(grandchild)
    finally:
        fixture.close()


def native_lock_and_exact_kill_test() -> None:
    fixture = Fixture()
    sentinel = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    first: Optional[subprocess.Popen[str]] = None
    try:
        first = fixture.start(
            [
                "run",
                "--backend",
                "native",
                "--name",
                "retroarch",
                "--",
                str(fixture.bin / "native-ignore-term"),
            ]
        )
        wait_for(fixture.logs / "native.marker")
        second = fixture.run(
            [
                "run",
                "--backend",
                "native",
                "--name",
                "retroarch",
                "--",
                str(fixture.bin / "native-quick"),
            ]
        )
        require(second.returncode == 75, "fcntl session lock did not reject overlap")
        first.send_signal(signal.SIGTERM)
        _stdout, stderr = first.communicate(timeout=6)
        require(first.returncode == 143, f"native TERM failed: {stderr}")
        controls = fixture.read_log("systemctl.log")
        stop = [line for line in controls if line.startswith("--user stop ")]
        kill = [line for line in controls if line.startswith("--user kill ")]
        require(len(stop) == 1 and len(kill) == 1, "native stop/kill was not bounded")
        stop_unit = stop[0].split()[-1]
        kill_unit = kill[0].split()[-1]
        require(
            stop_unit == kill_unit
            and stop_unit.startswith("pocketds-retroarch-")
            and stop_unit.endswith(".scope"),
            "native termination widened beyond its exact transient unit",
        )
        require(sentinel.poll() is None, "native termination killed an unrelated process")
    finally:
        if first is not None and first.poll() is None:
            first.kill()
            first.wait(timeout=2)
        if sentinel.poll() is None:
            sentinel.kill()
            sentinel.wait(timeout=2)
        fixture.close()


def process_group_eperm_race_test() -> None:
    spec = importlib.util.spec_from_file_location(
        "pocketds_game_session_under_test", SUPERVISOR
    )
    require(spec is not None and spec.loader is not None, "cannot load supervisor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class FinishedProcess:
        pid = 424242
        returncode = 0

        @staticmethod
        def poll() -> int:
            return 0

    supervisor = object.__new__(module.SessionSupervisor)
    supervisor.term_timeout = 0.05
    supervisor.uncertain_process_groups = set()
    process = FinishedProcess()
    calls: List[tuple] = []

    def zombie_transition(pid: int, signum: int) -> None:
        calls.append((pid, signum))
        if len(calls) == 1:
            raise PermissionError(1, "mock EPERM")
        raise ProcessLookupError(3, "mock ESRCH")

    with mock.patch.object(module.os, "killpg", side_effect=zombie_transition):
        supervisor.terminate_process_group(process)
    require(
        calls == [(process.pid, signal.SIGTERM), (process.pid, 0)],
        f"EPERM zombie resolution retried a real signal: {calls}",
    )

    calls.clear()

    def reappeared_group(pid: int, signum: int) -> None:
        calls.append((pid, signum))
        return None

    with mock.patch.object(module.os, "killpg", side_effect=reappeared_group):
        try:
            supervisor.terminate_process_group(process)
        except module.SupervisorError:
            pass
        else:
            raise AssertionError("reused/unverifiable PGID was accepted")
    require(
        calls and all(signum == 0 for _pid, signum in calls),
        f"ESRCH tombstone re-signalled a reused PGID: {calls}",
    )

    second_supervisor = object.__new__(module.SessionSupervisor)
    second_supervisor.term_timeout = 0.05
    second_supervisor.uncertain_process_groups = set()
    calls.clear()

    def persistent_eperm(pid: int, signum: int) -> None:
        calls.append((pid, signum))
        if len(calls) == 1:
            raise PermissionError(1, "mock EPERM")
        return None

    with mock.patch.object(module.os, "killpg", side_effect=persistent_eperm):
        try:
            second_supervisor.terminate_process_group(process)
        except module.SupervisorError:
            pass
        else:
            raise AssertionError("persistent EPERM PGID was accepted")
    require(
        calls[0] == (process.pid, signal.SIGTERM)
        and all(signum == 0 for _pid, signum in calls[1:]),
        f"persistent EPERM path sent an unsafe follow-up signal: {calls}",
    )

    calls.clear()
    with mock.patch.object(module.os, "killpg", side_effect=reappeared_group):
        try:
            second_supervisor.terminate_process_group(process)
        except module.SupervisorError:
            pass
        else:
            raise AssertionError("latched persistent EPERM PGID was accepted")
    require(
        calls and all(signum == 0 for _pid, signum in calls),
        f"latched persistent EPERM path re-signalled a PGID: {calls}",
    )

    probe_eperm_supervisor = object.__new__(module.SessionSupervisor)
    probe_eperm_supervisor.term_timeout = 0.05
    probe_eperm_supervisor.uncertain_process_groups = set()
    calls.clear()

    def term_then_probe_eperm(pid: int, signum: int) -> None:
        calls.append((pid, signum))
        if len(calls) == 2:
            raise PermissionError(1, "mock probe EPERM")
        return None

    with mock.patch.object(
        module.os, "killpg", side_effect=term_then_probe_eperm
    ):
        try:
            probe_eperm_supervisor.terminate_process_group(process)
        except module.SupervisorError:
            pass
        else:
            raise AssertionError("post-TERM probe EPERM was accepted")
    require(
        calls[0] == (process.pid, signal.SIGTERM)
        and calls[1] == (process.pid, 0)
        and all(signum == 0 for _pid, signum in calls[1:]),
        f"post-TERM probe EPERM escalated to a real signal: {calls}",
    )

    reuse_supervisor = object.__new__(module.SessionSupervisor)
    reuse_supervisor.term_timeout = 0.05
    reuse_supervisor.uncertain_process_groups = set()
    calls.clear()

    def disappears_then_reuses(pid: int, signum: int) -> None:
        calls.append((pid, signum))
        if len(calls) == 2:
            # The first post-TERM probe observes the original group gone.
            raise ProcessLookupError(3, "mock ESRCH")
        # Any later probe represents an unrelated group reusing the number.
        return None

    with mock.patch.object(
        module.os, "killpg", side_effect=disappears_then_reuses
    ):
        reuse_supervisor.terminate_process_group(process)
    require(
        calls == [(process.pid, signal.SIGTERM), (process.pid, 0)],
        f"ESRCH observation was followed by a reused-PGID signal: {calls}",
    )
    require(
        process.pid in reuse_supervisor.uncertain_process_groups,
        "ESRCH did not permanently tombstone the numeric PGID",
    )

    calls.clear()
    with mock.patch.object(module.os, "killpg", side_effect=reappeared_group):
        try:
            reuse_supervisor.terminate_process_group(process)
        except module.SupervisorError:
            pass
        else:
            raise AssertionError("reused PGID after ESRCH was accepted")
    require(
        calls and all(signum == 0 for _pid, signum in calls),
        f"reused PGID after ESRCH received a real signal: {calls}",
    )

    flatpak_supervisor = object.__new__(module.SessionSupervisor)
    flatpak_supervisor.backend_process = process
    flatpak_supervisor.flatpak_instance = "1001"
    flatpak_supervisor.backend_quiesced = False
    flatpak_supervisor.flatpak_binding_state = lambda: "absent"

    def failed_reap() -> None:
        raise module.SupervisorError("mock persistent EPERM")

    flatpak_supervisor.reap_backend_client = failed_reap
    try:
        flatpak_supervisor.stop_flatpak()
    except module.SupervisorError:
        pass
    else:
        raise AssertionError("failed Flatpak client reap was hidden")
    require(
        not flatpak_supervisor.backend_quiesced,
        "Flatpak cleanup authorized input restore before client reap",
    )


def native_no_scope_failure_test() -> None:
    fixture = Fixture()
    try:
        result = fixture.run(
            [
                "run",
                "--backend",
                "native",
                "--name",
                "retroarch",
                "--",
                str(fixture.bin / "native-quick"),
            ],
            {"MOCK_SYSTEMD_NO_SCOPE": "1"},
        )
        require(result.returncode == 7, f"no-scope rc was lost: {result.stderr}")
        require(
            fixture.input_state.read_text(encoding="ascii").strip() == "joymouse",
            "input was not restored after systemd-run failed before scope creation",
        )
        require(
            fixture.read_log("input.log")[-1]
            == f"recover-token {RECEIPT_TOKEN} joymouse",
            "no-scope failure did not consume the input receipt",
        )
    finally:
        fixture.close()


def native_scope_outlives_client_test() -> None:
    fixture = Fixture()
    sentinel = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    try:
        result = fixture.run(
            [
                "run",
                "--backend",
                "native",
                "--name",
                "retroarch",
                "--",
                str(fixture.bin / "native-ignore-term"),
            ],
            {"MOCK_SYSTEMD_DETACH": "1"},
        )
        require(result.returncode == 0, f"detached scope cleanup failed: {result.stderr}")
        controls = fixture.read_log("systemctl.log")
        require(
            len([line for line in controls if line.startswith("--user stop ")]) == 1
            and len([line for line in controls if line.startswith("--user kill ")]) == 1,
            "scope surviving systemd-run was not stopped and killed exactly",
        )
        require(
            (fixture.cgroup / "mock/session/cgroup.events")
            .read_text(encoding="ascii")
            .strip()
            == "populated 0",
            "input restoration raced a populated native cgroup",
        )
        require(sentinel.poll() is None, "native orphan cleanup killed a sentinel")
    finally:
        if sentinel.poll() is None:
            sentinel.kill()
            sentinel.wait(timeout=2)
        fixture.close()


def stale_record_recovery_test() -> None:
    command_tail = [
        "run",
        "--backend",
        "native",
        "--name",
        "retroarch",
        "--",
    ]

    fixture = Fixture()
    try:
        subprocess.run(
            [str(fixture.bin / "make-proc"), str(os.getpid()), "/mock/owner"],
            env=fixture.env,
            check=True,
        )
        token = "44444444-4444-4444-8444-444444444444"
        write_native_record(
            fixture,
            token=token,
            owner_pid=os.getpid(),
            owner_starttime=str(os.getpid() + 700000),
        )
        result = fixture.run(command_tail + [str(fixture.bin / "native-quick")])
        require(result.returncode == 1, "live stale-record owner was overwritten")
        require(not fixture.read_log("input.log"), "live stale record mutated input")
    finally:
        fixture.close()

    fixture = Fixture()
    try:
        token = "55555555-5555-4555-8555-555555555555"
        write_native_record(
            fixture, token=token, owner_pid=999999, owner_starttime="1"
        )
        (fixture.cgroup / "mock/session/cgroup.events").write_text(
            "populated 1\n", encoding="ascii"
        )
        result = fixture.run(command_tail + [str(fixture.bin / "native-quick")])
        require(result.returncode == 1, "orphan native cgroup was overwritten")
        require(not fixture.read_log("input.log"), "orphan record mutated input")
    finally:
        fixture.close()

    fixture = Fixture()
    try:
        token = "66666666-6666-4666-8666-666666666666"
        record_path = write_native_record(
            fixture, token=token, owner_pid=999999, owner_starttime="1"
        )
        old_unit = f"pocketds-retroarch-{token.replace('-', '')}.scope"
        result = fixture.run(
            command_tail + [str(fixture.bin / "native-quick")],
            {"MOCK_MISSING_UNIT": old_unit},
        )
        require(result.returncode == 0, f"proven-stale record was not recovered: {result.stderr}")
        require(not record_path.exists(), "completed replacement session left a record")
        require(
            fixture.input_state.read_text(encoding="ascii").strip() == "joymouse",
            "stale recovery did not preserve input restoration",
        )
    finally:
        fixture.close()

    fixture = Fixture()
    try:
        token = "77777777-7777-4777-8777-777777777777"
        record_path = write_native_record(
            fixture, token=token, owner_pid=999999, owner_starttime="1"
        )
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["phase"] = "acquiring-input"
        record.pop("unit")
        record.pop("scope_cgroup")
        record_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        result = fixture.run(command_tail + [str(fixture.bin / "native-quick")])
        require(
            result.returncode == 0,
            f"pre-backend input journal was not recovered: {result.stderr}",
        )
        require(not record_path.exists(), "pre-backend recovery left a journal")
    finally:
        fixture.close()

    fixture = Fixture()
    try:
        record_dir = fixture.runtime / "pocketds-game-session"
        record_dir.mkdir(mode=0o700)
        malformed = record_dir / "session.json"
        malformed.write_text("{not-json\n", encoding="utf-8")
        malformed.chmod(0o600)
        result = fixture.run(command_tail + [str(fixture.bin / "native-quick")])
        require(result.returncode == 1, "malformed stale record was overwritten")
        require(not fixture.read_log("input.log"), "malformed record mutated input")
    finally:
        fixture.close()

    fixture = Fixture()
    try:
        record_dir = fixture.runtime / "pocketds-game-session"
        record_dir.mkdir(mode=0o700)
        target = fixture.root / "record-target"
        target.write_text("{}\n", encoding="utf-8")
        (record_dir / "session.json").symlink_to(target)
        result = fixture.run(command_tail + [str(fixture.bin / "native-quick")])
        require(result.returncode == 1, "linked stale record was overwritten")
        require(not fixture.read_log("input.log"), "linked record mutated input")
    finally:
        fixture.close()


def flatpak_preflight_fail_closed_test() -> None:
    fixture = Fixture()
    try:
        result = fixture.run(
            [
                "run",
                "--backend",
                "flatpak",
                "--name",
                "wiliwili",
                "--app-id",
                APP_ID,
                "--",
                "--debug",
            ],
            {"MOCK_FLATPAK_PS_FAIL": "1"},
        )
        require(result.returncode == 1, "failed Flatpak ps was accepted")
        require(not fixture.read_log("input.log"), "ps failure mutated input")
        require(
            not any(line.startswith("run ") for line in fixture.read_log("flatpak.log")),
            "ps failure launched Wiliwili",
        )
    finally:
        fixture.close()


def flatpak_numeric_kill_test() -> None:
    fixture = Fixture()
    sentinel = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    process: Optional[subprocess.Popen[str]] = None
    try:
        process = fixture.start(
            [
                "run",
                "--backend",
                "flatpak",
                "--name",
                "wiliwili",
                "--app-id",
                APP_ID,
                "--",
                "--debug",
            ],
            {"MOCK_FLATPAK_RECEIPT": "no-newline"},
        )
        wait_for(fixture.logs / "flatpak.marker")
        record_path = fixture.runtime / "pocketds-game-session" / "session.json"
        deadline = time.monotonic() + 4
        record = {}
        while time.monotonic() < deadline:
            if record_path.exists():
                record = json.loads(record_path.read_text(encoding="utf-8"))
                if record.get("phase") == "running":
                    break
            time.sleep(0.02)
        require(record.get("flatpak_instance") == "1001", "numeric instance was not bound")
        require("flatpak_pid" not in record, "Flatpak ps PID leaked into identity")
        run_lines = [
            line for line in fixture.read_log("flatpak.log") if line.startswith("run ")
        ]
        require(
            len(run_lines) == 1
            and "--die-with-parent" in run_lines[0]
            and "--instance-id-fd=" in run_lines[0],
            "Flatpak did not use its authoritative instance-id fd",
        )
        process.send_signal(signal.SIGTERM)
        _stdout, stderr = process.communicate(timeout=6)
        require(process.returncode == 143, f"Flatpak TERM failed: {stderr}")
        kills = [
            line for line in fixture.read_log("flatpak.log") if line.startswith("kill ")
        ]
        require(kills == ["kill 1001"], f"Flatpak kill was not numeric-only: {kills}")
        require(
            all(APP_ID not in line for line in kills),
            "Flatpak app ID was used as a kill target",
        )
        require(sentinel.poll() is None, "Flatpak termination killed an unrelated process")
        require(
            fixture.input_state.read_text(encoding="ascii").strip() == "joymouse",
            "Flatpak termination did not restore input",
        )
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        if sentinel.poll() is None:
            sentinel.kill()
            sentinel.wait(timeout=2)
        fixture.close()


def flatpak_ambiguous_binding_test() -> None:
    fixture = Fixture()
    try:
        result = fixture.run(
            [
                "run",
                "--backend",
                "flatpak",
                "--name",
                "wiliwili",
                "--app-id",
                APP_ID,
                "--",
            ],
            {"MOCK_FLATPAK_MULTIPLE": "1"},
        )
        require(result.returncode == 1, "ambiguous Flatpak identity was accepted")
        kills = [
            line for line in fixture.read_log("flatpak.log") if line.startswith("kill ")
        ]
        require(
            kills and set(kills) == {"kill 1001"},
            "ambiguous binding did not stay on the receipt's numeric target",
        )
        require(
            fixture.input_state.read_text(encoding="ascii").strip() == "gamepad",
            "ambiguous Flatpak binding restored input without verified identity",
        )
        require(
            (fixture.runtime / "pocketds-game-session" / "session.json").exists(),
            "ambiguous Flatpak binding discarded its recovery journal",
        )
    finally:
        fixture.close()


def flatpak_post_bind_ambiguity_test() -> None:
    fixture = Fixture()
    process: Optional[subprocess.Popen[str]] = None
    record_path = fixture.runtime / "pocketds-game-session" / "session.json"
    try:
        process = fixture.start(
            [
                "run",
                "--backend",
                "flatpak",
                "--name",
                "wiliwili",
                "--app-id",
                APP_ID,
                "--",
            ]
        )
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if record_path.exists():
                record = json.loads(record_path.read_text(encoding="utf-8"))
                if record.get("phase") == "running":
                    break
            time.sleep(0.02)
        else:
            raise AssertionError("Flatpak did not bind before ambiguity injection")
        (fixture.logs / "flatpak-ambiguous.marker").write_text(
            "ambiguous\n", encoding="ascii"
        )
        process.send_signal(signal.SIGTERM)
        _stdout, stderr = process.communicate(timeout=6)
        require(process.returncode == 143, f"post-bind ambiguity TERM failed: {stderr}")
        require(
            fixture.input_state.read_text(encoding="ascii").strip() == "gamepad",
            "post-bind ps ambiguity restored input from historical verification",
        )
        require(record_path.exists(), "post-bind ps ambiguity deleted its journal")
        require(
            set(
                line
                for line in fixture.read_log("flatpak.log")
                if line.startswith("kill ")
            )
            == {"kill 1001"},
            "post-bind ambiguity widened beyond the receipt's numeric target",
        )
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        fixture.close()


def flatpak_ps_unknown_after_launch_test() -> None:
    fixture = Fixture()
    try:
        result = fixture.run(
            [
                "run",
                "--backend",
                "flatpak",
                "--name",
                "wiliwili",
                "--app-id",
                APP_ID,
                "--",
            ],
            {"MOCK_FLATPAK_PS_FAIL_AFTER_RUN": "1"},
        )
        require(result.returncode == 1, "unknown Flatpak ps state was accepted")
        kills = [
            line for line in fixture.read_log("flatpak.log") if line.startswith("kill ")
        ]
        require(
            kills and set(kills) == {"kill 1001"},
            "ps failure widened or lost the exact receipt kill target",
        )
        require(
            fixture.input_state.read_text(encoding="ascii").strip() == "gamepad",
            "unknown Flatpak ps state restored input",
        )
        require(
            (fixture.runtime / "pocketds-game-session" / "session.json").exists(),
            "unknown Flatpak ps state discarded its recovery journal",
        )
    finally:
        fixture.close()


def flatpak_instance_receipt_fail_closed_test() -> None:
    for receipt_mode in ("malformed", "missing"):
        fixture = Fixture()
        try:
            result = fixture.run(
                [
                    "run",
                    "--backend",
                    "flatpak",
                    "--name",
                    "wiliwili",
                    "--app-id",
                    APP_ID,
                    "--",
                ],
                {"MOCK_FLATPAK_RECEIPT": receipt_mode},
            )
            require(result.returncode == 1, f"{receipt_mode} receipt was accepted")
            require(
                fixture.input_state.read_text(encoding="ascii").strip() == "gamepad",
                f"{receipt_mode} receipt restored input without exact identity",
            )
            require(
                (fixture.runtime / "pocketds-game-session" / "session.json").exists(),
                f"{receipt_mode} receipt discarded its input journal",
            )
            require(
                not any(
                    APP_ID in line
                    for line in fixture.read_log("flatpak.log")
                    if line.startswith("kill ")
                ),
                f"{receipt_mode} receipt used an application-id kill",
            )
        finally:
            fixture.close()


def flatpak_instance_outlives_client_test() -> None:
    fixture = Fixture()
    try:
        result = fixture.run(
            [
                "run",
                "--backend",
                "flatpak",
                "--name",
                "wiliwili",
                "--app-id",
                APP_ID,
                "--",
            ],
            {"MOCK_FLATPAK_WRAPPER_EXIT": "1"},
        )
        require(result.returncode == 0, f"lingering instance cleanup failed: {result.stderr}")
        kills = [
            line for line in fixture.read_log("flatpak.log") if line.startswith("kill ")
        ]
        require(kills == ["kill 1001"], "normal exit did not kill the bound instance")
        require(
            fixture.input_state.read_text(encoding="ascii").strip() == "joymouse",
            "input restored before lingering Flatpak instance disappeared",
        )
    finally:
        fixture.close()


def forged_join_test() -> None:
    fixture = Fixture()
    try:
        record_dir = fixture.runtime / "pocketds-game-session"
        record_dir.mkdir(mode=0o700)
        fake_token = "22222222-2222-4222-8222-222222222222"
        record = {
            "protocol": "pds-game-session-v1",
            "token": fake_token,
            "owner_pid": os.getpid(),
            "owner_starttime": "1",
            "owner_cgroup": "/mock/owner",
            "backend": "native",
            "name": "es-de",
            "phase": "running",
            "scope_cgroup": "/mock/session",
            "unit": "pocketds-es-de-forged.scope",
        }
        record_path = record_dir / "session.json"
        record_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        record_path.chmod(0o600)
        result = fixture.run(
            [
                "join",
                "--token",
                fake_token,
                "--",
                str(fixture.bin / "retroarch"),
            ]
        )
        require(result.returncode == 1, "forged owner identity was accepted")
        require(not fixture.read_log("retroarch.log"), "forged join executed RetroArch")
        require(not fixture.read_log("input.log"), "join touched the input helper")
    finally:
        fixture.close()


def joined_process_tree_test() -> None:
    fixture = Fixture()
    process: Optional[subprocess.Popen[str]] = None
    try:
        subprocess.run(
            [str(fixture.bin / "make-proc"), str(os.getpid()), "/mock/owner"],
            env=fixture.env,
            check=True,
        )
        token = "33333333-3333-4333-8333-333333333333"
        record_dir = fixture.runtime / "pocketds-game-session"
        record_dir.mkdir(mode=0o700)
        record = {
            "protocol": "pds-game-session-v1",
            "token": token,
            "owner_pid": os.getpid(),
            "owner_starttime": str(os.getpid() + 700000),
            "owner_cgroup": "/mock/owner",
            "backend": "native",
            "name": "es-de",
            "phase": "running",
            "scope_cgroup": "/mock/session",
            "unit": "pocketds-es-de-33333333333343338333333333333333.scope",
        }
        record_path = record_dir / "session.json"
        record_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        record_path.chmod(0o600)
        process = subprocess.Popen(
            [
                str(fixture.bin / "join-wrapper"),
                "join",
                "--token",
                token,
                "--",
                str(fixture.bin / "retroarch-tree"),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=fixture.env,
        )
        wait_for(fixture.logs / "tree.marker")
        child = int((fixture.logs / "tree-child.pid").read_text(encoding="ascii"))
        grandchild = int(
            (fixture.logs / "tree-grandchild.pid").read_text(encoding="ascii")
        )
        process.send_signal(signal.SIGTERM)
        _stdout, stderr = process.communicate(timeout=6)
        require(process.returncode == 143, f"joined TERM failed: {stderr}")
        wait_dead(child)
        wait_dead(grandchild)
        require(not fixture.read_log("power.log"), "join changed global performance")
        require(not fixture.read_log("input.log"), "join acquired or restored input")
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        fixture.close()


def join_performance_rejected_test() -> None:
    fixture = Fixture()
    try:
        result = fixture.run(
            [
                "join",
                "--token",
                "33333333-3333-4333-8333-333333333333",
                "--performance",
                "--",
                str(fixture.bin / "retroarch"),
            ]
        )
        require(result.returncode == 2, "join --performance was not a parser error")
        require(not fixture.read_log("power.log"), "rejected join changed performance")
        require(not fixture.read_log("input.log"), "rejected join touched input")
        require(not fixture.read_log("retroarch.log"), "rejected join ran RetroArch")
    finally:
        fixture.close()


def performance_hook_fail_safe_test() -> None:
    fixture = Fixture()
    try:
        started = time.monotonic()
        result = fixture.run(
            [
                "run",
                "--backend",
                "native",
                "--name",
                "retroarch",
                "--performance",
                "--",
                str(fixture.bin / "native-quick"),
            ],
            {"MOCK_TUNED_HANG": "1", "POCKETDS_CONTROL_TIMEOUT": "0.5"},
        )
        elapsed = time.monotonic() - started
        require(result.returncode == 0, f"hung optional hook blocked game: {result.stderr}")
        require(elapsed < 3.0, "hung performance hook was not bounded")
        power_child = int(
            (fixture.logs / "power-grandchild.pid").read_text(encoding="ascii")
        )
        wait_dead(power_child)
        require(
            fixture.read_log("power.log") == ["tuned active"],
            "hung profile capture still changed performance",
        )

        (fixture.logs / "power.log").unlink(missing_ok=True)
        (fixture.logs / "power.marker").unlink(missing_ok=True)
        (fixture.logs / "power-grandchild.pid").unlink(missing_ok=True)
        result = fixture.run(
            [
                "run",
                "--backend",
                "native",
                "--name",
                "retroarch",
                "--performance",
                "--",
                str(fixture.bin / "native-quick"),
            ],
            {
                "MOCK_PANEL_APPLY_HANG": "1",
                "POCKETDS_CONTROL_TIMEOUT": "0.5",
            },
        )
        require(result.returncode == 0, f"mutating apply hang failed: {result.stderr}")
        power_child = int(
            (fixture.logs / "power-grandchild.pid").read_text(encoding="ascii")
        )
        wait_dead(power_child)
        require(
            fixture.read_log("power.log")
            == [
                "tuned active",
                "power performance",
                "tuned active",
                "power balanced",
            ],
            "mutating apply timeout did not restore the captured profile",
        )

        (fixture.logs / "power.log").unlink(missing_ok=True)
        result = fixture.run(
            [
                "run",
                "--backend",
                "native",
                "--name",
                "retroarch",
                "--performance",
                "--",
                str(fixture.bin / "native-quick"),
            ],
            {"MOCK_PANEL_APPLY_FAIL": "1"},
        )
        require(result.returncode == 0, f"mutating apply failure blocked game: {result.stderr}")
        require(
            fixture.read_log("power.log")
            == [
                "tuned active",
                "power performance",
                "tuned active",
                "power balanced",
            ],
            "mutating apply failure did not restore the captured profile",
        )

        (fixture.logs / "power.log").unlink(missing_ok=True)
        result = fixture.run(
            [
                "run",
                "--backend",
                "native",
                "--name",
                "retroarch",
                "--performance",
                "--",
                str(fixture.bin / "native-quick"),
            ],
            {"MOCK_PANEL_RESTORE_FAIL": "1"},
        )
        require(result.returncode == 1, "failed performance restore was hidden")
        require(
            fixture.read_log("power.log")
            == [
                "tuned active",
                "power performance",
                "tuned active",
                "power balanced",
            ],
            "exact previous Pocket DS performance mode was not restored",
        )
        require(
            fixture.input_state.read_text(encoding="ascii").strip() == "joymouse",
            "performance failure prevented safe input restoration",
        )
    finally:
        fixture.close()


def input_recovery_journal_retry_test() -> None:
    fixture = Fixture()
    command = [
        "run",
        "--backend",
        "native",
        "--name",
        "retroarch",
        "--",
        str(fixture.bin / "native-quick"),
    ]
    record_path = fixture.runtime / "pocketds-game-session" / "session.json"
    try:
        first = fixture.run(command, {"MOCK_FAIL_RESTORE": "1"})
        require(first.returncode == 1, "failed input recovery was hidden")
        require(record_path.exists(), "failed input recovery deleted its journal")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        require(
            record.get("input_previous") == "joymouse"
            and record.get("input_token") == RECEIPT_TOKEN,
            "input receipt was not durable in the journal",
        )
        require(
            fixture.input_state.read_text(encoding="ascii").strip() == "gamepad",
            "failing recovery unexpectedly changed input",
        )
        old_unit = str(record["unit"])
        second = fixture.run(command, {"MOCK_MISSING_UNIT": old_unit})
        require(second.returncode == 0, f"stale input retry failed: {second.stderr}")
        require(not record_path.exists(), "successful stale retry left a journal")
        require(
            fixture.input_state.read_text(encoding="ascii").strip() == "joymouse",
            "stale retry did not restore the recorded target",
        )
        input_log = fixture.read_log("input.log")
        require(
            input_log[:4]
            == [
                "acquire gamepad",
                f"recover-token {RECEIPT_TOKEN} joymouse",
                f"recover-token {RECEIPT_TOKEN} joymouse",
                "acquire gamepad",
            ],
            f"stale receipt was not recovered before reacquisition: {input_log}",
        )
    finally:
        fixture.close()


def invalid_recovery_receipt_retained_test() -> None:
    fixture = Fixture()
    try:
        result = fixture.run(
            [
                "run",
                "--backend",
                "native",
                "--name",
                "retroarch",
                "--",
                str(fixture.bin / "native-quick"),
            ],
            {"MOCK_RECOVER_RECEIPT": "invalid"},
        )
        require(result.returncode == 1, "invalid recovery receipt was accepted")
        require(
            (fixture.runtime / "pocketds-game-session" / "session.json").exists(),
            "invalid recovery receipt discarded its journal",
        )
        require(
            fixture.input_state.read_text(encoding="ascii").strip() == "gamepad",
            "invalid recovery receipt changed mock input state",
        )
    finally:
        fixture.close()


def performance_journal_recovery_and_cas_test() -> None:
    fixture = Fixture()
    command = [
        "run",
        "--backend",
        "native",
        "--name",
        "retroarch",
        "--performance",
        "--",
        str(fixture.bin / "native-quick"),
    ]
    record_path = fixture.runtime / "pocketds-game-session" / "session.json"
    try:
        first = fixture.run(command, {"MOCK_PANEL_RESTORE_FAIL": "1"})
        require(first.returncode == 1, "failed performance restore was hidden")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        require(
            record.get("performance_restore_required") is True
            and record.get("performance_previous") == "balanced",
            "standalone performance recovery marker was not retained",
        )
        require(
            fixture.power_state.read_text(encoding="ascii").strip() == "performance",
            "failed performance restore did not leave the expected crash state",
        )
        old_unit = str(record["unit"])
        replacement = [
            "run",
            "--backend",
            "native",
            "--name",
            "retroarch",
            "--",
            str(fixture.bin / "native-quick"),
        ]
        second = fixture.run(replacement, {"MOCK_MISSING_UNIT": old_unit})
        require(second.returncode == 0, f"stale performance recovery failed: {second.stderr}")
        require(
            fixture.power_state.read_text(encoding="ascii").strip() == "balanced",
            "stale recovery did not restore the exact previous mode",
        )
        require(not record_path.exists(), "stale performance journal survived recovery")
    finally:
        fixture.close()

    fixture = Fixture()
    try:
        result = fixture.run(
            [
                "run",
                "--backend",
                "native",
                "--name",
                "retroarch",
                "--performance",
                "--",
                str(fixture.bin / "native-switch-power"),
            ]
        )
        require(result.returncode == 0, f"performance CAS run failed: {result.stderr}")
        require(
            fixture.power_state.read_text(encoding="ascii").strip() == "powersave",
            "performance cleanup overwrote a newer managed user mode",
        )
        require(
            "power balanced" not in fixture.read_log("power.log"),
            "performance CAS restored over a superseding user mode",
        )
    finally:
        fixture.close()


def hung_input_restore_cleanup_test() -> None:
    fixture = Fixture()
    try:
        started = time.monotonic()
        result = fixture.run(
            [
                "run",
                "--backend",
                "native",
                "--name",
                "retroarch",
                "--",
                str(fixture.bin / "native-quick"),
            ],
            {
                "MOCK_RESTORE_HANG": "1",
                "POCKETDS_INPUT_RESTORE_TIMEOUT": "0.5",
            },
        )
        elapsed = time.monotonic() - started
        require(result.returncode == 1, "hung input restore was hidden")
        require(elapsed < 3.0, "input restore deadline was not bounded")
        restore_child = int(
            (fixture.logs / "restore-grandchild.pid").read_text(encoding="ascii")
        )
        wait_dead(restore_child)
        require(
            "input recovery failed" in result.stderr,
            "hung restore did not report its cleanup failure",
        )
    finally:
        fixture.close()


def main() -> int:
    tests = [
        ("native scope + nested join", native_join_test),
        ("managed Steam scope + selected power mode", native_steam_session_test),
        ("read-only exact managed-session probe", managed_session_probe_test),
        (
            "TERM before input receipt",
            lambda: signal_during_input_test("block-before", False),
        ),
        (
            "TERM after input receipt",
            lambda: signal_during_input_test("block-after", True),
        ),
        ("fcntl exclusion + exact native kill", native_lock_and_exact_kill_test),
        ("process-group EPERM race", process_group_eperm_race_test),
        ("native immediate no-scope failure", native_no_scope_failure_test),
        ("native scope outlives client", native_scope_outlives_client_test),
        ("stale session record recovery", stale_record_recovery_test),
        ("Flatpak ps fail-closed", flatpak_preflight_fail_closed_test),
        ("numeric-only Flatpak termination", flatpak_numeric_kill_test),
        ("ambiguous Flatpak binding", flatpak_ambiguous_binding_test),
        ("post-bind Flatpak ambiguity", flatpak_post_bind_ambiguity_test),
        ("Flatpak ps unknown after launch", flatpak_ps_unknown_after_launch_test),
        ("Flatpak instance receipt fail-closed", flatpak_instance_receipt_fail_closed_test),
        ("Flatpak instance outlives client", flatpak_instance_outlives_client_test),
        ("forged nested join", forged_join_test),
        ("joined process tree", joined_process_tree_test),
        ("join performance rejected", join_performance_rejected_test),
        ("performance hooks fail safe", performance_hook_fail_safe_test),
        ("input recovery journal retry", input_recovery_journal_retry_test),
        ("invalid recovery receipt retained", invalid_recovery_receipt_retained_test),
        ("performance journal recovery + CAS", performance_journal_recovery_and_cas_test),
        ("hung input restore cleanup", hung_input_restore_cleanup_test),
    ]
    for label, test in tests:
        test()
        print(f"  [OK] {label}")
    print("game-session supervisor mock tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

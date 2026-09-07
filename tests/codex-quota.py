#!/usr/bin/env python3
"""Exercise bounded private Codex quota collection without account credentials."""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import tempfile
import time
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
COLLECTOR = ROOT / "components/codex-quota/pocketds-codex-quota"
QML = (ROOT / "components/control-panel/plasmoid/contents/ui/main.qml").read_text(
    encoding="utf-8"
)
COLLECTOR_SOURCE = COLLECTOR.read_text(encoding="utf-8")
COLLECTOR_MODULE = runpy.run_path(str(COLLECTOR), run_name="quota_tests")


FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import sys
import time

assert sys.argv[1:] == ["app-server"]
mode = os.environ.get("FAKE_MODE", "live")
for line in sys.stdin:
    request = json.loads(line)
    log = os.environ.get("FAKE_REQUEST_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as stream:
            stream.write(json.dumps({"method": request.get("method"), "params": request.get("params")}) + "\n")
    if request.get("id") == 1:
        print(json.dumps({"id": 1, "result": {}}), flush=True)
    elif request.get("id") == 2:
        if mode == "long":
            sys.stdout.write("x" * (300 * 1024))
            sys.stdout.flush()
            time.sleep(60)
        if mode == "hang":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            child_code = "import os,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open(os.environ['FAKE_CHILD_PID'],'w').write(str(os.getpid())); time.sleep(60)"
            subprocess.Popen([sys.executable, "-c", child_code])
            time.sleep(60)
        used = 74
        if mode == "string-percent":
            used = "74"
        elif mode == "float-percent":
            used = 74.5
        elif mode == "bad-percent":
            used = 400
        response = {
            "id": 2,
            "result": {
                "rateLimits": {
                    "primary": {
                        "usedPercent": used,
                        "windowDurationMins": 10080,
                        "resetsAt": 1788452789
                    },
                    "secondary": None,
                    "planType": "prolite",
                    "credits": {"privateToken": "DO-NOT-EMIT"}
                },
                "rateLimitsByLimitId": {
                    "codex": {"limitId": "codex", "limitName": "Codex"}
                },
                "rateLimitResetCredits": {"privateEmail": "secret@example.invalid"}
            }
        }
        if mode == "desktop-metadata":
            response["result"]["accountId"] = "PRIVATE-ACCOUNT-DO-NOT-EMIT"
            response["result"]["rateLimitUpsell"] = {"message": "PRIVATE-UPSELL-DO-NOT-EMIT"}
        elif mode == "keyed-bucket":
            response["result"]["rateLimitsByLimitId"]["codex"]["primary"] = {
                "usedPercent": 90, "windowDurationMins": 10080, "resetsAt": 1788751350
            }
        print(json.dumps(response), flush=True)
'''


def run(env: dict[str, str], output: Path, *, timeout: float = 10) -> tuple[dict, float]:
    merged = os.environ.copy()
    merged.update(env)
    merged["POCKETDS_CODEX_QUOTA_OUTPUT"] = str(output)
    started = time.monotonic()
    completed = subprocess.run(
        [str(COLLECTOR)],
        env=merged,
        check=True,
        timeout=timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    elapsed = time.monotonic() - started
    assert completed.stdout == ""
    assert completed.stderr == ""
    content = output.read_text(encoding="utf-8")
    assert "DO-NOT-EMIT" not in content
    assert "secret@example.invalid" not in content
    assert "PRIVATE-ACCOUNT-DO-NOT-EMIT" not in content
    assert "PRIVATE-UPSELL-DO-NOT-EMIT" not in content
    return json.loads(content), elapsed


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


with tempfile.TemporaryDirectory(prefix="pocketds-codex-quota-") as temporary:
    temp = Path(temporary)
    fake = temp / "codex"
    fake.write_text(FAKE_CODEX, encoding="utf-8")
    fake.chmod(0o755)
    sessions = temp / "sessions"
    output = temp / "quota.json"
    request_log = temp / "requests.jsonl"

    live, _elapsed = run(
        {
            "POCKETDS_CODEX_BIN": str(fake),
            "POCKETDS_CODEX_SESSIONS": str(sessions),
            "POCKETDS_CODEX_NOW": "1788000000",
            "FAKE_REQUEST_LOG": str(request_log),
        },
        output,
    )
    assert live["available"] is True
    assert live["fresh"] is True
    assert live["source"] == "app-server"
    assert live["limit_id"] == "codex"
    assert live["primary_used_percent"] == 74
    assert live["primary_window_minutes"] == 10080
    assert live["secondary_used_percent"] is None
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert output.stat().st_nlink == 1
    requests = [json.loads(line) for line in request_log.read_text().splitlines()]
    assert requests[-1] == {"method": "account/rateLimits/read", "params": None}

    desktop, _elapsed = run(
        {"POCKETDS_CODEX_BIN": str(fake), "FAKE_MODE": "desktop-metadata"}, output
    )
    assert desktop["available"] is True
    assert desktop["primary_used_percent"] == 74
    keyed, _elapsed = run(
        {"POCKETDS_CODEX_BIN": str(fake), "FAKE_MODE": "keyed-bucket"}, output
    )
    assert keyed["primary_used_percent"] == 90

    # A desktop-only install has neither ~/.local/bin/codex nor a PATH entry.
    # Respect explicit overrides and standalone CLI precedence as before.
    find_codex = COLLECTOR_MODULE["find_codex"]
    with mock.patch.dict(os.environ, {}, clear=True), \
            mock.patch.object(Path, "home", return_value=temp / "empty-home"), \
            mock.patch("shutil.which", return_value=None), \
            mock.patch.dict(find_codex.__globals__, {"BUNDLED_CODEX_PATHS": (str(fake),)}):
        assert find_codex() == str(fake)
        with mock.patch.dict(os.environ, {"POCKETDS_CODEX_BIN": str(temp / "missing")}):
            assert find_codex() is None
        with mock.patch.dict(find_codex.__globals__, {"BUNDLED_CODEX_PATHS": ()}):
            assert find_codex() is None
        standalone = temp / "standalone-codex"
        standalone.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        standalone.chmod(0o755)
        with mock.patch("shutil.which", return_value=str(standalone)):
            assert find_codex() == str(standalone)

    for mode in ("string-percent", "float-percent", "bad-percent"):
        invalid, _elapsed = run(
            {
                "POCKETDS_CODEX_BIN": str(fake),
                "POCKETDS_CODEX_SESSIONS": str(temp / f"empty-{mode}"),
                "POCKETDS_CODEX_NOW": "1788000000",
                "FAKE_MODE": mode,
            },
            output,
        )
        assert invalid["available"] is False
        assert invalid["source"] == "unavailable"

    # Session logs contain complete conversations, are mutable, and are not a
    # canonical quota API.  Even a fresh-looking real-shape event must never be
    # parsed or emitted when app-server is unavailable.
    session = sessions / "2026/08/29/rollout.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                "timestamp": "2026-08-29T00:00:00.000Z",
                "payload": {
                    "privateConversation": "SESSION-SECRET-DO-NOT-READ",
                    "rate_limits": {
                        "limit_id": "codex",
                        "limit_name": None,
                        "primary": {
                            "used_percent": 61.0,
                            "resets_at": 1788452789,
                            "window_minutes": 10080,
                        },
                        "secondary": None,
                        "credits": {"has_credits": False, "unlimited": False},
                        "individual_limit": None,
                        "spend_control_reached": None,
                        "plan_type": "prolite",
                        "rate_limit_reached_type": None,
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    session.chmod(0o644)
    no_live, elapsed = run(
        {
            "POCKETDS_CODEX_BIN": str(temp / "missing-codex"),
            "POCKETDS_CODEX_SESSIONS": str(sessions),
            "POCKETDS_CODEX_NOW": "1788000000",
        },
        output,
    )
    assert no_live["available"] is False
    assert no_live["fresh"] is False
    assert no_live["source"] == "unavailable"
    assert "SESSION-SECRET-DO-NOT-READ" not in output.read_text(encoding="utf-8")
    assert elapsed < 2

    for invalid_timeout in ("nan", "inf", "-1", "60"):
        unavailable, elapsed = run(
            {
                "POCKETDS_CODEX_BIN": str(fake),
                "POCKETDS_CODEX_SESSIONS": str(temp / "no-sessions"),
                "POCKETDS_CODEX_TIMEOUT": invalid_timeout,
            },
            output,
        )
        assert unavailable["available"] is False
        assert elapsed < 2

    too_long, elapsed = run(
        {
            "POCKETDS_CODEX_BIN": str(fake),
            "POCKETDS_CODEX_SESSIONS": str(temp / "no-sessions"),
            "POCKETDS_CODEX_TIMEOUT": "0.3",
            "FAKE_MODE": "long",
        },
        output,
    )
    assert too_long["available"] is False
    assert elapsed < 3

    child_pid_file = temp / "child.pid"
    timed_out, elapsed = run(
        {
            "POCKETDS_CODEX_BIN": str(fake),
            "POCKETDS_CODEX_SESSIONS": str(temp / "no-sessions"),
            "POCKETDS_CODEX_TIMEOUT": "0.3",
            "FAKE_MODE": "hang",
            "FAKE_CHILD_PID": str(child_pid_file),
        },
        output,
    )
    assert timed_out["available"] is False
    assert elapsed < 3
    child_pid = int(child_pid_file.read_text())
    for _attempt in range(50):
        if not process_exists(child_pid):
            break
        time.sleep(0.02)
    assert not process_exists(child_pid)

for token in (
    "MAX_APP_SERVER_STDOUT_BYTES",
    "MAX_APP_SERVER_LINE_BYTES",
    "O_NOFOLLOW",
    "os.fchmod(fd, 0o600)",
    "os.fsync(fd)",
    "os.fsync(directory)",
    "os.killpg(process.pid, signal.SIGKILL)",
):
    assert token in COLLECTOR_SOURCE, token

for forbidden in (
    "POCKETDS_CODEX_SESSIONS",
    "session-log",
    "newest_session_rate_limits",
):
    assert forbidden not in COLLECTOR_SOURCE, forbidden

for token in (
    "property bool quotaAvailable: false",
    "property bool quotaFresh: false",
    "property bool quotaTelemetryValid: false",
    "function validQuotaFields(quota)",
    "readonly property int quotaMaximumAgeS: 360",
    "isIntegerBetween(quota.synced_at, 1, 9e15)",
    "quota.primary_used_percent !== null",
    "quota.primary_window_minutes !== null",
    "function quotaWindowText(minutes)",
    "quotaAgeS <= quotaMaximumAgeS",
    'return "额度数据已过期"',
):
    assert token in QML, token
assert "Number(s.quota.primary_used_percent || 0)" not in QML
assert "Number(s.quota.secondary_used_percent || 0)" not in QML

print("  [OK] Codex quota is canonical app-server only, bounded, private and fail-closed")

#!/usr/bin/env python3
"""Emit a privacy-minimal, read-only PDS-013 phone integration preflight."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time
from collections.abc import Callable


SCHEMA = "pocketds.phone-integration-preflight.v2"
MAX_OUTPUT_BYTES = 4096
COMMAND_TIMEOUT_SECONDS = 5.0
KDECONNECT_PORTS = {"1714-1764/tcp", "1714-1764/udp"}

# Every command is fixed and read-only. In particular, this list deliberately
# excludes nmcli, address/route inspection and kdeconnect-cli peer discovery.
COMMANDS: dict[str, tuple[str, ...]] = {
    "kde_connect_rpm": ("rpm", "-q", "--quiet", "kde-connect"),
    "kdeconnectd_rpm": ("rpm", "-q", "--quiet", "kdeconnectd"),
    # Plasma may D-Bus activate kdeconnectd before its generated autostart unit;
    # the well-known bus owner is the stable liveness signal.
    "kdeconnect_daemon": (
        "busctl",
        "--user",
        "call",
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameHasOwner",
        "s",
        "org.kde.kdeconnect",
    ),
    "firewalld": ("sudo", "-n", "firewall-cmd", "--state"),
    "trusted_zone": (
        "sudo",
        "-n",
        "firewall-cmd",
        "--zone=pocketds-home",
        "--query-service=kdeconnect",
    ),
    "public_zone": (
        "sudo",
        "-n",
        "firewall-cmd",
        "--zone=public",
        "--query-service=kdeconnect",
    ),
    "kdeconnect_ports": (
        "sudo",
        "-n",
        "firewall-cmd",
        "--permanent",
        "--service=kdeconnect",
        "--get-ports",
    ),
    "localsend_rpm": ("rpm", "-q", "--quiet", "localsend"),
    # Tailscale is an optional, independent user choice. Its presence or absence
    # must not make the local KDE Connect preflight pass or fail.
}


class ProbeError(RuntimeError):
    """A bounded probe could not produce a trustworthy result."""


@dataclass(frozen=True)
class ProbeResult:
    returncode: int
    stdout: bytes


def run_bounded(command: tuple[str, ...]) -> ProbeResult:
    """Run one allowlisted command with bounded time and output."""

    environment = os.environ.copy()
    environment.update({"LC_ALL": "C", "LANG": "C"})
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except OSError as exc:
        raise ProbeError("probe could not start") from exc

    assert process.stdout is not None and process.stderr is not None
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    selector.register(process.stderr, selectors.EVENT_READ)
    deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError("probe timed out")
            events = selector.select(min(remaining, 0.5))
            if not events and process.poll() is not None:
                events = [
                    (key, selectors.EVENT_READ)
                    for key in selector.get_map().values()
                ]
            for key, _mask in events:
                stream = key.fileobj
                chunk = os.read(stream.fileno(), 4096)
                if not chunk:
                    selector.unregister(stream)
                    continue
                streams[stream].extend(chunk)
                if len(streams[stream]) > MAX_OUTPUT_BYTES:
                    raise ProbeError("probe exceeded output bound")
        try:
            returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise ProbeError("probe timed out") from exc
    except (OSError, ProbeError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return ProbeResult(returncode, bytes(streams[process.stdout]))


def safe_probe(
    runner: Callable[[tuple[str, ...]], ProbeResult], command_id: str
) -> ProbeResult | None:
    try:
        return runner(COMMANDS[command_id])
    except (OSError, subprocess.SubprocessError, ProbeError):
        return None


def exact_text(result: ProbeResult | None, returncode: int, expected: str) -> bool:
    if result is None or result.returncode != returncode:
        return False
    try:
        observed = result.stdout.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError:
        return False
    return observed == expected


def quiet_status(result: ProbeResult | None, returncode: int) -> bool:
    return (
        result is not None
        and result.returncode == returncode
        and result.stdout.strip() == b""
    )


def build_report(
    runner: Callable[[tuple[str, ...]], ProbeResult] = run_bounded,
) -> dict[str, object]:
    results = {name: safe_probe(runner, name) for name in COMMANDS}
    ports_ok = False
    port_result = results["kdeconnect_ports"]
    if port_result is not None and port_result.returncode == 0:
        try:
            ports = set(port_result.stdout.decode("ascii", errors="strict").split())
        except UnicodeDecodeError:
            ports = set()
        ports_ok = ports == KDECONNECT_PORTS

    checks = {
        "kde_connect_rpm_installed": quiet_status(results["kde_connect_rpm"], 0),
        "kdeconnectd_rpm_installed": quiet_status(results["kdeconnectd_rpm"], 0),
        "kdeconnect_daemon_active": exact_text(
            results["kdeconnect_daemon"], 0, "b true"
        ),
        "firewalld_running": exact_text(results["firewalld"], 0, "running"),
        "trusted_zone_allows_kdeconnect": exact_text(
            results["trusted_zone"], 0, "yes"
        ),
        "public_zone_blocks_kdeconnect": exact_text(results["public_zone"], 1, "no"),
        "kdeconnect_service_ports_exact": ports_ok,
        "localsend_rpm_absent": quiet_status(results["localsend_rpm"], 1),
    }
    return {
        "schema": SCHEMA,
        "read_only": True,
        "privacy": {
            "network_identity_collected": False,
            "peer_identity_collected": False,
            "credentials_collected": False,
        },
        "checks": checks,
        "preflight_accepted": all(checks.values()),
        "physical_phone_acceptance": "not_run",
    }


def write_report(report: dict[str, object], destination: str) -> None:
    payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if destination == "-":
        sys.stdout.buffer.write(payload)
        return
    path = Path(destination)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short report write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="-",
        help="new JSON report path, or - for stdout (default: -)",
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="return non-zero unless the read-only preflight passes",
    )
    arguments = parser.parse_args()
    report = build_report()
    try:
        write_report(report, arguments.output)
    except OSError as exc:
        print(f"phone-integration report write failed: {exc}", file=sys.stderr)
        return 2
    if arguments.require_pass and not report["preflight_accepted"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

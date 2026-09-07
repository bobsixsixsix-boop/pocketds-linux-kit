#!/usr/bin/env python3
"""Run one supervised, fixed PDS-002 WebGL2 + collector matrix cell."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
COLLECTOR_PATH = REPO_ROOT / "scripts" / "pds002-gpu-kwin-baseline.py"
CONFIRMATION = "PDS002-RUN-IFPC-WEBGL-V1"
RUN_SCHEMA = "pocketds.kernel-ab-workload-run.v1"
GIT = "/usr/bin/git"
SYSTEMCTL = "/usr/bin/systemctl"
SESSION_KEYS = {"XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS"}


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


preflight = load_module("pocketds_kernel_ab_workload_run_preflight", HERE / "preflight.py")
evaluate = load_module("pocketds_kernel_ab_workload_run_evaluate", HERE.parent / "ab-evaluate.py")
collector = load_module("pocketds_kernel_ab_workload_run_collector", COLLECTOR_PATH)


class RunError(RuntimeError):
    """The formal run cannot safely start or did not complete."""


def expected_basename(variant: str, profile: str, round_number: int) -> str:
    return f"round-{round_number:02d}-{variant}-{profile}.jsonl"


def run_fixed(command: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "LC_ALL": "C", "LANG": "C"},
    )


def repository_revision() -> str:
    revision = run_fixed([GIT, "-C", os.fspath(REPO_ROOT), "rev-parse", "HEAD"])
    status = run_fixed(
        [GIT, "-C", os.fspath(REPO_ROOT), "status", "--porcelain=v1", "--untracked-files=normal"]
    )
    value = revision.stdout.strip()
    if revision.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise RunError("repository revision is unavailable")
    if status.returncode != 0 or status.stdout:
        raise RunError("repository must be clean before a formal run")
    return value


def session_environment(uid: int) -> dict[str, str]:
    completed = run_fixed([SYSTEMCTL, "--user", "show-environment"])
    if completed.returncode != 0 or len(completed.stdout) > 256 * 1024:
        raise RunError("desktop session environment is unavailable")
    values: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in SESSION_KEYS:
            if key in values:
                raise RunError("desktop session environment has duplicate keys")
            values[key] = value
    runtime = Path(values.get("XDG_RUNTIME_DIR", ""))
    expected_runtime = Path(f"/run/user/{uid}")
    if runtime != expected_runtime:
        raise RunError("desktop runtime directory differs")
    metadata = os.lstat(runtime)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != uid
    ):
        raise RunError("desktop runtime directory metadata differs")
    wayland = values.get("WAYLAND_DISPLAY", "")
    if re.fullmatch(r"wayland-[0-9]+", wayland) is None:
        raise RunError("Wayland display identity differs")
    wayland_metadata = os.stat(runtime / wayland, follow_symlinks=False)
    if not stat.S_ISSOCK(wayland_metadata.st_mode) or wayland_metadata.st_uid != uid:
        raise RunError("Wayland socket identity differs")
    if values.get("DBUS_SESSION_BUS_ADDRESS") != f"unix:path={runtime}/bus":
        raise RunError("desktop D-Bus identity differs")
    display = values.get("DISPLAY", "")
    if display and re.fullmatch(r":[0-9]+(?:\.[0-9]+)?", display) is None:
        raise RunError("X11 display identity differs")
    return {**os.environ, **values}


def browser_process_count(binary: Path = Path(preflight.EXPECTED_BROWSER_PATHS["binary"])) -> int:
    expected = os.stat(binary, follow_symlinks=False)
    count = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            measured = os.stat(entry / "exe")
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if (measured.st_dev, measured.st_ino) == (expected.st_dev, expected.st_ino):
            count += 1
    return count


def live_preflight(profile: str, variant: str, lock: dict[str, Any]) -> dict[str, object]:
    notes = collector.kernel_notes_snapshot()
    if notes != {
        "sha256": lock["variants"][variant]["notes_sha256"],
        "size": 128,
        "error": None,
    }:
        raise RunError("running kernel is not the declared variant")
    display = collector.display_snapshot(Path("/sys/class/drm"))
    if not evaluate.profile_matches(display, lock["profiles"][profile]):
        raise RunError("live display does not match the declared profile")
    fixed_controls = collector.fixed_control_snapshot()
    if not evaluate.control_set_matches(
        fixed_controls, lock["fixed_controls"]
    ):
        raise RunError("fixed KWin, firmware or fan controller differs")
    runtime_controls = collector.runtime_control_snapshot()
    if not evaluate.control_set_matches(
        runtime_controls, lock["runtime_controls"]
    ):
        raise RunError(
            "formal runs require pocketds-performance and aggressive fan profile"
        )
    existing = browser_process_count()
    if existing != 0:
        raise RunError("close every PocketDS Chromium process before a formal run")
    return {
        "kernel_notes_sha256": notes["sha256"],
        "display_signature": display["signature"],
        "fixed_controls": fixed_controls,
        "runtime_controls": runtime_controls,
        "preexisting_workload_browser_processes": existing,
    }


def reserve_output(path: Path, expected: str) -> tuple[int, int]:
    if path.name != expected:
        raise RunError("output basename differs from the matrix cell")
    parent = path.parent
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        parent_fd = os.open(parent, flags)
    except OSError as exc:
        raise RunError("output parent is unavailable or linked") from exc
    try:
        metadata = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.getuid()
        ):
            raise RunError("output parent must be a private owned mode-0700 directory")
        output_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        output_flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path.name, output_flags, 0o600, dir_fd=parent_fd)
        except OSError as exc:
            raise RunError("output must be a new regular file") from exc
        output_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(output_metadata.st_mode)
            or stat.S_IMODE(output_metadata.st_mode) != 0o600
            or output_metadata.st_uid != os.getuid()
            or output_metadata.st_nlink != 1
        ):
            os.close(descriptor)
            raise RunError("reserved output metadata differs")
        return descriptor, parent_fd
    except Exception:
        os.close(parent_fd)
        raise


def build_commands(
    manifest: dict[str, Any], manifest_sha256: str, profile_dir: Path, revision: str
) -> tuple[list[str], list[str]]:
    asset_uri = (HERE / manifest["asset"]["filename"]).as_uri()
    browser = [manifest["browser"]["wrapper"]["path"]]
    for argument in manifest["arguments"]:
        browser.append(
            argument.replace("{asset_uri}", asset_uri).replace(
                "{private_profile}", os.fspath(profile_dir)
            )
        )
    collector_command = [
        os.fspath(COLLECTOR_PATH),
        "--duration", str(manifest["duration_seconds"]),
        "--interval", "5",
        "--display-interval", "60",
        "--burst-gap", "5",
        "--repo-revision", revision,
        "--workload-sha256", manifest_sha256,
    ]
    return browser, collector_command


def terminate_owned(process: subprocess.Popen[Any], grace_seconds: float = 10.0) -> None:
    process_group = process.pid
    try:
        if process.poll() is None and os.getpgid(process.pid) != process_group:
            raise RunError("owned process group identity differs")
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            process.wait(timeout=5)
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return
    os.killpg(process_group, signal.SIGKILL)


def execute(args: argparse.Namespace, manifest: dict[str, Any], manifest_sha256: str, lock: dict[str, Any]) -> dict[str, object]:
    preflight.collect(manifest, manifest_sha256)
    revision = repository_revision()
    environment = session_environment(os.getuid())
    live = live_preflight(args.profile, args.variant, lock)
    expected = expected_basename(args.variant, args.profile, args.round)
    output_fd, parent_fd = reserve_output(args.output, expected)
    runtime = Path(environment["XDG_RUNTIME_DIR"])
    profile_dir: Path | None = None
    browser_process: subprocess.Popen[Any] | None = None
    collector_process: subprocess.Popen[Any] | None = None
    failure: str | None = None
    try:
        profile_dir = Path(tempfile.mkdtemp(prefix="pds002-ifpc-webgl-", dir=runtime))
        profile_dir.chmod(0o700)
        browser_command, collector_command = build_commands(
            manifest, manifest_sha256, profile_dir, revision
        )
        browser_process = subprocess.Popen(
            browser_command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=True,
            close_fds=True,
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if browser_process.poll() is not None:
                raise RunError("workload browser exited during startup")
            time.sleep(0.1)
        output_stream = os.fdopen(os.dup(output_fd), "wb", buffering=0)
        try:
            collector_process = subprocess.Popen(
                collector_command,
                stdin=subprocess.DEVNULL,
                stdout=output_stream,
                stderr=subprocess.DEVNULL,
                env=environment,
                start_new_session=True,
                close_fds=True,
            )
            while collector_process.poll() is None:
                if browser_process.poll() is not None:
                    failure = "workload_browser_exited_early"
                    terminate_owned(collector_process)
                    break
                time.sleep(1.0)
            if collector_process.returncode != 0 and failure is None:
                failure = "collector_failed"
        finally:
            output_stream.close()
        terminate_owned(browser_process)
        browser_process = None
        os.fsync(output_fd)
    finally:
        if collector_process is not None:
            terminate_owned(collector_process)
        if browser_process is not None:
            terminate_owned(browser_process)
        os.close(output_fd)
        os.fsync(parent_fd)
        os.close(parent_fd)
        if profile_dir is not None:
            shutil.rmtree(profile_dir)
    if browser_process_count() != 0:
        raise RunError("workload browser processes survived cleanup")
    content = args.output.read_bytes()
    result = "completed" if failure is None else failure
    report = {
        "schema": RUN_SCHEMA,
        "result": result,
        "variant": args.variant,
        "profile": args.profile,
        "round": args.round,
        "workload_sha256": manifest_sha256,
        "collector_report": {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)},
        "live_preflight": live,
        "raw_process_ids_recorded": False,
        "candidate_install_authorized": False,
    }
    if failure is not None:
        raise RunError(failure)
    return report


def preflight_report(
    args: argparse.Namespace,
    manifest: dict[str, Any],
    manifest_sha256: str,
    lock: dict[str, Any],
    lock_sha256: str,
) -> dict[str, object]:
    preflight.collect(manifest, manifest_sha256)
    revision = repository_revision()
    session_environment(os.getuid())
    live = live_preflight(args.profile, args.variant, lock)
    return {
        "schema": RUN_SCHEMA,
        "action": "live_preflight",
        "variant": args.variant,
        "profile": args.profile,
        "round": args.round,
        "expected_output_basename": expected_basename(
            args.variant, args.profile, args.round
        ),
        "repository_revision": revision,
        "workload_sha256": manifest_sha256,
        "evaluation_lock_sha256": lock_sha256,
        "live_preflight": live,
        "desktop_session_valid": True,
        "browser_started": False,
        "collector_started": False,
        "display_or_boot_state_changed": False,
        "candidate_install_authorized": False,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("baseline", "candidate"), required=True)
    parser.add_argument(
        "--profile", choices=("dual-165-60", "dual-60-60", "upper-only-165"), required=True
    )
    parser.add_argument("--round", type=int, choices=(1, 2, 3), required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--live-preflight", action="store_true")
    action.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.execute:
        if args.confirm != CONFIRMATION or args.output is None:
            parser.error("execution needs exact confirmation and a new output path")
    elif args.live_preflight:
        if args.confirm is not None or args.output is not None:
            parser.error("live preflight accepts no confirmation or output path")
    elif args.confirm is not None or args.output is not None:
        parser.error("plan-only mode accepts no confirmation or output path")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        manifest, manifest_sha256 = preflight.load_manifest()
        lock, lock_sha256 = evaluate.load_lock()
        if args.live_preflight:
            report = preflight_report(
                args, manifest, manifest_sha256, lock, lock_sha256
            )
        elif not args.execute:
            report = {
                "schema": RUN_SCHEMA,
                "action": "plan_only",
                "variant": args.variant,
                "profile": args.profile,
                "round": args.round,
                "expected_output_basename": expected_basename(
                    args.variant, args.profile, args.round
                ),
                "workload_sha256": manifest_sha256,
                "evaluation_lock_sha256": lock_sha256,
                "live_preflight_performed": False,
                "browser_started": False,
                "collector_started": False,
                "display_or_boot_state_changed": False,
                "candidate_install_authorized": False,
            }
        else:
            report = execute(args, manifest, manifest_sha256, lock)
    except (OSError, RunError, preflight.PreflightError, evaluate.EvaluationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

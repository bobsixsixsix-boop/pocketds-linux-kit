#!/usr/bin/env python3
"""Build an isolated ES-DE synthetic library and optionally run manual GUI acceptance.

The default path only creates and validates a temporary fixture.  It never
looks at the user's ES-DE configuration or ROM tree, starts a GUI, or accesses
the network.  Real GUI launches require both ``--run-gui`` and the exact
``POCKETDS_ALLOW_ESDE_GUI_TEST=YES`` environment opt-in.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
PREFLIGHT_SOURCE = ROOT / "scripts" / "pocketds-rom-preflight.py"
LOG_SUMMARY_SOURCE = ROOT / "scripts" / "pds012-esde-log-summary.py"
LAUNCHER = ROOT / "components" / "emulation" / "pocketds-es-de"
PREPARE = ROOT / "components" / "emulation" / "pocketds-es-de-prepare.py"
GAME_RUNTIME = ROOT / "components" / "game-runtime" / "pocketds-game-runtime"
GUI_CONFIRMATION_ENV = "POCKETDS_ALLOW_ESDE_GUI_TEST"
GUI_CONFIRMATION = "POCKETDS-RUN-ESDE-SYNTHETIC-GUI"
GUI_REPORT_SCHEMA = "pocketds.esde-synthetic-gui.v1"
SYNTHETIC_EXTENSION = "pdsfixture"
MAX_CANDIDATES = 250_000
MAX_STATUS_BYTES = 256
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class FixtureSummary:
    systems: int
    games_per_system: int
    candidates: int
    regular_files: int
    symlinks: int
    special_files: int
    stopped: str
    logical_sha256: str


@dataclass(frozen=True)
class GuiIteration:
    iteration: int
    startup_seconds: float
    shutdown_seconds: float
    log_bytes: int
    launcher_status: int
    log_startup_ms: int
    complete_session_observed: bool
    summary_has_errors: bool
    input_mode_restored: bool


def _load_preflight():
    spec = importlib.util.spec_from_file_location(
        "pocketds_synthetic_rom_preflight", PREFLIGHT_SOURCE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load metadata preflight: {PREFLIGHT_SOURCE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_log_summary():
    spec = importlib.util.spec_from_file_location(
        "pocketds_synthetic_esde_log_summary", LOG_SUMMARY_SOURCE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load ES-DE log summary")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_xml(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    tree = ET.ElementTree(root)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _logical_fixture_hash(rom_root: Path) -> str:
    """Hash only relative names and sizes, never regular-file contents."""

    digest = hashlib.sha256()
    for path in sorted(rom_root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("synthetic fixture unexpectedly contains a symbolic link")
        relative = path.relative_to(rom_root).as_posix()
        kind = "d" if path.is_dir() else "f"
        size = 0 if path.is_dir() else path.stat().st_size
        digest.update(f"{kind}\0{relative}\0{size}\n".encode("utf-8"))
    return digest.hexdigest()


def build_fixture(home: Path, systems: int, games_per_system: int) -> FixtureSummary:
    """Create a copyright-free ES-DE fixture entirely below *home*."""

    home = home.resolve(strict=True)
    # The production launcher requires the effective ROMDirectory to be
    # exactly $HOME/ROMs before it will start ES-DE.  Keep the isolated
    # fixture under that same contract so the attended harness exercises the
    # real launcher instead of failing in its configuration preflight.
    rom_root = home / "ROMs"
    settings = home / "ES-DE" / "settings" / "es_settings.xml"
    custom_systems = home / "ES-DE" / "custom_systems" / "es_systems.xml"
    managed_systems = (
        home
        / ".local"
        / "share"
        / "pocketds-linux-kit"
        / "emulation"
        / "es_systems.xml"
    )

    settings_root = ET.Element("settings")
    rom_setting = ET.SubElement(settings_root, "string")
    rom_setting.set("name", "ROMDirectory")
    rom_setting.set("value", str(rom_root))
    ET.SubElement(
        settings_root,
        "bool",
        {"name": "BackgroundJoystickInput", "value": "false"},
    )
    _write_xml(settings, settings_root)

    system_list = ET.Element("systemList")
    for system_number in range(1, systems + 1):
        system_name = f"pds-synthetic-{system_number:02d}"
        system_root = rom_root / system_name
        system_root.mkdir(parents=True)

        game_list = ET.Element("gameList")
        for game_number in range(1, games_per_system + 1):
            filename = f"fixture-{game_number:05d}.{SYNTHETIC_EXTENSION}"
            (system_root / filename).touch()
            game = ET.SubElement(game_list, "game")
            ET.SubElement(game, "path").text = f"./{filename}"
            ET.SubElement(game, "name").text = (
                f"Synthetic {system_number:02d}-{game_number:05d}"
            )
            ET.SubElement(game, "desc").text = (
                "Generated empty-file fixture; contains no game data or media."
            )
        _write_xml(
            home / "ES-DE" / "gamelists" / system_name / "gamelist.xml",
            game_list,
        )

        system = ET.SubElement(system_list, "system")
        ET.SubElement(system, "name").text = system_name
        ET.SubElement(system, "fullname").text = (
            f"Pocket DS Synthetic {system_number:02d}"
        )
        ET.SubElement(system, "path").text = str(system_root)
        ET.SubElement(system, "extension").text = f".{SYNTHETIC_EXTENSION}"
        ET.SubElement(system, "command", {"label": "Disabled fixture"}).text = (
            "/usr/bin/false %ROM%"
        )

    _write_xml(custom_systems, system_list)
    managed_systems.parent.mkdir(parents=True, exist_ok=True)
    managed_systems.write_bytes(custom_systems.read_bytes())

    preflight = _load_preflight()
    result = preflight.scan(
        rom_root,
        {SYNTHETIC_EXTENSION},
        timeout_seconds=30.0,
        max_entries=MAX_CANDIDATES + systems * 2,
        max_depth=8,
    )
    expected = systems * games_per_system
    if result.stopped != "complete":
        raise RuntimeError(f"synthetic metadata preflight stopped: {result.stopped}")
    if result.candidate_roms != expected:
        raise RuntimeError(
            f"synthetic candidate mismatch: {result.candidate_roms} != {expected}"
        )
    if result.symlinks or result.special_files:
        raise RuntimeError("synthetic fixture contains a symlink or special file")

    return FixtureSummary(
        systems=systems,
        games_per_system=games_per_system,
        candidates=result.candidate_roms,
        regular_files=result.regular_files,
        symlinks=result.symlinks,
        special_files=result.special_files,
        stopped=result.stopped,
        logical_sha256=_logical_fixture_hash(rom_root),
    )


def _validate_counts(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not 1 <= args.systems <= 50:
        parser.error("--systems must be between 1 and 50")
    if not 1 <= args.games_per_system <= 10_000:
        parser.error("--games-per-system must be between 1 and 10000")
    if args.systems * args.games_per_system > MAX_CANDIDATES:
        parser.error(f"synthetic candidates must not exceed {MAX_CANDIDATES}")
    if not 1 <= args.iterations <= 100:
        parser.error("--iterations must be between 1 and 100")
    if not 0 < args.startup_timeout <= 60:
        parser.error("GUI startup timeout must be between 0 and 60 seconds")
    if not 0 < args.shutdown_timeout <= 30:
        parser.error("GUI shutdown timeout must be between 0 and 30 seconds")


def _require_gui_opt_in() -> None:
    if os.environ.get(GUI_CONFIRMATION_ENV) != "YES":
        raise RuntimeError(
            f"GUI test refused: set {GUI_CONFIRMATION_ENV}=YES and pass --run-gui"
        )
    if not sys.stdin.isatty():
        raise RuntimeError("GUI test refused: an interactive terminal is required")


def _input_status(input_mode: Path) -> str:
    try:
        result = subprocess.run(
            [str(input_mode), "status"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("InputPlumber status probe failed") from exc
    if result.returncode != 0 or len(result.stdout) > MAX_STATUS_BYTES:
        raise RuntimeError("InputPlumber status probe failed or overflowed")
    try:
        value = result.stdout.decode("ascii", errors="strict").strip()
    except UnicodeError as exc:
        raise RuntimeError("InputPlumber status is not ASCII") from exc
    if value not in {"joymouse", "gamepad"}:
        raise RuntimeError("InputPlumber status is unmanaged")
    return value


def _repo_identity() -> tuple[str, bool]:
    outputs: list[bytes] = []
    for arguments in (("rev-parse", "HEAD"), ("status", "--porcelain=v1")):
        try:
            result = subprocess.run(
                ["/usr/bin/git", "-C", str(ROOT), *arguments],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("repository identity probe failed") from exc
        if result.returncode != 0 or len(result.stdout) > 64 * 1024:
            raise RuntimeError("repository identity probe failed or overflowed")
        outputs.append(result.stdout)
    try:
        revision = outputs[0].decode("ascii", errors="strict").strip()
    except UnicodeError as exc:
        raise RuntimeError("repository revision is not ASCII") from exc
    if REVISION_RE.fullmatch(revision) is None:
        raise RuntimeError("repository revision is invalid")
    return revision, not outputs[1].strip()


def _wait_for_operator(
    process: subprocess.Popen[bytes], timeout_seconds: float, iteration: int
) -> float:
    started = time.monotonic()
    deadline = started + timeout_seconds
    print(
        f"[{iteration}] Confirm the ES-DE window is interactive: Enter=PASS, q=cancel",
        flush=True,
    )
    while time.monotonic() < deadline:
        status = process.poll()
        if status is not None:
            raise RuntimeError(f"launcher exited before confirmation: status={status}")
        readable, _, _ = select.select([sys.stdin], [], [], 0.1)
        if not readable:
            continue
        response = sys.stdin.readline().strip().lower()
        if response in {"q", "quit", "cancel"}:
            raise KeyboardInterrupt
        if not response:
            return time.monotonic() - started
        print("Press Enter to confirm, or q to cancel", flush=True)
    raise TimeoutError(f"GUI was not confirmed within {timeout_seconds:.1f}s")


def _stop_launcher(process: subprocess.Popen[bytes], timeout_seconds: float) -> float:
    started = time.monotonic()
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2.0)
        raise TimeoutError(f"launcher did not exit within {timeout_seconds:.1f}s")
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        pass
    else:
        os.killpg(process.pid, signal.SIGKILL)
        raise RuntimeError("launcher exited but left a process in its session")
    if process.returncode not in {0, -signal.SIGTERM, 128 + signal.SIGTERM}:
        raise RuntimeError(f"launcher exited unexpectedly: status={process.returncode}")
    return time.monotonic() - started


def run_gui_iteration(
    args: argparse.Namespace, iteration: int, appimage: Path, input_mode: Path
) -> tuple[GuiIteration, FixtureSummary]:
    with tempfile.TemporaryDirectory(prefix="pocketds-esde-synthetic-gui-") as temporary:
        home = Path(temporary)
        fixture = build_fixture(home, args.systems, args.games_per_system)
        previous_input = _input_status(input_mode)
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DATA_HOME": str(home / ".local" / "share"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "XDG_STATE_HOME": str(home / ".local" / "state"),
                "ESDE_APPDATA_DIR": str(home / "ES-DE"),
                "POCKETDS_ESDE_APPIMAGE": str(appimage),
                "POCKETDS_INPUT_MODE": str(input_mode),
                "POCKETDS_ESDE_PREPARE": str(PREPARE),
                # This fixture evaluates ES-DE itself, not the presentation
                # compositor.  Keep its temporary HOME isolated while still
                # traversing the launcher's new runtime boundary.
                "POCKETDS_GAME_RUNTIME": str(GAME_RUNTIME),
                "POCKETDS_DISABLE_GAMESCOPE": "1",
            }
        )
        process = subprocess.Popen(
            [
                str(LAUNCHER),
                "--home",
                str(home),
                "--no-splash",
                "--no-update-check",
            ],
            env=environment,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            startup_seconds = _wait_for_operator(
                process, args.startup_timeout, iteration
            )
            shutdown_seconds = _stop_launcher(process, args.shutdown_timeout)
        except BaseException:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=args.shutdown_timeout)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            raise

        log_path = home / "ES-DE" / "logs" / "es_log.txt"
        log_summary = _load_log_summary()
        log_text, log_bytes = log_summary.read_log(log_path)
        summary = log_summary.summarize(log_text, log_bytes)
        startup_ms = summary["startup"]["latest_ms"]
        if type(startup_ms) is not int or startup_ms < 0:
            raise RuntimeError("isolated ES-DE log has no startup sample")
        restored = _input_status(input_mode) == previous_input
        return (
            GuiIteration(
                iteration=iteration,
                startup_seconds=round(startup_seconds, 3),
                shutdown_seconds=round(shutdown_seconds, 3),
                log_bytes=log_bytes,
                launcher_status=process.returncode,
                log_startup_ms=startup_ms,
                complete_session_observed=bool(summary["complete_session_observed"]),
                summary_has_errors=bool(summary["summary_has_errors"]),
                input_mode_restored=restored,
            ),
            fixture,
        )


def evaluate_gui(
    fixture: FixtureSummary,
    iterations: list[GuiIteration],
    *,
    startup_limit: float,
    shutdown_limit: float,
    repo_before: tuple[str, bool],
    repo_after: tuple[str, bool],
) -> dict[str, object]:
    if not 0 < startup_limit <= 60 or not 0 < shutdown_limit <= 30:
        raise RuntimeError("GUI evaluation limits are invalid")
    revision_before, clean_before = repo_before
    revision_after, clean_after = repo_after
    revision_stable = bool(
        REVISION_RE.fullmatch(revision_before)
        and revision_before == revision_after
        and clean_before is True
        and clean_after is True
    )
    gates = {
        "repo_clean_revision_unchanged": revision_stable,
        "ten_fresh_iterations": len(iterations) >= 10,
        "synthetic_candidates_at_least_5000": fixture.candidates >= 5_000,
        "fixture_complete_no_links_or_special": bool(
            fixture.stopped == "complete"
            and fixture.symlinks == 0
            and fixture.special_files == 0
            and fixture.candidates == fixture.systems * fixture.games_per_system
        ),
        "operator_interactive_within_limit": bool(
            iterations
            and all(0 <= item.startup_seconds <= startup_limit for item in iterations)
        ),
        "internal_startup_within_limit": bool(
            iterations
            and all(0 <= item.log_startup_ms <= startup_limit * 1_000 for item in iterations)
        ),
        "launcher_shutdown_within_limit": bool(
            iterations
            and all(0 <= item.shutdown_seconds <= shutdown_limit for item in iterations)
        ),
        "logs_nonempty_complete_and_error_free": bool(
            iterations
            and all(
                item.log_bytes > 0
                and item.complete_session_observed
                and not item.summary_has_errors
                for item in iterations
            )
        ),
        "input_mode_restored_every_iteration": bool(
            iterations and all(item.input_mode_restored for item in iterations)
        ),
        "launcher_status_expected": bool(
            iterations
            and all(
                item.launcher_status
                in {0, -signal.SIGTERM, 128 + signal.SIGTERM}
                for item in iterations
            )
        ),
    }
    accepted = all(gates.values())
    return {
        "schema": GUI_REPORT_SCHEMA,
        "mode": "attended-isolated-synthetic-gui",
        "privacy": {
            "paths_emitted": False,
            "rom_names_emitted": False,
            "raw_log_lines_emitted": False,
            "input_device_names_emitted": False,
        },
        "fixture": asdict(fixture),
        "repo_revision": revision_before if revision_stable else None,
        "iterations": [asdict(item) for item in iterations],
        "startup_limit_seconds": startup_limit,
        "shutdown_limit_seconds": shutdown_limit,
        "gates": gates,
        "accepted": accepted,
        "result": "PASS" if accepted else "INCOMPLETE",
    }


def write_gui_report(report: dict[str, object], destination: Path) -> None:
    content = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(destination, flags, 0o600)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short ES-DE GUI report write")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args() -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--prepare-only",
        action="store_true",
        help="build and validate a temporary fixture without starting ES-DE (default)",
    )
    mode.add_argument(
        "--run-gui",
        action="store_true",
        help="manually confirm an isolated real ES-DE GUI launch",
    )
    parser.add_argument("--systems", type=int, default=5)
    parser.add_argument("--games-per-system", type=int, default=1_000)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--startup-timeout", type=float, default=10.0)
    parser.add_argument("--shutdown-timeout", type=float, default=3.0)
    parser.add_argument("--appimage", type=Path)
    parser.add_argument("--input-mode", type=Path)
    parser.add_argument("--confirm")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    _validate_counts(parser, args)
    if args.run_gui:
        if args.confirm != GUI_CONFIRMATION or args.output is None:
            parser.error("GUI run needs the exact confirmation and a new --output path")
        if args.iterations < 10 or args.systems * args.games_per_system < 5_000:
            parser.error("GUI acceptance needs at least 10 iterations and 5000 candidates")
    elif args.confirm is not None or args.output is not None:
        parser.error("--confirm/--output are valid only with --run-gui")
    return parser, args


def main() -> int:
    parser, args = parse_args()
    if not args.run_gui:
        with tempfile.TemporaryDirectory(prefix="pocketds-esde-synthetic-") as temporary:
            summary = build_fixture(
                Path(temporary), args.systems, args.games_per_system
            )
            print(
                json.dumps(
                    {"mode": "prepare-only", "fixture": asdict(summary)},
                    sort_keys=True,
                )
            )
        return 0

    try:
        _require_gui_opt_in()
    except RuntimeError as exc:
        parser.error(str(exc))
    assert args.output is not None
    if args.output.exists() or args.output.is_symlink():
        parser.error("GUI report target already exists")
    if not args.output.parent.is_dir() or args.output.parent.is_symlink():
        parser.error("GUI report parent is unavailable or linked")

    appimage = (args.appimage or Path.home() / "Applications/ES-DE_aarch64.AppImage")
    input_mode = (
        args.input_mode or Path.home() / ".local/libexec/pocketds-input-mode"
    )
    for executable in (LAUNCHER, PREPARE, GAME_RUNTIME, appimage, input_mode):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            parser.error(f"required executable is missing: {executable}")

    repo_before = _repo_identity()
    if not repo_before[1]:
        parser.error("GUI acceptance requires a clean repository")
    iterations: list[GuiIteration] = []
    fixture: FixtureSummary | None = None
    fixture_identity: str | None = None
    try:
        for iteration in range(1, args.iterations + 1):
            result, fixture = run_gui_iteration(
                args, iteration, appimage.resolve(), input_mode.resolve()
            )
            iterations.append(result)
            print(json.dumps(asdict(result), sort_keys=True), flush=True)
            if fixture_identity is None:
                fixture_identity = fixture.logical_sha256
            elif fixture.logical_sha256 != fixture_identity:
                raise RuntimeError("synthetic fixture identity drifted between iterations")
    except KeyboardInterrupt:
        print("GUI acceptance cancelled; temporary fixture removed", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(f"GUI acceptance failed: {exc}", file=sys.stderr)
        return 1

    assert fixture is not None and args.output is not None
    report = evaluate_gui(
        fixture,
        iterations,
        startup_limit=args.startup_timeout,
        shutdown_limit=args.shutdown_timeout,
        repo_before=repo_before,
        repo_after=_repo_identity(),
    )
    write_gui_report(report, args.output)
    print(json.dumps({"accepted": report["accepted"], "result": report["result"]}))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

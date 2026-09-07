#!/usr/bin/env python3
"""Read-only source-versus-installed audit for Pocket DS Panel and keyboard."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Sequence


SCHEMA = "pocketds.ui-deployment-audit.v1"
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024


class AuditError(RuntimeError):
    """An audit input or output violated its bounded file contract."""


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    source: Path
    live: Path
    live_uid: int
    live_mode: int


def artifact_plan(repo_root: Path, home: Path, system_root: Path) -> list[Artifact]:
    user_uid = os.getuid()
    panel = home / ".local/share/plasma/plasmoids/org.pocketds.controlpanel.v3"
    return [
        Artifact(
            "panel-controller-diagram",
            repo_root / "components/control-panel/plasmoid/contents/ui/ControllerDiagram.qml",
            panel / "contents/ui/ControllerDiagram.qml",
            user_uid,
            0o644,
        ),
        Artifact(
            "panel-controller-session",
            repo_root / "components/control-panel/plasmoid/contents/ui/ControllerTestSession.qml",
            panel / "contents/ui/ControllerTestSession.qml",
            user_uid,
            0o644,
        ),
        Artifact(
            "panel-qml",
            repo_root / "components/control-panel/plasmoid/contents/ui/main.qml",
            panel / "contents/ui/main.qml",
            user_uid,
            0o644,
        ),
        Artifact(
            "panel-metadata",
            repo_root / "components/control-panel/plasmoid/metadata.json",
            panel / "metadata.json",
            user_uid,
            0o644,
        ),
        Artifact(
            "deep-suspend-guard",
            repo_root / "components/system/pocketds-deep-suspend.py",
            system_root / "usr/local/libexec/pocketds-deep-suspend",
            0,
            0o755,
        ),
        Artifact(
            "deep-suspend-polkit",
            repo_root / "components/system/90-pocketds-deep-suspend.rules",
            system_root / "etc/polkit-1/rules.d/90-pocketds-deep-suspend.rules",
            0,
            0o644,
        ),
        Artifact(
            "panel-root-helper",
            repo_root / "components/control-panel/pocketds-panel-root",
            system_root / "usr/local/libexec/pocketds-panel-root",
            0,
            0o755,
        ),
        Artifact(
            "plasma-recovery",
            repo_root / "scripts/pocketds-plasma-recovery.py",
            system_root / "usr/local/bin/pocketds-plasma-recovery",
            0,
            0o755,
        ),
        Artifact(
            "plasma-recovery-unit",
            repo_root / "components/control-panel/pocketds-plasma-recovery.service",
            home / ".config/systemd/user/pocketds-plasma-recovery.service",
            user_uid,
            0o644,
        ),
        Artifact(
            "keyboard-main",
            repo_root / "components/keyboard/pocketds-keyboard.py",
            home / ".local/bin/pocketds-keyboard.py",
            user_uid,
            0o755,
        ),
        Artifact(
            "keyboard-adapter",
            repo_root / "components/keyboard/keyboard_adapter.py",
            home / ".local/bin/keyboard_adapter.py",
            user_uid,
            0o644,
        ),
        Artifact(
            "keyboard-geometry",
            repo_root / "components/keyboard/screen_geometry.py",
            home / ".local/bin/screen_geometry.py",
            user_uid,
            0o644,
        ),
        Artifact(
            "keyboard-voice-artifacts",
            repo_root / "components/keyboard/voice_artifacts.py",
            home / ".local/bin/voice_artifacts.py",
            user_uid,
            0o644,
        ),
        Artifact(
            "keyboard-visibility",
            repo_root / "components/keyboard/visibility_state.py",
            home / ".local/bin/visibility_state.py",
            user_uid,
            0o644,
        ),
        Artifact(
            "keyboard-unit",
            repo_root / "components/keyboard/pocketds-keyboard.service",
            home / ".config/systemd/user/pocketds-keyboard.service",
            user_uid,
            0o644,
        ),
    ]


def read_digest(
    path: Path,
    *,
    expected_uid: int,
    expected_mode: int | None,
) -> tuple[str | None, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None, "MISSING"
    except OSError:
        return None, "UNSAFE"
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= MAX_ARTIFACT_BYTES
            or (
                expected_mode is not None
                and stat.S_IMODE(metadata.st_mode) != expected_mode
            )
        ):
            return None, "UNSAFE"
        remaining = metadata.st_size
        digest = hashlib.sha256()
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                return None, "UNSAFE"
            digest.update(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            return None, "UNSAFE"
        return digest.hexdigest(), "READ"
    finally:
        os.close(descriptor)


def audit_artifact(artifact: Artifact) -> dict[str, object]:
    source_hash, source_state = read_digest(
        artifact.source,
        expected_uid=os.getuid(),
        expected_mode=None,
    )
    live_hash, live_state = read_digest(
        artifact.live,
        expected_uid=artifact.live_uid,
        expected_mode=artifact.live_mode,
    )
    if source_state != "READ":
        state = "SOURCE_UNSAFE"
    elif live_state != "READ":
        state = live_state
    elif source_hash == live_hash:
        state = "MATCH"
    else:
        state = "DRIFT"
    return {
        "artifact_id": artifact.artifact_id,
        "state": state,
        "source_sha256": source_hash,
        "live_sha256": live_hash,
        "expected_live_mode": f"{artifact.live_mode:04o}",
    }


def panelctl_audit(
    candidate: Path | None, system_root: Path, *, live_uid: int = 0
) -> dict[str, object]:
    if candidate is None:
        return {
            "artifact_id": "panelctl-binary",
            "state": "NOT_EVALUATED",
            "candidate_sha256": None,
            "live_sha256": None,
            "source_binding": "EXTERNAL_BUILD_REQUIRED",
        }
    candidate_hash, candidate_state = read_digest(
        candidate,
        expected_uid=os.getuid(),
        expected_mode=0o755,
    )
    live_hash, live_state = read_digest(
        system_root / "usr/local/bin/pocketds-panelctl",
        expected_uid=live_uid,
        expected_mode=0o755,
    )
    if candidate_state != "READ":
        state = "CANDIDATE_UNSAFE"
    elif live_state != "READ":
        state = live_state
    elif candidate_hash == live_hash:
        state = "MATCH"
    else:
        state = "DRIFT"
    return {
        "artifact_id": "panelctl-binary",
        "state": state,
        "candidate_sha256": candidate_hash,
        "live_sha256": live_hash,
        "source_binding": "CALLER_SUPPLIED_CANDIDATE",
    }


def evaluate(
    artifacts: list[Artifact],
    candidate: Path | None,
    system_root: Path,
    *,
    panelctl_live_uid: int = 0,
) -> dict[str, object]:
    direct = [audit_artifact(artifact) for artifact in artifacts]
    compiled = panelctl_audit(candidate, system_root, live_uid=panelctl_live_uid)
    all_results = direct + [compiled]
    complete = all(item["state"] == "MATCH" for item in all_results)
    return {
        "schema": SCHEMA,
        "read_only": True,
        "privacy": {
            "absolute_paths_emitted": False,
            "user_or_host_emitted": False,
        },
        "artifacts": all_results,
        "counts": {
            state: sum(item["state"] == state for item in all_results)
            for state in (
                "MATCH",
                "DRIFT",
                "MISSING",
                "UNSAFE",
                "SOURCE_UNSAFE",
                "CANDIDATE_UNSAFE",
                "NOT_EVALUATED",
            )
        },
        "complete": complete,
        "result": "PASS" if complete else "INCOMPLETE",
    }


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short UI audit write")
        view = view[written:]


def write_report(report: dict[str, object], destination: str) -> None:
    content = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if destination == "-":
        sys.stdout.buffer.write(content)
        return
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(destination, flags, 0o600)
    try:
        _write_all(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panelctl-candidate", type=Path)
    parser.add_argument("--output", default="-")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = Path(__file__).resolve().parents[1]
    report = evaluate(
        artifact_plan(repo_root, Path.home(), Path("/")),
        arguments.panelctl_candidate,
        Path("/"),
    )
    try:
        write_report(report, arguments.output)
    except OSError as exc:
        print(f"UI deployment audit failed: {exc}", file=sys.stderr)
        return 2
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

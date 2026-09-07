#!/usr/bin/env python3
"""Evaluate a source-bound, supervised Pocket DS UI/input ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Sequence


EVIDENCE_SCHEMA = "pocketds.ui-input-evidence.v1"
REPORT_SCHEMA = "pocketds.ui-input-evaluation.v1"
TRANSACTION_SCHEMA = "pocketds.ui-update-transaction.v1"
MARKER_SCHEMA = "pocketds.ui-update-marker.v1"
MAX_JSON_BYTES = 512 * 1024
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_COUNT = 10_000
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
REVISION_RE = re.compile(r"[0-9a-f]{40}")

COMMON_FIELDS = {
    "attempts",
    "passes",
    "failures",
    "cancellations",
    "keyboard_restart_delta",
    "gpu_telemetry_restart_delta",
    "inputplumber_restart_delta",
    "observer_confirmed",
}
BINDING_FIELDS = {
    "repo_revision",
    "ui_manifest_sha256",
    "ui_payload_bundle_sha256",
    "joymouse_sha256",
}
CASE_POLICY: dict[str, tuple[int, set[str]]] = {
    "panel-lower-screen-geometry": (
        3,
        {"lower_screen_only", "all_edges_flush", "touch_regions_aligned"},
    ),
    "panel-desktop-roundtrip": (
        20,
        {"desktop_reachable", "panel_reentry_available"},
    ),
    "panel-fullscreen-roundtrip": (
        20,
        {"active_window_only", "fullscreen_state_reversible"},
    ),
    "panel-recovery-graceful": (
        5,
        {
            "plasma_restart_delta",
            "kwin_restart_delta",
            "panel_returned",
            "bounded_recovery",
        },
    ),
    "panel-recovery-signal-fallback": (
        6,
        {
            "sigterm_attempts",
            "sigkill_attempts",
            "plasma_restart_delta",
            "kwin_restart_delta",
            "panel_returned",
            "bounded_recovery",
        },
    ),
    "keyboard-cross-app": (
        10,
        {
            "application_class_count",
            "maximum_show_ms",
            "maximum_hide_ms",
            "text_commit_failures",
            "visibility_stuck_count",
            "lower_screen_only",
        },
    ),
    "keyboard-isolated-xwayland-failure": (
        3,
        {
            "isolated_server_kill_count",
            "host_x11_disruption_count",
            "client_failure_exit_count",
            "bounded_restart_count",
            "visibility_stuck_count",
        },
    ),
    "keyboard-touch-layout": (
        50,
        {
            "miskey_count",
            "visual_hitbox_mismatch_count",
            "minimum_touch_target_px",
            "lower_screen_only",
        },
    ),
    "joymouse-clicks": (
        100,
        {
            "rb_attempts",
            "rt_attempts",
            "rb_failures",
            "rt_failures",
            "wrong_button_count",
            "stuck_button_count",
        },
    ),
    "joymouse-scroll": (
        100,
        {
            "lb_attempts",
            "lt_attempts",
            "lb_failures",
            "lt_failures",
            "wrong_direction_count",
            "stuck_scroll_count",
        },
    ),
    "input-mode-cycle": (
        100,
        {
            "duplicate_input_count",
            "mode_mismatch_count",
            "panel_help_matched_all_states",
            "final_mode",
        },
    ),
}
BOOLEAN_FIELDS = {
    "observer_confirmed",
    "lower_screen_only",
    "all_edges_flush",
    "touch_regions_aligned",
    "desktop_reachable",
    "panel_reentry_available",
    "active_window_only",
    "fullscreen_state_reversible",
    "panel_returned",
    "bounded_recovery",
    "panel_help_matched_all_states",
}
STRING_FIELDS = {"final_mode"}

ARTIFACTS = (
    (
        "panel-controller-diagram",
        "components/control-panel/plasmoid/contents/ui/ControllerDiagram.qml",
        ".local/share/plasma/plasmoids/org.pocketds.controlpanel.v3/contents/ui/ControllerDiagram.qml",
        0o644,
        False,
    ),
    (
        "panel-controller-session",
        "components/control-panel/plasmoid/contents/ui/ControllerTestSession.qml",
        ".local/share/plasma/plasmoids/org.pocketds.controlpanel.v3/contents/ui/ControllerTestSession.qml",
        0o644,
        False,
    ),
    (
        "panel-qml",
        "components/control-panel/plasmoid/contents/ui/main.qml",
        ".local/share/plasma/plasmoids/org.pocketds.controlpanel.v3/contents/ui/main.qml",
        0o644,
        False,
    ),
    (
        "deep-suspend-guard",
        "components/system/pocketds-deep-suspend.py",
        "usr/local/libexec/pocketds-deep-suspend",
        0o755,
        True,
    ),
    (
        "deep-suspend-polkit",
        "components/system/90-pocketds-deep-suspend.rules",
        "etc/polkit-1/rules.d/90-pocketds-deep-suspend.rules",
        0o644,
        True,
    ),
    (
        "panel-root-helper",
        "components/control-panel/pocketds-panel-root",
        "usr/local/libexec/pocketds-panel-root",
        0o755,
        True,
    ),
    (
        "keyboard-main",
        "components/keyboard/pocketds-keyboard.py",
        ".local/bin/pocketds-keyboard.py",
        0o755,
        False,
    ),
    (
        "keyboard-adapter",
        "components/keyboard/keyboard_adapter.py",
        ".local/bin/keyboard_adapter.py",
        0o644,
        False,
    ),
    (
        "keyboard-geometry",
        "components/keyboard/screen_geometry.py",
        ".local/bin/screen_geometry.py",
        0o644,
        False,
    ),
    (
        "keyboard-voice-artifacts",
        "components/keyboard/voice_artifacts.py",
        ".local/bin/voice_artifacts.py",
        0o644,
        False,
    ),
)
REQUIRED_PREIMAGE_ARTIFACTS = {"panel-root-helper"}
JOY_SOURCE = "components/inputplumber/pocketds-joymouse.yaml"
JOY_LIVE = "usr/share/inputplumber/profiles/pocketds-joymouse.yaml"


class InteractionError(RuntimeError):
    """Unsafe, stale, malformed or incomplete supervised UI evidence."""


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short UI/input report write")
        view = view[written:]


def _read_owned(
    path: Path,
    *,
    expected_uid: int,
    expected_mode: int | None,
    maximum: int,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise InteractionError("required UI/input file is unavailable or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= maximum
            or (
                expected_mode is not None
                and stat.S_IMODE(metadata.st_mode) != expected_mode
            )
        ):
            raise InteractionError("required UI/input file identity is unsafe")
        remaining = metadata.st_size
        content = bytearray()
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise InteractionError("required UI/input file changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise InteractionError("required UI/input file grew while reading")
        return bytes(content)
    finally:
        os.close(descriptor)


def read_private_json(path: Path) -> tuple[object, bytes]:
    content = _read_owned(
        path,
        expected_uid=os.getuid(),
        expected_mode=0o600,
        maximum=MAX_JSON_BYTES,
    )
    return strict_json(content), content


def strict_json(content: bytes) -> object:
    def reject_constant(value: str) -> object:
        raise InteractionError(f"UI/input JSON contains non-finite value: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise InteractionError("UI/input JSON contains duplicate keys")
            result[key] = value
        return result

    try:
        return json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise InteractionError("UI/input JSON is not UTF-8") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        raise InteractionError("UI/input JSON is malformed") from exc


def _directory(path: Path, *, mode: int) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise InteractionError("UI transaction directory is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise InteractionError("UI transaction directory identity is unsafe")


def _path_present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise InteractionError("UI transaction state is unavailable") from exc
    return True


def _digest(value: object, name: str, *, length: int = 64) -> str:
    pattern = DIGEST_RE if length == 64 else REVISION_RE
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise InteractionError(f"invalid {name}")
    return value


def _integer(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise InteractionError(f"invalid {name}")
    return value


def _boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise InteractionError(f"invalid {name}")
    return value


def _mode_string(value: object, expected: int, name: str) -> str:
    mode = f"{expected:04o}"
    if value != mode:
        raise InteractionError(f"invalid {name}")
    return mode


def repository_revision(repo_root: Path) -> str:
    outputs: list[bytes] = []
    for arguments in (
        ("rev-parse", "HEAD"),
        ("status", "--porcelain=v1", "--untracked-files=normal"),
    ):
        try:
            result = subprocess.run(
                ("git", "-C", str(repo_root), *arguments),
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=20,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InteractionError("Git identity probe is unavailable") from exc
        if result.returncode != 0 or len(result.stdout) > 65_536:
            raise InteractionError("Git identity probe failed or overflowed")
        outputs.append(result.stdout)
    revision = outputs[0].decode("ascii", errors="strict").strip()
    if REVISION_RE.fullmatch(revision) is None or outputs[1]:
        raise InteractionError("UI/input evaluation requires a clean exact revision")
    return revision


def _validate_record(
    raw: object,
    *,
    artifact_id: str,
    mode: int,
    privileged: bool,
) -> dict[str, object]:
    fields = {
        "artifact_id",
        "privileged",
        "target_mode",
        "source_sha256",
        "source_bytes",
        "source_mode",
        "before_state",
        "before_sha256",
        "before_bytes",
        "before_mode",
    }
    if type(raw) is not dict or set(raw) != fields:
        raise InteractionError("UI transaction artifact fields are invalid")
    if (
        raw["artifact_id"] != artifact_id
        or raw["privileged"] is not privileged
        or _mode_string(raw["target_mode"], mode, "target mode") != f"{mode:04o}"
        or _mode_string(raw["source_mode"], mode, "source mode") != f"{mode:04o}"
        or type(raw["source_bytes"]) is not int
        or not 0 < raw["source_bytes"] <= MAX_ARTIFACT_BYTES
    ):
        raise InteractionError("UI transaction artifact identity is invalid")
    _digest(raw["source_sha256"], "source hash")
    if raw["before_state"] == "MISSING":
        if any(raw[key] is not None for key in ("before_sha256", "before_bytes", "before_mode")):
            raise InteractionError("missing UI preimage unexpectedly contains data")
    elif raw["before_state"] == "PRESENT":
        _digest(raw["before_sha256"], "preimage hash")
        if (
            type(raw["before_bytes"]) is not int
            or not 0 < raw["before_bytes"] <= MAX_ARTIFACT_BYTES
            or type(raw["before_mode"]) is not str
            or re.fullmatch(r"[0-7]{4}", raw["before_mode"]) is None
        ):
            raise InteractionError("present UI preimage identity is invalid")
    else:
        raise InteractionError("UI preimage state is invalid")
    if artifact_id in REQUIRED_PREIMAGE_ARTIFACTS and raw["before_state"] != "PRESENT":
        raise InteractionError("required UI preimage is missing")
    return raw


def expected_binding(
    repo_root: Path,
    transaction: Path,
    home: Path,
    system_root: Path,
    *,
    root_uid: int = 0,
) -> dict[str, str]:
    for directory in (transaction, transaction / "payload", transaction / "backup"):
        _directory(directory, mode=0o700)
    manifest, manifest_content = read_private_json(transaction / "manifest.json")
    if type(manifest) is not dict or set(manifest) != {
        "schema",
        "repository_revision",
        "artifact_count",
        "artifacts",
        "activation_deferred",
        "services_restarted",
        "absolute_paths_recorded",
    }:
        raise InteractionError("UI transaction manifest fields are invalid")
    revision = _digest(manifest["repository_revision"], "manifest revision", length=40)
    if (
        manifest["schema"] != TRANSACTION_SCHEMA
        or type(manifest["artifact_count"]) is not int
        or manifest["artifact_count"] != len(ARTIFACTS)
        or type(manifest["artifacts"]) is not list
        or len(manifest["artifacts"]) != len(ARTIFACTS)
        or manifest["activation_deferred"] is not True
        or manifest["services_restarted"] is not False
        or manifest["absolute_paths_recorded"] is not False
    ):
        raise InteractionError("UI transaction manifest identity is invalid")
    current_revision = repository_revision(repo_root)
    if revision != current_revision:
        raise InteractionError("UI transaction is not bound to the current revision")

    manifest_hash = hashlib.sha256(manifest_content).hexdigest()
    applied, _applied_content = read_private_json(transaction / "applied.json")
    if applied != {
        "schema": MARKER_SCHEMA,
        "state": "APPLIED",
        "repository_revision": revision,
        "manifest_sha256": manifest_hash,
        "activation_deferred": True,
        "services_restarted": False,
    }:
        raise InteractionError("UI transaction applied marker is invalid")
    if _path_present(transaction / "rolled-back.json"):
        raise InteractionError("UI transaction was rolled back")

    bundle: list[dict[str, str]] = []
    records = manifest["artifacts"]
    for raw, artifact in zip(records, ARTIFACTS):
        artifact_id, source_relative, live_relative, mode, privileged = artifact
        record = _validate_record(
            raw,
            artifact_id=artifact_id,
            mode=mode,
            privileged=privileged,
        )
        source = _read_owned(
            repo_root / source_relative,
            expected_uid=os.getuid(),
            expected_mode=mode,
            maximum=MAX_ARTIFACT_BYTES,
        )
        payload = _read_owned(
            transaction / "payload" / artifact_id,
            expected_uid=os.getuid(),
            expected_mode=0o600,
            maximum=MAX_ARTIFACT_BYTES,
        )
        if privileged:
            live_path = system_root / live_relative
            live_uid = root_uid
        else:
            live_path = home / live_relative
            live_uid = os.getuid()
        live = _read_owned(
            live_path,
            expected_uid=live_uid,
            expected_mode=mode,
            maximum=MAX_ARTIFACT_BYTES,
        )
        source_hash = hashlib.sha256(source).hexdigest()
        if (
            source_hash != record["source_sha256"]
            or len(source) != record["source_bytes"]
            or payload != source
            or live != source
        ):
            raise InteractionError("UI source, payload or live target drifted")
        backup_path = transaction / "backup" / artifact_id
        if record["before_state"] == "PRESENT":
            backup = _read_owned(
                backup_path,
                expected_uid=os.getuid(),
                expected_mode=0o600,
                maximum=MAX_ARTIFACT_BYTES,
            )
            if (
                len(backup) != record["before_bytes"]
                or hashlib.sha256(backup).hexdigest() != record["before_sha256"]
            ):
                raise InteractionError("UI rollback preimage drifted")
        elif _path_present(backup_path):
            raise InteractionError("missing UI preimage unexpectedly has a backup")
        bundle.append(
            {
                "artifact_id": artifact_id,
                "sha256": source_hash,
                "mode": f"{mode:04o}",
            }
        )

    joy_source = _read_owned(
        repo_root / JOY_SOURCE,
        expected_uid=os.getuid(),
        expected_mode=0o644,
        maximum=MAX_ARTIFACT_BYTES,
    )
    joy_live = _read_owned(
        system_root / JOY_LIVE,
        expected_uid=root_uid,
        expected_mode=0o644,
        maximum=MAX_ARTIFACT_BYTES,
    )
    if joy_source != joy_live:
        raise InteractionError("joymouse source and live profile drifted")
    bundle_content = json.dumps(
        bundle,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return {
        "repo_revision": revision,
        "ui_manifest_sha256": manifest_hash,
        "ui_payload_bundle_sha256": hashlib.sha256(bundle_content).hexdigest(),
        "joymouse_sha256": hashlib.sha256(joy_source).hexdigest(),
    }


def parse_evidence(raw: object) -> tuple[dict[str, str], dict[str, dict[str, object]]]:
    if type(raw) is not dict or set(raw) != {"schema", "binding", "cases"}:
        raise InteractionError("UI/input evidence root fields are invalid")
    if (
        raw["schema"] != EVIDENCE_SCHEMA
        or type(raw["binding"]) is not dict
        or type(raw["cases"]) is not dict
        or set(raw["binding"]) != BINDING_FIELDS
        or not set(raw["cases"]).issubset(CASE_POLICY)
    ):
        raise InteractionError("UI/input evidence schema is invalid")
    binding = {
        "repo_revision": _digest(raw["binding"]["repo_revision"], "repo revision", length=40),
        "ui_manifest_sha256": _digest(
            raw["binding"]["ui_manifest_sha256"], "UI manifest hash"
        ),
        "ui_payload_bundle_sha256": _digest(
            raw["binding"]["ui_payload_bundle_sha256"], "UI payload bundle hash"
        ),
        "joymouse_sha256": _digest(raw["binding"]["joymouse_sha256"], "joymouse hash"),
    }
    cases: dict[str, dict[str, object]] = {}
    for case_id, raw_record in raw["cases"].items():
        _minimum, extras = CASE_POLICY[case_id]
        if type(raw_record) is not dict or set(raw_record) != COMMON_FIELDS | extras:
            raise InteractionError(f"UI/input case {case_id} fields are invalid")
        record: dict[str, object] = {}
        for field, value in raw_record.items():
            if field in BOOLEAN_FIELDS:
                record[field] = _boolean(value, f"{case_id}/{field}")
            elif field in STRING_FIELDS:
                if value not in {"joymouse", "gamepad", "other"}:
                    raise InteractionError(f"invalid {case_id}/{field}")
                record[field] = value
            else:
                record[field] = _integer(value, f"{case_id}/{field}")
        if (
            record["passes"] + record["failures"] + record["cancellations"]
            != record["attempts"]
        ):
            raise InteractionError(f"UI/input case {case_id} outcomes do not add up")
        cases[case_id] = record
    return binding, cases


def _case_gates(case_id: str, record: dict[str, object]) -> dict[str, bool]:
    minimum, _extras = CASE_POLICY[case_id]
    gates = {
        "attempt_floor": record["attempts"] >= minimum,
        "all_attempts_passed": (
            record["passes"] == record["attempts"]
            and record["failures"] == 0
            and record["cancellations"] == 0
        ),
        "support_services_not_restarted": (
            record["keyboard_restart_delta"] == 0
            and record["gpu_telemetry_restart_delta"] == 0
            and record["inputplumber_restart_delta"] == 0
        ),
        "observer_confirmed": record["observer_confirmed"] is True,
    }
    if case_id == "panel-lower-screen-geometry":
        gates.update(
            {
                "lower_screen_only": record["lower_screen_only"] is True,
                "all_edges_flush": record["all_edges_flush"] is True,
                "touch_regions_aligned": record["touch_regions_aligned"] is True,
            }
        )
    elif case_id == "panel-desktop-roundtrip":
        gates.update(
            {
                "desktop_reachable": record["desktop_reachable"] is True,
                "panel_reentry_available": record["panel_reentry_available"] is True,
            }
        )
    elif case_id == "panel-fullscreen-roundtrip":
        gates.update(
            {
                "active_window_only": record["active_window_only"] is True,
                "fullscreen_state_reversible": record["fullscreen_state_reversible"] is True,
            }
        )
    elif case_id == "panel-recovery-graceful":
        gates.update(
            {
                "one_plasma_restart_per_attempt": record["plasma_restart_delta"] == record["attempts"],
                "kwin_not_restarted": record["kwin_restart_delta"] == 0,
                "panel_returned": record["panel_returned"] is True,
                "bounded_recovery": record["bounded_recovery"] is True,
            }
        )
    elif case_id == "panel-recovery-signal-fallback":
        gates.update(
            {
                "three_sigterm_attempts": record["sigterm_attempts"] >= 3,
                "three_sigkill_attempts": record["sigkill_attempts"] >= 3,
                "signal_counts_match_attempts": (
                    record["sigterm_attempts"] + record["sigkill_attempts"]
                    == record["attempts"]
                ),
                "one_plasma_restart_per_attempt": record["plasma_restart_delta"] == record["attempts"],
                "kwin_not_restarted": record["kwin_restart_delta"] == 0,
                "panel_returned": record["panel_returned"] is True,
                "bounded_recovery": record["bounded_recovery"] is True,
            }
        )
    elif case_id == "keyboard-cross-app":
        gates.update(
            {
                "three_application_classes": record["application_class_count"] >= 3,
                "show_within_900_ms": 0 < record["maximum_show_ms"] <= 900,
                "hide_within_900_ms": 0 < record["maximum_hide_ms"] <= 900,
                "all_text_committed": record["text_commit_failures"] == 0,
                "visibility_not_stuck": record["visibility_stuck_count"] == 0,
                "lower_screen_only": record["lower_screen_only"] is True,
            }
        )
    elif case_id == "keyboard-isolated-xwayland-failure":
        gates.update(
            {
                "each_attempt_killed_isolated_server": (
                    record["isolated_server_kill_count"] == record["attempts"]
                ),
                "host_x11_untouched": record["host_x11_disruption_count"] == 0,
                "client_failed_as_expected": (
                    record["client_failure_exit_count"] == record["attempts"]
                ),
                "each_client_restart_was_bounded": (
                    record["bounded_restart_count"] == record["attempts"]
                ),
                "visibility_not_stuck": record["visibility_stuck_count"] == 0,
            }
        )
    elif case_id == "keyboard-touch-layout":
        gates.update(
            {
                "no_miskey": record["miskey_count"] == 0,
                "visual_hitboxes_match": record["visual_hitbox_mismatch_count"] == 0,
                "minimum_touch_target_48px": record["minimum_touch_target_px"] >= 48,
                "lower_screen_only": record["lower_screen_only"] is True,
            }
        )
    elif case_id == "joymouse-clicks":
        gates.update(
            {
                "fifty_rb_clicks": record["rb_attempts"] >= 50,
                "fifty_rt_clicks": record["rt_attempts"] >= 50,
                "click_counts_match_attempts": record["rb_attempts"] + record["rt_attempts"] == record["attempts"],
                "no_click_failure": record["rb_failures"] == 0 and record["rt_failures"] == 0,
                "no_wrong_button": record["wrong_button_count"] == 0,
                "no_stuck_button": record["stuck_button_count"] == 0,
            }
        )
    elif case_id == "joymouse-scroll":
        gates.update(
            {
                "fifty_lb_scrolls": record["lb_attempts"] >= 50,
                "fifty_lt_scrolls": record["lt_attempts"] >= 50,
                "scroll_counts_match_attempts": record["lb_attempts"] + record["lt_attempts"] == record["attempts"],
                "no_scroll_failure": record["lb_failures"] == 0 and record["lt_failures"] == 0,
                "no_wrong_direction": record["wrong_direction_count"] == 0,
                "no_stuck_scroll": record["stuck_scroll_count"] == 0,
            }
        )
    elif case_id == "input-mode-cycle":
        gates.update(
            {
                "no_duplicate_input": record["duplicate_input_count"] == 0,
                "no_mode_mismatch": record["mode_mismatch_count"] == 0,
                "panel_help_matched_all_states": record["panel_help_matched_all_states"] is True,
                "final_mode_is_joymouse": record["final_mode"] == "joymouse",
            }
        )
    return gates


def evaluate(
    binding: dict[str, str],
    cases: dict[str, dict[str, object]],
    expected: dict[str, str],
) -> dict[str, object]:
    results: list[dict[str, object]] = []
    for case_id, (minimum, _extras) in CASE_POLICY.items():
        record = cases.get(case_id)
        if record is None:
            results.append(
                {
                    "case_id": case_id,
                    "present": False,
                    "attempts": 0,
                    "minimum_attempts": minimum,
                    "gates": {},
                    "pass": False,
                }
            )
            continue
        gates = _case_gates(case_id, record)
        results.append(
            {
                "case_id": case_id,
                "present": True,
                "attempts": record["attempts"],
                "minimum_attempts": minimum,
                "gates": gates,
                "pass": all(gates.values()),
            }
        )
    binding_matches = binding == expected
    complete = binding_matches and all(item["pass"] for item in results)
    return {
        "schema": REPORT_SCHEMA,
        "mode": "offline-supervised-ui-input-ledger",
        "privacy": {
            "free_text_emitted": False,
            "timestamps_emitted": False,
            "absolute_paths_emitted": False,
            "device_identifiers_emitted": False,
            "application_names_emitted": False,
        },
        "binding": binding,
        "binding_matches_current_deployment": binding_matches,
        "policy": {
            case_id: minimum for case_id, (minimum, _extras) in CASE_POLICY.items()
        },
        "cases": results,
        "passed_case_count": sum(bool(item["pass"]) for item in results),
        "required_case_count": len(results),
        "complete": complete,
        "result": "PASS" if complete else "INCOMPLETE",
    }


def empty_evidence(binding: dict[str, str]) -> dict[str, object]:
    cases: dict[str, dict[str, object]] = {}
    for case_id, (_minimum, extras) in CASE_POLICY.items():
        record: dict[str, object] = {
            "attempts": 0,
            "passes": 0,
            "failures": 0,
            "cancellations": 0,
            "keyboard_restart_delta": 0,
            "gpu_telemetry_restart_delta": 0,
            "inputplumber_restart_delta": 0,
            "observer_confirmed": False,
        }
        for field in extras:
            if field in BOOLEAN_FIELDS:
                record[field] = False
            elif field == "final_mode":
                record[field] = "other"
            else:
                record[field] = 0
        cases[case_id] = record
    return {"schema": EVIDENCE_SCHEMA, "binding": binding, "cases": cases}


def write_json(value: dict[str, object], destination: str, *, stdout_allowed: bool) -> None:
    content = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if destination == "-":
        if not stdout_allowed:
            raise InteractionError("supervised ledger template needs a new private file")
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
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def transaction_path(name: str) -> Path:
    if NAME_RE.fullmatch(name) is None:
        raise InteractionError("UI transaction name is invalid")
    return Path.home() / ".local/state/pocketds-linux-kit/ui-transactions" / name


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--template-output")
    action.add_argument("--evidence", type=Path)
    parser.add_argument("--transaction-name", required=True)
    parser.add_argument("--output", default="-")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = Path(__file__).resolve().parents[1]
    try:
        expected = expected_binding(
            repo_root,
            transaction_path(arguments.transaction_name),
            Path.home(),
            Path("/"),
        )
        if arguments.template_output is not None:
            write_json(
                empty_evidence(expected),
                arguments.template_output,
                stdout_allowed=False,
            )
            return 0
        assert arguments.evidence is not None
        raw, _content = read_private_json(arguments.evidence)
        binding, cases = parse_evidence(raw)
        report = evaluate(binding, cases, expected)
        write_json(report, arguments.output, stdout_allowed=True)
    except (InteractionError, OSError, UnicodeError) as exc:
        print(f"UI/input evaluation failed: {exc}", file=sys.stderr)
        return 2
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

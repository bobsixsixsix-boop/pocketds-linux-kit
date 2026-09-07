#!/usr/bin/env python3
"""Plan or atomically stage/restore the three locked Pocket DS boot aliases."""

from __future__ import annotations

import argparse
from collections.abc import Callable
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat
import sys


HERE = Path(__file__).resolve().parent
PREFLIGHT_SOURCE = HERE / "preflight.py"
PREFLIGHT_SPEC = importlib.util.spec_from_file_location(
    "pocketds_recovery_transaction_preflight", PREFLIGHT_SOURCE
)
if PREFLIGHT_SPEC is None or PREFLIGHT_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("cannot load recovery preflight library")
preflight = importlib.util.module_from_spec(PREFLIGHT_SPEC)
sys.modules[PREFLIGHT_SPEC.name] = preflight
PREFLIGHT_SPEC.loader.exec_module(preflight)
payload = preflight.payload

DISPATCH_SOURCE = HERE / "dispatch.py"
DISPATCH_SPEC = importlib.util.spec_from_file_location(
    "pocketds_recovery_transaction_dispatch", DISPATCH_SOURCE
)
if DISPATCH_SPEC is None or DISPATCH_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("cannot load recovery dispatch library")
dispatch = importlib.util.module_from_spec(DISPATCH_SPEC)
sys.modules[DISPATCH_SPEC.name] = dispatch
DISPATCH_SPEC.loader.exec_module(dispatch)


TransactionError = preflight.PreflightError
REPORT_SCHEMA = "pocketds.kernel-boot-alias-transaction.v1"
CONFIRMATIONS = {
    "candidate": "PDS002-STAGE-IFPC-CANDIDATE-V1",
    "baseline": "PDS002-RESTORE-LIVE-BASELINE-V1",
}
MAX_SMALL_FILE = 64 * 1024


def _read_pseudo(path: Path, name: str, *, nul: bool = False) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise TransactionError(f"runtime identity is unavailable or linked: {name}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise TransactionError(f"runtime identity metadata differs: {name}")
        content = bytearray()
        while len(content) <= MAX_SMALL_FILE:
            block = os.read(descriptor, min(4096, MAX_SMALL_FILE + 1 - len(content)))
            if not block:
                break
            content.extend(block)
        if not content or len(content) > MAX_SMALL_FILE:
            raise TransactionError(f"runtime identity size differs: {name}")
    finally:
        os.close(descriptor)
    if nul:
        content = content.rstrip(b"\x00")
    try:
        text = bytes(content).decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise TransactionError(f"runtime identity is not UTF-8: {name}") from exc
    if not text or "\x00" in text:
        raise TransactionError(f"runtime identity text differs: {name}")
    return text


def _boot_mount(root: Path) -> dict[str, object]:
    text = _read_pseudo(root / "proc/self/mountinfo", "mountinfo")
    matches: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if len(fields) <= separator + 2 or len(fields) < 6 or fields[4] != "/boot":
            continue
        matches.append((fields[5], fields[separator + 1], fields[separator + 2]))
    if len(matches) != 1:
        raise TransactionError("boot mount identity is missing or ambiguous")
    options, filesystem, source = matches[0]
    if (
        "rw" not in options.split(",")
        or filesystem != "vfat"
        or source != "/dev/sda12"
    ):
        raise TransactionError("boot mount identity differs")
    return {"filesystem": "vfat", "read_write": True, "source_is_sda12": True}


def _artifact(path: Path, expected: dict[str, object]) -> bytes:
    content, metadata = payload._read_regular(path, maximum=payload.MAX_ARTIFACT_BYTES)
    measured = {
        "filename": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": metadata.st_size,
    }
    if measured != expected:
        raise TransactionError(f"locked transaction artifact differs: {path.name}")
    return content


def _path_id(relative: str) -> str:
    return hashlib.sha256(("pds002-boot-alias-v1\x00" + relative).encode()).hexdigest()


def _alias_state(
    path: Path,
    relative: str,
    identities: dict[str, dict[str, object]],
) -> tuple[str, dict[str, object]]:
    content, metadata = payload._read_regular(path, maximum=payload.MAX_ARTIFACT_BYTES)
    digest = hashlib.sha256(content).hexdigest()
    matched = [
        name
        for name, record in identities.items()
        if digest == record["sha256"] and metadata.st_size == record["size"]
    ]
    if len(matched) != 1:
        raise TransactionError("boot alias is neither locked baseline nor candidate")
    return matched[0], {
        "path_id": _path_id(relative),
        "identity": matched[0],
        "sha256": digest,
        "size": metadata.st_size,
    }


def inspect(
    lock: dict[str, object],
    *,
    root: Path,
    baseline_image: Path,
    candidate_image: Path,
    target: str,
) -> tuple[dict[str, object], dict[str, bytes], dict[str, str]]:
    if target not in {"baseline", "candidate"}:
        raise TransactionError("invalid boot transaction target")
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise TransactionError("system root is unavailable") from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise TransactionError("system root is not a real directory")
    model = _read_pseudo(
        root / "sys/firmware/devicetree/base/model", "machine model", nul=True
    )
    release = _read_pseudo(root / "proc/sys/kernel/osrelease", "kernel release")
    if model != lock["machine_model"] or release != lock["kernel_release"]:
        raise TransactionError("machine or kernel release differs")
    mount = _boot_mount(root)
    records = {
        "baseline": lock["artifacts"]["baseline_rollback_boot"],
        "candidate": lock["artifacts"]["candidate_runtime_boot"],
    }
    contents = {
        "baseline": _artifact(baseline_image, records["baseline"]),
        "candidate": _artifact(candidate_image, records["candidate"]),
    }
    if contents["baseline"] == contents["candidate"]:
        raise TransactionError("baseline and candidate transaction artifacts are identical")
    states: dict[str, str] = {}
    aliases: list[dict[str, object]] = []
    for relative in lock["disk_boot_aliases"]:
        identity, measured = _alias_state(root / str(relative), str(relative), records)
        states[str(relative)] = identity
        aliases.append(measured)
    if target == "candidate" and set(states.values()) != {"baseline"}:
        raise TransactionError("candidate stage requires an all-baseline preimage")
    return {
        "schema": REPORT_SCHEMA,
        "action": "plan_only",
        "target_identity": target,
        "device_id": lock["device_id"],
        "kernel_release": lock["kernel_release"],
        "boot_mount": mount,
        "artifacts": records,
        "aliases_before": aliases,
        "required_confirmation": CONFIRMATIONS[target],
        "gates": {
            "machine_and_release_match": True,
            "boot_mount_is_expected_rw_vfat": True,
            "baseline_and_candidate_artifacts_match_lock": True,
            "all_aliases_have_known_identity": True,
            "candidate_stage_precondition_satisfied": True,
            "write_executed": False,
            "automatic_rollback_needed": False,
            "candidate_install_authorized": False,
        },
    }, contents, states


def _safe_parent(path: Path) -> tuple[int, str]:
    if path.name in {"", ".", ".."}:
        raise TransactionError("invalid boot alias target")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path.parent, flags)
    except OSError as exc:
        raise TransactionError("boot alias parent is unavailable or linked") from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise TransactionError("boot alias parent is not a directory")
    return descriptor, path.name


def _atomic_replace(path: Path, content: bytes) -> None:
    parent_fd, name = _safe_parent(path)
    temporary = f".pds002-{os.getpid()}-{secrets.token_hex(8)}.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
        created = True
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise TransactionError("boot alias write stopped")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.rename(
            temporary,
            name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        created = False
        os.fsync(parent_fd)
    except OSError as exc:
        raise TransactionError("boot alias atomic replacement failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except OSError:
                pass
        os.close(parent_fd)


def _reserve_report(
    path: Path, *, target: str, lock_sha256: str
) -> tuple[int, int, str]:
    absolute = Path(os.path.abspath(path))
    if absolute == Path("/boot") or Path("/boot") in absolute.parents:
        raise TransactionError("transaction report must not be stored on the boot mount")
    parent_fd, name = dispatch._safe_parent(absolute)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
    except OSError as exc:
        os.close(parent_fd)
        raise TransactionError("cannot reserve transaction report") from exc
    intent = json.dumps(
        {
            "schema": REPORT_SCHEMA,
            "action": "transaction_reserved_before_boot_alias_write",
            "target_identity": target,
            "lock_sha256": lock_sha256,
            "final_report_committed": False,
            "candidate_install_authorized": False,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    try:
        view = memoryview(intent)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise TransactionError("transaction intent write stopped")
            view = view[written:]
        os.fsync(descriptor)
    except Exception:
        os.close(descriptor)
        try:
            os.unlink(name, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        raise
    return parent_fd, descriptor, name


def _finish_report(
    reservation: tuple[int, int, str], report: dict[str, object]
) -> None:
    parent_fd, descriptor, name = reservation
    content = json.dumps(
        report, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    temporary = f".{name}.complete-{os.getpid()}-{secrets.token_hex(8)}"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    final_fd: int | None = None
    created = False
    success = False
    try:
        final_fd = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
        created = True
        view = memoryview(content)
        while view:
            written = os.write(final_fd, view)
            if written <= 0:
                raise TransactionError("transaction report write stopped")
            view = view[written:]
        os.fsync(final_fd)
        metadata = os.fstat(final_fd)
        if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size != len(content):
            raise TransactionError("transaction report metadata differs")
        os.close(final_fd)
        final_fd = None
        os.rename(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        created = False
        os.fsync(parent_fd)
        success = True
    finally:
        if final_fd is not None:
            os.close(final_fd)
        os.close(descriptor)
        if created:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except OSError:
                pass
        os.close(parent_fd)
    if not success:  # pragma: no cover - the raised write error exits first
        raise TransactionError("transaction final report was not committed")


def _cancel_report(reservation: tuple[int, int, str]) -> None:
    parent_fd, descriptor, name = reservation
    try:
        os.close(descriptor)
    finally:
        try:
            os.unlink(name, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)


def _abandon_report(reservation: tuple[int, int, str]) -> None:
    """Close a reservation while preserving its on-disk recovery intent."""

    parent_fd, descriptor, _name = reservation
    try:
        os.close(descriptor)
    finally:
        os.close(parent_fd)


def perform(
    lock: dict[str, object],
    *,
    root: Path,
    baseline_image: Path,
    candidate_image: Path,
    target: str,
    require_root: bool = True,
    writer: Callable[[Path, bytes], None] = _atomic_replace,
) -> dict[str, object]:
    if require_root and os.geteuid() != 0:
        raise TransactionError("boot alias execution requires root")
    plan, contents, states = inspect(
        lock,
        root=root,
        baseline_image=baseline_image,
        candidate_image=candidate_image,
        target=target,
    )
    changed = [relative for relative, identity in states.items() if identity != target]
    try:
        for relative in changed:
            current, _before_write = _alias_state(
                root / relative,
                relative,
                {
                    "baseline": lock["artifacts"]["baseline_rollback_boot"],
                    "candidate": lock["artifacts"]["candidate_runtime_boot"],
                },
            )
            if current != states[relative]:
                raise TransactionError("boot alias changed after transaction preflight")
            writer(root / relative, contents[target])
            identity, _measured = _alias_state(
                root / relative,
                relative,
                {
                    "baseline": lock["artifacts"]["baseline_rollback_boot"],
                    "candidate": lock["artifacts"]["candidate_runtime_boot"],
                },
            )
            if identity != target:
                raise TransactionError("boot alias post-write identity differs")
    except Exception as exc:
        rollback_failed = False
        # Reinspect every alias, not only calls that returned successfully: a
        # writer can fail after rename while syncing the VFAT directory.
        rollback_needed: list[str] = []
        for relative, original in states.items():
            try:
                current, _measured = _alias_state(
                    root / relative,
                    relative,
                    {
                        "baseline": lock["artifacts"]["baseline_rollback_boot"],
                        "candidate": lock["artifacts"]["candidate_runtime_boot"],
                    },
                )
                if current != original:
                    rollback_needed.append(relative)
            except Exception:
                rollback_failed = True
        for relative in reversed(rollback_needed):
            try:
                writer(root / relative, contents[states[relative]])
            except Exception:
                rollback_failed = True
        if rollback_failed:
            raise TransactionError(
                "boot alias transaction failed and automatic rollback is incomplete"
            ) from exc
        for relative, identity in states.items():
            restored, _measured = _alias_state(
                root / relative,
                relative,
                {
                    "baseline": lock["artifacts"]["baseline_rollback_boot"],
                    "candidate": lock["artifacts"]["candidate_runtime_boot"],
                },
            )
            if restored != identity:
                raise TransactionError(
                    "boot alias transaction failed and exact preimage was not restored"
                ) from exc
        raise TransactionError(
            "boot alias transaction failed; exact preimage restored"
        ) from exc
    aliases_after: list[dict[str, object]] = []
    for relative in lock["disk_boot_aliases"]:
        identity, measured = _alias_state(
            root / str(relative),
            str(relative),
            {
                "baseline": lock["artifacts"]["baseline_rollback_boot"],
                "candidate": lock["artifacts"]["candidate_runtime_boot"],
            },
        )
        if identity != target:
            raise TransactionError("boot alias transaction did not converge")
        aliases_after.append(measured)
    return {
        **{key: value for key, value in plan.items() if key not in {"action", "gates"}},
        "action": (
            "no_change_needed"
            if not changed
            else "candidate_staged" if target == "candidate" else "baseline_restored"
        ),
        "aliases_after": aliases_after,
        "changed_alias_count": len(changed),
        "automatic_rollback_attempted": False,
        "gates": {
            **plan["gates"],
            "write_executed": bool(changed),
            "all_aliases_match_target": True,
            "candidate_install_authorized": False,
        },
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-image", type=Path, required=True)
    parser.add_argument("--candidate-image", type=Path, required=True)
    parser.add_argument("--target", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        raw, lock_sha256 = payload.load_lock(HERE / "lock.json")
        lock = preflight.validate_lock(raw)
        if args.execute:
            if args.confirm != CONFIRMATIONS[args.target] or args.output is None:
                raise TransactionError("execution needs the exact confirmation and a new output")
            if os.geteuid() != 0:
                raise TransactionError("boot alias execution requires root")
            inspect(
                lock,
                root=Path("/"),
                baseline_image=args.baseline_image,
                candidate_image=args.candidate_image,
                target=args.target,
            )
            reservation = _reserve_report(
                args.output, target=args.target, lock_sha256=lock_sha256
            )
            try:
                report = perform(
                    lock,
                    root=Path("/"),
                    baseline_image=args.baseline_image,
                    candidate_image=args.candidate_image,
                    target=args.target,
                )
            except BaseException:
                _abandon_report(reservation)
                raise
            report["lock_sha256"] = lock_sha256
            _finish_report(reservation, report)
        else:
            if args.confirm is not None or args.output is not None:
                raise TransactionError("plan mode accepts no confirmation or output")
            report, _contents, _states = inspect(
                lock,
                root=Path("/"),
                baseline_image=args.baseline_image,
                candidate_image=args.candidate_image,
                target=args.target,
            )
            report["lock_sha256"] = lock_sha256
        print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    except (OSError, TransactionError, dispatch.DispatchError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

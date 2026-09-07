#!/usr/bin/env python3
"""Pinned, file-only InputPlumber haptics sidecar transaction."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
COMPONENT = REPO / "components" / "inputplumber"
LOCK_FILE = COMPONENT / "inputplumber-haptics-source-lock.json"
SYSTEM_BINARY = "/usr/bin/inputplumber"
STATE_DIR = "/var/lib/pocketds-linux-kit/inputplumber-haptics-transactions"
TRANSACTION_LOCK = ".transaction.lock"
APPLY_TOKEN = "APPLY-INPUTPLUMBER-HAPTICS-D3932CB4"
ROLLBACK_TOKEN = "ROLLBACK-INPUTPLUMBER-HAPTICS-D3932CB4"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
RECOVERABLE_PHASES = {"prepared", "apply-failed", "applied"}

TARGETS = (
    ("candidate_binary", None, "/usr/local/libexec/pocketds-inputplumber-haptics", 0o755),
    (
        "device_profile",
        "01-ayaneo-controller-haptics.yaml",
        "/etc/inputplumber/devices.d/01-ayaneo-controller.yaml",
        0o644,
    ),
    (
        "service_dropin",
        "30-pocketds-haptics-candidate.conf",
        "/etc/systemd/system/inputplumber.service.d/30-pocketds-haptics-candidate.conf",
        0o644,
    ),
    (
        "polkit_policy",
        "org.pocketds.InputPlumber.Haptics.policy",
        "/usr/share/polkit-1/actions/org.pocketds.InputPlumber.Haptics.policy",
        0o644,
    ),
)


class Refused(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rooted(root: Path, path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise Refused(f"unsafe path: {path}")
    return root / path.lstrip("/")


def refuse_symlink_parents(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise Refused(f"path escapes root: {path}") from exc
    current = root
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise Refused(f"refusing symlink parent: {current}")


def read_regular(path: Path, label: str) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise Refused(f"cannot read regular non-symlink {label}: {path}: {exc}") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise Refused(f"not a regular file: {path}")
        chunks = []
        while chunk := os.read(fd, 1024 * 1024):
            chunks.append(chunk)
        data = b"".join(chunks)
        if len(data) != metadata.st_size:
            raise Refused(f"short read: {path}")
        return data, metadata
    finally:
        os.close(fd)


def snapshot(path: Path, label: str) -> dict:
    if path.is_symlink():
        raise Refused(f"refusing symlink {label}: {path}")
    if not path.exists():
        return {"present": False}
    data, metadata = read_regular(path, label)
    return {
        "present": True,
        "data": data,
        "sha256": digest(data),
        "size": len(data),
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def public(item: dict) -> dict:
    return {key: value for key, value in item.items() if key != "data"}


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise Refused(f"refusing symlink destination: {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.pds-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


@contextmanager
def transaction_lock(root: Path):
    state = rooted(root, STATE_DIR)
    lock_path = state / TRANSACTION_LOCK
    refuse_symlink_parents(root, lock_path)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Refused(f"transaction lock is not regular: {lock_path}")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Refused("another haptics transaction holds the global lock") from exc
        yield
    finally:
        os.close(fd)


def unfinished_transactions(root: Path) -> list[str]:
    state = rooted(root, STATE_DIR)
    if not state.exists():
        return []
    if state.is_symlink() or not state.is_dir():
        raise Refused(f"unsafe transaction state directory: {state}")
    unfinished = []
    for entry in sorted(state.iterdir()):
        if entry.name == TRANSACTION_LOCK:
            continue
        if entry.is_symlink() or not entry.is_dir():
            raise Refused(f"unexpected transaction-state entry: {entry}")
        manifest_path = entry / "manifest.json"
        if not manifest_path.exists():
            continue  # No target write occurs before the prepared manifest.
        manifest = json.loads(read_regular(manifest_path, "transaction manifest")[0])
        if manifest.get("phase") in RECOVERABLE_PHASES:
            unfinished.append(entry.name)
    return unfinished


def load_lock_and_payloads(binary: Path) -> tuple[dict, dict[str, bytes]]:
    lock = json.loads(read_regular(LOCK_FILE, "source lock")[0])
    if (
        lock.get("schema") != "pocketds.inputplumber-haptics-source.v1"
        or lock.get("upstream_commit")
        != "d3932cb4e0de3eb47688e381b5eb5334ebac6254"
        or lock.get("rollback_binary") != SYSTEM_BINARY
    ):
        raise Refused("unexpected source lock")
    patch_name = "inputplumber-d3932cb4-pocketds-haptics.patch"
    if lock.get("downstream_patch") != patch_name:
        raise Refused("unexpected downstream patch")
    patch = read_regular(COMPONENT / patch_name, "downstream patch")[0]
    if (
        len(patch) != lock.get("downstream_patch_size")
        or digest(patch) != lock.get("downstream_patch_sha256")
    ):
        raise Refused("downstream patch does not match lock")

    candidate = read_regular(binary, "candidate binary")[0]
    if (
        len(candidate) != lock.get("binary_size")
        or digest(candidate) != lock.get("binary_sha256")
    ):
        raise Refused("candidate binary SHA-256/size mismatch")
    payloads = {"candidate_binary": candidate}
    locked = lock.get("install_payloads", {})
    for name, source, destination, mode in TARGETS[1:]:
        entry = locked.get(name, {})
        if (
            entry.get("source") != source
            or entry.get("destination") != destination
            or entry.get("mode") != f"{mode:04o}"
        ):
            raise Refused(f"unsafe lock entry: {name}")
        data = read_regular(COMPONENT / source, name)[0]
        if len(data) != entry.get("size") or digest(data) != entry.get("sha256"):
            raise Refused(f"payload does not match lock: {name}")
        payloads[name] = data
    return lock, payloads


def checked_root(value: str, mutate: bool) -> Path:
    root = Path(value)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise Refused("--root must be an existing absolute non-symlink directory")
    root = root.resolve()
    if mutate and root == Path("/") and os.geteuid() != 0:
        raise Refused("live apply/rollback requires root")
    return root


def preflight(root: Path, binary: Path) -> tuple[dict, dict[str, bytes], dict, dict]:
    lock, payloads = load_lock_and_payloads(binary)
    rollback = snapshot(rooted(root, SYSTEM_BINARY), "distro rollback binary")
    if not rollback["present"]:
        raise Refused("distro rollback binary is missing")
    before = {}
    for name, _source, destination, _mode in TARGETS:
        path = rooted(root, destination)
        refuse_symlink_parents(root, path)
        before[name] = snapshot(path, name)
    return lock, payloads, rollback, before


def plan(args: argparse.Namespace) -> None:
    root = checked_root(args.root, False)
    lock, payloads, rollback, before = preflight(root, Path(args.binary))
    print(
        json.dumps(
            {
                "operation": "plan-only",
                "mutated": False,
                "upstream_commit": lock["upstream_commit"],
                "binary_sha256": digest(payloads["candidate_binary"]),
                "binary_size": len(payloads["candidate_binary"]),
                "rollback_binary": {"path": SYSTEM_BINARY, **public(rollback)},
                "targets": [
                    {
                        "name": name,
                        "destination": destination,
                        "mode": f"{mode:04o}",
                        "before": public(before[name]),
                    }
                    for name, _source, destination, mode in TARGETS
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


def restore(root: Path, before: dict) -> None:
    for name, _source, destination, _mode in reversed(TARGETS):
        path = rooted(root, destination)
        item = before[name]
        if item["present"]:
            atomic_write(path, item["data"], item["mode"])
        elif path.exists():
            path.unlink()


def apply(args: argparse.Namespace) -> None:
    if args.confirm != APPLY_TOKEN:
        raise Refused(f"apply requires --confirm {APPLY_TOKEN}")
    root = checked_root(args.root, True)
    with transaction_lock(root):
        unfinished = unfinished_transactions(root)
        if unfinished:
            raise Refused(
                "recover or rollback unfinished transaction(s): " + ", ".join(unfinished)
            )
        apply_locked(args, root)


def apply_locked(args: argparse.Namespace, root: Path) -> None:
    lock, payloads, rollback, before = preflight(root, Path(args.binary))
    identifier = args.transaction_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not ID_RE.fullmatch(identifier):
        raise Refused("unsafe transaction id")
    tx = rooted(root, STATE_DIR) / identifier
    refuse_symlink_parents(root, tx)
    if tx.exists() or tx.is_symlink():
        raise Refused("transaction already exists")
    (tx / "backups").mkdir(parents=True, mode=0o700)
    entries = []
    for index, (name, _source, destination, mode) in enumerate(TARGETS):
        item = public(before[name])
        if before[name]["present"]:
            backup = f"backups/{index:02d}-{name}"
            atomic_write(tx / backup, before[name]["data"], 0o600)
            item["backup"] = backup
        entries.append(
            {
                "name": name,
                "destination": destination,
                "mode": f"{mode:04o}",
                "before": item,
                "installed": {
                    "sha256": digest(payloads[name]),
                    "size": len(payloads[name]),
                },
            }
        )
    manifest = {
        "schema": "pocketds.inputplumber-haptics-transaction.v1",
        "phase": "prepared",
        "transaction_id": identifier,
        "root": str(root),
        "upstream_commit": lock["upstream_commit"],
        "rollback_binary": {"path": SYSTEM_BINARY, **public(rollback)},
        "targets": entries,
    }
    atomic_write(tx / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode(), 0o600)
    print(f"prepared transaction {identifier}", flush=True)
    try:
        for name, _source, destination, mode in TARGETS:
            atomic_write(rooted(root, destination), payloads[name], mode)
        for name, _source, destination, mode in TARGETS:
            current = snapshot(rooted(root, destination), name)
            if current.get("data") != payloads[name] or current.get("mode") != mode:
                raise Refused(f"post-install verification failed: {name}")
        current_rollback = snapshot(rooted(root, SYSTEM_BINARY), "distro rollback binary")
        if current_rollback.get("sha256") != rollback["sha256"]:
            raise Refused("distro rollback binary changed")
    except Exception:
        restore(root, before)
        manifest["phase"] = "apply-failed"
        atomic_write(tx / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode(), 0o600)
        raise
    manifest["phase"] = "applied"
    atomic_write(tx / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode(), 0o600)
    print(f"installed transaction {identifier}; runtime unchanged")


def rollback(args: argparse.Namespace) -> None:
    if args.confirm != ROLLBACK_TOKEN:
        raise Refused(f"rollback requires --confirm {ROLLBACK_TOKEN}")
    root = checked_root(args.root, True)
    if not ID_RE.fullmatch(args.transaction_id):
        raise Refused("unsafe transaction id")
    with transaction_lock(root):
        rollback_locked(args, root)


def rollback_locked(args: argparse.Namespace, root: Path) -> None:
    tx = rooted(root, STATE_DIR) / args.transaction_id
    refuse_symlink_parents(root, tx)
    manifest = json.loads(read_regular(tx / "manifest.json", "transaction manifest")[0])
    if (
        manifest.get("schema") != "pocketds.inputplumber-haptics-transaction.v1"
        or manifest.get("phase") not in RECOVERABLE_PHASES
        or manifest.get("transaction_id") != args.transaction_id
        or manifest.get("root") != str(root)
    ):
        raise Refused("invalid or non-recoverable transaction")
    if len(manifest.get("targets", [])) != len(TARGETS):
        raise Refused("transaction target count mismatch")
    original_phase = manifest["phase"]
    before = {}
    for index, ((name, _source, destination, mode), entry) in enumerate(
        zip(TARGETS, manifest["targets"])
    ):
        if (
            entry.get("name") != name
            or entry.get("destination") != destination
            or entry.get("mode") != f"{mode:04o}"
        ):
            raise Refused("transaction target mismatch")
        item = entry.get("before", {})
        if item.get("present"):
            expected_backup = f"backups/{index:02d}-{name}"
            if item.get("backup") != expected_backup:
                raise Refused("unexpected backup path")
            data = read_regular(tx / expected_backup, f"backup {name}")[0]
            if digest(data) != item.get("sha256") or len(data) != item.get("size"):
                raise Refused(f"bad backup: {name}")
            before[name] = {**item, "data": data}
        else:
            before[name] = {"present": False}
        current = snapshot(rooted(root, destination), name)
        expected = entry.get("installed", {})
        matches_installed = (
            current.get("sha256") == expected.get("sha256")
            and current.get("size") == expected.get("size")
            and current.get("mode") == mode
        )
        matches_before = (
            not current.get("present")
            if not before[name]["present"]
            else current.get("sha256") == before[name].get("sha256")
            and current.get("size") == before[name].get("size")
            and current.get("mode") == before[name].get("mode")
        )
        if not (matches_installed or matches_before):
            raise Refused(f"{name} is neither installed payload nor preimage")
    restore(root, before)
    manifest["phase"] = "rolled-back"
    atomic_write(tx / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode(), 0o600)
    print(
        f"rolled back {original_phase} transaction {args.transaction_id}; runtime unchanged"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    read_only = commands.add_parser("plan", help="validate and print only")
    read_only.add_argument("--binary", required=True)
    read_only.add_argument("--root", default="/", help=argparse.SUPPRESS)
    read_only.set_defaults(handler=plan)
    install = commands.add_parser("apply", help="perform the exact file transaction")
    install.add_argument("--binary", required=True)
    install.add_argument("--confirm", required=True)
    install.add_argument("--transaction-id")
    install.add_argument("--root", default="/", help=argparse.SUPPRESS)
    install.set_defaults(handler=apply)
    undo = commands.add_parser("rollback", help="restore an exact transaction")
    undo.add_argument("--transaction-id", required=True)
    undo.add_argument("--confirm", required=True)
    undo.add_argument("--root", default="/", help=argparse.SUPPRESS)
    undo.set_defaults(handler=rollback)
    args = parser.parse_args()
    try:
        args.handler(args)
    except (Refused, OSError, json.JSONDecodeError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

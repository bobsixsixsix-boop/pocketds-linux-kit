#!/usr/bin/env python3
"""Verify and optionally install the locked offline SenseVoice runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import sys
import tarfile
import tempfile
from typing import Any, BinaryIO, Iterable, Sequence


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = ROOT / "components/keyboard/sensevoice-artifacts-lock.json"
CONFIRMATION = "POCKETDS-INSTALL-SENSEVOICE-V1.13.2"
MAX_LOCK_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_RUNTIME_FILE_BYTES = 1024 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

sys.path.insert(0, str(ROOT / "components/keyboard"))
from voice_artifacts import (  # noqa: E402
    MAX_SENSEVOICE_OUTPUT_BYTES,
    VoiceArtifactError,
    parse_sensevoice_transcript,
    run_bounded_command,
    sensevoice_command,
)


class PrepareError(RuntimeError):
    """A locked artifact, destination, or confirmation is unsafe."""


def read_regular(path: Path, maximum: int, *, owner: int | None = None) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PrepareError("required input is missing or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= maximum
            or mode & 0o022
            or (owner is not None and metadata.st_uid != owner)
        ):
            raise PrepareError("required input has unsafe metadata")
        content = bytearray()
        remaining = metadata.st_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise PrepareError("required input changed while being read")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise PrepareError("required input grew while being read")
        return bytes(content)
    finally:
        os.close(descriptor)


def strict_json(path: Path) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PrepareError("artifact lock contains a duplicate key")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise PrepareError(f"artifact lock contains non-finite value {value}")

    try:
        value = json.loads(
            read_regular(path, MAX_LOCK_BYTES).decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PrepareError("artifact lock is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PrepareError("artifact lock is not an object")
    return value


def safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PrepareError(f"{label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PrepareError(f"{label} is unsafe")
    return path


def sha_record(
    value: Any,
    label: str,
    *,
    target: bool,
    extra_keys: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    expected = {"archive_path", "size", "sha256"}
    if target:
        expected |= {"target", "mode"}
    expected |= set(extra_keys)
    if not isinstance(value, dict) or set(value) != expected:
        raise PrepareError(f"{label} shape is invalid")
    archive_path = safe_relative(value["archive_path"], f"{label} archive path")
    size = value["size"]
    digest = value["sha256"]
    if (
        type(size) is not int
        or not 0 < size <= MAX_RUNTIME_FILE_BYTES
        or not isinstance(digest, str)
        or not SHA256_RE.fullmatch(digest)
    ):
        raise PrepareError(f"{label} identity is invalid")
    result = {**value, "archive_path": archive_path}
    if target:
        target_path = safe_relative(value["target"], f"{label} target")
        if target_path.parts[0] != ".local" or value["mode"] not in (0o644, 0o755):
            raise PrepareError(f"{label} install boundary is invalid")
        result["target"] = target_path
    return result


def archive_record(value: Any, label: str) -> dict[str, Any]:
    keys = {"filename", "url", "size", "sha256", "member_count", "root"}
    if not isinstance(value, dict) or set(value) != keys:
        raise PrepareError(f"{label} archive shape is invalid")
    filename = safe_relative(value["filename"], f"{label} filename")
    root = safe_relative(value["root"], f"{label} root")
    if len(filename.parts) != 1 or len(root.parts) != 1:
        raise PrepareError(f"{label} archive names are invalid")
    if (
        not isinstance(value["url"], str)
        or not value["url"].startswith("https://github.com/")
        or not value["url"].endswith("/" + filename.as_posix())
        or type(value["size"]) is not int
        or not 0 < value["size"] <= MAX_ARCHIVE_BYTES
        or not isinstance(value["sha256"], str)
        or not SHA256_RE.fullmatch(value["sha256"])
        or type(value["member_count"]) is not int
        or not 1 <= value["member_count"] <= 512
    ):
        raise PrepareError(f"{label} archive identity is invalid")
    return {**value, "filename": filename, "root": root}


def validate_lock(value: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "schema",
        "platform",
        "download_ready",
        "install_ready",
        "release_ready",
        "release_blockers",
        "engine",
        "model",
        "licenses",
    }
    if set(value) != keys or type(value.get("schema")) is not int or value["schema"] != 1:
        raise PrepareError("artifact lock top-level shape is invalid")
    expected_platform = {"os": "linux", "arch": "aarch64"}
    if value["platform"] != expected_platform:
        raise PrepareError("artifact lock platform is invalid")
    for key, expected in (
        ("download_ready", True),
        ("install_ready", True),
        ("release_ready", False),
    ):
        if value[key] is not expected:
            raise PrepareError(f"artifact lock {key} boundary is invalid")
    blockers = value["release_blockers"]
    if not isinstance(blockers, list) or not blockers or any(
        not isinstance(item, str) or not item for item in blockers
    ):
        raise PrepareError("artifact lock release blockers are invalid")

    engine = value["engine"]
    engine_keys = {
        "name", "version", "tag", "source_commit", "source_url",
        "release_url", "archive", "runtime_files", "version_evidence",
    }
    if (
        not isinstance(engine, dict)
        or set(engine) != engine_keys
        or engine.get("name") != "sherpa-onnx"
        or engine.get("version") != "1.13.2"
        or engine.get("tag") != "v1.13.2"
        or not isinstance(engine.get("source_commit"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", engine["source_commit"])
        or not isinstance(engine.get("source_url"), str)
        or not engine["source_url"].startswith("https://github.com/")
        or not isinstance(engine.get("release_url"), str)
        or not engine["release_url"].startswith("https://github.com/")
        or not isinstance(engine.get("version_evidence"), dict)
    ):
        raise PrepareError("artifact lock engine identity is invalid")
    evidence = engine["version_evidence"]
    if (
        set(evidence) != {"binary", "version", "git_sha1", "build_date"}
        or evidence.get("binary") != "sherpa-onnx-version"
        or evidence.get("version") != engine["version"]
        or not isinstance(evidence.get("git_sha1"), str)
        or not re.fullmatch(r"[0-9a-f]{8}", evidence["git_sha1"])
        or not engine["source_commit"].startswith(evidence["git_sha1"])
        or not isinstance(evidence.get("build_date"), str)
        or not evidence["build_date"]
    ):
        raise PrepareError("artifact lock engine version evidence is invalid")
    engine_archive = archive_record(engine["archive"], "engine")
    engine_files = [
        sha_record(item, f"engine runtime file {index}", target=True)
        for index, item in enumerate(engine["runtime_files"])
    ] if isinstance(engine["runtime_files"], list) else []
    if len(engine_files) != 2:
        raise PrepareError("artifact lock engine runtime set is invalid")

    model = value["model"]
    model_keys = {
        "name", "source_model", "source_author", "documentation",
        "archive", "runtime_files", "smoke_fixture",
    }
    if (
        not isinstance(model, dict)
        or set(model) != model_keys
        or not all(isinstance(model.get(key), str) and model[key] for key in (
            "name", "source_model", "source_author", "documentation"
        ))
        or not model["documentation"].startswith("https://")
    ):
        raise PrepareError("artifact lock model identity is invalid")
    model_archive = archive_record(model["archive"], "model")
    model_files = [
        sha_record(item, f"model runtime file {index}", target=True)
        for index, item in enumerate(model["runtime_files"])
    ] if isinstance(model["runtime_files"], list) else []
    if len(model_files) != 2:
        raise PrepareError("artifact lock model runtime set is invalid")
    smoke = sha_record(
        model["smoke_fixture"],
        "model smoke fixture",
        target=False,
        extra_keys=frozenset({"language", "event", "text"}),
    )
    if any(not isinstance(smoke[key], str) or not smoke[key] for key in (
        "language", "event", "text"
    )) or smoke["language"] != "<|zh|>" or smoke["event"] != "<|Speech|>":
        raise PrepareError("artifact lock smoke result is invalid")

    license_items = value["licenses"]
    if not isinstance(license_items, list) or len(license_items) != 3:
        raise PrepareError("artifact lock license set is invalid")
    licenses: list[dict[str, Any]] = []
    license_keys = {
        "component", "license", "source_url", "source_commit",
        "repository_path", "target", "mode", "size", "sha256",
        "current_upstream_recheck_at_release",
    }
    for index, item in enumerate(license_items):
        if not isinstance(item, dict) or set(item) != license_keys:
            raise PrepareError(f"license record {index} shape is invalid")
        repository_path = safe_relative(item["repository_path"], "license repository path")
        target_path = safe_relative(item["target"], "license target")
        if (
            repository_path.parts[:3] != ("components", "keyboard", "licenses")
            or target_path.parts[:4] != (".local", "share", "licenses", "pocketds-sensevoice")
            or item["mode"] != 0o644
            or type(item["size"]) is not int
            or not 0 < item["size"] <= MAX_LOCK_BYTES
            or not isinstance(item["sha256"], str)
            or not SHA256_RE.fullmatch(item["sha256"])
            or type(item["current_upstream_recheck_at_release"]) is not bool
            or not isinstance(item["component"], str)
            or not item["component"]
            or not isinstance(item["license"], str)
            or not item["license"]
        ):
            raise PrepareError(f"license record {index} is invalid")
        source_url = item["source_url"]
        source_commit = item["source_commit"]
        if not (
            (source_url is None and source_commit is None)
            or (
                isinstance(source_url, str)
                and source_url.startswith("https://github.com/")
                and isinstance(source_commit, str)
                and re.fullmatch(r"[0-9a-f]{40}", source_commit)
            )
        ):
            raise PrepareError(f"license record {index} source is invalid")
        licenses.append({**item, "repository_path": repository_path, "target": target_path})

    all_runtime = engine_files + model_files
    archive_paths = [item["archive_path"].as_posix() for item in all_runtime]
    targets = [item["target"].as_posix() for item in all_runtime] + [
        item["target"].as_posix() for item in licenses
    ]
    if len(set(archive_paths)) != len(archive_paths) or len(set(targets)) != len(targets):
        raise PrepareError("artifact lock contains duplicate paths")
    for record, archive in (
        *((item, engine_archive) for item in engine_files),
        *((item, model_archive) for item in model_files),
        (smoke, model_archive),
    ):
        if record["archive_path"].parts[0] != archive["root"].as_posix():
            raise PrepareError("locked member escapes its archive root")
    return {
        "engine_archive": engine_archive,
        "model_archive": model_archive,
        "engine_files": engine_files,
        "model_files": model_files,
        "smoke": smoke,
        "licenses": licenses,
        "release_blockers": blockers,
    }


def digest_stream(stream: BinaryIO, maximum: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while True:
        block = stream.read(1024 * 1024)
        if not block:
            break
        size += len(block)
        if size > maximum:
            raise PrepareError("archive member exceeds its locked bound")
        digest.update(block)
    return size, digest.hexdigest()


def verify_archive(
    path: Path,
    archive: dict[str, Any],
    wanted: Iterable[dict[str, Any]],
    *,
    owner: int,
) -> None:
    content = read_regular(path, MAX_ARCHIVE_BYTES, owner=owner)
    if len(content) != archive["size"] or hashlib.sha256(content).hexdigest() != archive["sha256"]:
        raise PrepareError("archive size or digest does not match the lock")
    try:
        with tarfile.open(fileobj=__import__("io").BytesIO(content), mode="r:bz2") as bundle:
            members = bundle.getmembers()
            if len(members) != archive["member_count"]:
                raise PrepareError("archive member count does not match the lock")
            names: set[str] = set()
            by_name: dict[str, tarfile.TarInfo] = {}
            for member in members:
                relative = safe_relative(member.name, "archive member")
                name = relative.as_posix()
                if relative.parts[0] != archive["root"].as_posix() or name in names:
                    raise PrepareError("archive member root or uniqueness is invalid")
                if not (member.isfile() or member.isdir()):
                    raise PrepareError("archive contains a linked or special member")
                names.add(name)
                by_name[name] = member
            for record in wanted:
                name = record["archive_path"].as_posix()
                member = by_name.get(name)
                if member is None or not member.isfile() or member.size != record["size"]:
                    raise PrepareError("locked archive member is missing or has wrong size")
                stream = bundle.extractfile(member)
                if stream is None:
                    raise PrepareError("locked archive member cannot be read")
                with stream:
                    size, digest = digest_stream(stream, record["size"])
                if size != record["size"] or digest != record["sha256"]:
                    raise PrepareError("locked archive member digest does not match")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise PrepareError("archive cannot be safely decoded") from exc


def repository_input(root: Path, relative: PurePosixPath) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise PrepareError("repository root is unsafe")
    current = root
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink() or not current.is_dir():
            raise PrepareError("repository license parent is unsafe")
    return current / relative.parts[-1]


def ensure_destination_root(root: Path, *, owner: int) -> None:
    try:
        metadata = os.lstat(root)
    except OSError as exc:
        raise PrepareError("destination root is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != owner
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise PrepareError("destination root is unsafe")


def target_path(root: Path, relative: PurePosixPath) -> Path:
    return root.joinpath(*relative.parts)


def ensure_parent(root: Path, relative: PurePosixPath, *, owner: int) -> Path:
    current = root
    for part in relative.parts[:-1]:
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            metadata = os.lstat(current)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != owner
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise PrepareError("destination parent is unsafe")
    return current


def target_state(path: Path, record: dict[str, Any], *, owner: int) -> str:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return "install"
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != owner
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != record["mode"]
        or metadata.st_size != record["size"]
    ):
        raise PrepareError("existing target is unsafe or drifted")
    with path.open("rb") as stream:
        _, digest = digest_stream(stream, record["size"])
    if digest != record["sha256"]:
        raise PrepareError("existing target digest is drifted")
    return "unchanged"


def stage_bytes(parent: Path, data: bytes, mode: int) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=".pds011-stage-", dir=parent)
    path = Path(name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return path
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise


def extract_member_bytes(
    path: Path, archive: dict[str, Any], record: dict[str, Any]
) -> bytes:
    try:
        with tarfile.open(path, mode="r:bz2") as bundle:
            member = bundle.getmember(record["archive_path"].as_posix())
            stream = bundle.extractfile(member)
            if stream is None:
                raise PrepareError("locked archive member cannot be staged")
            with stream:
                content = stream.read(record["size"] + 1)
    except (tarfile.TarError, KeyError, OSError, EOFError) as exc:
        raise PrepareError("locked archive member cannot be staged") from exc
    if len(content) != record["size"] or hashlib.sha256(content).hexdigest() != record["sha256"]:
        raise PrepareError("staged archive member changed after preflight")
    return content


def prepare(
    *,
    lock_path: Path,
    engine_archive_path: Path,
    model_archive_path: Path,
    repository_root: Path,
    destination_root: Path,
    execute: bool,
    confirmation: str | None,
    host_os: str,
    host_arch: str,
) -> dict[str, Any]:
    owner = os.getuid()
    locked = validate_lock(strict_json(lock_path))
    verify_archive(
        engine_archive_path,
        locked["engine_archive"],
        locked["engine_files"],
        owner=owner,
    )
    verify_archive(
        model_archive_path,
        locked["model_archive"],
        [*locked["model_files"], locked["smoke"]],
        owner=owner,
    )
    sources: list[tuple[dict[str, Any], Path | None, dict[str, Any] | None]] = []
    for record in locked["engine_files"]:
        sources.append((record, engine_archive_path, locked["engine_archive"]))
    for record in locked["model_files"]:
        sources.append((record, model_archive_path, locked["model_archive"]))
    for record in locked["licenses"]:
        path = repository_input(repository_root, record["repository_path"])
        content = read_regular(path, MAX_LOCK_BYTES)
        if len(content) != record["size"] or hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise PrepareError("repository license identity does not match the lock")
        sources.append((record, path, None))

    ensure_destination_root(destination_root, owner=owner)
    states = {
        record["target"].as_posix(): target_state(
            target_path(destination_root, record["target"]), record, owner=owner
        )
        for record, _, _ in sources
    }
    installs = sum(state == "install" for state in states.values())
    if not execute:
        return {
            "schema": 1,
            "read_only": True,
            "network": False,
            "archives_verified": 2,
            "files_verified": len(sources),
            "files_to_install": installs,
            "files_unchanged": len(states) - installs,
            "runtime_smoke": "not-run-in-plan",
            "release_ready": False,
            "release_blockers": locked["release_blockers"],
        }
    if confirmation != CONFIRMATION:
        raise PrepareError(f"execution requires --confirm {CONFIRMATION}")
    if owner == 0:
        raise PrepareError("run the installer as the desktop user, not root")
    if host_os != "linux" or host_arch != "aarch64":
        raise PrepareError("installation requires Linux aarch64")

    staged: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        for record, source, archive in sources:
            target = target_path(destination_root, record["target"])
            if states[record["target"].as_posix()] == "unchanged":
                continue
            parent = ensure_parent(destination_root, record["target"], owner=owner)
            if archive is None:
                assert source is not None
                content = read_regular(source, MAX_LOCK_BYTES)
            else:
                assert source is not None
                content = extract_member_bytes(source, archive, record)
            staged.append((stage_bytes(parent, content, record["mode"]), target))
        for stage, target in staged:
            if target.exists() or target.is_symlink():
                raise PrepareError("target appeared after preflight")
            os.replace(stage, target)
            installed.append(target)

        smoke_content = extract_member_bytes(
            model_archive_path, locked["model_archive"], locked["smoke"]
        )
        with tempfile.TemporaryDirectory(prefix="pds011-smoke-", dir=destination_root) as name:
            wav = Path(name) / "zh.wav"
            wav.write_bytes(smoke_content)
            wav.chmod(0o600)
            by_target = {
                record["target"].name: target_path(destination_root, record["target"])
                for record, _, _ in sources
            }
            try:
                return_code, output = run_bounded_command(
                    sensevoice_command(
                        by_target["sherpa-onnx-offline"],
                        by_target["model.int8.onnx"],
                        by_target["tokens.txt"],
                        wav,
                    ),
                    timeout=30,
                    maximum=MAX_SENSEVOICE_OUTPUT_BYTES,
                )
                transcript = parse_sensevoice_transcript(output)
            except (OSError, VoiceArtifactError) as exc:
                raise PrepareError("installed runtime smoke was invalid") from exc
        if return_code != 0:
            raise PrepareError("installed runtime smoke process failed")
        if transcript != locked["smoke"]["text"]:
            raise PrepareError("installed runtime smoke result drifted")
        for record, _, _ in sources:
            target_state(
                target_path(destination_root, record["target"]), record, owner=owner
            )
        return {
            "schema": 1,
            "read_only": False,
            "network": False,
            "archives_verified": 2,
            "files_verified": len(sources),
            "files_installed": len(installed),
            "files_unchanged": len(states) - len(installed),
            "runtime_smoke": "pass",
            "smoke_transcript_sha256": hashlib.sha256(transcript.encode()).hexdigest(),
            "release_ready": False,
            "release_blockers": locked["release_blockers"],
        }
    except Exception:
        for stage, _ in staged:
            stage.unlink(missing_ok=True)
        for target in reversed(installed):
            target.unlink(missing_ok=True)
        raise


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--engine-archive", type=Path, required=True)
    parser.add_argument("--model-archive", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--destination-root", type=Path, default=Path.home())
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        report = prepare(
            lock_path=args.lock,
            engine_archive_path=args.engine_archive,
            model_archive_path=args.model_archive,
            repository_root=args.repository_root,
            destination_root=args.destination_root,
            execute=args.execute,
            confirmation=args.confirm,
            host_os=platform.system().lower(),
            host_arch=platform.machine().lower(),
        )
    except (OSError, PrepareError) as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

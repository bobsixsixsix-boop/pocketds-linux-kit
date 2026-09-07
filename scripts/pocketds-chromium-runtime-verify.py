#!/usr/bin/env python3
"""Read-only, offline verifier for the pinned Pocket DS Chromium runtime.

Exit codes: 0 release-ready, 1 integrity mismatch, 2 invalid lock/arguments,
3 observed files match but package provenance is incomplete.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = ROOT / "components/chromium/runtime-lock.json"
DEFAULT_WRAPPER = Path("/usr/local/bin/pocketds-chromium-v4l2")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT_RE = re.compile(r"^[0-9A-F]{40}$")
ALLOWED_URL_HOSTS = {"archlinuxarm.org", "mirror.archlinuxarm.org"}
VERIFIED_STATUS = "signed-archive-verified"
MAX_RECEIPT_BYTES = 1_048_576
KEYRING_REPOSITORY = "https://github.com/archlinuxarm/archlinuxarm-keyring.git"


class LockError(ValueError):
    """The lock itself is unsafe or internally inconsistent."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_sha256(value: Any, label: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise LockError(f"{label} must be a lowercase SHA-256 digest")


def require_official_url(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise LockError(f"{label} must be an HTTPS URL")
    parsed = urlparse(value)
    hostname = parsed.hostname or ""
    allowed = hostname in ALLOWED_URL_HOSTS or hostname.endswith(
        ".mirror.archlinuxarm.org"
    )
    if parsed.scheme != "https" or not allowed:
        raise LockError(f"{label} must use an official Arch Linux ARM HTTPS host")


def safe_relative(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise LockError(f"{label} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise LockError(f"{label} is not a safe relative path")
    return path


def read_bounded_regular(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise LockError(f"{label} is missing or unsafe")
    metadata = path.lstat()
    if metadata.st_nlink != 1 or not 0 < metadata.st_size <= MAX_RECEIPT_BYTES:
        raise LockError(f"{label} has an unsafe size or link count")
    data = path.read_bytes()
    if len(data) != metadata.st_size:
        raise LockError(f"{label} changed while being read")
    return data


def _exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LockError(f"{label} must be an object")
    if set(value) != expected:
        raise LockError(f"{label} has missing or unknown fields")
    return value


def validate_provenance_receipt(
    lock: dict[str, Any], receipt_root: Path = ROOT
) -> dict[str, int]:
    reference = _exact_keys(
        lock.get("provenance_receipt"), {"path", "sha256"}, "provenance_receipt"
    )
    receipt_path = receipt_root / safe_relative(
        reference["path"], "provenance_receipt.path"
    )
    require_sha256(reference["sha256"], "provenance_receipt.sha256")
    receipt_bytes = read_bounded_regular(receipt_path, "provenance receipt")
    if hashlib.sha256(receipt_bytes).hexdigest() != reference["sha256"]:
        raise LockError("provenance receipt SHA-256 mismatch")
    try:
        receipt = json.loads(receipt_bytes)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LockError("provenance receipt is not valid JSON") from exc
    receipt = _exact_keys(
        receipt,
        {
            "schema",
            "verified_at",
            "signing_policy",
            "mirror_policy",
            "key",
            "verifier",
            "packages",
        },
        "provenance receipt",
    )
    if receipt["schema"] != 1:
        raise LockError("unsupported provenance receipt schema")
    if not isinstance(receipt["verified_at"], str) or not re.fullmatch(
        r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ", receipt["verified_at"]
    ):
        raise LockError("provenance receipt verified_at is invalid")
    if receipt["signing_policy"] != lock["package"]["signing_policy"]:
        raise LockError("provenance signing policy mismatch")
    require_official_url(receipt["mirror_policy"], "provenance mirror_policy")

    key = _exact_keys(
        receipt["key"],
        {
            "fingerprint",
            "certificate_path",
            "certificate_sha256",
            "source_repository",
            "source_commit",
            "source_path",
        },
        "provenance key",
    )
    if key["fingerprint"] != lock["package"]["signing_key_fingerprint"]:
        raise LockError("provenance signer fingerprint mismatch")
    require_sha256(key["certificate_sha256"], "provenance key certificate_sha256")
    if key["source_repository"] != KEYRING_REPOSITORY:
        raise LockError("provenance key repository is not pinned")
    if not isinstance(key["source_commit"], str) or not re.fullmatch(
        r"[0-9a-f]{40}", key["source_commit"]
    ):
        raise LockError("provenance key source commit is invalid")
    if key["source_path"] != "packager/builder.asc":
        raise LockError("provenance key source path is unexpected")
    certificate_path = receipt_root / safe_relative(
        key["certificate_path"], "provenance key certificate_path"
    )
    certificate = read_bounded_regular(certificate_path, "provenance key certificate")
    if hashlib.sha256(certificate).hexdigest() != key["certificate_sha256"]:
        raise LockError("provenance key certificate SHA-256 mismatch")

    verifier = _exact_keys(
        receipt["verifier"],
        {"name", "version", "openpgp_version", "backend", "command_contract"},
        "provenance verifier",
    )
    if verifier["name"] != "sequoia-sqv":
        raise LockError("provenance verifier name is unsupported")
    for field in ("version", "openpgp_version", "backend", "command_contract"):
        if not isinstance(verifier[field], str) or not verifier[field]:
            raise LockError(f"provenance verifier {field} is invalid")

    sources = {"chromium": lock["package"], **lock["compat_sources"]}
    packages = receipt["packages"]
    if not isinstance(packages, dict) or set(packages) != set(sources):
        raise LockError("provenance receipt package set mismatch")
    compat_counts: dict[str, int] = {name: 0 for name in lock["compat_sources"]}
    for item in lock["compat_files"]:
        compat_counts[item["source_package"]] += 1
    expected_entry_counts = {"chromium": len(lock["observed"]), **compat_counts}
    metadata_names = {".PKGINFO", ".BUILDINFO", ".MTREE"}
    observed_names = {
        ".PKGINFO": "pkginfo",
        ".BUILDINFO": "buildinfo",
        ".MTREE": "mtree",
    }
    for name, source in sources.items():
        package = _exact_keys(
            packages[name],
            {
                "version",
                "arch",
                "archive_url",
                "archive_size",
                "archive_sha256",
                "signature_url",
                "signature_size",
                "signature_sha256",
                "signature_base64",
                "signer_fingerprint",
                "signature_valid",
                "locked_entries_verified",
                "metadata",
            },
            f"provenance package {name}",
        )
        if package["version"] != source["version"] or package["arch"] != "aarch64":
            raise LockError(f"provenance package {name} identity mismatch")
        if package["archive_url"] != source["download"]:
            raise LockError(f"provenance package {name} archive URL mismatch")
        if package["signature_url"] != source["signature"]:
            raise LockError(f"provenance package {name} signature URL mismatch")
        require_official_url(package["archive_url"], f"provenance package {name} archive")
        require_official_url(package["signature_url"], f"provenance package {name} signature")
        if package["archive_sha256"] != source["archive_sha256"]:
            raise LockError(f"provenance package {name} archive SHA-256 mismatch")
        require_sha256(package["signature_sha256"], f"provenance package {name} signature")
        if not isinstance(package["archive_size"], int) or package["archive_size"] <= 0:
            raise LockError(f"provenance package {name} archive size is invalid")
        if not isinstance(package["signature_size"], int) or not 64 <= package["signature_size"] <= 65536:
            raise LockError(f"provenance package {name} signature size is invalid")
        try:
            signature = base64.b64decode(package["signature_base64"], validate=True)
        except (binascii.Error, TypeError, ValueError) as exc:
            raise LockError(f"provenance package {name} signature is not base64") from exc
        if len(signature) != package["signature_size"] or hashlib.sha256(
            signature
        ).hexdigest() != package["signature_sha256"]:
            raise LockError(f"provenance package {name} signature receipt mismatch")
        if package["signer_fingerprint"] != key["fingerprint"]:
            raise LockError(f"provenance package {name} signer mismatch")
        if package["signature_valid"] is not True:
            raise LockError(f"provenance package {name} signature is not verified")
        if package["locked_entries_verified"] != expected_entry_counts[name]:
            raise LockError(f"provenance package {name} locked-entry count mismatch")
        metadata = package["metadata"]
        if not isinstance(metadata, dict) or set(metadata) != metadata_names:
            raise LockError(f"provenance package {name} metadata set mismatch")
        for metadata_name, artifact in metadata.items():
            artifact = _exact_keys(
                artifact, {"size", "sha256"}, f"provenance package {name} {metadata_name}"
            )
            if not isinstance(artifact["size"], int) or artifact["size"] <= 0:
                raise LockError(f"provenance package {name} metadata size is invalid")
            require_sha256(
                artifact["sha256"], f"provenance package {name} {metadata_name}"
            )
            if name == "chromium":
                observed = lock["observed"][observed_names[metadata_name]]
                if artifact != {"size": observed["size"], "sha256": observed["sha256"]}:
                    raise LockError(f"provenance Chromium {metadata_name} mismatch")
    return {"packages": len(packages), "signatures": len(packages)}


def parse_pkginfo(path: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if line.startswith("#") or " = " not in line:
            continue
        key, value = line.split(" = ", 1)
        result.setdefault(key, []).append(value)
    return result


def parse_mtree(path: Path) -> dict[str, dict[str, str]]:
    entries: dict[str, dict[str, str]] = {}
    with gzip.open(path, "rt", encoding="utf-8", errors="strict") as stream:
        for raw in stream:
            line = raw.strip()
            if not line.startswith("./"):
                continue
            fields = line.split()
            values: dict[str, str] = {}
            for field in fields[1:]:
                if "=" in field:
                    key, value = field.split("=", 1)
                    values[key] = value
            entries[fields[0][2:]] = values
    return entries


def enumerate_compat(root: Path) -> dict[str, tuple[str, str | None, int | None]]:
    result: dict[str, tuple[str, str | None, int | None]] = {}
    if not root.is_dir() or root.is_symlink():
        raise OSError(f"compat root is missing or unsafe: {root}")
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            result[relative] = ("symlink", os.readlink(path), None)
        elif stat.S_ISREG(mode):
            result[relative] = ("file", sha256_file(path), path.stat().st_size)
        elif stat.S_ISDIR(mode):
            continue
        else:
            result[relative] = ("special", None, None)
    return result


def validate_source(name: str, source: Any) -> tuple[bool, str | None]:
    if not isinstance(source, dict):
        raise LockError(f"source {name} must be an object")
    for key in ("version", "package_page", "source_files", "download", "signature"):
        if key == "version":
            if not isinstance(source.get(key), str) or not source[key]:
                raise LockError(f"source {name}.version is missing")
        else:
            require_official_url(source.get(key), f"source {name}.{key}")
    require_sha256(source.get("archive_sha256"), f"source {name}.archive_sha256", nullable=True)
    if not isinstance(source.get("signature_verified"), bool):
        raise LockError(f"source {name}.signature_verified must be boolean")
    status = source.get("status")
    if status not in {VERIFIED_STATUS, "observed-unverified"}:
        raise LockError(f"source {name}.status is unsupported")
    complete = (
        status == VERIFIED_STATUS
        and source["archive_sha256"] is not None
        and source["signature_verified"] is True
    )
    if status == VERIFIED_STATUS and not complete:
        raise LockError(f"source {name} claims verification without hash/signature")
    gap = source.get("gap")
    if complete:
        if gap not in {None, ""}:
            raise LockError(f"verified source {name} must not retain a provenance gap")
        return True, None
    if not isinstance(gap, str) or not gap:
        raise LockError(f"incomplete source {name} must explain its provenance gap")
    return False, gap


def validate_lock(lock: dict[str, Any]) -> tuple[bool, list[str]]:
    if lock.get("schema") != 1:
        raise LockError("unsupported runtime lock schema")
    package = lock.get("package")
    if not isinstance(package, dict):
        raise LockError("package section is missing")
    for key in (
        "name",
        "version",
        "arch",
        "root_basename",
        "main_binary",
        "packager",
    ):
        if not isinstance(package.get(key), str) or not package[key]:
            raise LockError(f"package.{key} is missing")
    if package["name"] != "chromium" or package["arch"] != "aarch64":
        raise LockError("runtime lock is not for Chromium aarch64")
    if not isinstance(package.get("builddate"), int) or package["builddate"] <= 0:
        raise LockError("package.builddate is invalid")
    safe_relative(package["main_binary"], "package.main_binary")
    for key in ("package_page", "source_files", "download", "signature", "signing_policy"):
        require_official_url(package.get(key), f"package.{key}")
    fingerprint = package.get("signing_key_fingerprint")
    if not isinstance(fingerprint, str) or not FINGERPRINT_RE.fullmatch(fingerprint):
        raise LockError("package.signing_key_fingerprint must be 40 uppercase hex digits")
    observed = lock.get("observed")
    if not isinstance(observed, dict):
        raise LockError("observed section is missing")
    for key in ("pkginfo", "buildinfo", "mtree", "main_binary"):
        artifact = observed.get(key)
        if not isinstance(artifact, dict):
            raise LockError(f"observed.{key} is missing")
        safe_relative(artifact.get("path"), f"observed.{key}.path")
        require_sha256(artifact.get("sha256"), f"observed.{key}.sha256")
        if not isinstance(artifact.get("size"), int) or artifact["size"] < 0:
            raise LockError(f"observed.{key}.size is invalid")
    require_sha256(package.get("pkgbuild_sha256"), "package.pkgbuild_sha256")
    if observed["main_binary"]["path"] != package["main_binary"]:
        raise LockError("package.main_binary contradicts observed.main_binary.path")

    wrapper = lock.get("wrapper")
    if not isinstance(wrapper, dict):
        raise LockError("wrapper section is missing")
    safe_relative(wrapper.get("path"), "wrapper.path")
    require_sha256(wrapper.get("sha256"), "wrapper.sha256")

    sources = lock.get("compat_sources")
    if not isinstance(sources, dict) or not sources:
        raise LockError("compat_sources must be a non-empty object")
    gaps: list[str] = []
    package_complete, package_gap = validate_source("chromium", package)
    if package_gap:
        gaps.append(f"chromium: {package_gap}")
    complete = package_complete
    for name, source in sorted(sources.items()):
        source_complete, gap = validate_source(name, source)
        complete = complete and source_complete
        if gap:
            gaps.append(f"{name}: {gap}")

    files = lock.get("compat_files")
    if not isinstance(files, list) or not files:
        raise LockError("compat_files must be a non-empty list")
    seen: set[str] = set()
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise LockError(f"compat_files[{index}] must be an object")
        path = safe_relative(item.get("path"), f"compat_files[{index}].path").as_posix()
        if path in seen:
            raise LockError(f"duplicate compat path: {path}")
        seen.add(path)
        if item.get("source_package") not in sources:
            raise LockError(f"compat path has unknown source package: {path}")
        if not isinstance(item.get("runtime_required"), bool):
            raise LockError(f"compat path lacks runtime_required boolean: {path}")
        if item.get("type") == "file":
            require_sha256(item.get("sha256"), f"compat file {path}.sha256")
            if not isinstance(item.get("size"), int) or item["size"] < 0:
                raise LockError(f"compat file {path}.size is invalid")
        elif item.get("type") == "symlink":
            target = safe_relative(item.get("target"), f"compat symlink {path}.target")
            if target.as_posix().startswith("../"):
                raise LockError(f"compat symlink escapes its directory: {path}")
        else:
            raise LockError(f"compat path has unsupported type: {path}")
    declared_ready = lock.get("release_ready")
    if not isinstance(declared_ready, bool):
        raise LockError("release_ready must be boolean")
    if declared_ready != complete:
        raise LockError("release_ready contradicts package provenance evidence")
    return complete, gaps


def verify_runtime(
    lock: dict[str, Any],
    root: Path,
    wrapper: Path,
    receipt_root: Path = ROOT,
) -> dict[str, Any]:
    provenance_complete, gaps = validate_lock(lock)
    receipt = (
        validate_provenance_receipt(lock, receipt_root)
        if provenance_complete
        else None
    )
    errors: list[str] = []
    package = lock["package"]
    if root.is_symlink() or not root.is_dir():
        errors.append("runtime root is missing or is a symbolic link")
    if root.name != package["root_basename"]:
        errors.append("runtime root basename does not match the pinned version")

    observed = lock["observed"]
    for label, artifact in observed.items():
        path = root / safe_relative(artifact["path"], f"observed.{label}.path")
        if path.is_symlink() or not path.is_file():
            errors.append(f"{label}: missing or unsafe regular file")
            continue
        if path.stat().st_size != artifact["size"]:
            errors.append(f"{label}: size mismatch")
        if sha256_file(path) != artifact["sha256"]:
            errors.append(f"{label}: SHA-256 mismatch")

    pkginfo_path = root / observed["pkginfo"]["path"]
    mtree_path = root / observed["mtree"]["path"]
    if pkginfo_path.is_file() and not pkginfo_path.is_symlink():
        pkginfo = parse_pkginfo(pkginfo_path)
        expected = {
            "pkgname": package["name"],
            "pkgver": package["version"],
            "arch": package["arch"],
            "packager": package["packager"],
            "builddate": str(package["builddate"]),
        }
        for key, value in expected.items():
            if pkginfo.get(key) != [value]:
                errors.append(f".PKGINFO {key} mismatch")
    buildinfo_path = root / observed["buildinfo"]["path"]
    if buildinfo_path.is_file() and not buildinfo_path.is_symlink():
        buildinfo = parse_pkginfo(buildinfo_path)
        expected = {
            "pkgname": package["name"],
            "pkgver": package["version"],
            "pkgarch": package["arch"],
            "packager": package["packager"],
            "builddate": str(package["builddate"]),
            "pkgbuild_sha256sum": package["pkgbuild_sha256"],
        }
        for key, value in expected.items():
            if buildinfo.get(key) != [value]:
                errors.append(f".BUILDINFO {key} mismatch")
    if mtree_path.is_file() and not mtree_path.is_symlink():
        try:
            mtree = parse_mtree(mtree_path)
        except (OSError, UnicodeError) as exc:
            errors.append(f".MTREE cannot be parsed: {exc}")
        else:
            for label in ("pkginfo", "buildinfo", "main_binary"):
                artifact = observed[label]
                entry = mtree.get(artifact["path"])
                if entry is None:
                    errors.append(f".MTREE lacks {artifact['path']}")
                    continue
                if entry.get("sha256digest") != artifact["sha256"]:
                    errors.append(f".MTREE digest mismatch for {artifact['path']}")
                if entry.get("size") != str(artifact["size"]):
                    errors.append(f".MTREE size mismatch for {artifact['path']}")

    wrapper_lock = lock["wrapper"]
    if wrapper.is_symlink() or not wrapper.is_file():
        errors.append("wrapper is missing or is a symbolic link")
    else:
        if sha256_file(wrapper) != wrapper_lock["sha256"]:
            errors.append("wrapper SHA-256 mismatch")
        text = wrapper.read_text(encoding="utf-8", errors="strict")
        required_root = f"readonly chromium_root={root}"
        if required_root not in text:
            errors.append("wrapper does not pin the verified runtime root")
        if 'compat/usr/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}' not in text:
            errors.append("wrapper does not prepend the pinned compat directory")
        if "export CHROME_DESKTOP=chromium-browser.desktop" not in text:
            errors.append("wrapper does not identify the installed desktop launcher")

    try:
        actual = enumerate_compat(root / "compat")
    except OSError as exc:
        errors.append(str(exc))
        actual = {}
    expected_files = {item["path"]: item for item in lock["compat_files"]}
    for path in sorted(set(expected_files) - set(actual)):
        errors.append(f"compat file missing: {path}")
    for path in sorted(set(actual) - set(expected_files)):
        errors.append(f"unlocked compat entry: {path}")
    for path in sorted(set(actual) & set(expected_files)):
        kind, value, size = actual[path]
        expected = expected_files[path]
        if kind != expected["type"]:
            errors.append(f"compat type mismatch: {path}")
        elif kind == "file":
            if value != expected["sha256"] or size != expected["size"]:
                errors.append(f"compat file mismatch: {path}")
        elif value != expected["target"]:
            errors.append(f"compat symlink mismatch: {path}")

    return {
        "schema": 1,
        "runtime_id": package["root_basename"],
        "integrity": "fail" if errors else "pass",
        "provenance": "complete" if provenance_complete else "incomplete",
        "provenance_receipt": "pass" if receipt is not None else "not-applicable",
        "release_ready": not errors and provenance_complete,
        "compat_entries": len(actual),
        "errors": errors,
        "provenance_gaps": gaps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--wrapper", type=Path, default=DEFAULT_WRAPPER)
    parser.add_argument("--receipt-root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        lock = json.loads(args.lock.read_text(encoding="utf-8"))
        root = args.root or Path("/opt") / lock["package"]["root_basename"]
        report = verify_runtime(lock, root, args.wrapper, args.receipt_root)
    except (OSError, KeyError, json.JSONDecodeError, LockError) as exc:
        print(json.dumps({"integrity": "invalid-lock", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    if report["integrity"] != "pass":
        return 1
    return 0 if report["release_ready"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify a signed hardened userspace RPM in an isolated temporary keyring."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BUILD_LOCK = ROOT / "packaging/pocketds-userspace/build-lock.json"
DEFAULT_SOURCE_LOCK = ROOT / "packaging/pocketds-userspace/source-lock.json"
AUDITOR_PATH = ROOT / "scripts/pds020-userspace-rpm-audit.py"
REPRO_PATH = ROOT / "scripts/pds020-userspace-rpm-repro.py"
MAX_RECEIPT_BYTES = 128 * 1024
MAX_KEY_BYTES = 1024 * 1024
MAX_RPM_BYTES = 16 * 1024 * 1024
MAX_TOOL_OUTPUT = 128 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
SIGNATURE_RE = re.compile(
    r"^    Header OpenPGP V(?P<version>[46]) "
    r"(?P<algorithm>[A-Za-z0-9+._/-]+) signature, key fingerprint: "
    r"(?P<fingerprint>(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})): OK$"
)
ALLOWED_ALGORITHMS = {
    "RSA/SHA256",
    "RSA/SHA512",
    "ECDSA/SHA256",
    "ECDSA/SHA512",
    "EcDSA/SHA256",
    "EcDSA/SHA512",
    "EdDSA/SHA256",
    "EdDSA/SHA512",
}
DIGEST_LINES = {
    "    Header SHA256 digest: OK",
    "    Payload SHA256 digest: OK",
}


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


auditor = load_module("pds020_userspace_postsign_auditor", AUDITOR_PATH)
repro = load_module("pds020_userspace_postsign_repro", REPRO_PATH)


class PostSignError(RuntimeError):
    """The signed artifact, receipt or isolated verification failed closed."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PostSignError("receipt contains a duplicate key")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    raise PostSignError(f"receipt contains non-finite number {value}")


def strict_json(data: bytes, label: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PostSignError(f"{label} is invalid UTF-8 JSON") from exc


def exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise PostSignError(f"{label} fields are incomplete or unsupported")
    return value


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_executable(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise PostSignError("RPM signature verifier is missing or unsafe") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid not in {0, os.getuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        raise PostSignError("RPM signature verifier is missing or unsafe")


def run_bounded(
    command: list[str], maximum: int, label: str, temporary_home: Path
) -> bytes:
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
            env={
                "LC_ALL": "C",
                "LANG": "C",
                "HOME": os.fspath(temporary_home),
                "XDG_CONFIG_HOME": os.fspath(temporary_home / "config"),
                "PATH": "/usr/bin:/bin",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PostSignError(f"{label} could not run") from exc
    if (
        result.returncode != 0
        or len(result.stdout) > maximum
        or len(result.stderr) > maximum
    ):
        raise PostSignError(f"{label} failed or exceeded its output bound")
    return result.stdout


def parse_signature_output(
    data: bytes,
    artifact: Path,
    fingerprint: str,
    signature_version: int,
    signature_algorithm: str,
) -> dict[str, Any]:
    try:
        lines = data.decode("utf-8", errors="strict").splitlines()
    except UnicodeError as exc:
        raise PostSignError("RPM signature output is not UTF-8") from exc
    if not lines or lines[0] != f"{artifact}:":
        raise PostSignError("RPM signature output artifact identity is invalid")
    signatures: list[re.Match[str]] = []
    digests: list[str] = []
    for line in lines[1:]:
        match = SIGNATURE_RE.fullmatch(line)
        if match is not None:
            signatures.append(match)
        else:
            digests.append(line)
    if len(signatures) != 1 or len(digests) != 2 or set(digests) != DIGEST_LINES:
        raise PostSignError("RPM signature or digest set is invalid")
    signature = signatures[0]
    if (
        int(signature["version"]) != signature_version
        or signature["algorithm"] != signature_algorithm
        or signature["fingerprint"].lower() != fingerprint
    ):
        raise PostSignError("RPM signature does not match the receipt")
    return {
        "scope": "Header",
        "version": signature_version,
        "algorithm": signature_algorithm,
        "fingerprint": fingerprint,
        "count": 1,
        "legacy_signature_absent": True,
        "header_sha256_digest": "pass",
        "payload_sha256_digest": "pass",
    }


def verify_signature(
    rpmkeys: Path,
    public_key: Path,
    artifact: Path,
    signing: dict[str, Any],
) -> dict[str, Any]:
    safe_executable(rpmkeys)
    with tempfile.TemporaryDirectory(prefix="pds020-postsign-keyring-") as name:
        temporary_home = Path(name)
        database = temporary_home / "rpmdb"
        database.mkdir(mode=0o700)
        common = [str(rpmkeys), "--dbpath", str(database)]
        imported = run_bounded(
            common + ["--import", str(public_key)],
            MAX_TOOL_OUTPUT,
            "public key import",
            temporary_home,
        )
        if imported:
            raise PostSignError("public key import produced unexpected output")
        listed = run_bounded(
            common + ["--list"],
            MAX_TOOL_OUTPUT,
            "isolated key listing",
            temporary_home,
        )
        try:
            key_lines = listed.decode("utf-8", errors="strict").splitlines()
        except UnicodeError as exc:
            raise PostSignError("isolated key listing is not UTF-8") from exc
        fingerprint = signing["fingerprint"]
        if (
            len(key_lines) != 1
            or not key_lines[0].lower().startswith(f"{fingerprint} ")
            or not key_lines[0].endswith(" public key")
        ):
            raise PostSignError("isolated keyring does not contain exactly the receipt key")
        checked = run_bounded(
            common + ["--checksig", "--verbose", str(artifact)],
            MAX_TOOL_OUTPUT,
            "RPM signature verification",
            temporary_home,
        )
    return parse_signature_output(
        checked,
        artifact,
        fingerprint,
        signing["signature_version"],
        signing["signature_algorithm"],
    )


def load_receipt(path: Path) -> dict[str, Any]:
    receipt = exact_object(
        strict_json(repro.read_regular(path, MAX_RECEIPT_BYTES), "post-sign receipt"),
        {"schema", "artifact", "canonical_build", "signing", "policy"},
        "post-sign receipt",
    )
    if type(receipt["schema"]) is not int or receipt["schema"] != 1:
        raise PostSignError("post-sign receipt schema is unsupported")
    artifact = exact_object(
        receipt["artifact"], {"filename", "size", "sha256"}, "signed artifact"
    )
    if (
        not isinstance(artifact["filename"], str)
        or Path(artifact["filename"]).name != artifact["filename"]
        or type(artifact["size"]) is not int
        or artifact["size"] <= 0
        or not isinstance(artifact["sha256"], str)
        or not SHA256_RE.fullmatch(artifact["sha256"])
    ):
        raise PostSignError("signed artifact receipt is invalid")
    canonical = exact_object(
        receipt["canonical_build"],
        {
            "build_lock_sha256",
            "pre_sign_binary_sha256",
            "payload_sha256",
            "payload_manifest_sha256",
        },
        "canonical build receipt",
    )
    if any(
        not isinstance(canonical[key], str) or not SHA256_RE.fullmatch(canonical[key])
        for key in canonical
    ):
        raise PostSignError("canonical build receipt is invalid")
    signing = exact_object(
        receipt["signing"],
        {
            "public_key",
            "fingerprint",
            "signature_scope",
            "signature_version",
            "signature_algorithm",
            "signature_count",
        },
        "signing receipt",
    )
    public_key = exact_object(
        signing["public_key"], {"filename", "size", "sha256"}, "public key receipt"
    )
    if (
        not isinstance(public_key["filename"], str)
        or Path(public_key["filename"]).name != public_key["filename"]
        or type(public_key["size"]) is not int
        or not 0 < public_key["size"] <= MAX_KEY_BYTES
        or not isinstance(public_key["sha256"], str)
        or not SHA256_RE.fullmatch(public_key["sha256"])
        or not isinstance(signing["fingerprint"], str)
        or not FINGERPRINT_RE.fullmatch(signing["fingerprint"])
        or signing["signature_scope"] != "Header"
        or type(signing["signature_version"]) is not int
        or signing["signature_version"] not in {4, 6}
        or signing["signature_algorithm"] not in ALLOWED_ALGORITHMS
        or type(signing["signature_count"]) is not int
        or signing["signature_count"] != 1
    ):
        raise PostSignError("signing receipt is invalid")
    policy = exact_object(
        receipt["policy"],
        {
            "isolated_temporary_keyring",
            "legacy_signature_forbidden",
            "post_sign_payload_audit",
        },
        "post-sign policy",
    )
    if policy != {
        "isolated_temporary_keyring": True,
        "legacy_signature_forbidden": True,
        "post_sign_payload_audit": True,
    }:
        raise PostSignError("post-sign policy is invalid")
    return receipt


def evaluate(
    receipt_path: Path,
    artifact_path: Path,
    public_key_path: Path,
    build_lock_path: Path,
    source_lock_path: Path,
    rpmkeys: Path,
    rpm: Path,
    rpm2archive: Path,
) -> dict[str, Any]:
    receipt = load_receipt(receipt_path)
    artifact_record = receipt["artifact"]
    signing = receipt["signing"]
    key_record = signing["public_key"]
    build_lock_bytes = repro.read_regular(build_lock_path, repro.MAX_LOCK_BYTES)
    build_lock = repro.load_lock(build_lock_path)
    package = build_lock["package"]
    builder = build_lock["builder"]
    payload = build_lock["payload_audit"]
    canonical = receipt["canonical_build"]
    if canonical != {
        "build_lock_sha256": sha256(build_lock_bytes),
        "pre_sign_binary_sha256": package["binary_result"]["sha256"],
        "payload_sha256": package["binary_result"]["payload_sha256"],
        "payload_manifest_sha256": payload["manifest_sha256"],
    }:
        raise PostSignError("canonical build receipt differs from the build lock")
    if (
        artifact_path.name != artifact_record["filename"]
        or artifact_path.name != package["binary_result"]["filename"]
        or public_key_path.name != key_record["filename"]
    ):
        raise PostSignError("receipt filename differs from the supplied artifact")
    artifact_bytes = repro.read_regular(artifact_path, MAX_RPM_BYTES)
    public_key_bytes = repro.read_regular(public_key_path, MAX_KEY_BYTES)
    if (
        len(artifact_bytes) != artifact_record["size"]
        or sha256(artifact_bytes) != artifact_record["sha256"]
        or artifact_record["sha256"] == package["binary_result"]["sha256"]
        or len(public_key_bytes) != key_record["size"]
        or sha256(public_key_bytes) != key_record["sha256"]
    ):
        raise PostSignError("signed artifact or public key differs from the receipt")
    signature = verify_signature(rpmkeys, public_key_path, artifact_path, signing)
    header = repro.query_header(rpm, artifact_path)
    expected_header = {
        "name": package["name"],
        "version": package["version"],
        "release": package["release"],
        "arch": package["arch"],
        "buildhost": builder["macros"]["_buildhost"],
        "buildtime": builder["buildtime"],
        "payload_sha256": package["binary_result"]["payload_sha256"],
    }
    if header != expected_header:
        raise PostSignError("signed RPM header differs from the canonical build")
    audit = auditor.audit(source_lock_path, artifact_path, rpm, rpm2archive)
    expected_audit = {
        "payload_file_count": payload["file_count"],
        "payload_manifest_sha256": payload["manifest_sha256"],
        "scriptlet_count": payload["scriptlet_count"],
        "scriptlets_sha256": payload["scriptlets_sha256"],
        "fan_controller_sha256": payload["fan_controller_sha256"],
    }
    if (
        audit.get("payload_safe") is not True
        or audit.get("global_nopasswd_absent") is not True
        or audit.get("fan_controller_locked") is not True
        or any(audit.get(key) != value for key, value in expected_audit.items())
    ):
        raise PostSignError("post-sign payload audit differs from the build lock")
    if (
        repro.read_regular(artifact_path, MAX_RPM_BYTES) != artifact_bytes
        or repro.read_regular(public_key_path, MAX_KEY_BYTES) != public_key_bytes
        or repro.read_regular(build_lock_path, repro.MAX_LOCK_BYTES) != build_lock_bytes
    ):
        raise PostSignError("post-sign evidence changed during verification")
    return {
        "schema": 1,
        "artifact_read_only": True,
        "network": False,
        "build": False,
        "install": False,
        "isolated_temporary_keyring": True,
        "signature_verified": True,
        "signature_count": signature["count"],
        "signature_version": signature["version"],
        "signature_algorithm": signature["algorithm"],
        "signing_fingerprint": signature["fingerprint"],
        "legacy_signature_absent": signature["legacy_signature_absent"],
        "canonical_build_bound": True,
        "canonical_payload_verified": True,
        "payload_audit_verified": True,
        "artifact_ready_for_repository_staging": True,
        "release_ready": False,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--build-lock", type=Path, default=DEFAULT_BUILD_LOCK)
    parser.add_argument("--source-lock", type=Path, default=DEFAULT_SOURCE_LOCK)
    parser.add_argument("--rpmkeys", type=Path, default=Path("/usr/bin/rpmkeys"))
    parser.add_argument("--rpm", type=Path, default=Path("/usr/bin/rpm"))
    parser.add_argument("--rpm2archive", type=Path, default=Path("/usr/bin/rpm2archive"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        report = evaluate(
            args.receipt,
            args.artifact,
            args.public_key,
            args.build_lock,
            args.source_lock,
            args.rpmkeys,
            args.rpm,
            args.rpm2archive,
        )
    except (
        OSError,
        UnicodeError,
        ValueError,
        PostSignError,
        repro.ReproError,
        auditor.AuditError,
    ) as exc:
        print(json.dumps({"post_sign_verified": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify the future signed pds2 RPM archive and detached repository signature."""

from __future__ import annotations

import argparse
import copy
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
DEFAULT_ARCHIVE_LOCK = (
    ROOT / "packaging/pocketds-userspace/rootfs-package-archives-lock.pds2.json"
)
DEFAULT_REPOSITORY_LOCK = (
    ROOT / "packaging/pocketds-userspace/repository-metadata-lock.pds2.json"
)
DEFAULT_BUILD_LOCK = ROOT / "packaging/pocketds-userspace/build-lock.pds2.json"
DEFAULT_SOURCE_LOCK = ROOT / "packaging/pocketds-userspace/source-lock.pds2.json"
ARCHIVE_VERIFY_PATH = ROOT / "scripts/pds020-userspace-rpm-archive-verify.py"
REPOSITORY_VERIFY_PATH = ROOT / "scripts/pds020-userspace-rpm-repository-verify.py"
POSTSIGN_VERIFY_PATH = ROOT / "scripts/pds020-userspace-rpm-postsign.py"
MAX_RECEIPT_BYTES = 256 * 1024
MAX_KEY_BYTES = 1024 * 1024
MAX_SIGNATURE_BYTES = 1024 * 1024
MAX_RPM_BYTES = 16 * 1024 * 1024
MAX_TOOL_OUTPUT = 256 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
SEQUENCE = [
    "verify_unsigned_rootfs_archive",
    "verify_signed_project_rpm",
    "replace_project_archive_member",
    "verify_two_final_repodata_results",
    "verify_detached_repomd_signature",
]
POLICY = {
    "rpm_signed_before_metadata": True,
    "two_independent_metadata_results": True,
    "detached_repomd_signature_required": True,
    "dnf5_repo_gpgcheck_required": True,
    "isolated_public_keyring": True,
    "release_ready": False,
}
HASH_ALGORITHM_IDS = {"SHA256": "8", "SHA512": "10"}
FAILURE_STATUS = {
    "BADSIG",
    "ERRSIG",
    "EXPSIG",
    "EXPKEYSIG",
    "REVKEYSIG",
    "NO_PUBKEY",
    "NODATA",
    "FAILURE",
    "ERROR",
}


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


archive = load_module("pds020_repository_postsign_archive", ARCHIVE_VERIFY_PATH)
repository = load_module("pds020_repository_postsign_metadata", REPOSITORY_VERIFY_PATH)
postsign = load_module("pds020_repository_postsign_rpm", POSTSIGN_VERIFY_PATH)
repro = archive.repro


class RepositoryPostSignError(RuntimeError):
    """The signed repository receipt or its content chain failed closed."""


def exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RepositoryPostSignError(f"{label} fields are incomplete or unsupported")
    return value


def is_sha(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def artifact_record(value: Any, label: str, maximum: int) -> dict[str, Any]:
    record = exact_object(value, {"filename", "size", "sha256"}, label)
    if (
        not isinstance(record["filename"], str)
        or Path(record["filename"]).name != record["filename"]
        or type(record["size"]) is not int
        or not 0 < record["size"] <= maximum
        or not is_sha(record["sha256"])
    ):
        raise RepositoryPostSignError(f"{label} is invalid")
    return record


def load_receipt(path: Path) -> dict[str, Any]:
    receipt = exact_object(
        repro.strict_json(repro.read_regular(path, MAX_RECEIPT_BYTES), "repository receipt"),
        {
            "schema",
            "unsigned_inputs",
            "project_post_sign",
            "final_archive",
            "generator",
            "repository",
            "repository_signature",
            "sequence",
            "policy",
        },
        "repository post-sign receipt",
    )
    if type(receipt["schema"]) is not int or receipt["schema"] != 1:
        raise RepositoryPostSignError("repository receipt schema is unsupported")
    unsigned = exact_object(
        receipt["unsigned_inputs"],
        {"archive_lock_sha256", "repository_lock_sha256", "build_lock_sha256"},
        "unsigned input binding",
    )
    if not all(is_sha(value) for value in unsigned.values()):
        raise RepositoryPostSignError("unsigned input binding is invalid")
    project = exact_object(
        receipt["project_post_sign"],
        {"receipt", "signed_rpm"},
        "project post-sign binding",
    )
    artifact_record(project["receipt"], "project post-sign receipt", MAX_RECEIPT_BYTES)
    artifact_record(project["signed_rpm"], "signed project RPM", MAX_RPM_BYTES)
    final_archive = exact_object(
        receipt["final_archive"],
        {
            "package_count",
            "fedora_package_count",
            "total_bytes",
            "records_sha256",
            "project_filename",
            "project_sha256",
        },
        "final archive binding",
    )
    if (
        type(final_archive["package_count"]) is not int
        or final_archive["package_count"] != 322
        or type(final_archive["fedora_package_count"]) is not int
        or final_archive["fedora_package_count"] != 321
        or type(final_archive["total_bytes"]) is not int
        or not 0 < final_archive["total_bytes"] <= 256 * 1024 * 1024
        or not is_sha(final_archive["records_sha256"])
        or not isinstance(final_archive["project_filename"], str)
        or Path(final_archive["project_filename"]).name
        != final_archive["project_filename"]
        or not is_sha(final_archive["project_sha256"])
    ):
        raise RepositoryPostSignError("final archive binding is invalid")
    generator = exact_object(
        receipt["generator"],
        {
            "distro",
            "architecture",
            "nvr",
            "executable_size",
            "executable_sha256",
            "rpm_verify_clean",
            "independent_result_count",
            "parameters",
            "input_file_mtime",
        },
        "repository generator binding",
    )
    if type(generator["independent_result_count"]) is not int:
        raise RepositoryPostSignError("repository generator binding is invalid")
    repo = exact_object(
        receipt["repository"],
        {"revision", "repomd", "metadata", "primary_location_checksum_sha256"},
        "final repository binding",
    )
    artifact_record(repo["repomd"], "final repomd", repository.MAX_REPOMD_BYTES)
    if repo["repomd"]["filename"] != "repomd.xml":
        raise RepositoryPostSignError("final repomd filename is invalid")
    if type(repo["revision"]) is not int or not is_sha(
        repo["primary_location_checksum_sha256"]
    ):
        raise RepositoryPostSignError("final repository binding is invalid")
    metadata = repo["metadata"]
    if not isinstance(metadata, list) or len(metadata) != 3:
        raise RepositoryPostSignError("final repository metadata is incomplete")
    types: list[str] = []
    for value in metadata:
        record = exact_object(
            value,
            {"type", "filename", "size", "sha256", "open_size", "open_sha256"},
            "final metadata record",
        )
        artifact_record(
            {key: record[key] for key in ("filename", "size", "sha256")},
            "final metadata artifact",
            repository.MAX_METADATA_BYTES,
        )
        if (
            record["type"] not in {"filelists", "other", "primary"}
            or type(record["open_size"]) is not int
            or not 0 < record["open_size"] <= repository.MAX_METADATA_BYTES
            or not is_sha(record["open_sha256"])
            or not record["filename"].endswith(f"-{record['type']}.xml.xz")
            or not record["filename"].startswith(record["sha256"])
        ):
            raise RepositoryPostSignError("final metadata record is invalid")
        types.append(record["type"])
    if types != ["filelists", "other", "primary"]:
        raise RepositoryPostSignError("final metadata records are not canonical")
    signature = exact_object(
        receipt["repository_signature"],
        {
            "artifact",
            "public_key",
            "certificate_primary_fingerprint",
            "signing_fingerprint",
            "signature_version",
            "hash_algorithm",
            "signature_count",
        },
        "repository signature binding",
    )
    artifact_record(signature["artifact"], "detached signature", MAX_SIGNATURE_BYTES)
    artifact_record(signature["public_key"], "repository public key", MAX_KEY_BYTES)
    if (
        signature["artifact"]["filename"] != "repomd.xml.asc"
        or not isinstance(signature["certificate_primary_fingerprint"], str)
        or FINGERPRINT_RE.fullmatch(signature["certificate_primary_fingerprint"])
        is None
        or not isinstance(signature["signing_fingerprint"], str)
        or FINGERPRINT_RE.fullmatch(signature["signing_fingerprint"]) is None
        or type(signature["signature_version"]) is not int
        or signature["signature_version"] not in {4, 6}
        or signature["hash_algorithm"] not in HASH_ALGORITHM_IDS
        or type(signature["signature_count"]) is not int
        or signature["signature_count"] != 1
    ):
        raise RepositoryPostSignError("repository signature binding is invalid")
    if receipt["sequence"] != SEQUENCE or receipt["policy"] != POLICY:
        raise RepositoryPostSignError("repository signing sequence or policy is invalid")
    return receipt


def derive_final_packages(
    unsigned_archive: dict[str, Any],
    signed_rpm: dict[str, Any],
    signing_fingerprint: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    final = copy.deepcopy(unsigned_archive)
    projects = [
        record for record in final["packages"] if record["origin"] == "project-pre-sign"
    ]
    if len(projects) != 1:
        raise RepositoryPostSignError("unsigned archive project boundary differs")
    project = projects[0]
    if project["filename"] != signed_rpm["filename"]:
        raise RepositoryPostSignError("signed project filename differs from the archive")
    project.update(
        {
            "origin": "project-post-sign",
            "size": signed_rpm["size"],
            "sha256": signed_rpm["sha256"],
            "signature_verified": True,
            "signing_key_fingerprint": signing_fingerprint,
        }
    )
    canonical = json.dumps(
        final["packages"], sort_keys=True, separators=(",", ":")
    ).encode()
    aggregate = {
        "package_count": len(final["packages"]),
        "fedora_package_count": unsigned_archive["fedora_package_count"],
        "total_bytes": sum(record["size"] for record in final["packages"]),
        "records_sha256": hashlib.sha256(canonical).hexdigest(),
        "project_filename": project["filename"],
        "project_sha256": project["sha256"],
    }
    return final, aggregate


def verify_final_archive(directory: Path, final_archive: dict[str, Any]) -> None:
    records = final_archive["packages"]
    names = {record["filename"] for record in records}
    archive.safe_archive_directory(directory, names)
    for record in records:
        archive.read_locked(
            directory / record["filename"],
            record["size"],
            record["sha256"],
            MAX_RPM_BYTES,
            "final archive package",
        )
    archive.safe_archive_directory(directory, names)


def safe_executable(path: Path, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise RepositoryPostSignError(f"{label} is missing or unsafe") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid not in {0, os.getuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        raise RepositoryPostSignError(f"{label} is missing or unsafe")


def run_tool(command: list[str], label: str, home: Path) -> tuple[bytes, bytes]:
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
                "HOME": os.fspath(home),
                "GNUPGHOME": os.fspath(home),
                "PATH": "/usr/bin:/bin",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RepositoryPostSignError(f"{label} could not run") from exc
    if (
        result.returncode != 0
        or len(result.stdout) > MAX_TOOL_OUTPUT
        or len(result.stderr) > MAX_TOOL_OUTPUT
    ):
        raise RepositoryPostSignError(f"{label} failed or exceeded its output bound")
    return result.stdout, result.stderr


def primary_fingerprint(colons: bytes) -> str:
    try:
        lines = colons.decode("utf-8", errors="strict").splitlines()
    except UnicodeError as exc:
        raise RepositoryPostSignError("public key listing is not UTF-8") from exc
    records = [line.split(":") for line in lines]
    pub_positions = [index for index, record in enumerate(records) if record[0] == "pub"]
    if len(pub_positions) != 1 or any(record[0] in {"sec", "ssb"} for record in records):
        raise RepositoryPostSignError("public key input is not exactly one public certificate")
    start = pub_positions[0] + 1
    for record in records[start:]:
        if record[0] in {"pub", "sub"}:
            break
        if record[0] == "fpr" and len(record) > 9:
            fingerprint = record[9].lower()
            if FINGERPRINT_RE.fullmatch(fingerprint):
                return fingerprint
    raise RepositoryPostSignError("public certificate primary fingerprint is missing")


def parse_signature_status(data: bytes, signature: dict[str, Any]) -> dict[str, Any]:
    try:
        lines = data.decode("utf-8", errors="strict").splitlines()
    except UnicodeError as exc:
        raise RepositoryPostSignError("OpenPGP status is not UTF-8") from exc
    payloads = [line[9:] for line in lines if line.startswith("[GNUPG:] ")]
    tags = [payload.split(" ", 1)[0] for payload in payloads]
    if any(tag in FAILURE_STATUS for tag in tags):
        raise RepositoryPostSignError("detached repository signature is invalid")
    valid = [payload.split() for payload in payloads if payload.startswith("VALIDSIG ")]
    if tags.count("NEWSIG") != 1 or tags.count("GOODSIG") != 1 or len(valid) != 1:
        raise RepositoryPostSignError("detached repository signature set is invalid")
    fields = valid[0]
    if (
        len(fields) < 11
        or fields[1].lower() != signature["signing_fingerprint"]
        or fields[5] != str(signature["signature_version"])
        or fields[8] != HASH_ALGORITHM_IDS[signature["hash_algorithm"]]
        or fields[10].lower() != signature["certificate_primary_fingerprint"]
    ):
        raise RepositoryPostSignError("detached repository signature identity differs")
    return {
        "signature_count": 1,
        "signature_version": signature["signature_version"],
        "hash_algorithm": signature["hash_algorithm"],
        "signing_fingerprint": signature["signing_fingerprint"],
        "certificate_primary_fingerprint": signature[
            "certificate_primary_fingerprint"
        ],
    }


def verify_detached_signature(
    gpg: Path,
    public_key: Path,
    signature_path: Path,
    repomd_path: Path,
    signature: dict[str, Any],
) -> dict[str, Any]:
    safe_executable(gpg, "OpenPGP verifier")
    with tempfile.TemporaryDirectory(prefix="pds020-repository-keyring-") as name:
        home = Path(name)
        home.chmod(0o700)
        common = [str(gpg), "--batch", "--no-tty", "--no-options", "--homedir", str(home)]
        shown, _ = run_tool(
            common + ["--with-colons", "--fingerprint", "--show-keys", str(public_key)],
            "public certificate inspection",
            home,
        )
        if primary_fingerprint(shown) != signature["certificate_primary_fingerprint"]:
            raise RepositoryPostSignError("public certificate fingerprint differs")
        run_tool(
            common
            + [
                "--no-auto-key-retrieve",
                "--import-options",
                "import-minimal",
                "--import",
                str(public_key),
            ],
            "public certificate import",
            home,
        )
        listed, _ = run_tool(
            common + ["--with-colons", "--fingerprint", "--list-keys"],
            "isolated public key listing",
            home,
        )
        if primary_fingerprint(listed) != signature["certificate_primary_fingerprint"]:
            raise RepositoryPostSignError("isolated public keyring differs")
        status, _ = run_tool(
            common
            + [
                "--no-auto-key-retrieve",
                "--status-fd",
                "1",
                "--verify",
                str(signature_path),
                str(repomd_path),
            ],
            "detached repository signature verification",
            home,
        )
    return parse_signature_status(status, signature)


def read_bound(path: Path, record: dict[str, Any], maximum: int, label: str) -> bytes:
    try:
        data = repro.read_regular(path, maximum)
    except repro.ReproError as exc:
        raise RepositoryPostSignError(f"{label} is missing or unsafe") from exc
    if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise RepositoryPostSignError(f"{label} differs from the receipt")
    return data


def evaluate(
    receipt_path: Path,
    rpm_postsign_receipt_path: Path,
    signed_rpm_path: Path,
    public_key_path: Path,
    signature_path: Path,
    unsigned_archive_dir: Path,
    signed_archive_dir: Path,
    result_a: Path,
    result_b: Path,
    archive_lock_path: Path,
    repository_lock_path: Path,
    build_lock_path: Path,
    source_lock_path: Path,
    createrepo_c: Path,
    gpg: Path,
    rpmkeys: Path,
    rpm: Path,
    rpm2archive: Path,
) -> dict[str, Any]:
    receipt_bytes = repro.read_regular(receipt_path, MAX_RECEIPT_BYTES)
    receipt = load_receipt(receipt_path)
    archive_lock_bytes = repro.read_regular(archive_lock_path, archive.MAX_LOCK_BYTES)
    repository_lock_bytes = repro.read_regular(repository_lock_path, repository.MAX_LOCK_BYTES)
    build_lock_bytes = repro.read_regular(build_lock_path, repro.MAX_LOCK_BYTES)
    if receipt["unsigned_inputs"] != {
        "archive_lock_sha256": hashlib.sha256(archive_lock_bytes).hexdigest(),
        "repository_lock_sha256": hashlib.sha256(repository_lock_bytes).hexdigest(),
        "build_lock_sha256": hashlib.sha256(build_lock_bytes).hexdigest(),
    }:
        raise RepositoryPostSignError("unsigned input locks differ from the receipt")
    unsigned_archive = archive.load_lock(archive_lock_path)
    unsigned_repository = repository.load_lock(repository_lock_path)
    if receipt["generator"] != unsigned_repository["generator"]:
        raise RepositoryPostSignError("final repository generator differs from pre-sign proof")
    if receipt["repository"]["revision"] != unsigned_repository["repository"]["revision"]:
        raise RepositoryPostSignError("final repository revision differs from pre-sign proof")
    if receipt["repository"] == unsigned_repository["repository"]:
        raise RepositoryPostSignError("unsigned repository metadata cannot be release-signed")
    if (
        receipt["repository"]["primary_location_checksum_sha256"]
        == unsigned_repository["repository"]["primary_location_checksum_sha256"]
    ):
        raise RepositoryPostSignError(
            "final primary manifest did not follow project RPM signing"
        )
    unsigned_by_type = {
        record["type"]: record for record in unsigned_repository["repository"]["metadata"]
    }
    for record in receipt["repository"]["metadata"]:
        old = unsigned_by_type[record["type"]]
        if record["sha256"] == old["sha256"] or record["open_sha256"] == old["open_sha256"]:
            raise RepositoryPostSignError("final metadata did not follow project RPM signing")
    project_binding = receipt["project_post_sign"]
    rpm_receipt_bytes = read_bound(
        rpm_postsign_receipt_path,
        project_binding["receipt"],
        MAX_RECEIPT_BYTES,
        "project post-sign receipt",
    )
    signed_rpm_bytes = read_bound(
        signed_rpm_path,
        project_binding["signed_rpm"],
        MAX_RPM_BYTES,
        "signed project RPM",
    )
    key_bytes = read_bound(
        public_key_path,
        receipt["repository_signature"]["public_key"],
        MAX_KEY_BYTES,
        "repository public key",
    )
    detached_bytes = read_bound(
        signature_path,
        receipt["repository_signature"]["artifact"],
        MAX_SIGNATURE_BYTES,
        "detached repository signature",
    )
    signature_name = receipt["repository_signature"]["artifact"]["filename"]
    if signature_path != result_a / signature_name:
        raise RepositoryPostSignError(
            "detached signature must be inside the first repodata result"
        )
    second_detached_bytes = read_bound(
        result_b / signature_name,
        receipt["repository_signature"]["artifact"],
        MAX_SIGNATURE_BYTES,
        "second detached repository signature",
    )
    if second_detached_bytes != detached_bytes:
        raise RepositoryPostSignError("detached repository signature copies differ")
    rpm_receipt = postsign.load_receipt(rpm_postsign_receipt_path)
    if (
        rpm_receipt["artifact"] != project_binding["signed_rpm"]
        or rpm_receipt["signing"]["public_key"]
        != receipt["repository_signature"]["public_key"]
        or rpm_receipt["signing"]["fingerprint"]
        != receipt["repository_signature"]["certificate_primary_fingerprint"]
    ):
        raise RepositoryPostSignError("RPM and repository signing identities differ")
    rpm_report = postsign.evaluate(
        rpm_postsign_receipt_path,
        signed_rpm_path,
        public_key_path,
        build_lock_path,
        source_lock_path,
        rpmkeys,
        rpm,
        rpm2archive,
    )
    if (
        rpm_report.get("signature_verified") is not True
        or rpm_report.get("canonical_payload_verified") is not True
        or rpm_report.get("artifact_ready_for_repository_staging") is not True
        or rpm_report.get("release_ready") is not False
    ):
        raise RepositoryPostSignError("project RPM post-sign gate failed")
    unsigned_report = archive.verify(
        unsigned_archive,
        unsigned_archive_dir,
        source_lock_path,
        build_lock_path,
        rpm,
        rpmkeys,
        rpm2archive,
    )
    if unsigned_report.get("verified") is not True:
        raise RepositoryPostSignError("unsigned rootfs archive verification failed")
    final_archive, aggregate = derive_final_packages(
        unsigned_archive,
        project_binding["signed_rpm"],
        rpm_receipt["signing"]["fingerprint"],
    )
    if aggregate != receipt["final_archive"]:
        raise RepositoryPostSignError("final archive aggregate differs from the receipt")
    verify_final_archive(signed_archive_dir, final_archive)
    repository.verify_generator(receipt, createrepo_c, rpm)
    metadata_lock = {
        "archive": {"package_count": aggregate["package_count"]},
        "repository": receipt["repository"],
    }
    first = repository.verify_result(
        metadata_lock, final_archive, result_a, {signature_name}
    )
    second = repository.verify_result(
        metadata_lock, final_archive, result_b, {signature_name}
    )
    if first != second:
        raise RepositoryPostSignError("final repository metadata results differ")
    repomd_path = result_a / receipt["repository"]["repomd"]["filename"]
    signature_report = verify_detached_signature(
        gpg,
        public_key_path,
        signature_path,
        repomd_path,
        receipt["repository_signature"],
    )
    if (
        repro.read_regular(receipt_path, MAX_RECEIPT_BYTES) != receipt_bytes
        or repro.read_regular(rpm_postsign_receipt_path, MAX_RECEIPT_BYTES)
        != rpm_receipt_bytes
        or repro.read_regular(signed_rpm_path, MAX_RPM_BYTES) != signed_rpm_bytes
        or repro.read_regular(public_key_path, MAX_KEY_BYTES) != key_bytes
        or repro.read_regular(signature_path, MAX_SIGNATURE_BYTES) != detached_bytes
        or repro.read_regular(result_b / signature_name, MAX_SIGNATURE_BYTES)
        != second_detached_bytes
        or repro.read_regular(archive_lock_path, archive.MAX_LOCK_BYTES)
        != archive_lock_bytes
        or repro.read_regular(repository_lock_path, repository.MAX_LOCK_BYTES)
        != repository_lock_bytes
        or repro.read_regular(build_lock_path, repro.MAX_LOCK_BYTES) != build_lock_bytes
    ):
        raise RepositoryPostSignError("repository signing evidence changed during verification")
    verify_final_archive(signed_archive_dir, final_archive)
    return {
        "schema": 1,
        "verified": True,
        "offline": True,
        "read_only_artifacts": True,
        "build": False,
        "install": False,
        "sign": False,
        "project_rpm_signature_verified": True,
        "project_payload_verified": True,
        "fedora_package_signatures_verified": True,
        "final_archive_package_count": aggregate["package_count"],
        "metadata_reproducible": True,
        "detached_repomd_signature_verified": True,
        "signature": signature_report,
        "dnf5_repo_gpgcheck_required": True,
        "dnf5_repo_gpgcheck_smoke": False,
        "repository_ready_for_composition": True,
        "release_ready": False,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--rpm-postsign-receipt", type=Path, required=True)
    parser.add_argument("--signed-rpm", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--unsigned-archive-dir", type=Path, required=True)
    parser.add_argument("--signed-archive-dir", type=Path, required=True)
    parser.add_argument("--result-a", type=Path, required=True)
    parser.add_argument("--result-b", type=Path, required=True)
    parser.add_argument("--archive-lock", type=Path, default=DEFAULT_ARCHIVE_LOCK)
    parser.add_argument("--repository-lock", type=Path, default=DEFAULT_REPOSITORY_LOCK)
    parser.add_argument("--build-lock", type=Path, default=DEFAULT_BUILD_LOCK)
    parser.add_argument("--source-lock", type=Path, default=DEFAULT_SOURCE_LOCK)
    parser.add_argument("--createrepo-c", type=Path, default=Path("/usr/bin/createrepo_c"))
    parser.add_argument("--gpg", type=Path, default=Path("/usr/bin/gpg"))
    parser.add_argument("--rpmkeys", type=Path, default=Path("/usr/bin/rpmkeys"))
    parser.add_argument("--rpm", type=Path, default=Path("/usr/bin/rpm"))
    parser.add_argument("--rpm2archive", type=Path, default=Path("/usr/bin/rpm2archive"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        report = evaluate(
            args.receipt,
            args.rpm_postsign_receipt,
            args.signed_rpm,
            args.public_key,
            args.signature,
            args.unsigned_archive_dir,
            args.signed_archive_dir,
            args.result_a,
            args.result_b,
            args.archive_lock,
            args.repository_lock,
            args.build_lock,
            args.source_lock,
            args.createrepo_c,
            args.gpg,
            args.rpmkeys,
            args.rpm,
            args.rpm2archive,
        )
    except (
        OSError,
        UnicodeError,
        ValueError,
        RepositoryPostSignError,
        repro.ReproError,
        repository.repro.ReproError,
        postsign.repro.ReproError,
        archive.ArchiveError,
        repository.RepositoryError,
        postsign.PostSignError,
        archive.auditor.AuditError,
        postsign.auditor.AuditError,
    ) as exc:
        print(json.dumps({"verified": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

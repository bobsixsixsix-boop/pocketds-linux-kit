#!/usr/bin/env python3
"""Fail-closed, read-only audit of a mounted Pocket DS release root."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Sequence
from urllib.parse import urlparse


# Importing the in-process Chromium verifier must not create __pycache__ in the
# repository being audited.
sys.dont_write_bytecode = True


SCHEMA_VERSION = 1
LIVE_CONFIRMATION = "READ-ONLY-LIVE-ROOT"
MAX_TEXT_BYTES = 262_144
MAX_ASSET_BYTES = 64 * 1024 * 1024
BROAD_NOPASSWD = re.compile(r"\bNOPASSWD\s*:\s*ALL(?:\s*(?:,|$))", re.IGNORECASE)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SPDX_CREATED_RE = re.compile(r"^20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
UNRESOLVED = {"", "none", "noassertion", "unknown", "tbd", "todo", "unresolved"}
LEGAL_PLACEHOLDER_RE = re.compile(
    rb"\b(?:TBD|TODO|UNKNOWN|UNRESOLVED|PLACEHOLDER|NOASSERTION)\b",
    re.IGNORECASE,
)
REQUIRED_EVIDENCE = (
    "LICENSE",
    "NOTICE",
    "THIRD-PARTY.json",
    "SBOM.spdx.json",
    "components/assets/ASSETS.json",
)


class AuditError(RuntimeError):
    """An invalid audit target or unsafe input."""


@dataclass(frozen=True)
class Check:
    check_id: str
    status: str
    summary: str
    evidence: dict[str, object]


def check(
    check_id: str,
    passed: bool,
    summary: str,
    **evidence: object,
) -> Check:
    return Check(check_id, "pass" if passed else "fail", summary, evidence)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_regular(path: Path, maximum: int = MAX_ASSET_BYTES) -> str:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > maximum
        ):
            raise AuditError("unsafe-asset")
        digest = hashlib.sha256()
        remaining = metadata.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise AuditError("short-asset-read")
            digest.update(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise AuditError("asset-changed-during-read")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def read_regular(
    path: Path,
    *,
    require_root_owner: bool = False,
    allow_empty: bool = False,
) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AuditError(f"unsafe-or-unreadable:{path.name}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise AuditError(f"unsafe-file:{path.name}")
        if require_root_owner and metadata.st_uid != 0:
            raise AuditError(f"non-root-owner:{path.name}")
        if metadata.st_size > MAX_TEXT_BYTES or (
            metadata.st_size == 0 and not allow_empty
        ):
            raise AuditError(f"unsafe-size:{path.name}")
        data = bytearray()
        while True:
            block = os.read(descriptor, min(65_536, MAX_TEXT_BYTES + 1 - len(data)))
            if not block:
                break
            data.extend(block)
            if len(data) > MAX_TEXT_BYTES:
                raise AuditError(f"unsafe-size:{path.name}")
        return bytes(data)
    finally:
        os.close(descriptor)


def logical_sudo_lines(data: bytes) -> list[str]:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise AuditError("sudoers-not-utf8") from exc
    lines: list[str] = []
    pending = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        is_hash_include = bool(
            re.match(r"#include(?:dir)?\s", stripped, re.IGNORECASE)
        )
        if not stripped or (stripped.startswith("#") and not is_hash_include):
            continue
        if not is_hash_include:
            escaped = False
            content: list[str] = []
            for character in stripped:
                if character == "#" and not escaped:
                    break
                content.append(character)
                escaped = character == "\\" and not escaped
                if character != "\\":
                    escaped = False
            stripped = "".join(content).rstrip()
            if not stripped:
                continue
        current = pending + stripped
        if current.endswith("\\"):
            pending = current[:-1] + " "
            continue
        pending = ""
        lines.append(current)
    if pending:
        raise AuditError("unterminated-sudoers-continuation")
    return lines


def audit_sudoers(root: Path, repo: Path) -> list[Check]:
    expected_path = repo / "components/control-panel/90-pocketds-linux-kit"
    expected = read_regular(expected_path)
    paths: list[Path] = []
    structural_issues = 0
    base = root / "etc/sudoers"
    if base.is_file() and not base.is_symlink():
        paths.append(base)
    else:
        structural_issues += 1
    directory = root / "etc/sudoers.d"
    if directory.is_dir() and not directory.is_symlink():
        try:
            metadata = directory.lstat()
            if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
                structural_issues += 1
            paths.extend(
                sorted(
                    path
                    for path in directory.iterdir()
                    if not path.name.startswith(".")
                )
            )
        except OSError:
            structural_issues += 1
    else:
        structural_issues += 1

    broad_count = 0
    unexpected_nopasswd_count = 0
    unsafe_count = 0
    parsed_count = 0
    expected_matches = False
    unexpected_include_count = 0
    rule_hashes: list[str] = []
    for path in paths:
        try:
            data = read_regular(path, require_root_owner=True)
            metadata = path.lstat()
            mode = stat.S_IMODE(metadata.st_mode)
            if mode & 0o022:
                unsafe_count += 1
            lines = logical_sudo_lines(data)
        except (AuditError, OSError):
            unsafe_count += 1
            continue
        parsed_count += 1
        rule_hashes.append(sha256_bytes(data))
        is_expected = path.name == "90-pocketds-linux-kit" and data == expected
        expected_matches = expected_matches or is_expected
        for line in lines:
            include = re.match(
                r"(?:@|#)include(dir)?\s+(.+?)\s*$", line, re.IGNORECASE
            )
            if include:
                operand = include.group(2).strip().strip('"')
                if not (include.group(1) and operand == "/etc/sudoers.d"):
                    unexpected_include_count += 1
            if BROAD_NOPASSWD.search(line):
                broad_count += 1
            if "NOPASSWD" in line.upper() and not is_expected:
                unexpected_nopasswd_count += 1

    return [
        check(
            "sudoers-files-safe",
            bool(paths)
            and structural_issues == 0
            and unsafe_count == 0
            and unexpected_include_count == 0
            and parsed_count == len(paths),
            "all discovered sudoers files are root-owned, regular, bounded and not writable by group/other",
            discovered=len(paths),
            parsed=parsed_count,
            unsafe=unsafe_count,
            structural_issues=structural_issues,
            unexpected_includes=unexpected_include_count,
            content_sha256=sorted(rule_hashes),
        ),
        check(
            "no-broad-nopasswd",
            broad_count == 0,
            "no NOPASSWD rule grants the ALL command set",
            broad_rule_count=broad_count,
        ),
        check(
            "only-repository-nopasswd",
            unexpected_nopasswd_count == 0 and expected_matches,
            "the only passwordless rule is the exact repository Panel helper policy",
            unexpected_rule_count=unexpected_nopasswd_count,
            expected_policy_matches=expected_matches,
            expected_policy_sha256=sha256_bytes(expected),
        ),
    ]


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise AuditError("duplicate-json-key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> Any:
    raise AuditError(f"release evidence contains non-finite number {value}")


def _json_object(path: Path) -> dict[str, Any] | None:
    try:
        text = read_regular(path).decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (
        AuditError,
        UnicodeError,
        json.JSONDecodeError,
        OSError,
        RecursionError,
    ):
        return None
    return value if isinstance(value, dict) else None


def _resolved_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() not in UNRESOLVED


def _https_url(value: Any) -> bool:
    if not isinstance(value, str) or any(ord(character) < 0x20 for character in value):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username


def _manifest_path(value: Any) -> PurePosixPath | None:
    if not isinstance(value, str) or "\\" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path


def _repository_file(repo: Path, relative: PurePosixPath) -> Path:
    if repo.is_symlink() or not repo.is_dir():
        raise AuditError("unsafe-repository-root")
    current = repo
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise AuditError("unsafe-repository-parent")
    return current / relative.parts[-1]


def _repository_json(repo: Path, relative: str) -> dict[str, Any] | None:
    try:
        return _json_object(_repository_file(repo, PurePosixPath(relative)))
    except AuditError:
        return None


def _spdx_expression(value: Any) -> bool:
    if not isinstance(value, str) or value.strip().lower() in UNRESOLVED:
        return False
    value = value.strip()
    tokens: list[str] = []
    position = 0
    token_re = re.compile(r"\s*(AND|OR|WITH|\(|\)|[A-Za-z0-9][A-Za-z0-9.+:-]*)")
    while position < len(value):
        match = token_re.match(value, position)
        if match is None:
            return False
        tokens.append(match.group(1))
        position = match.end()
    if not tokens:
        return False

    cursor = 0

    def primary() -> bool:
        nonlocal cursor
        if cursor >= len(tokens):
            return False
        if tokens[cursor] == "(":
            cursor += 1
            if not expression() or cursor >= len(tokens) or tokens[cursor] != ")":
                return False
            cursor += 1
            return True
        token = tokens[cursor]
        if token in {"AND", "OR", "WITH", ")"} or token.lower() in UNRESOLVED:
            return False
        cursor += 1
        return True

    def factor() -> bool:
        nonlocal cursor
        if not primary():
            return False
        if cursor < len(tokens) and tokens[cursor] == "WITH":
            cursor += 1
            if cursor >= len(tokens) or tokens[cursor] in {"AND", "OR", "WITH", "(", ")"}:
                return False
            cursor += 1
        return True

    def conjunction() -> bool:
        nonlocal cursor
        if not factor():
            return False
        while cursor < len(tokens) and tokens[cursor] == "AND":
            cursor += 1
            if not factor():
                return False
        return True

    def expression() -> bool:
        nonlocal cursor
        if not conjunction():
            return False
        while cursor < len(tokens) and tokens[cursor] == "OR":
            cursor += 1
            if not conjunction():
                return False
        return True

    return expression() and cursor == len(tokens)


def _legal_text_preflight(repo: Path) -> bool:
    for relative, minimum in (("LICENSE", 128), ("NOTICE", 32)):
        try:
            data = read_regular(_repository_file(repo, PurePosixPath(relative)))
            data.decode("utf-8", errors="strict")
        except (AuditError, OSError, UnicodeError):
            return False
        if len(data.strip()) < minimum or LEGAL_PLACEHOLDER_RE.search(data):
            return False
    return True


def _spdx_preflight(document: dict[str, Any] | None) -> tuple[bool, int]:
    if (
        document is None
        or document.get("@context")
        != "https://spdx.org/rdf/3.0.1/spdx-context.jsonld"
    ):
        return False, 0
    graph = document.get("@graph")
    if not isinstance(graph, list) or not 5 <= len(graph) <= 4_096:
        return False, 0 if not isinstance(graph, list) else len(graph)

    def valid_identifier(value: Any) -> bool:
        if not isinstance(value, str) or value.strip().lower() in UNRESOLVED:
            return False
        if value.startswith("_:"):
            return len(value) > 2
        return bool(urlparse(value).scheme)

    by_identifier: dict[str, dict[str, Any]] = {}
    for element in graph:
        if not isinstance(element, dict):
            return False, len(graph)
        spdx_id = element.get("spdxId")
        blank_id = element.get("@id")
        if (spdx_id is None) == (blank_id is None):
            return False, len(graph)
        identifier = spdx_id if spdx_id is not None else blank_id
        if (
            not valid_identifier(identifier)
            or identifier in by_identifier
            or not _resolved_text(element.get("type"))
        ):
            return False, len(graph)
        by_identifier[identifier] = element

    creation_infos = {
        identifier: element
        for identifier, element in by_identifier.items()
        if element["type"] == "CreationInfo"
    }
    agent_types = {"Agent", "Organization", "Person", "SoftwareAgent"}

    def valid_creation_info(value: dict[str, Any]) -> bool:
        creators = value.get("createdBy")
        return (
            value.get("type") == "CreationInfo"
            and value.get("specVersion") == "3.0.1"
            and isinstance(value.get("created"), str)
            and SPDX_CREATED_RE.fullmatch(value["created"]) is not None
            and isinstance(creators, list)
            and bool(creators)
            and all(isinstance(item, str) for item in creators)
            and len(set(creators)) == len(creators)
            and all(
                valid_identifier(item)
                and item in by_identifier
                and by_identifier[item]["type"] in agent_types
                for item in creators
            )
        )

    if not creation_infos or not all(
        valid_creation_info(item) for item in creation_infos.values()
    ):
        return False, len(graph)
    for element in graph:
        if element["type"] == "CreationInfo":
            continue
        reference = element.get("creationInfo")
        if (
            not isinstance(reference, str)
            or reference not in creation_infos
        ):
            return False, len(graph)

    def references(value: Any) -> set[str] | None:
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) for item in value)
            or len(set(value)) != len(value)
            or any(item not in by_identifier for item in value)
        ):
            return None
        return set(value)

    documents = [item for item in graph if item["type"] == "SpdxDocument"]
    sboms = [item for item in graph if item["type"] == "software_Sbom"]
    packages = [item for item in graph if item["type"] == "software_Package"]
    if len(documents) != 1 or not sboms or not packages:
        return False, len(graph)
    document_elements = references(documents[0].get("element"))
    document_roots = references(documents[0].get("rootElement"))
    sbom_ids = {item.get("spdxId", item.get("@id")) for item in sboms}
    package_ids = {item.get("spdxId", item.get("@id")) for item in packages}
    if (
        document_elements is None
        or document_roots is None
        or not sbom_ids.issubset(document_elements)
        or not package_ids.issubset(document_elements)
        or not document_roots.intersection(sbom_ids)
    ):
        return False, len(graph)
    referenced_packages: set[str] = set()
    for sbom in sboms:
        sbom_elements = references(sbom.get("element"))
        sbom_roots = references(sbom.get("rootElement"))
        if (
            sbom_elements is None
            or sbom_roots is None
            or not sbom_roots.intersection(package_ids)
        ):
            return False, len(graph)
        referenced_packages.update(sbom_elements.intersection(package_ids))
    if referenced_packages != package_ids:
        return False, len(graph)
    for package in packages:
        if (
            not _resolved_text(package.get("name"))
            or not _resolved_text(package.get("software_packageVersion"))
            or not _resolved_text(package.get("software_copyrightText"))
            or not (
                _https_url(package.get("software_downloadLocation"))
                or _resolved_text(package.get("software_packageUrl"))
            )
        ):
            return False, len(graph)
    return True, len(graph)


def _third_party_preflight(repo: Path, manifest: dict[str, Any] | None) -> tuple[bool, int]:
    if (
        manifest is None
        or type(manifest.get("schema")) is not int
        or manifest.get("schema") != 1
    ):
        return False, 0
    components = manifest.get("components")
    if not isinstance(components, list) or not components:
        return False, 0
    identifiers: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            return False, len(components)
        identifier = component.get("id")
        evidence = component.get("evidence")
        if (
            not _resolved_text(identifier)
            or identifier in identifiers
            or not _resolved_text(component.get("name"))
            or not _resolved_text(component.get("version"))
            or not _spdx_expression(component.get("license"))
            or not _https_url(component.get("source"))
            or component.get("redistribution") is not True
            or not isinstance(evidence, list)
            or not evidence
        ):
            return False, len(components)
        identifiers.add(identifier)
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
                return False, len(components)
            relative = _manifest_path(item["path"])
            expected = item["sha256"]
            if (
                relative is None
                or not isinstance(expected, str)
                or not SHA256_RE.fullmatch(expected)
            ):
                return False, len(components)
            try:
                if sha256_regular(_repository_file(repo, relative)) != expected:
                    return False, len(components)
            except (AuditError, OSError):
                return False, len(components)
    return True, len(components)


def _asset_preflight(repo: Path, manifest: dict[str, Any] | None) -> tuple[bool, int, int]:
    asset_root = repo / "assets"
    actual: set[str] = set()
    try:
        metadata = asset_root.lstat()
    except FileNotFoundError:
        # An empty asset-free tree loses this directory in a Git checkout.
        return (
            isinstance(manifest, dict)
            and type(manifest.get("schema")) is int
            and manifest == {"schema": 1, "profile": "asset-free", "assets": []},
            0,
            0,
        )
    except OSError:
        return False, 0, 0
    if not stat.S_ISDIR(metadata.st_mode):
        return False, 0, 0
    try:
        for path in asset_root.rglob("*"):
            if path.is_symlink() or (not path.is_file() and not path.is_dir()):
                return False, 0, len(actual)
            if path.is_file():
                actual.add(path.relative_to(repo).as_posix())
    except OSError:
        return False, 0, len(actual)

    if (
        manifest is None
        or set(manifest) != {"schema", "profile", "assets"}
        or type(manifest.get("schema")) is not int
        or manifest.get("schema") != 1
    ):
        return False, 0, len(actual)
    profile = manifest.get("profile")
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        return False, 0, len(actual)
    if profile == "asset-free":
        return not assets and not actual, len(assets), len(actual)
    if profile != "redistributable" or not assets:
        return False, len(assets), len(actual)
    declared: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict):
            return False, len(assets), len(actual)
        relative = _manifest_path(asset.get("path"))
        expected = asset.get("sha256")
        if (
            relative is None
            or not relative.parts
            or relative.parts[0] != "assets"
            or relative.as_posix() in declared
            or not isinstance(expected, str)
            or not SHA256_RE.fullmatch(expected)
            or not _https_url(asset.get("source"))
            or not _resolved_text(asset.get("author"))
            or not _spdx_expression(asset.get("license"))
            or asset.get("redistribution") is not True
        ):
            return False, len(assets), len(actual)
        declared.add(relative.as_posix())
        try:
            if sha256_regular(_repository_file(repo, relative)) != expected:
                return False, len(assets), len(actual)
        except (AuditError, OSError):
            return False, len(assets), len(actual)
    return declared == actual, len(assets), len(actual)


def audit_repository_evidence(repo: Path) -> list[Check]:
    states: dict[str, str] = {}
    for relative in REQUIRED_EVIDENCE:
        try:
            path = _repository_file(repo, PurePosixPath(relative))
            data = read_regular(path)
            states[relative] = "present" if data.strip() else "empty"
        except (AuditError, OSError):
            states[relative] = "missing-or-unsafe"

    legal_text_ready = _legal_text_preflight(repo)
    spdx = _repository_json(repo, "SBOM.spdx.json")
    spdx_context = spdx.get("@context") if spdx else None
    spdx_structural, spdx_elements = _spdx_preflight(spdx)
    third_party = _repository_json(repo, "THIRD-PARTY.json")
    third_party_structural, component_count = _third_party_preflight(repo, third_party)
    assets = _repository_json(repo, "components/assets/ASSETS.json")
    assets_structural, asset_count, asset_file_count = _asset_preflight(repo, assets)
    asset_profile = assets.get("profile") if assets else None
    return [
        check(
            "release-evidence-files",
            all(value == "present" for value in states.values()),
            "root license, notice, third-party, SPDX and asset evidence files are present",
            files=states,
        ),
        check(
            "project-license-notice-ready",
            legal_text_ready,
            "project license and notice are bounded UTF-8 legal text without unresolved placeholders",
            bounded_semantic_preflight=True,
        ),
        check(
            "spdx-3.0.1-jsonld-structure",
            spdx_structural,
            "SBOM declares the official SPDX 3.0.1 JSON-LD context and non-empty elements",
            context=spdx_context if isinstance(spdx_context, str) else None,
            element_count=spdx_elements,
            structural_check_only=True,
        ),
        check(
            "third-party-source-structure",
            third_party_structural,
            "every third-party component pins identity, source, license, "
            "redistribution permission and content-hashed evidence",
            component_count=component_count,
            bounded_semantic_preflight=True,
        ),
        check(
            "asset-redistribution-structure",
            assets_structural,
            "the asset set is explicitly empty, or every repository asset is "
            "covered exactly once with matching hash, source, author, license "
            "and redistribution permission",
            manifest_profile=asset_profile if isinstance(asset_profile, str) else None,
            manifest_asset_count=asset_count,
            repository_asset_count=asset_file_count,
            bounded_semantic_preflight=True,
        ),
    ]


def _privacy_directory_category(name: str) -> str | None:
    lowered = name.lower()
    return {
        ".ssh": "ssh-state",
        ".codex": "codex-account-state",
        ".steam": "steam-account-state",
        "steam": "steam-account-state",
        "wireguard": "vpn-state",
        ".cache": "user-cache-state",
        "roms": "rom-content",
        "saves": "game-save-state",
        "states": "game-save-state",
        "telegram desktop": "messaging-account-state",
    }.get(lowered)


def _privacy_file_category(name: str, parent_parts: tuple[str, ...]) -> str | None:
    lowered = name.lower()
    parents = {part.lower() for part in parent_parts}
    if lowered.startswith("id_") or lowered in {"authorized_keys", "known_hosts"}:
        return "ssh-state"
    if lowered in {"auth.json", "credentials.json"} and ".codex" in parents:
        return "codex-account-state"
    if lowered in {"cookies", "cookies-journal", "login data", "web data", "history"}:
        return "browser-profile-state"
    if lowered == "local state" and "chromium" in parents:
        return "browser-profile-state"
    if lowered == "loginusers.vdf" or lowered.startswith("ssfn"):
        return "steam-account-state"
    if lowered.startswith("wg") and lowered.endswith(".conf"):
        return "vpn-state"
    if Path(lowered).suffix in {".ovpn", ".pem", ".p12", ".pfx", ".jks"}:
        return "credential-file"
    if Path(lowered).suffix in {
        ".nes", ".sfc", ".smc", ".gba", ".gb", ".gbc", ".nds", ".3ds",
        ".n64", ".z64", ".v64", ".iso", ".chd", ".cue", ".gdi", ".rvz",
    }:
        return "rom-content"
    return None


def audit_private_state(root: Path, max_entries: int) -> Check:
    categories: set[str] = set()
    entries = 0
    limited = False
    homes: list[Path] = []
    home_root = root / "home"
    if home_root.is_symlink() or (home_root.exists() and not home_root.is_dir()):
        categories.add("unsafe-home-layout")
    elif home_root.is_dir():
        try:
            for path in home_root.iterdir():
                if path.is_symlink():
                    categories.add("unsafe-home-symlink")
                elif path.is_dir():
                    homes.append(path)
        except OSError:
            categories.add("home-scan-error")
    root_home = root / "root"
    if root_home.is_symlink() or (root_home.exists() and not root_home.is_dir()):
        categories.add("unsafe-home-layout")
    elif root_home.is_dir():
        homes.append(root_home)

    def record_walk_error(_error: OSError) -> None:
        categories.add("home-scan-error")

    for home in homes:
        for current, directories, files in os.walk(
            home,
            followlinks=False,
            onerror=record_walk_error,
        ):
            current_path = Path(current)
            relative = current_path.relative_to(home)
            entries += len(directories) + len(files)
            if entries > max_entries:
                limited = True
                break
            kept: list[str] = []
            for directory in directories:
                if (current_path / directory).is_symlink():
                    categories.add("unsafe-home-symlink")
                    continue
                category = _privacy_directory_category(directory)
                if category:
                    categories.add(category)
                    continue
                if directory == ".git":
                    categories.add("development-checkout")
                    continue
                if directory in {"node_modules", "__pycache__", "build", "test-results"}:
                    continue
                if directory.lower() == "chromium" and ".config" in relative.parts:
                    categories.add("browser-profile-state")
                    continue
                kept.append(directory)
            directories[:] = kept
            for filename in files:
                category = _privacy_file_category(filename, relative.parts)
                if category:
                    categories.add(category)
        if limited:
            break
    if limited:
        categories.add("scan-limit-exceeded")
    return check(
        "no-private-user-state",
        not categories,
        "mounted release homes contain no detected credentials, account/browser/game state or development checkout",
        scanned_entries=min(entries, max_entries + 1),
        max_entries=max_entries,
        finding_categories=sorted(categories),
        paths_disclosed=False,
    )


def _directory_has_entries(path: Path) -> bool:
    if path.is_symlink():
        return True
    if not path.exists():
        return False
    if not path.is_dir():
        return True
    try:
        with os.scandir(path) as entries:
            return next(entries, None) is not None
    except OSError:
        return True


def audit_private_system_state(root: Path) -> Check:
    """Detect cloned machine identity and secrets without disclosing their paths."""
    categories: set[str] = set()
    directory_categories = (
        ("etc/wireguard", "vpn-state"),
        ("etc/NetworkManager/system-connections", "network-connection-secrets"),
        ("var/lib/tailscale", "vpn-state"),
    )
    for relative, category in directory_categories:
        if _directory_has_entries(root / PurePosixPath(relative)):
            categories.add(category)

    ssh_directory = root / "etc/ssh"
    if ssh_directory.is_symlink():
        categories.add("ssh-host-identity")
    elif ssh_directory.is_dir():
        try:
            for entry in ssh_directory.iterdir():
                if (
                    entry.name.startswith("ssh_host_")
                    and entry.name.endswith("_key")
                ):
                    categories.add("ssh-host-identity")
                    break
        except OSError:
            categories.add("ssh-host-identity")

    machine_id = root / "etc/machine-id"
    if machine_id.exists() or machine_id.is_symlink():
        try:
            value = read_regular(machine_id, allow_empty=True).strip()
            if value not in {b"", b"uninitialized"}:
                categories.add("machine-identity")
        except (AuditError, OSError):
            categories.add("machine-identity")

    random_seed = root / "var/lib/systemd/random-seed"
    if random_seed.exists() or random_seed.is_symlink():
        try:
            if read_regular(random_seed, allow_empty=True):
                categories.add("boot-random-seed")
        except (AuditError, OSError):
            categories.add("boot-random-seed")

    return check(
        "no-private-system-state",
        not categories,
        "mounted release root contains no detected network secrets or cloned machine identity",
        finding_categories=sorted(categories),
        paths_disclosed=False,
    )


def load_chromium_verifier(repo: Path):
    source = repo / "scripts/pocketds-chromium-runtime-verify.py"
    spec = importlib.util.spec_from_file_location("pds020_chromium_verify", source)
    if spec is None or spec.loader is None:
        raise AuditError("cannot load Chromium verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def audit_chromium(root: Path, repo: Path) -> Check:
    lock = _json_object(repo / "components/chromium/runtime-lock.json")
    if lock is None:
        return check(
            "chromium-runtime-provenance",
            False,
            "Chromium runtime lock is missing or invalid",
            integrity="unknown",
            provenance="unknown",
        )
    package = lock.get("package")
    basename = package.get("root_basename") if isinstance(package, dict) else None
    if not isinstance(basename, str) or not basename:
        return check(
            "chromium-runtime-provenance",
            False,
            "Chromium runtime lock lacks a root basename",
            integrity="unknown",
            provenance="unknown",
        )
    try:
        verifier = load_chromium_verifier(repo)
        report = verifier.verify_runtime(
            lock,
            root / "opt" / basename,
            root / "usr/local/bin/pocketds-chromium-v4l2",
        )
    except (AuditError, ImportError, OSError, ValueError) as exc:
        return check(
            "chromium-runtime-provenance",
            False,
            "Chromium verifier rejected the mounted runtime or lock",
            integrity="unknown",
            provenance="unknown",
            error_type=type(exc).__name__,
        )
    return check(
        "chromium-runtime-provenance",
        report.get("release_ready") is True,
        "Chromium runtime integrity and every source-package receipt are release-ready",
        integrity=report.get("integrity"),
        provenance=report.get("provenance"),
        release_ready=report.get("release_ready"),
        error_count=len(report.get("errors", [])),
        provenance_gap_count=len(report.get("provenance_gaps", [])),
    )


def resolve_target(path: Path, confirmation: str | None) -> tuple[Path, str]:
    if path.is_symlink() or not path.is_dir():
        raise AuditError("release root must be a real directory")
    resolved = path.resolve()
    live = Path("/").stat()
    target = resolved.stat()
    is_live = (live.st_dev, live.st_ino) == (target.st_dev, target.st_ino)
    if is_live and confirmation != LIVE_CONFIRMATION:
        raise AuditError(
            f"live root requires --allow-live-root {LIVE_CONFIRMATION}"
        )
    if not (resolved / "etc/os-release").is_file() or not (resolved / "usr").is_dir():
        raise AuditError("target does not look like a Linux root")
    return resolved, "live-development-root" if is_live else "offline-mounted-root"


def run_audit(root: Path, repo: Path, max_entries: int) -> dict[str, object]:
    checks = [
        *audit_sudoers(root, repo),
        *audit_repository_evidence(repo),
        audit_chromium(root, repo),
        audit_private_state(root, max_entries),
        audit_private_system_state(root),
    ]
    blockers = [item.check_id for item in checks if item.status != "pass"]
    return {
        "schema": SCHEMA_VERSION,
        "read_only": True,
        "standard": {
            "sbom": "SPDX-3.0.1-JSON-LD",
            "sbom_validation": "structural-preflight-only",
        },
        "checks": [asdict(item) for item in checks],
        "blockers": blockers,
        "release_ready": not blockers,
        "privacy": {"paths_disclosed": False},
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument("--allow-live-root")
    parser.add_argument("--max-home-entries", type=int, default=200_000)
    args = parser.parse_args(argv)
    if args.allow_live_root not in {None, LIVE_CONFIRMATION}:
        parser.error(f"--allow-live-root must equal {LIVE_CONFIRMATION}")
    if not 1_000 <= args.max_home_entries <= 2_000_000:
        parser.error("--max-home-entries must be between 1000 and 2000000")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        repo = args.repo.resolve(strict=True)
        if not (repo / ".git").exists():
            raise AuditError("--repo is not a repository root")
        root, root_mode = resolve_target(args.root, args.allow_live_root)
        report = run_audit(root, repo, args.max_home_entries)
        report["root_mode"] = root_mode
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if report["release_ready"] else 1
    except (AuditError, OSError) as exc:
        print(f"pds020-release-root-audit: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

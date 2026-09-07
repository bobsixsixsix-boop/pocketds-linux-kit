#!/usr/bin/env python3
"""Validate an SPDX 3.0.1 JSON-LD SBOM against pinned offline materials."""

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
from typing import Any, Callable, Sequence


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = ROOT / "packaging/image/spdx-validation-lock.json"
MAX_LOCK_BYTES = 64 * 1024
MAX_SBOM_BYTES = 64 * 1024 * 1024
MAX_CONTEXT_BYTES = 128 * 1024
MAX_SCHEMA_BYTES = 1024 * 1024
MAX_MODEL_BYTES = 1024 * 1024
MAX_TOOL_OUTPUT = 64 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SPECIFICATION = {
    "version": "3.0.1",
    "documentation": "https://spdx.github.io/spdx-spec/v3.0.1/serializations/",
    "context_iri": "https://spdx.org/rdf/3.0.1/spdx-context.jsonld",
    "model_commit": "a745f63e8643d5ae0f0851fcfa6836085308f80b",
    "spec_commit": "61a649da8ca27924ac1ca8d2a061cb228839b24c",
}
ARTIFACT_URLS = {
    "context": "https://spdx.org/rdf/3.0.1/spdx-context.jsonld",
    "json_schema": "https://spdx.org/schema/3.0.1/spdx-json-schema.json",
    "semantic_model": "https://spdx.org/rdf/3.0.1/spdx-model.ttl",
}
PACKAGE_NAMES = {
    "python": "python3-libs",
    "jsonschema": "python3-jsonschema",
    "rdflib": "python3-rdflib",
    "pyshacl": "python3-pyshacl",
}
POLICY = {
    "json_schema_required": True,
    "owl_shacl_required": True,
    "network_allowed": False,
    "legal_conclusion": "NOT_DETERMINED",
    "release_ready": False,
}
OFFICIAL_SCHEMA_IDENTICAL_DUPLICATES = {
    "extension": 1,
    "prop_Element_extension": 1,
}


class SpdxValidationError(RuntimeError):
    """The SPDX evidence or validation result failed closed."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SpdxValidationError("JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise SpdxValidationError(f"JSON contains non-finite number {value}")


def strict_json(data: bytes, label: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SpdxValidationError(f"{label} is not strict UTF-8 JSON") from exc


def official_schema_json(data: bytes) -> Any:
    duplicate_counts = {key: 0 for key in OFFICIAL_SCHEMA_IDENTICAL_DUPLICATES}

    def schema_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                if (
                    key not in OFFICIAL_SCHEMA_IDENTICAL_DUPLICATES
                    or result[key] != value
                ):
                    raise SpdxValidationError(
                        "official JSON Schema has an unknown or unequal duplicate key"
                    )
                duplicate_counts[key] += 1
            result[key] = value
        return result

    try:
        result = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=schema_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SpdxValidationError("official JSON Schema is not UTF-8 JSON") from exc
    if duplicate_counts != OFFICIAL_SCHEMA_IDENTICAL_DUPLICATES:
        raise SpdxValidationError("official JSON Schema duplicate-key contract differs")
    return result


def exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise SpdxValidationError(f"{label} fields differ")
    return value


def read_regular(path: Path, maximum: int, label: str) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SpdxValidationError(f"{label} is missing or unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= maximum
        ):
            raise SpdxValidationError(f"{label} metadata is unsafe")
        remaining = metadata.st_size
        data = bytearray()
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise SpdxValidationError(f"{label} changed while reading")
            data.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise SpdxValidationError(f"{label} grew while reading")
        return bytes(data)
    finally:
        os.close(descriptor)


def load_lock(path: Path) -> dict[str, Any]:
    lock = exact_object(
        strict_json(read_regular(path, MAX_LOCK_BYTES, "validation lock"), "validation lock"),
        {"schema", "specification", "artifacts", "validator", "policy"},
        "validation lock",
    )
    if type(lock["schema"]) is not int or lock["schema"] != 1:
        raise SpdxValidationError("validation lock schema differs")
    specification = exact_object(
        lock["specification"], set(SPECIFICATION), "specification"
    )
    if specification != SPECIFICATION or any(
        COMMIT_RE.fullmatch(specification[name]) is None
        for name in ("model_commit", "spec_commit")
    ):
        raise SpdxValidationError("SPDX specification identity differs")
    artifacts = exact_object(lock["artifacts"], set(ARTIFACT_URLS), "artifacts")
    for name, expected_url in ARTIFACT_URLS.items():
        record = exact_object(
            artifacts[name], {"filename", "url", "size", "sha256"}, f"{name} artifact"
        )
        if (
            not isinstance(record["filename"], str)
            or Path(record["filename"]).name != record["filename"]
            or record["url"] != expected_url
            or type(record["size"]) is not int
            or record["size"] <= 0
            or not isinstance(record["sha256"], str)
            or SHA256_RE.fullmatch(record["sha256"]) is None
        ):
            raise SpdxValidationError(f"{name} artifact identity differs")
    validator = exact_object(lock["validator"], set(PACKAGE_NAMES), "validator")
    for key, package in PACKAGE_NAMES.items():
        if (
            not isinstance(validator[key], str)
            or not validator[key].startswith(f"{package}-")
            or any(character.isspace() for character in validator[key])
        ):
            raise SpdxValidationError("validator package identity differs")
    if exact_object(lock["policy"], set(POLICY), "policy") != POLICY:
        raise SpdxValidationError("SPDX validation policy differs")
    return lock


def verify_bound_artifact(
    path: Path, record: dict[str, Any], maximum: int, label: str
) -> bytes:
    data = read_regular(path, maximum, label)
    if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise SpdxValidationError(f"{label} differs from its official lock")
    return data


def safe_executable(path: Path) -> None:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise SpdxValidationError("RPM query executable is missing") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or not os.access(path, os.X_OK)
        or metadata.st_mode & 0o022
    ):
        raise SpdxValidationError("RPM query executable is unsafe")


def verify_validator_packages(rpm: Path, expected: dict[str, str]) -> dict[str, str]:
    safe_executable(rpm)
    observed: dict[str, str] = {}
    for key, package in PACKAGE_NAMES.items():
        try:
            result = subprocess.run(
                [
                    str(rpm),
                    "-q",
                    "--qf",
                    "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\\n",
                    package,
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SpdxValidationError("validator package query failed") from exc
        if (
            result.returncode != 0
            or len(result.stdout) > MAX_TOOL_OUTPUT
            or len(result.stderr) > MAX_TOOL_OUTPUT
        ):
            raise SpdxValidationError("validator package query failed")
        try:
            identity = result.stdout.decode("utf-8", errors="strict").strip()
        except UnicodeError as exc:
            raise SpdxValidationError("validator package identity is not UTF-8") from exc
        if identity != expected[key]:
            raise SpdxValidationError("validator package identity differs")
        observed[key] = identity
    return observed


def context_count(value: Any) -> int:
    if isinstance(value, dict):
        return sum(key == "@context" for key in value) + sum(
            context_count(item) for item in value.values()
        )
    if isinstance(value, list):
        return sum(context_count(item) for item in value)
    return 0


def validate_document(
    document: Any,
    context: Any,
    schema: Any,
    model_text: str,
    jsonschema_module: Any,
    graph_type: Any,
    shacl_validate: Callable[..., tuple[Any, Any, Any]],
) -> dict[str, int]:
    if not isinstance(document, dict) or document.get("@context") != SPECIFICATION[
        "context_iri"
    ]:
        raise SpdxValidationError("SBOM does not use the exact SPDX 3.0.1 context")
    if context_count(document) != 1:
        raise SpdxValidationError("SBOM contains a nested or duplicate JSON-LD context")
    context = exact_object(context, {"@context"}, "official context")
    if not isinstance(context["@context"], dict):
        raise SpdxValidationError("official context body differs")
    if not isinstance(schema, dict):
        raise SpdxValidationError("official JSON Schema body differs")
    try:
        validator_type = jsonschema_module.Draft202012Validator
        validator_type.check_schema(schema)
        errors = list(validator_type(schema).iter_errors(document))
    except Exception as exc:
        raise SpdxValidationError("official JSON Schema validation failed") from exc
    if errors:
        raise SpdxValidationError("SBOM fails official JSON Schema validation")
    local_document = dict(document)
    local_document["@context"] = context["@context"]
    try:
        data_graph = graph_type().parse(
            data=json.dumps(local_document, ensure_ascii=False), format="json-ld"
        )
        shapes_graph = graph_type().parse(data=model_text, format="turtle")
        conforms, results_graph, _results_text = shacl_validate(
            data_graph=data_graph,
            shacl_graph=shapes_graph,
            ont_graph=shapes_graph,
            inference="none",
            abort_on_first=False,
            allow_infos=False,
            allow_warnings=False,
            advanced=False,
            meta_shacl=False,
            do_owl_imports=False,
        )
    except Exception as exc:
        raise SpdxValidationError("offline JSON-LD/OWL/SHACL validation failed") from exc
    if conforms is not True:
        raise SpdxValidationError("SBOM fails official OWL/SHACL validation")
    return {
        "rdf_triple_count": len(data_graph),
        "semantic_result_triple_count": len(results_graph),
    }


def write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short SPDX report write")
        view = view[written:]


def write_report(path: str, report: dict[str, Any]) -> None:
    data = (json.dumps(report, sort_keys=True) + "\n").encode()
    if path == "-":
        sys.stdout.buffer.write(data)
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        write_all(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--json-schema", type=Path, required=True)
    parser.add_argument("--semantic-model", type=Path, required=True)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--rpm", type=Path, default=Path("/usr/bin/rpm"))
    parser.add_argument("--output", default="-")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        lock = load_lock(args.lock)
        materials = {
            "context": verify_bound_artifact(
                args.context, lock["artifacts"]["context"], MAX_CONTEXT_BYTES, "context"
            ),
            "json_schema": verify_bound_artifact(
                args.json_schema,
                lock["artifacts"]["json_schema"],
                MAX_SCHEMA_BYTES,
                "JSON Schema",
            ),
            "semantic_model": verify_bound_artifact(
                args.semantic_model,
                lock["artifacts"]["semantic_model"],
                MAX_MODEL_BYTES,
                "semantic model",
            ),
        }
        sbom_data = read_regular(args.sbom, MAX_SBOM_BYTES, "SBOM")
        document = strict_json(sbom_data, "SBOM")
        context = strict_json(materials["context"], "official context")
        schema = official_schema_json(materials["json_schema"])
        try:
            model_text = materials["semantic_model"].decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise SpdxValidationError("official semantic model is not UTF-8") from exc
        packages = verify_validator_packages(args.rpm, lock["validator"])
        try:
            import jsonschema  # type: ignore[import-not-found]
            from pyshacl import validate as shacl_validate  # type: ignore[import-not-found]
            from rdflib import Graph  # type: ignore[import-not-found]
        except ImportError as exc:
            raise SpdxValidationError("locked SPDX validator modules are unavailable") from exc
        result = validate_document(
            document,
            context,
            schema,
            model_text,
            jsonschema,
            Graph,
            shacl_validate,
        )
        report = {
            "schema": 1,
            "verified": True,
            "offline": True,
            "spec_version": "3.0.1",
            "sbom_sha256": hashlib.sha256(sbom_data).hexdigest(),
            "official_artifacts": {
                name: record["sha256"] for name, record in lock["artifacts"].items()
            },
            "validator_packages": packages,
            "json_schema_valid": True,
            "owl_shacl_valid": True,
            **result,
            "legal_conclusion": "NOT_DETERMINED",
            "rights_or_redistribution_inferred": False,
            "release_ready": False,
        }
        write_report(args.output, report)
    except (
        OSError,
        UnicodeError,
        ValueError,
        SpdxValidationError,
    ) as exc:
        print(f"SPDX offline validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

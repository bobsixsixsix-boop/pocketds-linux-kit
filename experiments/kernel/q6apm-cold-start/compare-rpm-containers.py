#!/usr/bin/env python3
"""Explain RPM container differences without treating them as payload differences.

Run this in the pinned Fedora build environment, where the rpm Python module is
available.  It never installs a package or writes outside the requested report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from typing import Any

try:
    import rpm
except ImportError as error:  # pragma: no cover - host-side diagnostic
    raise SystemExit("compare-rpm-containers: the rpm Python module is required") from error


RPM_LEAD_SIZE = 96
HEADER_MAGIC = bytes.fromhex("8eade8")
EXPECTED_DECODED_DIFFERENCES = {
    261: "SIGMD5",
    269: "SHA1HEADER",
    273: "SHA256HEADER",
    1006: "BUILDTIME",
}
EXPECTED_RAW_SIGNATURE_DIFFERENCES = {269, 273, 1004}
EXPECTED_RAW_MAIN_DIFFERENCES = {1006}


class CompareError(RuntimeError):
    pass


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"type": "bytes", "hex": value.hex()}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {"type": type(value).__name__, "repr": repr(value)}


def difference_count(first: bytes, second: bytes) -> int:
    if len(first) != len(second):
        raise CompareError("cannot count differences across unequal raw sections")
    return sum(left != right for left, right in zip(first, second, strict=True))


def parse_header(content: bytes, offset: int) -> dict[str, Any]:
    if content[offset : offset + 3] != HEADER_MAGIC:
        raise CompareError(f"RPM header magic missing at offset {offset}")
    if content[offset + 3] != 1:
        raise CompareError("unsupported RPM header version")
    index_count, store_size = struct.unpack_from(">II", content, offset + 8)
    index_offset = offset + 16
    data_offset = index_offset + 16 * index_count
    end = data_offset + store_size
    if end > len(content):
        raise CompareError("RPM header extends beyond the artifact")
    indexes = []
    for number in range(index_count):
        tag, data_type, data_relative, count = struct.unpack_from(
            ">IIII", content, index_offset + 16 * number
        )
        indexes.append(
            {
                "tag": tag,
                "type": data_type,
                "offset": data_relative,
                "count": count,
            }
        )
    return {
        "offset": offset,
        "index_count": index_count,
        "store_size": store_size,
        "index_offset": index_offset,
        "data_offset": data_offset,
        "end": end,
        "indexes": indexes,
    }


def tag_span(content: bytes, header: dict[str, Any], entry: dict[str, int]) -> tuple[int, int]:
    start = header["data_offset"] + entry["offset"]
    data_type = entry["type"]
    count = entry["count"]
    if data_type in (1, 2, 7):
        end = start + count
    elif data_type == 3:
        end = start + count * 2
    elif data_type == 4:
        end = start + count * 4
    elif data_type == 5:
        end = start + count * 8
    elif data_type in (6, 8, 9):
        end = start
        for _ in range(count):
            terminator = content.find(b"\0", end, header["end"])
            if terminator < 0:
                raise CompareError("unterminated RPM header string")
            end = terminator + 1
    elif data_type == 0:
        end = start
    else:
        raise CompareError(f"unsupported RPM header type {data_type}")
    if end > header["end"]:
        raise CompareError("RPM tag data extends beyond the header store")
    return start, end


def differing_tags(
    first: bytes,
    second: bytes,
    first_header: dict[str, Any],
    second_header: dict[str, Any],
) -> tuple[list[int], int]:
    if first_header["indexes"] != second_header["indexes"]:
        raise CompareError("RPM header index tables differ")
    first_section = first[first_header["offset"] : first_header["end"]]
    second_section = second[second_header["offset"] : second_header["end"]]
    changed = [
        first_header["offset"] + relative
        for relative, (left, right) in enumerate(
            zip(first_section, second_section, strict=True)
        )
        if left != right
    ]
    tags: set[int] = set()
    covered: set[int] = set()
    for entry in first_header["indexes"]:
        start, end = tag_span(first, first_header, entry)
        matches = {position for position in changed if start <= position < end}
        if matches:
            tags.add(entry["tag"])
            covered.update(matches)
    if covered != set(changed):
        raise CompareError("raw header differences occur outside decoded tag data")
    return sorted(tags), len(changed)


def read_rpm_header(path: Path) -> Any:
    transaction = rpm.TransactionSet()
    transaction.setVSFlags(rpm._RPMVSF_NOSIGNATURES | rpm._RPMVSF_NODIGESTS)
    descriptor = os.open(path, os.O_RDONLY)
    try:
        return transaction.hdrFromFdno(descriptor)
    finally:
        os.close(descriptor)


def decoded_header_record(header: Any, excluded: set[int] | None = None) -> dict[str, Any]:
    skip = excluded or set()
    return {
        str(tag): {
            "name": rpm.tagnames.get(tag, str(tag)),
            "value": canonical(header[tag]),
        }
        for tag in sorted(header.keys())
        if tag not in skip
    }


def verify_rpm(path: Path) -> list[str]:
    result = subprocess.run(
        ["/usr/bin/rpm", "-Kv", str(path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        timeout=60,
    )
    lines = [line.strip() for line in result.stdout.splitlines()[1:] if line.strip()]
    if result.returncode or result.stderr:
        raise CompareError(f"rpm -Kv failed for {path.name}")
    required = {"Header SHA256 digest: OK", "Payload SHA256 digest: OK"}
    if not required.issubset(lines):
        raise CompareError(f"rpm -Kv did not validate both digests for {path.name}")
    return lines


def header_value(header: Any, name: str) -> Any:
    try:
        return header[name]
    except (KeyError, ValueError) as error:
        raise CompareError(f"RPM header tag is unavailable: {name}") from error


def single_header_value(header: Any, name: str) -> Any:
    value = header_value(header, name)
    if isinstance(value, (list, tuple)):
        if len(value) != 1:
            raise CompareError(f"RPM header tag does not have one value: {name}")
        return value[0]
    return value


def raw_artifact(path: Path) -> tuple[bytes, dict[str, Any]]:
    content = path.read_bytes()
    return content, {"filename": path.name, "size": len(content), "sha256": sha256(content)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-a", type=Path, required=True)
    parser.add_argument("--candidate-b", type=Path, required=True)
    args = parser.parse_args()
    try:
        first, first_artifact = raw_artifact(args.candidate_a)
        second, second_artifact = raw_artifact(args.candidate_b)
        if len(first) != len(second):
            raise CompareError("RPM containers have different sizes")

        first_signature = parse_header(first, RPM_LEAD_SIZE)
        second_signature = parse_header(second, RPM_LEAD_SIZE)
        first_main_offset = (first_signature["end"] + 7) & ~7
        second_main_offset = (second_signature["end"] + 7) & ~7
        first_main = parse_header(first, first_main_offset)
        second_main = parse_header(second, second_main_offset)
        if first_main_offset != second_main_offset:
            raise CompareError("RPM main-header offsets differ")
        if first_signature["indexes"] != second_signature["indexes"]:
            raise CompareError("RPM signature header layouts differ")
        if first_main["indexes"] != second_main["indexes"]:
            raise CompareError("RPM main header layouts differ")

        signature_tags, signature_difference_count = differing_tags(
            first, second, first_signature, second_signature
        )
        main_tags, main_difference_count = differing_tags(first, second, first_main, second_main)
        if set(signature_tags) != EXPECTED_RAW_SIGNATURE_DIFFERENCES:
            raise CompareError("unexpected signature-header differences")
        if set(main_tags) != EXPECTED_RAW_MAIN_DIFFERENCES:
            raise CompareError("unexpected main-header differences")

        first_header = read_rpm_header(args.candidate_a)
        second_header = read_rpm_header(args.candidate_b)
        if set(first_header.keys()) != set(second_header.keys()):
            raise CompareError("decoded RPM header key sets differ")
        decoded_differences = {
            tag: {
                "name": rpm.tagnames.get(tag, str(tag)),
                "candidate_a": canonical(first_header[tag]),
                "candidate_b": canonical(second_header[tag]),
            }
            for tag in sorted(first_header.keys())
            if first_header[tag] != second_header[tag]
        }
        if set(decoded_differences) != set(EXPECTED_DECODED_DIFFERENCES):
            raise CompareError("unexpected decoded RPM header differences")

        excluded = set(EXPECTED_DECODED_DIFFERENCES)
        normalized_first = json.dumps(
            decoded_header_record(first_header, excluded),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        normalized_second = json.dumps(
            decoded_header_record(second_header, excluded),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if normalized_first != normalized_second:
            raise CompareError("non-volatile decoded RPM headers differ")

        first_main_normalized = bytearray(first[first_main["offset"] : first_main["end"]])
        second_main_normalized = bytearray(second[second_main["offset"] : second_main["end"]])
        buildtime_entries = [entry for entry in first_main["indexes"] if entry["tag"] == 1006]
        if len(buildtime_entries) != 1 or buildtime_entries[0]["type"] != 4 or buildtime_entries[0]["count"] != 1:
            raise CompareError("unexpected BUILDTIME representation")
        start, end = tag_span(first, first_main, buildtime_entries[0])
        relative_start = start - first_main["offset"]
        relative_end = end - first_main["offset"]
        first_main_normalized[relative_start:relative_end] = b"\0" * (relative_end - relative_start)
        second_main_normalized[relative_start:relative_end] = b"\0" * (relative_end - relative_start)
        if first_main_normalized != second_main_normalized:
            raise CompareError("main headers differ after normalizing BUILDTIME")

        first_payload = first[first_main["end"] :]
        second_payload = second[second_main["end"] :]
        if first_payload != second_payload:
            raise CompareError("runtime RPM payload bytes differ")
        payload_digest = sha256(first_payload)
        if single_header_value(first_header, "PAYLOADSHA256") != payload_digest:
            raise CompareError("candidate A payload digest tag does not bind the payload")
        if single_header_value(second_header, "PAYLOADSHA256") != payload_digest:
            raise CompareError("candidate B payload digest tag does not bind the payload")

        payload_metadata_names = (
            "PAYLOADFORMAT",
            "PAYLOADCOMPRESSOR",
            "PAYLOADFLAGS",
            "PAYLOADSHA256",
            "FILEDIGESTALGO",
        )
        first_payload_metadata = {
            name.lower(): canonical(
                single_header_value(first_header, name)
                if name == "PAYLOADSHA256"
                else header_value(first_header, name)
            )
            for name in payload_metadata_names
        }
        second_payload_metadata = {
            name.lower(): canonical(
                single_header_value(second_header, name)
                if name == "PAYLOADSHA256"
                else header_value(second_header, name)
            )
            for name in payload_metadata_names
        }
        if first_payload_metadata != second_payload_metadata:
            raise CompareError("RPM payload metadata differs")

        reserve_entry = next(
            (entry for entry in first_signature["indexes"] if entry["tag"] == 1008), None
        )
        if reserve_entry is None:
            raise CompareError("RPM signature reservation is missing")
        reserve_start, reserve_end = tag_span(first, first_signature, reserve_entry)
        first_reservation = first[reserve_start:reserve_end]
        second_reservation = second[reserve_start:reserve_end]
        if first_reservation != second_reservation:
            raise CompareError("RPM signature reservation differs")

        first_buildtime = int(first_header[1006])
        second_buildtime = int(second_header[1006])
        report = {
            "schema": "pocketds.q6apm-rpm-container-comparison.v1",
            "read_only": True,
            "artifacts": {"candidate_a": first_artifact, "candidate_b": second_artifact},
            "rpm_verify": {
                "candidate_a": verify_rpm(args.candidate_a),
                "candidate_b": verify_rpm(args.candidate_b),
            },
            "layout": {
                "lead_size": RPM_LEAD_SIZE,
                "signature_header": {
                    "offset": first_signature["offset"],
                    "index_count": first_signature["index_count"],
                    "store_size": first_signature["store_size"],
                    "end": first_signature["end"],
                },
                "signature_padding_size": first_main_offset - first_signature["end"],
                "main_header": {
                    "offset": first_main["offset"],
                    "index_count": first_main["index_count"],
                    "store_size": first_main["store_size"],
                    "end": first_main["end"],
                },
                "payload_offset": first_main["end"],
            },
            "raw_sections": {
                "lead": {
                    "size": RPM_LEAD_SIZE,
                    "identical": first[:RPM_LEAD_SIZE] == second[:RPM_LEAD_SIZE],
                    "sha256": sha256(first[:RPM_LEAD_SIZE]),
                },
                "signature_header": {
                    "size": first_signature["end"] - first_signature["offset"],
                    "identical": False,
                    "differing_byte_count": signature_difference_count,
                    "differing_raw_tags": signature_tags,
                    "candidate_a_sha256": sha256(first[RPM_LEAD_SIZE:first_signature["end"]]),
                    "candidate_b_sha256": sha256(second[RPM_LEAD_SIZE:second_signature["end"]]),
                },
                "signature_padding": {
                    "size": first_main_offset - first_signature["end"],
                    "identical": first[first_signature["end"]:first_main_offset]
                    == second[second_signature["end"]:second_main_offset],
                },
                "main_header": {
                    "size": first_main["end"] - first_main["offset"],
                    "identical": False,
                    "differing_byte_count": main_difference_count,
                    "differing_raw_tags": main_tags,
                    "candidate_a_sha256": sha256(first[first_main["offset"]:first_main["end"]]),
                    "candidate_b_sha256": sha256(second[second_main["offset"]:second_main["end"]]),
                    "normalized_buildtime_zeroed_sha256": sha256(bytes(first_main_normalized)),
                },
                "payload": {
                    "size": len(first_payload),
                    "identical": True,
                    "sha256": payload_digest,
                },
            },
            "decoded_header": {
                "tag_count": len(first_header.keys()),
                "differences": {str(tag): value for tag, value in decoded_differences.items()},
                "difference_is_only_buildtime_and_derived_digests": True,
                "nonvolatile_normalized_sha256": sha256(normalized_first),
                "buildtime_delta_seconds": second_buildtime - first_buildtime,
            },
            "payload_metadata": first_payload_metadata,
            "signature_reservation": {
                "raw_tag": 1008,
                "size": len(first_reservation),
                "identical": True,
                "all_zero": not any(first_reservation),
                "sha256": sha256(first_reservation),
            },
            "conclusion": {
                "rpm_container_byte_reproducible": False,
                "package_payload_byte_reproducible": True,
                "rpm_header_layout_reproducible": True,
                "rpm_nonvolatile_header_semantics_reproducible": True,
                "only_root_cause": (
                    f"BUILDTIME differs by {second_buildtime - first_buildtime} seconds; "
                    "SIGMD5, SHA1HEADER and SHA256HEADER are derived from that "
                    "main-header difference"
                ),
            },
        }
        if not report["raw_sections"]["lead"]["identical"]:
            raise CompareError("RPM lead differs")
        if not report["raw_sections"]["signature_padding"]["identical"]:
            raise CompareError("RPM signature padding differs")
        json.dump(report, sys.stdout, sort_keys=True, indent=2)
        sys.stdout.write("\n")
        return 0
    except (OSError, UnicodeError, subprocess.SubprocessError, CompareError) as error:
        print(f"compare-rpm-containers: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify two already extracted, independently built q6apm candidate RPMs."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
PAYLOAD_SOURCE = HERE.parents[2] / "tools/kernel-ab/reproducible-build/payload-evidence.py"
SPEC = importlib.util.spec_from_file_location("pocketds_payload_evidence", PAYLOAD_SOURCE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load payload evidence helper")
payload = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = payload
SPEC.loader.exec_module(payload)

RELEASE = "7.1.0-101.20260829000000.pocketds.q6apmcs1.fc44.aarch64"
NEVRA = f"kernel-{RELEASE}"
BASELINE_RELEASE = "7.1.0-100.20260730172655.pocketds.fc44.aarch64"
EXPECTED_SUMMARY = {
    "entry_count": 499,
    "regular_file_count": 341,
    "directory_count": 156,
    "symlink_count": 2,
}
KEY_FILES = {
    "raw_image": (
        f"lib/modules/{RELEASE}/Image",
        34515456,
        "30766024b63cc223bce3c873a8578784c8344bac805f668d875453e5b970994a",
    ),
    "package_boot": (
        f"boot/Image-{RELEASE}",
        17090560,
        "eefdbf93211e6295cfa6c503daa5c7729b32494edcf609d6f3a7402f47eda7c1",
    ),
    "package_dtb": (
        f"boot/dtb-{RELEASE}/qcom/qcs8550-ayaneo-pocketds.dtb",
        135527,
        "0557cee113d4e34fcae274c69400a8a53653e60229a1f333eafde83b2dfe6994",
    ),
    "q6apm_module": (
        f"lib/modules/{RELEASE}/kernel/sound/soc/qcom/qdsp6/snd-q6apm.ko",
        54080,
        "b6c4d102d4752e0621407b4ea11a4931de61b07434a34aa86156268afcc4e4ed",
    ),
    "config": (
        f"boot/config-{RELEASE}",
        234425,
        "2b8dc027552fe691e3700d1c34aa9e161f125fc4393a481a98db938d86453971",
    ),
    "system_map": (
        f"boot/System.map-{RELEASE}",
        5980271,
        "434ea95b3a9afeb84de08753cecf0e9e10403ca6f84cefd346d8594ba2e8b16d",
    ),
}


class VerifyError(RuntimeError):
    pass


def artifact_record(path: Path) -> dict[str, object]:
    content, metadata = payload._read_regular(path, maximum=payload.MAX_ARTIFACT_BYTES)
    return {"filename": path.name, "size": metadata.st_size, "sha256": hashlib.sha256(content).hexdigest()}


def query(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["/usr/bin/rpm", "-qp", *arguments, str(path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        timeout=30,
    )
    if result.returncode or result.stderr:
        raise VerifyError(f"RPM query failed: {path.name}")
    return result.stdout


def inspect_candidate(rpm: Path, root: Path) -> dict[str, object]:
    artifact = artifact_record(rpm)
    records, summary = payload.scan_tree(root)
    for name, expected in EXPECTED_SUMMARY.items():
        if summary[name] != expected:
            raise VerifyError(f"payload {name} differs")
    package_records = payload.read_rpm_manifest(rpm, artifact)
    implicit = payload._bind_package_to_tree(package_records, records)
    if len(package_records) != 496 or implicit != ["boot", "lib", "lib/modules"]:
        raise VerifyError("RPM payload binding differs")
    if query(rpm, "--qf", "%{NEVRA}\\n") != NEVRA + "\n":
        raise VerifyError("candidate NEVRA differs")
    scripts = query(rpm, "--scripts")
    required = (
        "Experimental kernel packages must never select themselves for the ABL.",
        "Do not alter either ABL alias when removing an experimental package.",
    )
    if not all(text in scripts for text in required) or any(text in scripts for text in ("/boot/", "cp -f", "latest=")):
        raise VerifyError("candidate scriptlets can change the active boot alias")
    by_path = {str(record["path"]): record for record in records}
    keys: dict[str, object] = {}
    for name, (path, size, digest) in KEY_FILES.items():
        record = by_path.get(path)
        expected = {"path": path, "type": "file", "mode": 0o644, "size": size, "sha256": digest}
        if record != expected:
            raise VerifyError(f"key payload differs: {name}")
        keys[name] = {"path": path, "size": size, "sha256": digest}
    module_paths = [path for path in by_path if path.endswith(".ko") and f"lib/modules/{RELEASE}/kernel/" in path]
    if len(module_paths) != 319:
        raise VerifyError("module count differs")
    if any(BASELINE_RELEASE in path for path in by_path):
        raise VerifyError("candidate payload overlaps the baseline release")
    modules_root = f"lib/modules/{RELEASE}"
    modules_prefix = modules_root + "/"
    if any(
        path.startswith("lib/modules/")
        and path != modules_root
        and not path.startswith(modules_prefix)
        for path in by_path
    ):
        raise VerifyError("candidate contains a foreign module tree")
    module = root / KEY_FILES["q6apm_module"][0]
    modinfo = subprocess.run(
        ["/usr/sbin/modinfo", "-F", "vermagic", str(module)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin"},
        timeout=30,
    )
    expected_vermagic = f"{RELEASE} SMP preempt mod_unload aarch64\n"
    if modinfo.returncode or modinfo.stdout != expected_vermagic:
        raise VerifyError("q6apm module vermagic differs")
    return {
        "artifact": artifact,
        "records": records,
        "package_records": package_records,
        "summary": summary,
        "key_files": keys,
        "module_count": len(module_paths),
        "implicit_directories": implicit,
        "scriptlets_do_not_select_boot_alias": True,
        "q6apm_vermagic": modinfo.stdout.strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-a-rpm", type=Path, required=True)
    parser.add_argument("--candidate-a-root", type=Path, required=True)
    parser.add_argument("--candidate-b-rpm", type=Path, required=True)
    parser.add_argument("--candidate-b-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        first = inspect_candidate(args.candidate_a_rpm, args.candidate_a_root)
        second = inspect_candidate(args.candidate_b_rpm, args.candidate_b_root)
        if first["records"] != second["records"]:
            raise VerifyError("independent extracted payloads differ")
        if first["package_records"] != second["package_records"]:
            raise VerifyError("independent RPM header manifests differ")
        report = {
            "schema": "pocketds.q6apm-candidate-reproduction.v1",
            "read_only": True,
            "network": False,
            "kernel_release": RELEASE,
            "candidate_a": {key: value for key, value in first.items() if key not in {"records", "package_records"}},
            "candidate_b": {key: value for key, value in second.items() if key not in {"records", "package_records"}},
            "comparison": {
                "rpm_container_byte_identical": all(
                    first["artifact"][field] == second["artifact"][field]
                    for field in ("size", "sha256")
                ),
                "rpm_header_manifests_identical": True,
                "extracted_payloads_identical": True,
                "matched_entry_count": first["summary"]["entry_count"],
            },
            "reproduction_scope": {
                "claim": "runtime_package_payload_byte_reproducible",
                "includes": [
                    "compressed_cpio_payload",
                    "extracted_runtime_tree",
                    "rpm_header_file_manifest",
                ],
                "excludes": ["rpm_container_bytes", "rpm_buildtime_and_derived_digests"],
            },
            "gates": {
                "distinct_kernel_release": RELEASE != BASELINE_RELEASE,
                "baseline_module_tree_preserved_by_path": True,
                "candidate_built_twice": True,
                "candidate_runtime_payload_independently_reproduced": True,
                "boot_alias_scriptlets_disabled": True,
                "deployed": False,
                "runtime_tested": False,
                "runtime_authorized": False,
            },
        }
        json.dump(report, sys.stdout, sort_keys=True, indent=2)
        sys.stdout.write("\n")
        return 0
    except (OSError, subprocess.SubprocessError, UnicodeError, payload.ReproductionError, VerifyError) as error:
        print(f"verify-reproduction: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

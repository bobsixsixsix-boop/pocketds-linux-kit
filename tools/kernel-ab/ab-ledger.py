#!/usr/bin/env python3
"""Create a private unresolved ledger for the locked PDS-002 IFPC matrix."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys


HERE = Path(__file__).resolve().parent
EVALUATE_SOURCE = HERE / "ab-evaluate.py"
SPEC = importlib.util.spec_from_file_location("pocketds_kernel_ab_ledger_evaluate", EVALUATE_SOURCE)
if SPEC is None or SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("cannot load A/B evaluator library")
evaluate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = evaluate
SPEC.loader.exec_module(evaluate)

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def build_ledger(lock: dict[str, object], workloads: dict[str, str]) -> dict[str, object]:
    if set(workloads) != set(lock["profiles"]):
        raise evaluate.EvaluationError("workload profile set differs")
    if any(HEX64.fullmatch(value) is None for value in workloads.values()):
        raise evaluate.EvaluationError("workload identity is not a lowercase SHA-256")
    runs: list[dict[str, object]] = []
    for variant in lock["variants"]:
        for round_number in range(1, lock["minimum_rounds_per_variant"] + 1):
            for profile in lock["profiles"]:
                runs.append(
                    {
                        "variant": variant,
                        "profile": profile,
                        "round": round_number,
                        "report": f"round-{round_number:02d}-{variant}-{profile}.jsonl",
                        "operator": {
                            "black_screen_or_desktop_failed": None,
                            "thermal_power_or_fan_limit_exceeded": None,
                            "local_console_and_ssh_both_unavailable": None,
                            "manual_recovery_required": None,
                        },
                    }
                )
    return {
        "schema": evaluate.LEDGER_SCHEMA,
        "experiment": lock["experiment"],
        "workloads": workloads,
        "runs": runs,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    for profile in ("dual-165-60", "dual-60-60", "upper-only-165"):
        parser.add_argument(f"--workload-{profile}", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        lock, lock_sha256 = evaluate.load_lock()
        workloads = {
            profile: getattr(args, "workload_" + profile.replace("-", "_"))
            for profile in lock["profiles"]
        }
        ledger = build_ledger(lock, workloads)
        evaluate.safe_output(args.output, ledger)
    except (OSError, evaluate.EvaluationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "schema": evaluate.LEDGER_SCHEMA,
                "run_count": len(ledger["runs"]),
                "operator_observations_resolved": False,
                "evaluation_lock_sha256": lock_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

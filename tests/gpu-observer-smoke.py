#!/usr/bin/env python3
"""Smoke-test the read-only GPU observer and its stable JSONL schema."""

from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
OBSERVER = ROOT / "scripts" / "pocketds-gpu-observer.py"

spec = importlib.util.spec_from_file_location("pocketds_gpu_observer", OBSERVER)
assert spec and spec.loader
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


with tempfile.TemporaryDirectory(prefix="pocketds-gpu-observer-") as temp:
    output = Path(temp) / "observation.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            str(OBSERVER),
            "--duration",
            "0.6",
            "--interval",
            "0.2",
            "--rescan-interval",
            "5",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout)
    records = [json.loads(line) for line in output.read_text().splitlines()]

assert records[0]["type"] == "header"
assert records[0]["schema"] == 1
assert records[0]["read_only"] is True
samples = [record for record in records if record["type"] == "sample"]
assert len(samples) >= 2
for sample in samples:
    assert isinstance(sample["client_count"], int)
    assert isinstance(sample["clients"], list)
    assert sample["observed_gpu_percent"] is None or sample["observed_gpu_percent"] >= 0
summary = records[-1]
assert summary["type"] == "summary"
assert summary["sample_count"] == len(samples)
assert summary["observer_cpu_percent"] >= 0

with tempfile.TemporaryDirectory(prefix="pocketds-fdinfo-") as temp:
    proc = Path(temp)
    process = proc / "100"
    (process / "fd").mkdir(parents=True)
    (process / "fdinfo").mkdir()
    (process / "comm").write_text("fixture-client\n", encoding="utf-8")
    for descriptor, client, engine in (("3", 14, 100), ("4", 14, 110), ("5", 15, 50)):
        (process / "fd" / descriptor).symlink_to("/dev/dri/renderD128")
        (process / "fdinfo" / descriptor).write_text(
            "drm-driver:\tmsm\n"
            f"drm-client-id:\t{client}\n"
            f"drm-engine-gpu:\t{engine} ns\n"
            f"drm-cycles-gpu:\t{engine * 2}\n"
            "drm-maxfreq-gpu:\t1000000000 Hz\n",
            encoding="utf-8",
        )
    paths = observer.discover_drm_fdinfo(proc)
    clients = observer.scan_drm_clients(paths)
    assert len(paths) == 2
    assert len(clients) == 2
    assert clients[("msm", 14)]["engine_ns"] == 110
    assert clients[("msm", 14)]["processes"] == {"fixture-client"}
print("  [OK] GPU observer JSONL schema and smoke run")

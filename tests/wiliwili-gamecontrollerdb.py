#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/pocketds-wiliwili-gamecontrollerdb.py"
SOURCE = ROOT / "components/wiliwili/gamecontrollerdb.txt"
spec = importlib.util.spec_from_file_location("wiliwili_gamecontrollerdb", MODULE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

mapping = module.read_source(SOURCE)
assert mapping.startswith(module.POCKETDS_GLFW_GUID + ",")
assert mapping.endswith(",platform:Linux,")

fresh = module.merge_database("", mapping)
assert fresh == mapping + "\n"

other = (
    "# keep this comment\n"
    "03000000aaaaaaaaaaaaaaaaaaaaaaaa,Other Pad,a:b9,platform:Linux,\n"
)
merged = module.merge_database(other, mapping)
assert merged.startswith(other)
assert merged.count(module.POCKETDS_GLFW_GUID) == 1

stale = mapping.replace("a:b0", "a:b9")
duplicated = f"{stale}\n{other}{stale}\n"
replaced = module.merge_database(duplicated, mapping)
assert stale not in replaced
assert replaced.count(mapping) == 1
assert "Other Pad" in replaced

partial = "x" + module.POCKETDS_GLFW_GUID + ",Not Ours,a:b0,platform:Linux,\n"
assert module.merge_database(partial, mapping).startswith(partial)

windows = mapping.replace("InputPlumber Xbox Elite 2", "Windows Twin").replace(
    "platform:Linux", "platform:Windows"
)
cross_platform = module.merge_database(windows + "\n" + stale + "\n", mapping)
assert windows in cross_platform
assert mapping in cross_platform
assert stale not in cross_platform
assert cross_platform.count(module.POCKETDS_GLFW_GUID) == 2

generic_stale = stale.replace("platform:Linux,", "")
linux_then_generic = module.merge_database(stale + "\n" + generic_stale + "\n", mapping)
assert linux_then_generic == mapping + "\n"
assert generic_stale not in linux_then_generic

generic_then_linux = module.merge_database(generic_stale + "\n" + stale + "\n", mapping)
assert generic_then_linux == mapping + "\n"
assert generic_stale not in generic_then_linux

assert module.database_is_current(mapping + "\n", mapping)
assert module.database_is_current(windows + "\n" + mapping + "\n", mapping)
assert not module.database_is_current(stale + "\n", mapping)
assert not module.database_is_current(mapping + "\n" + generic_stale + "\n", mapping)

for bad in (
    mapping.replace(module.POCKETDS_GLFW_GUID, "0" * 32),
    mapping.replace("a:b0,", ""),
    mapping.replace("a:b0,", "a:b9,"),
    mapping.replace("platform:Linux,", "platform:Windows,"),
    mapping.rstrip(","),
):
    try:
        module.validate_mapping(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(f"invalid mapping was accepted: {bad}")

with tempfile.TemporaryDirectory() as raw_tmp:
    tmp = Path(raw_tmp)
    existing = tmp / "existing.txt"
    existing.write_text(other, encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--source",
            str(SOURCE),
            "--existing",
            str(existing),
        ],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert result.stdout == module.merge_database(other, mapping)

    current = tmp / "current.txt"
    current.write_text(windows + "\n" + mapping + "\n", encoding="utf-8")
    subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--source",
            str(SOURCE),
            "--check-existing",
            str(current),
        ],
        check=True,
    )
    current.write_text(stale + "\n", encoding="utf-8")
    rejected = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--source",
            str(SOURCE),
            "--check-existing",
            str(current),
        ],
        check=False,
    )
    assert rejected.returncode == 1

print("  [OK] Wiliwili GLFW mapping is exact, merge-safe and deterministic")

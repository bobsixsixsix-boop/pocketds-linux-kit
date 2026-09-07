#!/usr/bin/env python3
"""Mock locking plus recoverable plan/apply/rollback sidecar transactions."""

import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1]
COMPONENT_FILES = (
    "01-ayaneo-controller-haptics.yaml",
    "30-pocketds-haptics-candidate.conf",
    "org.pocketds.InputPlumber.Haptics.policy",
    "inputplumber-d3932cb4-pocketds-haptics.patch",
    "inputplumber-haptics-source-lock.json",
    "inputplumber-d3932cb4-remove-unused-usb-deck.patch",
)
TARGETS = {
    "candidate": "/usr/local/libexec/pocketds-inputplumber-haptics",
    "profile": "/etc/inputplumber/devices.d/01-ayaneo-controller.yaml",
    "dropin": "/etc/systemd/system/inputplumber.service.d/30-pocketds-haptics-candidate.conf",
    "policy": "/usr/share/polkit-1/actions/org.pocketds.InputPlumber.Haptics.policy",
}


def rooted(root: Path, value: str) -> Path:
    return root / value.lstrip("/")


def run(helper: Path, *args: str, ok: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [sys.executable, str(helper), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if ok:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode == 2, (result.stdout, result.stderr)
    return result


with tempfile.TemporaryDirectory() as temporary:
    fixture = Path(temporary)
    repo = fixture / "repo"
    component = repo / "components" / "inputplumber"
    scripts = repo / "scripts"
    component.mkdir(parents=True)
    scripts.mkdir()
    for name in COMPONENT_FILES:
        shutil.copy2(SOURCE / "components" / "inputplumber" / name, component / name)
    helper = scripts / "pocketds-inputplumber-haptics.py"
    shutil.copy2(SOURCE / "scripts" / helper.name, helper)

    candidate = fixture / "inputplumber-pocketds-pulse"
    candidate.write_bytes(b"\x7fELF-pocketds-mock-candidate\n")
    lock_path = component / "inputplumber-haptics-source-lock.json"
    lock = json.loads(lock_path.read_text())
    lock["binary_size"] = candidate.stat().st_size
    lock["binary_sha256"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    lock_path.write_text(json.dumps(lock, indent=2) + "\n")

    root = fixture / "root"
    distro = rooted(root, "/usr/bin/inputplumber")
    distro.parent.mkdir(parents=True)
    distro.write_bytes(b"distro-inputplumber-0.75.2\n")
    os.chmod(distro, 0o755)
    old_profile = b"old-device-profile\n"
    old_policy = b"old-polkit-policy\n"
    for path, data in (
        (rooted(root, TARGETS["profile"]), old_profile),
        (rooted(root, TARGETS["policy"]), old_policy),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.chmod(path, 0o640)

    before = sorted(
        (str(path.relative_to(root)), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    )
    planned = run(helper, "plan", "--binary", str(candidate), "--root", str(root))
    assert json.loads(planned.stdout)["mutated"] is False
    after_plan = sorted(
        (str(path.relative_to(root)), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    )
    assert after_plan == before

    bad = fixture / "bad-binary"
    bad.write_bytes(b"wrong")
    run(helper, "plan", "--binary", str(bad), "--root", str(root), ok=False)
    run(
        helper,
        "apply",
        "--binary",
        str(candidate),
        "--confirm",
        "wrong",
        "--root",
        str(root),
        ok=False,
    )

    transaction_lock = rooted(
        root,
        "/var/lib/pocketds-linux-kit/inputplumber-haptics-transactions/.transaction.lock",
    )
    transaction_lock.parent.mkdir(parents=True)
    with transaction_lock.open("w") as held_lock:
        fcntl.flock(held_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = run(
            helper,
            "apply",
            "--binary",
            str(candidate),
            "--confirm",
            "APPLY-INPUTPLUMBER-HAPTICS-D3932CB4",
            "--transaction-id",
            "must-not-start",
            "--root",
            str(root),
            ok=False,
        )
        assert "global lock" in locked.stderr

    run(
        helper,
        "apply",
        "--binary",
        str(candidate),
        "--confirm",
        "APPLY-INPUTPLUMBER-HAPTICS-D3932CB4",
        "--transaction-id",
        "mock-001",
        "--root",
        str(root),
    )
    assert distro.read_bytes() == b"distro-inputplumber-0.75.2\n"
    assert rooted(root, TARGETS["candidate"]).read_bytes() == candidate.read_bytes()
    assert stat.S_IMODE(rooted(root, TARGETS["candidate"]).stat().st_mode) == 0o755
    assert rooted(root, TARGETS["profile"]).read_bytes() == (
        component / "01-ayaneo-controller-haptics.yaml"
    ).read_bytes()
    assert rooted(root, TARGETS["dropin"]).is_file()

    blocked = run(
        helper,
        "apply",
        "--binary",
        str(candidate),
        "--confirm",
        "APPLY-INPUTPLUMBER-HAPTICS-D3932CB4",
        "--transaction-id",
        "mock-002",
        "--root",
        str(root),
        ok=False,
    )
    assert "unfinished transaction" in blocked.stderr
    assert not rooted(
        root,
        "/var/lib/pocketds-linux-kit/inputplumber-haptics-transactions/mock-002",
    ).exists()

    with transaction_lock.open("w") as held_lock:
        fcntl.flock(held_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked_rollback = run(
            helper,
            "rollback",
            "--transaction-id",
            "mock-001",
            "--confirm",
            "ROLLBACK-INPUTPLUMBER-HAPTICS-D3932CB4",
            "--root",
            str(root),
            ok=False,
        )
        assert "global lock" in locked_rollback.stderr

    installed_profile = rooted(root, TARGETS["profile"]).read_bytes()
    rooted(root, TARGETS["profile"]).write_bytes(b"operator-change\n")
    run(
        helper,
        "rollback",
        "--transaction-id",
        "mock-001",
        "--confirm",
        "ROLLBACK-INPUTPLUMBER-HAPTICS-D3932CB4",
        "--root",
        str(root),
        ok=False,
    )
    assert rooted(root, TARGETS["candidate"]).is_file()
    rooted(root, TARGETS["profile"]).write_bytes(installed_profile)
    os.chmod(rooted(root, TARGETS["profile"]), 0o644)

    # Simulate SIGKILL between atomic target replacements and the final phase
    # update: some targets are installed and others still equal their preimage.
    manifest_path = rooted(
        root,
        "/var/lib/pocketds-linux-kit/inputplumber-haptics-transactions/mock-001/manifest.json",
    )
    interrupted = json.loads(manifest_path.read_text())
    interrupted["phase"] = "prepared"
    manifest_path.write_text(json.dumps(interrupted, indent=2) + "\n")
    rooted(root, TARGETS["profile"]).write_bytes(old_profile)
    os.chmod(rooted(root, TARGETS["profile"]), 0o640)
    rooted(root, TARGETS["dropin"]).unlink()

    run(
        helper,
        "rollback",
        "--transaction-id",
        "mock-001",
        "--confirm",
        "ROLLBACK-INPUTPLUMBER-HAPTICS-D3932CB4",
        "--root",
        str(root),
    )
    assert distro.read_bytes() == b"distro-inputplumber-0.75.2\n"
    assert rooted(root, TARGETS["profile"]).read_bytes() == old_profile
    assert stat.S_IMODE(rooted(root, TARGETS["profile"]).stat().st_mode) == 0o640
    assert rooted(root, TARGETS["policy"]).read_bytes() == old_policy
    assert not rooted(root, TARGETS["candidate"]).exists()
    assert not rooted(root, TARGETS["dropin"]).exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["phase"] == "rolled-back"

print("  [OK] sidecar lock/apply/conflict/interrupted-recovery mock transaction")

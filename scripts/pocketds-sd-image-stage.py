#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage the Kit into a marked disposable Fedora root, without running it.

This is an image-build operation, not an installer for an existing machine.
All payload and destination checks finish before the first payload is written.
An interrupted build must discard its root and start with a clean composition.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys

MARKER = b"pocketds.sd-image-root.v1\n"
HOME = "/home/pocketds"
USER_UNITS = (
    "pocketds-brightness.service", "pocketds-gamepad-activity.service",
    "pocketds-gpu-telemetry.service", "pocketds-keyboard.service",
    "pocketds-light-standby.service", "pocketds-speaker-enhancement.service",
    "pocketds-touchpad.service", "pocketds-image-desktop.service",
)
SYSTEM_UNITS = (
    "tuned.service", "inputplumber.service", "pocketds-mode-listener.service",
    "pocketds-lid-switch.service", "pocketds-gmu-runtime.service",
    "pocketds-firstboot-password.service", "pocketds-resize-root.service",
)
COMMANDS = (
    "python3", "arecord", "pactl", "parecord", "pw-dump", "wpctl",
    "kdialog", "kwriteconfig6", "kscreen-doctor", "busctl", "sudo",
    "gpioget", "gpiomon", "cracklib-check", "systemctl",
)


class StageError(RuntimeError):
    pass


def regular(path: Path, maximum: int = 16 * 1024 * 1024) -> bytes:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > maximum:
        raise StageError(f"Unsafe or oversized regular file: {path.name}")
    return path.read_bytes()


def target(root: Path, absolute: str) -> Path:
    relative = PurePosixPath(absolute)
    if not relative.is_absolute() or ".." in relative.parts:
        raise StageError("Invalid image destination")
    path = root / absolute.lstrip("/")
    # A destination's final component is never followed. Parent links may only
    # stay in this root (Fedora's relative /bin -> usr/bin layout is allowed).
    if not path.parent.resolve().is_relative_to(root):
        raise StageError(f"Destination parent escapes image: {absolute}")
    if path.is_symlink():
        raise StageError(f"Refusing existing destination link: {absolute}")
    if path.exists() and not path.is_file():
        raise StageError(f"Refusing non-file destination: {absolute}")
    if path.exists() and path.stat().st_nlink != 1:
        raise StageError(f"Refusing shared destination inode: {absolute}")
    return path


def source_file(source: Path, relative: str) -> Path:
    path = source / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise StageError("Invalid source inventory path")
    if not path.resolve().is_relative_to(source) or path.is_symlink():
        raise StageError("Source escapes the exported tree")
    regular(path)
    return path


def account(root: Path) -> tuple[int, int]:
    rows = [r.split(":") for r in regular(root / "etc/passwd").decode().splitlines()]
    users = [r for r in rows if r[0] == "pocketds"]
    if len(users) != 1 or len(users[0]) != 7 or users[0][5] != HOME:
        raise StageError("Image requires the pocketds account at /home/pocketds")
    uid, gid = int(users[0][2]), int(users[0][3])
    groups = [r.split(":") for r in regular(root / "etc/group").decode().splitlines()]
    if uid < 1000 or not any(r[0] == "pocketds" and int(r[2]) == gid for r in groups):
        raise StageError("Image requires an unprivileged pocketds account and private group")
    shadows = [r.split(":") for r in regular(root / "etc/shadow").decode().splitlines()]
    entries = [r for r in shadows if r[0] == "pocketds"]
    if len(entries) != 1 or entries[0][1] not in {"!", "*", "!*"}:
        raise StageError("The image account needs a bare password lock with no retained password hash")
    return uid, gid


def elf(path: Path) -> bytes:
    data = regular(path, 32 * 1024 * 1024)
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01" or int.from_bytes(data[18:20], "little") != 183:
        raise StageError("Native payload must be a Linux AArch64 ELF64 binary")
    return data


def plan(root: Path, source: Path, panelctl: Path, observer: Path) -> dict:
    if not root.is_absolute() or root.is_symlink() or root.resolve() != root or root == Path("/"):
        raise StageError("--root must be an absolute, canonical disposable image directory")
    if regular(root / "etc/pocketds-image-build-root") != MARKER:
        raise StageError("Disposable image build marker is absent or invalid")
    os_release = (root / "usr/lib/os-release").read_text()
    if "ID=fedora\n" not in os_release or not any(v in os_release for v in ('VERSION_ID=44\n', 'VERSION_ID="44"\n')):
        raise StageError("Image root must contain Fedora 44")
    if not source.is_absolute() or source.is_symlink() or source.resolve() != source or (source / ".git").exists():
        raise StageError("--source-root must be a canonical history-free source export")
    assets = json.loads(regular(source_file(source, "components/assets/ASSETS.json")))
    if assets != {"schema": 1, "profile": "asset-free", "assets": []}:
        raise StageError("Image build requires the exact asset-free source profile")
    if (source / "assets").exists() and any((source / "assets").rglob("*")):
        raise StageError("Source export contains personal assets")
    if (root / "etc/sudoers.d/10-wheel-nopasswd").exists():
        raise StageError("Install the hardened pds2 userspace package before staging the Kit")
    uid, gid = account(root)
    home = root / HOME.lstrip("/")
    if home.is_symlink() or not home.is_dir():
        raise StageError("The image account needs a real home directory")
    missing = [name for name in COMMANDS if not (root / "usr/bin" / name).is_file()]
    if not list((root / "usr/lib64").glob("libseccomp.so.2*")):
        missing.append("libseccomp.so.2 (libseccomp package)")
    for path in ("/usr/lib/systemd/user/filter-chain.service", "/usr/share/applications/org.fcitx.Fcitx5.desktop", "/usr/libexec/pocketds-fancontrol-set-profile", "/usr/sbin/chpasswd"):
        if not (root / path.lstrip("/")).is_file():
            missing.append(path)
    if missing:
        raise StageError("Missing image runtime dependencies: " + ", ".join(missing))
    inventory = json.loads(regular(source_file(source, "packaging/sd-image/kit-files.json")))
    if inventory.get("schema") != "pocketds.sd-image-files.v1":
        raise StageError("Unknown Kit inventory schema")
    files = []
    seen = set()
    for row in inventory["files"]:
        if set(row) != {"source", "target", "mode", "owner"} or row["owner"] not in {"root", "pocketds"}:
            raise StageError("Invalid Kit file inventory")
        if row["target"] in seen or row["mode"] not in {"0440", "0644", "0755"}:
            raise StageError("Duplicate destination or unsafe mode in Kit inventory")
        seen.add(row["target"])
        user_owned = row["target"].startswith(HOME + "/")
        if user_owned != (row["owner"] == "pocketds"):
            raise StageError("Kit file ownership boundary mismatch")
        path = source_file(source, row["source"])
        target(root, row["target"])
        files.append({**row, "content": regular(path), "uid": uid if user_owned else 0, "gid": gid if user_owned else 0})
    for name, path in (("pocketds-panelctl", panelctl), ("pocketds-gamescope-observer", observer)):
        dest = "/usr/local/" + ("bin/" if name == "pocketds-panelctl" else "libexec/") + name
        target(root, dest)
        files.append({"source": "native-build/" + name, "target": dest, "mode": "0755", "owner": "root", "uid": 0, "gid": 0, "content": elf(path)})
    generated = {
        "/etc/tuned/active_profile": b"pocketds-balanced\n",
        HOME + "/.config/gtk-3.0/settings.ini": b"[Settings]\ngtk-im-module=fcitx\n",
        HOME + "/.config/kwinrc": b"[Wayland]\nInputMethod=/usr/share/applications/org.fcitx.Fcitx5.desktop\nVirtualKeyboardEnabled=true\nVirtualKeyboardMode=2\n\n[Xwayland]\nScale=1.5\n",
        HOME + "/.local/share/fcitx5/rime/default.custom.yaml": b"# Public image: use the packaged simplified pinyin schema by default.\npatch:\n  schema_list:\n    - schema: luna_pinyin_simp\n    - schema: luna_pinyin\n  menu/page_size: 7\n",
        "/var/lib/pocketds-firstboot/pending": b"",
        "/var/lib/pocketds-firstboot/resize-pending": b"pocketds.sd-resize.v1\n",
    }
    for dest, data in generated.items():
        target(root, dest)
        user_owned = dest.startswith(HOME + "/")
        files.append({"source": "generated", "target": dest, "mode": "0600" if dest == "/var/lib/pocketds-firstboot/pending" else "0644", "owner": "pocketds" if user_owned else "root", "uid": uid if user_owned else 0, "gid": gid if user_owned else 0, "content": data})
    links = []
    for unit in USER_UNITS:
        unit_data = next((row["content"] for row in files if row["target"] == HOME + "/.config/systemd/user/" + unit), None)
        if unit_data is None:
            raise StageError("Missing Kit user service: " + unit)
        wanted = [line.split("=", 1)[1] for line in unit_data.decode().splitlines() if line.startswith("WantedBy=")]
        if len(wanted) != 1 or wanted[0] not in {"graphical-session.target", "default.target"}:
            raise StageError("Unknown user service activation target: " + unit)
        links.append((HOME + "/.config/systemd/user/" + wanted[0] + ".wants/" + unit, "../" + unit, uid, gid))
    links.append((HOME + "/.config/systemd/user/timers.target.wants/pocketds-codex-quota.timer", "../pocketds-codex-quota.timer", uid, gid))
    links.append((HOME + "/.config/systemd/user/graphical-session.target.wants/filter-chain.service", "/usr/lib/systemd/user/filter-chain.service", uid, gid))
    for unit in SYSTEM_UNITS:
        unit_path = "/etc/systemd/system/" + unit
        if unit_path not in seen:
            unit_path = "/usr/lib/systemd/system/" + unit
            if unit_path not in seen and not (root / unit_path.lstrip("/")).is_file():
                raise StageError("Missing base image service: " + unit)
        links.append(("/etc/systemd/system/multi-user.target.wants/" + unit, unit_path, 0, 0))
    for unit in ("pocketds-osk-listener.service", "drkonqi-coredump-pickup.service"):
        links.append((HOME + "/.config/systemd/user/" + unit, "/dev/null", uid, gid))
    links.append(("/etc/systemd/system/power-profiles-daemon.service", "/dev/null", 0, 0))
    links.append(("/etc/systemd/system/pocketds-power-button.service", "/dev/null", 0, 0))
    links.append(("/etc/systemd/system/pocketds-fs-resize.service", "/dev/null", 0, 0))
    links.append(("/etc/systemd/system/plasma-setup.service", "/dev/null", 0, 0))
    for dest, link, _uid, _gid in links:
        path = root / dest.lstrip("/")
        if path.is_symlink() and os.readlink(path) == link:
            if not path.parent.resolve().is_relative_to(root):
                raise StageError("Unit link parent escapes image")
        else:
            target(root, dest)
    remove = []
    resize_marker = root / "storage/.please_resize_me"
    if resize_marker.exists() or resize_marker.is_symlink():
        target(root, "/storage/.please_resize_me")
        regular(resize_marker, 1024)
        remove.append("/storage/.please_resize_me")
    target(root, "/usr/share/pocketds-linux-kit/image-install-manifest.json")
    return {"files": files, "links": links, "uid": uid, "gid": gid, "remove": remove}


def apply(root: Path, payload: dict) -> dict:
    def parent(path: Path, uid: int, gid: int) -> None:
        missing = []
        current = path.parent
        while not current.exists():
            missing.append(current)
            current = current.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o755)
            os.chown(directory, uid, gid)

    manifest = {"schema": "pocketds.sd-image-staged.v1", "files": [], "links": [], "hardware_tested": False, "api_configured": False, "deep_suspend_enabled": False}
    for row in payload["files"]:
        path = target(root, row["target"])
        parent(path, row["uid"], row["gid"])
        with path.open("wb") as stream:
            stream.write(row["content"])
        os.chmod(path, int(row["mode"], 8))
        os.chown(path, row["uid"], row["gid"])
        manifest["files"].append({k: row[k] for k in ("source", "target", "mode", "uid", "gid") } | {"sha256": hashlib.sha256(row["content"]).hexdigest(), "size": len(row["content"])})
    for dest, link, uid, gid in payload["links"]:
        path = root / dest.lstrip("/")
        parent(path, uid, gid)
        if path.exists() or path.is_symlink():
            path.unlink()
        path.symlink_to(link)
        os.chown(path, uid, gid, follow_symlinks=False)
        manifest["links"].append({"target": dest, "link": link, "uid": uid, "gid": gid})
    manifest["removed"] = payload["remove"]
    for dest in payload["remove"]:
        target(root, dest).unlink()
    # Directories made by useradd can already exist; normalize only the dedicated
    # desktop home and paths that belong to this newly staged image inventory.
    home = root / HOME.lstrip("/")
    os.chown(home, payload["uid"], payload["gid"])
    report = target(root, "/usr/share/pocketds-linux-kit/image-install-manifest.json")
    parent(report, 0, 0)
    report.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    os.chmod(report, 0o644)
    os.chown(report, 0, 0)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "source-root", "panelctl", "gamescope-observer"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--plan", action="store_true", help="validate every payload and print counts without writing")
    args = parser.parse_args()
    try:
        payload = plan(args.root, args.source_root, args.panelctl, args.gamescope_observer)
        if not args.plan:
            if os.geteuid() != 0:
                raise StageError("Staging ownership requires root inside the isolated image builder")
            apply(args.root, payload)
        print(json.dumps({"schema": "pocketds.sd-image-stage-result.v1", "planned": args.plan, "files": len(payload["files"]), "links": len(payload["links"]), "hardware_tested": False}))
        return 0
    except (OSError, ValueError, KeyError, StageError) as exc:
        print(f"Image staging failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

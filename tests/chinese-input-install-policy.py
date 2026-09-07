#!/usr/bin/env python3
"""Static contract for the bounded zh_CN + Fcitx5/Rime Ice installer."""

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts/install-chinese-input.sh").read_text(encoding="utf-8")
LOCK = json.loads(
    (ROOT / "components/locale/rime-ice.lock.json").read_text(encoding="utf-8")
)


assert LOCK == {
    "schema_version": 1,
    "repository": "https://github.com/iDvel/rime-ice.git",
    "commit": "fbb516b2786e4d5444383706d13c31c2e4d10c08",
    "commit_date": "2026-08-31T17:44:46+08:00",
    "schema": "rime_ice",
    "license": "GPL-3.0-only",
}

package_block = re.search(r"packages=\(\n(?P<body>.*?)\n\)", SCRIPT, re.DOTALL)
assert package_block is not None
packages = set(re.findall(r"^\s+([a-z0-9][a-z0-9+_.-]+)\s*$", package_block["body"], re.MULTILINE))
assert {
    "fcitx5",
    "fcitx5-autostart",
    "fcitx5-gtk",
    "fcitx5-rime",
    "glibc-langpack-zh",
    "google-noto-sans-cjk-vf-fonts",
    "google-noto-sans-mono-cjk-vf-fonts",
    "librime-lua",
} == packages
assert not any(name.startswith(("ibus", "fcitx5-qt", "qt6-")) for name in packages)

required_fragments = (
    "--setopt=install_weak_deps=False",
    "dnf install --assumeno",
    "^Upgrading:|^Removing:",
    "POCKETDS-INSTALL-ZH-CN-RIME-ICE",
    "Refusing apply on non-aarch64 host",
    "Refusing apply outside the verified Fedora 44 base",
    "Refusing to replace an unmanaged Rime directory",
    "git -C \"$transaction/rime-source\" fsck --no-dangling",
    "localectl set-locale LANG=zh_CN.UTF-8",
    "localectl set-keymap us",
    "localectl set-x11-keymap us pc105",
    "backup_root_file /etc/vconsole.conf",
    "backup_root_file /etc/X11/xorg.conf.d/00-keyboard.conf",
    "restore-chinese-catalogs.py",
    "POCKETDS-RESTORE-ZH-CN-CATALOGS",
    "name: 雾凇拼音",
    "--group Translations --key LANGUAGE zh_CN",
    "/usr/share/applications/org.fcitx.Fcitx5.desktop",
    "protected-packages-before.txt",
    "protected-packages-after.txt",
    "protected_platform_packages=unchanged",
    "base_keymap=us",
    "relogin_required=true",
)
for fragment in required_fragments:
    assert fragment in SCRIPT, fragment

for forbidden in ("dnf upgrade", "dnf update", "--disableexcludes", "/boot/", "fastboot"):
    assert forbidden not in SCRIPT, forbidden

profile = (ROOT / "components/locale/profile").read_text(encoding="utf-8")
override = (ROOT / "components/locale/default.custom.yaml").read_text(encoding="utf-8")
schema_override = (ROOT / "components/locale/rime_ice.custom.yaml").read_text(encoding="utf-8")
theme = (ROOT / "components/locale/classicui.conf").read_text(encoding="utf-8")
assert "DefaultIM=rime" in profile
assert "Name=keyboard-us" in profile
assert "schema: rime_ice" in override
assert "menu/page_size: 7" in override
for setting in (
    "translator/enable_user_dict: true",
    "translator/enable_sentence: true",
    "translator/enable_completion: true",
    "translator/enable_word_completion: true",
):
    assert setting in schema_override
assert 'components/locale/rime_ice.custom.yaml' in SCRIPT
main_installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
assert 'components/locale/rime_ice.custom.yaml' in main_installer
assert 'Font="Noto Sans CJK SC 15"' in theme
assert "Vertical Candidate List=True" in theme

hook = ROOT / "components/locale/90-fcitx5-wayland.sh"
autostart = (ROOT / "components/locale/org.fcitx.Fcitx5.desktop").read_text(encoding="utf-8")
environment = (ROOT / "components/locale/90-fcitx5.conf").read_text(encoding="utf-8")
importer = (ROOT / "scripts/import-live.sh").read_text(encoding="utf-8")
assert "Hidden=true" in autostart
assert "Exec=" not in autostart
assert "XMODIFIERS=@im=fcitx" in environment
assert "QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1" in environment
assert not re.search(r"^(GTK|QT)_IM_MODULE=", environment, re.MULTILINE)

for installer in (SCRIPT, main_installer):
    for component, target in (
        ("90-fcitx5-wayland.sh", "$HOME/.config/plasma-workspace/env/90-fcitx5-wayland.sh"),
        ("org.fcitx.Fcitx5.desktop", "$HOME/.config/autostart/org.fcitx.Fcitx5.desktop"),
    ):
        assert f'"$repo_root/components/locale/{component}"' in installer
        assert f'"{target}"' in installer
    assert '--group Settings --key gtk-im-module fcitx' in installer
    assert '"$HOME/.config/gtk-3.0/settings.ini"' in installer
    assert installer.index('backup_user_file "$target"') < installer.index(
        '--group Settings --key gtk-im-module fcitx'
    )
    assert "components/system/kwinrc.current" not in installer
assert 'if [[ -f /usr/share/applications/org.fcitx.Fcitx5.desktop ]]; then' in main_installer
assert "start app-org.fcitx.Fcitx5@autostart.service" not in SCRIPT
assert "fcitx5 -d" not in SCRIPT
assert "--replace" not in SCRIPT
assert 'org.kde.KWin reconfigure' in SCRIPT
assert '"ReloadConfig"' in SCRIPT
assert "pending_session" in SCRIPT and "pending_rime" in SCRIPT
assert '"status=$status"' in SCRIPT
assert '"session_status=$session_status"' in SCRIPT
assert '"rime_status=$rime_status"' in SCRIPT
assert "for _ in {1..45}" in SCRIPT

# Execute the real POSIX shell hook with inherited pollution. Plasma 6.7.3
# sourceFiles merges only returned keys; it cannot propagate an unset key.
keys = ("GTK_IM_MODULE", "QT_IM_MODULE", "SDL_IM_MODULE", "XMODIFIERS")
inherited = os.environ.copy()
inherited.update({key: "fcitx" for key in keys})
inherited["POCKETDS_TEST_UNRELATED"] = "preserve"
shell_code = '. "$1"; exec "$2" -c "import json, os; print(json.dumps(dict(os.environ)))"'


def source_environment(path, session):
    result = subprocess.run(
        ["sh", "-c", shell_code, "sh", str(path), sys.executable],
        env={**inherited, "XDG_SESSION_TYPE": session},
        capture_output=True, text=True, check=True, timeout=5,
    )
    return json.loads(result.stdout)


wayland = source_environment(hook, "wayland")
for key in keys[:2]:
    assert key in wayland and wayland[key] == "", (key, wayland.get(key))
assert wayland["SDL_IM_MODULE"] == inherited["SDL_IM_MODULE"]
assert wayland["XMODIFIERS"] == "@im=fcitx"
assert wayland["POCKETDS_TEST_UNRELATED"] == "preserve"
merged_parent = {**inherited, **wayland}
assert all(merged_parent[key] == "" for key in keys[:2])
for session in ("x11", ""):
    other = source_environment(hook, session)
    assert all(other[key] == inherited[key] for key in keys)

with tempfile.TemporaryDirectory() as directory:
    root = pathlib.Path(directory)
    broken = root / "unset.sh"
    broken.write_text("unset GTK_IM_MODULE QT_IM_MODULE\n", encoding="utf-8")
    unset_child = source_environment(broken, "wayland")
    assert all(key not in unset_child for key in keys[:2])
    assert all({**inherited, **unset_child}[key] == "fcitx" for key in keys[:2])

    # Run the importer's actual validation without invoking any copy action.
    marker = 'python3 - "$HOME/.config/environment.d/90-fcitx5.conf" <<\'PY\'\n'
    validation = importer.split(marker, 1)[1].split("\nPY\n", 1)[0]
    assert importer.index(marker) < importer.index("copy_live()")
    source = root / "90-fcitx5.conf"
    for addition, allowed in (
        ("", True), ("GTK_IM_MODULE=\nQT_IM_MODULE=''\nSDL_IM_MODULE=\"\"\n", True),
        ("GTK_IM_MODULE=fcitx\n", False), ("QT_IM_MODULE = fcitx\n", False),
        ("SDL_IM_MODULE=ibus\n", True), ("QT_IM_MODULE=${OLD_IM}\n", False),
    ):
        source.write_text(environment + addition, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-c", validation, str(source)],
            capture_output=True, text=True, timeout=5,
        )
        assert (result.returncode == 0) == allowed, (addition, result.stderr)

# Exercise the installer ownership check itself with isolated /proc and bus
# fixtures. Only a compositor-created, clean-environment instance is reloaded.
reload_code = SCRIPT.split("reload_kwin_fcitx() {\n    python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def check_reload(*, parent=200, socket=True, polluted=False, owner_changed=False, absent=False):
    calls = []
    owner_reads = 0

    def run(command, **kwargs):
        nonlocal owner_reads
        destination, _, _, method, *arguments = command[5:]
        calls.append((destination, method))
        if method == "GetNameOwner":
            if absent:
                raise subprocess.CalledProcessError(1, command)
            name = arguments[-1]
            if name == "org.fcitx.Fcitx5":
                owner_reads += 1
                value = ":1.999" if owner_changed and owner_reads > 1 else ":1.10"
            else:
                value = ":1.20"
        elif method == "GetConnectionUnixProcessID":
            value = 100 if arguments[-1] == ":1.10" else 200
        else:
            assert method == "ReloadConfig", method
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"data": []}))
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"data": [value]}))

    class ProcPath:
        def __init__(self, path):
            self.path = path

        def __truediv__(self, suffix):
            return ProcPath(self.path + "/" + suffix)

        def read_text(self):
            assert self.path == "/proc/100/stat"
            return f"100 (fcitx5) S {parent} 0"

        def read_bytes(self):
            assert self.path == "/proc/100/environ"
            return ((b"WAYLAND_SOCKET=3\0" if socket else b"")
                    + (b"QT_IM_MODULE=fcitx\0" if polluted else b"QT_IM_MODULE=\0")
                    + b"SDL_IM_MODULE=fcitx\0")

        def resolve(self):
            return self

        @property
        def name(self):
            return "fcitx5"

    with mock.patch("subprocess.run", run), mock.patch("pathlib.Path", ProcPath):
        try:
            exec(compile(reload_code, "installer-reload-fixture", "exec"), {})
        except SystemExit as error:
            assert error.code == 1
            passed = False
        else:
            passed = True
    return passed, calls


passed, calls = check_reload()
assert passed and calls[-1] == (":1.10", "ReloadConfig")
for arguments in ({"parent": 201}, {"socket": False}, {"polluted": True},
                  {"owner_changed": True}, {"absent": True}):
    passed, calls = check_reload(**arguments)
    assert not passed and not any(method == "ReloadConfig" for _, method in calls), arguments

# Execute the real bounded activation/receipt decision without waiting or
# touching a session. Missing first-deploy output must never become verified.
activation = SCRIPT.split("session_status=pending_session\n", 1)[1].split("\nrpm -qa", 1)[0]
activation = "session_status=pending_session\n" + activation
fixture = r'''
set -e
rime_dir=$1
rime_schema=rime_ice
reload_count=0
sleep_count=0
reload_kwin_fcitx() {
    reload_count=$((reload_count + 1))
    [ "$TEST_OWNER" = ready ] || { [ "$TEST_OWNER" = recovered ] && [ "$reload_count" -gt 1 ]; }
}
busctl() { [ "$TEST_BUS" = ready ]; }
sleep() { sleep_count=$((sleep_count + 1)); }
'''
with tempfile.TemporaryDirectory() as directory:
    schema = pathlib.Path(directory) / "build/rime_ice.schema.yaml"
    schema.parent.mkdir()
    for owner, bus, built, expected in (
        ("ready", "ready", True, "verified ready ready 0"),
        ("ready", "ready", False, "pending_rime ready pending_rime 45"),
        ("absent", "ready", False, "pending_session pending_session pending_rime 1"),
        ("absent", "ready", True, "pending_session pending_session ready 1"),
        ("recovered", "ready", True, "verified ready ready 1"),
        ("absent", "absent", False, "pending_session pending_session pending_rime 0"),
    ):
        if built:
            schema.write_text("schema: rime_ice\n", encoding="utf-8")
        else:
            schema.unlink(missing_ok=True)
        result = subprocess.run(
            ["bash", "-c", fixture + activation + '\nprintf "%s %s %s %s\\n" "$status" "$session_status" "$rime_status" "$sleep_count"',
             "bash", directory],
            env={**os.environ, "TEST_OWNER": owner, "TEST_BUS": bus},
            capture_output=True, text=True, check=True, timeout=5,
        )
        assert result.stdout.strip() == expected, (owner, bus, built, result.stdout)

print("  [OK] Chinese/Rime installer: package policy, Wayland environment, import and KWin ownership")

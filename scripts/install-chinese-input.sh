#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
lock_file="$repo_root/components/locale/rime-ice.lock.json"
confirm_phrase=POCKETDS-INSTALL-ZH-CN-RIME-ICE
apply=0
confirm=''

packages=(
    fcitx5
    fcitx5-autostart
    fcitx5-gtk
    fcitx5-rime
    glibc-langpack-zh
    google-noto-sans-cjk-vf-fonts
    google-noto-sans-mono-cjk-vf-fonts
    librime-lua
)

usage() {
    cat <<EOF
usage: $0 [--apply --confirm $confirm_phrase]

Without --apply, print the bounded package and configuration plan only.
EOF
}

while (($#)); do
    case "$1" in
        --apply)
            apply=1
            ;;
        --confirm)
            shift
            (($#)) || { usage >&2; exit 2; }
            confirm=$1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if [[ $EUID -eq 0 ]]; then
    echo 'Run this script as the Pocket DS desktop user, not root.' >&2
    exit 1
fi

for command in busctl cmp dnf git grep install kwriteconfig6 locale localectl \
    python3 rpm sudo systemctl tar; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

[[ -f $lock_file ]] || {
    echo "Missing Rime Ice source lock: $lock_file" >&2
    exit 1
}

mapfile -t rime_lock < <(python3 - "$lock_file" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    lock = json.load(handle)

if lock.get("schema_version") != 1:
    raise SystemExit("Unsupported Rime Ice lock schema")
repository = lock.get("repository", "")
commit = lock.get("commit", "")
schema = lock.get("schema", "")
if repository != "https://github.com/iDvel/rime-ice.git":
    raise SystemExit("Unexpected Rime Ice repository")
if not re.fullmatch(r"[0-9a-f]{40}", commit):
    raise SystemExit("Invalid Rime Ice commit")
if schema != "rime_ice":
    raise SystemExit("Unexpected Rime schema")
print(repository)
print(commit)
print(schema)
PY
)
rime_repository=${rime_lock[0]}
rime_commit=${rime_lock[1]}
rime_schema=${rime_lock[2]}

echo '[plan] Simplified Chinese + Fcitx5/Rime Ice'
printf '  package  %s\n' "${packages[@]}"
echo "  source   $rime_repository@$rime_commit"
echo '  locale   zh_CN.UTF-8'
echo '  keymap   US (console and X11 base layout)'
echo '  desktop  Plasma translations + KWin Wayland input method'
echo '  safety   no Qt, Plasma, kernel, firmware or boot package changes'

if ((apply == 0)); then
    echo "Re-run with --apply --confirm $confirm_phrase after reviewing the plan."
    exit 0
fi

[[ $confirm == "$confirm_phrase" ]] || {
    echo "Refusing apply: confirmation must be $confirm_phrase" >&2
    exit 2
}

[[ $(uname -m) == aarch64 ]] || {
    echo "Refusing apply on non-aarch64 host: $(uname -m)" >&2
    exit 1
}
[[ $(rpm -E '%fedora') == 44 ]] || {
    echo "Refusing apply outside the verified Fedora 44 base: $(rpm -E '%fedora')" >&2
    exit 1
}
sudo -n true

rime_dir="$HOME/.local/share/fcitx5/rime"
rime_marker="$rime_dir/.pocketds-rime-ice-commit"
if [[ -L $rime_dir || (-e $rime_dir && ! -d $rime_dir) ]]; then
    echo "Refusing unsafe Rime target: $rime_dir" >&2
    exit 1
fi
if [[ -d $rime_dir && ! -f $rime_marker ]]; then
    echo "Refusing to replace an unmanaged Rime directory: $rime_dir" >&2
    exit 1
fi
if [[ -f $rime_marker && $(<"$rime_marker") != "$rime_commit" ]]; then
    echo 'Refusing an in-place Rime Ice upgrade; preserve user dictionaries first.' >&2
    exit 1
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
transaction="$HOME/.local/state/pocketds-linux-kit/locale-transactions/zh-cn-rime-$stamp"
umask 077
mkdir -p "$transaction/backup/user" "$transaction/backup/root" \
    "$transaction/rime-source"

backup_user_file() {
    local target=$1 rel
    [[ -e $target || -L $target ]] || return 0
    rel=${target#"$HOME"/}
    mkdir -p "$transaction/backup/user/$(dirname "$rel")"
    cp -a -- "$target" "$transaction/backup/user/$rel"
}

backup_root_file() {
    local target=$1 rel
    sudo test -e "$target" || return 0
    rel=${target#/}
    sudo mkdir -p "$transaction/backup/root/$(dirname "$rel")"
    sudo cp -a -- "$target" "$transaction/backup/root/$rel"
    sudo chown -R "$(id -u):$(id -g)" "$transaction/backup/root"
}

install_user_file() {
    local source=$1 target=$2
    if [[ -e $target ]] && cmp -s -- "$source" "$target"; then
        return 0
    fi
    [[ ! -L $target ]] || {
        echo "Refusing to replace symlink: $target" >&2
        exit 1
    }
    backup_user_file "$target"
    mkdir -p "$(dirname "$target")"
    install -m 0644 "$source" "$target"
}

rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | sort \
    >"$transaction/packages-before.txt"
rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | \
    grep -E '^(kernel|kwin|mesa|plasma|qt6-|linux-firmware)' | sort \
    >"$transaction/protected-packages-before.txt" || true
localectl status >"$transaction/localectl-before.txt"
backup_root_file /etc/locale.conf
backup_root_file /etc/vconsole.conf
backup_root_file /etc/X11/xorg.conf.d/00-keyboard.conf
for target in \
    "$HOME/.config/environment.d/90-fcitx5.conf" \
    "$HOME/.config/plasma-workspace/env/90-fcitx5-wayland.sh" \
    "$HOME/.config/autostart/org.fcitx.Fcitx5.desktop" \
    "$HOME/.config/gtk-3.0/settings.ini" \
    "$HOME/.config/fcitx5/config" \
    "$HOME/.config/fcitx5/profile" \
    "$HOME/.config/fcitx5/conf/classicui.conf" \
    "$HOME/.config/plasma-localerc" \
    "$HOME/.config/kwinrc"; do
    backup_user_file "$target"
done

git -C "$transaction/rime-source" init -q
git -C "$transaction/rime-source" remote add origin "$rime_repository"
git -C "$transaction/rime-source" fetch -q --depth=1 origin "$rime_commit"
git -C "$transaction/rime-source" checkout -q --detach FETCH_HEAD
[[ $(git -C "$transaction/rime-source" rev-parse HEAD) == "$rime_commit" ]] || {
    echo 'Fetched Rime Ice source does not match the locked commit.' >&2
    exit 1
}
git -C "$transaction/rime-source" fsck --no-dangling >/dev/null

preview="$transaction/dnf-preview.txt"
sudo env LC_ALL=C.UTF-8 dnf install --assumeno --setopt=install_weak_deps=False \
    "${packages[@]}" >"$preview" 2>&1 || true
if ! grep -Eq 'Transaction Summary:|Nothing to do\.' "$preview"; then
    cat "$preview" >&2
    echo 'DNF preview did not produce a valid transaction.' >&2
    exit 1
fi
if grep -Eq '^Upgrading:|^Removing:|^[[:space:]]+(kernel|kwin|mesa|plasma|qt6-|linux-firmware)' \
    "$preview"; then
    cat "$preview" >&2
    echo 'DNF preview touched the protected Pocket DS platform stack.' >&2
    exit 1
fi

echo '[apply] installing the bounded Fedora package set'
sudo env LC_ALL=C.UTF-8 dnf install -y --setopt=install_weak_deps=False \
    "${packages[@]}"
sudo dnf history info last >"$transaction/dnf-history-last.txt"

if [[ ! -d $rime_dir ]]; then
    mkdir -p "$rime_dir"
    git -C "$transaction/rime-source" archive HEAD | tar -x -C "$rime_dir"
    printf '%s\n' "$rime_commit" >"$rime_marker"
fi
install_user_file "$repo_root/components/locale/default.custom.yaml" \
    "$rime_dir/default.custom.yaml"
install_user_file "$repo_root/components/locale/rime_ice.custom.yaml" \
    "$rime_dir/rime_ice.custom.yaml"
touch "$rime_dir/default.custom.yaml"
touch "$rime_dir/rime_ice.custom.yaml"

install_user_file "$repo_root/components/locale/90-fcitx5.conf" \
    "$HOME/.config/environment.d/90-fcitx5.conf"
install_user_file "$repo_root/components/locale/90-fcitx5-wayland.sh" \
    "$HOME/.config/plasma-workspace/env/90-fcitx5-wayland.sh"
install_user_file "$repo_root/components/locale/org.fcitx.Fcitx5.desktop" \
    "$HOME/.config/autostart/org.fcitx.Fcitx5.desktop"
install_user_file "$repo_root/components/locale/config" \
    "$HOME/.config/fcitx5/config"
install_user_file "$repo_root/components/locale/profile" \
    "$HOME/.config/fcitx5/profile"
install_user_file "$repo_root/components/locale/classicui.conf" \
    "$HOME/.config/fcitx5/conf/classicui.conf"

echo '[apply] selecting Simplified Chinese and the Wayland input method'
sudo localectl set-locale LANG=zh_CN.UTF-8
sudo localectl set-keymap us
sudo localectl set-x11-keymap us pc105
python3 "$repo_root/scripts/restore-chinese-catalogs.py" \
    --apply --confirm POCKETDS-RESTORE-ZH-CN-CATALOGS
kwriteconfig6 --file plasma-localerc --group Formats --key LANG zh_CN.UTF-8
kwriteconfig6 --file plasma-localerc --group Translations --key LANGUAGE zh_CN
for target in "$HOME/.config/gtk-3.0/settings.ini" "$HOME/.config/kwinrc"; do
    [[ ! -L $target && ( ! -e $target || -f $target ) ]] || {
        echo "Refusing non-regular input-method configuration: $target" >&2
        exit 1
    }
done
mkdir -p "$HOME/.config/gtk-3.0"
kwriteconfig6 --file "$HOME/.config/gtk-3.0/settings.ini" \
    --group Settings --key gtk-im-module fcitx
kwriteconfig6 --file kwinrc --group Wayland --key InputMethod \
    /usr/share/applications/org.fcitx.Fcitx5.desktop
kwriteconfig6 --file kwinrc --group Wayland --key VirtualKeyboardEnabled true

systemctl --user daemon-reload
# Never replace KWin's input method with an independently launched process:
# its private Wayland socket belongs to the compositor-created child.
reload_kwin_fcitx() {
    python3 - <<'PY'
import json
import pathlib
import subprocess


def call(destination, path, interface, method, *arguments):
    result = subprocess.run(
        ["busctl", "--user", "--timeout=2s", "--json=short", "call",
         destination, path, interface, method, *arguments],
        check=True, capture_output=True, text=True, timeout=3,
    )
    data = json.loads(result.stdout)["data"] if result.stdout.strip() else []
    return data[0] if data else None


def bus(method, name):
    return call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", method, "s", name)


try:
    fcitx_owner = bus("GetNameOwner", "org.fcitx.Fcitx5")
    kwin_owner = bus("GetNameOwner", "org.kde.KWin")
    fcitx_pid = bus("GetConnectionUnixProcessID", fcitx_owner)
    kwin_pid = bus("GetConnectionUnixProcessID", kwin_owner)
    if type(fcitx_pid) is not int or type(kwin_pid) is not int:
        raise ValueError("Invalid input-method process identity")
    process = pathlib.Path(f"/proc/{fcitx_pid}")
    # stat fields after the final ')' start with state, then parent PID.
    if int((process / "stat").read_text().rsplit(")", 1)[1].split()[1]) != kwin_pid:
        raise ValueError("Fcitx was not launched by KWin")
    if (process / "exe").resolve().name != "fcitx5":
        raise ValueError("Unexpected input-method executable")
    environment = dict(part.split(b"=", 1) for part in
                       (process / "environ").read_bytes().split(b"\0") if b"=" in part)
    if not environment.get(b"WAYLAND_SOCKET") or any(
        environment.get(key) for key in (b"GTK_IM_MODULE", b"QT_IM_MODULE")
    ):
        raise ValueError("Fcitx needs a fresh Wayland login environment")
    if (bus("GetNameOwner", "org.fcitx.Fcitx5") != fcitx_owner
            or bus("GetNameOwner", "org.kde.KWin") != kwin_owner):
        raise ValueError("Input-method owner changed")
    call(fcitx_owner, "/controller", "org.fcitx.Fcitx.Controller1", "ReloadConfig")
except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
    raise SystemExit(1)
PY
}

session_status=pending_session
if reload_kwin_fcitx; then
    session_status=ready
elif busctl --user --timeout=2s call org.kde.KWin /KWin org.kde.KWin reconfigure \
        >/dev/null 2>&1; then
    # A new InputMethod setting lets KWin launch its child. An unchanged
    # setting may do nothing; do not toggle it or steal an existing session.
    sleep 1
    if reload_kwin_fcitx; then
        session_status=ready
    fi
fi

rime_status=pending_rime
if [[ $session_status == ready ]]; then
    for _ in {1..45}; do
        [[ -f $rime_dir/build/$rime_schema.schema.yaml ]] && break
        sleep 1
    done
fi
if [[ -f $rime_dir/build/$rime_schema.schema.yaml ]]; then
    rime_status=ready
fi
status=verified
if [[ $session_status != ready ]]; then
    status=pending_session
elif [[ $rime_status != ready ]]; then
    status=pending_rime
fi

rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | sort \
    >"$transaction/packages-after.txt"
rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' | \
    grep -E '^(kernel|kwin|mesa|plasma|qt6-|linux-firmware)' | sort \
    >"$transaction/protected-packages-after.txt" || true
cmp -s "$transaction/protected-packages-before.txt" \
    "$transaction/protected-packages-after.txt" || {
    echo 'Protected Pocket DS platform packages changed unexpectedly.' >&2
    exit 1
}

for package in "${packages[@]}"; do
    rpm -q "$package" >/dev/null
done
locale -a | grep -qi '^zh_CN\.utf8$'
grep -qx 'LANG=zh_CN.UTF-8' /etc/locale.conf
localectl status >"$transaction/localectl-after.txt"
grep -Eq '^[[:space:]]*VC Keymap:[[:space:]]+us$' \
    "$transaction/localectl-after.txt"
grep -Eq '^[[:space:]]*X11 Layout:[[:space:]]+us$' \
    "$transaction/localectl-after.txt"
grep -qx 'DefaultIM=rime' "$HOME/.config/fcitx5/profile"
grep -q 'schema: rime_ice' "$rime_dir/default.custom.yaml"
grep -q 'translator/enable_user_dict: true' "$rime_dir/rime_ice.custom.yaml"
grep -q '^[[:space:]]*name: 雾凇拼音' "$rime_dir/rime_ice.schema.yaml"

printf '%s\n' \
    "status=$status" \
    "session_status=$session_status" \
    "rime_status=$rime_status" \
    "locale=zh_CN.UTF-8" \
    "base_keymap=us" \
    "input_method=fcitx5-rime" \
    "rime_repository=$rime_repository" \
    "rime_commit=$rime_commit" \
    "protected_platform_packages=unchanged" \
    "relogin_required=true" \
    >"$transaction/receipt.txt"

if [[ $status == verified ]]; then
    echo '[verify] PASS'
else
    echo "[verify] configuration installed; $status (not a completed input-method verification)"
fi
echo "Transaction: $transaction"
echo 'Log out or reboot once to apply the Chinese Plasma language everywhere.'

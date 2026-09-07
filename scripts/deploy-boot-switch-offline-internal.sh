#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
confirmation=${1:-}
[[ $# -eq 1 && $confirmation == CONFIRM-OFFLINE-INTERNAL ]] || {
    echo "usage: $0 CONFIRM-OFFLINE-INTERNAL" >&2
    exit 2
}
[[ $EUID -ne 0 ]] || {
    echo 'Run as the desktop user; the script uses sudo only for bounded writes.' >&2
    exit 1
}
[[ $(findmnt -n -o SOURCE /) == /dev/mmcblk0p2 ]] || {
    echo 'Refusing: the running root is not the verified rescue TF Fedora.' >&2
    exit 1
}
[[ $(git -C "$repo_root" status --porcelain) == "" ]] || {
    echo 'Refusing: source repository is not clean.' >&2
    exit 1
}
for partition in /dev/sda12 /dev/sda13; do
    [[ -b $partition ]] || {
        echo "Refusing: missing internal partition $partition" >&2
        exit 1
    }
    [[ $(findmnt -rn -S "$partition") == "" ]] || {
        echo "Refusing: $partition is already mounted." >&2
        exit 1
    }
done
[[ $(lsblk -dn -o PARTLABEL /dev/sda12) == ROCKNIX ]] || {
    echo 'Refusing: /dev/sda12 is not the ROCKNIX partition.' >&2
    exit 1
}
[[ $(lsblk -dn -o PARTLABEL /dev/sda13) == STORAGE ]] || {
    echo 'Refusing: /dev/sda13 is not the STORAGE partition.' >&2
    exit 1
}

devinfo_before=$(sudo sha256sum /dev/disk/by-partlabel/devinfo | cut -d ' ' -f 1)
[[ $devinfo_before == fb560ce40bf17c7fc0dbda4b7e17748eb6e2fd30bbc8281ee0fc95acb28e19da ]] || {
    echo 'Refusing: live devinfo is not the verified Linux/SD-card checkpoint.' >&2
    exit 1
}

mount_root=$(sudo mktemp -d /run/pocketds-boot-switch-deploy.XXXXXX)
build_binary=$(mktemp "$repo_root/build/pocketds-panelctl.offline.XXXXXX")
cleanup() {
    if mountpoint -q "$mount_root/boot"; then sudo umount "$mount_root/boot"; fi
    if mountpoint -q "$mount_root"; then sudo umount "$mount_root"; fi
    sudo rmdir "$mount_root" 2>/dev/null || true
    rm -f "$build_binary"
}
trap cleanup EXIT HUP INT TERM

sudo mount /dev/sda13 "$mount_root"
sudo mount /dev/sda12 "$mount_root/boot"
[[ -f $mount_root/etc/fedora-release ]] || {
    echo 'Refusing: internal STORAGE is not the Fedora root.' >&2
    exit 1
}
internal_repo=$mount_root/home/pocketds/pocketds-linux-kit-final
[[ -d $internal_repo/.git ]] || {
    echo 'Refusing: internal Fedora source repository is missing.' >&2
    exit 1
}
[[ $(sudo -u pocketds git -C "$internal_repo" status --porcelain) == "" ]] || {
    echo 'Refusing: internal Fedora source repository is not clean.' >&2
    exit 1
}
source_head=$(git -C "$repo_root" rev-parse HEAD)
internal_head=$(sudo -u pocketds git -C "$internal_repo" rev-parse HEAD)
git -C "$repo_root" merge-base --is-ancestor "$internal_head" "$source_head" || {
    echo 'Refusing: internal source cannot be fast-forwarded to the rescue source.' >&2
    exit 1
}
sudo -u pocketds git -C "$internal_repo" fetch "$repo_root" HEAD
sudo -u pocketds git -C "$internal_repo" merge --ff-only FETCH_HEAD
[[ $(sudo -u pocketds git -C "$internal_repo" rev-parse HEAD) == "$source_head" ]]

c++ -std=c++17 -O2 -Wall -Wextra \
    "$repo_root/components/control-panel/pocketds-panelctl.cpp" -o "$build_binary"

stamp=$(date +%Y%m%d-%H%M%S)
backup_root=$mount_root/var/lib/pocketds-linux-kit/backups/$stamp-offline-boot-switch
sudo mkdir -p "$backup_root/usr/local/libexec" "$backup_root/usr/local/bin" \
    "$backup_root/home/pocketds/.local/share/plasma/plasmoids/org.pocketds.controlpanel.v3/contents/ui" \
    "$backup_root/boot"
for relative in \
    usr/local/libexec/pocketds-panel-root \
    usr/local/libexec/pocketds-boot-mode \
    usr/local/bin/pocketds-panelctl \
    home/pocketds/.local/share/plasma/plasmoids/org.pocketds.controlpanel.v3/contents/ui/ControllerDiagram.qml \
    home/pocketds/.local/share/plasma/plasmoids/org.pocketds.controlpanel.v3/contents/ui/ControllerTestSession.qml \
    home/pocketds/.local/share/plasma/plasmoids/org.pocketds.controlpanel.v3/contents/ui/main.qml \
    boot/PocketDS-Switch-to-Linux.sh; do
    if sudo test -e "$mount_root/$relative"; then
        sudo cp -a "$mount_root/$relative" "$backup_root/$relative"
    else
        printf '%s\n' "$relative" | sudo tee -a "$backup_root/missing-files" >/dev/null
    fi
done

sudo install -o root -g root -m 0755 \
    "$repo_root/components/control-panel/pocketds-panel-root" \
    "$mount_root/usr/local/libexec/pocketds-panel-root"
sudo install -o root -g root -m 0755 \
    "$repo_root/components/system/pocketds-boot-mode.py" \
    "$mount_root/usr/local/libexec/pocketds-boot-mode"
sudo install -o root -g root -m 0755 "$build_binary" \
    "$mount_root/usr/local/bin/pocketds-panelctl"
for ui_file in ControllerDiagram.qml ControllerTestSession.qml main.qml; do
    sudo install -o pocketds -g pocketds -m 0644 \
        "$repo_root/components/control-panel/plasmoid/contents/ui/$ui_file" \
        "$mount_root/home/pocketds/.local/share/plasma/plasmoids/org.pocketds.controlpanel.v3/contents/ui/$ui_file"
done
sudo install -o root -g root -m 0644 \
    "$repo_root/components/system/pocketds-switch-to-linux-android.sh" \
    "$mount_root/boot/PocketDS-Switch-to-Linux.sh"

sudo cmp -s "$repo_root/components/control-panel/pocketds-panel-root" \
    "$mount_root/usr/local/libexec/pocketds-panel-root"
sudo cmp -s "$repo_root/components/system/pocketds-boot-mode.py" \
    "$mount_root/usr/local/libexec/pocketds-boot-mode"
sudo cmp -s "$build_binary" "$mount_root/usr/local/bin/pocketds-panelctl"
for ui_file in ControllerDiagram.qml ControllerTestSession.qml main.qml; do
    sudo cmp -s "$repo_root/components/control-panel/plasmoid/contents/ui/$ui_file" \
        "$mount_root/home/pocketds/.local/share/plasma/plasmoids/org.pocketds.controlpanel.v3/contents/ui/$ui_file"
done
sudo cmp -s "$repo_root/components/system/pocketds-switch-to-linux-android.sh" \
    "$mount_root/boot/PocketDS-Switch-to-Linux.sh"
sudo sync

sudo umount "$mount_root/boot"
sudo umount "$mount_root"
sudo e2fsck -fn /dev/sda13
sudo fsck.fat -vn /dev/sda12
devinfo_after=$(sudo sha256sum /dev/disk/by-partlabel/devinfo | cut -d ' ' -f 1)
[[ $devinfo_after == "$devinfo_before" ]] || {
    echo 'ERROR: devinfo changed during offline deployment.' >&2
    exit 1
}

echo "[offline-boot-switch] internal Fedora updated to $source_head"
echo "[offline-boot-switch] backup: /var/lib/pocketds-linux-kit/backups/$stamp-offline-boot-switch"
echo "[offline-boot-switch] devinfo unchanged: $devinfo_after"

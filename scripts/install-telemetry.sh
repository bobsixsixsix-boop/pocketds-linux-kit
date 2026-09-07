#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)

if [[ $EUID -eq 0 ]]; then
    echo 'Run this script as the desktop user, not root.' >&2
    exit 1
fi
for command in c++ install systemctl sudo; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

stamp=$(date +%Y%m%d-%H%M%S)
backup="$HOME/.local/state/pocketds-linux-kit/backups/$stamp-telemetry"
root_backup="/var/lib/pocketds-linux-kit/backups/$stamp-telemetry"
mkdir -p "$backup/user" "$repo_root/build"

backup_user() {
    local target=$1 rel
    [[ -e $target || -L $target ]] || return 0
    rel=${target#"$HOME"/}
    mkdir -p "$backup/user/$(dirname "$rel")"
    cp -a "$target" "$backup/user/$rel"
}

install_user() {
    local source=$1 target=$2 mode=${3:-0644}
    if [[ -e $target ]] && cmp -s "$source" "$target"; then
        return 0
    fi
    backup_user "$target"
    mkdir -p "$(dirname "$target")"
    install -m "$mode" "$source" "$target"
    echo "  user  $target"
}

echo '[install-telemetry] validate and build'
"$repo_root/scripts/lint.sh"
c++ -std=c++17 -O2 -Wall -Wextra \
    "$repo_root/components/control-panel/pocketds-panelctl.cpp" \
    -o "$repo_root/build/pocketds-panelctl"

echo '[install-telemetry] user service and read-only sampler'
libexec="$HOME/.local/libexec/pocketds"
install_user "$repo_root/components/telemetry/pocketds-gpu-telemetry.py" \
    "$libexec/pocketds-gpu-telemetry.py" 0755
install_user "$repo_root/scripts/pocketds-gpu-observer.py" \
    "$libexec/pocketds-gpu-observer.py" 0755
install_user "$repo_root/components/telemetry/pocketds-gpu-telemetry.service" \
    "$HOME/.config/systemd/user/pocketds-gpu-telemetry.service"

echo '[install-telemetry] panel reader and UI'
if sudo test -e /usr/local/bin/pocketds-panelctl && \
        ! sudo cmp -s "$repo_root/build/pocketds-panelctl" /usr/local/bin/pocketds-panelctl; then
    sudo mkdir -p "$root_backup/usr/local/bin"
    sudo cp -a /usr/local/bin/pocketds-panelctl \
        "$root_backup/usr/local/bin/pocketds-panelctl"
fi
sudo install -m 0755 "$repo_root/build/pocketds-panelctl" \
    /usr/local/bin/pocketds-panelctl
plasmoid="$HOME/.local/share/plasma/plasmoids/org.pocketds.controlpanel.v3"
install_user "$repo_root/components/control-panel/plasmoid/metadata.json" \
    "$plasmoid/metadata.json"
for ui_file in ControllerDiagram.qml ControllerTestSession.qml main.qml; do
    install_user "$repo_root/components/control-panel/plasmoid/contents/ui/$ui_file" \
        "$plasmoid/contents/ui/$ui_file"
done

systemctl --user daemon-reload
systemctl --user enable pocketds-gpu-telemetry.service
systemctl --user restart pocketds-gpu-telemetry.service
echo "[install-telemetry] complete; user backup: $backup"
echo "[install-telemetry] root backup: $root_backup"

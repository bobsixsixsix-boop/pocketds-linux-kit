#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
capture_arg=

case "${1:-}" in
    '') ;;
    --capture-current) capture_arg=--capture-current ;;
    -h|--help)
        echo "usage: $0 [--capture-current]"
        exit 0
        ;;
    *)
        echo "usage: $0 [--capture-current]" >&2
        exit 2
        ;;
esac

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
backup="$HOME/.local/state/pocketds-linux-kit/backups/$stamp-brightness"
root_backup="/var/lib/pocketds-linux-kit/backups/$stamp-brightness"
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

echo '[install-brightness] validate and build'
"$repo_root/scripts/lint.sh"
c++ -std=c++17 -O2 -Wall -Wextra \
    "$repo_root/components/control-panel/pocketds-panelctl.cpp" \
    -o "$repo_root/build/pocketds-panelctl"

echo '[install-brightness] state machine'
libexec="$HOME/.local/libexec/pocketds"
if sudo test -e /usr/local/libexec/pocketds-panel-root && \
        ! sudo cmp -s "$repo_root/components/control-panel/pocketds-panel-root" \
            /usr/local/libexec/pocketds-panel-root; then
    sudo mkdir -p "$root_backup/usr/local/libexec"
    sudo cp -a /usr/local/libexec/pocketds-panel-root \
        "$root_backup/usr/local/libexec/pocketds-panel-root"
fi
sudo install -m 0755 "$repo_root/components/control-panel/pocketds-panel-root" \
    /usr/local/libexec/pocketds-panel-root
install_user "$repo_root/components/brightness/pocketds-brightness.py" \
    "$libexec/pocketds-brightness" 0755
install_user "$repo_root/components/brightness/pocketds-brightness.service" \
    "$HOME/.config/systemd/user/pocketds-brightness.service"

# Released/new images use the 60%/60% first-run default. Existing development
# machines pass --capture-current so installation never surprises the user.
"$libexec/pocketds-brightness" initialize $capture_arg

echo '[install-brightness] panel command router'
if sudo test -e /usr/local/bin/pocketds-panelctl && \
        ! sudo cmp -s "$repo_root/build/pocketds-panelctl" /usr/local/bin/pocketds-panelctl; then
    sudo mkdir -p "$root_backup/usr/local/bin"
    sudo cp -a /usr/local/bin/pocketds-panelctl \
        "$root_backup/usr/local/bin/pocketds-panelctl"
fi
sudo install -m 0755 "$repo_root/build/pocketds-panelctl" \
    /usr/local/bin/pocketds-panelctl

systemctl --user daemon-reload
systemctl --user enable pocketds-brightness.service
systemctl --user restart pocketds-brightness.service
echo "[install-brightness] complete; user backup: $backup"
echo "[install-brightness] root backup: $root_backup"

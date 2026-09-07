#!/usr/bin/env bash
# Narrow reversible daily-sleep opt-in. No boot image, modules or partitions.
set -Eeuo pipefail
[[ $EUID -ne 0 && $# -eq 3 && $2 == --confirm &&
   $3 == POCKETDS-DAILY-DEEP-20260905 && ( $1 == --enable || $1 == --disable ) ]] || {
    echo 'usage: install-daily-suspend.sh --enable|--disable --confirm POCKETDS-DAILY-DEEP-20260905' >&2
    exit 2
}
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
mode=$1
[[ $(id -un) == pocketds && $HOME == /home/pocketds ]] || exit 2
sudo -n true
[[ ! -L $HOME/.config/powerdevilrc && -f $HOME/.config/powerdevilrc ]] || exit 2
[[ ! -L $HOME/.local/libexec/pocketds/pocketds-lid-mode ]] || exit 2
[[ ! -e $HOME/.local/libexec/pocketds/pocketds-lid-mode ||
   -f $HOME/.local/libexec/pocketds/pocketds-lid-mode ]] || exit 2
[[ $(tr -d '\000' < /proc/device-tree/model) == 'AYANEO Pocket DS' ]] || exit 2
for config_tool in kreadconfig6 kwriteconfig6; do
    command -v "$config_tool" >/dev/null || {
        echo 'KDE configuration tools are required before changing sleep policy.' >&2; exit 1;
    }
done
if [[ $mode == --enable ]]; then
    sudo python3 "$repo_root/components/system/pocketds-daily-suspend.py" verify-deployment
    [[ $(busctl get-property org.freedesktop.login1 /org/freedesktop/login1 \
        org.freedesktop.login1.Manager LidClosed) == 'b false' ]] || {
        echo 'Open the lid before changing sleep policy.' >&2; exit 1;
    }
    [[ ! -e $HOME/.local/state/pocketds-linux-kit/light-standby.json ]] || {
        echo 'Resolve the old lid/profile transaction before enabling deep sleep.' >&2; exit 1;
    }
fi
stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
backup=/var/lib/pocketds-linux-kit/daily-suspend-$stamp
sudo install -d -m 0700 "$backup"
root_targets=(
    /usr/local/libexec/pocketds-daily-suspend
    /etc/systemd/system/systemd-suspend.service.d/30-pocketds-daily-suspend-guard.conf
    /etc/polkit-1/rules.d/89-pocketds-daily-suspend.rules
    /etc/systemd/sleep.conf.d/90-pocketds-daily-suspend.conf
    /etc/pocketds-linux-kit/daily-suspend.enabled
)
for target in "${root_targets[@]}"; do
    sudo test ! -L "$target" || exit 2
    if sudo test -e "$target"; then
        sudo install -d -m 0700 "$backup$(dirname "$target")"
        sudo cp -a -- "$target" "$backup$target"
    fi
done
sudo cp -a -- "$HOME/.config/powerdevilrc" "$backup/powerdevilrc.before"
sudo cp -a -- "$HOME/.local/libexec/pocketds/pocketds-light-standby" "$backup/light-standby.before"
if [[ -e $HOME/.local/libexec/pocketds/pocketds-lid-mode ]]; then
    sudo cp -a -- "$HOME/.local/libexec/pocketds/pocketds-lid-mode" "$backup/lid-mode.before"
fi

read_capability() {
    busctl --user --timeout=2s call org.freedesktop.PowerManagement \
        /org/freedesktop/PowerManagement org.freedesktop.PowerManagement CanSuspend
}

wait_capability() {
    local expected=$1 attempt
    for attempt in {1..30}; do
        [[ $(read_capability 2>/dev/null) != "$expected" ]] || return 0
        sleep 0.1
    done
    return 1
}

previous_capability=$(read_capability)
[[ $previous_capability == 'b false' || $previous_capability == 'b true' ]] || exit 1
previous_active=()
for unit in pocketds-light-standby.service plasma-powerdevil.service; do
    if systemctl --user is-active --quiet "$unit"; then previous_active+=(restart)
    else previous_active+=(stop); fi
done

restore_file() {
    local saved=$1 target=$2 saved_metadata target_metadata
    if sudo test -e "$saved"; then
        sudo cp -a -- "$saved" "$target" || return
        sudo cmp -s -- "$saved" "$target" || return
        saved_metadata=$(sudo stat -c '%a:%u:%g' "$saved") || return
        target_metadata=$(sudo stat -c '%a:%u:%g' "$target") || return
        [[ $saved_metadata == "$target_metadata" ]]
    else
        sudo rm -f -- "$target"
    fi
}

rollback_policy() {
    local target failed=0
    # Withdraw opt-in before changing any guard. Restore it only after all
    # preimages and the unit definitions are back; failure stays blocked.
    sudo rm -f -- "${root_targets[4]}" || return 1
    for target in "${root_targets[@]:0:4}"; do
        restore_file "$backup$target" "$target" || failed=1
    done
    restore_file "$backup/light-standby.before" "$HOME/.local/libexec/pocketds/pocketds-light-standby" || failed=1
    restore_file "$backup/lid-mode.before" "$HOME/.local/libexec/pocketds/pocketds-lid-mode" || failed=1
    restore_file "$backup/powerdevilrc.before" "$HOME/.config/powerdevilrc" || failed=1
    sudo systemctl daemon-reload || failed=1
    if ((failed == 0)); then
        restore_file "$backup${root_targets[4]}" "${root_targets[4]}" || failed=1
    fi
    systemctl --user "${previous_active[0]}" pocketds-light-standby.service || failed=1
    systemctl --user "${previous_active[1]}" plasma-powerdevil.service || failed=1
    wait_capability "$previous_capability" || failed=1
    if ((failed)); then
        sudo rm -f -- "${root_targets[4]}" || {
            echo 'CRITICAL: could not withdraw the daily-sleep opt-in.' >&2;
        }
        return 1
    fi
}

finish_transaction() {
    local status=$?
    trap - EXIT INT TERM
    ((status != 0)) || return 0
    if rollback_policy; then
        echo "Daily sleep change failed; previous policy restored. Backup: $backup" >&2
    else
        echo "CRITICAL: daily sleep rollback incomplete; inspect $backup before retrying." >&2
        status=70
    fi
    exit "$status"
}
trap finish_transaction EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

read_power_key() {
    kreadconfig6 --file "$HOME/.config/powerdevilrc" --group "$1" \
        --group SuspendAndShutdown --key "$2" --default __POCKETDS_UNSET__
}

write_power_key() {
    local profile=$1 key=$2 value=$3 saved
    kwriteconfig6 --file "$HOME/.config/powerdevilrc" --group "$profile" \
        --group SuspendAndShutdown --key "$key" --type int "$value" || return
    saved=$(read_power_key "$profile" "$key") || return
    [[ $saved == "$value" ]] || {
        echo "KDE did not save $profile/$key." >&2; return 1;
    }
}

prepare_power_policy() {
    local profile key value previous_default
    for profile in AC Battery LowBattery; do
        if [[ $mode == --enable ]]; then
            # PowerDevil 6.7.3: SleepMode::SuspendToRam = 1. Capability
            # enablement must not choose lid, power-button or idle actions.
            # Two missing keys have capability-dependent defaults; pin their
            # OLD effective values before CanSuspend changes underneath KDE.
            for key in LidAction AutoSuspendAction; do
                value=$(read_power_key "$profile" "$key") || return
                if [[ $value == __POCKETDS_UNSET__ ]]; then
                    previous_default=1
                    if [[ $previous_capability == 'b false' ]]; then
                        [[ $key == LidAction ]] && previous_default=64 || previous_default=0
                    fi
                    write_power_key "$profile" "$key" "$previous_default" || return
                fi
            done
            write_power_key "$profile" SleepMode 1 || return
        else
            # Sleep=1 includes RAM/hybrid/suspend-then-hibernate through
            # SleepMode. Other actions (including Hibernate=2) are unrelated
            # preferences and remain untouched; platform policy gates them.
            for key in LidAction PowerButtonAction PowerDownAction AutoSuspendAction; do
                value=$(read_power_key "$profile" "$key") || return
                if [[ $value =~ ^0*1$ ]]; then
                    write_power_key "$profile" "$key" 0 || return
                fi
            done
        fi
    done
}

# Deploy the user helper before any newer standby daemon can depend on it.
# A pre-upgrade system may lack this file; rollback then removes the new copy.
install -m 0755 "$repo_root/components/system/pocketds-lid-mode.py" \
    "$HOME/.local/libexec/pocketds/pocketds-lid-mode"

if [[ $mode == --enable ]]; then
    # Update the user configuration while the old capability is still in force.
    prepare_power_policy
    sudo install -m 0755 "$repo_root/components/system/pocketds-daily-suspend.py" "${root_targets[0]}"
    sudo install -d -m 0755 /etc/systemd/system/systemd-suspend.service.d /etc/pocketds-linux-kit
    sudo install -m 0644 "$repo_root/components/system/30-pocketds-daily-suspend-guard.conf" "${root_targets[1]}"
    sudo systemctl daemon-reload
    sudo install -m 0644 "$repo_root/components/system/89-pocketds-daily-suspend.rules" "${root_targets[2]}"
    # Publish the enablement only after the fail-closed precheck exists.
    sudo install -m 0644 "$repo_root/components/system/90-pocketds-daily-suspend.conf" "${root_targets[3]}"
    sudo install -m 0644 "$repo_root/components/system/daily-suspend.enabled" "${root_targets[4]}"
    install -m 0755 "$repo_root/components/light-standby/pocketds-light-standby.py" \
        "$HOME/.local/libexec/pocketds/pocketds-light-standby"
else
    # Recoverable moves: drop the opt-in first, so no new sleep is authorized.
    for target in "${root_targets[4]}" "${root_targets[3]}" "${root_targets[2]}" "${root_targets[1]}"; do
        if sudo test -e "$target"; then
            sudo install -d -m 0700 "$backup/disabled$(dirname "$target")"
            sudo mv -- "$target" "$backup/disabled$target"
        fi
    done
    sudo systemctl daemon-reload
    prepare_power_policy
fi
systemctl --user restart pocketds-light-standby.service
# Recreate capability discovery after polkit/sleep policy changes. Never restart
# logind, KWin or Plasma, and never request sleep from this installer.
if [[ $mode == --enable ]]; then
    # PowerDevil caches CanSuspend at startup. The preflight rejects blocking
    # sleep inhibitors (including operator maintenance locks); otherwise the
    # deliberately denied ignore-inhibit permission can make it cache "no".
    # Also wait for the asynchronously reloaded ordinary authorization rule.
    powerdevil_pid=$(systemctl --user show plasma-powerdevil.service -p MainPID --value)
    ready=0
    for attempt in {1..30}; do
        if sudo -n pkcheck --action-id org.freedesktop.login1.suspend \
            --process "$powerdevil_pid" >/dev/null 2>&1; then
            ready=1; break
        fi
        sleep 0.1
    done
    ((ready)) || { echo 'Active desktop sleep authorization did not become ready.' >&2; exit 1; }
fi
systemctl --user restart plasma-powerdevil.service
expected='b false'
[[ $mode != --enable ]] || expected='b true'
wait_capability "$expected" || { echo 'KDE did not confirm the requested sleep capability.' >&2; exit 1; }
printf 'Daily sleep policy %s; rollback preimages: %s\n' "$mode" "$backup"

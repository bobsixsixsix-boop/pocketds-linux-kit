#!/usr/bin/env bash
# Install only the user-owned Codex quota collector. Activation is explicit and
# verifies a fresh app-server result from the account already logged in locally.
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
activate=0

case "${1:-}" in
    '') ;;
    --activate) activate=1 ;;
    -h|--help)
        echo "usage: $0 [--activate]"
        exit 0
        ;;
    *)
        echo "usage: $0 [--activate]" >&2
        exit 2
        ;;
esac

if [[ $EUID -eq 0 ]]; then
    echo 'Run this script as the desktop user, not root.' >&2
    exit 1
fi
for command in chmod cmp cp install mkdir mktemp mv python3 rm systemctl; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
python3 "$repo_root/tests/codex-quota.py"

collector="$HOME/.local/bin/pocketds-codex-quota"
service="$HOME/.config/systemd/user/pocketds-codex-quota.service"
timer="$HOME/.config/systemd/user/pocketds-codex-quota.timer"
runtime_dir=${XDG_RUNTIME_DIR:-/run/user/$EUID}
quota_cache="${runtime_dir%/}/pocketds-codex-quota.json"
sources=(
    "$repo_root/components/codex-quota/pocketds-codex-quota"
    "$repo_root/components/codex-quota/pocketds-codex-quota.service"
    "$repo_root/components/codex-quota/pocketds-codex-quota.timer"
)
targets=("$collector" "$service" "$timer")
modes=(0755 0644 0644)
names=(collector service timer)
existed=(0 0 0)
cache_existed=0

timer_was_enabled=0
timer_was_active=0
service_was_active=0
if ((activate)); then
    if systemctl --user is-enabled --quiet pocketds-codex-quota.timer; then
        timer_was_enabled=1
    fi
    if systemctl --user is-active --quiet pocketds-codex-quota.timer; then
        timer_was_active=1
    fi
    if systemctl --user is-active --quiet pocketds-codex-quota.service; then
        service_was_active=1
    fi
fi

backup_root="$HOME/.local/state/pocketds-linux-kit/backups"
mkdir -p "$backup_root"
backup=$(mktemp -d "$backup_root/codex-quota.XXXXXX")
chmod 0700 "$backup"

for index in 0 1 2; do
    target=${targets[$index]}
    if [[ -L $target || ( -e $target && ! -f $target ) ]]; then
        echo "Unsafe quota target: ${names[$index]}" >&2
        exit 1
    fi
    if [[ -e $target ]]; then
        existed[$index]=1
        cp -a "$target" "$backup/${names[$index]}"
    fi
done
if ((activate)); then
    if [[ -L $quota_cache || ( -e $quota_cache && ! -f $quota_cache ) ]]; then
        echo 'Unsafe quota cache target' >&2
        exit 1
    fi
    if [[ -e $quota_cache ]]; then
        cache_existed=1
        cp -a "$quota_cache" "$backup/cache"
    fi
fi

transaction_active=1
rollback_failed=0
units_may_be_mutated=0
manager_reload_attempted=0
cache_may_be_mutated=0

record_rollback_failure() {
    local label=$1
    shift
    if ! "$@"; then
        echo "  rollback step failed: $label" >&2
        rollback_failed=1
    fi
}

restore_files() {
    local index target temporary
    for index in 2 1 0; do
        target=${targets[$index]}
        temporary="$target.pocketds-codex-rollback.$$"
        record_rollback_failure "remove install temporary ${names[$index]}" \
            rm -f "$target.pocketds-codex-new.$$"
        record_rollback_failure "remove temporary ${names[$index]}" rm -f "$temporary"
        if ((existed[$index])); then
            record_rollback_failure "copy ${names[$index]} preimage" \
                cp -a "$backup/${names[$index]}" "$temporary"
            if [[ -e $temporary ]]; then
                record_rollback_failure "restore ${names[$index]}" \
                    mv -f "$temporary" "$target"
            fi
        else
            record_rollback_failure "remove new ${names[$index]}" rm -f "$target"
        fi
        record_rollback_failure "clean temporary ${names[$index]}" rm -f "$temporary"
    done
}

quiesce_units() {
    record_rollback_failure "stop changed timer" \
        systemctl --user stop pocketds-codex-quota.timer
    record_rollback_failure "stop changed service" \
        systemctl --user stop pocketds-codex-quota.service
    # Remove the enablement link while the newly installed timer unit is still
    # present.  This also makes rollback from a first installation reliable.
    record_rollback_failure "disable changed timer" \
        systemctl --user disable pocketds-codex-quota.timer
}

restore_unit_state() {
    if ((timer_was_enabled)); then
        record_rollback_failure "re-enable timer" \
            systemctl --user enable pocketds-codex-quota.timer
    fi
    if ((timer_was_active)); then
        record_rollback_failure "restart prior timer" \
            systemctl --user start pocketds-codex-quota.timer
    fi
    if ((service_was_active)); then
        record_rollback_failure "restart prior service" \
            systemctl --user start pocketds-codex-quota.service
    fi
}

restore_cache() {
    local temporary="$quota_cache.pocketds-codex-rollback.$$"
    record_rollback_failure "remove cache temporary" rm -f "$temporary"
    if ((cache_existed)); then
        record_rollback_failure "copy quota cache preimage" \
            cp -a "$backup/cache" "$temporary"
        if [[ -e $temporary ]]; then
            record_rollback_failure "restore quota cache" \
                mv -f "$temporary" "$quota_cache"
        fi
    else
        record_rollback_failure "remove new quota cache" rm -f "$quota_cache"
    fi
    record_rollback_failure "clean cache temporary" rm -f "$temporary"
}

handle_failure() {
    local original_status=$1
    trap - ERR
    trap '' INT TERM HUP
    set +e
    if ((transaction_active)); then
        echo '[install-codex-quota] failed; restoring exact preimages' >&2
        if ((units_may_be_mutated)); then
            quiesce_units
        fi
        restore_files
        if ((activate && (manager_reload_attempted || units_may_be_mutated))); then
            record_rollback_failure "daemon reload" systemctl --user daemon-reload
        fi
        if ((activate && units_may_be_mutated)); then
            restore_unit_state
        fi
        if ((activate && cache_may_be_mutated)); then
            restore_cache
        fi
        if ((rollback_failed)); then
            echo '[install-codex-quota] ROLLBACK FAILED; manual recovery required' >&2
            exit 3
        fi
        echo '[install-codex-quota] rollback complete' >&2
    fi
    exit "$original_status"
}
trap 'handle_failure $?' ERR
trap 'handle_failure 130' INT
trap 'handle_failure 143' TERM
trap 'handle_failure 129' HUP

install_user_file() {
    local source=$1 target=$2 mode=$3 label=$4 temporary
    mkdir -p "$(dirname "$target")"
    temporary="$target.pocketds-codex-new.$$"
    rm -f "$temporary"
    install -m "$mode" "$source" "$temporary"
    mv -f "$temporary" "$target"
    if ((activate)) && [[ $label == collector ]] && \
            ((timer_was_active || service_was_active)); then
        # An already running timer/service can execute the replaced collector
        # before daemon-reload, so its cache is part of the transaction now.
        units_may_be_mutated=1
        cache_may_be_mutated=1
    fi
    echo "  user  $label"
}

for index in 0 1 2; do
    install_user_file "${sources[$index]}" "${targets[$index]}" \
        "${modes[$index]}" "${names[$index]}"
done

for index in 0 1 2; do
    cmp -s "${sources[$index]}" "${targets[$index]}"
done
[[ -x $collector && ! -x $service && ! -x $timer ]]

if ((activate)); then
    manager_reload_attempted=1
    systemctl --user daemon-reload
    units_may_be_mutated=1
    cache_may_be_mutated=1
    systemctl --user enable --now pocketds-codex-quota.timer
    systemctl --user restart pocketds-codex-quota.service
    python3 "$repo_root/scripts/pocketds-codex-quota-verify.py" --maximum-age 360
    echo '  active live app-server quota cache verified'
else
    echo '  installed but not activated; timer and cache were not touched'
fi

transaction_active=0
trap - ERR INT TERM HUP
echo "[install-codex-quota] complete; user backup: $backup"

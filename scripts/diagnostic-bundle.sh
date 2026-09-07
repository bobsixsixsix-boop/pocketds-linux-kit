#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
stamp=$(date +%Y%m%d-%H%M%S)
output_root="$repo_root/diagnostics"
umask 077
if [[ -L $output_root ]]; then
    echo 'Refusing a symlinked diagnostics directory.' >&2
    exit 1
fi
mkdir -p "$output_root"
if [[ ! -d $output_root || -L $output_root ]]; then
    echo 'Refusing an unsafe diagnostics directory.' >&2
    exit 1
fi
chmod 0700 "$output_root"

work_dir=$(mktemp -d "$output_root/.pocketds-diagnostic-$stamp.XXXXXXXX")
archive_tmp=''
cleanup() {
    if [[ -n $archive_tmp && -f $archive_tmp ]]; then
        rm -f -- "$archive_tmp"
    fi
    if [[ -n $work_dir && -d $work_dir ]]; then
        rm -rf -- "$work_dir"
    fi
}
trap cleanup EXIT

bundle_name=${work_dir##*/}
bundle_name=${bundle_name#.}
bundle_dir="$output_root/$bundle_name"
archive="$bundle_dir.tar.gz"

bounded_capture() {
    local destination=$1
    shift
    (
        ulimit -f 8192
        LC_ALL=C timeout --signal=TERM --kill-after=2s 20s "$@"
    ) >"$destination" 2>&1 || true
}

capture() {
    local name=$1
    shift
    bounded_capture "$work_dir/$name" "$@"
}

capture_shell() {
    local name=$1 script=$2
    bounded_capture "$work_dir/$name" bash -c "$script"
}

{
    echo "created_at=$(date --iso-8601=seconds 2>/dev/null || date)"
    echo "git_head=$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || echo unknown)"
    printf 'git_change_count='
    git -C "$repo_root" status --porcelain 2>/dev/null | wc -l || true
    echo "kernel=$(uname -srmo)"
    sed -n 's/^\(NAME\|VERSION\|VARIANT\|BUILD_ID\)=/os_\1=/p' /etc/os-release 2>/dev/null || true
} >"$work_dir/manifest.txt"

capture system-failed.txt systemctl --failed --no-pager
capture user-failed.txt systemctl --user --failed --no-pager
capture inhibitors.txt systemd-inhibit --list --no-pager
capture panel-status.json /usr/local/bin/pocketds-panelctl status
capture brightness-status.json \
    "$HOME/.local/libexec/pocketds/pocketds-brightness" status
capture packages.txt rpm -q plasma-workspace kwin inputplumber tuned-ppd upower \
    pocketds-userspace pocketds-steam onboard

capture_shell power.txt '
    printf "state="; cat /sys/power/state
    printf "mem_sleep="; cat /sys/power/mem_sleep
    printf "pm_async="; cat /sys/power/pm_async 2>/dev/null || true
    printf "wakeup_count="; cat /sys/power/wakeup_count
'

capture_shell displays-backlights.txt '
    for d in /sys/class/drm/card*-DSI-*; do
        [ -d "$d" ] || continue
        echo "--- $d"
        cat "$d/status" 2>/dev/null
        cat "$d/modes" 2>/dev/null
    done
    for d in /sys/class/backlight/*; do
        echo "--- $d"
        printf "brightness="; cat "$d/brightness" 2>/dev/null
        printf "max="; cat "$d/max_brightness" 2>/dev/null
    done
'

capture_shell battery.txt '
    for d in /sys/class/power_supply/*; do
        echo "--- $d"
        for f in type status capacity voltage_now current_now power_now temp health online; do
            [ -r "$d/$f" ] || continue
            printf "%s=" "$f"; cat "$d/$f"
        done
    done
'

capture_shell gpu-devfreq.txt '
    root=/sys/devices/platform/soc@0/3d00000.gpu/devfreq/3d00000.gpu
    for f in available_frequencies cur_freq governor available_governors \
             busy_time total_time gpu_busy_percentage; do
        [ -r "$root/$f" ] || continue
        printf "%s=" "$f"; cat "$root/$f"
    done
'

capture services.txt systemctl show --no-pager \
    --property=Id,LoadState,ActiveState,SubState,UnitFileState,Result,ExecMainCode,ExecMainStatus,NRestarts,MemoryCurrent,CPUUsageNSec \
    pocketds-fancontrol.service inputplumber.service tuned.service \
    NetworkManager-wait-online.service
capture user-services.txt systemctl --user show --no-pager \
    --property=Id,LoadState,ActiveState,SubState,UnitFileState,Result,ExecMainCode,ExecMainStatus,NRestarts,MemoryCurrent,CPUUsageNSec \
    pocketds-keyboard.service \
    pocketds-gpu-telemetry.service pocketds-brightness.service \
    plasma-plasmashell.service

capture_shell kernel-power-gpu.log \
    'journalctl -b -k --no-pager | grep -Ei "suspend|resume|wakeup|drm|msm|adreno|gpu|fence|goodix|i2c|lid" | tail -2000'
capture_shell desktop.log \
    'journalctl --user -b --no-pager -u plasma-plasmashell.service -u pocketds-gpu-telemetry.service -u pocketds-brightness.service -u plasma-powerdevil.service | grep -Ei "crash|segfault|fail|error|drm|egl|gpu|fence|wayland|xwayland" | tail -1000'

# The collector is a fixed allowlist. It intentionally excludes account and
# network configuration, browser/Steam/Codex profiles, keyboard/ASR logs and
# game-library paths. Apply the tested redactor before hashing or archiving.
while IFS= read -r -d '' file; do
    "$repo_root/scripts/pocketds-diagnostic-redact.py" "$file"
done < <(find "$work_dir" -type f -print0)

if find "$work_dir" \( -type l -o -type f -links +1 -o -type f -size +8388608c \) \
        -print -quit | grep -q .; then
    echo 'Refusing unsafe or oversized diagnostic content.' >&2
    exit 1
fi
if (( $(du -sk "$work_dir" | awk '{print $1}') > 32768 )); then
    echo 'Refusing a diagnostic bundle larger than 32 MiB.' >&2
    exit 1
fi

(
    cd "$work_dir"
    find . -type f ! -name contents.sha256 -print0 | sort -z | \
        xargs -0 -r sha256sum
) >"$work_dir/contents.sha256"

if [[ -e $bundle_dir || -L $bundle_dir ]]; then
    echo 'Refusing to replace an existing diagnostic directory.' >&2
    exit 1
fi
mv -T -- "$work_dir" "$bundle_dir"
work_dir=''

archive_tmp=$(mktemp "$output_root/.pocketds-archive.XXXXXXXX")
tar -C "$output_root" --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime=@0 -czf "$archive_tmp" "$bundle_name"
if [[ -e $archive || -L $archive ]]; then
    echo 'Refusing to replace an existing diagnostic archive.' >&2
    exit 1
fi
mv -T -- "$archive_tmp" "$archive"
archive_tmp=''
if [[ -e $archive.sha256 || -L $archive.sha256 ]]; then
    echo 'Refusing to replace an existing diagnostic checksum.' >&2
    exit 1
fi
set -o noclobber
(
    cd "$output_root"
    sha256sum "$(basename "$archive")"
) >"$archive.sha256"
echo "Diagnostic bundle: $archive"
echo 'It remains local and is not uploaded automatically.'

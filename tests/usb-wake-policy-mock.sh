#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
helper="$repo_root/components/system/pocketds-usb-wake-policy"
rule="$repo_root/components/system/71-pocketds-usb-wakeup.rules"
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT

make_controller() {
    local root=$1 slot=$2 vendor=$3 device=$4 driver=$5 state=$6 path
    path="$root/devices/$slot"
    mkdir -p "$path/power" "$root/drivers/$driver"
    printf '%s\n' "$vendor" >"$path/vendor"
    printf '%s\n' "$device" >"$path/device"
    printf '%s\n' "$state" >"$path/power/wakeup"
    ln -s "$root/drivers/$driver" "$path/driver"
}

mkdir -p "$fixture/valid/devices"
make_controller "$fixture/valid" 0001:01:00.0 0x1912 0x0014 xhci-pci-renesas enabled
"$helper" --test-root "$fixture/valid/devices" >/dev/null
[[ $(<"$fixture/valid/devices/0001:01:00.0/power/wakeup") == disabled ]]
"$helper" --test-root "$fixture/valid/devices" >/dev/null

mkdir -p "$fixture/wrong/devices"
make_controller "$fixture/wrong" 0000:01:00.0 0x1912 0x0015 xhci-pci-renesas enabled
if "$helper" --test-root "$fixture/wrong/devices" >/dev/null 2>&1; then
    echo '[FAIL] wrong PCI identity was accepted' >&2
    exit 1
fi

mkdir -p "$fixture/wrong-driver/devices"
make_controller "$fixture/wrong-driver" 0001:01:00.0 0x1912 0x0014 xhci-pci enabled
if "$helper" --test-root "$fixture/wrong-driver/devices" >/dev/null 2>&1; then
    echo '[FAIL] wrong bound driver was accepted' >&2
    exit 1
fi
[[ $(<"$fixture/wrong-driver/devices/0001:01:00.0/power/wakeup") == enabled ]]

mkdir -p "$fixture/unbound/devices/0001:01:00.0/power"
printf '%s\n' 0x1912 >"$fixture/unbound/devices/0001:01:00.0/vendor"
printf '%s\n' 0x0014 >"$fixture/unbound/devices/0001:01:00.0/device"
printf '%s\n' enabled >"$fixture/unbound/devices/0001:01:00.0/power/wakeup"
if "$helper" --test-root "$fixture/unbound/devices" >/dev/null 2>&1; then
    echo '[FAIL] unbound controller was accepted' >&2
    exit 1
fi
[[ $(<"$fixture/unbound/devices/0001:01:00.0/power/wakeup") == enabled ]]

mkdir -p "$fixture/unexpected/devices"
make_controller "$fixture/unexpected" 0001:01:00.0 0x1912 0x0014 xhci-pci-renesas auto
if "$helper" --test-root "$fixture/unexpected/devices" >/dev/null 2>&1; then
    echo '[FAIL] unexpected wake state was accepted' >&2
    exit 1
fi
[[ $(<"$fixture/unexpected/devices/0001:01:00.0/power/wakeup") == auto ]]

mkdir -p "$fixture/duplicate/devices"
make_controller "$fixture/duplicate" 0001:01:00.0 0x1912 0x0014 xhci-pci-renesas enabled
make_controller "$fixture/duplicate" 0002:01:00.0 0x1912 0x0014 xhci-pci-renesas enabled
if "$helper" --test-root "$fixture/duplicate/devices" >/dev/null 2>&1; then
    echo '[FAIL] ambiguous PCI identity was accepted' >&2
    exit 1
fi
[[ $(<"$fixture/duplicate/devices/0001:01:00.0/power/wakeup") == enabled ]]
[[ $(<"$fixture/duplicate/devices/0002:01:00.0/power/wakeup") == enabled ]]

expected_rule='ACTION=="bind", SUBSYSTEM=="pci", DRIVER=="xhci-pci-renesas", ATTR{vendor}=="0x1912", ATTR{device}=="0x0014", TEST=="power/wakeup", ATTR{power/wakeup}="disabled"'
[[ $(tail -n 1 "$rule") == "$expected_rule" ]]
if grep -Eq 'ACTION=="add"|RUN\{|RUN\+=|PROGRAM=|IMPORT\{' "$rule"; then
    echo '[FAIL] wake rule contains a pre-probe or executable path' >&2
    exit 1
fi

echo '  [OK] exact identity/driver/state, ambiguity rejection, idempotence, and bind rule'

#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
output=${1:-"$repo_root/build/pocketds-gamescope-observer"}
build_dir=$(mktemp -d "${TMPDIR:-/tmp}/pocketds-gamescope-observer.XXXXXXXX")
trap 'rm -rf -- "$build_dir"' EXIT HUP INT TERM

for command in cc pkg-config wayland-scanner; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing build dependency: $command" >&2
        exit 1
    }
done
pkg-config --exists wayland-client xcb || {
    echo 'Missing wayland-client or xcb development files.' >&2
    exit 1
}

protocol="$repo_root/components/game-runtime/gamescope-control.xml"
source="$repo_root/components/game-runtime/pocketds-gamescope-observer.c"
wayland-scanner client-header \
    "$protocol" "$build_dir/gamescope-control-client-protocol.h"
wayland-scanner private-code \
    "$protocol" "$build_dir/gamescope-control-protocol.c"

mkdir -p "$(dirname "$output")"
cc -std=c11 -O2 -Wall -Wextra -Werror -s -Wl,--build-id=none \
    -I"$build_dir" \
    "$source" "$build_dir/gamescope-control-protocol.c" \
    $(pkg-config --cflags --libs wayland-client xcb) \
    -o "$output"
chmod 0755 "$output"

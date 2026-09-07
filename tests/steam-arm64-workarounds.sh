#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
helper="$repo_root/components/steam/pocketds-steam-arm64-workarounds"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
steam_root="$fixture/Steam"
game_dir="$steam_root/steamapps/common/Stardew Valley"
mkdir -p "$game_dir"

POCKETDS_STEAM_ROOT="$steam_root" "$helper"

printf 'original\n' >"$game_dir/ucrtbase.dll"
POCKETDS_STEAM_ROOT="$steam_root" "$helper"
[[ ! -e $game_dir/ucrtbase.dll ]]
[[ $(<"$game_dir/ucrtbase.dll.pocketds-disabled") == original ]]

# A later Steam update replaces the quarantined copy with the new one.
printf 'updated\n' >"$game_dir/ucrtbase.dll"
POCKETDS_STEAM_ROOT="$steam_root" "$helper"
[[ ! -e $game_dir/ucrtbase.dll ]]
[[ $(<"$game_dir/ucrtbase.dll.pocketds-disabled") == updated ]]

ln -s /etc/passwd "$game_dir/ucrtbase.dll"
set +e
POCKETDS_STEAM_ROOT="$steam_root" "$helper" >/dev/null 2>&1
status=$?
set -e
[[ $status != 0 ]]
[[ -L $game_dir/ucrtbase.dll ]]

echo '  [OK] ARM64 game workarounds are repeatable and reject unsafe files'

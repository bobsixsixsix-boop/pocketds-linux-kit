#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
source_file="$repo_root/components/moonlight/pocketds-moonlight-fps-hook.c"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
mkdir -m 0700 "$fixture/private"

cc -std=c11 -O2 -fPIC -shared -Wall -Wextra -Werror \
    -o "$fixture/libfake-sdl.so" \
    "$repo_root/tests/moonlight-fps-hook-fake-sdl.c"
cc -std=c11 -O2 -Wall -Wextra -Werror \
    -o "$fixture/driver" "$repo_root/tests/moonlight-fps-hook-driver.c" \
    -L"$fixture" -lfake-sdl -Wl,-rpath,"$fixture"
cc -std=c11 -O2 -fPIC -shared -fvisibility=hidden \
    -Wall -Wextra -Werror \
    -DPOCKETDS_FPS_SAMPLE_NS=50000000LL \
    -DPOCKETDS_FPS_MIN_FRAMES=8U \
    -DPOCKETDS_FPS_MIN_HZ=20.0 \
    -o "$fixture/hook.so" "$source_file" -ldl -pthread

run_driver() {
    local output_path=$1
    shift
    POCKETDS_MOONLIGHT_FPS_FILE="$output_path" \
        LD_PRELOAD="$fixture/hook.so" "$fixture/driver" "$@"
}

single_output="$fixture/private/single"
[[ $(run_driver "$single_output" 1 0) == 1 ]]
[[ ! -e $single_output ]]

burst_output="$fixture/private/one-window-burst"
[[ $(run_driver "$burst_output" 18 4000) == 18 ]]
[[ ! -e $burst_output ]]

low_output="$fixture/private/low"
[[ $(run_driver "$low_output" 6 30000) == 6 ]]
[[ ! -e $low_output ]]

active_output="$fixture/private/fps"
[[ $(run_driver "$active_output" 120 4000) == 120 ]]
python3 - "$active_output" <<'PY'
from pathlib import Path
import re
import sys

payload = Path(sys.argv[1]).read_bytes()
assert re.fullmatch(rb"[0-9]+\.[0-9]\n", payload), payload
value = float(payload)
assert 100.0 <= value <= 400.0, value
PY
[[ $(stat -c '%a' "$active_output") == 600 ]]
if find "$fixture/private" -maxdepth 1 -name '*.tmp' -print -quit | grep -q .; then
    echo 'temporary telemetry file leaked' >&2
    exit 1
fi

mkdir "$fixture/public"
chmod 0755 "$fixture/public"
unsafe_output="$fixture/public/fps"
[[ $(run_driver "$unsafe_output" 120 4000) == 120 ]]
[[ ! -e $unsafe_output ]]

relative_output=relative-fps
(
    cd "$fixture/private"
    [[ $(run_driver "$relative_output" 120 4000) == 120 ]]
    [[ ! -e $relative_output ]]
)

echo '  [OK] Moonlight hook publishes active swaps atomically and ignores idle/unsafe paths'

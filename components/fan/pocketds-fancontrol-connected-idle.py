#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# /usr/sbin/pocketds-fancontrol
#
# Userspace PWM fan controller for the AYANEO Pocket DS (SM8550).
#
# Background: the QCS8550 thermal-zone DT used to bind every cpuss/gpuss
# trip-point to <&pwm_fan> via cooling-maps, letting the kernel ramp the
# fan automatically. We dropped those maps in
# qcs8550-ayaneo-pocket-common.dtsi (mirroring ROCKNIX 524d0c9) so the
# fan now sits idle unless something writes its hwmon pwm1 -- which is
# us.
#
# We discover the pwm-fan hwmon entry, track the hottest cpu*/gpuss*
# die sensor, and step pwm1 according to a profile. The hotspot (not an
# average) drives the curve: the SoC's 37 zones include modem/CDSP/
# camera/aoss sensors that read near-ambient (the remoteprocs are
# deleted on this board), and averaging them kept the fan at 60 % while
# cpu7 sat at 96 °C. Profile is read from /etc/pocketds-fancontrol/profile
# (one of: auto, quiet, moderate, aggressive, custom, off) and reloaded
# whenever the file's mtime changes -- no SIGHUP plumbing needed,
# `install -m 0664` lets the indicator app rewrite it via pkexec/sudo.
#
# Custom curves live in /etc/pocketds-fancontrol/custom.conf as
# `tempC=pwm` lines (e.g. `60=80`).

import errno
import glob
import os
import signal
import sys
import time

PROFILE_FILE = "/etc/pocketds-fancontrol/profile"
CUSTOM_FILE = "/etc/pocketds-fancontrol/custom.conf"
STATE_DIR = "/run/pocketds-fancontrol"
STATE_FILE = os.path.join(STATE_DIR, "state")

POLL_INTERVAL = 1.5
PROFILE_RECHECK_INTERVAL = 5.0
BACKLIGHT_POWER_PATHS = (
    "/sys/class/backlight/ae94000.dsi.0/bl_power",
    "/sys/class/backlight/sy7758-backlight/bl_power",
)
SCREEN_OFF_FAN_STOP_C = 50.0
SCREEN_OFF_FAN_RESUME_C = 60.0
SCREEN_OFF_ENTER_TICKS = 2

# Only the die sensors that track compute heat drive the fan.
INCLUDE_ZONE_PREFIXES = ("cpu", "gpu")

# The original controller jumped between eight coarse PWM steps.  On this
# small blower a single 51 -> 102 jump is very audible, so use the same
# control strategy as Armada's SM8550 power daemon: interpolate between
# curve points, smooth the hottest sensors, quantize only lightly and rate
# limit both acceleration and deceleration.
TEMP_SMOOTHING = 0.50
PWM_QUANTUM = 8
MIN_PWM = 51
MAX_PWM = 255
RAMP_UP_PER_TICK = 18
RAMP_DOWN_PER_TICK = 3

# Curves are (temperature C, pwm 0..255), sorted from cool to hot.  Keeping
# the fan at its proven 20% floor avoids the much more annoying stop/start
# surge around idle.  These are Armada's current SM8550 defaults.
PROFILES = {
    "quiet": (
        (0, 51), (65, 51), (76, 77), (82, 102),
        (88, 153), (94, 204), (98, 255),
    ),
    "moderate": (
        (0, 51), (55, 51), (72, 77), (78, 102),
        (84, 153), (90, 204), (96, 255),
    ),
    "auto": (
        (0, 51), (55, 51), (72, 77), (78, 102),
        (84, 153), (90, 204), (96, 255),
    ),
    "aggressive": (
        (0, 51), (45, 51), (66, 77), (74, 102),
        (80, 153), (85, 204), (90, 255),
    ),
}

# A smoothed average makes the fan pleasant; the instantaneous hotspot is
# still authoritative for safety.  A single die reaching these levels can
# never be hidden by averaging or by selecting the quiet/off profile.
SAFETY_FLOORS = ((95, 255), (88, 204), (84, 153))


def log(msg):
    print(f"pocketds-fancontrol: {msg}", flush=True)


def find_pwm_path():
    for pwm in sorted(glob.glob("/sys/class/hwmon/hwmon*/pwm1")):
        # The pwm-fan driver exposes pwm1 only.
        try:
            with open(os.path.join(os.path.dirname(pwm), "name")) as f:
                name = f.read().strip()
        except OSError:
            name = ""
        if name in ("pwmfan", "pwm-fan") or os.path.exists(pwm + "_enable"):
            return pwm
    # Fall back to the first pwm1 we find.
    matches = sorted(glob.glob("/sys/class/hwmon/hwmon*/pwm1"))
    return matches[0] if matches else None


def find_temp_paths():
    paths = []
    for zone in sorted(glob.glob("/sys/devices/virtual/thermal/thermal_zone*")):
        try:
            with open(os.path.join(zone, "type")) as f:
                ztype = f.read().strip()
        except OSError:
            continue
        if not ztype.startswith(INCLUDE_ZONE_PREFIXES):
            continue
        tpath = os.path.join(zone, "temp")
        if os.path.exists(tpath):
            paths.append(tpath)
    return paths


def read_temps_c(paths):
    values = []
    for p in paths:
        try:
            with open(p) as f:
                v = int(f.read().strip())
        except (OSError, ValueError):
            continue
        values.append(v / 1000.0)
    return values


def internal_displays_blanked():
    """Return true only when both fixed internal DPMS states are readable and off."""
    states = []
    for path in BACKLIGHT_POWER_PATHS:
        try:
            with open(path) as stream:
                states.append(int(stream.read().strip()))
        except (OSError, ValueError):
            return False
    return all(state != 0 for state in states)


def next_screen_off_fan_state(stopped, ready_ticks, temps_valid, backlights_off, hot_c):
    """Debounce entry, but leave the stopped state immediately on unsafe input."""
    if stopped:
        if not temps_valid or not backlights_off or hot_c >= SCREEN_OFF_FAN_RESUME_C:
            return False, 0
        return True, ready_ticks
    if not temps_valid or not backlights_off or hot_c > SCREEN_OFF_FAN_STOP_C:
        return False, 0
    ready_ticks += 1
    return ready_ticks >= SCREEN_OFF_ENTER_TICKS, ready_ticks


def parse_custom_conf(path):
    # Each line: tempC=pwm (0-255). Sorted ascending by temp; the first
    # entry whose temp >= current applies.
    table = []
    try:
        with open(path) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                if "=" not in line:
                    continue
                t_str, p_str = line.split("=", 1)
                try:
                    t = int(t_str.strip())
                    p = int(p_str.strip())
                except ValueError:
                    continue
                p = max(0, min(255, p))
                table.append((t, p))
    except OSError:
        return None
    if not table:
        return None
    table.sort()
    return table


def read_profile():
    try:
        with open(PROFILE_FILE) as f:
            v = f.read().strip().lower()
    except OSError:
        v = ""
    if v not in PROFILES and v not in ("custom", "off"):
        v = "moderate"
    return v


def quantize_pwm(value):
    value = round(value / PWM_QUANTUM) * PWM_QUANTUM
    return max(MIN_PWM, min(MAX_PWM, int(value)))


def pwm_for_curve(temp_c, table):
    """Linearly interpolate PWM between adjacent temperature points."""
    table = sorted(table)
    if temp_c <= table[0][0]:
        return quantize_pwm(table[0][1])
    if temp_c >= table[-1][0]:
        return quantize_pwm(table[-1][1])
    for (low_t, low_pwm), (high_t, high_pwm) in zip(table, table[1:]):
        if low_t <= temp_c <= high_t:
            span = high_t - low_t
            if span <= 0:
                return quantize_pwm(high_pwm)
            ratio = (temp_c - low_t) / span
            return quantize_pwm(low_pwm + (high_pwm - low_pwm) * ratio)
    return MIN_PWM


def safety_floor(hotspot_c):
    for threshold, pwm in SAFETY_FLOORS:
        if hotspot_c >= threshold:
            return pwm
    return 0


def approach_pwm(current, target):
    if target > current:
        return min(target, current + RAMP_UP_PER_TICK)
    return max(target, current - RAMP_DOWN_PER_TICK)


def output_pwm(last_pwm, target, hot_c, temps_valid, screen_off_fan_stopped):
    """Choose the hardware PWM with immediate safety floors and reliable restart."""
    floor = safety_floor(hot_c)
    target = max(target, floor)
    if hot_c >= 95:
        pwm = 255
    elif not temps_valid:
        pwm = max(204, last_pwm)
    elif screen_off_fan_stopped:
        pwm = floor
    else:
        pwm = approach_pwm(last_pwm, target)
        if target > 0:
            # This blower does not reliably start below the proven 20% floor.
            pwm = max(MIN_PWM, pwm)
        pwm = max(pwm, floor)
    return max(0, min(MAX_PWM, int(pwm)))


def stopped_on_hardware(screen_off_fan_stopped, actual_pwm):
    return screen_off_fan_stopped and actual_pwm == 0


def write_atomic(path, content):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            f.write(content)
        os.replace(tmp, path)
    except OSError as e:
        log(f"write {path}: {e}")


def write_pwm(pwm_path, value):
    try:
        with open(pwm_path, "w") as f:
            f.write(str(value))
    except OSError as e:
        log(f"write {pwm_path}={value}: {e}")
        return False
    return True


def enable_pwm(pwm_path):
    en = pwm_path + "_enable"
    if not os.path.exists(en):
        return
    try:
        with open(en) as f:
            cur = f.read().strip()
    except OSError:
        cur = ""
    if cur != "1":
        try:
            with open(en, "w") as f:
                f.write("1")
        except OSError as e:
            log(f"enable {en}: {e}")


def disable_pwm(pwm_path):
    en = pwm_path + "_enable"
    if not os.path.exists(en):
        return
    try:
        with open(en, "w") as f:
            f.write("0")
    except OSError:
        pass


def main():
    pwm_path = find_pwm_path()
    if not pwm_path:
        log("no pwm1 hwmon entry found; nothing to control")
        return 1
    log(f"pwm path: {pwm_path}")

    temp_paths = find_temp_paths()
    if not temp_paths:
        log("no thermal zones found; nothing to read")
        return 1
    log(f"thermal zones: {len(temp_paths)} sensors")

    enable_pwm(pwm_path)

    os.makedirs(STATE_DIR, exist_ok=True)
    os.chmod(STATE_DIR, 0o755)

    cleanup = {"done": False}

    def _shutdown(*_):
        if cleanup["done"]:
            return
        cleanup["done"] = True
        log("shutting down; leaving fan at a safe fixed speed")
        # The Pocket DS device tree does not currently provide a kernel fan
        # curve, so disabling manual mode here would stop cooling entirely.
        # Leave a safe value latched until systemd restarts us or power dies.
        write_pwm(pwm_path, 128)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGHUP, lambda *_: None)  # reread happens on its own

    profile = read_profile()
    custom_table = parse_custom_conf(CUSTOM_FILE) if profile == "custom" else None
    profile_mtime = _mtime(PROFILE_FILE)
    custom_mtime = _mtime(CUSTOM_FILE)
    last_profile_check = 0.0
    try:
        with open(pwm_path) as f:
            last_pwm = int(f.read().strip())
    except (OSError, ValueError):
        last_pwm = MIN_PWM
    # At cold boot the hwmon value may be zero.  Start immediately at the
    # proven stable floor; ramp limiting is for audible transitions, not for
    # delaying basic cooling after boot.
    last_pwm = max(MIN_PWM, min(MAX_PWM, last_pwm))
    write_pwm(pwm_path, last_pwm)
    smoothed_temp = 0.0
    screen_off_fan_stopped = False
    screen_off_ready_ticks = 0

    log(f"profile={profile}")

    while True:
        now = time.monotonic()

        if now - last_profile_check >= PROFILE_RECHECK_INTERVAL:
            last_profile_check = now
            new_profile_mtime = _mtime(PROFILE_FILE)
            new_custom_mtime = _mtime(CUSTOM_FILE)
            if new_profile_mtime != profile_mtime:
                profile_mtime = new_profile_mtime
                profile = read_profile()
                log(f"profile changed → {profile}")
            if profile == "custom" and new_custom_mtime != custom_mtime:
                custom_mtime = new_custom_mtime
                custom_table = parse_custom_conf(CUSTOM_FILE)
                log(f"custom curve reloaded ({len(custom_table or [])} pts)")

        temperatures = sorted(read_temps_c(temp_paths), reverse=True)
        temps_valid = bool(temperatures)
        if not temps_valid:
            # Missing sensors must fail toward cooling, never toward silence.
            hot_c = 0.0
            control_temp = 0.0
        else:
            hot_c = temperatures[0]
            hottest_three = temperatures[:3]
            control_temp = sum(hottest_three) / len(hottest_three)
            smoothed_temp = (
                control_temp if not smoothed_temp else
                smoothed_temp * TEMP_SMOOTHING +
                control_temp * (1.0 - TEMP_SMOOTHING)
            )

        backlights_off = internal_displays_blanked()
        screen_off_fan_stopped, screen_off_ready_ticks = next_screen_off_fan_state(
            screen_off_fan_stopped, screen_off_ready_ticks,
            temps_valid, backlights_off, hot_c,
        )

        if not temps_valid:
            target = 204
        elif profile == "off":
            target = 0
        elif profile == "custom" and custom_table:
            target = pwm_for_curve(smoothed_temp, custom_table)
        else:
            # "custom" with a missing/empty curve falls back to moderate.
            curve = PROFILES.get(profile, PROFILES["moderate"])
            target = pwm_for_curve(smoothed_temp, curve)

        if screen_off_fan_stopped:
            target = 0
        target = max(target, safety_floor(hot_c))
        pwm = output_pwm(last_pwm, target, hot_c, temps_valid, screen_off_fan_stopped)

        if pwm != last_pwm:
            if write_pwm(pwm_path, pwm):
                last_pwm = pwm

        fan_stopped = stopped_on_hardware(screen_off_fan_stopped, last_pwm)
        write_atomic(
            STATE_FILE,
            f"profile={profile}\n"
            f"temp_c={round(smoothed_temp)}\n"
            f"hotspot_c={round(hot_c)}\n"
            f"target_pwm={target}\n"
            f"pwm={last_pwm}\n"
            f"internal_displays_blanked={1 if backlights_off else 0}\n"
            f"screen_off_fan_stopped={1 if fan_stopped else 0}\n",
        )

        time.sleep(POLL_INTERVAL)


def _mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)

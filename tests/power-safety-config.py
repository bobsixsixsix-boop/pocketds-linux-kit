#!/usr/bin/env python3
"""Static checks for the temporary Pocket DS suspend/lock-screen safety policy."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_kconfig(path: Path) -> dict[str, dict[str, str]]:
    groups: dict[str, dict[str, str]] = {}
    current = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            groups.setdefault(current, {})
            continue
        if "=" not in line or not current:
            raise SystemExit(f"[FAIL] malformed config line in {path}: {raw_line}")
        key, value = line.split("=", 1)
        groups[current][key.strip()] = value.strip()
    return groups


def require(config: dict[str, dict[str, str]], group: str, key: str, value: str) -> None:
    actual = config.get(group, {}).get(key)
    if actual != value:
        raise SystemExit(f"[FAIL] {group}/{key}: expected {value!r}, found {actual!r}")


sleep = read_kconfig(ROOT / "components/system/80-pocketds-sleep.conf")
require(sleep, "Sleep", "SuspendState", "mem")
require(sleep, "Sleep", "AllowSuspend", "no")
require(sleep, "Sleep", "MemorySleepMode", "deep")

logind = read_kconfig(ROOT / "components/system/80-pocketds-lid-safety.conf")
for key in (
    "HandleLidSwitch",
    "HandleLidSwitchExternalPower",
    "HandleLidSwitchDocked",
    "HandlePowerKey",
):
    require(logind, "Login", key, "ignore")

upower = read_kconfig(ROOT / "components/system/90-pocketds-critical-power.conf")
require(upower, "UPower", "CriticalPowerAction", "PowerOff")

locker = read_kconfig(ROOT / "components/system/kscreenlockerrc")
require(locker, "Daemon", "Autolock", "false")
require(locker, "Daemon", "LockOnResume", "false")

powerdevil = read_kconfig(ROOT / "components/system/powerdevilrc")
idle_timeouts = {"AC": "300", "Battery": "300", "LowBattery": "120"}
for profile, idle_timeout in idle_timeouts.items():
    display_section = f"{profile}][Display"
    require(powerdevil, display_section, "DimDisplayWhenIdle", "false")
    require(powerdevil, display_section, "LockBeforeTurnOffDisplay", "false")
    require(powerdevil, display_section, "TurnOffDisplayWhenIdle", "true")
    require(
        powerdevil,
        display_section,
        "TurnOffDisplayIdleTimeoutSec",
        idle_timeout,
    )
    require(
        powerdevil,
        display_section,
        "TurnOffDisplayIdleTimeoutWhenLockedSec",
        "60",
    )

    section = f"{profile}][SuspendAndShutdown"
    for key in ("AutoSuspendAction", "PowerDownAction"):
        require(powerdevil, section, key, "0")
    require(powerdevil, section, "LidAction", "0")
    require(powerdevil, section, "PowerButtonAction", "64")
    require(powerdevil, section, "InhibitLidActionWhenExternalMonitorPresent", "false")

installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
for managed_file in (
    "80-pocketds-sleep.conf",
    "80-pocketds-lid-safety.conf",
    "90-pocketds-critical-power.conf",
    "90-pocketds-deep-suspend.rules",
):
    if managed_file not in installer:
        raise SystemExit(f"[FAIL] installer does not manage {managed_file}")
if "VirtualKeyboardLoader.qml" in installer:
    raise SystemExit("[FAIL] unverified lock-screen QML must not overwrite Plasma")
if "critical-action:[[:space:]]*//p') != PowerOff" not in installer:
    raise SystemExit("[FAIL] installer does not verify UPower clean poweroff")
if "refreshStatus" not in installer:
    raise SystemExit("[FAIL] installer does not reload the live PowerDevil profile")

powerdevil_respawn_path = (
    ROOT / "components/system/20-pocketds-powerdevil-respawn.conf"
)
powerdevil_respawn = read_kconfig(powerdevil_respawn_path)
require(powerdevil_respawn, "Service", "Restart", "always")
require(powerdevil_respawn, "Service", "RestartSec", "2s")
if "plasma-powerdevil.service.d/20-pocketds-respawn.conf" not in installer:
    raise SystemExit("[FAIL] installer does not manage the PowerDevil respawn policy")
runtime_check = (ROOT / "scripts/check.sh").read_text(encoding="utf-8")
if "critical battery action is clean poweroff" not in runtime_check:
    raise SystemExit("[FAIL] runtime check does not monitor UPower clean poweroff")
if "PowerDevil is running and can enforce idle display-off" not in runtime_check:
    raise SystemExit("[FAIL] runtime check does not monitor PowerDevil liveness")

print(
    "  [OK] deep-only sleep, self-healing unlocked idle display-off, and "
    "clean low-battery poweroff are explicit"
)

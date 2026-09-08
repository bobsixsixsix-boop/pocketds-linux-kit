#!/usr/bin/env python3
"""Validate that Panel performance actions have one TuneD authority."""

from configparser import ConfigParser
from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
HELPER = (ROOT / "components/control-panel/pocketds-panel-root").read_text(
    encoding="utf-8"
)
PANELCTL = (ROOT / "components/control-panel/pocketds-panelctl.cpp").read_text(
    encoding="utf-8"
)
INSTALLER = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
DEPENDENCIES = (ROOT / "docs/DEPENDENCIES.md").read_text(encoding="utf-8")

expected = {
    "power-saver": "pocketds-powersave",
    "balanced": "pocketds-balanced",
    "performance": "pocketds-performance",
}
config = ConfigParser()
config.read(ROOT / "components/fan/ppd.conf")
assert dict(config["profiles"]) == expected

for public, tuned in (
    ("powersave", "pocketds-powersave"),
    ("balanced", "pocketds-balanced"),
    ("performance", "pocketds-performance"),
):
    assert f"{public}) tuned_profile={tuned}" in HELPER
    assert f'profile == "{tuned}"' in PANELCTL

assert "/usr/bin/busctl --system --timeout=2s call com.redhat.tuned /Tuned" in HELPER
assert "com.redhat.tuned.control switch_profile" in HELPER
assert "'(bs) true '*" in HELPER
assert "/usr/sbin/tuned-adm" not in HELPER
assert "scaling_governor" not in HELPER
assert 'read_text("/etc/tuned/active_profile")' in PANELCTL
assert "policy0/scaling_governor" not in PANELCTL

for profile in expected.values():
    tuned = (ROOT / f"components/fan/profiles/{profile}/tuned.conf").read_text(
        encoding="utf-8"
    )
    assert "/sys/class/devfreq/3d00000.gpu/" in tuned
    assert "script=${i:PROFILE_DIR}/fan-profile.sh" in tuned

assert "sudo systemctl enable --now tuned.service" in INSTALLER
assert "sudo systemctl disable --now power-profiles-daemon.service" in INSTALLER
assert "sudo systemctl mask power-profiles-daemon.service" in INSTALLER
assert "ppd_enable_state != masked" in INSTALLER
assert "sudo systemctl is-active --quiet tuned.service" in INSTALLER
assert "sudo systemctl is-active --quiet power-profiles-daemon.service" in INSTALLER
assert INSTALLER.index("disable --now power-profiles-daemon.service") < INSTALLER.index(
    "enable --now tuned.service"
)
assert INSTALLER.index("mask power-profiles-daemon.service") < INSTALLER.index(
    "enable --now tuned.service"
)
assert "sudo /usr/sbin/tuned-adm profile pocketds-balanced" in INSTALLER
assert "sudo /usr/sbin/tuned-adm verify" in INSTALLER
assert INSTALLER.count('components/fan/profiles/$profile/tuned.conf') == 1
assert "jq tuned plasma-milou inputplumber" in DEPENDENCIES
assert "tuned-ppd" in DEPENDENCIES
assert "jq tuned-ppd" not in DEPENDENCIES
assert "mask" in DEPENDENCIES
assert "D-Bus" in DEPENDENCIES

# Every install scope must expose the same three profiles to KDE. Keeping
# activation in /usr/local preserves the original package-owned files.
apps = INSTALLER.split('install_apps() {', 1)[1].split('\ninstall_system()', 1)[0]
assert 'components/fan/ppd.conf' in apps
assert INSTALLER.count('components/fan/ppd.conf') == 1
assert 'sudo systemctl enable --now pocketds-tuned-ppd.service' in apps
assert '/usr/local/share/dbus-1/system-services/$target.service' in apps
assert apps.index('enable --now tuned.service') < apps.index('enable --now pocketds-tuned-ppd.service')
for name in ('org.freedesktop.UPower.PowerProfiles', 'net.hadess.PowerProfiles'):
    activation = ConfigParser()
    activation.read(ROOT / f'components/fan/{name}.service')
    assert dict(activation['D-BUS Service']) == {
        'name': name, 'exec': '/bin/false', 'user': 'root',
        'systemdservice': 'pocketds-tuned-ppd.service',
    }

# Both API names share our authorization namespace, without weakening the
# retained daemon's policy or granting remote/inactive callers access.
policy = ET.parse(ROOT / 'components/fan/org.pocketds.TunedPowerProfiles.policy')
actions = policy.findall('action')
assert {a.attrib['id'] for a in actions} == {
    'org.pocketds.TunedPowerProfiles.' + action
    for action in ('switch-profile', 'hold-profile', 'release-profile')
}
for action in actions:
    assert {e.tag: e.text for e in action.find('defaults')} == {
        'allow_any': 'no', 'allow_inactive': 'no', 'allow_active': 'yes',
    }

print("  [OK] Panel, PPD, CPU/GPU limits and fan scripts share TuneD profiles")

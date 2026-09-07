#!/usr/bin/env python3
"""Static contract checks for the Android one-tap Linux switcher."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "components/android-boot-switch/src/li/azka/pocketds/dualboot/MainActivity.java"
MANIFEST = ROOT / "components/android-boot-switch/AndroidManifest.xml"

source = SOURCE.read_text(encoding="utf-8")
manifest = MANIFEST.read_text(encoding="utf-8")

expected = {
    "BOOT_MODE_OFFSET": "0xA34",
    "BOOT_SOURCE_OFFSET": "0xA92",
    "NORMALIZED_SHA256": "fb560ce40bf17c7fc0dbda4b7e17748eb6e2fd30bbc8281ee0fc95acb28e19da",
}
for name, value in expected.items():
    assert re.search(rf"{name}\s*=\s*(?:\n\s*)?\"?{value}\"?;", source), name

assert 'private static final String FEDORA_BOOT = "/dev/block/sda12";' in source
assert 'private static final String FEDORA_DATA = "/dev/block/sda13";' in source
assert 'LABEL=\\"ROCKNIX\\"' in source
assert 'LABEL=\\"STORAGE\\"' in source
assert "TF 卡" not in source
assert "BootSourceMode" not in source
assert "android.permission" not in manifest
assert 'android:label="切换到 Linux"' in manifest
assert 'package="li.azka.pocketds.dualboot"' in manifest

print("  [OK] Android launcher preserves the verified internal-Fedora boot contract")

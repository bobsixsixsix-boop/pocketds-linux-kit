#!/usr/bin/env python3
"""Offline provenance-drift checks for the reverified copied components."""
import hashlib
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    return json.loads((ROOT / name).read_text())


def digest(content):
    return hashlib.sha256(content).hexdigest()


class CopiedSourceTests(unittest.TestCase):
    def check_source(self, record, revision):
        self.assertRegex(revision, r"\A[0-9a-f]{40}\Z")
        self.assertIn("/" + revision + "/", record["url"])
        self.assertTrue(record["url"].startswith("https://raw.githubusercontent.com/"))
        self.assertTrue(record["url"].endswith("/" + record["path"]))
        self.assertRegex(record["sha256"], r"\A[0-9a-f]{64}\Z")

    def check_icon_record(self, record):
        self.assertEqual(record["schema"], 1)
        self.assertIsNone(record["original_copy_revision"])
        self.assertEqual(record["license"], "MIT")
        revision = record["verified_revision"]
        self.check_source(record["upstream_license"], revision)
        license_bytes = (ROOT / record["license_file"]).read_bytes()
        self.assertEqual(digest(license_bytes), record["upstream_license"]["sha256"])
        self.assertIn(b"Copyright (c) 2023 Phosphor Icons", license_bytes)
        self.assertIn(b"Permission is hereby granted, free of charge", license_bytes)
        self.assertIn(b"THE SOFTWARE IS PROVIDED", license_bytes)
        for icon in record["icons"]:
            self.check_source(icon["source"], revision)
            self.assertRegex(icon["upstream_path_sha256"], r"\A[0-9a-f]{64}\Z")
            self.assertIn(icon["comparison"], ("identical", "adapted"))
            if icon["comparison"] == "adapted":
                self.assertTrue(icon["adaptation"])

    def test_panel_inventory_covers_every_embedded_path_and_retains_its_license(self):
        record = load("components/control-panel/plasmoid/PHOSPHOR-SOURCES.json")
        self.check_icon_record(record)
        source = (ROOT / record["local_file"]).read_text()
        block = source.split("readonly property var phosphorIconPaths: ({", 1)[1].split("})", 1)[0]
        paths = json.loads("{" + block + "}")
        self.assertEqual(len(record["icons"]), len(paths))
        self.assertEqual({icon["name"] for icon in record["icons"]}, set(paths))
        adapted = []
        for icon in record["icons"]:
            with self.subTest(icon=icon["name"]):
                actual = digest(paths[icon["name"]].encode())
                self.assertEqual(actual, icon["local_path_sha256"])
                if icon["comparison"] == "identical":
                    self.assertEqual(actual, icon["upstream_path_sha256"])
                else:
                    adapted.append(icon["name"])
        self.assertEqual(adapted, ["speaker-slash"])

    def test_android_adaptation_is_explicit_and_path_does_not_drift(self):
        record = load("components/android-boot-switch/licenses/Phosphor-SOURCES.json")
        self.check_icon_record(record)
        icon, = record["icons"]
        self.assertEqual(icon["name"], "linux-logo")
        self.assertEqual(icon["comparison"], "adapted")
        vector = ET.parse(ROOT / record["local_file"]).getroot()
        android = "{http://schemas.android.com/apk/res/android}"
        path = vector.find(".//path").get(android + "pathData")
        self.assertEqual(digest(path.encode()), icon["local_path_sha256"])
        self.assertEqual(vector.get(android + "viewportWidth"), "256")
        self.assertEqual(vector.get(android + "viewportHeight"), "256")
        self.assertEqual((ROOT / record["license_file"]).read_bytes(),
                         (ROOT / "components/control-panel/plasmoid/licenses/Phosphor-Icons-MIT.txt").read_bytes())

    def test_gamescope_is_the_exact_pinned_xml_with_embedded_notice(self):
        record = load("components/game-runtime/gamescope-control.sources.json")
        self.check_source(record["source"], record["verified_revision"])
        self.assertIsNone(record["original_copy_revision"])
        content = (ROOT / record["local_file"]).read_bytes()
        self.assertEqual(digest(content), record["source"]["sha256"])
        protocol = ET.fromstring(content)
        interface = protocol.find("interface")
        self.assertEqual(interface.get("name"), record["interface"])
        self.assertEqual(int(interface.get("version")), 6)
        copyright = protocol.find("copyright").text
        self.assertIn("2023 Valve Corporation", copyright)
        self.assertIn("Permission is hereby granted", copyright)
        self.assertIn("THE SOFTWARE IS PROVIDED", copyright)

    def test_switchdeck_pins_complete_notices_and_current_adaptations(self):
        record = load("components/steam/licenses/Switchdeck-SOURCES.json")
        self.assertIsNone(record["original_copy_revision"])
        self.assertEqual(record["license"], "GPL-3.0-only")
        for source in record["sources"] + record["notices"]:
            self.check_source(source, record["verified_revision"])
        for notice in record["notices"]:
            self.assertEqual(digest((ROOT / notice["local_file"]).read_bytes()), notice["sha256"])
        for local in record["local_files"]:
            content = (ROOT / local["path"]).read_bytes()
            self.assertEqual(digest(content), local["sha256"])
            self.assertIn(b"SPDX-License-Identifier: GPL-3.0-only", content)
            self.assertIn(b"SildurFX", content)
            self.assertIn(record["verified_revision"].encode(), content)
        license_text = (ROOT / "components/steam/licenses/Switchdeck-GPL-3.0.txt").read_text()
        self.assertIn("GNU GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("Version 3, 29 June 2007", license_text)
        self.assertIn("END OF TERMS AND CONDITIONS", license_text)
        self.assertGreater(len(license_text), 30000)
        notice = (ROOT / "components/steam/licenses/Switchdeck-NOTICE.txt").read_text()
        self.assertIn("GNU General Public License v3.0", notice)
        self.assertIn("proprietary property of Valve", notice)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Pure tests for reversible Applet-33 migration."""

from pathlib import Path
import json
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "experiments/pds005-panel-shell/migration"
sys.path.insert(0, str(MODULE))

from applet_migration import (  # noqa: E402
    AppletTarget,
    BackupBundle,
    MigrationConflict,
    MigrationError,
    MigrationState,
    classify,
    create_backup_bundle,
    migrate_bytes,
    plan_migration,
    restore_bytes,
)


FIXTURE = b"""[Containments][2]\nItemGeometries-819x614=Applet-8:1,2,3,4,0;Applet-33:0,0,816,608,0;\nItemGeometriesHorizontal=Applet-33:0,0,816,608,0;\nactivityId=keep-me\nplugin=org.kde.plasma.folder\n\n[Containments][2][Applets][8]\nplugin=org.kde.plasma.clock\n\n[Containments][2][Applets][33]\nimmutability=1\nplugin=org.pocketds.controlpanel.v3\n\n[Containments][2][Applets][33][Configuration][General]\nprivate=value-33-must-go\n\n[Containments][2][Wallpaper][org.kde.image][General]\nImage=file:///keep/lower.png\n\n[Containments][3][Applets][133]\nplugin=org.example.keep33\n"""


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.plan = plan_migration(FIXTURE)
        self.bundle = create_backup_bundle(self.plan)

    def test_exact_target_and_geometry_removed_only(self):
        migrated = self.plan.migrated
        self.assertNotIn(b"[Containments][2][Applets][33]", migrated)
        self.assertNotIn(b"Applet-33:", migrated)
        self.assertNotIn(b"ItemGeometriesHorizontal=", migrated)
        self.assertIn(b"Applet-8:1,2,3,4,0;", migrated)
        self.assertIn(b"[Containments][2][Applets][8]", migrated)
        self.assertIn(b"[Containments][3][Applets][133]", migrated)
        self.assertIn(b"Image=file:///keep/lower.png", migrated)
        self.assertEqual(self.plan.removed_sections, 2)
        self.assertEqual(self.plan.removed_geometry_references, 2)

    def test_backup_and_restore_are_exact_bytes(self):
        self.assertEqual(classify(FIXTURE, self.bundle), MigrationState.RESTORED)
        migrated = migrate_bytes(FIXTURE, self.bundle)
        self.assertEqual(migrated, self.plan.migrated)
        self.assertEqual(classify(migrated, self.bundle), MigrationState.MIGRATED)
        self.assertEqual(restore_bytes(migrated, self.bundle), FIXTURE)

    def test_apply_and_restore_are_idempotent(self):
        migrated = migrate_bytes(FIXTURE, self.bundle)
        self.assertEqual(migrate_bytes(migrated, self.bundle), migrated)
        restored = restore_bytes(migrated, self.bundle)
        self.assertEqual(restore_bytes(restored, self.bundle), restored)

    def test_config_change_after_migration_refuses_restore(self):
        changed = self.plan.migrated + b"\n[user-change]\nkey=value\n"
        self.assertEqual(classify(changed, self.bundle), MigrationState.CONFLICT)
        with self.assertRaises(MigrationConflict):
            restore_bytes(changed, self.bundle)

    def test_config_change_before_apply_refuses_migration(self):
        with self.assertRaises(MigrationConflict):
            migrate_bytes(FIXTURE + b"\n# concurrent change\n", self.bundle)

    def test_tampered_backup_fails_closed(self):
        tampered = BackupBundle(
            self.bundle.manifest, self.bundle.original + b"x", self.bundle.migrated
        )
        self.assertEqual(classify(FIXTURE, tampered), MigrationState.INVALID_BACKUP)
        with self.assertRaises(MigrationError):
            restore_bytes(self.plan.migrated, tampered)

    def test_self_consistent_but_underived_payload_is_rejected(self):
        wrong_migrated = self.bundle.migrated + b"# injected\n"
        manifest = json.loads(self.bundle.manifest)
        manifest["migrated"]["size"] = len(wrong_migrated)
        from hashlib import sha256

        manifest["migrated"]["sha256"] = sha256(wrong_migrated).hexdigest()
        encoded = (
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        forged = BackupBundle(encoded, self.bundle.original, wrong_migrated)
        self.assertEqual(classify(FIXTURE, forged), MigrationState.INVALID_BACKUP)
        with self.assertRaises(MigrationError):
            migrate_bytes(FIXTURE, forged)

    def test_wrong_or_duplicate_owner_is_rejected(self):
        wrong = FIXTURE.replace(
            b"[Containments][2][Applets][33]",
            b"[Containments][9][Applets][33]",
            1,
        )
        with self.assertRaises(MigrationError):
            plan_migration(wrong)
        duplicate = FIXTURE + b"\n[Containments][9][Applets][44]\nplugin=org.pocketds.controlpanel.v3\n"
        with self.assertRaises(MigrationError):
            plan_migration(duplicate)

    def test_absent_unmanaged_is_distinct_from_ambiguous(self):
        self.assertEqual(
            classify(self.plan.migrated, None), MigrationState.ABSENT_UNMANAGED
        )
        ambiguous = FIXTURE.replace(
            b"plugin=org.pocketds.controlpanel.v3",
            b"plugin=org.example.not-panel",
        )
        self.assertEqual(classify(ambiguous, None), MigrationState.ABSENT_UNMANAGED)
        duplicate = FIXTURE + b"\n[Containments][9][Applets][44]\nplugin=org.pocketds.controlpanel.v3\n"
        self.assertEqual(classify(duplicate, None), MigrationState.AMBIGUOUS)

    def test_crlf_and_no_final_newline_round_trip_exactly(self):
        fixture = FIXTURE.replace(b"\n", b"\r\n").rstrip(b"\r\n")
        plan = plan_migration(fixture)
        bundle = create_backup_bundle(plan)
        self.assertNotIn(b"\n", plan.migrated.replace(b"\r\n", b""))
        self.assertEqual(restore_bytes(migrate_bytes(fixture, bundle), bundle), fixture)

    def test_fifty_migrate_restore_cycles_are_lossless(self):
        current = FIXTURE
        for _ in range(50):
            current = migrate_bytes(current, self.bundle)
            current = restore_bytes(current, self.bundle)
        self.assertEqual(current, FIXTURE)


if __name__ == "__main__":
    unittest.main()

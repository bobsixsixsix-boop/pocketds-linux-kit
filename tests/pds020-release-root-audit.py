#!/usr/bin/env python3
"""Pure/static tests for the PDS-020 mounted-root release audit."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pds020-release-root-audit.py"
SPEC = importlib.util.spec_from_file_location("pds020_release_root_audit", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


class SudoersTests(unittest.TestCase):
    def test_continuations_comments_and_broad_nopasswd(self):
        lines = audit.logical_sudo_lines(
            b"# comment\nCmnd_Alias SAFE = /usr/bin/true, \\\n /usr/bin/false\n%wheel ALL=(ALL) NOPASSWD: ALL # unsafe\n"
        )
        self.assertEqual(lines[0], "Cmnd_Alias SAFE = /usr/bin/true,  /usr/bin/false")
        self.assertTrue(audit.BROAD_NOPASSWD.search(lines[1]))
        self.assertFalse(
            audit.BROAD_NOPASSWD.search(
                "%wheel ALL=(root) NOPASSWD: POCKETDS_PANEL"
            )
        )

    def test_include_directive_is_not_discarded_as_a_comment(self):
        lines = audit.logical_sudo_lines(
            b"#includedir /etc/sudoers.d\n@include /tmp/unsafe\n# ordinary\n"
        )
        self.assertEqual(
            lines,
            ["#includedir /etc/sudoers.d", "@include /tmp/unsafe"],
        )


class PrivacyTests(unittest.TestCase):
    def test_categories_are_reported_without_private_paths(self):
        with tempfile.TemporaryDirectory(prefix="pds020-private-") as name:
            root = Path(name)
            home = root / "home/private-person"
            (home / ".ssh").mkdir(parents=True)
            (home / ".ssh/id_ed25519").write_text("private", encoding="utf-8")
            (home / "ROMs").mkdir()
            (home / "ROMs/private-title.nes").write_bytes(b"fixture")
            result = audit.audit_private_state(root, 10_000)
            self.assertEqual(result.status, "fail")
            self.assertEqual(
                result.evidence["finding_categories"], ["rom-content", "ssh-state"]
            )
            self.assertNotIn("private-person", repr(result.evidence))
            self.assertNotIn("private-title", repr(result.evidence))
            self.assertFalse(result.evidence["paths_disclosed"])

    def test_clean_empty_home_passes(self):
        with tempfile.TemporaryDirectory(prefix="pds020-clean-") as name:
            root = Path(name)
            (root / "home/pocketds").mkdir(parents=True)
            result = audit.audit_private_state(root, 10_000)
            self.assertEqual(result.status, "pass")

    def test_home_symlink_fails_closed_without_target_disclosure(self):
        with tempfile.TemporaryDirectory(prefix="pds020-home-link-") as name:
            root = Path(name)
            (root / "home").mkdir()
            (root / "private-target").mkdir()
            (root / "home/private-person").symlink_to(root / "private-target")
            result = audit.audit_private_state(root, 10_000)
            self.assertEqual(result.status, "fail")
            self.assertEqual(
                result.evidence["finding_categories"], ["unsafe-home-symlink"]
            )
            self.assertNotIn("private-person", repr(result.evidence))

    def test_system_identity_categories_do_not_disclose_paths(self):
        with tempfile.TemporaryDirectory(prefix="pds020-system-private-") as name:
            root = Path(name)
            (root / "etc/wireguard").mkdir(parents=True)
            (root / "etc/wireguard/private.conf").write_text(
                "fixture", encoding="utf-8"
            )
            (root / "etc/ssh").mkdir()
            (root / "etc/ssh/ssh_host_ed25519_key").write_text(
                "fixture", encoding="utf-8"
            )
            (root / "etc/machine-id").write_text("abc123\n", encoding="utf-8")
            result = audit.audit_private_system_state(root)
            self.assertEqual(result.status, "fail")
            self.assertEqual(
                result.evidence["finding_categories"],
                ["machine-identity", "ssh-host-identity", "vpn-state"],
            )
            self.assertNotIn("private.conf", repr(result.evidence))
            self.assertFalse(result.evidence["paths_disclosed"])

    def test_empty_or_uninitialized_system_identity_passes(self):
        with tempfile.TemporaryDirectory(prefix="pds020-system-clean-") as name:
            root = Path(name)
            (root / "etc").mkdir()
            (root / "etc/machine-id").write_text(
                "uninitialized\n", encoding="utf-8"
            )
            result = audit.audit_private_system_state(root)
            self.assertEqual(result.status, "pass")


class EvidenceTests(unittest.TestCase):
    def test_missing_assets_requires_exact_empty_manifest_without_bypassing_other_evidence(self):
        with tempfile.TemporaryDirectory(prefix="pds020-absent-assets-") as name:
            repo = Path(name)
            metadata = repo / "components/assets"
            metadata.mkdir(parents=True)
            valid = {"schema": 1, "profile": "asset-free", "assets": []}
            manifest = metadata / "ASSETS.json"
            manifest.write_text(json.dumps(valid), encoding="utf-8")
            checks = audit.audit_repository_evidence(repo)
            self.assertEqual(checks[4].status, "pass")
            self.assertTrue(all(check.status == "fail" for check in checks[:4]))
            self.assertFalse((repo / "assets").exists())
            for value in (
                None,
                {"schema": True, "profile": "asset-free", "assets": []},
                {"schema": 1, "profile": "redistributable", "assets": []},
                {"schema": 1, "profile": "asset-free", "assets": ["assets/missing"]},
                {**valid, "allow_missing": True},
            ):
                with self.subTest(manifest=value):
                    self.assertFalse(audit._asset_preflight(repo, value)[0])

    def test_asset_free_rejects_non_directory_links_and_hidden_files(self):
        with tempfile.TemporaryDirectory(prefix="pds020-unsafe-assets-") as name:
            repo = Path(name)
            assets = repo / "assets"
            valid = {"schema": 1, "profile": "asset-free", "assets": []}
            assets.write_text("not a directory", encoding="utf-8")
            self.assertFalse(audit._asset_preflight(repo, valid)[0])
            assets.unlink()
            target = repo / "other"
            for exists in (False, True):
                if exists:
                    target.mkdir()
                assets.symlink_to(target)
                self.assertFalse(audit._asset_preflight(repo, valid)[0])
                assets.unlink()
            assets.mkdir()
            (assets / ".hidden").write_text("not empty", encoding="utf-8")
            self.assertFalse(audit._asset_preflight(repo, valid)[0])

    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _write_valid_evidence(self, repo: Path) -> None:
        (repo / "LICENSE").write_text(
            "Pocket DS fixture license terms for deterministic release-audit "
            "testing only. Permission is granted for this synthetic fixture; "
            "this text is not used as the project license.\n",
            encoding="utf-8",
        )
        (repo / "NOTICE").write_text(
            "Pocket DS synthetic release-audit fixture notice.\n",
            encoding="utf-8",
        )
        creation_info = {
            "type": "CreationInfo",
            "@id": "_:creationinfo",
            "created": "2026-08-28T00:00:00Z",
            "createdBy": ["urn:pocketds:agent:fixture"],
            "specVersion": "3.0.1",
        }
        sbom = {
            "@context": "https://spdx.org/rdf/3.0.1/spdx-context.jsonld",
            "@graph": [
                creation_info,
                {
                    "type": "SoftwareAgent",
                    "spdxId": "urn:pocketds:agent:fixture",
                    "name": "Pocket DS fixture generator",
                    "creationInfo": "_:creationinfo",
                },
                {
                    "type": "SpdxDocument",
                    "spdxId": "urn:pocketds:document:fixture",
                    "name": "Pocket DS fixture document",
                    "creationInfo": "_:creationinfo",
                    "rootElement": ["urn:pocketds:sbom:fixture"],
                    "element": [
                        "urn:pocketds:sbom:fixture",
                        "urn:pocketds:package:fixture",
                        "urn:pocketds:agent:fixture",
                    ],
                },
                {
                    "type": "software_Sbom",
                    "spdxId": "urn:pocketds:sbom:fixture",
                    "name": "Pocket DS fixture SBOM",
                    "creationInfo": "_:creationinfo",
                    "rootElement": ["urn:pocketds:package:fixture"],
                    "element": ["urn:pocketds:package:fixture"],
                    "software_sbomType": ["build"],
                },
                {
                    "type": "software_Package",
                    "spdxId": "urn:pocketds:package:fixture",
                    "name": "fixture-package",
                    "software_packageVersion": "1.0.0",
                    "software_copyrightText": "Copyright fixture",
                    "software_downloadLocation": "https://example.invalid/fixture.tar.xz",
                    "creationInfo": "_:creationinfo",
                },
            ],
        }
        (repo / "SBOM.spdx.json").write_text(json.dumps(sbom), encoding="utf-8")

        evidence = b"content-hashed third-party receipt\n"
        (repo / "evidence").mkdir()
        (repo / "evidence/dependency.txt").write_bytes(evidence)
        third_party = {
            "schema": 1,
            "components": [
                {
                    "id": "fixture-dependency",
                    "name": "Fixture Dependency",
                    "version": "1.0.0",
                    "license": "MIT",
                    "source": "https://example.invalid/source",
                    "redistribution": True,
                    "evidence": [
                        {
                            "path": "evidence/dependency.txt",
                            "sha256": self._digest(evidence),
                        }
                    ],
                }
            ],
        }
        (repo / "THIRD-PARTY.json").write_text(
            json.dumps(third_party), encoding="utf-8"
        )

        asset = b"redistributable fixture asset\n"
        (repo / "assets").mkdir()
        (repo / "assets/fixture.bin").write_bytes(asset)
        (repo / "components/assets").mkdir(parents=True)
        assets = {
            "schema": 1,
            "profile": "redistributable",
            "assets": [
                {
                    "path": "assets/fixture.bin",
                    "sha256": self._digest(asset),
                    "source": "https://example.invalid/asset",
                    "author": "Fixture Author",
                    "license": "CC0-1.0",
                    "redistribution": True,
                }
            ],
        }
        (repo / "components/assets/ASSETS.json").write_text(
            json.dumps(assets), encoding="utf-8"
        )

    def test_spdx_expression_subset_is_bounded(self):
        self.assertTrue(audit._spdx_expression("MIT"))
        self.assertTrue(
            audit._spdx_expression(
                "(MIT OR Apache-2.0) AND GPL-2.0-only WITH Classpath-exception-2.0"
            )
        )
        self.assertTrue(audit._spdx_expression("LicenseRef-Project-Proprietary"))
        self.assertFalse(audit._spdx_expression("NOASSERTION"))
        self.assertFalse(audit._spdx_expression("MIT or Apache-2.0"))
        self.assertFalse(audit._spdx_expression("All rights reserved"))

    def test_spdx_jsonld_graph_references_and_profile_fields_are_required(self):
        with tempfile.TemporaryDirectory(prefix="pds020-evidence-") as name:
            repo = Path(name)
            self._write_valid_evidence(repo)
            sbom = json.loads((repo / "SBOM.spdx.json").read_text())
        self.assertEqual(audit._spdx_preflight(sbom), (True, 5))
        for mutate in (
            lambda value: next(
                item for item in value["@graph"] if item["type"] == "CreationInfo"
            ).update(createdBy=["urn:pocketds:agent:missing"]),
            lambda value: next(
                item for item in value["@graph"] if item["type"] == "CreationInfo"
            ).update(createdBy=[["not", "an", "identifier"]]),
            lambda value: next(
                item
                for item in value["@graph"]
                if item.get("spdxId") == "urn:pocketds:agent:fixture"
            ).update(type="software_File"),
            lambda value: next(
                item for item in value["@graph"] if item["type"] == "software_Sbom"
            ).update(element=["urn:pocketds:document:fixture"]),
            lambda value: next(
                item for item in value["@graph"] if item["type"] == "software_Package"
            ).update(creationInfo="_:missing"),
            lambda value: next(
                item for item in value["@graph"] if item["type"] == "software_Package"
            ).update(
                packageVersion="1.0.0",
                software_packageVersion="TBD",
            ),
        ):
            changed = json.loads(json.dumps(sbom))
            mutate(changed)
            self.assertFalse(audit._spdx_preflight(changed)[0])

    def test_missing_release_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-evidence-") as name:
            checks = audit.audit_repository_evidence(Path(name))
            self.assertTrue(all(item.status == "fail" for item in checks))
            self.assertEqual(checks[0].evidence["files"]["LICENSE"], "missing-or-unsafe")

    def test_complete_content_hashed_evidence_passes_preflight(self):
        with tempfile.TemporaryDirectory(prefix="pds020-evidence-") as name:
            repo = Path(name)
            self._write_valid_evidence(repo)
            checks = audit.audit_repository_evidence(repo)
            self.assertTrue(all(item.status == "pass" for item in checks))
            self.assertEqual(checks[2].evidence["element_count"], 5)
            self.assertEqual(checks[3].evidence["component_count"], 1)
            self.assertEqual(checks[4].evidence["repository_asset_count"], 1)
            (repo / "assets/fixture.bin").unlink()
            manifest = json.loads(
                (repo / "components/assets/ASSETS.json").read_text()
            )
            manifest["profile"] = "asset-free"
            manifest["assets"] = []
            (repo / "components/assets/ASSETS.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            checks = audit.audit_repository_evidence(repo)
            self.assertTrue(all(item.status == "pass" for item in checks))
            self.assertEqual(checks[4].evidence["manifest_profile"], "asset-free")
            self.assertEqual(checks[4].evidence["manifest_asset_count"], 0)
            self.assertEqual(checks[4].evidence["repository_asset_count"], 0)
            (repo / "assets/unlisted.bin").write_bytes(b"must fail\n")
            checks = audit.audit_repository_evidence(repo)
            self.assertEqual(checks[4].status, "fail")
            (repo / "assets/unlisted.bin").unlink()
            manifest["allow_unlisted"] = True
            (repo / "components/assets/ASSETS.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            checks = audit.audit_repository_evidence(repo)
            self.assertEqual(checks[4].status, "fail")
            del manifest["allow_unlisted"]
            manifest["profile"] = "unknown"
            (repo / "components/assets/ASSETS.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            checks = audit.audit_repository_evidence(repo)
            self.assertEqual(checks[4].status, "fail")

    def test_nonempty_placeholder_manifests_still_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-evidence-") as name:
            repo = Path(name)
            self._write_valid_evidence(repo)
            (repo / "LICENSE").write_text("TODO choose a license\n", encoding="utf-8")
            third_party = json.loads((repo / "THIRD-PARTY.json").read_text())
            third_party["components"][0]["license"] = "NOASSERTION"
            (repo / "THIRD-PARTY.json").write_text(
                json.dumps(third_party), encoding="utf-8"
            )
            assets = json.loads(
                (repo / "components/assets/ASSETS.json").read_text()
            )
            assets["assets"][0]["redistribution"] = False
            (repo / "components/assets/ASSETS.json").write_text(
                json.dumps(assets), encoding="utf-8"
            )
            sbom = json.loads((repo / "SBOM.spdx.json").read_text())
            package = next(
                item for item in sbom["@graph"] if item["type"] == "software_Package"
            )
            package["software_packageVersion"] = "TBD"
            (repo / "SBOM.spdx.json").write_text(json.dumps(sbom), encoding="utf-8")

            checks = audit.audit_repository_evidence(repo)
            self.assertEqual(checks[0].status, "pass")
            self.assertTrue(all(item.status == "fail" for item in checks[1:]))

    def test_unlisted_asset_and_tampered_receipt_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-evidence-") as name:
            repo = Path(name)
            self._write_valid_evidence(repo)
            (repo / "assets/unlisted.bin").write_bytes(b"unlisted\n")
            (repo / "evidence/dependency.txt").write_bytes(b"tampered\n")
            checks = audit.audit_repository_evidence(repo)
            self.assertEqual(checks[0].status, "pass")
            self.assertEqual(checks[1].status, "pass")
            self.assertEqual(checks[2].status, "pass")
            self.assertEqual(checks[3].status, "fail")
            self.assertEqual(checks[4].status, "fail")

    def test_duplicate_json_keys_fail_closed(self):
        cases = (
            ("SBOM.spdx.json", '"@context":"https://invalid.example/",', 2),
            ("THIRD-PARTY.json", '"schema":0,', 3),
            ("components/assets/ASSETS.json", '"schema":0,', 4),
        )
        for relative, duplicate, check_index in cases:
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory(prefix="pds020-evidence-") as name:
                    repo = Path(name)
                    self._write_valid_evidence(repo)
                    path = repo / relative
                    original = path.read_text(encoding="utf-8")
                    self.assertTrue(original.startswith("{"))
                    path.write_text(
                        "{" + duplicate + original[1:],
                        encoding="utf-8",
                    )
                    checks = audit.audit_repository_evidence(repo)
                    self.assertEqual(checks[check_index].status, "fail")

        with tempfile.TemporaryDirectory(prefix="pds020-evidence-") as name:
            repo = Path(name)
            self._write_valid_evidence(repo)
            path = repo / "components/assets/ASSETS.json"
            original = path.read_text(encoding="utf-8")
            path.write_text(
                original.replace(
                    '"redistribution": true',
                    '"redistribution": false, "redistribution": true',
                    1,
                ),
                encoding="utf-8",
            )
            checks = audit.audit_repository_evidence(repo)
            self.assertEqual(checks[4].status, "fail")

    def test_evidence_requires_utf8_finite_json_and_exact_schema_types(self):
        with tempfile.TemporaryDirectory(prefix="pds020-evidence-") as name:
            repo = Path(name)
            self._write_valid_evidence(repo)
            path = repo / "THIRD-PARTY.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            path.write_bytes(json.dumps(value).encode("utf-16"))
            checks = audit.audit_repository_evidence(repo)
            self.assertEqual(checks[3].status, "fail")

        for relative, check_index in (
            ("THIRD-PARTY.json", 3),
            ("components/assets/ASSETS.json", 4),
        ):
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory(prefix="pds020-evidence-") as name:
                    repo = Path(name)
                    self._write_valid_evidence(repo)
                    path = repo / relative
                    value = json.loads(path.read_text(encoding="utf-8"))
                    value["schema"] = True
                    path.write_text(json.dumps(value), encoding="utf-8")
                    checks = audit.audit_repository_evidence(repo)
                    self.assertEqual(checks[check_index].status, "fail")

        with tempfile.TemporaryDirectory(prefix="pds020-evidence-") as name:
            repo = Path(name)
            self._write_valid_evidence(repo)
            path = repo / "SBOM.spdx.json"
            original = path.read_text(encoding="utf-8")
            path.write_text(
                '{"ignored":NaN,' + original[1:],
                encoding="utf-8",
            )
            checks = audit.audit_repository_evidence(repo)
            self.assertEqual(checks[2].status, "fail")

    def test_symlinked_evidence_parent_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="pds020-evidence-") as name:
            base = Path(name)
            repo = base / "repo"
            repo.mkdir()
            self._write_valid_evidence(repo)
            target = base / "outside"
            target.mkdir()
            (target / "dependency.txt").write_bytes(
                (repo / "evidence/dependency.txt").read_bytes()
            )
            (repo / "evidence/dependency.txt").unlink()
            (repo / "evidence").rmdir()
            (repo / "evidence").symlink_to(target, target_is_directory=True)
            checks = audit.audit_repository_evidence(repo)
            self.assertEqual(checks[3].status, "fail")


class CliAndStaticTests(unittest.TestCase):
    def test_live_root_requires_exact_read_only_confirmation(self):
        with tempfile.TemporaryDirectory(prefix="pds020-live-root-gate-") as name:
            repo = Path(name)
            # Satisfy only the repository precondition; exported sources have
            # no .git. Missing confirmation must stop before any live audit.
            (repo / ".git").mkdir()
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", "/", "--repo", str(repo)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn(audit.LIVE_CONFIRMATION, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_source_has_no_mutating_or_upload_path(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "sudo ",
            "systemctl",
            "subprocess.run",
            "urllib.request",
            "requests",
            "shutil.rmtree",
            "os.remove",
            "unlink(",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('"read_only": True', source)
        self.assertIn('"paths_disclosed": False', source)
        self.assertIn("sys.dont_write_bytecode = True", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

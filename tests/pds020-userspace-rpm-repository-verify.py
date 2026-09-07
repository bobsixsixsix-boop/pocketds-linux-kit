#!/usr/bin/env python3
"""Offline tests for reproducible unsigned pds2 repository metadata."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pds020-userspace-rpm-repository-verify.py"
LOCK = ROOT / "packaging/pocketds-userspace/repository-metadata-lock.pds2.json"
SPEC = importlib.util.spec_from_file_location("pds020_repository_verify_test", SOURCE)
assert SPEC and SPEC.loader
repository = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = repository
SPEC.loader.exec_module(repository)


class RepositoryVerifyTests(unittest.TestCase):
    def test_real_lock_binds_two_unsigned_reproducible_results(self) -> None:
        lock = repository.load_lock(LOCK)
        self.assertEqual(lock["archive"]["package_count"], 322)
        self.assertEqual(lock["generator"]["independent_result_count"], 2)
        self.assertEqual(lock["repository"]["revision"], 1787875200)
        self.assertEqual(
            lock["repository"]["repomd"]["sha256"],
            "c45ef7800681004df2d6e17436de3c02739d3be2a680aab95e9040402878bf39",
        )
        self.assertFalse(lock["policy"]["detached_repomd_signature_verified"])
        self.assertFalse(lock["policy"]["release_ready"])

    def test_lock_rejects_generator_parameter_order_and_signature_overclaim(self) -> None:
        original = json.loads(LOCK.read_text(encoding="utf-8"))
        for mutation, error in (
            (
                lambda value: value["generator"]["parameters"].reverse(),
                "generator contract",
            ),
            (
                lambda value: value["policy"].update(
                    detached_repomd_signature_verified=True
                ),
                "signing boundary",
            ),
        ):
            with self.subTest(error=error):
                with tempfile.TemporaryDirectory(prefix="pds020-repository-lock-") as name:
                    candidate = copy.deepcopy(original)
                    mutation(candidate)
                    path = Path(name) / "lock.json"
                    path.write_text(json.dumps(candidate), encoding="utf-8")
                    with self.assertRaisesRegex(repository.RepositoryError, error):
                        repository.load_lock(path)

    def test_repodata_directory_is_exact_private_and_link_free(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-repodata-") as name:
            directory = Path(name)
            directory.chmod(0o700)
            item = directory / "repomd.xml"
            item.write_bytes(b"xml")
            repository.exact_directory(directory, {"repomd.xml"})
            extra = directory / "extra"
            extra.write_bytes(b"x")
            with self.assertRaisesRegex(repository.RepositoryError, "extra"):
                repository.exact_directory(directory, {"repomd.xml"})
            extra.unlink()
            item.unlink()
            os.symlink("missing", item)
            with self.assertRaisesRegex(repository.RepositoryError, "metadata"):
                repository.exact_directory(directory, {"repomd.xml"})

    def test_xz_stream_is_size_and_open_hash_bound(self) -> None:
        content = b"metadata"
        compressed = lzma.compress(content, format=lzma.FORMAT_XZ)
        record = {
            "open_size": len(content),
            "open_sha256": hashlib.sha256(content).hexdigest(),
        }
        self.assertEqual(repository.decompress_locked(compressed, record), content)
        with self.assertRaisesRegex(repository.RepositoryError, "open content"):
            repository.decompress_locked(compressed, {**record, "open_size": 1})

    def test_repomd_requires_exact_revision_checksums_sizes_and_locations(self) -> None:
        lock = repository.load_lock(LOCK)
        records = lock["repository"]["metadata"]
        nodes = []
        for record in records:
            nodes.append(
                f'<data type="{record["type"]}">'
                f'<checksum type="sha256">{record["sha256"]}</checksum>'
                f'<open-checksum type="sha256">{record["open_sha256"]}</open-checksum>'
                f'<location href="repodata/{record["filename"]}"/>'
                f'<timestamp>{lock["repository"]["revision"]}</timestamp>'
                f'<size>{record["size"]}</size><open-size>{record["open_size"]}</open-size>'
                "</data>"
            )
        xml = (
            f'<repomd xmlns="{repository.REPO_NS}">'
            f'<revision>{lock["repository"]["revision"]}</revision>'
            + "".join(nodes)
            + "</repomd>"
        ).encode()
        repository.validate_repomd(lock, xml)
        with self.assertRaises(repository.RepositoryError):
            repository.validate_repomd(lock, xml.replace(b"repodata/", b"other/", 1))
        with self.assertRaisesRegex(repository.RepositoryError, "child set"):
            repository.validate_repomd(lock, xml.replace(b"</repomd>", b"<unknown/></repomd>"))

    def test_primary_binds_each_location_nevra_size_and_package_hash(self) -> None:
        package = {
            "filename": "pkg-1-2.noarch.rpm",
            "name": "pkg",
            "epoch": 0,
            "version": "1",
            "release": "2",
            "arch": "noarch",
            "sha256": "a" * 64,
            "size": 123,
        }
        canonical = f"{package['filename']}\t{package['sha256']}\n".encode()
        lock = {
            "archive": {"package_count": 1},
            "repository": {
                "revision": 1787875200,
                "primary_location_checksum_sha256": hashlib.sha256(canonical).hexdigest(),
            },
        }
        xml = (
            f'<metadata xmlns="{repository.COMMON_NS}" packages="1">'
            '<package type="rpm"><name>pkg</name><arch>noarch</arch>'
            '<version epoch="0" ver="1" rel="2"/>'
            f'<checksum type="sha256" pkgid="YES">{"a" * 64}</checksum>'
            '<time file="1787875200" build="1"/>'
            '<size package="123" installed="1" archive="1"/>'
            '<location href="pkg-1-2.noarch.rpm"/><format/></package></metadata>'
        ).encode()
        pkgids = repository.validate_primary(lock, xml, {package["filename"]: package})
        self.assertEqual(pkgids, {"a" * 64: ("pkg", "noarch")})
        with self.assertRaisesRegex(repository.RepositoryError, "identity"):
            repository.validate_primary(
                lock,
                xml.replace(b'package="123"', b'package="124"'),
                {package["filename"]: package},
            )

    def test_filelists_and_other_require_the_same_complete_pkgid_set(self) -> None:
        pkgids = {
            "a" * 64: ("a", "noarch"),
            "b" * 64: ("b", "noarch"),
        }
        for namespace, root_name, label in (
            (repository.FILELISTS_NS, "filelists", "filelists metadata"),
            (repository.OTHER_NS, "otherdata", "other metadata"),
        ):
            xml = (
                f'<{root_name} xmlns="{namespace}" packages="2">'
                f'<package pkgid="{"a" * 64}" name="a" arch="noarch"/>'
                f'<package pkgid="{"b" * 64}" name="b" arch="noarch"/>'
                f'</{root_name}>'
            ).encode()
            repository.validate_auxiliary(xml, namespace, label, pkgids)
            with self.assertRaisesRegex(repository.RepositoryError, "package set"):
                repository.validate_auxiliary(
                    xml.replace(b"b" * 64, b"c" * 64),
                    namespace,
                    label,
                    pkgids,
                )

    def test_generator_requires_exact_binary_nvr_and_clean_rpm_verify(self) -> None:
        lock = repository.load_lock(LOCK)
        data = b"generator"
        candidate = copy.deepcopy(lock)
        candidate["generator"]["executable_size"] = len(data)
        candidate["generator"]["executable_sha256"] = hashlib.sha256(data).hexdigest()
        responses = [
            f"{lock['generator']['nvr']}\n".encode(),
            b"",
        ]
        with (
            mock.patch.object(repository, "safe_executable", return_value=data),
            mock.patch.object(repository, "run_bounded", side_effect=responses),
        ):
            repository.verify_generator(
                candidate, Path("/usr/bin/createrepo_c"), Path("/usr/bin/rpm")
            )

    def test_evaluate_requires_two_distinct_results_and_preserves_unsigned_boundary(self) -> None:
        lock = repository.load_lock(LOCK)
        archive_lock = {
            "records_sha256": lock["archive"]["records_sha256"],
            "package_count": 322,
        }
        with tempfile.TemporaryDirectory(prefix="pds020-repository-results-") as name:
            first = Path(name) / "a"
            second = Path(name) / "b"
            first.mkdir(mode=0o700)
            second.mkdir(mode=0o700)
            result = {
                "repomd_sha256": "a" * 64,
                "metadata_file_count": 3,
                "package_count": 322,
                "primary_location_checksum_sha256": "b" * 64,
            }
            with (
                mock.patch.object(repository, "load_lock", return_value=lock),
                mock.patch.object(repository.repro, "read_regular", return_value=b"archive"),
                mock.patch.object(repository.hashlib, "sha256") as digest,
                mock.patch.object(repository.archive_verifier, "load_lock", return_value=archive_lock),
                mock.patch.object(
                    repository.archive_verifier,
                    "verify",
                    return_value={"verified": True},
                ),
                mock.patch.object(repository, "verify_generator"),
                mock.patch.object(repository, "verify_result", return_value=result),
            ):
                digest.return_value.hexdigest.return_value = lock["archive"]["lock_sha256"]
                report = repository.evaluate(
                    Path("/repo-lock"),
                    Path("/archive-lock"),
                    Path("/archive"),
                    first,
                    second,
                    Path("/source"),
                    Path("/build"),
                    Path("/createrepo_c"),
                    Path("/rpm"),
                    Path("/rpmkeys"),
                    Path("/rpm2archive"),
                )
            self.assertTrue(report["metadata_reproducible"])
            self.assertFalse(report["detached_repomd_signature_verified"])
            self.assertFalse(report["project_rpm_signature_verified"])
            self.assertFalse(report["release_ready"])

    def test_verifier_cannot_generate_sign_download_install_or_write(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "subprocess.Popen",
            "shell=True",
            "urlopen",
            "requests.",
            "curl ",
            "wget ",
            '"install",',
            '"--sign"',
            "gpg --",
            "os.O_WRONLY",
            "write_text(",
            "write_bytes(",
        ):
            self.assertNotIn(forbidden, source)
        for required in (
            "archive_verifier.verify(",
            '"metadata_reproducible": True',
            '"detached_repomd_signature_verified": False',
            '"project_rpm_signature_verified": False',
            '"release_ready": False',
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

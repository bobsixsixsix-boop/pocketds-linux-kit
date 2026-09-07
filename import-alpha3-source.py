from pathlib import Path, PurePosixPath
import hashlib, json, tarfile
ARCHIVE_SHA='1556920a2e891a8cc2051fbfffbb979682fcd947bbea668562067d442d92562e'
MANIFEST_SHA='6123ae42f129812d4c3d6203a67da1fa4ca17d461a785e85e668cd94fb8395b0'
SOURCE_COMMIT='b7f5e71f03a9a37acab4813ecbd1a9c7c0b3ef75'
SOURCE_COUNT=1451
OLD_SOURCE_COMMIT='f8d633ae1335f6837b09236930b71e47d680c0a8'
OLD_MANIFEST_SHA='086d9aca3b3964abbacce3b913cfda084ee46a36a36bb6cc6ce67cd49aed5afe'
OLD_README_SHA='e7192168898f24d1c155593cdb8e801123cd1cabe5f5873e27e940204ca9e358'

def sha(data):
    return hashlib.sha256(data).hexdigest()
assert not Path(".github/alpha3-source-import-complete.json").exists()
old_manifest = Path("docs/release/source-preview-manifest.json")
assert sha(old_manifest.read_bytes()) == OLD_MANIFEST_SHA
old = json.loads(old_manifest.read_text())
assert old["source_commit"] == OLD_SOURCE_COMMIT
for row in old["files"]:
    path = Path(row["path"])
    expected = OLD_README_SHA if row["path"] == "README.md" else row["sha256"]
    assert path.is_file() and not path.is_symlink() and sha(path.read_bytes()) == expected
archive = Path("alpha3-source.tar.gz")
manifest = Path("alpha3-source-manifest.json")
assert sha(archive.read_bytes()) == ARCHIVE_SHA
assert sha(manifest.read_bytes()) == MANIFEST_SHA
m = json.loads(manifest.read_text())
assert m["source_commit"] == SOURCE_COMMIT
rows = {r["path"]: r for r in m["files"]}
assert len(rows) == len(m["files"]) == SOURCE_COUNT
payload = {}
with tarfile.open(archive, "r:gz") as tar:
    for item in tar.getmembers():
        assert item.isfile() and item.name.startswith("source/")
        name = item.name.removeprefix("source/")
        path = PurePosixPath(name)
        assert not path.is_absolute() and str(path) == name
        assert not any(x in ("..", ".git", ".github") for x in path.parts)
        assert name in rows and name not in payload
        data = tar.extractfile(item).read()
        row = rows[name]
        assert len(data) == row["size"] and sha(data) == row["sha256"]
        assert row["mode"] in ("0644", "0755") and item.mode == int(row["mode"], 8)
        payload[name] = (data, item.mode)
assert set(payload) == set(rows)
for row in old["files"]:
    if row["path"] not in rows:
        Path(row["path"]).unlink()
for name, (data, mode) in payload.items():
    dest = Path(name)
    assert not dest.is_symlink() and not any(p.is_symlink() for p in dest.parents)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    dest.chmod(mode)
for name, row in rows.items():
    assert sha(Path(name).read_bytes()) == row["sha256"]
Path("docs/release/source-preview-manifest.json").write_bytes(manifest.read_bytes())
receipt = {
    "schema": "pocketds.public-source-import.v2",
    "source_commit": SOURCE_COMMIT,
    "files_verified": SOURCE_COUNT,
    "original_git_history_imported": False,
    "archive_sha256": ARCHIVE_SHA,
    "previous_source_commit": OLD_SOURCE_COMMIT,
}
Path(".github/alpha3-source-import-complete.json").write_text(json.dumps(receipt, indent=2) + "\n")
for path in (archive, manifest, Path("import-alpha3-source.py")):
    path.unlink()
print("Verified and imported", SOURCE_COUNT, "Alpha3 source files with no original Git history.")

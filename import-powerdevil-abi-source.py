from pathlib import Path, PurePosixPath
import hashlib, json, tarfile
ARCHIVE_SHA='cc356298d628b2f7eb8e3232e7905ca61cac472e4dd237d67f9c50341555d87a'
MANIFEST_SHA='06682725dc759bccec52a229e3636dda6b95d4557dbd63411f1834c004ce350c'
SOURCE_COMMIT='68d0dea21f495b0266e8ced4c79dbd6f3cbe7d5f'
SOURCE_COUNT=1459
OLD_SOURCE_COMMIT='b7f5e71f03a9a37acab4813ecbd1a9c7c0b3ef75'
OLD_MANIFEST_SHA='6123ae42f129812d4c3d6203a67da1fa4ca17d461a785e85e668cd94fb8395b0'
OLD_README_SHA='71a5b84391d872670f126b9fdeb88d2297c11d730bfe55243a1e68c1026e1435'

def sha(data):
    return hashlib.sha256(data).hexdigest()
assert not Path(".github/powerdevil-abi-source-import-complete.json").exists()
old_manifest = Path("docs/release/source-preview-manifest.json")
assert sha(old_manifest.read_bytes()) == OLD_MANIFEST_SHA
old = json.loads(old_manifest.read_text())
assert old["source_commit"] == OLD_SOURCE_COMMIT
for row in old["files"]:
    path = Path(row["path"])
    expected = OLD_README_SHA if row["path"] == "README.md" else row["sha256"]
    assert path.is_file() and not path.is_symlink() and sha(path.read_bytes()) == expected
archive = Path("powerdevil-abi-source.tar.gz")
manifest = Path("powerdevil-abi-source-manifest.json")
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
Path(".github/powerdevil-abi-source-import-complete.json").write_text(json.dumps(receipt, indent=2) + "\n")
for path in (archive, manifest, Path("import-powerdevil-abi-source.py")):
    path.unlink()
print("Verified and imported", SOURCE_COUNT, "reviewed source files with no original Git history.")

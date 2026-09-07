#!/usr/bin/env python3
"""Export a clean HEAD as an asset-free source draft, never a publication.

The byte-pattern scan is a release aid, not a complete secret scanner: it cannot
recognize arbitrary, encoded or split credentials. Ignored files, old commits,
external caches, licensing clearance and permission to publish are out of scope.
"""
from __future__ import annotations

import argparse
import gzip
import fnmatch
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile


EXCLUDED_PREFIXES = ("assets/", "work/", ".github/")
EXCLUDED_FILES = (
    "components/game-runtime/pocketds-gamescope-observer",
    "docs/release/source-preview-manifest.json",
)
EXCLUDED_GLOBS = ("*-personal-preset.json", "*/asr-api/config.json", "*/asr-api-config.json")
ASSET_MANIFEST = "components/assets/ASSETS.json"
ASSET_BYTES = b'{"schema":1,"profile":"asset-free","assets":[]}\n'
PATTERNS = {
    "asr-access-code": re.compile(rb"\bvb-[0-9a-fA-F]{10,}\b"),
    "private-key": re.compile(rb"-----BEG" rb"IN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    "github-token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "api-token": re.compile(rb"\bsk-(?:(?:proj|svcacct)-)?[A-Za-z0-9_-]{32,}\b"),
    "slack-token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "aws-access-key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "google-api-key": re.compile(rb"\bAIza[A-Za-z0-9_-]{35}\b"),
    "macos-home": re.compile(b"/" + b"Users/" + rb"(?!<[^/\r\n]+>)[^/\s\"'`<>]+"),
}


class ExportError(RuntimeError):
    pass


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo), *args],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=True, timeout=120,
        )
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        raise ExportError("git inspection failed") from None


def require_clean(repo: Path, commit: str | None = None) -> str:
    head = git(repo, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    if commit is not None and head != commit:
        raise ExportError("HEAD changed during export")
    if git(repo, "status", "--porcelain=v1", "--untracked-files=normal"):
        raise ExportError("source worktree must be clean, including untracked files")
    return head


def entries(repo: Path, commit: str) -> list[tuple[str, int, str]]:
    result = []
    for record in git(repo, "ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
        if not record:
            continue
        header, raw_path = record.split(b"\t", 1)
        mode, kind, oid = header.split()
        try:
            path = raw_path.decode("utf-8")
        except UnicodeError:
            raise ExportError("tracked path is not UTF-8") from None
        parts = path.split("/")
        if (PurePosixPath(path).is_absolute() or "\\" in path
                or any(part in ("", ".", "..", ".git") for part in parts)
                or any(ord(char) < 32 or ord(char) == 127 for char in path)):
            raise ExportError("unsafe tracked path")
        if kind != b"blob" or mode not in (b"100644", b"100755"):
            raise ExportError(f"{path}: unsupported tracked mode (links are not exported)")
        if (path in EXCLUDED_FILES or path.startswith(EXCLUDED_PREFIXES)
                or any(fnmatch.fnmatchcase(path, pattern) for pattern in EXCLUDED_GLOBS)):
            continue
        if path != ASSET_MANIFEST:
            result.append((path, int(mode, 8) & 0o777, oid.decode("ascii")))
    return sorted(result)


def read_blobs(repo: Path, selected: list[tuple[str, int, str]]) -> dict[str, bytes]:
    objects = list(dict.fromkeys(oid for _, _, oid in selected))
    payload = git(repo, "cat-file", "--batch", data="".join(oid + "\n" for oid in objects).encode("ascii"))
    result = {}
    offset = 0
    for oid in objects:
        end = payload.find(b"\n", offset)
        header = payload[offset:end].split()
        if len(header) != 3 or header[:2] != [oid.encode("ascii"), b"blob"]:
            raise ExportError("invalid git blob response")
        size = int(header[2])
        start = end + 1
        result[oid] = payload[start:start + size]
        if len(result[oid]) != size or payload[start + size:start + size + 1] != b"\n":
            raise ExportError("incomplete git blob response")
        offset = start + size + 1
    if offset != len(payload):
        raise ExportError("unexpected git blob response")
    return result


def scan(files: dict[str, tuple[int, bytes]]) -> list[dict[str, str]]:
    findings = [{"path": path, "category": category}
                for path, (_, content) in sorted(files.items())
                for category, pattern in PATTERNS.items() if pattern.search(content)]
    # User-defined providers may issue keys with no recognizable token prefix.
    configured_key = re.compile(rb'"api_key"\s*:\s*"(?:[^"\\]|\\.)+"', re.IGNORECASE)
    findings.extend({"path": path, "category": "configured-api-key"}
                    for path, (_, content) in sorted(files.items())
                    if path.endswith(".json") and configured_key.search(content))
    return findings


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def export(repo: Path, output: Path) -> dict:
    repo = Path(git(repo, "rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ExportError("output must not already exist")
    output = output.resolve()
    if output == repo or repo in output.parents:
        raise ExportError("output must be outside the source repository")
    if not output.parent.is_dir():
        raise ExportError("output parent directory must already exist")
    commit = require_clean(repo)
    timestamp = int(git(repo, "show", "-s", "--format=%ct", commit).strip())
    selected = entries(repo, commit)
    blobs = read_blobs(repo, selected)
    files = {path: (mode, blobs[oid]) for path, mode, oid in selected}
    files[ASSET_MANIFEST] = (0o644, ASSET_BYTES)
    findings = scan(files)
    if findings:
        # Never include a matching value, source line or git stderr.
        raise ExportError("suspected private data: " + json.dumps(findings, ensure_ascii=True))
    require_clean(repo, commit)
    records = [{"path": path, "mode": f"{mode:04o}", "sha256": digest(content), "size": len(content)}
               for path, (mode, content) in sorted(files.items())]
    manifest = {
        "schema": 1, "draft_only": True, "publication_authorized": False,
        "source_commit": commit, "source_commit_time": timestamp,
        "profile": "asset-free", "excluded_prefixes": list(EXCLUDED_PREFIXES),
        "excluded_files": list(EXCLUDED_FILES), "generated_files": [ASSET_MANIFEST],
        "excluded_globs": list(EXCLUDED_GLOBS),
        "scan_limitations": "Pattern scan only; arbitrary, encoded or split secrets may be missed. Licensing and publication approval are not assessed.",
        "files": records,
    }
    output.mkdir(mode=0o755)
    try:
        source = output / "source"
        source.mkdir(mode=0o755)
        for path, (mode, content) in sorted(files.items()):
            target = source / path
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(mode)
            os.utime(target, (timestamp, timestamp))
        # Scan the actual tree again before creating the archive.
        written = {record["path"]: (int(record["mode"], 8), (source / record["path"]).read_bytes())
                   for record in records}
        findings = scan(written)
        if findings or any(digest(written[r["path"]][1]) != r["sha256"] for r in records):
            raise ExportError("exported bytes did not pass verification: " + json.dumps(findings))
        archive = output / "source.tar.gz"
        with archive.open("xb") as stream:
            with gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                    for record in records:
                        info = tarfile.TarInfo("source/" + record["path"])
                        info.size = record["size"]
                        info.mode = int(record["mode"], 8)
                        info.mtime = timestamp
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        tar.addfile(info, io.BytesIO(written[record["path"]][1]))
        manifest["archive"] = {"path": archive.name, "sha256": digest(archive.read_bytes())}
        manifest_bytes = (json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()
        (output / "manifest.json").write_bytes(manifest_bytes)
        sums = [f"{r['sha256']}  source/{r['path']}" for r in records]
        sums.extend((f"{manifest['archive']['sha256']}  source.tar.gz",
                     f"{digest(manifest_bytes)}  manifest.json"))
        (output / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
        return manifest
    except BaseException:
        shutil.rmtree(output)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = export(args.repo, args.output)
    except (ExportError, OSError, ValueError) as error:
        # All deliberate diagnostics are path/category only; OS failures omit
        # their source text, which can include environment-specific details.
        print(str(error) if isinstance(error, ExportError) else "source export failed")
        return 1
    print(json.dumps({"status": "draft_only", "source_commit": manifest["source_commit"],
                      "files": len(manifest["files"]), "archive_sha256": manifest["archive"]["sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

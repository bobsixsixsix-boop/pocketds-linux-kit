#!/usr/bin/env python3
"""Read-only PowerDevil ELF relocation checks against this system's runtime.

--installed reads the installed RPM file table (including file states).
--root scans a trusted, root-owned unpacked RPM directory before installation.
No staged library directory is added to the loader environment. Existing ELF
RPATH/RUNPATH still applies; resolutions inside the staged package are reported.
This checks loader compatibility, not QML behavior or physical sleep/resume.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import time


MAX_ENTRIES = 2048
MAX_ELFS = 128
MAX_ELF_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
COMMAND_TIMEOUT = 4
TOTAL_TIMEOUT = 60
LOADER_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"}


class CheckError(Exception):
    def __init__(self, kind, path=None):
        self.kind, self.path = kind, path

    def record(self):
        return {"kind": self.kind, **({"path": self.path} if self.path else {})}


def run_command(argv, *, timeout, env):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=timeout, env=env, check=False)
    if len(result.stdout) > MAX_OUTPUT_BYTES:
        raise CheckError("command-output-limit")
    return result.returncode, result.stdout.decode("utf-8", errors="replace")


def remaining(deadline, clock):
    seconds = deadline - clock()
    if seconds <= 0:
        raise CheckError("total-timeout")
    return min(COMMAND_TIMEOUT, seconds)


def regular_path(value):
    path = PurePosixPath(value)
    if (not path.is_absolute() or ".." in path.parts
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise CheckError("invalid-package-path")
    return path.as_posix()


def rpm_paths(runner, deadline, clock):
    code, output = runner(
        ["/usr/bin/rpm", "-q", "--queryformat", "[%{FILESTATES}\t%{FILENAMES}\n]", "powerdevil"],
        timeout=remaining(deadline, clock), env=LOADER_ENV.copy(),
    )
    if code:
        raise CheckError("rpm-query-failed")
    if len(output.encode()) > MAX_OUTPUT_BYTES:
        raise CheckError("command-output-limit")
    lines = output.splitlines()
    if not lines or len(lines) > MAX_ENTRIES:
        raise CheckError("package-entry-limit")
    paths, skipped = [], 0
    for line in lines:
        state, separator, value = line.partition("\t")
        if not separator:
            raise CheckError("invalid-rpm-file-table")
        path = regular_path(value)
        if state == "2":  # RPMFILE_STATE_NOTINSTALLED, e.g. excluded translations.
            skipped += 1
        elif state == "0":
            paths.append(path)
        else:
            raise CheckError("rpm-file-state", path)
    return paths, skipped


def owned_path(root, package_path, owner_uid):
    """Resolve aliases within the package root; inspect each component/hop.

    The explicitly selected root is the trust boundary. Absolute RPM symlinks
    are interpreted inside that root, never silently against the host /usr.
    """
    metadata = root.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != owner_uid
            or metadata.st_mode & 0o022):
        raise CheckError("unsafe-root")
    current, pending, hops = root, list(PurePosixPath(package_path).parts[1:]), 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if current == root:
                raise CheckError("symlink-escape", package_path)
            current = current.parent
            continue
        candidate = current / part
        metadata = candidate.lstat()
        if metadata.st_uid != owner_uid:
            raise CheckError("unsafe-owner", package_path)
        if stat.S_ISLNK(metadata.st_mode):
            hops += 1
            if hops > 16:
                raise CheckError("symlink-loop", package_path)
            target = PurePosixPath(os.readlink(candidate))
            if target.is_absolute():
                current = root
                pending = list(target.parts[1:]) + pending
            else:
                pending = list(target.parts) + pending
            continue
        if metadata.st_mode & 0o022:
            raise CheckError("writable-path", package_path)
        if not stat.S_ISDIR(metadata.st_mode) and not stat.S_ISREG(metadata.st_mode):
            raise CheckError("non-regular-path", package_path)
        if pending and not stat.S_ISDIR(metadata.st_mode):
            raise CheckError("non-directory-parent", package_path)
        current = candidate
    return current, current.stat()


def staged_paths(root, deadline, clock):
    paths = []

    def walk_error(_error):
        raise CheckError("stage-enumeration-failed")

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        remaining(deadline, clock)
        for name in sorted(dirs + files):
            paths.append("/" + (Path(directory) / name).relative_to(root).as_posix())
            if len(paths) > MAX_ENTRIES:
                raise CheckError("package-entry-limit")
    return paths


def fingerprint(metadata):
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns,
            metadata.st_ctime_ns, metadata.st_uid, metadata.st_mode)


def elf_checksum(actual, metadata, package_path):
    fd = os.open(actual, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if fingerprint(os.fstat(fd)) != fingerprint(metadata):
            raise CheckError("file-changed", package_path)
        digest, size = hashlib.sha256(), 0
        while chunk := os.read(fd, min(65536, metadata.st_size + 1 - size)):
            size += len(chunk)
            if size > metadata.st_size:
                raise CheckError("file-changed", package_path)
            digest.update(chunk)
        if size != metadata.st_size or fingerprint(os.fstat(fd)) != fingerprint(metadata):
            raise CheckError("file-changed", package_path)
        return digest.hexdigest()
    finally:
        os.close(fd)


def collect_elfs(root, paths, owner_uid, deadline, clock):
    records, seen, total = [], {}, 0
    for package_path in sorted(set(paths)):
        remaining(deadline, clock)
        try:
            actual, metadata = owned_path(root, package_path, owner_uid)
        except FileNotFoundError:
            # Documentation/icon links may target another RPM and be absent
            # from a staged package. Binary paths and build-id aliases remain
            # mandatory; this is not a general replacement for rpm -V.
            if (root != Path("/") and (root / package_path.lstrip("/")).is_symlink()
                    and not package_path.startswith(("/usr/lib", "/usr/bin/", "/usr/sbin/"))):
                continue
            raise
        if stat.S_ISDIR(metadata.st_mode):
            continue
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in seen:
            seen[identity]["aliases"].append(package_path)
            continue
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        fd = os.open(actual, flags)
        try:
            if fingerprint(os.fstat(fd)) != fingerprint(metadata):
                raise CheckError("file-changed", package_path)
            if os.read(fd, 4) != b"\x7fELF":
                continue
        finally:
            os.close(fd)
        total += metadata.st_size
        if (metadata.st_size > MAX_ELF_BYTES or total > MAX_TOTAL_BYTES
                or len(records) >= MAX_ELFS):
            raise CheckError("elf-size-or-count-limit", package_path)
        record = {"path": "/" + actual.relative_to(root).as_posix(),
                  "aliases": [package_path], "actual": actual,
                  "fingerprint": fingerprint(metadata), "size": metadata.st_size,
                  "sha256": elf_checksum(actual, metadata, package_path)}
        records.append(record)
        seen[identity] = record
    if not records:
        raise CheckError("no-elf-files")
    return records


def relocation_errors(code, output, package_names):
    errors = []
    for match in re.finditer(r"undefined symbol:\s*([^\s,()]+)(?:,\s*version\s+([^\s()]+))?", output):
        item = {"kind": "undefined-symbol", "symbol": match[1]}
        if match[2]:
            item["version"] = match[2]
        errors.append(item)
    for match in re.finditer(r"^\s*(\S+)\s+=>\s+not found(?:\s|$)", output, re.MULTILINE):
        errors.append({"kind": "package-library-not-found" if match[1] in package_names
                       else "library-not-found", "library": match[1]})
    for match in re.finditer(r"version [`']([^`']+)[`'] not found", output):
        errors.append({"kind": "symbol-version-not-found", "version": match[1]})
    # Fail closed on unfamiliar loader diagnostics, even with a zero exit code.
    if not errors and ("undefined symbol" in output or "not found" in output):
        errors.append({"kind": "unresolved-loader-error"})
    if code and not errors:
        errors.append({"kind": "ldd-failed", "exit_code": code})
    return errors


def check_runtime(*, root=None, runner=run_command, owner_uid=0, clock=time.monotonic,
                  expected_elf_count=None):
    """owner_uid is injectable only for isolated fixtures, never via the CLI."""
    staged = root is not None
    boundary = Path(root).absolute() if staged else Path("/")
    deadline = clock() + TOTAL_TIMEOUT
    report = {"schema": 1, "mode": "staged" if staged else "installed", "status": "fail",
              "files": [], "errors": [], "rpm_not_installed_entries": 0, "elf_count": 0,
              "staged_library_path_added": False}
    try:
        if staged and boundary == Path("/"):
            raise CheckError("invalid-stage-root")
        owned_path(boundary, "/", owner_uid)
        if staged:
            paths = staged_paths(boundary, deadline, clock)
        else:
            paths, report["rpm_not_installed_entries"] = rpm_paths(runner, deadline, clock)
        records = collect_elfs(boundary, paths, owner_uid, deadline, clock)
        report["elf_count"] = len(records)
        if expected_elf_count is not None and len(records) != expected_elf_count:
            raise CheckError("unexpected-elf-count")
        package_names = {Path(alias).name for record in records for alias in record["aliases"]}
        for record in records:
            item = {"path": record["path"], "aliases": record["aliases"],
                    "size": record["size"], "sha256": record["sha256"],
                    "status": "fail", "errors": [], "package_library_resolutions": []}
            report["files"].append(item)
            try:
                actual, metadata = owned_path(boundary, record["path"], owner_uid)
                if fingerprint(metadata) != record["fingerprint"]:
                    raise CheckError("file-changed", record["path"])
                code, output = runner(["/usr/bin/ldd", "-r", str(actual)],
                                      timeout=remaining(deadline, clock), env=LOADER_ENV.copy())
                if len(output.encode()) > MAX_OUTPUT_BYTES:
                    raise CheckError("command-output-limit", record["path"])
                item["errors"] = relocation_errors(code, output, package_names)
                if staged:
                    for match in re.finditer(r"^\s*(\S+)\s+=>\s+(/\S+)", output, re.MULTILINE):
                        resolved = Path(match[2])
                        if boundary in resolved.parents:
                            item["package_library_resolutions"].append({
                                "library": match[1], "path": "/" + resolved.relative_to(boundary).as_posix()})
                _, after = owned_path(boundary, record["path"], owner_uid)
                if fingerprint(after) != record["fingerprint"]:
                    raise CheckError("file-changed", record["path"])
                if not item["errors"]:
                    item["status"] = "pass"
            except subprocess.TimeoutExpired:
                item["errors"].append({"kind": "ldd-timeout"})
            except CheckError as error:
                item["errors"].append(error.record())
            except OSError:
                item["errors"].append({"kind": "file-or-command-unavailable"})
        report["status"] = "pass" if all(item["status"] == "pass" for item in report["files"]) else "fail"
    except subprocess.TimeoutExpired:
        report["errors"].append({"kind": "rpm-query-timeout"})
    except CheckError as error:
        report["errors"].append(error.record())
    except OSError:
        report["errors"].append({"kind": "file-or-command-unavailable"})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--installed", action="store_true")
    mode.add_argument("--root", type=Path, help="trusted root-owned RPM extraction directory")
    parser.add_argument("--expected-elf-count", type=int, choices=range(1, MAX_ELFS + 1),
                        metavar="N", help="require the full reviewed package ELF count (22 for 6.7.3)")
    args = parser.parse_args()
    report = check_runtime(root=args.root, expected_elf_count=args.expected_elf_count)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

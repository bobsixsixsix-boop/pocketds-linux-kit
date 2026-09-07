#!/usr/bin/env python3
"""Restore zh_CN catalogs omitted by the original minimal RPM transaction.

The device image installed existing packages with language payload filtering.
This tool downloads those exact installed NEVRAs from Fedora Koji, validates
their Fedora RPM signatures, stages only the missing zh_CN files, and refuses
to overwrite any path or change the RPM package set.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request


CONFIRM = "POCKETDS-RESTORE-ZH-CN-CATALOGS"
KOJI_BASE = "https://kojipkgs.fedoraproject.org/packages"
LOCALE_ROOT = pathlib.Path("/usr/share/locale/zh_CN/LC_MESSAGES")
LOCALE_PREFIX = f"{LOCALE_ROOT}/"
MAX_CATALOG_SIZE = 32 * 1024 * 1024
MAX_PACKAGES = 512
MAX_FILES = 4096


class RestoreError(RuntimeError):
    """An invariant failed before or during catalog restoration."""


def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=True, text=True, **kwargs)


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rpm_query_format() -> str:
    return "%{NAME}\n%{EPOCHNUM}\n%{VERSION}\n%{RELEASE}\n%{ARCH}\n%{SOURCERPM}\n"


def package_url(package: dict[str, object]) -> str:
    quote = lambda value: urllib.parse.quote(str(value), safe="._+~")
    return "/".join(
        (
            KOJI_BASE,
            quote(package["source_name"]),
            quote(package["version"]),
            quote(package["release"]),
            quote(package["arch"]),
            quote(package["rpm_filename"]),
        )
    )


def installed_packages() -> tuple[list[dict[str, object]], list[str]]:
    try:
        import rpm  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RestoreError("python3-rpm is required on the Fedora device") from exc

    packages: list[dict[str, object]] = []
    all_targets: list[str] = []
    seen_targets: set[str] = set()
    transaction_set = rpm.TransactionSet()
    for header in transaction_set.dbMatch():
        targets = sorted(
            path
            for path in (header["filenames"] or [])
            if path.startswith(LOCALE_PREFIX) and not os.path.lexists(path)
        )
        if not targets:
            continue
        name = str(header["name"])
        version = str(header["version"])
        release = str(header["release"])
        arch = str(header["arch"])
        source_rpm = str(header["sourcerpm"])
        source_suffix = f"-{version}-{release}.src.rpm"
        if not source_rpm.endswith(source_suffix):
            raise RestoreError(f"cannot derive source package from {source_rpm}")
        source_name = source_rpm[: -len(source_suffix)]
        overlap = seen_targets.intersection(targets)
        if overlap:
            raise RestoreError(f"duplicate catalog ownership: {sorted(overlap)[0]}")
        seen_targets.update(targets)
        all_targets.extend(targets)
        rpm_filename = f"{name}-{version}-{release}.{arch}.rpm"
        packages.append(
            {
                "name": name,
                "epoch": int(header["epochnum"] or 0),
                "version": version,
                "release": release,
                "arch": arch,
                "source_rpm": source_rpm,
                "source_name": source_name,
                "rpm_filename": rpm_filename,
                "targets": targets,
            }
        )
    packages.sort(key=lambda item: str(item["name"]))
    if len(packages) > MAX_PACKAGES or len(all_targets) > MAX_FILES:
        raise RestoreError(
            f"catalog scope exceeds limits: {len(packages)} packages, "
            f"{len(all_targets)} files"
        )
    return packages, sorted(all_targets)


def protected_packages() -> list[str]:
    output = run(
        [
            "rpm",
            "-qa",
            "--qf",
            "%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\\n",
        ],
        capture_output=True,
    ).stdout
    pattern = re.compile(r"^(kernel|kwin|mesa|plasma|qt6-|linux-firmware)")
    return sorted(line for line in output.splitlines() if pattern.match(line))


def download_one(package: dict[str, object], package_dir: pathlib.Path) -> dict[str, object]:
    destination = package_dir / str(package["rpm_filename"])
    partial = destination.with_suffix(destination.suffix + ".part")
    url = package_url(package)
    request = urllib.request.Request(
        url, headers={"User-Agent": "PocketDS-Chinese-Catalog-Restore/1"}
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=45) as response, partial.open(
                "wb"
            ) as output:
                if response.status != 200:
                    raise RestoreError(f"HTTP {response.status} for {url}")
                shutil.copyfileobj(response, output, length=1024 * 1024)
            partial.replace(destination)
            break
        except Exception as exc:  # urllib exposes several transport exceptions
            last_error = exc
            partial.unlink(missing_ok=True)
            if attempt == 2:
                raise RestoreError(f"download failed for {url}: {exc}") from exc
            time.sleep(attempt + 1)
    if last_error is not None and not destination.exists():
        raise RestoreError(f"download failed for {url}: {last_error}")

    signature = run(
        ["rpmkeys", "--checksig", str(destination)], capture_output=True
    ).stdout.strip()
    query = run(
        ["rpm", "-qp", "--qf", rpm_query_format(), str(destination)],
        capture_output=True,
    ).stdout.splitlines()
    expected = [
        str(package["name"]),
        str(package["epoch"]),
        str(package["version"]),
        str(package["release"]),
        str(package["arch"]),
        str(package["source_rpm"]),
    ]
    if query != expected:
        raise RestoreError(f"downloaded RPM identity mismatch for {destination.name}")
    result = dict(package)
    result.update(
        {
            "url": url,
            "rpm_sha256": sha256(destination),
            "rpm_signature": signature,
            "rpm_path": str(destination),
        }
    )
    return result


def extract_catalogs(package: dict[str, object], extract_root: pathlib.Path) -> list[dict[str, object]]:
    package_root = extract_root / str(package["name"])
    package_root.mkdir(mode=0o700)
    patterns = [f".{path}" for path in package["targets"]]  # type: ignore[index]
    producer = subprocess.Popen(
        ["rpm2cpio", str(package["rpm_path"])], stdout=subprocess.PIPE
    )
    assert producer.stdout is not None
    consumer = subprocess.run(
        ["cpio", "-idm", "--quiet", "--no-absolute-filenames", *patterns],
        cwd=package_root,
        stdin=producer.stdout,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=False,
    )
    producer.stdout.close()
    producer_status = producer.wait()
    if producer_status != 0 or consumer.returncode != 0:
        error = consumer.stderr.decode("utf-8", errors="replace").strip()
        raise RestoreError(f"catalog extraction failed for {package['name']}: {error}")

    catalogs: list[dict[str, object]] = []
    for target in package["targets"]:  # type: ignore[assignment]
        staged = package_root / str(target).lstrip("/")
        try:
            metadata = staged.lstat()
        except FileNotFoundError as exc:
            raise RestoreError(f"RPM payload omitted expected catalog: {target}") from exc
        if stat.S_ISREG(metadata.st_mode):
            if metadata.st_size <= 0 or metadata.st_size > MAX_CATALOG_SIZE:
                raise RestoreError(f"catalog size is outside bounds: {target}")
            catalogs.append(
                {
                    "type": "file",
                    "target": target,
                    "staged": str(staged),
                    "size": metadata.st_size,
                    "sha256": sha256(staged),
                    "package": package["name"],
                }
            )
            continue
        if stat.S_ISLNK(metadata.st_mode):
            link_target = os.readlink(staged)
            if (
                pathlib.PurePath(link_target).is_absolute()
                or pathlib.PurePath(link_target).name != link_target
                or link_target in ("", ".", "..")
            ):
                raise RestoreError(f"catalog has unsafe symlink target: {target}")
            catalogs.append(
                {
                    "type": "symlink",
                    "target": target,
                    "staged": str(staged),
                    "link_target": link_target,
                    "package": package["name"],
                }
            )
            continue
        raise RestoreError(f"catalog has unsupported file type: {target}")
    return catalogs


def write_receipt(
    path: pathlib.Path,
    status: str,
    packages: list[dict[str, object]],
    catalogs: list[dict[str, object]],
    protected_before: list[str],
    protected_after: list[str] | None = None,
) -> None:
    public_packages = []
    for package in packages:
        public = {key: value for key, value in package.items() if key != "rpm_path"}
        public_packages.append(public)
    payload = {
        "schema_version": 1,
        "status": status,
        "locale": "zh_CN",
        "source": "Fedora Koji exact installed NEVRA",
        "packages": public_packages,
        "catalogs": [
            {key: value for key, value in catalog.items() if key != "staged"}
            for catalog in catalogs
        ],
        "protected_packages_before": protected_before,
        "protected_packages_after": protected_after,
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if os.geteuid() == 0:
        raise RestoreError("run as the Pocket DS desktop user, not root")
    for command in ("cpio", "rpm", "rpm2cpio", "rpmkeys", "sudo"):
        if shutil.which(command) is None:
            raise RestoreError(f"missing required command: {command}")
    if platform.machine() != "aarch64":
        raise RestoreError(f"refusing non-aarch64 host: {platform.machine()}")
    fedora = run(["rpm", "-E", "%fedora"], capture_output=True).stdout.strip()
    if fedora != "44":
        raise RestoreError(f"refusing unverified Fedora release: {fedora}")
    if LOCALE_ROOT.resolve() != LOCALE_ROOT:
        raise RestoreError(f"locale root is not canonical: {LOCALE_ROOT}")

    packages, targets = installed_packages()
    print(
        f"[catalog plan] {len(packages)} exact signed RPMs, "
        f"{len(targets)} missing zh_CN files"
    )
    print("  source  Fedora Koji (exact installed name/version/release/arch)")
    print("  write   absent /usr/share/locale/zh_CN files only")
    print("  safety  no overwrite, package install, upgrade or boot change")
    if not targets:
        print("[catalog verify] nothing missing")
        return 0
    if not args.apply:
        print(f"Re-run with --apply --confirm {CONFIRM}")
        return 0
    if args.confirm != CONFIRM:
        raise RestoreError(f"confirmation must be {CONFIRM}")
    run(["sudo", "-n", "true"])

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    transaction = (
        pathlib.Path.home()
        / ".local/state/pocketds-linux-kit/locale-transactions"
        / f"zh-cn-catalogs-{stamp}"
    )
    transaction.mkdir(mode=0o700, parents=True)
    package_dir = transaction / "packages"
    extract_root = transaction / "extracted"
    package_dir.mkdir(mode=0o700)
    extract_root.mkdir(mode=0o700)
    protected_before = protected_packages()

    print("[catalog stage] downloading and verifying exact Fedora RPMs")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(download_one, package, package_dir): package
            for package in packages
        }
        downloaded = []
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            downloaded.append(future.result())
            if index % 25 == 0 or index == len(futures):
                print(f"  verified {index}/{len(futures)} RPMs", flush=True)
    downloaded.sort(key=lambda item: str(item["name"]))

    catalogs: list[dict[str, object]] = []
    for index, package in enumerate(downloaded, 1):
        catalogs.extend(extract_catalogs(package, extract_root))
        if index % 25 == 0 or index == len(downloaded):
            print(f"  extracted {index}/{len(downloaded)} RPMs", flush=True)
    catalogs.sort(key=lambda item: str(item["target"]))
    if [catalog["target"] for catalog in catalogs] != targets:
        raise RestoreError("staged catalog set does not match the RPM database plan")
    target_set = set(targets)
    for catalog in catalogs:
        if catalog["type"] != "symlink":
            continue
        resolved = str(
            (
                pathlib.Path(str(catalog["target"])).parent
                / str(catalog["link_target"])
            ).resolve(strict=False)
        )
        if not resolved.startswith(f"{LOCALE_ROOT}/"):
            raise RestoreError(f"catalog symlink escapes locale root: {catalog['target']}")
        if resolved not in target_set and not os.path.isfile(resolved):
            raise RestoreError(f"catalog symlink target is unavailable: {resolved}")
    if any(os.path.lexists(str(catalog["target"])) for catalog in catalogs):
        raise RestoreError("a catalog target appeared while staging; refusing overwrite")
    write_receipt(
        transaction / "receipt.json",
        "staged",
        downloaded,
        catalogs,
        protected_before,
    )

    print("[catalog apply] installing verified translation files")
    installed: list[str] = []
    try:
        for catalog in catalogs:
            target = str(catalog["target"])
            if os.path.lexists(target):
                raise RestoreError(f"refusing overwrite: {target}")
            if catalog["type"] == "file":
                run(
                    [
                        "sudo",
                        "install",
                        "-D",
                        "-m",
                        "0644",
                        str(catalog["staged"]),
                        target,
                    ]
                )
            else:
                run(["sudo", "ln", "-s", "--", str(catalog["link_target"]), target])
            installed.append(target)
        for catalog in catalogs:
            target = pathlib.Path(str(catalog["target"]))
            if catalog["type"] == "file":
                if not target.is_file() or target.is_symlink():
                    raise RestoreError(f"installed catalog type mismatch: {target}")
                if sha256(target) != catalog["sha256"]:
                    raise RestoreError(f"installed catalog hash mismatch: {target}")
            elif not target.is_symlink() or os.readlink(target) != catalog["link_target"]:
                raise RestoreError(f"installed catalog symlink mismatch: {target}")
        _, still_missing = installed_packages()
        if still_missing:
            raise RestoreError(f"{len(still_missing)} zh_CN catalogs remain missing")
        protected_after = protected_packages()
        if protected_after != protected_before:
            raise RestoreError("protected package inventory changed during catalog restore")
    except Exception:
        for target in reversed(installed):
            subprocess.run(["sudo", "rm", "-f", "--", target], check=False)
        raise
    write_receipt(
        transaction / "receipt.json",
        "verified",
        downloaded,
        catalogs,
        protected_before,
        protected_after,
    )
    print("[catalog verify] PASS")
    print(f"Transaction: {transaction}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RestoreError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

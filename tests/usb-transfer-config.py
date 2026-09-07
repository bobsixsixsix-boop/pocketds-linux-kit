#!/usr/bin/env python3
"""Keep USB MTP limited to the user's intentional transfer directory."""

from pathlib import Path
import shlex


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "components/system/umtprd.conf"
INSTALLER = ROOT / "scripts/install.sh"
EXPECTED_STORAGE = [
    "storage",
    "/home/pocketds/Downloads",
    "Pocket DS Transfer",
    "rw",
]


def directives() -> list[list[str]]:
    parsed = []
    for raw_line in CONFIG.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parsed.append(shlex.split(line, comments=True, posix=True))
    return parsed


def main() -> int:
    parsed = directives()
    storages = [tokens for tokens in parsed if tokens[0] == "storage"]
    if storages != [EXPECTED_STORAGE]:
        raise SystemExit(f"unexpected MTP storage scope: {storages!r}")
    if any(tokens[:2] == ["storage", "/home/pocketds"] for tokens in parsed):
        raise SystemExit("full user home must never be exported over MTP")
    for name, expected in (("default_uid", "1000"), ("default_gid", "1000")):
        values = [tokens for tokens in parsed if tokens[0] == name]
        if values != [[name, expected]]:
            raise SystemExit(f"unexpected {name}: {values!r}")

    installer = INSTALLER.read_text(encoding="utf-8")
    install_call = (
        'install_root_file "$repo_root/components/system/umtprd.conf" \\\n'
        "        /etc/umtprd/umtprd.conf"
    )
    if install_call not in installer:
        raise SystemExit("installer does not bind the reviewed MTP config")
    print("usb-transfer-config: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

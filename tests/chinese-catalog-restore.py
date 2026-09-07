#!/usr/bin/env python3
"""Static safety contract for restoring filtered zh_CN RPM payloads."""

import ast
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts/restore-chinese-catalogs.py"
SOURCE = PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

required = (
    'CONFIRM = "POCKETDS-RESTORE-ZH-CN-CATALOGS"',
    'KOJI_BASE = "https://kojipkgs.fedoraproject.org/packages"',
    'LOCALE_ROOT = pathlib.Path("/usr/share/locale/zh_CN/LC_MESSAGES")',
    'path.startswith(LOCALE_PREFIX) and not os.path.lexists(path)',
    '["rpmkeys", "--checksig", str(destination)]',
    '"rpm", "-qp", "--qf", rpm_query_format()',
    '"--no-absolute-filenames"',
    "if stat.S_ISREG(metadata.st_mode):",
    'pathlib.PurePath(link_target).name != link_target',
    'catalog symlink escapes locale root',
    'if any(os.path.lexists(str(catalog["target"])) for catalog in catalogs):',
    'if sha256(target) != catalog["sha256"]:',
    'if protected_after != protected_before:',
    '"status": status',
)
for fragment in required:
    assert fragment in SOURCE, fragment

for forbidden in (
    "dnf install",
    "dnf update",
    "dnf upgrade",
    "rpm -U",
    "rpm -i",
    "/boot/",
    "fastboot",
):
    assert forbidden not in SOURCE, forbidden

sudo_calls = []
for node in ast.walk(TREE):
    if not isinstance(node, ast.List) or not node.elts:
        continue
    first = node.elts[0]
    if isinstance(first, ast.Constant) and first.value == "sudo":
        sudo_calls.append(
            [item.value for item in node.elts if isinstance(item, ast.Constant)]
        )
assert sudo_calls
for call in sudo_calls:
    assert any(action in call for action in ("-n", "install", "ln", "rm")), call

print("  [OK] zh_CN catalog restore is exact-version, signed and no-overwrite")

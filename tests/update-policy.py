#!/usr/bin/env python3
"""Static contract for the DNF5 Pocket DS hardware protection boundary."""

import ast
import subprocess
import sys
from configparser import ConfigParser
from fnmatch import fnmatchcase
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "components/system/90-pocketds-hardware-protection.conf"
CONFIG_TEXT = CONFIG_PATH.read_text(encoding="utf-8")
INSTALLER = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
FOCUSED_INSTALLER = (ROOT / "scripts/install-update-protection.sh").read_text(
    encoding="utf-8"
)
POLICY = (ROOT / "docs/UPDATE-POLICY.md").read_text(encoding="utf-8")
PLANNER = (ROOT / "scripts/pocketds-userspace-update-plan.py").read_text(
    encoding="utf-8"
)
PLANNER_TEST = (ROOT / "tests/userspace-update-plan.py").read_text(encoding="utf-8")

parser = ConfigParser(interpolation=None, strict=True)
parser.read_string(CONFIG_TEXT)
assert parser.sections() == ["main"]
assert set(parser["main"]) == {"excludepkgs"}
configured_excludes = tuple(parser["main"]["excludepkgs"].split(","))
generated = subprocess.check_output([sys.executable, str(ROOT / "scripts/render-update-protection.py")], text=True)
assert CONFIG_TEXT == generated

# Every hardware, graphics or boot package that the stored-transaction auditor rejects
# is also blocked in ordinary DNF frontends. This keeps Discover from bypassing
# the planner while leaving unrelated applications updateable.
for package in (
    "systemd",
    "systemd-libs",
    "dracut",
    "fwupd",
    "grubby",
    "shim-aa64",
    "qcom-firmware",
    "qcom-firmware-a740",
    "mesa-dri-drivers",
    "libdrm",
    "libglvnd-glx",
    "libva",
    "kwin",
    "plasma-workspace",
    "qt6-qtbase",
    "wayland-libs-client",
    "xorg-x11-server-Xwayland",
    "xorg-x11-drv-freedreno",
    "vulkan-loader",
):
    assert any(fnmatchcase(package, pattern) for pattern in configured_excludes), package
for package in ("chromium", "bash", "kdeconnectd", "retroarch"):
    assert not any(fnmatchcase(package, pattern) for pattern in configured_excludes), package

planner_tree = ast.parse(PLANNER, filename="pocketds-userspace-update-plan.py")
planner_default_patterns = []
for node in planner_tree.body:
    if isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id in {"HARDWARE_PATTERNS", "GRAPHICS_PATTERNS", "BOOT_PATTERNS"}
        for target in node.targets
    ):
        planner_default_patterns.extend(ast.literal_eval(node.value))
assert tuple(planner_default_patterns) == configured_excludes

for forbidden in (
    "disable_excludes",
    "gpgcheck=0",
    "skip_if_unavailable",
    "allowerasing",
):
    assert forbidden not in CONFIG_TEXT, forbidden

for token in (
    '"$repo_root/components/system/90-pocketds-hardware-protection.conf"',
    "/etc/dnf/libdnf5.conf.d/90-pocketds-hardware-protection.conf",
):
    assert token in INSTALLER, token

for token in (
    "sudo -n true",
    "tests/update-policy.py",
    "ConfigParser(interpolation=None, strict=True)",
    "dnf5 --dump-main-config",
    'expected_effective="excludepkgs = $canonical_excludes"',
    'grep -Fxq -- "$expected_effective"',
    "/var/lib/pocketds-linux-kit/backups/$stamp-update-protection",
    "install_and_validate",
    "rollback",
    "restoring the pre-install state",
):
    assert token in FOCUSED_INSTALLER, token

for forbidden in (
    "dnf5 upgrade",
    "dnf5 install",
    "dnf5 remove",
    "dnf5 download",
    "--refresh",
    "--no-gpgchecks",
    "--disable-excludes",
):
    assert forbidden not in FOCUSED_INSTALLER, forbidden

for token in (
    "硬件启动环",
    "图形会话环",
    "普通用户态环",
    "candidate_install_authorized=false",
    "pocketds-userspace-update-plan.py",
    "AUDITED_NOT_RUN",
    "REJECTED_NOT_RUN",
    '"execution_authorized":false',
    '"execution_state":"NOT RUN"',
    "--assumeno",
    "不授权 stored transaction replay",
    "禁止仅凭 evaluator 的成功报告运行",
    "旧 DNF/DNF4",
    "DNF5 `history store`",
    "REJECTED_NOT_RUN",
    "owner-only",
    "持续持有 transaction 与 `packages/` 的目录 FD",
):
    assert token in POLICY, token

for token in (
    'REPORT_SCHEMA = "pocketds.userspace-update-plan.v1"',
    'DNF_TRANSACTION_VERSION = "1.0"',
    'DNF_FORMAT_AUDIT_COMMIT = "3cc84ee1540f1e40a29b19414a8f3e8521e5f3ff"',
    '"kernel*"',
    '"pocketds-*"',
    '"linux-firmware*"',
    '"qcom-firmware*"',
    '"mesa*"',
    '"libdrm*"',
    '"kwin*"',
    '"qt6-qtwayland*"',
    '"xorg-x11-server*"',
    '"vulkan*"',
    '"grub*"',
    '"dracut*"',
    '"shim*"',
    '"systemd*"',
    '"execution_authorized": False',
    '"execution_state": "NOT RUN"',
    '"dnf5_invoked": False',
    '"rpm_transaction_invoked": False',
    'SAFE_REPO = re.compile(r"^@stored_transaction',
):
    assert token in PLANNER, token

for forbidden in (
    "import subprocess",
    "os.system",
    "os.exec",
    "os.spawn",
    "ctypes",
    "urllib",
    "requests",
    "socket",
    "dnf5 replay",
    "dnf5 upgrade",
):
    assert forbidden not in PLANNER, forbidden

for token in (
    "test_every_protected_ring_is_rejected",
    "test_remove_downgrade_reinstall_and_reason_change_are_rejected",
    "test_missing_symlink_and_traversal_payloads_fail_closed",
    "test_dnf4_history_store_and_human_output_are_outside_the_parser_boundary",
    "test_pinned_dnf5_omits_empty_optional_collections",
    "test_world_readable_transaction_or_packages_directory_is_rejected",
    "test_packages_directory_mtime_change_during_audit_is_rejected",
    "test_transaction_directory_replacement_during_audit_is_rejected",
    "test_planner_never_invokes_dnf5_or_rpm_even_when_they_are_on_path",
):
    assert token in PLANNER_TEST, token

print("  [OK] DNF5 protection and userspace-only NOT RUN planner are fail closed")

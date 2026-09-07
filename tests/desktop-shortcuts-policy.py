#!/usr/bin/env python3
"""Exercise the installed-app-only Pocket DS desktop shortcut transaction."""

import os
import pathlib
import stat
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-desktop-shortcuts.sh"

with tempfile.TemporaryDirectory(prefix="pocketds-shortcuts-") as name:
    fixture = pathlib.Path(name)
    home = fixture / "home"
    desktop = home / "Desktop"
    applications = home / "applications"
    exports = home / "flatpak-exports"
    state = home / "state"
    for directory in (desktop, applications, exports, state):
        directory.mkdir(parents=True)

    app_sources = (
        "Steam.desktop",
        "cn.xfangfang.wiliwili.desktop",
        "ES-DE.desktop",
        "Stardew Valley.desktop",
        "土豆兄弟(Brotato).desktop",
        "chromium-browser.desktop",
        "pocketds-switch-to-android.desktop",
    )
    flatpak_sources = (
        "com.moonlight_stream.Moonlight.desktop",
        "net.kuribo64.melonDS.desktop",
    )
    for source in app_sources:
        (applications / source).write_text(f"[Desktop Entry]\nName={source}\n", encoding="utf-8")
    for source in flatpak_sources:
        (exports / source).write_text(f"[Desktop Entry]\nName={source}\n", encoding="utf-8")
    tailscale = home / "tailscale"
    tailscale.write_text("#!/bin/sh\n", encoding="utf-8")
    tailscale.chmod(0o755)
    chromium_runtime = home / "chromium-runtime"
    chromium_runtime.write_text("synthetic chromium runtime\n", encoding="utf-8")
    chromium_runtime.chmod(0o755)
    steam_session = home / "pocketds-steam-session"
    steam_session.write_text("#!/bin/sh\n", encoding="utf-8")
    steam_session.chmod(0o755)
    android_switch = home / "pocketds-switch-to-android"
    android_switch.write_text("#!/bin/sh\n", encoding="utf-8")
    android_switch.chmod(0o755)

    environment = os.environ | {
        "HOME": str(home),
        "POCKETDS_DESKTOP_DIR": str(desktop),
        "POCKETDS_APPLICATION_DIR": str(applications),
        "POCKETDS_FLATPAK_EXPORT_DIR": str(exports),
        "POCKETDS_SHORTCUT_STATE_ROOT": str(state),
        "POCKETDS_TAILSCALE_BIN": str(tailscale),
        "POCKETDS_CHROMIUM_RUNTIME_BIN": str(chromium_runtime),
        "POCKETDS_STEAM_SESSION_BIN": str(steam_session),
        "POCKETDS_ANDROID_SWITCH_BIN": str(android_switch),
    }
    result = subprocess.run(
        [str(SCRIPT)],
        env=environment,
        check=True,
        text=True,
        capture_output=True,
    )
    assert "Installed 10 desktop shortcuts." in result.stdout
    expected = {
        "Steam.desktop",
        "Moonlight.desktop",
        "Wiliwili.desktop",
        "ES-DE.desktop",
        "Stardew Valley.desktop",
        "土豆兄弟.desktop",
        "Chromium.desktop",
        "melonDS.desktop",
        "Tailscale.desktop",
        "切换到 Android.desktop",
    }
    assert {path.name for path in desktop.iterdir()} == expected
    for path in desktop.iterdir():
        assert path.is_file() and not path.is_symlink()
        assert stat.S_IMODE(path.stat().st_mode) == 0o755
    for name in ("Stardew Valley.desktop", "土豆兄弟.desktop"):
        content = (desktop / name).read_text(encoding="utf-8")
        assert "pocketds-steam-session" in content
        assert "Exec=steam " not in content
    transactions = list(state.iterdir())
    assert len(transactions) == 1
    assert len((transactions[0] / "installed.tsv").read_text().splitlines()) == 10
    # An application entry alone must not publish a non-working switch.
    (desktop / "切换到 Android.desktop").unlink()
    android_switch.chmod(0o644)
    result = subprocess.run([str(SCRIPT)], env=environment, check=True,
                            text=True, capture_output=True)
    assert "Installed 9 desktop shortcuts." in result.stdout
    assert not (desktop / "切换到 Android.desktop").exists()

source = SCRIPT.read_text(encoding="utf-8")
assert "sudo" not in source
assert "rm " not in source
assert "[[ -f $source ]] || return 0" in source
assert "readlink -f" in source
assert "cp -a --no-dereference" in source
assert "[[ -x $chromium_runtime_bin && ! -L $chromium_runtime_bin ]]" in source
assert "[[ -x $steam_session_bin && ! -L $steam_session_bin ]]" in source
assert "[[ -x $android_switch_bin && ! -L $android_switch_bin ]]" in source

installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
assert "kbuildsycoca6 --noincremental" in installer
chromium_launcher = (
    ROOT / "components/chromium/chromium-browser.desktop"
).read_text(encoding="utf-8")
assert (
    "Icon=/opt/pocketds-chromium-v4l2-151.0.7922.137/"
    "usr/share/icons/hicolor/256x256/apps/chromium.png"
) in chromium_launcher

print("  [OK] desktop shortcuts cover only installed apps and keep exact preimages")

# Offline Kit integration

`scripts/pocketds-sd-image-stage.py` stages the existing Panel, keyboard,
touchpad, controller and system integration into a disposable Fedora 44 image
root. It never invokes the live installer, a host service manager, D-Bus,
devices, mount, partitioning, or an external API. Kernel/firmware/boot material
and the hardened userspace and haptics binaries are separate image-build inputs.

The builder must first create the unprivileged `pocketds` account and private
group, with `/home/pocketds` and a bare password lock (`!`, `*` or `!*`). A
locked hash is rejected because it would retain the upstream default-password
material. Keep normal nonzero password
aging; forced password expiry would block the graphical first-use flow.
The root must contain `etc/pocketds-image-build-root` with exactly
`pocketds.sd-image-root.v1` followed by a newline. Remove this build marker from
the final image. It is independent of the first-use and resize pending markers.

Run inside the isolated builder with a canonical absolute root path:

```sh
python3 scripts/pocketds-sd-image-stage.py \
  --root /private/build/rootfs \
  --source-root /private/build/history-free-source \
  --panelctl /private/build/native/pocketds-panelctl \
  --gamescope-observer /private/build/native/pocketds-gamescope-observer \
  --plan
```

Remove `--plan` to write the reviewed inventory. Actual staging needs root only
to assign file ownership in that disposable tree. The source must be a clean
asset-free export with no `.git`. Both native binaries must be AArch64 ELF64.
The stage validates all sources, destinations and required command/service
paths before writing. Discard an interrupted composition instead of running
this as an upgrade on an existing installation.

`kit-files.json` is the explicit source-to-destination inventory. It includes
the complete seven-file keyboard generation and the game/input integration,
including its native observer, root controller gate and launcher helpers.
It also includes the existing ALSA UCM, udev, libinput lid quirk, independent
brightness, speaker enhancement, TuneD/fan profiles, guarded power helpers and
session recovery drop-ins. No first-use helper is executed during staging.

The resulting `/usr/share/pocketds-linux-kit/image-install-manifest.json`
records each staged file's source, destination, mode, owner, byte count and
SHA-256, plus service links and the removed upstream resize marker. First-use
files and resize code are described by their separate helpers and tests.
The manifest explicitly keeps hardware acceptance false.

On first Plasma login, `pocketds-image-desktop.service` waits for the keyboard,
sets the two built-in display defaults and adds the existing Panel to DSI-2.
It identifies the connector through Plasma's scripting API and uses its actual
logical geometry; it does not copy a personal desktop database, output UUIDs or
wallpaper. The defaults match the incumbent Kit: DSI-1 scale 1.5 at `(0,0)`,
DSI-2 scale 1.25 at `(283,720)`. A successful private marker keeps subsequent
logins from overwriting the user's display and desktop changes. The service
remains active only after initialization and a running keyboard; password
onboarding uses that readiness condition.

The implementation uses the [Plasma desktop scripting API](https://develop.kde.org/docs/plasma/scripting/api/).
The Node fixture checks connector selection, geometry and idempotence; it is
not a Plasma render or physical dual-screen test.

The base image must provide the packages in `docs/DEPENDENCIES.md`, KDE/Plasma
Wayland, GTK 3, XWayland, Fcitx 5, `kscreen-doctor`, `kdialog`, the PipeWire
`filter-chain.service`, `chpasswd`, and the upstream fan/profile and input
services. The stage checks required files; the builder must separately verify
Python imports, shared libraries, RPM dependencies and firmware compatibility.

The public image leaves API configuration absent and retains the ordinary
light-standby policy with deep suspend disabled. The unrelated upstream Plasma
setup wizard is masked so the Kit's first-use flow can reach the desktop.
The generated Rime override selects packaged `luna_pinyin_simp` by default and
also offers `luna_pinyin`; it does not depend on a separate Rime Ice download.
Steam, ES-DE, emulator content, browser runtimes, Codex login
and speech models remain separate optional dependencies. Their launcher/helper
files do not prove that those external applications or accounts are available.

# Local SD image build

This is the local engineering SD-image path. It consumes a clean Fedora 44 KDE
root, the history-free asset-free Kit source, and hash-verified hardware
artifacts. It does not advance the signed public-release gates in `../image`.
The output uses the upstream 512-byte GPT, 512 MiB `ROCKNIX` FAT and at least
8 GiB `STORAGE` ext4 layout. Use a 16 GB or larger SD card for testing.

`upstream.json` records the upstream builder revision and hashes of the pristine
upstream recipe sources. Those hashes remain provenance for the original files;
the local recipe adds the downstream changes listed there.
`recipe/` composes a directory first so that staging and identity audits happen
before partition assembly. Copy the recipe into a new private build directory
on a disposable Fedora 44/aarch64 builder, supply its `packages/` with the
locked hardened `pocketds-userspace` and patched PowerDevil RPMs, then run mkosi.
The package versions are explicit, but Fedora/COPR repositories are not immutable
snapshots: record the resolved RPM manifest and archive checksums for each build.
The current locally built RPMs are unsigned engineering inputs.

The build order is:

1. Compose `recipe/` using mkosi 26. This generates `output/pocketds-kde-rootfs`.
2. Replace the root and `pocketds` password fields with bare `!`, preserving
   non-expired account aging. Never ship the upstream default password hash.
3. Build `pocketds-panelctl.cpp` and `build-gamescope-observer.sh` on AArch64.
4. Run `scripts/pocketds-sd-image-stage.py` with the marked root, clean source
   and both native artifacts. `--plan` validates without writing.
5. Test the locked-account Plasma autologin PAM path and the one-use password
   server in the disposable root; restore its locked account and pending marker.
   Before sealing, remove the composed root's `/root/.ssh` and
   `/var/lib/bluetooth/mesh` directories only after confirming they are empty,
   using `rmdir` semantics. The sealer rejects these paths even when empty;
   stop and inspect the clean-root inputs if either directory contains data.
6. Before sealing or assembly, explicitly validate PowerDevil inside an isolated
   copy of the actual composed runtime. Run
   `scripts/powerdevil-runtime-abi.py --installed --expected-elf-count 22` there,
   and run the separate cold QML probe with a clean environment, empty caches
   and disconnected buses as described in the
   [PowerDevil instructions](../../components/powerdevil/README.md).
   Require all 22 ELF checks and both module loads/type registrations to pass;
   stop on any failure. Record the resolved runtime packages and bind the tested
   files to the final image. The sealer does not run these explicit checks
   automatically. The unchanged Alpha3 recipe retains `pocketds1` with its
   verified Qt 6.11.2 runtime; the Qt 6.11.1 `pocketds2` candidate is not a
   replacement for that image. RPM dependency satisfaction alone is insufficient.
7. Run `scripts/pocketds-sd-image-seal.py` with `--root`, `--source-root`,
   `--source-archive`, `--export-manifest` (the same source export's
   `manifest.json`), `--boot-image`, `--haptics-binary`, `--modules` and
   `--module-manifest` (the frozen 332-file final kernel-module receipt).
   `--plan` performs all input and destination checks without writing.
   All 317 baseline module hashes must match
   `kernel-modules.sha256`; the newly rebuilt RFCOMM addition is separately
   hash-checked and still needs a hardware retest.
8. Run `scripts/pocketds-sd-image-assemble.py --root ROOT --definitions REPART
   --output NEW.raw`, where `REPART` is this directory's `repart/`. This creates
   a regular file only. Inspect both filesystems and verify the FAT's
   `/boot/Image` checksum before compressing the image with zstd. Also verify the
   FAT-root `/PocketDS-Switch-to-Linux.sh` against the staged Kit helper. This is
   one explicit downstream copy: mounting the FAT at `/boot` preserves the
   runtime `/boot/PocketDS-Switch-to-Linux.sh` path needed for returning from
   Android. The upstream `/boot/Image` route is unchanged, and no EFI loader or
   blanket `/boot` copy is added.

The seal step installs the exact kernel/driver pair and complete haptics
generation, embeds the Kit source archive and RPM source manifest, clears
machine identity and build state, disables SSH and the obsolete initial-setup
and resize paths, and removes the build marker. It never copies a daily root or
home directory. First boot uses the fixed generic `pocketds` desktop account,
requests a new local password through the lower-screen keyboard, and allows
users to configure their own transcription API. The image contains no API key.

Automatic expansion derives the disk from the currently mounted root's device
number, requires an SD card and the exact two-partition layout, and preserves a
retry marker on failure. It never searches all disks by a shared label.

The tested v4 boot image and exactly reproduced drivers are component evidence.
They do not prove that this newly composed image boots on hardware. A real SD
boot, first-use UI, restart, display/input/audio and suspend-resume test remain
required before changing `hardware_boot_tested` or publishing a stable release.
No ABL installation, device writing or reboot is performed by these build tools.

See `kit-integration.md` for the offline payload and optional runtime boundary.

## Alpha3 overlays and notices

The public InputPlumber build replaces both `/usr/bin/inputplumber` and the
active `/usr/local/libexec/pocketds-inputplumber-haptics`. The seal binds exact
preimages, both patch files, and the new artifact; RPM metadata remains unchanged
and the two overlays are explicitly recorded. Old payload copies are rejected,
download caches are cleared, and no RPM payload archives are retained.

`kit-files.json` also installs `third-party/` under
`/usr/share/doc/pocketds-linux-kit/third-party/`. This contains full notices,
firmware scope evidence and exact source RPM access information. Original
component terms and provenance limitations remain in those files. The export
excludes GitHub bootstrap workflow/marker files and the earlier publication
manifest. A new export and full image audit are required for each Alpha3 build.

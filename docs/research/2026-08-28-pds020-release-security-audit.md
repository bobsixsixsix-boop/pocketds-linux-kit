# PDS-020 release security audit — 2026-08-28

The current development image is **not releasable**. This is an evidence gate,
not a request to remove development access from the only recovery device.

## Confirmed blockers

- `/etc/sudoers.d/10-wheel-nopasswd` grants `%wheel ALL=(ALL) NOPASSWD: ALL`.
  It is owned by the vendor `pocketds-userspace` RPM, not by this repository.
  Do not delete it in place before all root workflows have constrained
  replacements and a rescue image exists.
- The repository has no root LICENSE/COPYING/NOTICE. Imported configuration,
  vendored Steam launcher changes and external runtime layers lack one complete
  provenance manifest.
- The bundled wallpapers have no recorded author/source/license/redistribution
  evidence; one carries a visible no-reupload notice. They fail closed for a
  public image until replaced or documented with explicit permission.
- Chromium V4L2 is an RPM-unowned `/opt` runtime without a reproducible build
  receipt. Steam user runtime/login content must never be copied into a release.
- The repository helper boundary is closed and root-owned today, but `%wheel`
  plus sudoers argument wildcards is not the final least-privilege model.

## Safe migration order

1. Build release images from a clean composition; never “clean” the daily-use
   device home into an image.
2. Add root LICENSE, NOTICE/third-party source manifest, runtime lock, SBOM and
   redistribution evidence; replace assets that cannot pass.
3. Package panel helpers, policy and any custom runtime as signed RPMs with
   verifiable ownership and hashes.
4. Remove every hidden direct-sudo dependency. RetroArch now uses the Panel
   helper; installation remains an image-build/development action.
5. Install a dedicated control group and exact helper rules, then remove the
   vendor global NOPASSWD rule from the *release composition*.
6. Offline-audit the mounted image: `sudo -n true` and `sudo -n /bin/sh` must
   fail; allowed helper operations and all injection/extra-argument cases must
   pass their matrix.
7. Sign image, checksums, SBOM, source offer, notices and recovery instructions
   together. Public publishing remains a separately approved action.

Diagnostic collection is now local-only, fixed-allowlist, identifier-redacted,
symlink-rejecting and content-hashed. It still needs a release audit against an
actual mounted image before PDS-020 can close.

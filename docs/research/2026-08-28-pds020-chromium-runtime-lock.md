# PDS-020 Chromium runtime lock audit

## Follow-up outcome

The original read-only audit below correctly failed closed when only extracted
files were available. The package-receipt gap is now closed for this Chromium
layer. Five exact Arch Linux ARM aarch64 archives were obtained from an official
valid-TLS mirror and verified with Sequoia `sqv` against build-system key
`68B3537F39A313B3E574D06777193F152BDBE6A6`. Their archive SHA-256 values,
detached signatures, package identities, `.PKGINFO`/`.BUILDINFO`/`.MTREE`
digests and locked payload entries are pinned in
`components/chromium/provenance/receipt.json`.

The final offline replay passed five signatures, 15 metadata artifacts, five
Chromium locked artifacts (including the official 256px launcher icon) and 54
compatibility entries. The verifier performs
no network, installation, system extraction or Chromium launch. Its
`release_ready=true` result is deliberately scoped to the isolated Chromium
layer. PDS-020 remains open for the complete firmware because sudo policy,
license/source/SBOM/asset evidence, private state and machine identity still
need a clean image composition and mounted-root audit.

## Initial scope and result (historical)

This read-only audit used repository revision `ee537f0` and the extracted tree
`/opt/pocketds-chromium-v4l2-151.0.7922.137`. It did not install a package,
download a package archive, launch Chromium, open a browser profile or change
the device.

The observed Chromium package payload is internally self-consistent but is
**not release-reproducible**. Its `.MTREE` records the same sizes and SHA-256
digests measured for `.PKGINFO`, `.BUILDINFO` and the main executable. However,
the original package archive and detached signature were not retained. A
self-consistent extracted mtree cannot prove that the mtree itself came from a
signed official package.

The separate `compat` tree has a larger provenance gap. Its filenames, SONAMEs
and versions align with both the four exact `installed =` versions in Chromium's
`.BUILDINFO` and the current Arch Linux ARM package pages, but no
per-package `.PKGINFO`, `.BUILDINFO`, `.MTREE`, archive digest or verified
signature remains. The lock therefore labels every compat file
`observed-unverified`; none is called reproducible.

## Official package and signing evidence

The official Arch Linux ARM package pages report these aarch64 versions and
provide Source Files and Download links:

| Layer | Version | Official package page | Official download link recorded in lock |
|---|---|---|---|
| Chromium | `151.0.7922.137-1` | [chromium](https://archlinuxarm.org/packages/aarch64/chromium) | `extra/chromium-151.0.7922.137-1-aarch64.pkg.tar.xz` |
| ICU | `78.3-1` | [icu](https://archlinuxarm.org/packages/aarch64/icu) | `core/icu-78.3-1-aarch64.pkg.tar.xz` |
| libjpeg-turbo | `3.2.0-2` | [libjpeg-turbo](https://archlinuxarm.org/packages/aarch64/libjpeg-turbo) | `extra/libjpeg-turbo-3.2.0-2-aarch64.pkg.tar.xz` |
| minizip | `1:1.3.2-3` | [minizip](https://archlinuxarm.org/packages/aarch64/minizip) | `core/minizip-1%3A1.3.2-3-aarch64.pkg.tar.xz` |
| libxml2 | `2.15.3-1` | [libxml2](https://archlinuxarm.org/packages/aarch64/libxml2) | `core/libxml2-2.15.3-1-aarch64.pkg.tar.xz` |

Arch Linux ARM's [package-signing policy](https://archlinuxarm.org/about/package-signing)
states that all repository packages are signed by the build system key. The
published full fingerprint is
`68B3537F39A313B3E574D06777193F152BDBE6A6`. This establishes the policy and
expected signer, not verification of this extracted runtime: its archive and
`.sig` are absent.

The audit host also received a TLS hostname mismatch when making read-only HEAD
requests to the package-page-provided `mirror.archlinuxarm.org` URLs. No TLS
bypass was used. Closing the receipt must use an official listed mirror with a
valid TLS chain and the Arch Linux ARM keyring; `curl -k` or an unsigned copy is
not acceptable evidence.

## Observed identities

| Artifact | Size | SHA-256 |
|---|---:|---|
| `.PKGINFO` | 1,967 | `8b6956af9945d8dd688251a490641747070ed007e48d918566798136ac99a137` |
| `.BUILDINFO` | 12,577 | `34a07c0353bdc4e7cb95127def2cafa7e148c8adc24816dca8564c04cfbb0d38` |
| `.MTREE` | 5,592 | `03a8c2507dce08dc70c400b68ae58153cbc7c351b216bf51a3b120d68915a0f8` |
| `usr/lib/chromium/chromium` | 322,767,832 | `2f0002bce6c72188c9426f083e6e802c690eacbbc4fee03b2c08343833fcd1b0` |
| `usr/share/icons/hicolor/256x256/apps/chromium.png` | 9,614 | `e14120fdefb8eb455f44eac572f34bda75c32c9404e5c3745d44793dae217331` |
| repository/live wrapper | 580 | `273ea337eedbf3af2da56c4283811197a9322ccf59df16615bdd6ea77324b1c0` |

The package metadata pins `pkgbuild_sha256sum` to
`9aba5eab0263c8deaf5f7bb2d660c6cbac4d4e499d21aa06d0cb4c307be0b4d2`,
but no pinned source snapshot was retained to independently reproduce or check
that recipe.

The wrapper in Git and `/usr/local/bin` are byte-identical. With its private
`LD_LIBRARY_PATH`, the main executable resolves five logical compatibility
libraries from `compat`: libjpeg 8, minizip 1, libxml2 16, ICU uc 78 and ICU data
78. These correspond to ten locked entries when each SONAME symlink and target
file is counted. The live compat tree contains 54 files/symlinks total; the
other 44 are development metadata, Python bindings or libraries not selected by
the observed main-binary dependency resolution. They remain locked and
attributed rather than silently ignored or deleted.

## Verifier contract

`pocketds-chromium-runtime-verify.py` is offline and read-only. It verifies:

- fixed package name/version/architecture, packager, build date and PKGBUILD
  digest in `.PKGINFO`/`.BUILDINFO`;
- hashes and sizes for package metadata, mtree and the main executable;
- mtree agreement for both metadata files and the executable;
- the repository wrapper hash and its exact runtime/compat routing;
- the live `/usr/local/bin/pocketds-chromium-v4l2` wrapper against that same hash;
- an exact 54-entry compat set, including content hashes, symlink targets,
  source-package attribution and whether each entry is needed by current ELF
  resolution.

Exit code 0 is reserved for a fully signed/archive-hashed lock. Integrity drift
returns 1, an invalid or contradictory lock returns 2, and a matching observed
tree with incomplete provenance returns 3. The current lock has a content-hashed
receipt and certificate, so a matching live tree returns 0 with provenance
`complete`. Removing or changing that evidence still fails closed.

`pocketds-chromium-provenance-verify.py` is the stronger archive replay gate. It
requires a caller-supplied archive directory and trusted executable `sqv`, checks
each archive size/hash and package identity, rejects unsafe/duplicate tar member
paths, verifies all pinned payload entries, reconstructs each content-hashed
detached signature in a private temporary file, and requires the exact signer
fingerprint plus a successful one-of-one signature result.

## Remaining release work

The five original package archives are intentionally not committed to Git. A
release build must fetch the exact locked versions through its controlled input
pipeline, rerun `make verify-chromium-provenance`, rebuild the minimal runtime
from those verified archives, and bind this receipt to the image digest. It must
also satisfy package-license/source-offer obligations and the other mounted-root
gates. Until the complete clean composition passes, PDS-020 remains OPEN.

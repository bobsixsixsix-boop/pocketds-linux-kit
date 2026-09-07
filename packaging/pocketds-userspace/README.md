# Hardened `pocketds-userspace` rebuild input

The installed Fedora 44 package is required by `pocketds-base` and carries most
Pocket DS hardware integration. It must not be removed from the daily device.
Its exact source RPM also ships `/etc/sudoers.d/10-wheel-nopasswd` containing a
global `%wheel ALL=(ALL) NOPASSWD: ALL` rule, which blocks a public image.

`source-lock.json` pins the exact COPR source RPM, its Fedora COPR signing-key
fingerprint, the complete flat 67-file/148,381-byte extracted-source manifest,
source spec, offending source file, upstream fan controller and deterministic
spec transform. An added, removed or content-changed auxiliary source now fails
before outputs are written. The transform also replaces `Source70` with the
repository's exact downstream smooth controller. That file is byte-identical to
the audited daily-device implementation; it is a project override, not an
upstream or vendor default.
The verifier requires both RPM signature/digest success, the independent
archive SHA-256 match and the complete extracted-tree manifest. The transform
creates a distinct
`20260730174706.pds1` release, deletes only the source/install/file-list and
description lines for the global rule, and adds a changelog record. Any upstream
line drift, ambiguous match, changed archive, changed source or remaining
forbidden token fails closed.

The repository does not download, install or build the package automatically.
Given the exact source RPM, extract it into a new private build directory with
the platform RPM tools, then verify or emit the paired hardened build inputs:

```sh
python3 scripts/pds020-userspace-srpm-prepare.py \
  --source-rpm /path/to/pocketds-userspace-20260507-20260730174706.fc44.src.rpm \
  --extracted-dir /path/to/new/extracted-srpm

python3 scripts/pds020-userspace-srpm-prepare.py \
  --source-rpm /path/to/pocketds-userspace-20260507-20260730174706.fc44.src.rpm \
  --extracted-dir /path/to/new/extracted-srpm \
  --output-spec /path/to/new/build/SPECS/pocketds-userspace.pds1.spec \
  --output-fan-controller /path/to/new/build/SOURCES/pocketds-fancontrol.pds1
```

The two outputs are inseparable: requesting only one fails closed, existing
targets are never overwritten, and both are created mode `0600`. Copying only
the spec would silently rebuild the old coarse-step controller and is forbidden.

The later release build must use an unprivileged clean buildroot, sign the
resulting RPM, verify its payload does not contain
`etc/sudoers.d/10-wheel-nopasswd`, install the repository's constrained Panel
policy, and run the mounted-root audit. Rebuilding a modified package under the
original NEVR, editing the installed daily RPM in place, or deleting the whole
hardware-integration package is not acceptable.

Before signing a newly built binary, audit it without installing or extracting
anything to disk:

```sh
BINARY_RPM=/path/to/pocketds-userspace-20260507-20260730174706.pds1.fc44.noarch.rpm \
  make audit-userspace-rpm
```

The audit uses official `rpm -qp` query formats plus
`rpm2archive --nocompression --format=cpio`. It requires the exact hardened
NEVRA/source RPM, SHA-256 file digests, root-owned regular/directory/symlink
entries, matching header/newc paths, modes, sizes and contents, bounded valid
newc padding/checksums, the exact locked `/usr/bin/pocketds-fancontrol`, and no
broad sudo rule in either payload or scriptlets.
It is read-only and emits only artifact/manifest hashes. Its result deliberately
keeps `signature_verified=false` and `release_ready=false`; after signing, a
separate signed-artifact receipt and the mounted-root audit remain mandatory.

`build-lock.json` records the canonical pre-sign result from two independent,
clean Fedora 44/aarch64 `fedora-44-aarch64` mock result directories. Both runs
used offline rebuild mode, `_buildhost=pocketds-build.invalid` and
`use_source_date_epoch_as_buildtime=1`. The mock-emitted source RPM, binary RPM
and `installed_pkgs.log` are byte-identical across the two results. Verify those
already-built results without building, installing or accessing the network:

```sh
RESULT_A=/path/to/private/mock-a \
RESULT_B=/path/to/private/mock-b \
  make verify-userspace-rpm-reproduction
```

The locked binary SHA-256 is
`cd60625fca2f668d828d6cee119afc6a370925ce77302362f373d4373e4944d4`;
its 71-entry payload manifest SHA-256 is
`fdd27a685c5488d6c04eeb8867e4c6e0741a5a996b792aee0b09b67e9725006d`.
This gate deliberately treats only mock's fixed-path result SRPM as canonical.
An intermediate transformed input SRPM produced under arbitrary host topdirs is
not claimed byte-reproducible because its expanded spec records those paths.
The build parameters are content-bound evidence, not a signed build
attestation. The outputs remain unsigned, uninstalled and non-release-ready.

After the maintainer chooses a project release key and signs a copy of the
canonical binary, create an external post-sign receipt that binds the exact
signed artifact, `build-lock.json`, pre-sign artifact/payload hashes, public
certificate, full 40- or 64-hex fingerprint and expected Header OpenPGP
version/algorithm. Verify it with:

```sh
RECEIPT=/path/to/private/post-sign-receipt.json \
SIGNED_RPM=/path/to/pocketds-userspace-20260507-20260730174706.pds1.fc44.noarch.rpm \
RELEASE_PUBLIC_KEY=/path/to/project-release-key.asc \
  make verify-userspace-rpm-postsign
```

The verifier creates an ephemeral private RPM database, imports only the
receipt-bound public certificate, requires exactly one matching Header OpenPGP
signature, rejects legacy/extra signatures, confirms both RPM SHA-256 digests,
then repeats the canonical header and full payload audit. It cannot sign,
generate keys, build, install or download. A pass means only that the artifact
is eligible for repository staging; `release_ready` remains false until the
repository, upgrade and mounted-root gates pass. The exact receipt contract and
allowed signature algorithms are documented in
`docs/research/2026-08-28-pds020-userspace-signing-policy.md`.

`transaction-lock.json` separately binds a twice-reproduced vendor baseline
binary and the canonical hardened build lock. The default transaction command
only prints its plan. On a disposable Fedora 44/aarch64 mock builder, an exact
confirmation runs vendor install → hardened upgrade → vendor rollback →
hardened re-upgrade in a generated, offline `uniqueext` root and always cleans
that root:

```sh
make plan-userspace-rpm-transaction

CONFIRM='RUN ISOLATED PDS020 RPM TRANSACTION' \
VENDOR_RPM=/path/to/pocketds-userspace-20260507-20260730174706.fc44.noarch.rpm \
HARDENED_RPM=/path/to/pocketds-userspace-20260507-20260730174706.pds1.fc44.noarch.rpm \
OUTPUT=/path/to/private/new-result.json \
  make run-userspace-rpm-transaction
```

The cycle uses `--nodeps --noscripts --notriggers`; it proves only RPM payload
ownership/removal and package identity semantics. It checks twice that the
vendor sudoers and coarse fan controller return, and twice that the hardened
upgrade removes the sudoers, owns the 71 locked paths and installs the exact
smooth controller. It does not test dependency resolution, scriptlets,
triggers, SELinux, services or a real mounted system.

## `.pds2` clean dependency candidate

The `.pds1` locks above remain the historical sudo/fan hardening evidence.
`source-lock.pds2.json` and `build-lock.pds2.json` advance the candidate after a
normal clean-root install proved Fedora 44 cannot resolve the retired vendor
Onboard dependencies. The `.pds2` transform removes the complete inactive
Onboard integration while preserving the hardware package and smooth fan
controller. Two clean offline mock rebuilds are byte-identical.

Plan or run the separate full transaction gate:

```sh
make plan-userspace-rpm-full-transaction

CONFIRM='RUN ISOLATED PDS020 PDS2 FULL TRANSACTION' \
ARCHIVE_DIR=/path/to/private/mode-0700/dependency-archives \
OUTPUT=/path/to/private/new-result.json \
  make run-userspace-rpm-full-transaction
```

`ARCHIVE_DIR=... make verify-userspace-rpm-archive` first verifies the exact
135 Fedora-signed dependency RPMs, Fedora 44 key and independently audited
unsigned project RPM without installing or networking. The full gate then does
offline mock initialization, installs all 136 exact local RPMs with normal DNF
dependency/scriptlet/trigger handling, removes the project RPM offline and
reinstalls it offline. All four exact package manifests, the installed NEVRA,
RPM verification result, legacy-path absence, fan hash and systemd wants are
locked; the generated mock root is always cleaned. The gate deliberately
remains pre-sign and `release_ready=false`.

After a maintainer signs the canonical `.pds2` RPM, verify its receipt against
the `.pds2` source/build locks rather than the historical `.pds1` candidate:

```sh
RECEIPT=/path/to/private/pds2-post-sign-receipt.json \
SIGNED_RPM=/path/to/pocketds-userspace-20260507-20260730174706.pds2.fc44.noarch.rpm \
RELEASE_PUBLIC_KEY=/path/to/project-release-key.asc \
  make verify-userspace-rpm-postsign-pds2
```

Signing changes the RPM file checksum. Only after this gate passes may the
signed RPM replace the pre-sign archive member and may final repodata be rebuilt.

The larger rootfs package archive adds the 186 non-key mock-baseline RPMs, for
322 exact local RPMs total. Verify or exercise it without repositories:

```sh
ARCHIVE_DIR=/path/to/private/mode-0700/rootfs-package-archives \
  make verify-userspace-rpm-rootfs-archive

make plan-userspace-rpm-rootfs-transaction

CONFIRM='RUN ISOLATED PDS020 PDS2 ROOTFS TRANSACTION' \
ARCHIVE_DIR=/path/to/private/mode-0700/rootfs-package-archives \
WORK_DIR=/path/to/private/empty-work-directory \
OUTPUT=/path/to/private/new-result.json \
  make run-userspace-rpm-rootfs-transaction
```

The confirmation gate installs only those 322 local files into a new empty
installroot with repositories/plugins disabled and cache-only DNF5, executes
normal dependencies/scriptlets/triggers, runs `dnf check`, audits the installed
payload and always removes the generated child root. It does not start services
or prove SELinux labels, signed project metadata or a bootable image.

Two independently generated fixed-revision repodata directories can be checked
against every package archive record and the locked Fedora generator:

```sh
ARCHIVE_DIR=/path/to/private/mode-0700/rootfs-package-archives \
RESULT_A=/path/to/private/first/repodata \
RESULT_B=/path/to/private/second/repodata \
  make verify-userspace-rpm-repository
```

The current pair is byte-identical and semantically binds all 322 packages,
but remains unsigned. This command cannot create metadata, keys or signatures;
a maintainer-selected project key, detached `repomd.xml` signature and signed
project RPM are still mandatory.

The final signed-repository gate is intentionally separate because RPM signing
changes the project file checksum. After the `.pds2` post-sign gate passes,
replace only that archive member, regenerate repodata twice, put the same
`repomd.xml.asc` inside both repodata directories and verify the whole chain:

```sh
RECEIPT=/private/final-repository-receipt.json \
RPM_POSTSIGN_RECEIPT=/private/pds2-post-sign-receipt.json \
SIGNED_RPM=/private/pocketds-userspace-20260507-20260730174706.pds2.fc44.noarch.rpm \
RELEASE_PUBLIC_KEY=/private/project-release-key.asc \
REPOMD_SIGNATURE=/private/repo-a/repodata/repomd.xml.asc \
UNSIGNED_ARCHIVE_DIR=/private/rootfs-package-archives \
SIGNED_ARCHIVE_DIR=/private/final-package-archives \
RESULT_A=/private/repo-a/repodata RESULT_B=/private/repo-b/repodata \
  make verify-userspace-rpm-repository-postsign
```

The verifier cannot sign or generate keys. A future pass admits the repository
to composition staging only. After that verifier passes, exercise the same
inputs through the confirmation-gated DNF5 smoke in a private mode-0700 work
directory:

```sh
make plan-userspace-rpm-repository-smoke

CONFIRM='RUN ISOLATED PDS020 PDS2 REPOSITORY SMOKE' \
WORK_DIR=/private/mode-0700/work \
OUTPUT=/private/new-repository-smoke.json \
RECEIPT=/private/final-repository-receipt.json \
RPM_POSTSIGN_RECEIPT=/private/pds2-post-sign-receipt.json \
SIGNED_RPM=/private/pocketds-userspace-20260507-20260730174706.pds2.fc44.noarch.rpm \
RELEASE_PUBLIC_KEY=/private/project-release-key.asc \
REPOMD_SIGNATURE=/private/repo-a/repodata/repomd.xml.asc \
UNSIGNED_ARCHIVE_DIR=/private/rootfs-package-archives \
SIGNED_ARCHIVE_DIR=/private/final-package-archives \
RESULT_A=/private/repo-a/repodata RESULT_B=/private/repo-b/repodata \
  make run-userspace-rpm-repository-smoke
```

This uses only the materialized `file://` repository, requires both package and
repository signature checks plus `skip_if_unavailable=0`, verifies the exact
322-row available-package manifest, installs all exact NEVRAs into an empty
root with normal scriptlets/triggers, runs `dnf check` and the installed
payload contract, and always cleans the disposable root. It does not touch the
device root or make the overall release ready. All image gates remain open. See
`docs/research/2026-08-28-pds020-userspace-repository-signing-chain.md`.

See `docs/research/2026-08-28-pds020-userspace-pds2-osk-retirement.md`.

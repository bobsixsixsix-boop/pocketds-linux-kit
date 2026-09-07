# PDS-020 userspace RPM reproducibility evidence

## Scope

This record covers the hardened pre-sign `pocketds-userspace` package only. It
does not authorize installation, claim a trusted builder, close the complete
firmware release gate or modify the daily Pocket DS. The source is the exact
COPR-signed SRPM and complete 67-file tree already bound by `source-lock.json`.

## Nondeterminism found and bounded

An initial pair of direct Fedora 44/aarch64 builds had identical payloads but
different RPM bytes because the RPM header used wall-clock `BUILDTIME`. Setting
`use_source_date_epoch_as_buildtime=1` and the fixed
`_buildhost=pocketds-build.invalid` made two direct binary builds byte-identical.

Intermediate transformed input SRPMs created under different random host
topdirs still differed. Their expanded specs contained those absolute topdir
paths even though their source payloads were otherwise equal. Such host-path
input SRPMs are not accepted as canonical artifacts. The canonical boundary is
the SRPM and binary RPM emitted by mock's fixed `/builddir/build` environment.

## Independent clean rebuild result

Two separate `fedora-44-aarch64` mock result directories were rebuilt offline
with mock `6.8-1.fc44`, the fixed buildhost and source-date buildtime. The clean
buildroot package list is bound as evidence rather than inferred from the host.

| Artifact | Size | SHA-256 | RPM payload SHA-256 |
|---|---:|---|---|
| mock result source RPM | 95,537 | `b01ba6ad22d275e1631af1c2ede751a15a530dc8974ee62d486d5f89a38e2a52` | `70efce37d47a4a33e8745a7f16e850ac809fcc5ee07c341fb12903007c7da8f2` |
| mock result binary RPM | 61,723 | `cd60625fca2f668d828d6cee119afc6a370925ce77302362f373d4373e4944d4` | `b7929976bc326cec4f99aa5af85a7c584a40c9c3266cd95b0405f81874f90f5e` |
| `installed_pkgs.log` | 11,414 | `1937b96a0770d17505b84726853863ca809efdc81ace45d155628cde64bb3ae8` | n/a |

All three canonical artifacts are byte-identical across the two result
directories. Both RPMs report build time `1787875200`, build host
`pocketds-build.invalid`, package name `pocketds-userspace`, version `20260507`,
release `20260730174706.pds1.fc44` and architecture `noarch`.

The binary payload audit found 71 entries with manifest SHA-256
`fdd27a685c5488d6c04eeb8867e4c6e0741a5a996b792aee0b09b67e9725006d`.
Its three scriptlets hash to
`e40e3b03d3df7041b59bf15933548499d4f27a13bf98ce23ec230e04d16615d8`.
The installed fan controller exactly matches
`9f04ff7113a3e2ea8412fca17a44cd728c20f6cbb7ab5cd463d8f66dd006f0fe`,
and no global `NOPASSWD: ALL` rule is present.

## Verifier and trust boundary

`scripts/pds020-userspace-rpm-repro.py` is a read-only, offline result verifier.
It accepts two distinct current-user-owned mode-0700 result directories, checks
safe regular-file metadata, the complete `build-lock.json`, RPM headers and
payload digests, then invokes the independent binary payload auditor for both
artifacts. It does not contain a build, install, network or output path.

Eight fixture/static cases pass, including artifact/header/payload drift,
duplicate/non-finite JSON, result-directory aliasing and unsafe permissions. The
real two-result invocation also passes with source, binary and package-manifest
identity all true.

The mock parameters in `build-lock.json` are content-bound declarations, not a
cryptographically signed invocation or builder attestation. The canonical RPMs
are unsigned and uninstalled. Required next gates are project signing and
receipt locking, post-sign payload audit, upgrade/rollback testing, clean
composition inclusion and mounted-root release audit.

## Payload-only upgrade and rollback result

The original vendor SRPM was also rebuilt twice with the same Fedora 44/aarch64
offline mock parameters. Its two binary RPMs are byte-identical at 61,048 bytes
and SHA-256
`27694198193c165369e560dcf1dd1779a81ecf9d191d605e57368c1c432a1f63`;
the source RPMs and `installed_pkgs.log` are also identical. The vendor binary
has 72 paths, including sudoers SHA-256
`6b4c40d177f490829fb5f2507d8bb8bcd8767b92f20230e0f95637d601be809d`
and coarse fan controller SHA-256
`07c8a931672cc90d5033f00a2bbc849ddc30e82b275a5373d5491f3f8efbdcc7`.

`transaction-lock.json` binds that baseline and the hardened build lock. The
confirmation-gated runner completed an offline disposable mock cycle:

1. vendor install: sudoers present, coarse fan controller, one vendor NEVRA;
2. hardened upgrade: sudoers absent, smooth controller, one `.pds1` NEVRA;
3. vendor rollback: the exact vendor state returns;
4. hardened re-upgrade: the exact hardened state returns again.

The mock root was cleaned and the private report SHA-256 was
`0c08ada2dd774d1e263c0c89889ef8cf884117e9981ce8f51bcb2408e0d820eb`.
The runner has nine fixture/static cases and always uses a generated bounded
`uniqueext` plus offline mode. This result intentionally disables dependency
resolution, scriptlets and triggers. It proves RPM payload ownership/removal
semantics only; it does not close full-root upgrade, SELinux or live-service
testing.

# PDS-020 `pocketds-userspace` SRPM hardening

## Outcome

The remaining broad sudo rule is not a loose local edit. RPM ownership and the
installed dependency graph identify
`pocketds-userspace-20260507-20260730174706.fc44.noarch`, required by
`pocketds-base`. The package also carries essential audio, touchscreen,
InputPlumber, fan, Bluetooth and boot integration, so removing it from the daily
device is not a safe fix.

The exact source RPM was obtained from its configured Fedora COPR repository.
It is 94,753 bytes with SHA-256
`b0d509040195c19cc8b8ead5e296ffe26dddf34886f5a7c4428bf5adf921d561`.
RPM 6.0.2 verified both Header and Legacy OpenPGP signatures with fingerprint
`cb2323a20ebafb6d02d625b76aaf40a74b7ff931`, plus Header and Payload SHA-256.
The source spec, offending source file and original coarse-step fan controller
are separately content-hashed in `packaging/pocketds-userspace/source-lock.json`.
The daily device's downstream smooth controller is now checked in beside the
lock. Its 11,955 bytes and SHA-256 `9f04ff7113a3e2ea8412fca17a44cd728c20f6cbb7ab5cd463d8f66dd006f0fe`
exactly match the live `/usr/bin/pocketds-fancontrol` audited by PDS-009.

## Bounded transform

`scripts/pds020-userspace-srpm-prepare.py` performs no download, installation or
build. It verifies the supplied SRPM hash and RPM signatures, then validates the
already extracted source spec and rule file. Its transform requires unique exact
matches and:

- changes the Release from the upstream value to distinct
  `20260730174706.pds1%{?dist}`;
- removes the `Source22`, `%install`, `%files` and description lines that carry
  `10-wheel-nopasswd`;
- changes `Source70` from the independently pinned 9,582-byte upstream
  coarse-step controller to `pocketds-fancontrol.pds1`;
- verifies the repository override and emits it only as a pair with the hardened
  spec, using new private files and refusing partial requests or overwrite;
- adds a release-hardening changelog entry;
- rejects any remaining `10-wheel-nopasswd`, `NOPASSWD: ALL` or
  `wheel nopasswd` token;
- pins the resulting spec size and SHA-256.

Thirteen fixture/static tests cover archive tamper before and during signature
verification, exact-line upstream drift, signature/digest output, output
pairing/exclusivity/mode, both fan inputs, NEVR/archive/spec-release binding and absence of
network/install/build paths. The real pinned SRPM, complete extracted tree and
paired override path passed this verifier before the two clean mock rebuilds.

An independent pre-sign binary boundary is now implemented in
`scripts/pds020-userspace-rpm-audit.py`. It uses RPM's official query-format
interface and `rpm2archive` standard newc output rather than parsing the private
RPM payload format. Eleven fixture/static paths cover exact hardened identity,
bounded streaming, path/owner/type safety, header-to-payload SHA-256 matching,
newc padding/checksum integrity, the exact fan-controller payload, scriptlets,
sudoers symlinks and broad passwordless rules. It writes and installs nothing, and remains non-release-ready
until a real artifact is independently signed and receipt-locked.

Primary RPM references:

- <https://rpm.org/docs/latest/man/rpm-queryformat.7>
- <https://rpm.org/docs/6.0.x/man/rpm2archive.1>
- <https://rpm.org/docs/6.1.x/man/rpmkeys.8>

## Reproducible pre-sign result

Two independent Fedora 44/aarch64 offline mock rebuilds now produce identical
mock-emitted source RPMs, binary RPMs and installed-package manifests. The
locked binary SHA-256 is
`cd60625fca2f668d828d6cee119afc6a370925ce77302362f373d4373e4944d4`;
the 71-entry payload manifest SHA-256 is
`fdd27a685c5488d6c04eeb8867e4c6e0741a5a996b792aee0b09b67e9725006d`.
The exact result-pair verifier passed both directories and keeps
`signature_verified=false` and `release_ready=false`. See
`2026-08-28-pds020-userspace-reproduction.md` for the build boundary and
measured receipts.

## Safety boundary and next gate

No RPM was installed and the current daily system was not edited. The hardened
result still needs a project signing identity, a signed-artifact receipt and a
post-sign payload audit. The isolated payload-only upgrade/rollback cycle now
passes, but dependencies, scriptlets, triggers, SELinux and services were
deliberately outside that test. Before the package can enter a composition, run
that full clean-root transaction, install only the repository's constrained
Panel helper policy, then rerun the mounted-root audit. Reusing the original
NEVR, treating build parameters as a signed builder attestation, or deleting
the full hardware-integration package is forbidden.

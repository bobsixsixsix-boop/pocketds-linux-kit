# PDS-020 `.pds2` userspace dependency and OSK retirement

## Failure reproduced

The `.pds1` payload-only transaction was intentionally unable to test normal
dependency resolution or scriptlets. A fresh Fedora 44/aarch64 mock install of
the vendor package stopped before installation because the official Fedora 44
repositories do not provide `onboard` or `onboard-data`.

This is not a live keyboard requirement. The daily Pocket DS has both packages
installed, but `pocketds-osk-listener.service` is masked/inactive with no
Onboard process. The active input path is the repository-owned
`pocketds-keyboard.service`; its Panel action and InputPlumber `ui_osk` event no
longer call the vendor listener.

## Bounded `.pds2` transform

`source-lock.pds2.json` keeps the same COPR-signed source RPM, complete
67-file source-tree identity, exact smooth fan-controller override and global
sudo removal. It advances only the downstream release to `.pds2` and removes
the retired OSK integration from the spec using unique exact-line contracts:

- the `onboard`, `onboard-data` and OSK-only DejaVu hard dependencies;
- Sources 100--109, including launch/toggle/listener, service, theme, schema,
  InputPlumber ignore rule, old KWin defaults and D-Bus activation override;
- their install and `%files` entries, plus the stale schema comment.

The transform rejects either case of the token `onboard` in the resulting spec.
It leaves the historical `pocketds-fancontrol.pds1` source filename unchanged
because that filename identifies the already audited 11,955-byte content; the
RPM release, source/binary result names and identity are all `.pds2`.

The image repository now owns the replacement migration: `install.sh` backs up
and installs the two-rule QuickMenu/custom-keyboard `kwinrulesrc` for the
desktop user and `/etc/skel`, and masks the retired vendor listener before
enabling the custom keyboard. These changes are source-only while the 24-hour
telemetry soak runs; they have not been deployed to the daily device.

## Reproducible build result

The real COPR signature, archive hash, source-tree manifest and `.pds2` spec
hash all passed in Fedora 44/aarch64. Two independent clean offline mock builds
then produced byte-identical artifacts and buildroot manifests:

| Artifact | Size | SHA-256 | RPM payload SHA-256 |
|---|---:|---|---|
| source RPM | 86,308 | `14eb4f3b2d320ec0c7ececb905ac03df1c11f8878573ec2d54b569cd4be6b2ea` | `847eb3362e81c7bd0e8262496e081b1a2099e590793ba4e1d4931014e7e30f51` |
| binary RPM | 56,579 | `457ee254ff2ae15b9a0cee2a839077b02e990da1015dbdcfbc4207b654c25cdf` | `e1038eb99738eabec9826af32bc610766550e74f50ebf20c3ca0b7b2557fb537` |
| `installed_pkgs.log` | 11,414 | `1937b96a0770d17505b84726853863ca809efdc81ace45d155628cde64bb3ae8` | n/a |

The binary contains 60 paths with manifest SHA-256
`e7d14b81239c701961ffc42ae3b2dc41f4de606c38daa870bc44db479975445b`.
Its three scriptlets hash to
`4b7e4d26e00b5f5f549ac2fcb03236dd55065bec6e776859d3620cc40d2df15a`;
the locked smooth fan controller remains exact and the broad sudo rule is
absent. `build-lock.pds2.json` records this pre-sign result separately from the
existing `.pds1` evidence.

## Immutable dependency archive

The exact dependency closure added above the 187-package mock baseline is now
materialized as a private mode-0700 archive: 135 Fedora RPMs, the `.pds2`
project RPM and the Fedora 44 primary public key. The 136 RPMs total 81,798,562
bytes. `dependency-archives-lock.pds2.json` binds each filename, NEVRA, origin,
size, file SHA-256 and RPM payload SHA-256; its canonical record hash is
`4d3f287f10af33e8f08f1c079f30f050699bc8c28e24b2d1a5ec281329ca9697`.

`pds020-userspace-rpm-archive-verify.py` is offline and read-only. It rejects
missing/extra files, symlinks, links, unsafe modes, identity or payload drift,
and imports only the locked Fedora key into an ephemeral RPM database. All 135
Fedora RPMs passed exactly one Header OpenPGP V4 RSA/SHA256 signature from
fingerprint `36f612dcf27f7d1a48a835e4dbfcf71c6d9f90a6`, plus header and payload
SHA-256 digests. The unsigned project RPM is not mislabeled as Fedora-signed;
it is instead bound to `build-lock.pds2.json` and independently passes the
complete pre-sign payload audit. The key file SHA-256 is
`93642aec521a1e5e96dd715f7ae0ec0850ebc9de09a94ce03cae5263f26cc18a`.

## Full transaction result

`pds020-userspace-rpm-full-transaction.py` is plan-only by default and requires
an exact confirmation, the verified archive and a new mode-0600 output. In a
generated disposable mock root it initializes from the already populated mock
cache offline, installs all 136 exact local RPMs with normal DNF dependency
resolution and scripts/triggers enabled, removes the project package offline,
then reinstalls that exact local project RPM offline. It never uses `--nodeps`,
`--noscripts` or `--notriggers`, never contacts a repository and always cleans
the root.

The formal run matched all locked package manifests:

1. baseline: 187 packages, `0acd1123…`;
2. dependency install: 323 packages, `314281f9…`;
3. offline removal: 322 packages, `ede29f9a…`;
4. offline reinstall: the same 323-package `314281f9…` manifest.

Both installed stages had the exact `.pds2` NEVRA, smooth fan hash, expected
systemd wants and no Onboard packages/files or global sudoers. RPM verification
had only the expected `/etc/fstab` config difference: mock already owns an
empty fstab, so the package correctly preserves it and writes `fstab.rpmnew`.
Removal deleted critical fan/audio/unit payload and wants while retaining the
135 explicitly installed dependency RPMs; offline reinstall restored the
project payload. The private result SHA-256 is
`b3e6f2b1d9876e67d68c915a23434b185747bd5635a593a96424b36d23f7c05d`.

The mock baseline still depends on a pre-populated mock cache and is locked by
its installed-package manifest rather than a complete base-OS RPM archive. This
dependency archive therefore closes mutable resolution for the 136-package
transaction set, but is not yet the immutable signed repository snapshot for a
whole image. SELinux labels are not tested because mock invokes DNF with
`tsflags=nocontexts`.

## Complete target package closure and empty-root transaction

The 186 non-key RPMs behind the mock baseline were also recovered exactly from
the existing local cache with zero missing or duplicate identities. Combining
them with the dependency archive produces 322 unique RPMs: 321 Fedora packages
plus the project `.pds2` RPM. The package bytes total 151,701,195; the canonical
322-record hash is
`fcfd45ca652707f4ad5fb16ccd49c258898e2bed0e1a3bf3203b0ce190104d89`,
and `rootfs-package-archives-lock.pds2.json` itself hashes to
`308324e557cb366e7adfe5afd74d74332ed53972489f32f07a84d5625e01d085`.
The same isolated verifier passed all 321 Fedora signatures and the project
payload boundary on the 323-entry directory including the public key.

`pds020-userspace-rpm-rootfs-transaction.py` then creates a random private
child of an explicit mode-0700 work directory. With Fedora 44 DNF5
`dnf5-5.4.1.0-1.fc44.aarch64`, host configuration is read-only, all
repositories and plugins are disabled, cache-only mode is set, and every input
is an exact local archive path. Normal dependency solving, scriptlets and
triggers install all 322 RPMs into the initially empty root. The installed
manifest is exactly
`7cdf60c9f647186250a05c0b643d7e6d1cd4b0dac861e15bf97a03035f7ce0d0`;
`dnf check`, package verification, legacy absence, fan/audio payload and systemd
wants all pass. The root is inode-bound before privileged cleanup and absent
afterward. The final private report SHA-256 is
`c547f9a94139fc83131fb0ecf5db1f9a93fd7e60668015898ca8a0de32000c15`.

This closes the pre-heated mock-cache dependency for the target package set,
but it is still an archive plus an external receipt rather than signed
repository metadata. The project RPM remains unsigned, services are not
started in the installroot and SELinux labels are not claimed. Those gates,
plus a real image-root boot/service test, remain open.

## Reproducible repository metadata

Two independent copies of the 322-package archive were normalized to the
locked build timestamp `1787875200` and indexed by the Fedora 44 package
`createrepo_c-1.2.1-5.fc44.aarch64`. The generator RPM verifies clean; its
72,464-byte executable SHA-256 is
`3e7e3ec49ff9bfe3ff1a2748fbafb6254379fe52e02e053438164ec03e149c1d`.
Both runs used SHA-256 package/repomd checksums, no database, XZ compression,
fixed revision/timestamps and one worker. Their four repodata files are
byte-identical.

The reproducible `repomd.xml` SHA-256 is
`c45ef7800681004df2d6e17436de3c02739d3be2a680aab95e9040402878bf39`.
The three content-addressed primary/filelists/other streams are locked by both
compressed and open SHA-256/size. The primary metadata contains exactly 322
safe, sorted package locations; every NEVRA, file size and package checksum is
cross-bound to the rootfs archive. Filelists and other metadata contain the
same 322 package IDs/names/architectures. The canonical location/checksum hash
is `3b5a03e00cdf46fd089ff60ef77c756af76fdebb573c9dcd017a1c5ad2cead02`.

`pds020-userspace-rpm-repository-verify.py` rechecks both private result
directories, the complete package archive/signatures/project payload and the
installed generator without generating or modifying metadata. The repository
is still explicitly unsigned: no `repomd.xml.asc` exists, no release key was
selected or created, the project RPM is unsigned and `release_ready=false`.

The `.pds2` RPM remains unsigned and uninstalled on the daily device. Project
signing, detached repository-metadata signing, image-root SELinux/service tests
and the mounted-root release audit remain mandatory; `release_ready` stays false.

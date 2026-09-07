# PDS-020 signed userspace repository chain

## Outcome

The repository now contains a verifier for a future signed `.pds2` package
archive and detached repository-metadata signature. It does not select or
generate a release key, sign an RPM, sign metadata, download packages, install
software or mutate a repository. Real signed inputs do not yet exist, so no
production pass is claimed.

DNF5's installed Fedora 44 documentation states that `repo_gpgcheck=true`
performs an OpenPGP check on repository metadata and that repository-metadata
keys are stored separately from package-signature keys, per repository. The
default is false. The final image must therefore enable this option explicitly
and pass a later DNF5 installroot smoke; a standalone OpenPGP verification is
necessary but not sufficient.

Primary references:

- <https://dnf5.readthedocs.io/en/latest/dnf5.conf.5.html>
- <https://www.gnupg.org/documentation/manuals/gnupg/gpg.html>
- <https://rpm.org/docs/6.0.x/man/rpmkeys.8>

## Required order

Signing an RPM changes its container SHA-256, even when its payload stays the
same. The current reproducible repodata binds the unsigned project RPM and must
never be signed and relabelled as the final repository. The receipt and
verifier enforce this exact order:

1. verify the existing unsigned 322-package rootfs archive;
2. verify the signed project RPM against its `.pds2` post-sign receipt;
3. replace only the project archive member and derive a new 322-package lock;
4. generate final repodata twice from that signed package set and verify both;
5. verify `repomd.xml.asc` against the receipt-bound public certificate.

All three compressed/open primary, filelists and other hashes must change from
the unsigned proof. The final primary metadata is checked against the derived
signed archive: 321 Fedora RPM records remain byte-identical and signed by the
Fedora key; only the project container size/SHA/signature state changes, while
the `.pds2` NEVRA and payload remain bound to the canonical build.

## Receipt and verification boundary

The external private JSON receipt binds the three pre-sign lock hashes, `.pds2`
RPM receipt and signed RPM, derived final-archive aggregate, exact Fedora 44
`createrepo_c` identity/arguments, both final metadata results, detached
signature, public certificate, primary/signing fingerprints, OpenPGP version
and SHA-256/SHA-512 algorithm. Duplicate/unknown fields, weak types, non-finite
JSON, links, public result directories, extra files, content drift and reused
metadata are rejected.

`repomd.xml.asc` must be present inside both private repodata results and match
byte-for-byte. The verifier first inspects the supplied file as exactly one
public certificate, imports only that certificate into an ephemeral mode-0700
GnuPG home with automatic key retrieval disabled, and accepts exactly one
`GOODSIG`/`VALIDSIG` set. Bad, missing, expired, revoked, multiple, wrong-key,
wrong-version or wrong-hash results fail closed. RPM and repository receipts
must bind the same public certificate and primary fingerprint.

Ten fixture/static tests cover the complete future chain, strict receipt,
single project replacement, exact final archive, public-only certificate,
signature identity, signature failure classes, shared RPM/repository key,
unsigned-metadata reuse and absence of signing/network/install/build paths.

Even a successful future run returns
`repository_ready_for_composition=true` but `release_ready=false`.

## DNF5 signed-repository smoke

The next confirmation-gated harness is also implemented and covered by ten
fixture/static tests. It first reruns the final-repository gate, copies the
exact 322 RPMs and five signed metadata files into a disposable private
`file://` repository, and lets the locked Fedora 44/aarch64 DNF5 resolve that
repository into a completely empty installroot. Both
`pds2.repo_gpgcheck=1` and `pds2.gpgcheck=1` are mandatory, plugins are
disabled, both receipt-bound public certificates are provided locally, and no
network repository is enabled.

An isolated Fedora 44 probe exposed a non-obvious failure mode: when
`repomd.xml.asc` is missing, DNF5 prints a signature/metadata error but can
still return zero after skipping an unavailable repository. The harness
therefore also forces `pds2.skip_if_unavailable=0`, rejects known
signature/skip diagnostics even on a zero exit, and requires the repoquery
result to equal all 322 locked name/epoch/version/release/arch rows. It then
installs those 322 exact NEVRAs with normal scriptlets/triggers, compares the
installed manifest, runs `dnf check` and the existing payload contract, reruns
the final-repository gate to detect evidence drift, and always removes the
disposable root. Its private mode-0600 report binds the final receipt, signed
project RPM, repomd, detached signature and final archive manifest hashes.

The harness cannot sign, generate keys, download, modify the device root or
start services. Real signed inputs do not yet exist, so the actual smoke is
still NOT RUN and `release_ready=false`. SELinux labels, image service
behavior, composition evidence, mounted-root audit and real upgrade/rollback
remain separate gates.

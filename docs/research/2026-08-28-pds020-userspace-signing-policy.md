# PDS-020 userspace RPM signing and receipt policy

## Outcome

The repository now has a verifier for a future signed hardened
`pocketds-userspace` RPM. It deliberately does not create or select a key, sign
an artifact, access the network, install a package or mutate the system RPM
database. Those are maintainer-controlled release decisions.

RPM's official model separates the signature header, main header and payload.
RPM 6 supports Header OpenPGP signatures and full-fingerprint key operations;
`rpmkeys --checksig` verifies both package digests and signatures. Therefore a
signed artifact must not be compared byte-for-byte with the canonical unsigned
RPM. Instead, this policy binds the new signed artifact hash and independently
proves that its immutable package header and payload remain the canonical build.

Official references:

- <https://rpm.org/docs/6.0.x/man/rpmkeys.8>
- <https://rpm.org/docs/6.0.x/man/rpmsign.1>
- <https://rpm.org/docs/6.0.x/manual/signatures_digests.html>
- <https://rpm.org/docs/6.0.x/manual/format_v4.html>

## Receipt contract

The external UTF-8 JSON receipt has exactly five top-level fields:

```json
{
  "schema": 1,
  "artifact": {
    "filename": "pocketds-userspace-20260507-20260730174706.pds1.fc44.noarch.rpm",
    "size": 0,
    "sha256": "64 lowercase hex after signing"
  },
  "canonical_build": {
    "build_lock_sha256": "64 lowercase hex",
    "pre_sign_binary_sha256": "64 lowercase hex",
    "payload_sha256": "64 lowercase hex",
    "payload_manifest_sha256": "64 lowercase hex"
  },
  "signing": {
    "public_key": {
      "filename": "project-release-key.asc",
      "size": 0,
      "sha256": "64 lowercase hex"
    },
    "fingerprint": "complete 40- or 64-character lowercase fingerprint",
    "signature_scope": "Header",
    "signature_version": 4,
    "signature_algorithm": "RSA/SHA256",
    "signature_count": 1
  },
  "policy": {
    "isolated_temporary_keyring": true,
    "legacy_signature_forbidden": true,
    "post_sign_payload_audit": true
  }
}
```

The zero sizes above are explanatory placeholders and will fail verification;
a real receipt records measured positive sizes and hashes. Supported Header
signature versions are 4 and 6. Algorithms are restricted to RSA, ECDSA/EcDSA
or EdDSA with SHA-256 or SHA-512. SHA-1, MD5, DSA, unknown algorithms, legacy
header+payload signatures, absent signatures and multiple signatures fail.

## Isolated verification

`scripts/pds020-userspace-rpm-postsign.py` creates a private temporary
directory and points `rpmkeys --dbpath` at an empty database. It imports only the
exact public certificate whose filename, size and SHA-256 are in the receipt,
then confirms that the isolated key list contains exactly its full fingerprint.
It does not trust or modify the host keyring.

The verifier requires exactly these successful elements from RPM 6:

- one matching `Header OpenPGP V4` or `V6` signature;
- Header SHA-256 digest;
- Payload SHA-256 digest;
- no legacy or second OpenPGP signature.

It then checks the canonical build-lock hash, unsigned binary hash, RPM
identity/buildhost/buildtime/payload digest and repeats the 71-entry payload,
scriptlet, sudoers and fan-controller audit. Receipt JSON is duplicate-safe and
finite; artifact, receipt and key files must be bounded, regular, single-link,
non-writable by group/other and owned by root or the invoking user.

Nine fixture/static cases cover a valid future result, signed artifact/key/
canonical drift, receipt types and policy, exact RPM output, unsigned/legacy/
multiple/wrong signatures, header/payload drift, unsafe files and verification-time
evidence drift. The real
canonical RPM is unsigned, so no real post-sign PASS is claimed.

## Remaining release gates

A PASS sets `artifact_ready_for_repository_staging=true`, not
`release_ready=true`. The project still needs a deliberately selected and
protected release key, a measured real receipt, signed immutable repository
metadata, upgrade/rollback proof, a clean composition and mounted-root audit.
Private key material must never enter this repository, a diagnostic bundle or
the Pocket DS image.

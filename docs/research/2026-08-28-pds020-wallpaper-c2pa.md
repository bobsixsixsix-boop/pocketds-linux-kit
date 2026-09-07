# PDS-020 wallpaper C2PA provenance

## Verified fact

Both checked-in wallpapers contain embedded C2PA 2.2 claims. The current
official CAI `c2patool` release verified the image data hashes, claim signature,
assertion hashes and signing chain against the official C2PA signer trust list.
Both reports reached `validation_state: Trusted` with no failure status.

The active claims identify `OpenAI Media Service API`, creation by `gpt-image`
2.0 and the IPTC digital source type `trainedAlgorithmicMedia`. The two active
manifest identifiers, image hashes, dimensions, signature times and exact
validation-code counts are locked in
`components/assets/provenance/c2pa-receipt.json`.

Verification inputs:

- `c2patool` 0.26.60 universal Apple archive: 19,955,218 bytes,
  SHA-256 `4ca9a8e0937ca28b9090c35e5af1a41c252023799b9bca07f27d974e9ea57beb`;
- extracted binary: 46,628,528 bytes,
  SHA-256 `3fd3d51f90317f4cffe37b44b7e397392d92dc178dce609ba48e0bcee15420a6`;
- C2PA conformance-public commit
  `2466172859fad1215f7aaf7e3768b41a0ac29abc`;
- signer trust list SHA-256
  `75cacc98b79ecac33713c7ecfb58d4a0ef383f3c1f886e7409f9e37e8664aea5`;
- TSA trust list SHA-256
  `c688d3555f4a2f1f8d663472bbd37888ff234abdd234c25934c0f9292e4eb5c9`.

Primary references:

- <https://github.com/contentauth/c2pa-rs/releases/tag/c2patool-v0.26.60>
- <https://github.com/contentauth/c2pa-rs/blob/main/cli/docs/usage.md>
- <https://github.com/c2pa-org/conformance-public>

The verifier is offline: its checked-in settings disable OCSP and remote
manifest fetching, both trust lists are local content-pinned inputs, and the
tool/asset/trust bytes are checked before and after child-process validation.

## Timestamp limitation

The RFC 3161 message digest validates, but c2patool 0.26.60 emits informational
`timeStamp.untrusted`: its CLI trust command consumes the signer trust list and
does not expose the separate TSA trust list required by newer C2PA trust models.
This is recorded as `timestamp_trust_verified=false`, not silently ignored.
The upstream limitation is tracked at:

- <https://github.com/contentauth/c2pa-rs/issues/1776>

## Rights boundary

C2PA proves the recorded generation/integrity chain; it is not a copyright
license, a human-author identity statement, or permission for every depicted
character, logo or mark. The official OpenAI product documentation domains
searched for this work did not provide a page sufficient to close those legal
questions. Therefore the receipt deliberately records:

- `c2pa_proves_license=false`;
- `human_author_identified=false`;
- `license_identified=false`;
- `redistribution_permission=false`;
- `release_ready=false`.

The personal daily-device wallpaper can remain installed. Public firmware must
either obtain and record adequate permission or use the strict asset-free
composition profile. That profile passes only when both its manifest array and
real asset directory are empty. The installer mirrors this with explicit
`--personal-assets` and `--asset-free` flags, defaults to the personal path and
runs the read-only preflight before any mutation. It never deletes the personal
device copies.

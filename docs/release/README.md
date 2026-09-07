# Release evidence workspace

For the initial source preview, start with [SOURCE-RELEASE.md](SOURCE-RELEASE.md).
The mounted-root, signed-RPM, whole-image SBOM and rescue gates below apply to
image/binary distribution. Source publication retains its own license, provenance
and privacy review; it does not claim that those image gates have passed.

This file also preserves historical packaging work. Current source attribution
is mapped in [THIRD-PARTY.md](../../THIRD-PARTY.md); source previews generate an
exact empty asset manifest instead of redistributing personal wallpapers.

This directory records unresolved release evidence without turning guesses into
licenses or redistribution grants. The authoritative gate is
`scripts/pds020-release-root-audit.py`; a non-empty placeholder does not pass it.

## Current evidence and remaining image-release blockers

| Evidence | Current state | Required resolution |
|---|---|---|
| Project `LICENSE` | [Full GPLv3 text](../../LICENSE) present; maintainer selected GPL-3.0-or-later for original material, subject to existing file-level and third-party licenses | Preserve the scope in [NOTICE](../../NOTICE); separately review additional material included in an image |
| `NOTICE` | [Source-preview notices](../../NOTICE) and component-specific notices present | Carry applicable notices into the final composition and account for any added components |
| `THIRD-PARTY.json` | Machine-readable composition inventory missing; [source attribution inventory](../../THIRD-PARTY.md) present | Inventory every redistributed non-system component with fixed version, HTTPS source, resolved license, explicit redistribution permission and checked-in content-hashed evidence |
| `SBOM.spdx.json` | Missing; pinned validator ready | Generate SPDX 3.0.1 JSON-LD from the final clean composition; pass the repository preflight and `make verify-spdx-offline` with the locked official JSON Schema plus OWL/SHACL model |
| `components/assets/ASSETS.json` | Source-preview exporter generates the exact empty `asset-free` manifest; final image composition remains unverified | Either resolve every redistributed runtime asset, or build a separately verified `asset-free` image composition with no runtime asset files |

Source-preview preparation does not satisfy the mounted-image audit. The reviewed
device screenshots and their [source record](../screenshots/README.md) are
documentation; runtime wallpapers remain excluded from source previews.

The broad sudo rule is separately understood rather than hidden in this table:
it belongs to the required `pocketds-userspace` hardware-integration RPM. Its
exact COPR source RPM, signing fingerprint and deterministic `.pds1` hardening
transform live under `packaging/pocketds-userspace/`. The transformed package
has been rebuilt twice in independent clean Fedora 44/aarch64 offline mock
results. The mock-emitted source RPM, binary RPM and buildroot package manifest
are byte-identical and pass the locked pre-sign payload audit. It has not been
signed, installed, upgrade-tested or included in a mounted release root. The
source-preparation, binary-payload and reproduction auditors require strict
UTF-8 JSON, reject duplicate keys and non-finite numbers, and require exact
integer types for package metadata.
The post-sign gate is also ready at fixture/static level. It verifies a
receipt-bound public certificate in an isolated temporary RPM database and
repeats the complete payload audit, but no project release key or real signed
artifact has been selected. Signing remains a maintainer-controlled release
operation, not an automatic repository action.
The payload-only vendor/hardened upgrade and rollback cycle has passed twice in
each state inside a disposable offline mock root. Because that run disabled
dependencies, scriptlets and triggers, a full clean-root transaction is still a
release blocker.

## Asset worksheet

The hashes are facts; the legal/source fields remain unresolved and therefore
blocked. Do not create `ASSETS.json` with guessed values.

| Repository path | SHA-256 | Known provenance | Release status |
|---|---|---|---|
| `assets/wallpapers/neko-bass-upper.png` | `e95ac3d4098a675b4029bbd004eebccae6fb10b36a3a9bbf4c01380063b4fa2c` | Trusted C2PA: OpenAI Media Service / GPT Image 2.0; exact receipt present; C2PA is not a license and human author/character rights are not recorded | BLOCKED |
| `assets/wallpapers/tendou-kei-lower.png` | `892a6cb49a8c870288a387af3bf0de8c21b75b660d354800349d20cf843d4be2` | Trusted C2PA: OpenAI Media Service / GPT Image 2.0; exact receipt present; C2PA is not a license and human author/character rights are not recorded | BLOCKED |

## Manifest contracts

Each `THIRD-PARTY.json` component must contain:

```json
{
  "id": "stable-component-id",
  "name": "Component name",
  "version": "fixed-version-or-commit",
  "license": "resolved SPDX expression or LicenseRef",
  "source": "https://upstream.example/source",
  "redistribution": true,
  "evidence": [
    {
      "path": "checked/in/evidence-file",
      "sha256": "64-lowercase-hex-digits"
    }
  ]
}
```

`components/assets/ASSETS.json` has exactly `schema`, `profile` and `assets`.
For `profile: redistributable`, every entry must contain `path`, `sha256`,
`source`, `author`, `license` and `redistribution: true`, and the manifest path
set must equal the complete regular-file set below `assets/`. For
`profile: asset-free`, both the array and the real `assets/` directory must be
empty. Unknown profiles/fields, symlinks, missing or duplicate entries, changed
hashes, duplicate JSON object keys at any nesting level, non-UTF-8 encodings,
non-finite numbers, and any file hidden behind an empty profile fail closed.
The same strict JSON rules apply to the SPDX and third-party manifests.

The SPDX preflight expects the official 3.0.1 context and flattened `@graph`,
exactly one `SpdxDocument`, referenced 3.0.1 `CreationInfo` and creator Agent,
unique URI/blank-node identifiers, a document-rooted `software_Sbom`, and a
complete referenced package set with fixed `software_*` identity, copyright and
source fields. It is intentionally not a substitute for the separately pinned
official JSON Schema and OWL/SHACL validators.

## Safe workflow

1. Preserve the selected project license and existing file-level notices. Review
   ownership, source and applicable licenses for any material added to the final
   image composition; the source-preview license does not cover every dependency.
2. Keep the trusted C2PA receipt as provenance, but either resolve the two
   wallpaper author/license/depicted-right records or use an independently
   composed `asset-free` public profile. Do not infer permission from C2PA or
   delete the personal daily-device copies to manufacture a release tree.
   The offline C2PA verifier accepts only strict UTF-8 JSON, an exact Trusted
   validation state, the pinned success-code set and an empty failure set; the
   receipt cannot redefine those trust requirements or reauthorize different
   c2patool and signer/TSA trust-list hashes.
   The installer requires that choice explicitly and runs its read-only asset
   preflight before creating backups, compiling or invoking sudo.
3. Build the image from a clean composition and produce its package inventory.
   Composition locks and their content-hashed gate receipts must use strict
   UTF-8 JSON: duplicate object keys, non-finite numbers, ambiguous boolean/
   integer values and over-deep structures fail closed.
4. Fill third-party and SPDX records only from pinned source/package receipts.
5. Run `make test-release-audit`, then audit the offline mounted image with
   `make audit-release-root RELEASE_ROOT=/mnt/pocketds-release`.
6. Validate `SBOM.spdx.json` against the official SPDX 3.0.1 JSON Schema and
   OWL/SHACL model with `make verify-spdx-offline`. Use only the lock-matching
   offline specification files; the verifier performs no downloads and keeps
   legal/redistribution conclusions unresolved.

Never derive release evidence by deleting private data from the daily-use
Pocket DS or by relabeling unknown material as redistributable.

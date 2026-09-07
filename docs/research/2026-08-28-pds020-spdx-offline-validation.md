# PDS-020 pinned offline SPDX 3.0.1 validation

## Outcome

The repository now has a read-only validator for a future final SPDX 3.0.1
JSON-LD SBOM. It independently requires both validation layers specified by
SPDX: structural validation against the official JSON Schema and semantic
validation against the official OWL model with its SHACL constraints. Eleven
fixture/static tests pass, and the pinned Fedora 44/aarch64 toolchain validated
the official `package_sbom.json` example with zero schema errors, 47 RDF data
triples and a conforming SHACL result.

This tool validates syntax and model conformance only. It does not generate an
SBOM, select a project license, infer copyright/redistribution rights, download
materials or make the firmware release-ready.

Primary references:

- <https://spdx.github.io/spdx-spec/v3.0.1/serializations/>
- <https://spdx.org/schema/3.0.1/spdx-json-schema.json>
- <https://spdx.org/rdf/3.0.1/spdx-context.jsonld>
- <https://spdx.org/rdf/3.0.1/spdx-model.ttl>
- <https://github.com/spdx/spdx-spec/blob/61a649da8ca27924ac1ca8d2a061cb228839b24c/examples/jsonld/package_sbom.json>

## Immutable offline boundary

`packaging/image/spdx-validation-lock.json` pins the three specification URLs,
their exact sizes/SHA-256 values, the SPDX model/spec release commits and the
four Fedora validator package identities. The validator accepts only regular,
single-link, current-user files of bounded size and rehashes every official
material. JSON-LD replaces the single top-level context IRI with the verified
local context object in memory before RDF parsing, so no context is fetched.
Nested contexts and OWL imports are disabled. The result can be written once as
a private mode-0600 report containing hashes/counts but no input paths.

The Fedora 44/aarch64 validation identities are:

- `python3-libs-3.14.3-2.fc44.aarch64`;
- `python3-jsonschema-4.23.0-7.fc44.noarch`;
- `python3-rdflib-7.1.4-7.fc44.noarch`;
- `python3-pyshacl-0.30.1-6.fc44.noarch`.

## Upstream ambiguity handled explicitly

The current normative `spdx.org` 3.0.1 semantic model is 182,034 bytes with
SHA-256 `30ebb4af…`; the GitHub 3.0.1 release asset is 183,176 bytes with
SHA-256 `6b0b3b91…`. They are not byte-identical, so the validator never fetches
either implicitly and the lock deliberately selects the URL named by the SPDX
3.0.1 serialization specification.

The current official JSON Schema also contains two duplicate object members:
`extension` and `prop_Element_extension`. Each duplicate pair has identical
values. A generic last-value-wins JSON parse would hide this, while rejecting
all duplicates would make the official validator unusable. The implementation
therefore permits exactly those two official-schema keys, each duplicated once
with an equal value, only after the whole artifact hash is verified. The SBOM,
lock and context remain strict UTF-8 JSON where every duplicate or non-finite
value fails closed.

## Usage

Obtain the three official files once into a private offline evidence directory,
verify their hashes against the checked-in lock, then run:

```sh
SBOM=/private/SBOM.spdx.json \
SPDX_CONTEXT=/private/spdx-context.jsonld \
SPDX_JSON_SCHEMA=/private/spdx-json-schema.json \
SPDX_MODEL=/private/spdx-model.ttl \
OUTPUT=/private/new-spdx-validation-report.json \
  make verify-spdx-offline
```

A successful validation report deliberately keeps
`legal_conclusion=NOT_DETERMINED`,
`rights_or_redistribution_inferred=false` and `release_ready=false`. The final
composition still needs complete package content, license/notice/source and
asset evidence plus the mounted-root audit.

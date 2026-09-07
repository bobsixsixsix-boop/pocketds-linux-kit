# PDS-020 mounted-root release audit — 2026-08-28

## Outcome

`scripts/pds020-release-root-audit.py` is now the fail-closed, read-only gate for
an offline mounted release root. It does not sanitize a system, call package or
service tools, upload data, or print discovered paths and account names. Exit 0
means every implemented gate passed, exit 1 means the target has release
blockers, and exit 2 means the target or invocation was unsafe/invalid.

The preferred path is a clean Fedora image composition. The live development
root is accepted only with the exact `READ-ONLY-LIVE-ROOT` confirmation token;
that exception exists to measure blockers and is not a release workflow.

## Implemented gates

- `/etc/sudoers` and `/etc/sudoers.d` must be real, bounded, root-owned and not
  writable by group/other. No rule outside the exact repository Panel policy
  may contain `NOPASSWD`, and `NOPASSWD: ALL` is always a blocker.
- `LICENSE`, `NOTICE`, `THIRD-PARTY.json`, `SBOM.spdx.json` and
  `components/assets/ASSETS.json` must be present and non-empty. A non-empty
  placeholder is not a pass. Third-party entries require unique identity,
  pinned version, HTTPS source, resolved license, explicit redistribution
  permission and content-hashed checked-in evidence. A `redistributable` asset
  manifest must cover every file below `assets/` exactly once and match its
  SHA-256, source, author, license and permission. An `asset-free` manifest
  passes only when both its array and the real asset directory are empty; it
  cannot hide an unlisted file.
- The SPDX preflight follows the official 3.0.1 JSON-LD flattened `@graph`
  representation. It requires exactly one `SpdxDocument`, referenced
  `CreationInfo`, at least one referenced `software_Sbom`, complete package
  membership and profile-prefixed `software_packageVersion`,
  `software_copyrightText` and source fields. Legacy embedded-object fixtures,
  dangling references, duplicate IDs and unreferenced packages fail closed.
  This remains a bounded release-policy preflight: final artifacts must also
  pass `make verify-spdx-offline` against the pinned official JSON Schema and
  OWL/SHACL model.
- The corrected five-node fixture was independently exercised in the isolated
  Fedora 44/aarch64 validator environment. Both official layers passed and the
  JSON-LD expansion produced 26 RDF triples; this checks fixture/model
  compatibility without claiming that the fixture is a final release SBOM.
- Chromium is rechecked in process against the pinned runtime lock. File
  integrity and package provenance remain separate; matching extracted files
  cannot turn incomplete source receipts into a pass.
- Home scanning reports only fixed finding categories for SSH/Codex/Steam/
  browser/VPN/cache/ROM/save/messaging/credential/development state. It never
  includes a home name, file name or discovered path in the JSON report.
- System-state scanning rejects persisted WireGuard/NetworkManager/Tailscale
  credentials, SSH host identity, initialized machine-id and systemd random
  seed. These must be generated or enrolled on first boot, not cloned.
- Reads use `O_NOFOLLOW`, regular-file/link-count/size bounds where content is
  consumed, and a bounded home scan. Reports declare their privacy contract.
  Python bytecode output is disabled so importing the Chromium verifier cannot
  modify the repository.

## Device evidence

The post-provenance 2026-08-28 live-development-root audit returned exit 1 as
expected. Its 5,000-byte report was mode 0600 and disclosed no discovered path
or account name. Chromium now passes integrity and provenance with zero gaps;
the overall development image remains blocked by nine categories:

- one broad and one unexpected passwordless policy result, both caused by the
  same vendor `%wheel ALL=(ALL) NOPASSWD: ALL` rule;
- missing release files, project license/notice, SPDX, third-party evidence and
  two-asset redistribution evidence;
- daily user state and cloned system identity/network secrets, reported only as
  fixed categories.

The broad rule is owned by the otherwise required `pocketds-userspace` RPM.
Its signed source RPM and deterministic `.pds1` hardening transform are now
pinned separately; no package has been built or installed on the daily device.

### Initial fail-closed run

This captured run predates the later Chromium package-receipt closure. Its five
Chromium gaps are historical evidence of the auditor's fail-closed behavior,
not the current repository lock state. The future clean composition must rerun
the audit rather than editing this record retroactively.

- sudoers file safety: PASS
- broad and unexpected passwordless sudo: FAIL, one vendor rule
- release evidence/SPDX/third-party/assets: FAIL
- Chromium runtime: integrity PASS, provenance incomplete with five gaps
- private user state: FAIL by category, as expected on the daily-use device
- private system state: FAIL for boot random seed, machine identity,
  NetworkManager connection secrets and SSH host identity

This result proves the auditor can distinguish a working development machine
from a distributable image. It is not permission to delete credentials or
weaken recovery access on the only development unit.

## Reproducible commands

```sh
make test-release-audit

# Preferred: audit an offline mounted image.
make audit-release-root RELEASE_ROOT=/mnt/pocketds-release

# Diagnostic exception for the development machine; always capture privately.
umask 077
sudo env PYTHONDONTWRITEBYTECODE=1 \
  ./scripts/pds020-release-root-audit.py \
  --root / \
  --allow-live-root READ-ONLY-LIVE-ROOT \
  >test-results/pds020-live-root-audit.json
```

## Release construction decision

Do not derive a public image by deleting data from the daily-use home. Compose a
clean Fedora 44 root with Image Builder/osbuild, install signed project RPMs,
leave per-machine identity uninitialized, and audit the mounted output before
signing. The current upstream references are:

- [Image Builder introduction](https://osbuild.org/docs/user-guide/introduction/)
- [Image descriptions and supported distributions](https://osbuild.org/docs/user-guide/image-descriptions/)
- [Blueprint reference](https://osbuild.org/docs/user-guide/blueprint-reference/)
- [SPDX 3.0.1 specification](https://spdx.github.io/spdx-spec/)
- [SPDX 3.0.1 JSON-LD serialization](https://spdx.github.io/spdx-spec/v3.0.1/serializations/)
- [Official SPDX 3.0.1 JSON Schema](https://spdx.org/schema/3.0.1/spdx-json-schema.json)
- [SPDX Lite mandatory fields](https://spdx.github.io/spdx-spec/v3.0.1/annexes/spdx-lite/)

PDS-020 stays open until a separately built mounted image returns exit 0 and its
license/source offer, checksums, recovery material and SBOM are signed together.

# Fedora 44 rootfs composition boundary

This directory is a fail-closed staging contract, not a flashable firmware
image. The pinned upstream Image Builder description supports Fedora 44 on
aarch64 and describes `generic-container` as `bootmode: none`, with no partition
table and a `container.tar` result. That makes it useful for assembling and
auditing a clean root filesystem without claiming compatibility with the Pocket
DS ABL, boot partitions, DTB, kernel or Android layout.

`pocketds-rootfs.template.toml` deliberately contains an unresolved project RPM
version and pins the current hardened userspace line to `.pds2`; its signature
gate remains false. `composition-lock.json` also leaves every build/release
gate false.
`make preflight-composition` must therefore fail until all of these are real,
signed and content-pinned:

- the Image Builder runtime;
- the project and hardened `pocketds-userspace` RPMs;
- an immutable signed Fedora/project repository snapshot;
- license, NOTICE, SPDX and asset redistribution evidence;
- a clean-root smoke result.

The signed-repository gate does not accept the generic four-field evidence
shape used by unfinished gates. It must consume the complete private output of
`pds020-userspace-rpm-repository-smoke.py`, including the final receipt/RPM/
repomd/signature/archive bindings, locked DNF5 identity, both GPG checks,
`skip_if_unavailable=false`, exact 322-package query/install results and
disposable-root cleanup boundary. A valid repository smoke still does not
claim SELinux, image services or whole-release readiness.

The release-evidence gate also rejects that generic shape. Its receipt must
content-bind the exact repository `LICENSE`, `NOTICE`, `THIRD-PARTY.json`,
`SBOM.spdx.json` and `components/assets/ASSETS.json`, plus two unique bounded
reports: a fully passing offline mounted-root audit and a passing offline SPDX
validator report. The latter must match the repository SBOM, the fixed
`spdx-validation-lock.json`, all three official material hashes and all four
Fedora validator package identities. Either report remains non-authorizing on
its own; the receipt itself therefore keeps `release_ready=false`.

The future final `SBOM.spdx.json` has a separate pinned offline validator:
`make verify-spdx-offline`. It checks both the official SPDX 3.0.1 JSON Schema
and OWL/SHACL model using exact Fedora 44 validator packages and local,
content-hashed specification materials. It never treats schema/model conformance
as a license or redistribution decision. See
`docs/research/2026-08-28-pds020-spdx-offline-validation.md`.

The future `container.tar` and its already mounted/extracted root have a
separate read-only smoke: `make plan-rootfs-smoke` and, with explicit inputs,
`make run-rootfs-smoke`. It binds the artifact and locks, checks the exact
322-package RPMDB and hardened payload contract, requires SELinux labels and
runs all mounted-root checks in-process against the same non-live root. It does
not extract or mount the artifact and does not start services. See
`docs/research/2026-08-28-pds020-rootfs-smoke.md`.
The `rootfs_smoke_passed` composition gate consumes only this report's exact
schema and cross-checks its stable composition-policy digest, repository
rootfs/build locks, package manifest, SELinux and mounted-root fields. A generic
gate receipt cannot close it.

The blueprint may not embed a user, password, SSH key, host identity, network
profile, repository URL, first-boot secret, unattended disk wipe or global sudo
grant. First-boot identity generation belongs to the later installer layer.
That installer and every ABL/partition/boot action remain a separate explicitly
approved phase. Never build on the daily Pocket DS or flash `container.tar`.

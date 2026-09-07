# PDS-020 clean composition boundary

## Decision

Use Fedora Image Builder only to create a clean, auditable **root filesystem
staging artifact**. Do not treat a generic Fedora disk image as Pocket DS
firmware and do not let this stage write a partition table, ABL, boot partition,
kernel or DTB.

The current official Image Builder catalog supports Fedora 44 and aarch64. Its
Fedora 44 `generic-container` description reports `bootmode: none`, an empty
partition type and `container.tar` output. This is the narrow upstream target
recorded in `packaging/image/composition-lock.json`:

- <https://osbuild.org/docs/user-guide/image-descriptions/fedora-44/>
- <https://osbuild.org/docs/user-guide/image-descriptions/fedora-44/generic-container/>
- <https://osbuild.org/docs/developer-guide/projects/image-builder/usage/>

This selection is a staging boundary, not proof that the container base package
set is a complete desktop. The final rootfs still requires signed project,
hardware, desktop, kernel-adjacent and application RPMs plus boot-time smoke
tests. Hardware boot integration remains a later, separately approved artifact.

## Supply-chain rule

The upstream page was generated with
`ghcr.io/osbuild/image-builder-cli@sha256:27cabea9e504a7c4a7546f287f5d60a4846c62b5a8d18c0f678d25fdc2d7975a`.
The digest is recorded but the gate remains false until the actual image is
retrieved and independently verified. A tag such as `latest` is forbidden.

Do not use a convenient mutable `--extra-repo` as release evidence. The official
repository guide explicitly says command-line `force-repo`/`extra-repo` content
is not verified, and blueprint repositories configure the result rather than
the build source. The release needs an immutable signed repository snapshot with
content-hashed metadata and RPMs:

- <https://osbuild.org/docs/developer-guide/projects/image-builder/advanced/repositories/>

## Identity and failure behavior

The blueprint grammar is intentionally restricted to metadata and exact package
pins. It cannot contain users, passwords, SSH keys, hostname, network profiles,
repository URLs, first-boot scripts, unattended disk wipes or sudo grants.
Machine identity, host keys and the first user are generated only by the later
installer/first-boot layer. Image Builder documents first-boot services, while
the osbuild machine-id stage distinguishes uninitialized first boot from merely
empty identity:

- <https://osbuild.org/docs/user-guide/firstboot/>
- <https://osbuild.org/docs/developer-guide/projects/osbuild/modules/stages/org.osbuild.machine-id/>

The checked-in template deliberately has one unresolved package version and all
six gates false. `make preflight-composition` therefore exits 1 with fixed
blocker categories. It does no network access, build, installation or write.
Every true gate also requires a unique, repository-bounded, content-hashed JSON
evidence record naming the same gate and a non-placeholder subject digest;
symlinked evidence parents are rejected. The repository gate consumes the full
DNF5 signature/install smoke receipt. The release-evidence gate now separately
binds all five repository evidence files, a complete 11-check offline
mounted-root audit, and the official two-layer SPDX validation report to the
fixed validator lock and same SBOM hash; a generic four-field receipt is
rejected. The rootfs-smoke gate likewise accepts only the structured offline
smoke report bound to the stable composition policy, actual rootfs/build locks,
322-package manifest, SELinux and mounted-root results. Only a fully pinned
fixture passes its 19 tests.

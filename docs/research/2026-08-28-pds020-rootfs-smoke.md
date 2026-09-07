# PDS-020 offline final-root smoke

Date: 2026-08-28. This is a future release-image gate. No final osbuild
artifact exists yet, and the daily Pocket DS must not be used as its target.

`pds020-rootfs-smoke.py` inspects one already extracted or mounted offline root
and its matching `container.tar`. It never extracts, mounts, installs, erases,
repairs, starts services, accesses the network or accepts `/` as the target.
Execution needs the exact confirmation, a non-live Linux root, the artifact and
a new private report path.

The smoke binds and checks:

- artifact size/SHA-256 and stable identity across the full run;
- the composition, `.pds2` rootfs-transaction and build locks, including the
  build-lock link already recorded by the rootfs lock;
- Fedora 44 identity and an RPMDB containing exactly the locked 322
  `aarch64`/`noarch` package rows;
- exact hardened `pocketds-userspace` NEVRA and RPM verification output;
- absence of retired Onboard packages/paths, presence and content of critical
  payloads, and exact system service enablement symlinks;
- non-empty, syntactically bounded `security.selinux` labels on the three
  critical hardware-integration payloads;
- all 11 mounted-root sudoers/evidence/Chromium/privacy/identity checks, run
  in-process against the same offline root;
- a second artifact/lock/root-inode/package-manifest measurement after the
  audit, so a mid-run replacement fails closed.

The report is a new mode-0600 file. It contains hashes, counts and fixed policy
booleans, not paths or private identities. A successful smoke still emits
`release_ready=false`: signing, builder provenance, official SPDX validation
and the other composition gates remain separate.
Its composition binding deliberately hashes only the stable builder/boundary/
blueprint/identity policy plus the actual blueprint bytes, not the gates object:
hashing the full lock would create a cycle because that lock also records the
smoke report hash. The composition preflight now consumes the exact report
schema and independently recomputes that stable policy and both package locks.

```sh
make test-rootfs-smoke
make plan-rootfs-smoke
CONFIRM='RUN READ-ONLY PDS020 ROOTFS SMOKE' \
RELEASE_ROOT=/mnt/pocketds-release \
ROOTFS_ARTIFACT=/private/container.tar \
OUTPUT=/private/new-rootfs-smoke.json \
make run-rootfs-smoke
```

Ten fixture/static tests cover the complete happy path, exact composition
object shapes and safe rootfs-only boundaries, Fedora's canonical
`/etc/os-release -> ../usr/lib/os-release` layout (while rejecting arbitrary
links), package duplicate and
architecture drift, artifact/payload/service-link tamper, mandatory SELinux and
mounted-root audit gates, live-root refusal before tool execution, private
no-clobber output, the execution confirmation and absence of mutation/network/
service-start code paths. Two additional composition cases reject a generic
receipt and drift in policy/package/SELinux/audit fields. Real execution remains
NOT RUN until the signed composition produces an artifact.

# PDS-002 independent reproducible kernel build — 2026-08-28

## Scope and safety

This work was performed in an isolated Fedora 44/aarch64 Lima VM on the Mac.
It did not install a package on the Pocket DS, alter `/boot`, replace a kernel
or DTB, restart Plasma/KWin, suspend, reboot, or change a live performance/fan
profile.  The device's 24-hour telemetry soak stayed in the same systemd
invocation with zero restarts throughout the build investigation.

The objective was narrower than creating an A/B candidate: independently
rebuild the exact signed COPR source RPM and determine whether the complete
binary RPM payload can equal the signed reference.  Candidate construction,
rollback boot validation, and device installation remain separate gates.

## Locked environment and inputs

The VM used Fedora Cloud Base 44-1.7 aarch64, image SHA-256
`55c60a3b80d3616a08705afd0459e75fe9f03c54aba7a46e4002a41a72fa0d5b`,
8 vCPU, 16 GiB RAM, 8 GiB swap, and a 40 GiB disk.  Host mounts and containerd
were disabled.  The build ran in a retained Mock root rather than on the host
filesystem.

The already verified COPR evidence supplied:

- build `10794847`, Fedora 44/aarch64;
- signed source RPM SHA-256 `03076822...`, 259,026,310 bytes;
- signed reference RPM SHA-256 `3d1502c7...`, 37,131,042 bytes;
- exact source commit `a4975ce7c8eec079bd0cf7ec3890e100e016813f`;
- original COPR child configuration and logs;
- `SOURCE_DATE_EPOCH=1778025600`.

The original build used GCC `16.1.1-2.fc44`; current Fedora 44 repositories
provided GCC 16.2.1.  Nine historical aarch64 GCC packages were downloaded
from Fedora Koji and locked individually.  The final retained root reports:

```text
gcc (GCC) 16.1.1 20260515 (Red Hat 16.1.1-2)
libfaketime-0.9.12-12.fc44.aarch64
```

Mock 6.8 reproduced a build originally run under Mock 6.7.  The version
difference did not affect the final payload after the actual compiler and
metadata inputs were restored.

## Investigation sequence

The first ordinary `copr-rpmbuild` replay completed successfully, but only 16
of 341 regular payload files matched.  The config differed only in its two GCC
identity lines; 325 file hashes differed.  This proved the source and build
procedure were viable, but not reproducible against a floating compiler.

Downgrading GCC and then running normal `mock --rebuild` was rejected as an
invalid experiment: Mock's builddep phase upgraded GCC again.  The accepted
method populated a retained root, installed all build dependencies, downgraded
the locked historical compiler set, installed the locked libfaketime RPM, and
then invoked `rpmbuild --rebuild` directly as Mock's unprivileged build user.

The remaining iterations isolated metadata rather than code generation:

1. Exact GCC plus globally forced `KBUILD_BUILD_TIMESTAMP` and
   `KBUILD_BUILD_VERSION` matched 337/341 files.  It was rejected because the
   original build contains an early temporary version string ending in
   `# SMP PREEMPT ` and only the final string contains
   `#1 SMP PREEMPT Thu Jul 30 17:34:11 UTC 2026`; global Kbuild variables
   change both stages.
2. Pinning only Kbuild user/host, intercepting the two required `date` forms,
   and normalizing initramfs filesystem metadata matched 339/341.  All 323
   modules, configs, both DTBs, and System.map were exact.  Only raw Image and
   its derived Android bootimg differed.
3. Decompressing the embedded initramfs showed identical 75-entry names,
   contents, modes, and owners.  Its 42 ordinary files correctly had epoch
   `1767225600`; 32 generated directory/symlink entries used the current build
   time instead of the original epoch `1785432757`.  This comes from
   `gen_init_cpio` calling `time(NULL)` for those entry types.
4. Applying libfaketime to the whole `make` process tree was rejected.  It
   changed unrelated generated data.  Limiting it to `gen_init_cpio` was also
   initially insufficient: libfaketime's default stat interception translated
   the already normalized regular-file mtimes, and its timestamp string was
   parsed in the buildroot's JST timezone.  Direct cpio inspection exposed
   both errors exactly.
5. The final wrapper uses `FAKETIME_ONLY_CMDS=gen_init_cpio`,
   `NO_FAKE_STAT=1`, and the JST expression that evaluates to epoch
   `1785432757`.  It restored the raw Image, System.map, modules, configs, and
   DTBs.  This reached 340/341.
6. The only remaining bootimg difference was 23 bytes: four gzip mtime bytes
   (three nonzero bytes differed) plus the 20-byte mkbootimg SHA-1 ID derived
   from that stream.  The spec uses `gzip -c arch/arm64/boot/Image`, so the
   gzip header records the source Image mtime.  Restoring the measured source
   mtime `1785432861` produced the exact gzip stream, mkbootimg ID, and final
   Android bootimg.

These normalizations reproduce historical archive metadata; they do not patch
kernel source or alter code/config semantics.

## Final evidence

The final unsigned rebuild RPM is 37,131,050 bytes with SHA-256
`37b2f27c8c956c1c0ae36800cdbd38f5a2ddf2a0fd470577d16af0144fc9a479`.
It is not byte-identical to the signed RPM container.  Both RPM headers declare
the same 496 payload entries.  Extracting either RPM supplies the same three
implicit parent directories (`boot`, `lib`, and `lib/modules`) and produces the
same 499-entry scanned tree:

| Type | Count |
|---|---:|
| regular files | 341 |
| directories | 156 |
| symlinks | 2 |
| regular-file bytes | 81,183,506 |

The complete normalized manifest SHA-256 is
`4654c0aa99fcf447b6c589986a1c286a33357aef2e164a65ab79f464ec6b1ada`.
The prior flat SHA-256 lists contain 341 lines each and have the same SHA-256
`facf8271b79d55fb44c3addf06bcfa23d804f68ade03cab5df8afe4811505640`;
their unified diff is empty.

Key matches include:

| Payload | SHA-256 |
|---|---|
| raw Image | `b77e3cecf7c49689017965b5b19829d8224144179fdd4c6c41d79c04e3239ba1` |
| Android bootimg | `cf910fef42e06aaae7f983d3e29ec920705e311e768721428b481a033670d55b` |
| System.map | `576ca5cc5ccfaf3e024043daea7fd02860fccbabe789afc000cac86b2b68c147` |
| package DTB | `0557cee113d4e34fcae274c69400a8a53653e60229a1f333eafde83b2dfe6994` |
| config | `5414bbb75c971adfc7410ca259994ce8a3c7013530105ea79a04500c7f750755` |

`tools/kernel-ab/reproducible-build/payload-evidence.py` independently replayed
the locked artifacts and both extracted trees. It queries each locked RPM with
fixed `/usr/bin/rpm`, without a shell, on the same read-only descriptor used to
recheck the locked RPM hash, then binds each package header manifest to its own
tree before comparing the trees. The successful retained-VM report has
SHA-256
`c3d9613f468ca7a5f61d85b179756a5a144b1535dae513109b02b5bd391f618b`;
its embedded lock identity is
`90ae07d624b0f595e171b97efbf864756ac3fbc355b8acf1f3a6caac49bb05db`.

The report contains:

```text
payload_manifest_byte_identical=true
rpm_container_byte_identical=false
complete_kernel_payload_reproduced=true
source_to_binary_reproducible_build_proven=true
rollback_artifact_prevalidated=false
```

The verifier is read-only, offline, fixed-lock, stdout-only on success, and
fail-closed for artifact, recipe, RPM-header manifest, content, size, mode, path,
symlink, special file, or payload-count differences. Unit tests cover success
plus file, mode, symlink, special-entry, artifact, RPM-header, digest algorithm,
schema, duplicate-key, and CLI-boundary failures.

## Remaining PDS-002 gate

The baseline source-to-binary question is now answered. The single-variable
A740 IFPC-revert candidate has since been built and independently reproduced in
two isolated roots; see `2026-08-28-pds002-ifpc-candidate-reproduction.md`.
The next safe step is a separate baseline rollback bootimg. Before the candidate
can touch the Pocket DS, the rollback image and report must be independently hashed,
cold-boot tested, recovery tested, bound to the exact device/release, and
accepted by the schema-v2 planner.  Only then can the three matched
165/60, 60/60, and single-screen A/B series begin under supervision.

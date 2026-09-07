# PDS-002 independent kernel payload reproduction

This directory preserves the exact normalization recipe and a fail-closed,
offline evidence replayer for COPR build `10794847`.  It is deliberately not a
kernel installer, boot-image flasher, downloader, or general-purpose builder.
Nothing here writes a Pocket DS, `/boot`, or a Mock root.

## Result and boundary

An independent Fedora 44/aarch64 build from the pinned signed source RPM
reproduced all 496 RPM-declared payload entries.  Extraction adds the three
implicit parent directories `boot`, `lib`, and `lib/modules`, so each scanned
tree contains 499 entries: 341 regular files, 156 directories, and two
symlinks.  File contents, sizes, modes, directory modes, and symlink targets
have normalized tree SHA-256
`4654c0aa99fcf447b6c589986a1c286a33357aef2e164a65ab79f464ec6b1ada`.
This includes all 323 modules, the raw `Image`, Android bootimg v0,
`System.map`, config, module metadata, and both copies of the Pocket DS DTB.

The signed reference RPM is 37,131,042 bytes with SHA-256 `3d1502c7...`; the
attested unsigned rebuild RPM is 37,131,050 bytes with SHA-256 `37b2f27c...`.
Their RPM containers are therefore **not** byte-identical.  Their complete
installable payloads are byte-identical.  The verifier reports these as two
separate booleans and never treats RPM-header/signature variance as payload
identity.

This closes only the source-to-binary reproduction gate.  It does not make an
IFPC candidate safe to install: a separately validated rollback bootimg,
independent recovery path, and supervised matched A/B runs remain mandatory.

## Why the wrappers exist

A normal replay against current Fedora 44 repositories selected GCC 16.2.1;
325 of 341 regular-file hashes differed.  Reinstalling historical GCC 16.1.1
before `mock --rebuild` was insufficient because Mock's builddep phase upgraded
it again.  The accepted run therefore populated and retained a Mock root,
downgraded the locked historical compiler RPM set, and invoked `rpmbuild
--rebuild` directly inside that root.

With the exact compiler, only build metadata remained:

- `pinned-child.cfg` preserves `mockbuild`, the COPR build host, and wrapper
  search path.  Its first line is the path used in the retained evidence VM;
  a replay may relocate that include only after checking `copr-child.cfg`.
- `date-wrapper.sh` reproduces the final Kbuild banner and the bootimg Android
  patch month without setting global `KBUILD_BUILD_TIMESTAMP`.  Setting that
  variable changed Kbuild's earlier temporary `version.o` and was rejected.
- `make-wrapper.sh` restores the initramfs archive's observed owner and two
  timestamp classes.  `libfaketime` is limited to `gen_init_cpio`, and
  `NO_FAKE_STAT=1` is essential so the library cannot translate the 42 real
  file mtimes while fixing the 32 generated directory/symlink mtimes.
- The final raw Image mtime is restored to epoch `1785432861` because the spec
  uses `gzip -c arch/arm64/boot/Image`; gzip stores that source mtime in the
  Android bootimg kernel stream.  Before this last normalization, 340/341
  regular files matched and only the gzip mtime plus its derived mkbootimg
  SHA-1 ID differed (23 bytes total).

The successful build used GCC `16.1.1-2.fc44`, libfaketime
`0.9.12-12.fc44`, Mock 6.8, and the original COPR child configuration emitted
by Mock 6.7.  `lock.json` pins the signed source/reference RPMs, attested
rebuild RPM, nine historical GCC runtime/development RPMs, libfaketime RPM,
all four recipe files, the build identity, every payload count, and five key
payload hashes.  The Fedora 44 aarch64 Lima base was
`Fedora-Cloud-Base-Generic-44-1.7.aarch64.qcow2`, SHA-256
`55c60a3b80d3616a08705afd0459e75fe9f03c54aba7a46e4002a41a72fa0d5b`.

## Evidence replay

Prepare one private artifact directory containing the exact filenames and
hashes in `lock.json`.  Extract the signed reference and attested rebuild RPMs
into separate new directories using the same `rpm2cpio | cpio -idm` tooling.
Do not overlay either extraction onto an existing tree.

Then run:

```sh
python3 tools/kernel-ab/reproducible-build/payload-evidence.py \
  --artifact-dir /private/pds002/artifacts \
  --reference-root /private/pds002/reference \
  --rebuilt-root /private/pds002/rebuilt \
  --pretty
```

The lock path is fixed beside the verifier.  The verifier has no alternate
lock, output, build, install, or network option.  It invokes only the fixed
`/usr/bin/rpm` binary, without a shell, to read each RPM's SHA-256 file manifest
through the same read-only file descriptor used to recheck its locked hash. It
binds each package to its own extracted tree before comparing the trees. It
opens locked regular files without following symlinks, rejects writable,
oversized, racy inputs and special payload files, hashes every regular file,
records modes, implicit parent directories and symlink targets, and writes only
the successful report to stdout.  Any mismatch exits 2 before stdout.  A
successful report still says
`rollback_artifact_prevalidated=false`.

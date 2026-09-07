# PDS-002 package/source/boot evidence — 2026-08-28

This is an offline provenance and reproducibility audit. It did not install a
kernel, change `/boot`, restart Plasma/KWin, suspend, reboot, or load a GPU or
fan profile. The one derived DTB was built on the Mac in an isolated scratch
tree while the device telemetry soak continued unchanged.

## Published COPR artifacts

The installed NEVRA is
`kernel-7.1.0-100.20260730172655.pocketds.fc44.aarch64`; RPM records its source
as `kernel-7.1.0-100.20260730172655.pocketds.fc44.src.rpm`. Official COPR build
`10794847` succeeded and its Fedora 44/aarch64 repository metadata binds the
following downloads:

| Artifact | Size | SHA-256 |
|---|---:|---|
| binary RPM | 37,131,042 | `3d1502c7995fbd4b496796dc66777242c373240209902290899f38160efe2cb9` |
| source RPM | 259,026,310 | `03076822c95a0ec0315d0d400ac0163e86c1ef46eb783bc72621e99f0fed7c03` |
| `repomd.xml` | 1,568 | `ccbf55008c7103e128d5cb1fa729605fae4e68248ebf6bfaffc35537dc9e037b` |
| primary metadata | 1,064,408 | `06feeadee2e39774e5d9b5ca2ada653a3caff2a7b0cf708b0ad9783c4dd5bcd1` |

The COPR public key is pinned as SHA-256
`dc65f34bb568e624a6d5dce1e81eabe11072d4c74e27c7b6b46be9cc061185fc`.
The offline verifier parses the RPM lead and signature/main headers with
strict bounds, extracts only tag 268, and asks pinned Sequoia `sqv` 1.5.0 to
verify the exact signed main-header bytes. Both RPMs pass with fingerprint
`CB2323A20EBAFB6D02D625B76AAF40A74B7FF931`:

| RPM | Signature SHA-256 | Signed-header SHA-256 |
|---|---|---|
| binary | `4db5a6b55d33b9d19001b6dbd2e46ed2bef2c6cf8b991432fa90233ffb713aae` | `e99e1238807db07120066623c040c82e3869b4ce882d19d60e59524dbe6d3634` |
| source | `ae63b4763bfd312570edc9af939676358673a917dc19ed6c3603fbdf844f34a6` | `126239626fada755ea71f0355a1ea8cb59999c40f5b3ee75588b432b61a13fac` |

This is archive verification, not an inference from the installed RPM
database.

## Exact source snapshot

The SRPM member `linux-7.1-rc2.tar.gz` is 262,099,891 bytes with SHA-256
`715b639c67d31b7abd4a3ccc02ad644e780ef369d4b4a27101698211272866bf`.
The independent GitLab archive for commit
`a4975ce7c8eec079bd0cf7ec3890e100e016813f` is 262,256,950 bytes with SHA-256
`969c26e7c1778aebc262690317687c60bd81aff4cbf572b0d259df369f0b727a`.

`source-evidence.py` strips only each archive's single top directory, rejects
unsafe paths, escaping symlinks, duplicates and unsupported entry types, then
hashes file contents, sizes, modes, directories and symlink targets. Both
trees have exactly 99,850 entries and the same normalized tree SHA-256:
`9b2742727cc804f9d3f2727025155f074ea807a48f97ef35aaa2b2c082241404`.
The SRPM source snapshot therefore matches that exact commit, rather than only
the mutable branch name written in the spec file.

## Published package versus running boot container

The binary RPM contains:

| Payload | Size | SHA-256 |
|---|---:|---|
| raw module-tree `Image` | 34,515,456 | `b77e3cecf7c49689017965b5b19829d8224144179fdd4c6c41d79c04e3239ba1` |
| config | 234,416 | `5414bbb75c971adfc7410ca259994ce8a3c7013530105ea79a04500c7f750755` |
| Android bootimg v0 | 17,090,560 | `cf910fef42e06aaae7f983d3e29ec920705e311e768721428b481a033670d55b` |
| package Pocket DS DTB | 135,527 | `0557cee113d4e34fcae274c69400a8a53653e60229a1f333eafde83b2dfe6994` |

The running raw `Image` and both config copies match the signed RPM. The three
runtime boot-image paths are byte-identical Android bootimg v0 files with
SHA-256 `e4e0a38ab2f247dc67b6b4521f2fcc57123528ea50edda46ea08d305d9336c94`.
Their gzip stream expands exactly to the signed raw `Image`; their appended DTB
is 135,820 bytes with SHA-256
`195345de379195df466b999cf7c7ba53764dfd9c0bb12b18214083e530979556`,
and is byte-identical to the versioned `/boot/dtb-*` mirror.

The runtime boot container is therefore **not** the unmodified RPM boot
payload. It is the signed RPM kernel plus the local native-lid DTB. The source
delta is preserved as
`tools/kernel-ab/patches/0001-arm64-dts-qcom-pocketds-native-lid.patch`.
Applying it to the exact SRPM tree, preprocessing the Pocket DS DTS, and using
the DTC 1.7.2 source shipped in that same tree reproduces the runtime DTB
exactly: 135,820 bytes and SHA-256 `195345de...`. The semantic delta adds only
the GPIO 166 pin state and `EV_SW/SW_LID` gpio-key with 50 ms debounce,
`wakeup-source`, and deassert-only wake; the other textual DTB differences are
the consequent phandle renumbering.

`evidence.py` v3 now fails if any of the three boot aliases differs, if the
gzip stream is incomplete or differs from the raw `Image`, if the appended FDT
is malformed/out of bounds, or if it differs from the versioned DTB mirror.

## Subsequent reproduction result and remaining release blockers

The follow-up isolated Fedora 44/aarch64 build now proves the source-to-binary
step.  With historical GCC 16.1.1 and locked metadata normalization, all 496
RPM-declared payload entries from the independently rebuilt RPM match the signed
reference. Extraction supplies three implicit parent directories, yielding 499
scanned entries: 341 regular files, 156 directories and two symlinks. This
includes raw Image, Android bootimg, System.map, all 323 modules, config and
both DTBs. The full
evidence and rejected intermediate experiments are recorded in
`2026-08-28-pds002-reproducible-kernel-build.md`.

The unsigned rebuilt RPM container is not byte-identical to the signed RPM;
the verifier reports that separately instead of conflating header/signature
bytes with the installable payload.  No separate rollback boot artifact has
yet been installed and cold-boot validated. Consequently, the combined
evidence says:

- `source_to_binary_reproducible_build_proven=true`;
- `runtime_source_commit_cryptographically_bound=false`;
- `prevalidated_rollback_artifact=false`;
- no IFPC/dmabuf A/B kernel may be installed yet.

The next safe PDS-002 step is to build the IFPC single-variable candidate in
the same isolated root and create an independently validated rollback
artifact. Only after the rollback gate passes should the two single-variable
GPU candidates enter a supervised boot/display matrix.

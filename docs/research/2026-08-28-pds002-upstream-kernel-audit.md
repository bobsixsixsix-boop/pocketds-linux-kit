# PDS-002 upstream/kernel audit — 2026-08-28

This audit is read-only. It records candidate variables; it does not claim a
root cause and does not authorize a kernel install.

## Reproducible stack

- Kernel: `7.1.0-100.20260730172655.pocketds.fc44.aarch64`, high-confidence
  match to linux-pocketds `a4975ce7` (v7.1-rc2 plus 46 downstream commits).
- Installed package: `kernel-0:7.1.0-100.20260730172655.pocketds.fc44.aarch64`;
  its recorded source RPM is
  `kernel-7.1.0-100.20260730172655.pocketds.fc44.src.rpm`. The running image is
  `/usr/lib/modules/<release>/Image`, 34,515,456 bytes, SHA-256
  `b77e3cecf7c49689017965b5b19829d8224144179fdd4c6c41d79c04e3239ba1`.
- Mesa: Fedora 26.1.8 without downstream patches.
- KWin: Fedora 6.7.4 with its packaging patch set.
- libdrm: 2.4.134; GMU firmware reports v4.1.9.
- `CONFIG_DETECT_HUNG_TASK` is disabled, so the absence of hung-task stacks is
  not evidence that fence waits do not occur.

The module-tree and `/boot` config copies are byte-identical (234,416 bytes),
SHA-256 `5414bbb75c971adfc7410ca259994ce8a3c7013530105ea79a04500c7f750755`.
This closes the planner's config measurement, not the source-to-binary binding.

## Source and firmware closure

The official GitLab compare API places both candidate commits in the direct
history leading to the pinned baseline: A740 IFPC `d84f65c` and GEM/dmabuf
teardown `9f8fbaa`. The pinned A740 catalog declares SQE
`a740_sqe.fw`, GMU `gmu_gen70200.bin`, and optional ZAP
`a740_zap.mdt`:

- <https://gitlab.com/linux-pocketds/linux/-/raw/a4975ce7c8eec079bd0cf7ec3890e100e016813f/drivers/gpu/drm/msm/adreno/a6xx_catalog.c>
- <https://gitlab.com/linux-pocketds/linux/-/raw/a4975ce7c8eec079bd0cf7ec3890e100e016813f/drivers/gpu/drm/msm/adreno/adreno_gpu.c>
- <https://gitlab.com/api/v4/projects/linux-pocketds%2Flinux/repository/compare?from=d84f65cc8cd7af0bf62cca52d13583394851cfe8&to=a4975ce7c8eec079bd0cf7ec3890e100e016813f&straight=true>
- <https://gitlab.com/api/v4/projects/linux-pocketds%2Flinux/repository/compare?from=9f8fbaaeb9e8d6c11bca695de77fd6ee2778fa02&to=a4975ce7c8eec079bd0cf7ec3890e100e016813f&straight=true>

The current boot log confirms new-location loads for the SQE and GMU files.
The live device tree has no available `zap-shader` node and no firmware-name
override; the pinned loader returns before requesting ZAP in that case. The
authoritative runtime GPU firmware set is therefore exactly:

| Relative path | Size | SHA-256 |
|---|---:|---|
| `qcom/a740_sqe.fw` | 76,724 | `96fee336424b139100fc60b5b45a907360e4b3936d7e1d00406b9bd80ca48473` |
| `qcom/gmu_gen70200.bin` | 66,844 | `1a2a419c39046d3141fc5fed5aa7f971de2db40cc7a1d89693c3e26fad64dd98` |

The canonical `pocketds.gpu-firmware-set.v1` identity SHA-256 is
`e2278e91d724c8cae27865544f91d200bd3cd5e1d70331fdb9bc2d2ec7abd829`.
`tools/kernel-ab/evidence.py` reproduces these hashes with no privilege,
network, subprocess, output file, or mutable action. It also verifies the exact
device-tree compatibility, running release, both config copies, regular-file
permissions and no symlink substitution. Eleven fault-injection tests and the
full local/device test suites pass.

The follow-up package/source/boot audit independently verified both RPM
signatures, proved the SRPM source tree equals exact commit `a4975ce7`, parsed
the actual Android bootimg v0, and reproduced the local native-lid DTB from a
preserved source patch. See
`2026-08-28-pds002-package-source-boot-chain.md`. A subsequent isolated
Fedora 44/aarch64 build with the historical GCC 16.1.1 toolchain reproduced all
496 RPM-declared payload entries (499 scanned extraction entries after three
implicit parents); see
`2026-08-28-pds002-reproducible-kernel-build.md`. The evidence still leaves
`production_plan_ready=false` because no separately validated rollback boot
artifact exists.

## Highest-value single-variable candidates

1. linux-pocketds `d84f65c` forces `ADRENO_QUIRK_IFPC` on A740 and reuses the
   A750/X185 IFPC register list. Upstream v7.1 and current mainline do not enable
   IFPC for A740. First A/B: revert only this commit while keeping firmware,
   Mesa, KWin, display modes and workload fixed.
2. linux-pocketds `9f8fbaa` adds a `MAX_SCHEDULE_TIMEOUT` fence wait to
   `msm_gem_dmabuf_release()`. Upstream does not contain this extra teardown
   wait. Second A/B: revert the complete downstream commit; do not invent a
   partial finite-timeout variant.
3. Linux v7.1 final includes stable-marked msm shrinker deadlock fix
   `3392291fc509`; the rc2-based image likely lacks it. Add it symmetrically to
   both sides of any comparison or keep it out of both.

KWin atomic `EBUSY` appears both before and after GMU failures. Treat a lone
`EBUSY` as DRM nonblocking-commit pressure, not proof of an invalid mode. Only a
sustained burst correlated with frame loss or recovery is a failure signal.

## Minimum A/B and rollback gates

- Capture 45–60 minutes with boot ID, exact revision and display signature.
- Test 165/60 first; 60/60 and single-upper-screen are later independent
  display variables.
- Require zero new GMU OOB/HFI/fenced-register-delay/lockup/recover/offender
  events and no user process in a dma-fence D-state.
- Measure idle power and temperature because disabling IFPC can cost battery.
- Roll back immediately on black screen, GPU probe failure, new SMMU faults,
  display-signature drift, unsafe thermals, or simultaneous loss of local and
  SSH recovery paths.
- A promising run is not release evidence: repeat at least three matched runs.

Only after the two kernel variables are isolated should the KWin page-flip
lifecycle stack (`c37f407`, `d3bf6bc`, `e61f57a`) be considered as a unit.

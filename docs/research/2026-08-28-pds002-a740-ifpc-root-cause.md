# PDS-002 A740 IFPC root-cause hypothesis — 2026-08-28

Status: **high-confidence working diagnosis, not yet A/B confirmed**. This
document and the candidate source patch do not authorize a build, install,
reboot or desktop restart.

## What fails first

A current-boot journal snapshot contained 99 GMU OOB timeouts, seven GPU
lockups, 15 recovery records, seven offender records, four fenced-register
delays, two HFI errors, ten SMMU faults, 34 KWin atomic `EBUSY` records and
seven KWin graphics-reset notices. The offender was KWin in every lockup, but
that identifies the submitting context rather than proving that KWin initiated
the hardware failure.

All seven lockups share the same order:

1. six to thirteen `GPU_SET` GMU OOB timeouts occur in the preceding ten-second
   window;
2. the first of those failures is roughly three to five seconds before the
   lockup;
3. hangcheck declares a GPU lockup;
4. kernel recovery starts one to four milliseconds later;
5. KWin reports its attributable graphics reset 43 to 491 milliseconds later.

| Lockup uptime (s) | Pre-lockup `GPU_SET` OOB range (s) | Recover after (s) |
|---:|---:|---:|
| 5,951.776 | -3.462 to -3.396 | +0.004 |
| 8,757.760 | -3.620 to -3.568 | within milliseconds |
| 14,635.772 | -3.879 to -3.752 | within milliseconds |
| 25,248.800 | -3.708 to -2.668 | within milliseconds |
| 52,726.783 | -4.307 to -4.103 | within milliseconds |
| 52,772.800 | -4.931 to -1.184 | within milliseconds |
| 53,065.791 | -5.027 to -3.115 | within milliseconds |

This makes the KWin reset a downstream observation. Atomic `EBUSY` is mostly
independent: only one event is close to the first lockup. It remains a separate
display-path problem and must stay a measured non-regression metric.

The ten SMMU faults form one boot-initialization burst at uptime
3.584–3.587 seconds, about 5,948 seconds before the first lockup. The two HFI
errors form a burst at 17,605.628 seconds, about 2,969 seconds from the nearest
lockup. Neither series is temporally coupled to these seven runtime failures.
The kernel has `CONFIG_DETECT_HUNG_TASK` disabled, so absence of hung-task
records is not affirmative fence-health evidence.

## The source delta that matches the failure mode

The exact Pocket DS source baseline is
`a4975ce7c8eec079bd0cf7ec3890e100e016813f`. Downstream commit
`d84f65cc8cd7af0bf62cca52d13583394851cfe8` makes exactly two semantic changes
to A740 (`0x43050a01`):

- adds `ADRENO_QUIRK_IFPC`;
- assigns `a750_ifpc_reglist`.

The commit describes the A740 as equivalent to an overclocked A741. In the
official DRM/MSM `msm-next` snapshot
`140b13475302601368c0cf4e193e66126a49feb3` (2026-07-31), the same A740 entry
has cached-coherent, hardware-APRIV and preemption quirks only. It has neither
IFPC nor an IFPC register list. The shared list is still annotated
`Applicable for X185, A750`:

- <https://gitlab.freedesktop.org/drm/msm.git>
- <https://gitlab.com/linux-pocketds/linux/-/commit/d84f65cc8cd7af0bf62cca52d13583394851cfe8>

The runtime sequence is consistent with an invalid or unreliable
power-collapse/wake path: repeated `GPU_SET` OOB handshakes fail before the GPU
stops making progress. The official A740 catalog omission independently points
at the same downstream IFPC change. Current official linux-firmware copies of
`a740_sqe.fw` and `gmu_gen70200.bin` are byte-identical to the running files, so
firmware replacement is not part of this first experiment.

This is strong triangulation, not proof. The only acceptable proof is a matched
single-variable A/B.

## Isolated candidate and acceptance boundary

`tools/kernel-ab/patches/0002-revert-a740-ifpc.patch` reverses only the four
textual lines introduced by `d84f65c`. It dry-runs and applies cleanly to the
exact `a4975ce7` archive. It does not alter firmware, Mesa, KWin, display mode,
native-lid DTB, workload or any other kernel commit.

The candidate remains blocked from materialization until all of these exist:

- a complete independent kernel rebuild path;
- an Android bootimg-v0 rollback image hashed and bound to this device/release;
- a hashed validation report recording successful boots and at least one
  independently tested recovery operation;
- exact control/candidate fixed-variable identities;
- three matched runs of at least 45 minutes each.

Acceptance requires zero new GMU OOB, HFI, fenced-register-delay, lockup,
recover and offender events, no critical dma-fence D-state, unchanged display
signature/firmware/Mesa/KWin/workload, and no regression in atomic `EBUSY`,
pressure, power, temperature or observer overhead. Black screen, GPU probe
failure, new SMMU faults, unsafe thermals, display drift, or simultaneous loss
of local and SSH recovery paths means immediate rollback.

## 2026-08-28 23:32 JST revalidation

Both source comparisons were refreshed from their authoritative remotes before
rechecking the hypothesis:

- DRM/MSM `msm-next` remains at `140b13475302601368c0cf4e193e66126a49feb3`.
  Its A740 entry has preemption but neither `ADRENO_QUIRK_IFPC` nor an IFPC
  register list.
- linux-pocketds `pocketds/v7.1-rc2` remains at
  `a4975ce7c8eec079bd0cf7ec3890e100e016813f`. Its A740 entry still adds IFPC
  and `a750_ifpc_reglist` through
  [`d84f65c`](https://gitlab.com/linux-pocketds/linux/-/commit/d84f65cc8cd7af0bf62cca52d13583394851cfe8).

A new stdout-only read of the current boot journal counted 118 GMU OOB
timeouts, 9 GPU lockups, 9 GPU offender records, 18 recover messages, 4 fenced
register delays, 2 HFI errors, 10 SMMU faults and 35 KWin atomic `EBUSY`
records. The bounded observation window itself added no event and kept all
fixed/runtime controls unchanged. These counts do not by themselves prove
causality, but they confirm that the failure is active and that the exact
downstream/upstream divergence still exists. The prepared IFPC-only candidate
therefore remains the first A/B variable; it does not bypass the real fastboot
recovery gate or authorize installation.

The dmabuf-release wait candidate remains separate and lower priority for these
live lockups. Later GMU sysprof/OOB accessor refinements must also remain out of
the first A/B: the baseline already contains the earlier corrected OOB usage,
and the observed pre-lockup failures are `GPU_SET`, not the perf-counter path.

## 2026-08-29 upstream delta

A content-locked comparison through Linus HEAD `c20313e98b04` found the A740
catalog entry, the implicated GMU OOB functions and the complete preemption
source unchanged from pinned DRM/MSM `msm-next`; current official A740 firmware
also remains byte-identical to the already recorded runtime files. See
[`2026-08-29-pds002-upstream-delta.md`](2026-08-29-pds002-upstream-delta.md).
This preserves IFPC-only as the first single-variable A/B; it is still not
root-cause proof and does not authorize installation.

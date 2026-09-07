# PDS-002 upstream delta check — 2026-08-29

Status: **read-only revalidation; no fix claimed**. This check does not
authorize a kernel build, install, boot-image write, reboot, desktop restart or
other device mutation.

## Scope and pinned heads

At 2026-08-29 04:39 JST, the moving upstream refs resolved to the following
immutable commits:

| Source ref | Pinned commit | Commit time |
|---|---|---|
| DRM/MSM `msm-next` | [`140b13475302601368c0cf4e193e66126a49feb3`](https://gitlab.freedesktop.org/drm/msm/-/commit/140b13475302601368c0cf4e193e66126a49feb3) | 2026-07-31 17:08:01 +03:00 |
| Linus `master`/HEAD | [`c20313e98b04ce543936431b6122dd639d3a8346`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/commit/?id=c20313e98b04ce543936431b6122dd639d3a8346) | 2026-08-28 17:01:02 UTC |
| linux-firmware `main` | [`b6bdea3726dc9b477eea2d7db7ceba54133964a2`](https://gitlab.com/kernel-firmware/linux-firmware/-/commit/b6bdea3726dc9b477eea2d7db7ceba54133964a2) | 2026-08-28 13:39:05 UTC |

The kernel comparison is deliberately scoped to the A740 catalog entry, the
two GMU OOB handshake functions implicated by the journal, and the complete
preemption source file. The surrounding catalog and GMU files contain other
GPU work, so whole-file inequality would be a false positive for this question.

## Content-locked kernel comparison

The relevant content is byte-identical between the pinned `msm-next` tree and
Linus HEAD:

| Comparison unit | Extraction rule | SHA-256 in both trees |
|---|---|---|
| A740 catalog entry | From the first byte of the `.chip_ids = ADRENO_CHIP_IDS(0x43050a01)` line up to, but not including, the next `\t}, {\n` entry separator | `697b967820a592ec72d5b418158415b66ebf081c41c8147d0730f62fc91ee32b` |
| `a6xx_gmu_set_oob()` + `a6xx_gmu_clear_oob()` | Each function from the first byte of its declaration through its brace-balanced final `}`, then source-order concatenation with one `\n` byte | `27dbffef448ec4298bb5f85ecd332ddb2bb1f6bd20df4802f59a57e23d048417` |
| complete `a6xx_preempt.c` | Raw file bytes | `3975a996c49fc756cc58a7cbf546914a78632638241e39a752214ab0ec64941f` |

The separately reproducible function digests are
`c4b612ec58683342351741a3cb0196a8954eae9a35421ca527c810fadfb359f5`
for `a6xx_gmu_set_oob()` and
`3da646d6aed3ed59ece13e7051d85fa5fea9b23d6cef53987f14fcff8a97e5e3`
for `a6xx_gmu_clear_oob()` in both trees.

The A740 entry still declares only:

- `ADRENO_QUIRK_HAS_CACHED_COHERENT`;
- `ADRENO_QUIRK_HAS_HW_APRIV`;
- `ADRENO_QUIRK_PREEMPTION`.

It still has neither `ADRENO_QUIRK_IFPC` nor `.ifpc_reglist`. The shared
`a750_ifpc_reglist` remains annotated `Applicable for X185, A750`. Official
pinned source files:

- DRM/MSM [`a6xx_catalog.c`](https://gitlab.freedesktop.org/drm/msm/-/blob/140b13475302601368c0cf4e193e66126a49feb3/drivers/gpu/drm/msm/adreno/a6xx_catalog.c), [`a6xx_gmu.c`](https://gitlab.freedesktop.org/drm/msm/-/blob/140b13475302601368c0cf4e193e66126a49feb3/drivers/gpu/drm/msm/adreno/a6xx_gmu.c), and [`a6xx_preempt.c`](https://gitlab.freedesktop.org/drm/msm/-/blob/140b13475302601368c0cf4e193e66126a49feb3/drivers/gpu/drm/msm/adreno/a6xx_preempt.c);
- Linus HEAD [`a6xx_catalog.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/drivers/gpu/drm/msm/adreno/a6xx_catalog.c?id=c20313e98b04ce543936431b6122dd639d3a8346), [`a6xx_gmu.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/drivers/gpu/drm/msm/adreno/a6xx_gmu.c?id=c20313e98b04ce543936431b6122dd639d3a8346), and [`a6xx_preempt.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/drivers/gpu/drm/msm/adreno/a6xx_preempt.c?id=c20313e98b04ce543936431b6122dd639d3a8346).

Thus no targeted upstream code delta between these pins supersedes the
existing A740 IFPC hypothesis. This is an absence-of-relevant-delta result,
not causal proof.

The measured downstream divergence is therefore unchanged: Pocket DS source
baseline [`a4975ce7c8eec079bd0cf7ec3890e100e016813f`](https://gitlab.com/linux-pocketds/linux/-/tree/a4975ce7c8eec079bd0cf7ec3890e100e016813f)
contains [`d84f65cc8cd7af0bf62cca52d13583394851cfe8`](https://gitlab.com/linux-pocketds/linux/-/commit/d84f65cc8cd7af0bf62cca52d13583394851cfe8),
which adds A740 IFPC and assigns the A750/X185 register list. The prepared
candidate reverses only that downstream change.

## Firmware status

At linux-firmware `main` commit `b6bdea3`, both runtime A740 files still trace
to the same last path-changing commit,
[`30979b116b5c5857b72c4332db8db0ff1ca2dc08`](https://gitlab.com/kernel-firmware/linux-firmware/-/commit/30979b116b5c5857b72c4332db8db0ff1ca2dc08)
(`qcom: Add firmwares for sm8550 GPU`, 2025-12-11):

| Official file at pinned `main` | Size | SHA-256 |
|---|---:|---|
| [`qcom/a740_sqe.fw`](https://gitlab.com/kernel-firmware/linux-firmware/-/blob/b6bdea3726dc9b477eea2d7db7ceba54133964a2/qcom/a740_sqe.fw) | 76,724 | `96fee336424b139100fc60b5b45a907360e4b3936d7e1d00406b9bd80ca48473` |
| [`qcom/gmu_gen70200.bin`](https://gitlab.com/kernel-firmware/linux-firmware/-/blob/b6bdea3726dc9b477eea2d7db7ceba54133964a2/qcom/gmu_gen70200.bin) | 66,844 | `1a2a419c39046d3141fc5fed5aa7f971de2db40cc7a1d89693c3e26fad64dd98` |

These hashes remain identical to the running-file hashes already captured in
the PDS-002 audit. This revalidation did not reread or modify the device.
Firmware replacement therefore remains outside the first experiment.

## Mailing-list delta

A bounded `linux-arm-msm` review after the `msm-next` pin found one directly
IFPC-related proposal: [enable IFPC for A830/Pakala](https://lore.kernel.org/linux-arm-msm/20260809-pakala-gpu-v1-1-207ad1fac1c7@oss.qualcomm.com/).
It is for A830, not the A740/SM8550 catalog entry, so it is not evidence that
A740 should acquire IFPC or the A750/X185 register list.

Two nearby items were reviewed and screened out as fixes for this experiment:

- the [Adreno timestamp proposal](https://lore.kernel.org/linux-arm-msm/20260828-b4-adreno-timestamp-v1-1-273525da144c@oss.qualcomm.com/)
  leaves the GMU portions alone, and the targeted OOB functions above are
  byte-identical across the pinned trees;
- the [A8xx/Glymur recovery report](https://lore.kernel.org/linux-arm-msm/20260820154428.1278@ferrisoft.com/)
  concerns another GPU generation and is a recovery report, not an A740 patch
  present in either compared tree.

This was a bounded source and mailing-list check, not proof that no future or
out-of-tree A740 change exists.

## Decision

Maintain the prepared **IFPC-only single-variable A/B** as the first PDS-002
experiment. Keep firmware, Mesa, KWin, display mode, workload and all other
kernel changes fixed. The unchanged upstream blocks strengthen the rationale
for that experiment, but **still do not prove root cause and do not mean the
GPU issue is fixed**. The candidate remains uninstalled pending the existing
user-present recovery and matched 18-report A/B gates.

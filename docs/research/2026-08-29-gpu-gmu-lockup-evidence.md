# Pocket DS Adreno/GMU lockup evidence — 2026-08-29

## Scope

This is a read-only snapshot of the current boot journal. It does not change a
performance profile, display mode, KWin, firmware, kernel, service, or power
state. The purpose is to distinguish real kernel GPU recovery from a generic
"desktop froze" report before selecting an experiment.

The running release is
`7.1.0-100.20260730172655.pocketds.fc44.aarch64`. The inspected boot began at
2026-08-27 21:28 JST and was still active when the snapshot was taken.

## Counts in the current boot

| Kernel/user-space signal | Count |
|---|---:|
| `Timeout waiting for GMU OOB ... GPU_SET` | 118 |
| `hangcheck detected gpu lockup` | 9 |
| `hangcheck recover` | 18 |
| `preemption timed out` | 9 |
| `delay in fenced register write` | 5 |
| kernel `offending task` records | 9 |
| KWin `atomic commit failed: Device or resource busy` | 82 |
| recorded `plasmashell` SIGABRT coredumps | 4 |

All nine kernel `offending task` records identify `kwin_wayland`. Representative
sequences are:

```text
platform 3d6a000.gmu: Timeout waiting for GMU OOB set GPU_SET
msm_dpu ... hangcheck detected gpu lockup rb 0
msm_dpu ... hangcheck recover!
msm_dpu ... offending task: kwin_wayland
```

The first complete sequence appeared at 2026-08-27 23:07 JST. Further lockups
were present at 23:54, 2026-08-28 01:32, 04:29, 12:07, and 12:08, among others.
Several recovery-only/preemption sequences occurred between full lockup
records. Four plasmashell SIGABRT coredumps were retained between 2026-08-27
21:37 and 2026-08-28 02:32.

## Interpretation boundary

This proves that at least part of the observed desktop failure is a real
Adreno/GMU kernel recovery problem. It is not explained solely by a QML crash,
an unresponsive panel, or KWin presentation logic. KWin is the submitting task
reported at recovery time, but that alone does not prove KWin caused the GMU
failure.

The boot also contains early `arm-smmu` faults for SID `0x1c00`. They are not
counted as GPU faults here because the journal snapshot does not yet bind that
SID/context bank to the Adreno device. They require a separate mapping proof.

The 82 atomic-commit failures and four plasmashell aborts may be consequences,
triggers, or independent bugs. They must be correlated by timestamp and
workload rather than folded into the kernel count.

## Performance-profile correlation

A second read-only snapshot at 2026-08-29 13:31:49 JST found a previously
unrecorded control transition at the end of the failure burst:

- the final OOB burst began at monotonic `57133.096`;
- the final explicit lockup/recovery/offender sequence completed at
  `57136.773`;
- the Panel requested `power performance` at `57137.027`, 255 milliseconds
  later, and TuneD returned at `57138.417`;
- the live profile remained `pocketds-performance`, the GPU governor remained
  `performance`, and `cur_freq` read 1,000,000,000 Hz at the snapshot;
- the following 87,036.532 seconds (24.177 hours) contained zero new OOB,
  hangcheck, recovery, preemption-timeout, or offender records.

One later `delay in fenced register write` at 2026-08-29 10:45:54 JST means the
performance-profile interval was not symptom-free. The profile also fixes the
GPU at its maximum exposed frequency, so it is not an acceptable daily fix for
battery, temperature, or fan behaviour.

This is a strong temporal correlation, not a randomized A/B and not causal
proof. The profile change followed a recovery and may have been an operator
response. It nevertheless supports the existing A740 IFPC hypothesis: keeping
the GPU in the performance regime may avoid the idle/power transition that the
downstream-only IFPC quirk enables. The IFPC-only kernel comparison remains the
required causal test; the quiet interval must not be used to declare the
running kernel stable.

Pinned DRM/MSM source also sets a strict interpretation boundary. The devfreq
performance governor selects the active frequency; it does not clear
`ADRENO_QUIRK_IFPC` or change `gmu->idle_level`. The only reviewed runtime path
that explicitly blocks IFPC is the separate sysprof/perfcounter OOB vote. TuneD
does not activate that path. Therefore `pocketds-performance` is not an IFPC
disable switch, and the quiet tail may reflect workload timing or chance rather
than suppression of the faulty state. This makes the kernel A/B more important,
not less.

## Next controlled work

1. Preserve this boot's bounded GPU-event extract and the exact firmware,
   KWin, Mesa, tuned/fan, display topology, refresh-rate, and kernel identities.
2. Use the existing PDS-002 collector to establish matched 165/60, 60/60, and
   upper-only baselines with one frozen local workload.
3. Audit the Pocket kernel against upstream A7xx GMU/OOB, preemption, IFPC, and
   recovery fixes. Keep one source variable per candidate.
4. Do not install the existing IFPC or any new GPU candidate until independent
   baseline RAM recovery is physically validated. The current rollback lock
   still records zero successful recovery tests.
5. Treat "restart desktop" as damage recovery only; it cannot be the final fix
   for a kernel-reported GMU lockup.

The current evidence is sufficient to prioritize a kernel/firmware A/B, but
not to claim a root cause or authorize a kernel installation.

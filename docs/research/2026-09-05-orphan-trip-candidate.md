# Pocket DS orphan-trip candidate — 2026-09-05

Subsequent state: the user later accepted a reversible installation. Its
independent recovery exercise and final normal internal boot are recorded in
`2026-09-05-installed-kernel-trial.md`. Automatic suspend remains disabled.
The RAM-only statements below describe the original experiment, not that
later installed state.

## Scope

Following the read-only TSENS investigation, the user explicitly asked to fix
the five-second wake. This experiment removes only the 28 unbound passive
fan trips in `cpuss0-thermal` through `cpuss3-thermal`, and only for Pocket DS.
The previous wake's exact sensor/edge was not captured; this tests the leading
configuration hypothesis, not an already proven historical attribution.

`tools/kernel-ab/patches/0003-pocketds-unbound-cpuss-fan-trips.patch` adds
board-local `/delete-node/` overrides. It does not modify the common include,
TSENS driver, IRQ wake capability, individual CPU/GPU cooling maps or any
surviving trip. The standalone PWM fan controller is unchanged. The retained
125 C / 115 C aggregate passive nodes are not a substitute for the actual
Linux critical trips; all 22 compiled critical trips are also retained.

This is a temporary diagnostic RAM boot. The original 20260717 boot image
and its module tree are not replaced, and automatic suspend is not enabled.
Android, partition tables, SD library, firmware and user data are out of scope.

## Artifact and delta checks

Only `qcom/qcs8550-ayaneo-pocketds.dtb` was rebuilt in the retained 7.1.12
diagnostic source tree, using CPUs 0–2 and three build jobs. No full kernel or
module rebuild was needed. The candidate uses the exact v2 ST7703-enabled
kernel gzip stream, all prior boot arguments and the same diagnostic modules.

| Artifact | SHA-256 |
| --- | --- |
| Original on-disk boot image | `85b41b68738090e5e04aa9672eecf8b08d9ef189d7c9207d5ec99ef8c7252673` |
| v2 input boot image | `79f008b6aae1cfbe0c984ee987d1de4feafbe3c4f607fcc4f596f02b3e800bcb` |
| Preserved raw kernel Image | `bde22bbb420b27a71c095d367bbb32ada42e7dbaae1d1fcd6c5b761877ae1079` |
| Preserved kernel gzip stream | `48c5e1c6e524ed0d10243171c0ffbdf9f3502c5f63f0e55a81abe035c713ee2c` |
| v3 candidate DTB | `bd2a211e5e00c88e8be9ff476acb02292fd33c09e98a85edc3638ad456315e9d` |
| v3 candidate boot image, 19,077,120 bytes | `a136aeb060d38f9a305336af36577c5971e7c7e81e78839a608a60d30ff24c71` |

`tools/kernel-ab/verify-orphan-trip-dtb.py` parses both compiled trees and
requires the delta to be exactly those 28 nodes, with their exact old values
and no phandle. Every surviving node/property and the FDT reservation table
must match. It locks the v2 image/kernel hashes, preserves its gzip and header
except payload length/checksum, and creates a new image exclusively. It cannot
install, reboot or suspend. Optimized Python (`-O`) is explicitly rejected
because the experimental checks use assertions. Five synthetic tests in
`tests/orphan-trip-dtb.py` passed, including protection-change, missing-node,
referenced-trip and incomplete-removal rejection. Independent `unpack_bootimg`
inspection also verified container addresses, root arguments and empty ramdisk.

## Temporary boot and device-callback test

Verified ROCKNIX-ABL, serial `678cef50`, slot a, unlocked, non-userspace
fastboot accepted the RAM image. Boot identity was
`7816e2dc-fb13-48fd-a08f-dd1ef766a37e`; release remained
`7.1.12-pdsdiag.20260905.aarch64`. A live DT dump matched every candidate thermal
zone and TSENS property, including all 22 critical trips. Both DSI outputs,
upper 120 Hz / lower 60 Hz layout, original desktop/Panel screenshots, ALSA,
physical/enhanced sinks, Wi-Fi and required services passed initial checks.

One `pm_test=devices` smoke completed: entry 109.953748, explicit five-second
debug wait at 111.123741, device resume complete 117.756559, exit 118.226319.
Success changed 0 → 1, fail stayed 0. The guard observed PrepareForSleep true
then false and cleared RTC; cleanup restored `pm_test=none`, debug 0 and the
original blocked sleep policy. No UFS/EXT4/block I/O errors were observed.

SSH was temporarily unreachable after this successful local return. The
operator stopped progression and asked about the displays rather than treating
the network outage as a proven kernel hang. It recovered without forced power
or service restart. NetworkManager's first post-resume connection attempt
failed during address configuration; the next attempt succeeded at monotonic
179.866095, roughly 62 seconds after resume, with the same DHCP address. This
network recovery delay is a separate observed acceptance issue, not evidence
of sixty seconds spent asleep. Both desktop services had zero restarts, and a
second screenshot confirmed the desktop/Panel before the real-deep attempt.

Private wrappers are derived from the preceding attended experiment, with new
evidence paths and unchanged lifecycle guard. Device-test wrapper SHA-256:
`77617176f282dc0fb61c60dc8920efbc86859e839d3f6a2fb6d26ae4b7bfb06d`.
Real-deep wrapper SHA-256:
`e6033c410e8a1eab588f858fd363e83218e29085d48521dd39ad611f15ebc631`.
No automatic series or repeat-until-success is configured.

## Single real-deep result

All 17 preflight gates passed, with external power connected, serial device
callbacks, `pm_test=none`, and the guard arming RTC with 61 seconds remaining.
One real cycle ran; it was not the harness's automated acceptance series.

- Suspend entry: 292.809935; noirq suspend complete: 294.155823.
- **Timekeeping suspended for 59.424 seconds**. IRQ 166 triggered wake and
  resolves to `pm8xxx_rtc_alarm`, `pmic_arb`, hardware IRQ 6431281. This is the
  intended rescue deadline, not the old TSENS0 upper/lower IRQ.
- Noirq, early and normal device resume completed; suspend exit: 296.939294.
  Kernel monotonic timestamps exclude sleep; wall-clock guard lifecycle was
  11:53:42 → 11:54:47 JST. The timer starts before device entry, explaining why
  actual platform sleep is slightly shorter than 60 seconds.
- Success increased 1 → 2, fail stayed 0 (one device-debug plus one real cycle,
  **not two real cycles**). Boot identity was unchanged. No UFS/EXT4/block I/O
  errors were observed, and root remained read-write.
- The guard received PrepareForSleep false before clearing RTC. Temporary
  policy overlay was removed, `pm_test=none`, debug 0, empty RTC,
  `AllowSuspend=no`, and safely blocked execution were verified afterwards.
- Wi-Fi returned without manual intervention about 16 seconds after the wake
  request (NetworkManager activated at 312.542799). Both displays, sound card,
  physical/enhanced sinks and required services were available. KWin, Plasma
  and PowerDevil had zero restarts and no failed units were present.
- A post-resume screenshot was personally inspected and showed the original
  upper desktop and lower Panel. The user subsequently answered “正常” when
  asked whether the upper screen, lower Panel and touch were all normal.
  Physical acceptance of this short cycle is therefore confirmed separately
  from the screenshot.

This is a successful **single short RTC-wake test of the candidate**. It
supports the orphan-trip explanation, but does not retroactively identify the
old event's exact sensor and is not proof that every early-wake cause is gone.

Evidence was copied privately off-device. Archive SHA-256:
`2fc3833089174734dbda30ae5f449dd9a64b2f4bc128032e50ef1fef0594ebda`.
After-deep dmesg:
`549a69a102678a9757791784384e3f7a81dfbc4e5674a46aa178016740ef9320`.
PM unit journal:
`a3d1de89f6c5cfd19f61f8fb451fdfdc23b2c436fb79dae47c7097f6dbaba3ce`.
The archive includes both distinct wrappers, both test directories, the live
DT dump and preflight/lifecycle evidence. Raw logs and screenshots are not Git
content.

On that confirmation follow-up, read-only live checks found the same v3 boot,
success/fail counters still 2/0, `pm_test=none`, debug 0, empty RTC and safely
blocked suspend. The original boot image hash was unchanged and root was rw,
with no observed UFS/EXT4/block errors. No further suspend, reboot or permanent
installation ran in that follow-up. A 300-second test was then proposed, not
inferred from that physical confirmation.

## Separately authorized 300-second follow-up

The user explicitly agreed to leave the device untouched for five minutes.
Before dispatch, the same v3 boot, unchanged original on-disk image, current
guard/helper hashes, empty RTC, 2/0 suspend counters, no storage errors, rw root,
both enabled backlights and all required services were checked again. Battery
reported 100% / Full; cpuss0–3 samples were 51.6–52.4 C. Ramoops remained
registered with its 2 MiB reservation; pstore had no files. The off-device
current-baseline recovery image and candidate image hashes matched the table.
A pre-test screenshot was inspected and showed the original desktop and Panel.

The new private wrapper, `attended-deep-300-v3.sh`, is SHA-256
`a1e853655a02cdb6cb10352bcf692ebdc950079e644f1b72c74da190888654ae`.
Compared with the 60-second wrapper it uses a fresh evidence directory, locks
the current boot identity, requires the prior device and real-cycle successes,
expects count 2/0, and adds before/after temperature and interrupt inventories.
It requests exactly one `guard run 300`; there is no loop. Kernel, drivers,
performance profile, trip configuration, guard and on-disk sleep policy bytes
are unchanged. The sleep-policy permission is a temporary memory-backed bind
mount and is removed by cleanup, as in the prior tests.

The system-owned unit `pds001-real-deep-300-v3` was dispatched at 12:06:15 JST.
No SSH polling or intentional input was sent during the five-minute window;
the already-open kernel-journal stream was retained privately on Mac.

Observed result:

- All 17 preflight gates passed; RTC readback was 301 seconds at arming.
- Suspend entry 987.080848; actual platform sleep **299.809 seconds**; wake
  source IRQ 166, verified again as `pm8xxx_rtc_alarm` / hardware 6431281.
  No TSENS early wake was observed. Suspend exit was 992.936998. Kernel
  monotonic timestamps exclude sleep, so their difference is not total time.
- Guard events span 12:06:16–12:11:23 JST. PrepareForSleep false preceded RTC
  cleanup. Unit exit/result were 0/success, and success/fail counters became
  3/0: one device-debug plus **two real cycles** (60 and 300 seconds), not
  three real cycles. The boot identity remained unchanged.
- No UFS, EXT4 or block I/O errors were observed. Root was still rw and the
  original on-disk boot image retained SHA-256 `85b41b68…`.
- Both backlights were on and exactly matched their pre-test raw values
  (1515 and 2529); the post-test screenshot showed the original desktop and
  Panel. KWin, Plasma and PowerDevil remained at zero restarts, required
  services were active and no failed units were present.
- Wi-Fi activated at 1008.446413, 16.116 seconds after NetworkManager's wake
  request at 992.330138. SM8550-APS, the physical speaker sink and the enhanced
  sink were present. Audible playback was not claimed from sink presence.
- The wrapper sampled cpuss0–3 at 54.0/53.2/53.6/53.2 C before requesting
  sleep and 50.4/50.0/49.2/49.2 C after resume. These are awake snapshots,
  not an in-sleep minimum-temperature trace or an exact first-IRQ diagnosis.
- Cleanup restored debug 0, `pm_test=none`, empty RTC, no temporary policy
  overlay and safely blocked automatic suspend. No repeat test or permanent
  installation was run. The user subsequently answered “正常” to the separate
  question about this five-minute cycle's physical displays, Panel and touch.
  Physical acceptance of both the one-minute and five-minute cycles is now
  confirmed. The confirmation follow-up only updated documentation; it did
  not initiate another test, enable automatic suspend or change the boot image.

Private evidence was copied off-device and hash-verified:

- Five-minute wrapper/run archive:
  `61b28456ad354eac5830e900c0d5561e1856eaa0e811f7558c27247e06f43b9e`.
- After-test dmesg:
  `2b4c03e38283fc8e4124a1f663e24370d92597eddc4eb3b4d9b8b85729f09af7`.
- PM unit journal:
  `c873bcbdd467d230a8fad5c0465801d30594bc307e166a8f18cf2f516e1cba02`.

Offline fixture checks on Mac passed 16 single-cycle and 9 series tests.
The combined `make test-deep-cycle` target could not start its guard tests on
Mac because PyGObject (`gi`) is absent. That environment failure is not a
hardware failure or a passing guard-test result; no dependency was installed
on either host to work around it during standby. After storage/service health
checks passed on resume, the same pure fixture targets ran on Fedora: 17 guard,
16 single-cycle and 9 series tests passed. They did not request another sleep.

## Limits

A short controlled result cannot establish everyday standby safety or resolve
the historical UFS resume fault. Pstore reservation remains best-effort;
external UART capture is not verified. A new storage error requires stopping
disk probes/writes and collecting volatile evidence before recovery. No global
TSENS wake disable, s2idle switch, UFS-PM change or protection-limit increase
is part of this candidate.

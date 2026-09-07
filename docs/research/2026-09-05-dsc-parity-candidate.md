# v4 DSC parity candidate — 2026-09-05

**Later Hall-wake acceptance failed:** the second cycle slept 128.367 s and
woke on IRQ202, but upper output stayed disabled. A single explicit enable
recovered it without DSC errors. This proves the first RTC/open success did
not close the full lid-resume issue. See
`2026-09-05-hall-wake-disabled-output.md`. v4 remains RAM-only and deep off.

## Scope and source defect

After the physical lid failure and user-authorized reboot recovery, the user
requested continued repair. Daily deep sleep stays disabled. The new candidate
is isolated from the installed v3 image and the original build tree.

The non-paired `_dpu_rm_dsc_alloc` stops at the first available DSC whose
parity differs from the assigned pingpong. A lower-first configuration can
leave PP0 on the uncompressed lower output and PP1 on the returning upper
output: free DSC0 causes `-ENAVAIL`, even though compatible DSC1 is available.
The old source's parity helper explicitly requires matching even/odd indices.
The paired allocator already skips incompatible pairs, but is not modified.

`0004-dpu-dsc-skip-incompatible-parity.patch` changes only this early failure
to `continue`: no assignment or pingpong advance occurs for a rejected DSC;
ownership checks, hardware parity restrictions and exhaustion failure remain.
The same early-return implementation appears in the upstream
[Linux v6.16 resource manager](https://raw.githubusercontent.com/torvalds/linux/v6.16/drivers/gpu/drm/msm/disp/dpu1/dpu_rm.c).
This is a locally prepared candidate, not a claimed upstream-accepted fix.

## Regression tests

`tests/dpu-dsc-parity.py` locks the exact v3 source hash, applies the actual
patch in a temporary directory, permits precisely the one branch change and
compiles both real extracted C allocators with bounded resource structures.
The independently written ordered-pairing oracle checks 262,144 combinations
of pingpong ownership, DSC presence/reservation and requested counts 1–4.
Mac and native Fedora ARM64 runs both passed:

- 6,580 false baseline rejections become successful allocations.
- Every baseline success remains successful with identical assignments.
- Reserved resources and pingpong ownership are unchanged.
- Lower-first PP1/DSC0-free regression fails baseline and passes candidate.
- Resource exhaustion and retaining an already owned compatible DSC are tested.

These prove allocator behavior, not physical resume acceptance.

## Build and packaging

The 3.6 GiB v3 build tree was copied into `linux-7.1.12-dsc-v4`, not hard-linked.
The original source remains untouched. The first build preflight declined to
start due to its temperature limit; after cooling, compilation succeeded on
CPUs 0–2 / three jobs, without governor or fan changes. Logged maximum during
that build was 63.6 C; automatic termination bound is 75 C / 30 minutes.

Build identity is **#4**, fixed UTS timestamp `Sat Sep 5 08:00:00 UTC 2026`.
The release string stays `7.1.12-pdsdiag.20260905.aarch64` to use the unchanged
module tree. Runtime identity must therefore use kernel notes, not uname alone.
317 external modules, built-in module metadata, all 15,216 freshly linked
kernel exports and the entire kernel configuration match baseline. The
initramfs archive has changed timestamps; every other header field, pathname
and payload compares equal. No module or firmware installation was performed.

The offline composer locks the existing v3 boot container and new raw Image;
it preserves the exact v3 DTB (including thermal/lid changes), all command-line
and address fields, and changes only kernel size/content, padding and image ID.
It validates its generated container again before creating a new output file.
The image, raw Image, notes and build log were copied to Mac and rehashed.

| Artifact | SHA-256 |
| --- | --- |
| Original dpu_rm.c | `aab48d26ed2a7413d7944e84068e07150a469073573f8a0fb4da628cf8ec2851` |
| Patched dpu_rm.c | `2a7a2215949451918ba39f9badf8a59afd1d27cd13dae3d5fd1c566a0192956a` |
| v4 raw Image | `37c5d65c381aa434cdfc43dbe8923ebe8525b6dd85818b41da5e003974a5b653` |
| v4 boot image, 18,993,152 bytes | `57322cb6dc3bce822289bcd6bde5e5934362efc2547120ebc88be0eea7a89d92` |
| v4 kernel notes | `05ffb67550d15176df5302978a51cf1834fa70869e663f8e01c440c253116092` |
| Preserved v3 DTB | `bd2a211e5e00c88e8be9ff476acb02292fd33c09e98a85edc3638ad456315e9d` |

The on-disk `/boot/boot/Image` remains v3 `a136aeb0…`; current baseline is not
the older `85b41b68…` image. A normal reboot returns to this installed v3.
Raw artifacts/evidence stay outside Git in private `pds001-lid-display-MEnar0`.

## Attended test boundary

The user explicitly confirmed USB connected and authorized temporary reboot
testing. At 17:01 JST the device entered bootloader normally. Exactly one
fastboot device reported ROCKNIX-ABL 1.1.8, unlocked=yes, slot a,
is-userspace=no. `fastboot boot` accepted the v4 image; no flash, slot change,
boot-partition write or automatic deep-sleep enablement was done.

Runtime/physical display and suspend acceptance are separate checks; boot
dispatch alone is not acceptance. See subsequent evidence before deployment.

## RAM boot verification and initial test preparation

Boot `72a9286e-7eae-46cf-966a-6afc7d2102be` reports build #4 and exact v4
kernel notes above. The installed `/boot/boot/Image` still hashes to v3.
Root and boot are read-write; no observed DSC or storage errors, no failed
system/user units. Both outputs are connected/enabled at original 120/60 Hz,
layout and priorities; a personally inspected screenshot shows the existing
desktop and lower Panel. Screenshot evidence does not verify physical touch.

The physical readiness question was sent to the user. At preparation time,
before physical lid closure, no suspend had been requested: counters were 0/0.
`attended-dsc-lid-test.py check` passed natively. It reuses existing strict
storage/thermal/wake checks with only the RAM notes identity substituted in
its private process, leaving production eligibility unmodified. A future `run`
is locked to this exact boot, zero prior cycles and a closed physical lid.
The one-use receipt prevents repetition. Temporary policy is a /run bind
mount with cleanup; the existing unchanged guard owns the single 120-second
RTC rescue and true/false lifecycle. No automatic KDE sleep action is enabled.
Daily opt-in stays absent, root safely_blocked=true outside an actual test.

Five offline wrapper lifecycle fixtures passed on Mac; 17 existing daily
guard fixtures passed there as well. The existing GI-dependent guard tests
cannot run in the Mac Python environment and are checked on Fedora instead.
Fedora passed all 17 attended-guard, 16 deep-cycle and 17 daily-guard fixtures.

## First closed-lid cycle: RTC return

The user then replied **已合盖**. The read-only preflight confirmed LidClosed=true,
exact candidate boot/notes, healthy storage and counters 0/0. The single
transient `pds001-v4-lid-1.service` ran the committed wrapper without changing
the installed image or production policy bytes.

- At 17:17:26 JST, the guard armed the 120-second RTC rescue, observed
  PrepareForSleep=true and entered genuine `PM: suspend entry (deep)`.
- It returned at 17:19:29. CLOCK_BOOTTIME minus CLOCK_MONOTONIC deltas measure
  **119.153 seconds** asleep; wake IRQ **166** is the RTC, not the lid sensor.
  The live Wi-Fi journal stream stopped before delivering the entry events;
  these were recovered from the journal after reconnection. Loss of network
  was not itself treated as proof of sleep.
- PrepareForSleep=false, verified RTC cleanup and temporary policy unmount
  all completed. Test and suspend units returned success; counters are 1/0.
  Root guard again reports safely_blocked=true / CanSuspend=no. There were
  no observed UFS/EXT4/I/O or DSC reservation errors during this cycle.
- Wi-Fi activated again at 17:19:45, about 16 seconds after resume. KWin,
  Plasma and PowerDevil remain active with unchanged main PIDs and NRestarts=0.
- The post-resume device still reports LidClosed=true. DSI-1 is disabled;
  DSI-2 owns CRTC110, LM0 and PP0, with all DSCs unassigned. This is the
  lower-first allocation state needed to test the candidate on reopening,
  **not an upper-display failure diagnosis while the lid remains closed**.
  Backlight power reads upper=4 / lower=0: the RTC-return closed-lid LCD state
  is not accepted as correct and needs checking separately.

At the first post-resume checkpoint the user had been asked to open the lid
without pressing power; no reply or lid-open event had yet been recorded.
The later reopening observation is below. No Hall-sensor wake is claimed:
the RTC had already resumed the system. Daily deep sleep stays off; the
device remains on the temporary v4 RAM kernel.

The /run evidence archive was copied to the private Mac evidence directory,
SHA-256 `e9ab26495606855203938c2b57d76ca786ea2ced03a461bf96ad4de46365bef4`.
The live stream, precise unit journal and closed-lid DRM mapping are preserved
alongside it, outside Git. The unit journal also contains one KSplash service
timeout preceding test dispatch; no corresponding coredump or desktop restart
was observed, so this is not attributed to suspend.

## Subsequent physical reopening: automatic dual-display recovery observed

Logind recorded **Lid opened at 17:21:59.938 JST**, after the RTC return.
Without any operator output-enable, DPMS-wake, compositor restart or reboot,
the managed lid-open path restored both outputs. The inspected 17:23 screenshot
shows the original upper desktop and lower Panel. KScreen reports original
120/60 Hz, scaling, layout and priorities; both backlights are powered on.
Current brightness reads 2416/1999 (Panel 59%/49%), different from the much
earlier boot screenshot; no exact-brightness-restoration claim is made here.

The decisive allocation evidence is now observed on hardware: lower CRTC110
retains **LM0 / PP0**, while returning upper CRTC109 successfully gets
**LM1 / PP1 / DSC1**. DSC0 remains free. This is precisely the lower-first
parity case the unpatched allocator prematurely rejected. No DSC reservation
or storage error appeared. This supports the candidate's intended display fix,
but is not proof of every suspend/reopen path.

The light helper's first profile readback failed at 17:22:00, then its bounded
retry completed at 17:22:04 with both outputs on and performance preserved.
The final Panel shows performance mode. This transient is recorded, not hidden.
The user subsequently explicitly confirmed **双屏和触摸都正常**, accepting
physical display/touch recovery for this RTC-return/reopen cycle. Repeated
cycles and opening the lid **during** sleep remain untested on v4; this one
cycle does not authorize persistent installation or establish daily reliability.
Screenshot and open-state/precise lid journal are saved off-device alongside
the earlier evidence. The temporary live journal connection has been stopped.

## Next one-use Hall wake test prepared, not dispatched

`attended-dsc-hall-test.py` preserves the first /run evidence and reuses the
same test engine with exactly three fixed changes: output `/run/pds001-v4-lid-2`,
expected starting success count 1, RTC rescue 180 seconds. It also locks the
trusted predecessor receipt to this boot, start 0/0, result 1/0 and measured
119.153 seconds. No repeat counter or arbitrary kernel override is exposed.
Seven offline lifecycle fixtures pass, including refusal of the wrong followup
count and policy cleanup for the second fixed parameter set.

Current interrupt inventory distinguishes **IRQ202 Lid Switch** from
**IRQ166 pm8xxx_rtc_alarm**. The next acceptance must examine actual sleep
duration, wake IRQ and lid events rather than call an RTC return a Hall wake.
The user must close the lid again and be told promptly to open after dispatch,
well before the rescue deadline. No second sleep has been requested in this
preparation step; current counters stay 1/0 and the guard safely blocked.

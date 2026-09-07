# Hall wake leaves the upper output disabled — 2026-09-05

## Acceptance result and safety boundary

The second attended v4 cycle **failed physical display acceptance**. The user
opened the lid without pressing the power button and reported that the lower
display and touch worked, and that upper touch worked but the upper display
remained dark. Successful kernel suspend counters do not override that result.

The device remains on temporary RAM-booted v4, boot
`72a9286e-7eae-46cf-966a-6afc7d2102be`. The installed `/boot/boot/Image` is still
the unchanged v3 image. No persistent v4 installation, automatic output-enable
fallback or automatic deep-sleep enablement was performed. The previous
[RTC-return/reopen acceptance](2026-09-05-dsc-parity-candidate.md) remains valid
for that different sequence; it does not establish Hall-wake reliability.

## Observed second cycle

- Measured genuine deep-sleep duration: **128.367 seconds**.
- Wake IRQ: **202, Lid Switch**, rather than the RTC rescue interrupt.
- Suspend success/failure counters: **2 / 0** after this cycle.
- The attended guard observed resume, cleared its RTC rescue and removed the
  temporary sleep-policy bind mount. Normal policy again reports
  `safely_blocked=true`; daily deep sleep remains disabled.
- After resume, logind reports `LidClosed=false`. Upper `DSI-1` is connected
  but **disabled**, CRTC109 inactive and upper `bl_power=4`. Lower CRTC110 is
  active on LM0 / PP0, with lower `bl_power=0`; all DSCs are unassigned.
- No DSC reservation failure or UFS/EXT4/storage I/O error was observed in this
  cycle. This is not the prior v3 `unable to find appropriate DSC` failure.
- KWin, Plasma and PowerDevil kept their existing processes, with no restart.
  The light helper repeatedly reports only `DSI-2:on`, followed by failed
  dual-display verification. Its global DPMS retry does not enable a disabled
  output, so those retries alone cannot recover this state.

## Thaw evidence and what it does not prove

The private `v4-hall-failure-state-journal.txt`, lines 639–648, records the
KWin failure and first helper retry below; the paired logind evidence places
its lid-open notification between them:

| Monotonic time | Observation |
| --- | --- |
| 2006.351881 | KWin `drmModeListLessees()` fails with permission denied. |
| 2006.359960 | KWin reports `Applying output configuration failed!`. |
| About 2006.398 | The logind lid-open notification follows. |
| 2007.390197 onward | The helper sees only the lower output in DPMS state. |

The permission and configuration errors are concrete observations during
session thaw. They do **not** prove that a later KWin Hall-open callback ran
and failed for the same reason. In the reviewed
[KWin 6.7.3 workspace source](https://raw.githubusercontent.com/KDE/kwin/v6.7.3/src/workspace.cpp),
the exact generic configuration-failure message belongs to
`updateOutputConfiguration()`. The sensor-change callback separately attempts
an output configuration and does not inspect the returned error there.

KWin's
[LidSwitchTracker](https://raw.githubusercontent.com/KDE/kwin/v6.7.3/src/lidswitchtracker.cpp)
maintains its own state from input switch events. The Pocket DS light helper
instead reads logind's `LidClosed` property. A correct logind value therefore
does not establish that KWin's independent tracker has reconciled after thaw.
Transient output-application failure and stale KWin lid/configuration state
remain hypotheses to distinguish, not a fully proven single root cause.

## Scoped recovery, not an automatic fix

After preserving failure evidence and a screenshot, one scoped output-enable
request restored `DSI-1` using its existing 120 Hz mode/layout, with upper/lower
priorities 1 / 2. It did not disable the surviving lower output. Actual output
readback showed both enabled and upper `bl_power=0`, with no new DSC or storage
error. The resulting screenshot shows the upper desktop and lower Panel.
There was no KWin/Plasma restart, reboot or further sleep request.

This demonstrates that the v4 driver can restore the upper output from this
state when an explicit configuration request is made. The user later confirmed
the paired ordinary close/open was normal, as recorded below. That confirms
the recovered device, not automatic recovery from the failed Hall sleep cycle.
The manual request is not a deployed automatic lid fix.

A later read-only health checkpoint still confirmed the installed v3 hash,
exact v4 runtime notes, counters 2 / 0, clear RTC alarm and blocked daily sleep
policy. Wi-Fi and KWin/Plasma/PowerDevil remained active with no service
restarts. By that checkpoint both backlights read `4` while the lid was open;
the final check also confirmed both DPMS states were off. One invocation of
the existing managed `wake_display()` path then returned both DPMS states to
on and both backlights to `0`, without another output-enable request or sleep
cycle. This is a separate managed screen-wake observation after the manual
configuration recovery, not an automatic Hall-wake fix or physical user
confirmation.

## Saved closed-lid configuration requires verification

After that recovery, `v4-hall-kwinconfig-after-enable.json` contains both
`lidClosed:false` and `lidClosed:true` setups with **both outputs enabled**.
There is no immediately-before-recovery JSON snapshot for this second cycle,
so the exact time and operation that changed the closed setup are not proven.

An earlier preserved full JSON in `drm-before.log`, lines 384–434, has file
mtime **2026-09-05 16:32:59 JST** and the following different state:

| Saved setup | Upper | Lower |
| --- | --- | --- |
| Lid open | Enabled | Enabled |
| Lid closed | Disabled | Enabled |

This is not merely a harmless runtime-only sensor override. KWin's
[output configuration store](https://raw.githubusercontent.com/KDE/kwin/v6.7.3/src/outputconfigurationstore.cpp)
loads an existing lid-specific setup's enabled values directly. Its default
internal-output disabling occurs when generating a missing closed setup;
successful applications save actual states under KWin's current lid state.
The current both-enabled closed setup can therefore affect subsequent closes.
It warrants checking both the live tracker and saved configuration. Editing
the JSON underneath running KWin is not an accepted repair: KWin also holds
the configuration in memory and can rewrite it.

## Ordinary lid check — close and reopen passed, no suspend

Keep deep sleep disabled and preserve the current configuration. Ask the user
to perform one ordinary physical **close, hold, then open**, without requesting
system sleep, while capturing paired input/lid events, actual output state and
configuration snapshots. This is intended to distinguish lid-tracker state
from the saved closed profile before designing an automatic recovery path.
The user subsequently confirmed **已合盖** for this ordinary, non-suspend
check. Logind recorded closure at **17:55:28.580 JST** and reports true.
Both KScreen outputs remain connected/enabled with original 120/60 Hz,
geometry, scales and priorities. Both DPMS states are **off** and both
backlight power values are **4**. This confirms display-off while retaining
the two-output configuration; it is not deep suspend or a failure to power off.
Counters stay **2/0**, root safely_blocked=true and the RTC is clear.

`v4-normal-lid-closed-kwinconfig.json` and
`v4-normal-lid-closed-state.txt` preserve the configuration, DRM and lid-helper
evidence outside Git. The config file mtime is 17:55:29.235 JST. It still
contains two-enabled-output setups for both lid states.
The new and post-manual-recovery JSON are byte-identical (5,111 bytes), SHA-256
`0bcd7440ea1374cf3de780a309039b4183fcf91402cc71d7b6bd4f7118735c74`.
Because the two saved setups are identical, this does not reveal KWin's
internal lid boolean or which setup it selected. The ordinary light helper
changed the managed performance profile to powersave; initial readback failed
and its existing retry confirmed ownership at 17:55:37. No operator
output-enable, configuration edit, sleep request or reboot occurred here.

Logind subsequently recorded open at **18:00:02.836 JST**, a close about
161 ms later, then open again at **18:00:05.155**. The existing helper's
debounce/retry handled this observed bounce. It verified both DPMS states on
at 18:00:05.541 and restored its owned performance profile on the existing
retry at 18:00:07.236 after an initial readback mismatch. Actual output
readback retained both original modes/layouts, both backlights were on, and
the user confirmed **正常了**. The root task personally inspected
`v4-normal-lid-open.png`, showing the upper desktop and lower Panel. No
operator wake/output-enable or service restart was needed for this reopen.
Counters remained **2/0**, RTC clear and daily sleep blocked. There were no
new observed DSC or storage errors. The matching open JSON is byte-identical
to both closed and post-manual-recovery snapshots with the hash above.

The open screenshot records the user's current brightness at 59% / 49%; this
is not a claim to have restored older brightness targets. A later idle read
found both DPMS states off with the lid open, without a new suspend cycle;
we did not wake the idle device merely to prepare another test.

No automatic fallback is deployed. A future candidate must not turn repeated
DPMS retries into unbounded output-enable mutations, override unrelated manual
display choices, disable the usable lower output, or write into an unverified
closed-lid setup. A further deep-sleep acceptance is separate from the proposed
ordinary lid check.

## Next bounded candidate: retain the paired layout across real Hall wake

Both-enabled open/closed layouts can be an intentional dual-panel policy:
the ordinary helper turns illumination off without removing the upper output.
This avoids depending on a later lid transition successfully re-enabling it.
It remains a hypothesis for deep sleep, not proof of KWin's internal lid
state or a resolution of the second cycle's failure.

`tools/kernel-ab/attended-stable-layout-test.py` prepares exactly one third
attended cycle, with a separate `/run/pds001-v4-lid-3` receipt, fixed starting
counts 2/0, the same v4 RAM boot and a 180-second RTC rescue. It reuses the
original storage, thermal, wake, installed-image and temporary-policy guard.
The failed second cycle's kernel return identifies the predecessor; it is
explicitly **not** treated as successful physical acceptance.

The extra read-only gate pins the existing KWin JSON and installed light-helper
hashes, rejects any disabled, disconnected, external or mirrored output, and
requires original modes, geometry, scales and priorities. Immediately before
dispatch it additionally requires both DPMS states off, both `bl_power=4`, and
lower LCD raw/actual brightness zero, then rechecks lid closure. A changed
configuration is refused, never overwritten. A refusal after temporary policy
binding cleans up and consumes the one-use receipt. `check` does not dispatch,
create a receipt, alter a display or enable daily sleep. No third cycle has
been dispatched at this preparation checkpoint.

The root-owned `/run/pds001-v4-stable-tools/` staging copy passed native
read-only `check`: same boot, counts 2/0, lid open, candidate gates satisfied.
No `/run/pds001-v4-lid-3` receipt exists, and the production guard still reports
`CanSuspend=no`, `execution_ready=false`, `safely_blocked=true`. KWin, Plasma
and PowerDevil remain active with original PIDs and zero restarts. Mac and
root-staged copies have matching SHA-256 values:

| Staged test file | SHA-256 |
| --- | --- |
| Base lifecycle | `fabe0c8e3a1887dd82dda847c8e217ec24336188911650618ec377197a6efdf1` |
| Retained-layout wrapper | `c687bc5e857e1720d123375528efe9a853a36eeaf2b7ee9c559cc28f7d8f7a33` |

Verification passed on both Mac and native Fedora ARM64: 26 retained-layout
fixtures, 11 attended-lifecycle fixtures and 17 daily-policy fixtures (54
total per host). These include topology/config drift refusal, live-query
recheck, physical-off requirements and dispatch-gate failure restoring the
temporary policy without sleeping. Both attended suites are now included in
the standard test runner. Shell syntax and Git whitespace checks passed.
No full hardware acceptance is inferred from these offline fixtures.

Even if this attended Hall test passes, production ownership is different:
`LidDaemon._apply()` yields its closed-lid action to PowerDevil when daily
sleep is eligible. The attended test leaves daily disabled, so the light
helper still owns DPMS and profile handling. Before any daily deployment,
separately verify RTC/unrelated wake while still closed (both physical screens
must remain dark), inhibited/aborted native sleep, native Hall wake, and
authorized persistence/reboot. The first v4 RTC cycle already exposed the
closed lower LCD lighting after resume. Retained logical outputs alone do
not prove physical blanking or standby power. v4 remains RAM-only and the
installed v3 image remains unchanged throughout preparation.

## Third cycle result — RTC return and later reopen passed, not Hall wake

After the user confirmed closure, the root-staged one-use wrapper was
dispatched exactly once as `pds001-v4-lid-3.service`, invocation
`a359dbcbe7274b97bf613b9b6481cedc`. The candidate-dispatch receipt proves
both original outputs enabled, paired configuration/helper hashes intact,
both DPMS states off, both backlights blanked and lower raw/actual zero before
suspend. The previous two receipts were preserved.

| Event (JST) | Evidence |
| --- | --- |
| 18:28:37 | Guard arms 180-second rescue, receives PrepareForSleep=true; kernel enters deep. |
| 18:31:41 | System returns; measured suspended time **179.442 seconds**, counters **3/0**. |
| 18:31:41 | PrepareForSleep=false, RTC cleared, temporary policy unmounted, test unit succeeds. |
| 18:32:13 | Logind records physical lid opening, about 32 seconds after return. |
| 18:32:13–16 | Existing helper verifies both displays on and completes managed profile restoration on retry. |

The live wake IRQ is **166, pm8xxx_rtc_alarm**. The intended Hall-wake
condition was therefore **not exercised**: the timed rescue returned first,
then the lid opened. This is neither Hall success nor a new demonstrated
Hall failure. Network loss during the cycle was not used as proof of deep
sleep; the later kernel/guard receipts establish the real duration. No further
sleep request or fourth-cycle wrapper was issued in this turn.

The user then reported **开盖了 一切正常** in response to the dual-display
and touch check. Actual post-open state has both DSI outputs connected/enabled,
original 120/59.999 Hz modes, geometry, rotation, scales and priorities, no
mirroring, both DPMS on and both `bl_power=0`. The root task personally
inspected `v4-stable-layout-rtc-reopen.png`, showing upper desktop/Dolphin
and lower Panel. No operator DPMS/output-enable, restart or reboot intervened;
KWin/Plasma/PowerDevil retain their original PIDs with zero restarts. This
accepts **automatic display/touch recovery for RTC return followed by reopening**.

No new observed DSC-allocation or UFS/EXT4/storage I/O error occurred. Known
CPU feature variation and Wi-Fi hwmon resume warnings remain in the kernel
log; this is not a claim of a warning-free kernel. Wi-Fi re-associated about
16 seconds after system return. The screenshot shows current brightness
23% / 49%; no matching physical pre-sleep upper brightness baseline was
captured, so exact brightness restoration is not claimed.

The still-closed interval between RTC return and opening has **no paired
DPMS/physical-backlight snapshot**. An audit entry records the lower brightness
restore request during that interval, but does not by itself prove panel
illumination. This cycle must not be used to accept “RTC return while closed
keeps both physical screens dark.” That acceptance and native daily ownership
remain separate outstanding work.

Same v4 boot/notes, installed v3 image, KWin JSON, light helper and baseline
sleep-policy hashes all remain unchanged. RTC content is empty, daily opt-in
absent, policy is not a bind mount, and the guard reports CanSuspend=no,
execution_ready=false, safely_blocked=true. v4 remains RAM-only; daily deep
sleep stays disabled. Both read-only journal streams were stopped after the
cycle. Raw receipts and logs were copied off-device; root receipts retain
their machine-written `physical_display_acceptance=pending`, with this user
confirmation and its limited scope recorded here instead of rewriting them.

Off-device third-cycle evidence:

| Artifact | SHA-256 |
| --- | --- |
| `v4-stable-layout-3-volatile-evidence.tar.gz` | `4d80e13c95c93a46b67b1d47949359e61794aea1192a723f2674d2924c8779fb` |
| `v4-stable-layout-rtc-reopen.png` | `d1aa403d9ed0288a17ce135c7a40159a8fe8b06c99feff2691927ec3c5274b34` |

`v4-stable-layout-cycle-3-journal-state.txt` contains the time-correlated
service/lid/helper logs, before/result receipts, wake IRQ and post-open output
readback. A future Hall test needs its own fixed, short physical-open schedule
agreed **before** dispatch; waiting for a suspended SSH stream to flush is not
a reliable way to coordinate opening before the rescue deadline.

## Private evidence

Raw device evidence is kept outside Git under:

`PRIVATE_MAINTENANCE_WORKSPACE`

Relevant files include `v4-hall-failure-state-journal.txt`,
`v4-hall-failure-before.png`, `v4-hall-kwinconfig-after-enable.json` and the
earlier `drm-before.log`. The root task also retained the manual recovery
readback and personally inspected its resulting screenshot. The report does
not substitute the first cycle's screenshot or physical acceptance for this
second cycle's failed Hall-wake acceptance.

Off-device evidence SHA-256 values recorded by the root task:

| Artifact | SHA-256 |
| --- | --- |
| Second-cycle evidence archive | `35069e0ec42d832620ab76cef14e07965e1cf765d6463085dd0db48c27476b4c` |
| Failure screenshot | `d21075a97bf627ff3e91aa000ea624d5722033158c624b50e62bd463da0090b8` |
| Manual-recovery screenshot | `d61fed3c082849e3f6241c1bbae7b9b9c1d1c8a5241895d67ab986e0de83f011` |
| Ordinary-reopen screenshot | `f2422ff7b47b69457112fa57fddcb6bd47bc8f6b4b05b776200eca744e2d700d` |

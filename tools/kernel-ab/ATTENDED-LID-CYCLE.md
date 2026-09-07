# Attended v4 Hall and closed-RTC evidence

`attended-lid-cycle.py` prepares one explicitly attended cycle. It does not
enable daily sleep, install a kernel, change outputs or repair a failed screen.
Each run needs a fresh `/run/pds-lid-cycle-*` receipt; failure consumes it too.
The three historical `attended-*-test.py` scripts and their receipts are unchanged.

Stage the new harness, `observe-lid-cycle.py`, unchanged
`attended-stable-layout-test.py`, and the reviewed daily verifier renamed
`pocketds-daily-suspend.py` together in a root-owned directory. All four files
must be ordinary, root-owned, single-link, non-writable by group/other files.
The verifier is compiled from the same validated bytes. Pass the independently
reviewed SHA-256 values for the verifier, observer, installed light helper,
installed lid-mode helper, KWin output JSON and PowerDevil config; do not infer
acceptance merely by hashing whatever happens to be installed.

Both `check` and `run` require `--boot-id`, `--expected-success`, `--receipt`,
`--image v3|v4`, `--cycle hall|rtc`, and those six `--*-sha256` options.
`--rtc-seconds` accepts 120 or 180. Runtime notes must always be exact v4;
the selected disk image is restricted to the two recorded complete boot
containers and their exact sizes. No arbitrary kernel hash is accepted.
Old v3 safety checks for storage, thermal tree, wake sources, serial device
callbacks, deep mode, debug mode, mounted filesystems and blocking inhibitors
remain in force. Daily opt-in, pending/failed daily cycles, changed counters or
boot identity refuse dispatch. No production verifier or guard is modified.

Run `check` first while open; it creates no receipt and does not start observers.
For the attended run, use a root transient service independent of SSH and the
user session. Set a stop timeout of at least 15 seconds and an `ExecStopPost`
that invokes this same root-staged script with
`cleanup --receipt /run/pds-lid-cycle-THE-SAME-ID`. Its cleanup only unmounts
the bind mount whose device/inode matches that receipt's own `attended.conf`.
An unrelated mount is preserved and reported as an error. The main `finally`
also performs cleanup. Do not use a service option that hides the policy bind
mount in a private mount namespace; the existing logind guard must see it.

Wait for `armed-waiting-for-close` **while the machine is awake**. This means
native Hall and login1 subscriptions are active and the initial outputs,
DPMS, DRM and fast observations succeeded. From that point the run waits at
most 180 seconds for physical closure, settles the closed lid for 3 seconds,
and refuses if dispatch is later than 15 seconds after observed closure.

Physical schedule is agreed before arming, with a phone timer:

| Test | User action from actual closure T0 | Required wake evidence |
| --- | --- | --- |
| Hall | Open at **T+30 seconds**, without power button, and keep open at least 35 seconds. | Lid Switch IRQ, native close/open within the agreed 25–35 second interval, one PrepareForSleep true/false pair, counters +1. RTC-first is rejected. |
| Closed RTC | Keep closed; open only at **T+(RTC seconds +60)**, then leave open at least 30 seconds. | RTC IRQ, at least 20 seconds of closed-lid post-return observations, both `bl_power=4`, lower raw/actual brightness 0 in all recorded samples. |

Never wait for a suspended SSH stream to flush before opening. If preflight or
dispatch was refused, the user still follows the announced opening time; do
not issue a late sleep request. Each subsequent test needs its own explicit
parameters, fresh counter read and new receipt. There is no automatic loop.

The v2 observer records CLOCK_BOOTTIME, MONOTONIC and Unix timestamps, native
SW_LID only (no EVIOCGRAB or keyboard capture), login1 PrepareForSleep and lid
events, 100 ms backlight/IRQ/RTC/counter samples, plus bounded read-only KScreen
and DRM snapshots. Before readiness it sets **only its own evdev file
descriptor** to CLOCK_BOOTTIME with EVIOCSCLOCKID. Clock setup failure or native
event overrun refuses arming. This does not change logind, KWin or another
client's event clock. The kernel timeval is retained alongside the normalized
nanoseconds; close/open timing uses that raw physical timestamp, not when a
DBus callback or the observer happens to receive it. The timeval's microsecond
precision is represented as a 1 microsecond interval.

Every fast record bounds all its reads with start/end BOOTTIME and MONOTONIC.
The RTC assessor identifies the first post-thaw clock epoch from the increase
in BOOTTIME minus MONOTONIC, including samples delivered **before**
PrepareForSleep(false). It checks every fully closed sample until the raw
physical opening, independent of logind/evdev notification order. A definitely
closed nonblack sample remains a failure even in the last fraction of a second;
no arbitrary tail is discarded. A nonblack or unavailable sample spanning
physical opening, a read spanning suspend/thaw, or missing timing evidence is
reported as `assessment_status=indeterminate`, never as a pass or a proved
closed-lid hardware fault. Dark samples spanning opening are listed separately
and do not count toward closed-period coverage. A 5 ms clock-read tolerance
handles sequential clock calls; it does not exclude any sample near opening.

Native event overrun, missing/invalid data or unexpected lifecycles prevent
acceptance. Sampling cannot prove absence of optical flashes shorter than its
interval. Save relevant unit/kernel journals from the same boot and time window
alongside the local `/run` records. New observer/harness hashes require a newly
reviewed stage and a new receipt. Existing scripts, hashes and receipts remain
unchanged; this assessor refuses legacy clock schemas rather than guessing a
wall-clock conversion or rewriting their conclusions.

The ABI is defined by Linux 7.1.12's [input UAPI](https://github.com/gregkh/linux/blob/v7.1.12/include/uapi/linux/input.h#L243)
and [time UAPI](https://github.com/gregkh/linux/blob/v7.1.12/include/uapi/linux/time.h#L56).
The [evdev implementation](https://github.com/gregkh/linux/blob/v7.1.12/drivers/input/evdev.c#L177)
sets the clock per client; changing it with queued events flushes that client's
queue and signals SYN_DROPPED, which is why that condition cannot be ignored.

Upper-panel `actual_brightness` must never be sampled: its driver performs a
DSI transaction and changes mode flags while reading. Upper samples are limited
to cached `bl_power`, `brightness` and `max_brightness`; lower `actual_brightness`
is retained because it reads GPIO-driver state. A receipt collected with upper
`actual_brightness` polling is not passive evidence of normal display recovery.

The root guard owns one RTC rescue and one inhibitor-respecting logind request.
Only its observed true→false lifecycle authorizes RTC cleanup. A failed or
unobserved cycle must not have its alarm manually cleared. The temporary
policy is removed immediately after return, before the separate display
observation period, and again in `finally`/`ExecStopPost` as needed.

Results distinguish kernel return, actual wake condition and observed closed
blanking. Physical display and touch acceptance remain **pending** even when
all software checks pass: personally inspect both screens and test both touch
surfaces, without output-enable, DPMS repair, compositor restart or another
sleep. Stop after a failed Hall cycle rather than proceed to RTC validation.
Storage errors stop persistent post-return probes; volatile evidence is kept.

This harness keeps daily opt-in absent. Thus it validates attended guard
ownership and the installed display recovery path; it does not by itself
accept the native PowerDevil ownership path, reboot persistence or unattended
daily suspend. Those require their own candidate gates and physical evidence.

Offline checks: `python3 tests/attended-lid-cycle.py`. The old attended suites
still exercise the reused fixed-layout and original lifecycle components.

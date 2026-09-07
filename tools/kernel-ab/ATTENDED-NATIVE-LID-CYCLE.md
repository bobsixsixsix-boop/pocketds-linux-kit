# One attended native KDE lid cycle

`attended-native-lid-cycle.py` observes one real lid-triggered KDE/logind suspend.
It never requests suspend, changes persistent sleep policy or lid mode, or
repairs a display. Once RTC arming is attempted, any incomplete supervision writes
a volatile daily block before releasing supervision; an unexpected closed/unknown
return blocks before removing its own RTC rescue, as described below. This is an
optional acceptance tool; daily use does not run it or arm an RTC. Do not deploy
or run it while investigating a failed cycle.

Prerequisites are an explicitly accepted v4 boot and disk image, the installed v4
daily gate already enabled, native lid mode already set to `sleep`, an open lid,
no existing RTC alarm, and no pending or blocked daily transaction. Pass the exact
current boot UUID and successful-cycle count (failed count must be zero), plus a
fresh `/run/pds-lid-cycle-*` receipt. Old receipts are never reused.

The supervisor checks **PowerDevil's** ordinary suspend authorization, not the
SSH/root helper's session. It reads the selected lid owner and all three
`SleepMode=1` preferences without changing them, checks the current open lid,
and obtains the verified unique D-Bus owner/PID from the installed daily verifier.
`pkcheck` uses that PID, `/proc` start time and verified desktop UID, without
interactive authorization, with a four-second timeout. Cached `CanSuspend` is
read from that same unique PowerDevil bus owner. The verified owner/plugin,
process lifetime and open lid are checked again before acceptance. A denial,
timeout, stale capability or owner change refuses the test. No policy is relaxed,
no user service is created and `check` still writes no configuration or receipt.

Install the script beside the reviewed `attended-lid-cycle.py`,
`observe-lid-cycle.py` and `attended-stable-layout-test.py`, with root ownership
and no group/other write permission. It uses the installed daily verifier and RTC
guard; it does not copy or override their identities. The observer must be the
corrected v2 version that never reads upper-panel `actual_brightness` (a DSI
read) and verifies BOOTTIME on its own evdev descriptor before readiness.

The CLI accepts `check` or `run` and requires these arguments:

```
--boot-id CURRENT-BOOT-UUID
--expected-success CURRENT-SUCCESS-COUNT
--receipt /run/pds-lid-cycle-NEW-NATIVE-RECEIPT
--common-sha256 REVIEWED-COMMON-HARNESS-SHA256
--verifier-sha256 INSTALLED-DAILY-VERIFIER-SHA256
--light-sha256 INSTALLED-LIGHT-HELPER-SHA256
--lid-mode-sha256 INSTALLED-LID-MODE-HELPER-SHA256
--kwin-sha256 CURRENT-KWIN-CONFIG-SHA256
--powerdevil-sha256 CURRENT-POWERDEVIL-CONFIG-SHA256
--observer-sha256 REVIEWED-SAFE-OBSERVER-SHA256
```

Run `check` first; it is read-only and creates no receipt. Before `run`, agree on
the physical schedule and keep USB rescue available. Run as a root transient
service so SSH suspension does not kill supervision. Do not attach any cleanup
that blindly clears the RTC alarm.

1. Keep the lid open until `armed-waiting-for-native-close` is reported.
2. Close within **180 seconds** of that readiness message. KDE must initiate
   sleep within **15 seconds of the actual close**; the tool never dispatches it.
3. Open **30 seconds after actual closure**, without pressing power, then keep
   open at least 35 seconds. Follow this clock even if SSH output stops.
4. Inspect both screens and test both touch surfaces. Software DPMS/backlight
   values do not prove that either screen actually recovered.

Before announcing readiness, the tool uses the unchanged guard's delay
inhibitor, owner lock and RTC adapter to arm **300 seconds**, which is already
within the guard's 60–300 second range. Actual RTC readback must leave at least
270 seconds. Thus even a close at the 180-second deadline and opening 30 seconds
later leave at least **60 seconds** of RTC rescue margin. The guard's delay
inhibitor is released on the first observed native `PrepareForSleep(true)`;
normal KDE/logind inhibitors remain in force.

Only exactly one observed `true` then `false` pair reaches RTC cleanup. A return
also queries the current lid, bounded to two seconds. Confirmed open proceeds
normally without writing a block. Closed or unknown first writes the current
boot's `/run/pocketds-daily-suspend/blocked.json` through the already validated
daily verifier, then checks RTC ownership and cleans only its own alarm. That
cycle exits with an error and `native_lifecycle=returned-but-blocked`, without
waiting for the normal 35-second observation period. The runtime latch makes
the installed daily `pre` reject another sleep; it does not remove persistent
opt-in or change the user's selected lid action.

This order matters because PowerDevil 6.7.3 can retry suspend ten seconds after
an Unknown wake while the lid remains closed ([HandleButtonEvents](https://invent.kde.org/plasma/powerdevil/-/blob/v6.7.3/daemon/actions/bundled/handlebuttonevents.cpp#L297)).
Removing rescue before blocking that retry could leave a second sleep without
an alarm. The same hazard applies after missed close/prepare deadlines or an
interrupted supervisor: a later closure must not start an unobserved native
sleep. Therefore **every exception or incomplete exit after attempting RTC arm**
first writes the same boot-scoped block, then unsubscribes/releases the held
delay inhibitor, while leaving RTC untouched. The attempt is marked before the
arm call because a write may succeed before its readback fails. A known late,
open or unknown native prepare writes the block immediately before releasing
its delay inhibitor; a later true/false pair does not authorize cleanup after
that supervision error. Already-written blocks are not repeated on exit.
Range/foreign-alarm refusals before arm need no block; a normal completed open
return also writes none. If the block cannot be written, that failure is explicit
and rescue is preserved. A foreign
replacement alarm is always preserved, even after a successful block; an already
expired empty alarm requires no RTC write. Missed close, incomplete lifecycle,
interruption or uncertain ownership also preserve rescue. No `finally` handler
blindly clears RTC. Stop after an error: leave the lid open, retain the receipt,
disable daily sleep through the existing recovery procedure, and inspect the
runtime block and RTC ownership before any further cycle. Do not manually clear
an alarm merely because the supervisor exited or remove the block to retry.

Acceptance requires the same boot, exactly one additional successful suspend,
no failed count, clean daily transaction completion, an empty final alarm, Hall
IRQ evidence, and raw BOOTTIME physical close/open timing, including the
180-second readiness and 15-second prepare bounds. Delayed event receipt and
wall-clock correction do not change those physical bounds. RTC-first does not pass Hall
acceptance. Display and touch acceptance remain `pending` until the user has
checked them. There is no retry, automatic sleep loop or second-cycle request.

Offline fixtures: `python3 tests/attended-native-lid-cycle.py`. They use the real
guard lifecycle and range constants without importing GI or accessing hardware.

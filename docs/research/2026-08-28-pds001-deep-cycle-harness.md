# PDS-001 gated deep-cycle acceptance harness

Date: 2026-08-28. Scope: source, fixture and read-only device preflight design.
No suspend, RTC alarm, service restart, helper installation or power-state
change was performed in this batch. The active PDS-008 telemetry soak was not
interrupted.

## Upstream boundary

The [Linux kernel sleep-state documentation](https://cdn.kernel.org/doc/html/latest/admin-guide/pm/sleep-states.html)
defines `deep` as suspend-to-RAM and states that its path is selecting `deep`
in `/sys/power/mem_sleep` and entering `mem`. The harness therefore has no
s2idle, freeze, standby, hibernate or fallback branch. The
[util-linux rtcwake manual](https://man7.org/linux/man-pages/man8/rtcwake.8.html)
documents a separately armed RTC wake and the `no` mode used before systemd
runs its normal suspend lifecycle.

Read-only live facts were: model `AYANEO Pocket DS`, `freeze mem disk` support,
`s2idle [deep]`, `pm_async=0`, `/usr/bin/rtcwake`, PMIC `rtc0` wake enabled,
no existing wake alarm, and active NetworkManager/InputPlumber/TuneD/fan
services. These facts are prerequisites, not suspend evidence.

## Fail-closed design

`pds001-deep-cycle.py` defaults to read-only JSON preflight. Its v2 report requires the
exact model, `mem`, selected `deep`, serialized callbacks, strict uncommented
deep-only and safe-ignore policy assignments, an enabled/clear RTC, both DSI
connectors, both hardware backlights, battery and ALSA availability, four
system services, three user services and a root-owned non-writable helper whose
SHA-256 exactly matches the repository reference.

Execution additionally requires both `POCKETDS_ALLOW_DEEP_CYCLE=YES` and the
literal `--confirm POCKETDS-DEEP-ONLY`. Attempts are bounded to 1--10, RTC
rescue to 15--300 seconds and post-resume settle to 1--60 seconds. A private
new report is checkpointed before suspend; an unchanged boot ID, bounded
`CLOCK_BOOTTIME` interval, successful helper return and complete post-resume
snapshot are all required. It stops on the first failed cycle.

The v2 report also records only the categorical battery state and external-power
boolean, a domain-separated one-way boot token, and a clean repository revision
plus helper-reference hash. The binding is rechecked after every resume and at
completion. Raw boot identity, battery percentage and private paths are not
written.

The existing allowlisted root helper gains only `deep-suspend <seconds>`. It
rechecks the exact model, current deep selection, `pm_async=0`, installed
deep-only policy, RTC wake state and absence of an existing alarm. It arms
`/dev/rtc0`, reads back a non-empty alarm, syncs, invokes only
`systemctl suspend`, and clears the alarm on return or failure. Invalid bounds
return before any privileged operation.

## Evidence and deferred execution

Twelve fixture/static tests cover healthy preflight, private-identifier absence,
wrong sleep mode, occupied RTC, inactive service, commented/duplicate policy,
stale helper hash, elapsed/boot/post-resume gates, private no-clobber reports,
link rejection, battery categories, domain-separated boot token, clean revision
binding and both confirmation gates. Existing privileged-helper invalid
input tests add four deep-suspend cases. Local full `make test` passed. Source
commit: local `e68147a`, device `44cd652`.

The companion offline series evaluator has nine fixture/static tests and accepts
only four distinct private successful v2 reports with one source binding: ten
30-second discharging cycles, ten 30-second charging cycles, five 30-second full
cycles and three 300-second discharging cycles. It emits no boot token and returns
only `AUTOMATED_PASS_PHYSICAL_PENDING`. Audible speaker, touch, gamepad, Wi-Fi
traffic and standby longer than five minutes remain `NOT_EVALUATED`, while
`pds001_complete` is always false.

After source-only synchronization, all 14 live hardware, policy, RTC, display,
backlight, service, battery and audio preflight gates were true. The fifteenth
gate, `privileged_helper_current`, was false because the installed helper hash
still intentionally differs from the repository reference. The device full
`make test` passed. This expected false gate proves that synchronizing source
alone cannot enable execution; no helper was installed and no RTC or suspend
operation occurred.

After the PDS-008 soak completes, deploy the exact helper, run
`make observe-deep-preflight`, place RTC/SSH/physical recovery and a person at
the device, then start with exactly one 30-second cycle. That future execution
must use a new report path. This commit does not authorize or claim a real
suspend test.

## v2 implementation evidence

The v2 implementation and four-report series landed as local commit
`287335be2eb455f229a70688691aeb8b7becc605` and device commit
`53c4f99ed2b23385e14aa8b444336a5b3769a406`; both resolve to tree
`84e6c05a57a0b29affe2c863b8a977d2c0248b77`. The 12 single-report tests, nine
series tests, lint, `git diff --check` and full `make test` passed on both hosts.
The device full matrix also found every expected live service healthy.

The device's new default read-only preflight returned schema v2 with 14/15
gates true and categorical battery context `full` plus external power. Its sole
false gate remained `privileged_helper_current`, because the current source
helper is intentionally not installed during the telemetry soak. No execution
flag, RTC alarm, suspend, helper installation or service restart was used.
Afterwards PDS-008 still had invocation
`e6d19f0a75fe4c54ad46c8eb2d9a7faa`, PID `162791`, zero restarts and its
original 2026-08-28 03:59:35 JST start; no UI transaction existed. All 28 real
deep cycles and every physical/long-standby follow-up remain **NOT RUN**.

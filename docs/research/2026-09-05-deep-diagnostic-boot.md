# 2026-09-05 attended deep-suspend experiment

## Current outcome

The corrected v2 diagnostic kernel boots the existing internal Fedora root,
and the user explicitly confirmed both displays are normal. One
`pm_test=devices` cycle returned with healthy storage and services. This is a
device-callback smoke, **not** a successful full deep-suspend cycle. The single
attended real-deep experiment entered sleep and resumed without observed
storage errors, but was woken by a TSENS threshold IRQ after only **5.365 s**,
before the 60-second RTC deadline. This is an early-wake result, not completed
60-second standby acceptance. The user confirmed both screens and touch after
resume. Automatic suspend remains disabled. No boot-partition write occurred.

The user authorized an attended normal-standby experiment. The recovery and
diagnostic preparation below precedes that experiment; it does not establish
that deep suspend is safe or that the former UFS resume fault is fixed.

## Current baseline and recovery

The old `e4e0a38a...` rollback artifact mentioned in August documents belongs
to the earlier installation. Do not use it for this installation: its kernel
does not match the current installed module tree.

The current disk image `/boot/boot/Image` is 17,086,464 bytes, SHA-256
`85b41b68738090e5e04aa9672eecf8b08d9ef189d7c9207d5ec99ef8c7252673`.
Its release is `7.1.0-100.20260717114522.pocketds.fc44.aarch64`.
An off-device private copy, matching configuration, System.map and kernel
notes are retained under the operator's `pds001-deep-recovery-current-v1`.

With the user holding volume down, exactly one ROCKNIX-ABL fastboot device
reported `unlocked=yes`, slot `a`, and `is-userspace=no`. RAM boot of the
current baseline passed, followed by a successful normal disk boot. Runtime
release/notes and unchanged disk image were checked, with healthy services,
read-write root and zero observed UFS/EXT4 errors. This validates the current
recovery artifact and fastboot path. It is **not** the older asymmetric
baseline-over-candidate experiment and must not be reported as that gate.

## Candidate build

The pinned Linux 7.1.12 source tarball from kernel.org has SHA-256
`389716b3ed27e4cd520b903eea04acc33b9804c4282cb7a918f05ff14e9b5ae1`.
The 46 Pocket DS changes from `7fd2df204f342` to `a4975ce7c8ee` were replayed,
followed by the repository native-lid patch. Two compatibility adjustments
were needed:

1. Patch 39 removes early Q6APM buffer mapping. Its one conflicting hunk was
   merged while retaining the stable kernel's extra `PAGE_SIZE` DSP buffer
   safeguard.
2. The downstream haptics trace event still used the obsolete two-argument
   `__assign_str(id_name, name)`. It was changed to `__assign_str(id_name)`;
   the string source is already supplied by `__string(id_name, name)`.

Build release: `7.1.12-pdsdiag.20260905.aarch64`. Diagnostics include PM_DEBUG,
PM_SLEEP_DEBUG, FTRACE, FUNCTION_TRACER, DYNAMIC_FTRACE, PSTORE_RAM built-in,
PSTORE_CONSOLE, PSTORE_FTRACE, PRINTK_CALLER and a 1 MiB printk buffer.
PANIC_TIMEOUT remains zero. No automatic PM test or watchdog was enabled.

Compilation initially exceeded the operator's thermal threshold on the
performance profile and was stopped. Binding compilation to CPUs 0–2 kept
observed temperatures around 55–64 C. Temporary governor/frequency changes
were restored to the exact original values after the build. Build dependencies
were installed; dependency resolution also updated OpenSSL, Python and elfutils.

The kernel, DTB and all 317 modules built successfully. Modules were staged,
stripped and checked before adding the independent 19 MiB
`/lib/modules/7.1.12-pdsdiag.20260905.aarch64` tree. The original module tree
was retained. All 331 module/index files matched the staged manifest.
No kernel RPM scriptlet or `/boot` installation was run.

## First diagnostic image and observation

Image v1 is 19,075,072 bytes, SHA-256
`81537f9c7221913f9d6930fa46a9adff7f3f26762eac3f06609df6f3aa6fa630`.
Its configuration hash is
`d38aecce0fba3f1607b9a8df8b4ec1ca1976f78e649ce644eb43559c30154453`.
DTB hash:
`3b3d654f86a97c50764e22712edefeef1d687ce602cc6e00a5a352d155024c25`.
The boot v0 container reproduces the baseline's 2048-byte pages, kernel/tags
address `0x10000000`, empty external ramdisk and original root arguments.
The built-in initramfs comes from the retained Pocket DS SRPM. Gzip output,
appended DTB and unpacked boot payload were compared byte-for-byte.

Added command-line diagnostics use the upstream `reserve_mem`/`ramoops.mem_name`
mechanism: `reserve_mem=2M:64K:pdsdiag`, 512 KiB each for record/console/ftrace,
`ramoops.max_reason=5`, `no_console_suspend`, `ignore_loglevel`, `loglevel=7`.
The kernel registered ramoops and a persistent console at boot, and exposed
`/sys/power/pm_test`. Dynamic reservation is best-effort across warm boots;
this does not prove persistence across a hard power cycle or replace external
UART. No physical UART capture has been verified.

Fastboot accepted v1 and Fedora became reachable at the existing address.
The user observed the Snapdragon logo with corruption beside it on the upper
screen, and a black lower screen. Live inspection found:

- correct diagnostic release, read-write `/dev/sda13`, working SSH/Wi-Fi;
- no DRM cards/connectors, no ALSA card, and therefore no Plasma session;
- DPU aggregate waiting after the first DSI component; lower DSI and later
  components were not matched, and audio deferred on the DP0 codec;
- an early display-clock `update_config` warning, which remains a separate
  observation until rechecked with the complete panel driver configuration;
- guard safely blocked, `AllowSuspend=no`, empty RTC alarm and zero successful
  suspends.

Root cause of the missing lower component is a configuration migration missed
during preparation: patch 43 replaces `ayaneo,pocket-ds-secondary-panel`/AR11
with `ayaneo,pocket-ds-lower-panel`/ST7703, and patch 45 removes AR11. The
20260717 config had AR11 enabled but ST7703 disabled. `olddefconfig` cannot
infer that driver replacement. V1 therefore compiled without its lower panel
driver. In the source, DSI component registration is triggered by panel attach;
the omitted driver prevents the display aggregate from completing.

The full kernel log, monotonic system journal, deferred devices, command line,
memory map, kernel notes, component state and blocked preflight were saved
privately on both the device and Mac before reboot. Raw logs are not committed.

## Recovery and v2 acceptance

A normal reboot restored the on-disk 20260717 kernel. Both DSI connectors,
all four display components, Plasma/KWin/PowerDevil/keyboard and the SM8550-APS
sound card returned. A Spectacle screenshot visibly confirmed the original
wallpaper, desktop application icons and lower Panel. The disk image hash
remained unchanged, root stayed read-write and no UFS/EXT4 errors were observed.

V2 enables `CONFIG_DRM_PANEL_SITRONIX_ST7703=y`; configuration diff is exactly
that one option. It must pass build, DT-compatible/driver presence, boot,
dual-screen, audio, network and service checks before any sleep experiment.
V1 is rejected for desktop and suspend acceptance, not a daily-use candidate.
The former UFS resume failure and external/persistent diagnostic limitations
remain open; do not infer suspend success from a working desktop or guard.

V2 build completed successfully. Its 19,079,168-byte boot image SHA-256 is
`79f008b6aae1cfbe0c984ee987d1de4feafbe3c4f607fcc4f596f02b3e800bcb`,
and its configuration SHA-256 is
`fe66e634d06015d7494ac657bfc96655222bdf56998db4b03f317371beccd51c`.
The Image contains `ayaneo,pocket-ds-lower-panel` and `modules.builtin` lists
`panel-sitronix-st7703`; the DTB is unchanged. All 317 external modules are
byte-identical to v1. Only the four built-in-module metadata/index files in
the isolated diagnostic module tree were updated and compared to the new
stage tree. V1 artifacts and its complete stage tree remain available.

The live baseline device tree and bound `nvmem-reboot-mode` driver explicitly
provide `mode-bootloader=2`; systemd supports `--reboot-argument=bootloader`.
This standard reboot path is used for the next temporary candidate boot,
instead of requiring another physical volume-key hold.

V2 fastboot RAM boot succeeded. ST7703, both DSI components, DisplayPort and
GPU bound; both connected outputs, Plasma/Panel, ALSA SM8550-APS, physical
speaker and enhanced sink returned. Wi-Fi, InputPlumber, TuneD, fan control,
keyboard, telemetry and brightness services were active. The user confirmed
both screens; a Spectacle capture also showed the expected wallpaper, desktop
icons and lower Panel. Root was read-write and the baseline disk image hash
was unchanged. Early display-clock/SMMU and Q6APM messages remain observations,
not cleared by this basic acceptance; audible sound has not been re-tested.

## Device-callback PM smoke

One system-owned transient test unit used the installed lifecycle guard with
a 60-second RTC rescue and `pm_test=devices`. A memory-backed bind mount made
the existing sleep policy temporarily readable as `AllowSuspend=yes`; its
on-disk bytes were never changed. Cleanup restored `pm_test=none`,
`pm_debug_messages=0`, removed the bind mount and verified the guard again
reported `safely_blocked=true` / `CanSuspend=no`.

Kernel monotonic timestamps: suspend entry 175.358004; devices suspended
176.553549; the explicit five-second PM debug wait; devices resumed
182.959923; suspend exit 183.430245. The guard observed the complete
`PrepareForSleep(true -> false)` sequence before clearing RTC. Success count
increased 0 to 1, failure count stayed 0. No UFS/EXT4 or block I/O error was
observed, root remained read-write, and Wi-Fi reconnected. Both backlights,
connectors, audio sinks and service preflight remained available.

The USB controller reported `port-1 HS-PHY not in L2`, and the AYANEO USB HID
device re-enumerated. These are retained in the raw evidence; this smoke does
not establish physical input or USB stability. A diagnostic `kscreen-doctor`
invocation without a Wayland environment aborted; rerunning with the real
session environment produced the expected layout. It was not a KWin crash.

## Single attended real-deep experiment

The first wrapper attempt stopped **before** arming RTC or requesting sleep:
its temporary policy file inherited mode 0600, so the unprivileged preflight
could not read it and correctly failed closed. Cleanup restored the original
policy; the suspend success count stayed 1. The wrapper was corrected to
preserve the original nonsecret policy's mode 0644, with a new evidence
directory rather than overwriting the rejected attempt. No preflight gate
was removed. Real-deep wrapper v2 SHA-256:
`d3a4c5b4598a960c3ed8b7d6ae604f47328cdf34c1aae2d11440509c115ff8ad`.

All 17 preflight gates passed in the corrected wrapper. The test used exactly
one `guard run 60`, `pm_test=none`, serialized device callbacks, full battery
and connected external power. The on-disk baseline was not replaced.

Observed result:

- Deep entry at kernel monotonic 573.931214; late and noirq device suspend
  completed and all seven secondary CPUs went offline.
- `Timekeeping suspended for 5.365 seconds`; `Triggering wakeup from IRQ 23`.
  Full wrapper boottime went from 572.53 to 582.73 seconds.
- Noirq/early/device resume completed, all CPUs returned, and suspend exited
  at kernel monotonic 577.561828. Monotonic kernel timestamps exclude sleep;
  do not treat their difference as total wall-clock standby duration.
- Success counter changed 1 to 2, fail stayed 0. The first success was the
  devices-only test: this is not evidence of two full deep cycles.
- Boot identity unchanged; root stayed read-write with no observed UFS,
  EXT4 or block I/O errors. Wi-Fi reloaded firmware and reconnected. Services,
  sound-card/sink presence and backlights remained available. KWin, Plasma
  and PowerDevil each reported zero restarts.
- Screenshot confirmed the desktop and Panel. The user explicitly confirmed
  both screens and touch worked after this real cycle. Fan readback remained
  operational (PWM 51/255, approximately 2228 RPM at a later sample).
- RTC was cleared only after `PrepareForSleep(false)`. Cleanup removed the
  memory-only policy overlay, restored debug messages to 0 and left the guard
  safely blocked with `AllowSuspend=no` / `CanSuspend=no`.

Neither an automated acceptance-series pass nor long-standby stability is
claimed. Audible audio, gamepad, Bluetooth and sustained storage activity
were not tested by this one smoke. The old UFS failure was not reproduced,
which does not prove its root cause has been fixed.

## Early-wake source and next boundary

Live `/proc/interrupts` and `/sys/kernel/irq/23` resolve the wake IRQ to
`c271000.thermal-sensor`, GIC hardware IRQ 538, wake enabled. The exact
SM8550 device tree maps this to SPI 506 (538 including the GIC SPI offset),
named `uplow`. Its separate `critical` IRQ is hardware 672 / live IRQ 24.
Thus the observed source is TSENS0's upper/lower thermal-threshold interrupt,
not RTC. The exact sensor, upper versus lower edge and trip temperature have
not been captured. A cooling/fan threshold is plausible but **not proven**.

The diagnostic source's `tsens_register_irq()` enables IRQ wake at registration
for both interrupt types. It does not expose a standard device wakeup switch.
Do not disable thermal protection or raise trip limits to make standby pass.
The next investigation should capture the TSENS sensor/threshold status at
suspend and wake, then review a narrowly scoped handling change that retains
all existing thermal safety paths and runtime thermal control. A separately
named `critical` IRQ alone is not proof that these paths are preserved; see
the [read-only follow-up](2026-09-05-tsens-early-wake-investigation.md), which
identifies orphaned fan trips as the leading candidate and clarifies this
safety boundary. No such change was
implemented, no thermal IRQ was disabled, and no further cycle was run.

Upstream context (not a proven Pocket DS fix): Qualcomm's
[wake-IRQ lifecycle proposal](https://lists.openwall.net/linux-kernel/2026/06/01/335)
moves wake setup into PM callbacks and introduces device wakeup policy. It
does not by itself distinguish nuisance threshold wakes from safety-critical
events. The kernel's [suspend interrupt documentation](https://cdn.kernel.org/doc/html/latest/power/suspend-and-interrupts.html)
explains why an armed IRQ can wake the platform or abort entry. Do not apply
an automotive-only opt-out wholesale to this handheld.

## Evidence retention and restoration

The private off-device `pm-tests-20260905.tar.gz` includes the devices test,
the rejected preflight attempt, the actual single-cycle evidence and exact
wrappers; SHA-256
`09ff8b84016e0fc3545fe69da364080e4a234ab00b9f20678247daeca9799df9`.
The second diagnostic boot's log archive includes:

- full after-deep dmesg: `00d6fe19a76751445dc766feaf362430dad5f1c3b06071c4b6753ce2ffd7855d`;
- PM-unit journal: `0165354f5550c0a15f6110051bc915ba7051fb35782ada126ec61ec3ee9da4d4`;
- interrupt mapping, kernel notes, before/after screenshots and blocked guard.

These logs and images remain private rather than entering Git. After copying
the evidence off-device, a final normal reboot successfully returned to
`7.1.0-100.20260717114522.pocketds.fc44.aarch64`, with the baseline image hash
still `85b41b68…`. Root was read-write, both displays connected, SM8550-APS
present, and system/user services active with no failed units or observed
UFS/EXT4/I/O errors. The guard reported `CanSuspend=no`, empty RTC and safely
blocked execution. The diagnostic module tree/artifacts are retained for
future investigation, but the candidate is **not** the installed boot kernel.
A final Spectacle capture was visually checked: original wallpaper, desktop
icons and lower Panel were present after the baseline reboot; both physical
speaker and enhanced sink were published on the first user-session start.

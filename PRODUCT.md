# Pocket DS Linux Panel

<!-- impeccable:product-schema 1 -->

## Platform

adaptive

## Users

The primary user is the single owner of one AYANEO Pocket DS. They use it as a
daily handheld in both Android and Fedora, usually without an external keyboard
or mouse. Work on the project should optimize the owner's real device rather
than a hypothetical public audience.

## Product Purpose

The project turns the Pocket DS into a dependable dual-system handheld. The
internal drive is shared approximately 50:50 between restored official Android
and Azkali Fedora. Fedora boots from internal storage without the rescue TF
card, while the lower display provides the controls needed to operate Linux by
touch.

Success means the owner can boot either system, switch deliberately between
them, control the dual displays and handheld hardware, play games, type or use
speech input, and recover from a failed change without rebuilding the machine.

## Positioning

This is not a generic Linux desktop skin or a broad Pocket DS distribution. It
is a device-specific integration layer that makes Fedora behave like a native
Pocket DS system while retaining official Android as an independent, usable
fallback. Its differentiator is the combination of dual-screen control,
handheld input, verified hardware helpers, and narrowly reversible system
changes on the owner's real device.

## Operating Context

- The upper display is normally the primary application or game display.
- The lower 1024 × 768 touch display hosts the Plasma Panel, virtual keyboard,
  touchpad and handheld actions.
- The machine normally runs without an external keyboard, mouse or rescue TF
  card, so first-run, login and recovery paths must remain touch-operable.
- Android and Fedora use separate internal partitions. Cross-system switching
  is intentional and confirmation-gated; it is not an automatic failover.
- The repository and its state documents are the durable source of truth before
  device changes. Git history and rollback snapshots are part of the normal
  maintenance workflow.

## Capabilities and Constraints

- The Linux Panel exposes battery and system telemetry, independent upper and
  lower brightness, audio, TuneD performance profiles, fan control, input mode,
  keyboard, touchpad, fullscreen/desktop actions, lid timeout, game FPS limit,
  Codex quota and selected recovery actions.
- Linux-to-Android and Android-to-Linux switchers are deployed. They preserve
  the ABL boot source and may change only the verified `devinfo` `BootMode` byte
  after full-image validation, backup and readback. A complete owner-observed
  round trip remains an explicit acceptance step.
- Android must remain independently bootable and functional. Fedora must remain
  bootable from internal storage with the rescue TF card removed.
- Hardware and graphics packages are protected from blanket updates. Changes to
  boot, partitions, firmware, kernel or the graphics session require separate
  evidence and rollback; ordinary UI work does not authorize them.
- Controls must fail closed: readable telemetry does not imply that a write
  helper is available, destructive recovery is secondary and confirmed, and
  one broken service must not disable unrelated controls.
- Existing keyboard chords, touchpad gestures, physical gamepad input, audio,
  Wi-Fi, Bluetooth, dual displays, wallpaper and Android recovery behavior must
  be preserved unless the owner explicitly requests a change.
- The project is for daily personal use, not a release-ready product. Missing
  licenses, distribution evidence and external runtimes must stay explicit and
  must not be fabricated or silently bundled.

## Brand Commitments

The name is Pocket DS Linux Panel. The official AYANEO Pocket DS Android
lower-screen experience is the binding product reference: the Linux interface
should feel native to the same handheld rather than like a generic PC dashboard.
The interface language is Simplified Chinese, copy is direct and compact, and
the verified physical Pocket DS controller geometry must be used instead of a
generic gamepad illustration.

## Evidence on Hand

- `docs/CURRENT-STATE.md` records the current deployed machine state, rollback
  points and real-device observations.
- `docs/PROJECT-STATE.md`, `docs/KNOWN-ISSUES.md` and `docs/TEST-MATRIX.md`
  distinguish implemented, deployed, smoke-passed, verified and still-open
  behavior.
- The repository contains the Plasma/QML Panel, bounded root and user helpers,
  keyboard/touchpad services, TuneD/fan profiles, InputPlumber integration,
  Android/Fedora switching tools and fixture/static tests.
- The restored Android boot, internal Fedora boot without the rescue TF card,
  Wi-Fi, both displays, Panel services, brightness writes and three TuneD modes
  have current real-device evidence. The full two-way OS-switch round trip is
  still pending owner observation.
- There are no public-customer claims, testimonials, benchmark claims or broad
  release guarantees. Future work must not invent them.

## Product Principles

1. Protect bootability, owner data and a known recovery path before adding or
   polishing features.
2. Prefer one coherent device-specific mechanism over overlapping KDE, kernel
   and custom gesture or input systems.
3. Make the daily touch path obvious, responsive and usable without peripherals.
4. Validate risky changes on the real device with bounded, reversible checks
   and record the result in Git and the state documents.
5. Preserve working Android and Fedora behavior; never treat one system as
   disposable while maintaining the other.

## Accessibility & Inclusion

The primary accessibility requirement is complete touch operation on the lower
display with finger-sized targets, readable Simplified Chinese labels, explicit
pending/disabled/error states and no dependence on hover, a physical keyboard
or fine pointer control for essential workflows.

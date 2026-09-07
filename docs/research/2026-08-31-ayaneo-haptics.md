# AYANEO Pocket DS haptics — 2026-08-31

## Result

The Pocket DS `4001:0428` sidecar exposes ordinary game force feedback and
bounded lower-keyboard haptics. An
attended calibration established that 50 ms is the motor's practical start
threshold: true 0.30, 0.50 and 0.70 / 50 ms pulses were all clearly perceptible
and had useful strength separation, while the shorter pulses were ineffective.
The formal mapping is now `DEPLOYED` and `SMOKE-PASSED`: 0.30/50 ms, 0.40/50
ms, 0.50/50 ms, 0.60/50 ms and 0.70/50 ms.
Generic game `Rumble`, the 55 ms keyboard interval, busy-drop behavior and the
joymouse-only guard are unchanged. No firmware, kernel, boot or partition
content changed.

## Hardware and upstream facts

- InputPlumber PR #670 commit
  `d3932cb4e0de3eb47688e381b5eb5334ebac6254` contains the exact AYANEO/KONKR
  `4001:0428` output protocol.
- Its eight-byte report is `00 00 00 00 LL RR 00 00`; InputPlumber owns the
  hidraw endpoint. Keyboard code never opens or writes `/dev/hidraw`.
- The candidate is based on InputPlumber 0.78.1. The installed Fedora
  `inputplumber-0.75.2-20260506235201.fc44.aarch64` binary remains at
  `/usr/bin/inputplumber` as rollback.
- The live fixed-50 ms AArch64 sidecar is 9,574,272 bytes with SHA-256
  `2c06a4cbfaa2aa93c923b1dc790bbaf15ae44e048662840cce0705bf2d7df244`
  and build ID `b2034bf875122a8810bb543db0faf583f3942331`.

## Routing

Game rumble follows the normal Linux path:

1. the emulator/game writes force feedback to the virtual Xbox controller;
2. InputPlumber translates it to the AYANEO physical output device;
3. stopping or removing the game target stops the motors.

Keyboard haptics use
`org.shadowblip.Output.ForceFeedback.Pulse(d value, u duration_ms)`. The
live backend clamps keyboard Pulse intensity to 0..0.70 and duration to 5..50
ms, always sends Stop before replying, and maps all five strengths to 50 ms.
The 55 ms minimum interval is unchanged. A single private D-Bus worker drops
busy presses rather than queueing them, so haptics cannot delay key injection.
D-Bus threading is initialized before the worker can start.

The keyboard reads only the bounded canonical
`/run/pocketds-input-mode/state` record. It pulses only for an exact stable
`pds-input-v1 <UUID> joymouse` record. Missing, malformed, transitioning,
unknown or gamepad state fails closed for vibration while typing continues.
This prevents the Pulse backend's Stop from truncating game force feedback.

Canonical mode changes first reduce targets to the D-Bus control target, then
inspect force-feedback support and send Stop as a queue barrier, then load the
profile and install new targets. InputPlumber 0.75 without that interface is
still accepted as rollback. If introspection or an advertised Stop fails, the
existing input transaction fails and restores the previous stable mode.

## Runtime files

The following are the live fixed-50 ms implementation:

- `/usr/local/libexec/pocketds-inputplumber-haptics`
- `/etc/systemd/system/inputplumber.service.d/30-pocketds-haptics-candidate.conf`
- `/etc/inputplumber/devices.d/01-ayaneo-controller.yaml`
- `/usr/share/polkit-1/actions/org.pocketds.InputPlumber.Haptics.policy`
- `/usr/local/libexec/pocketds-input-mode`
- `/home/pocketds/.local/bin/pocketds-keyboard.py`
- `/home/pocketds/.local/bin/keyboard_adapter.py`

The byte-identical retained build artifact is:

- `/home/pocketds/Projects/pocketds-linux-kit-worktrees/panel-touchpad-20260830/build/inputplumber/d3932cb4-pulse070-fixed50/inputplumber-pocketds-pulse`.

## Smoke evidence

For the deployed fixed-50 ms candidate:

- changed-file rustfmt, exact Pulse boundary test and polkit policy test: pass;
- Fedora 44 AArch64 locked release build: pass;
- target Python compilation and 174 keyboard tests: pass;
- 17 UI contracts: pass;
- sidecar contract, installer recovery mock, plan-only verification, lint and
  repository diff check: pass;
- binary/source/live SHA-256 and build ID match;
- formal D-Bus Pulse 0.30, 0.50 and 0.70 / 50 ms: one pass each;
- the supervised sequence automatically restored `gamepad` after each probe;
- InputPlumber PID 522514 and keyboard PID 523144: active/running,
  `NRestarts=0`;
- `/usr/bin/inputplumber` remains unchanged at SHA-256
  `f76a1a1f531fe28e3a2ebc76d8040c134ed15374c93512cf5ad0e69069dbd11d`.

A successful software path does not prove that every game will vibrate. The
fixed-50 ms actuation and three representative strengths already have the
user's subjective confirmation, and the formal D-Bus path is smoke-passed.
In-game rumble has not been verified by this deployment task; one title known
to emit rumble is sufficient later. Lack of vibration in a single Nintendo DS
or PSP title is not enough to reject the device path because those libraries do
not universally use rumble.

## Rollback

InputPlumber transaction `20260831Tfixed50` restores the exact deployed
preimage: sidecar SHA-256 `050500a1e6c7c465a06fd5c706645a17af761ce509ec6d37014d16bad37f97a9`,
the former 0.25 / 40 ms candidate. Keyboard transaction
`feedback-fixed50-20260831` restores its exact deployment preimage. The older
InputPlumber transaction `20260830T192143Z` is already `rolled-back` and must
not be used as the current rollback point. Reload and restart the corresponding
services after restoring.

The original Fedora binary and vendor device YAML were not overwritten.
Disable the candidate drop-in and `/etc` device override, reload systemd and
restart InputPlumber to return to `/usr/bin/inputplumber`. Restore keyboard
and canonical helper files from:

- `/home/pocketds/.local/state/pocketds-linux-kit/keyboard-layout-transactions/20260831-haptics-e444e8d`;
- `/home/pocketds/.local/state/pocketds-linux-kit/inputplumber-haptics-transactions/20260831-050500a1`;
- `/home/pocketds/.local/state/pocketds-linux-kit/inputplumber-haptics-transactions/20260831-reviewfix-43eb41fb`.

Do not remove the distro RPM or write a direct hidraw workaround.

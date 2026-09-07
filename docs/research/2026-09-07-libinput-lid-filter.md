# Pocket DS compositor lid filtering

## What this fixes, and what still needs acceptance

This candidate prevents KWin from treating Pocket DS's hinge as a laptop display
layout switch. It does not claim to fix every resume failure. In particular,
the September 5 RTC-wake success was not a successful Hall-triggered wake test;
the earlier Hall cycle left DSI-1 logically disabled. Actual Hall wake, both
screens, touch, volume keys, and closed-lid wake darkness still need attended
acceptance with the intended kernel and sleep owner.

The current input device is `gpio-keys`, not a standalone `Lid Switch` device.
On the observed v4 RAM boot it was `/dev/input/event4`, with EV=0x23, SW=1 and
KEY=`8000000000000 0`: it carries both SW_LID and KEY_VOLUMEUP (115). Never match
the transient event number and never apply LIBINPUT_IGNORE_DEVICE to this whole
device: that would also remove volume-up input from KWin.

The rule instead matches both `gpio-keys` and the first device-tree compatible
string `ayaneo,pocketds`, and removes only SW_LID from libinput's representation.
It leaves raw kernel capabilities and logind/UPower input unchanged. It also
removes libinput's automatic lid-based touchpad suppression; Pocket DS's normal
display/input ownership must therefore be tested with the lid closed.

## Source evidence

Checked against installed KWin 6.7.3 and libinput 1.31.3:

- KWin's [LidSwitchTracker](https://github.com/KDE/kwin/blob/v6.7.3/src/lidswitchtracker.cpp)
  only receives input switch events. Its [initial state](https://github.com/KDE/kwin/blob/v6.7.3/src/lidswitchtracker.h)
  is open. This class has no separate lid-layout policy setting.
- [Workspace](https://github.com/KDE/kwin/blob/v6.7.3/src/workspace.cpp) applies
  sensor changes without checking the returned apply error. Output reconciliation
  also has no general delayed retry after a transient failure. Its generic
  `Applying output configuration failed!` log alone does not prove the lid
  callback failed. The precise September 5 event-order race remains unproven.
- [OutputConfigurationStore](https://github.com/KDE/kwin/blob/v6.7.3/src/outputconfigurationstore.cpp)
  first loads a saved lid-specific setup; only missing setups use generation
  defaults. The observed open/closed saved layouts now both retain the two
  displays. This candidate leaves that JSON and its brightness values untouched.
- libinput [evdev.c](https://gitlab.freedesktop.org/libinput/libinput/-/blob/1.31.3/src/evdev.c)
  applies AttrEventCode before constructing device capabilities, using
  libevdev_disable_event_code. [libevdev's API](https://gitlab.freedesktop.org/libevdev/libevdev/-/blob/libevdev-1.13.6/libevdev/libevdev.h)
  defines this as a local representation change, not a kernel device change.
- PowerDevil's [LidController](https://github.com/KDE/powerdevil/blob/v6.7.3/daemon/controllers/lidcontroller.cpp)
  subscribes to UPower LidIsClosed. [UPower](https://gitlab.freedesktop.org/upower/upower/-/blob/v1.91.3/src/linux/up-input.c)
  opens the raw event device and reads EV_SW/EVIOCGSW. [logind](https://github.com/systemd/systemd/blob/v259.7/src/login/logind-button.c)
  likewise reads kernel input directly. Neither consumes this libinput quirk.

## Install and verify

Run from the repository, with the device open and an attended maintenance window:

```sh
sudo python3 scripts/check-libinput-lid.py --expect native
sudo python3 scripts/install-libinput-lid.py --install
sudo python3 scripts/check-libinput-lid.py
```

The first command is a baseline check for a device without the rule. Skip it if
the rule is already installed. The installer accepts either prior lid state,
but always requires the keyboard and volume-up key to survive.

The final result must have `ok`, `fresh_context`, `raw_lid`, `raw_volume_up`,
`keyboard`, and `volume_up` true; `lid` false; and `errors` empty. The diagnostic
opens only the matched device read-only, with no grab, dispatch, or injection.
The device path can change across boots and is discovered from raw capabilities.

Installation publishes only `/usr/share/libinput/99-pocketds-lid.quirks`.
It does not change `/etc/libinput/local-overrides.quirks`. Before publication it
copies the existing system database into a temporary directory, includes any
local overrides last, and checks a fresh context against the entire candidate.
It then checks another fresh context against the real installed database. A
failed check restores the previous file (or its absence), ownership, and mode.
The returned backup directory contains the exact preimage and manifest.

Explicit restoration uses the returned backup path:

```sh
sudo python3 scripts/install-libinput-lid.py --restore /var/lib/pocketds-linux-kit/libinput-lid/BACKUP
```

Restore refuses to overwrite an independently edited installed rule and tests
the restored database before and after publication. Neither install nor restore
changes the currently running compositor.

## Activation and upgrades

An existing KWin context does **not** reload this file automatically.
[libinput_init_quirks](https://gitlab.freedesktop.org/libinput/libinput/-/blob/1.31.3/src/libinput.c)
sets `quirks_initialized` once per context, even when initialization fails.
Removing/re-adding an input device or resuming the same context is insufficient
to reload the quirk database. A new KWin/libinput context is required, normally
through a controlled new desktop login. The installer deliberately does not
restart KWin, udev, logind, PowerDevil, or the desktop.

After activation, identify the current gpio-keys event number and read KWin's
`/org/kde/KWin/InputDevice/eventN` D-Bus properties. Require `lidSwitch=false`,
`keyboard=true`, and `enabled=true`, then physically verify volume-up and native
logind/UPower lid transitions before attempting attended sleep acceptance.
A successful fresh-context diagnostic alone is not evidence that live KWin has
reloaded the policy or that Hall wake is fixed.

[Device quirks are internal libinput API](https://wayland.freedesktop.org/libinput/doc/latest/device-quirks.html)
and can change on upgrade. The installer and diagnostic use the installed
parser/capability API rather than trusting text matching. Run the diagnostic
after libinput updates and again after starting the new desktop. Unsupported
parsing, a re-enabled lid, a missing keyboard/volume key, or a changed raw device
topology must block acceptance and prompt review of this candidate. Keep the
rule out of an unconditional sleep opt-in until the attended tests pass.

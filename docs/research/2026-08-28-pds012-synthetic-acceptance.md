# PDS-012 isolated synthetic ES-DE acceptance

## Audit boundary

This review used repository revision `cf0f88c` and did not enumerate or read the
user's ROM tree. The live AppImage and three installed launch-chain executables
were checked only with `stat`; no package, GUI, configuration or device state was
changed.

The existing launch chain correctly prepares ES-DE 3.x metadata before changing
InputPlumber to `gamepad`, forwards termination signals to ES-DE, and restores a
previous `joymouse` mode. Its paths are derived from `HOME`, with explicit
environment overrides for the AppImage, prepare helper and input helper. This
makes a temporary HOME sufficient to isolate an acceptance run without editing
the user's `~/ES-DE`.

The [official ES-DE install documentation](https://gitlab.com/es-de/emulationstation-de/-/blob/master/INSTALL.md)
also defines `--home`, `--no-update-check` and `ESDE_APPDATA_DIR`. The GUI harness
uses all three isolation controls: both home/application-data paths point inside
the temporary fixture and the startup update check is disabled. It deliberately
does not pass `--gamelist-only`, because PDS-012 must exercise the real synthetic
directory scan rather than bypass it.

`make test-emulation` is still useful as a live read-only inventory, but it
enters the configured ROM directory to count entries. It is therefore not the
default entry for this stronger "do not touch the real ROM tree at all" test.
The bounded preflight implementation itself opens no regular files and follows
no symlinks; the synthetic harness reuses it only against a newly generated
temporary tree.

## Fixture contract

`pocketds-esde-synthetic-acceptance.py` always creates a fresh temporary HOME.
The library contains only zero-byte `.pdsfixture` files. Locally generated XML
metadata lives in the ES-DE 3.x `~/ES-DE/gamelists/<system>/gamelist.xml`
location documented by the [official ES-DE FAQ](https://gitlab.com/es-de/emulationstation-de/-/blob/master/FAQ.md),
not in the synthetic game directories. There are no images, video, music,
archives, emulator payloads, remote URLs or copyrighted game data. The system
launch command is `/usr/bin/false`, so even accidentally selecting an entry
cannot start an emulator or consume a ROM.

The default run creates five systems with 1000 entries each, performs the
metadata-only bounded preflight, computes a path-independent logical hash from
relative names and sizes, prints aggregate JSON, and removes the temporary
directory. It does not resolve the live AppImage or input helper and does not
start a GUI or network action.

## Attended GUI gate

Real GUI acceptance is deliberately separate from `make test` and requires all
of the following:

1. an interactive terminal on the Pocket DS;
2. the explicit `--run-gui` mode (provided by
   `make test-emulation-synthetic-gui`);
3. the exact environment opt-in `POCKETDS_ALLOW_ESDE_GUI_TEST=YES`;
4. confirmation `POCKETDS-RUN-ESDE-SYNTHETIC-GUI` and a new report path.

Each of at least ten iterations receives a new HOME plus isolated XDG config/data/cache/state
directories. The real Wayland/DBus runtime remains available, while ES-DE can
only see the synthetic ES-DE configuration and library. The operator must press
Enter within 10 seconds after visually confirming the frontend is interactive.
The harness then sends `SIGTERM` through the normal launcher and requires exit in
three seconds. It uses the privacy-minimal log reader to require an internal startup
sample ≤10 seconds, a complete clean session, no error/fatal/parse event, and a
non-empty log; it also queries the bounded InputPlumber status before and after and
requires exact restoration. The fixture must contain at least 5000 candidates and
remain link/special-file free.

All ten iterations must begin and end on the same clean Git revision and are written
to a new mode-0600 `O_EXCL` report. The report contains
only aggregate fixture identity, durations, counts and booleans—no path, ROM name,
raw log line or input-device name. A missing round or any failed objective/attended
gate returns INCOMPLETE rather than manufacturing a pass.
Typing `q`, Ctrl-C, a timeout or an early launcher exit cancels the run and
cleans up. The default matrix is ten fresh iterations.

This is an attended interaction acceptance, not proof that every entry is
launchable. Real-library testing remains a separate opt-in phase because it
would necessarily enumerate user-owned paths and could involve copyrighted
content.

The strengthened report path and objective gates were added during the PDS-008
24-hour soak as source/fixture work only. No ES-DE GUI or GPU workload was launched;
the attended ten-round matrix remains **NOT RUN**.

## Implementation and verification evidence

- Local implementation commit: `a81649ba7c7949683042e1b812df400571761e9e`.
- Device implementation commit: `b4064b61c65d0217a2895243850d83e3ffb388c9`.
- Both repositories resolved to tree
  `cf41809698e16449a7b3e83dbf388f7490987d66` after patch transfer.
- The focused Python suite passed 12 tests on both hosts. The default isolated
  5000-candidate fixture completed on both hosts with logical SHA-256
  `d660e3d79ae81f414c47ef76a3305ea6697661a469229910916088dce42af737`.
- `make lint`, `git diff --check`, and the full `make test` passed on both hosts.
  The device full matrix also found every expected live user/system service
  healthy.
- Immediately after device verification, the PDS-008 soak remained
  `active/running` with invocation `e6d19f0a75fe4c54ad46c8eb2d9a7faa`, PID
  `162791`, zero restarts, and its original 2026-08-28 03:59:35 JST start. The
  report remained a mode-0600 file and the UI update transaction root was absent.

These checks establish only the harness and fixture boundary. The ten attended
GUI rounds, real-library rounds, entry launchability and physical controller
interaction remain **NOT RUN** until the soak is complete and a person is at the
device.

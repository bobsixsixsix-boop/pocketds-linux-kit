# WatermelonDS / Pegasus G dual-display integration

Date: 2026-09-03

## Result

Pegasus G can launch the NDS entry on the Pocket DS directly from its game card.
WatermelonDS renders the DS top screen on the 1920×1080 upper panel and the DS
touch screen plus emulator controls on the 1024×768 lower panel. Exiting through
WatermelonDS returns to the existing Pegasus G task without crashing it.

The installed independent package is `me.magnum.melondualds.pocketds`, built
from WatermelonDS commit `1e0a463d785448ab47f78a12e2a88241df288cf9`.

## Root cause and implementation constraints

Android does not honor a request from Pegasus to place another application's
activity on display 2. The display-targeting call must originate inside the
WatermelonDS package. Pegasus also cannot pass the cross-application SAF URI as
the intent data URI without a grant; passing the already-persisted URI as the
plain string extra `uri` avoids that launch-time permission rejection.

The first functional launcher removed its upper-screen activity immediately.
That allowed Pegasus to resume while WatermelonDS was still constructing the
second-display surface, causing a Qt event-filter null dereference. Keeping an
opaque launcher activity over Pegasus eliminated the launch race. A second race
existed on exit: destroying the lower-display emulator and revealing Pegasus in
the same frame again crashed Qt. Delaying the launcher removal by 2000 ms lets
Android finish tearing down display 2 before Pegasus becomes visible.

## Verified path

1. Open Pegasus G and select the NDS category.
2. Double-tap the New Super Mario Bros. game card.
3. Confirm the production `EmulatorActivity` is on display 2 and the package's
   upper-screen presentation is on display 0.
4. Open the WatermelonDS pause menu and choose Exit.
5. Confirm Pegasus G is again resumed on display 0 and no `SIGSEGV` or `Fatal
   signal` is present in the captured log.

Evidence images are stored in `build/shared-game-library/evidence/` as
`pegasus-pocketds-final-upper.png`, `pegasus-pocketds-final-lower.png`, and
`pegasus-pocketds-final-return-upper.png`.

## Delivered artifacts

- Local APK: `build/android/WatermelonDS-PocketDS-0.7.0-rc5.apk`
- TF archive: `PocketDS/Apps/WatermelonDS-PocketDS-0.7.0-rc5.apk`
- SHA-256: `ac2c9e6a916df54f5c93065bdf34af01f40ef696e71e6943ca2969a9848bf073`
- Reproducible delta: `components/emulation/android-watermelonds-pocketds/`

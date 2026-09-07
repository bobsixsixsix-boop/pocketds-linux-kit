# WatermelonDS Pocket DS Android variant

This directory archives the small Pocket DS-specific delta used to build an
independent WatermelonDS package for Pegasus G. It does not replace the stock
`me.magnum.melondualds` installation.

## Locked source and output

- Upstream: `https://github.com/SapphireRhodonite/WatermelonDS.git`
- Upstream commit: `1e0a463d785448ab47f78a12e2a88241df288cf9`
- Variant: `gitHubPocketDsRelease`
- Package: `me.magnum.melondualds.pocketds`
- Version: `0.7.0.rc5` (`39`)
- APK: `build/android/WatermelonDS-PocketDS-0.7.0-rc5.apk`
- APK SHA-256: `ac2c9e6a916df54f5c93065bdf34af01f40ef696e71e6943ca2969a9848bf073`

The delivered APK is a private release build signed with the local Android
development certificate. Signing material and passwords are deliberately not
stored in this repository.

## Reapply the delta

1. Check out the locked upstream commit.
2. Apply `build-gradle.patch` at the upstream repository root.
3. Copy `AndroidManifest.xml` to `app/src/pocketDs/AndroidManifest.xml`.
4. Copy `PocketDsLaunchActivity.kt` to
   `app/src/pocketDs/java/me/magnum/melonds/pocketds/PocketDsLaunchActivity.kt`.
5. Provide the upstream release-signing Gradle properties locally.
6. Run `./gradlew :app:assembleGitHubPocketDsRelease --no-daemon` with JDK 21
   and Android NDK 28.0.13004108.

## Frontend contract

Pegasus starts the exported component below and supplies the persisted SAF URI
as the string extra `uri`:

```text
action:    me.magnum.melondualds.pocketds.POCKETDS_LAUNCH_ROM
component: me.magnum.melondualds.pocketds/me.magnum.melonds.pocketds.PocketDsLaunchActivity
```

The package-local entry activity launches WatermelonDS on Android display 2.
WatermelonDS keeps its DS top-screen presentation on display 0. The entry
activity remains opaque over Pegasus while the game is running so Pegasus does
not rebuild a Qt surface during dual-display setup. After the emulator activity
is destroyed, it waits two seconds for the lower display task to settle before
revealing Pegasus; this prevents the reproducible Qt SIGSEGV seen on immediate
return.

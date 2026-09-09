# Pocket DS PowerDevil resume gate and package compatibility

`0001-pocketds-resume-lid-gate.patch` targets PowerDevil 6.7.3. It checks the
exact `ayaneo,pocketds` compatible token and makes a fresh system-bus login1
`LidClosed` request immediately before the normal display-on request after
resume. Only a valid boolean `false` reply permits display-on. Closed,
malformed, error and timeout replies keep the displays off. The request has a
1000 ms bound; other models retain upstream behavior. The normal idle-timeout
registration still runs. This patch does not change wake-source classification,
KWin, input handling, lid actions, display layout or the explicit opening wakeup
path. Verification must never read the upper display's `actual_brightness`.

The adjacent header must be identical to the header added by the patch. Its
private D-Bus fixture checks token/reply handling, open and closed cases, errors
and the one-second timeout. These tests do not replace an attended physical
sleep/resume test.

## Runtime compatibility is a whole-package check

The `pocketds1` package was built with Qt 6.11.2. On a Qt 6.11.1 installation,
its battery and brightness QML plugins failed to load because a constructor's
required symbol version differed from the installed Qt export. RPM dependency
satisfaction and a successful DPMS plugin check did not catch this failure.
That specific package/runtime pairing is incompatible.

This is not a claim that `pocketds1` fails with Qt 6.11.2. The published Alpha3
image contains QtBase 6.11.2-2.fc44, QtDeclarative 6.11.2-1.fc44, Frameworks
6.29.0 and Plasma Workspace 6.7.4-2.fc44. All 22 ELF relocation checks and both explicit cold-QML module checks passed
against that actual image runtime. Conversely, the Qt 6.11.1 `pocketds2` build
failed its QML private-ABI checks in a separate copy of the Alpha3 Qt 6.11.2
root, despite satisfying RPM dependencies. Neither cross-pair is accepted:
`pocketds1`/Qt 6.11.1 and `pocketds2`/Qt 6.11.2 both fail. Forward compatibility
is not guaranteed for private Qt ABI. A daily installation's result cannot be
used as an SD image result. Alpha3 and its original artifacts stay unchanged.

`scripts/powerdevil-runtime-abi.py --installed --expected-elf-count 22` enumerates the installed RPM's
file table and runs clean-environment `ldd -r` checks over every distinct ELF,
including both QML plugins. `--root` checks an unpacked RPM against the runtime
of the process executing the checker. To check an image, run the checker inside
an isolated copy of that image's root; pointing the VM-host checker at an
unpacked directory is not an image runtime check. Missing libraries, undefined
symbols, missing symbol versions, unexpected loader errors, unsafe files and
timeouts fail the check. These are explicit read-only checks, with no service
restart, installation or sleep request.

## Qt 6.11.1 candidate

`powerdevil-pocketds2.spec` preserves PowerDevil 6.7.3 and the exact existing
resume patch, with release `1.fc44.pocketds2`. Its build requirements pin QtBase
6.11.1-1.fc44 (including private headers), QtDeclarative 6.11.1-3.fc44,
Frameworks 6.28.0-1.fc44 and Plasma Workspace 6.7.3-1.fc44. The complete source and runtime packages are recorded in the separate
`powerdevil-6.7.3-1.fc44.pocketds2-qt6111-source-and-abi.tar.gz` provenance
bundle (SHA-256
`89478e82196038679ac01f704bd98302946dc5a78ff085e477e9426b3599474e`).
That bundle is a Qt 6.11.1 candidate record, not an Alpha3 image update.

| Artifact | SHA-256 |
| --- | --- |
| RPM | `e1e0b84a976e80e98080959f4407ddf34d65074ef0212543df21383f7d7359ff` |
| Source RPM | `6245df3ad918219b6062a09b90e13d41ed071a6ae46a9c7e9c457c7d0773a073` |
| DPMS plugin | `fe6fe3635c5d3239ca3044806185eef56f04f69d6b43e6abcfbc4a4da8a2b48a` |

`powerdevil-pocketds2-lock.json` binds the full set of 22 ELF paths, sizes and
hashes, the spec, and the explicit QML probe. All 22 ELF checks and the two QML
module loads/type registrations passed on the Qt 6.11.1 target. The private
resume-gate fixture passed. These results establish that tested runtime's
package compatibility; they do not establish a different image's compatibility
or completed hardware sleep/reboot acceptance. The physical buttons and a new
physical sleep/reboot cycle have not been accepted for this candidate.

## Explicit cold QML check

Build the probe using the target's Qt development packages:

```sh
cmake -S components/powerdevil/tests -B /tmp/powerdevil-tests \
  -DPOCKETDS_BUILD_QML_LOAD_PROBE=ON
cmake --build /tmp/powerdevil-tests
ctest --test-dir /tmp/powerdevil-tests --output-on-failure
```

The probe is deliberately excluded from automatic CTest registration. Run it
explicitly in an isolated target runtime, with a new empty HOME/runtime
location, a clean environment and no connection to a real session or system
bus:

```sh
env -i PATH=/usr/bin:/bin LC_ALL=C HOME=/tmp/empty-probe-home \
  XDG_RUNTIME_DIR=/tmp/empty-probe-runtime QML_DISABLE_DISK_CACHE=1 \
  DBUS_SYSTEM_BUS_ADDRESS=unix:path=/nonexistent \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent \
  /path/to/qml-plugin-load --check-installed
```

Require `status: pass`, the expected actual `runtime_qt`, and both `loaded` and
`type_registered` for `org.kde.plasma.private.batterymonitor` and
`org.kde.plasma.private.brightnesscontrolplugin`. The probe registers types
without constructing battery/brightness controller objects. It does not test
power-plan availability, button behavior or suspend/resume. Re-run whole-package
and cold-QML checks after relevant Qt/Plasma changes.

## Source and rebuild

The KDE source tarball hash is
`72e362963d67488cb55b9d6c563a66bbe043553980269f2e2c2b691f023e2f35`.
Its detached signature was verified with the official Plasma release keyring.
The Fedora baseline source was build 3035183, spec revision
`c28e5da743677d6b00105de873839330461935e7`. The local RPM is not Fedora signed.

Apply the patch to the unmodified 6.7.3 source and compare the header before
building the complete package with the target-matched spec:

```sh
patch -p1 < /path/to/0001-pocketds-resume-lid-gate.patch
cmp daemon/actions/bundled/pocketds-resume-gate.h \
  /path/to/components/powerdevil/pocketds-resume-gate.h
```

The daily deep-sleep gate accepts three exact DPMS/QtCore hash pairs: the published
Alpha3 `pocketds1` with its Qt 6.11.2 binary, and the `pocketds2` / `pocketds3` candidates with
their reviewed Qt 6.11.1 binary. Installing this source on unchanged Alpha3 therefore
preserves its known compatible pair; it does not install the Qt 6.11.1 package
or enable deep sleep. Both cross-pairs and an unreviewed Qt binary with the same
filename are refused. The small artifact check reads bounded DPMS and QtCore
files; it does not run a loader probe or query a desktop service. The full
preflight additionally checks the running PowerDevil process's executable
mappings for both exact device/inode identities, ownership and session-bus
identity. Replaced, deleted, alternate or stale QtCore/DPMS mappings are rejected.
A package/runtime update needs new complete ELF and cold-QML evidence before
adding a new accepted pair. That process check remains
necessary after an explicit deployment; it is not a substitute for the package
ABI and cold-QML checks. Daily deep sleep remains opt-in and needs attended
physical acceptance.

- [KDE PowerDevil 6.7.3 source](https://github.com/KDE/powerdevil/tree/v6.7.3)
- [KDE Plasma 6.7.3 release](https://kde.org/info/plasma-6.7.3/)

The published-image positive test and the cross-runtime negative test are recorded in
[the Alpha3 ABI addendum](../../docs/release/alpha3-powerdevil-abi-20260908.md).

## Battery health update · 2026-09-10

`0002-hide-unknown-battery-health.patch` applies to PowerDevil 6.7.3 after the
existing lid-gate patch. It changes the health-row condition from comparing an
integer to an empty string to requiring a positive capacity. Unknown health is
hidden; the existing low-health warning remains unchanged.

The matched-runtime build is `powerdevil-6.7.3-1.fc44.pocketds3.aarch64`.
Its RPM SHA-256 is `f3fdf2db437ad51837e40b8cb9f413aaffbdf6189402e8ec16b9177795bc8457`;
DPMS SHA-256 is `178015727fb444d1bfb47fad7e7f9c48c1d86c8ba29fd01f456f5caae118e7fa`.
The build retained all 711 development-package versions and 70 runtime requirements
from pocketds2. All 22 installed ELF files and cold loading of both QML modules
passed on the maintenance device's Qt 6.11.1. Package release metadata changes the
DPMS file hash; its code/data sections and the lid-gate patch are unchanged.
The new accepted pair preserves the two existing pairs and all runtime checks.

This source update provides patches and tests, not an automatic package or kernel
installer. Rebuild for the exact target runtime, then repeat the package ABI and
cold-QML checks before deployment. Alpha3 retains Qt 6.11.2 / pocketds1; neither
its image nor its release assets are replaced by this update.

```sh
patch --dry-run -p1 < /path/to/0002-hide-unknown-battery-health.patch
patch -p1 < /path/to/0002-hide-unknown-battery-health.patch
cmake -S /path/to/components/powerdevil/tests/battery-health -B /tmp/pds-health-test \
  -DBATTERY_ITEM_SOURCE=/path/to/powerdevil-6.7.3/applets/batterymonitor/BatteryItem.qml
cmake --build /tmp/pds-health-test
ctest --test-dir /tmp/pds-health-test --output-on-failure
```

The fixture evaluates the actual upstream bindings without creating power
controllers. On Qt 6.11.1, the old source failed the zero/negative cases and the
patched source passed all nine cases. The separate kernel capacity correction,
observed health result and remaining limits are described in the
[battery health report](../../docs/research/2026-09-10-battery-health.md).

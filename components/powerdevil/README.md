# Pocket DS: keep a closed lid dark after resume

`0001-pocketds-resume-lid-gate.patch` targets KDE PowerDevil **6.7.3**. It adds one gate immediately before the normal `DPMS::onResumeFromSuspend()` display-on request. Existing Timer, Telephony, and Network handling is unchanged. The normal `registerStandardIdleTimeout()` still runs when this gate declines to turn the displays on.

The gate applies only when `/proc/device-tree/compatible` contains the exact NUL-terminated `ayaneo,pocketds` token. On this model, it makes a fresh system-bus `org.freedesktop.DBus.Properties.Get` request for `org.freedesktop.login1.Manager.LidClosed`. Only a successful reply with exactly one `QDBusVariant` containing boolean `false` permits display-on. Closed, missing, malformed, error, and timeout replies keep the displays off. The synchronous call uses an explicit **1000 ms** timeout, so no late asynchronous callback can turn them on later. Other machine identities retain upstream behavior and do not make this D-Bus request.

This is a resume-only correction. It does not change KWin, wake-source classification, physical input, lid actions, output layout, or the explicit wakeup API used by the existing lid helper when opening. It does not retry, turn displays off after a flash, or schedule another sleep.

## Source and evidence

- Upstream: [KDE PowerDevil v6.7.3, dpms.cpp](https://github.com/KDE/powerdevil/blob/v6.7.3/daemon/actions/bundled/dpms.cpp), original SHA-256 `8df402535f4ae55e50956001374766ea52211d8e0c18c76e585658742268ff38`.
- The patch includes the complete new `daemon/actions/bundled/pocketds-resume-gate.h`. The adjacent header is the canonical copy used by the standalone Qt tests; it must remain byte-identical to the header produced by applying the patch.
- [SuspendController](https://github.com/KDE/powerdevil/blob/v6.7.3/daemon/controllers/suspendcontroller.cpp) classifies wake sources using `wakeup_count` deltas. RTC01 returned while physically closed; the recorded display-on interval began about 0.47 seconds after `PrepareForSleep(false)` and about 94 seconds before actual opening. All platform wakeup counts remained zero despite RTC events. Changing classification would also change PowerDevil's automatic re-suspend behavior, so this patch leaves it alone.
- Validation must retain the existing cache-only observer rule: never read the upper display's `actual_brightness`.

## Frozen build identity (2026-09-07)

The candidate is `powerdevil-6.7.3-1.fc44.pocketds1.aarch64.rpm`, built from the Fedora 44 `powerdevil-6.7.3-1.fc44` source package in an isolated Fedora 44 aarch64 mock root. PowerDevil remains **6.7.3**; only the package release and this patch change. This work does not upgrade the target's Plasma, Qt, or Fedora version.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Candidate RPM | 1,894,495 | `e450007842afc9283fad14679be6c6494ed64768e24b8cd0a5cd0256ba6bbd13` |
| Installed DPMS plugin | 73,504 | `49358da688b5c2a661af11c3a2f3533a68973584d9a8bb2c4dfe7c2a0a713744` |

The plugin path is `/usr/lib64/qt6/plugins/powerdevil/action/powerdevil_dpmsaction.so`. Build inputs, the frozen RPM, full logs, and `candidate-manifest.json` are retained in the task evidence directory `outputs/powerdevil-closed-resume-20260907/`; its README records acquisition and rollback provenance. Rebuilds must use a new output directory and must not overwrite these frozen artifacts or silently replace the allowed hash.

The original source package came from Fedora Koji build **3035183**, using Fedora spec revision `c28e5da743677d6b00105de873839330461935e7`. The downloaded Koji RPM/SRPM artifacts are **unsigned**; successful RPM digest checks are not signature verification. A downloadable Fedora-signed copy was not obtained. The locally rebuilt candidate is not a Fedora-signed package either.

Separately, the source tarball `powerdevil-6.7.3.tar.xz` has SHA-256 `72e362963d67488cb55b9d6c563a66bbe043553980269f2e2c2b691f023e2f35`, matching the [KDE 6.7.3 release page](https://kde.org/info/plasma-6.7.3/). Its detached KDE signature was successfully verified using the official Plasma release keyring: Bhushan Shah, signing subkey `B3CB366552540BE06EE9AD9711968C44928CAEFC`. This verifies the upstream source, not a signature on either RPM.

Both the unchanged baseline and candidate completed full package builds. Candidate `%prep` compared the patched header with the fixture's canonical header byte for byte; `%check` ran the real Qt/private D-Bus fixture, **1/1 passed in 0.99 seconds**. All 69 candidate runtime requirements were checked against the device, with no new requirements introduced. The build used Qt 6.11.2 development files; the device remains on Qt 6.11.1. These results establish build and dependency readiness, **not completion of physical closed-lid RTC or Hall acceptance**.

## Deployment verification and upgrade behavior

Before installation, verify the staged RPM against the frozen RPM hash and perform the normal package transaction test. Keep daily deep sleep disabled through package replacement and the controlled PowerDevil restart. Replacing the file alone is insufficient: an existing process can still map the deleted old plugin.

After installation, use the updated daily verifier from this repository. These checks are read-only and do not request sleep:

```sh
rpm -q powerdevil
rpm -V powerdevil
sha256sum /usr/lib64/qt6/plugins/powerdevil/action/powerdevil_dpmsaction.so
sudo -n /usr/local/libexec/pocketds-daily-suspend verify
```

The package query should report `powerdevil-6.7.3-1.fc44.pocketds1.aarch64`; the plugin hash must match the table. The verifier additionally checks root ownership and permissions, the live session-bus owner of `org.kde.Solid.PowerManagement`, that process's desktop UID, and executable plugin mappings with the exact installed device/inode. Deleted or alternate plugin mappings, changing process ownership, and file replacement during verification are rejected. The returned `powerdevil_preflight` identifies the checked process and artifact; checking only package metadata or the on-disk hash does not establish this running-process condition.

The ordinary `check` path performs the small on-disk artifact check without process inspection. The root `pre` path repeats the full running-plugin verification before every actual sleep, after the storage-health gate. If a later distribution upgrade replaces the patched plugin, daily deep sleep is refused until the replacement is reviewed and explicitly approved; distribution upgrades themselves remain available. `verify-deployment` adds inhibitor checks before any deliberate daily opt-in. Successful deployment verification still requires a separate attended physical acceptance run before claiming that closed-lid resume is fixed.

## Apply and build

From the unmodified PowerDevil 6.7.3 source root:

```sh
patch --dry-run -p1 < /path/to/0001-pocketds-resume-lid-gate.patch
patch -p1 < /path/to/0001-pocketds-resume-lid-gate.patch
cmp daemon/actions/bundled/pocketds-resume-gate.h /path/to/components/powerdevil/pocketds-resume-gate.h
```

Build using the matching distribution package's existing recipe. The header adds only Qt Core/DBus APIs already used by PowerDevil; no upstream CMake change is needed. No installer or automatic deployment is included here. This patch needs rebuilding and review when the distribution upgrades PowerDevil.

## Isolated behavior tests

With Qt 6 Core/DBus development files, CMake, and `dbus-run-session` available:

```sh
cmake -S components/powerdevil/tests -B /tmp/pocketds-resume-gate-build
cmake --build /tmp/pocketds-resume-gate-build
ctest --test-dir /tmp/pocketds-resume-gate-build --output-on-failure
```

The tests exercise the actual header using Qt values and a private D-Bus daemon. A child process implements a synthetic login1 service only on that private session bus. They check exact compatible tokens, untouched behavior on other models, strict reply types, disconnected/error replies, actual open/closed property replies, and a genuinely unanswered property request whose one-second timeout is measured. They never connect to the host system bus, invoke the production filesystem wrapper, change device state, or suspend anything. These fixtures establish the request/decision contract; they do not replace physical closed-lid RTC and Hall acceptance.

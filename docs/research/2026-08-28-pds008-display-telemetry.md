# PDS-008 display telemetry design — 2026-08-28

The telemetry backend, Panel reader and QML files are installed on the current
development device. The live cache and reader are accepted below. A later
controlled plasmashell reload and KDE Spectacle capture visually accepted the
new refresh/backend header; the separate full-screen geometry defect remains
owned by PDS-005.

## Contract

- The existing two-second GPU daemon remains the sole writer of the atomic
  `%t/pocketds-gpu-status.json` cache.
- A background thread invokes the read-only KWin `supportInformation` D-Bus
  method through `/usr/bin/busctl` once every 60 seconds. The subprocess has a
  2.5-second hard timeout and never runs on the GPU sampling thread.
- Only DSI-1/DSI-2 physical millimeter size, logical geometry, scale and
  physical refresh, plus Wayland/DRM/OpenGL/renderer identity, are flattened.
  Oriented physical pixel extents are explicitly marked as
  `logical-size-times-scale-rounded`; they are an inference, not a claim that
  KWin exposed the DRM native mode directly. Unknown, malformed, stale or
  unavailable values become JSON `null`.
- `display_app_fps` is always `null`. Physical 165/60 Hz output modes are not an
  application frame-rate measurement and must never be labeled FPS.

## Resource and failure boundary

Steady state adds one sleeping Python thread, one small flat object in the
already-written cache and one `busctl` child per minute. It does not increase
the two-second cache write rate or DRM fdinfo scan rate. An initial
68.028-second live observation kept one PID with zero restarts, published 34/34
distinct GPU timestamps at a maximum 2007 ms gap and two distinct KWin display
timestamps, and held `display_status=ok`. The process used 0.3173% of one CPU
and 14.3--16.1 MB RSS/peak during that window. This passes the short resource
gate.

The subsequent read-only 60-minute run completed for 3600.047 seconds with
1800/1800 successful cache reads and 1800 distinct GPU timestamps. It recorded
zero read failures, timestamp regressions or stale reads; maximum GPU timestamp
gap was 2099 ms. All 1800 display observations were `ok`, across 61 distinct
display timestamps, and the only refresh pair was 165.0/59.999 Hz.
`display_app_fps` remained null in every sample. The service stayed on PID
126678 with zero restarts. Its one-core CPU use was 0.3753%; current memory
moved from 14,168,064 to 14,417,920 bytes and recorded peak from 17,772,544 to
18,178,048 bytes. A separate bounded Plasma recovery occurred during the run
without breaking cache cadence or changing the telemetry PID. This passes the
60-minute resource/cadence gate, not the 24-hour gate.

KWin serializes `supportInformation` on its D-Bus object, so even a low-rate
read may briefly load the compositor. For that reason the minimum configurable
period is 15 seconds, the default is 60 seconds, output is capped at 256 KiB,
and parser/version drift fails to partial/null instead of retaining invented
values. Missing user-bus environment, missing `busctl`, timeout, non-zero exit,
invalid JSON and parser exceptions cannot terminate the GPU sampler.

An isolated live probe set `DBUS_SESSION_BUS_ADDRESS` to a nonexistent socket
for eight seconds and wrote only `/run/user/1000/pds008-invalid-bus.json`.
Display status became `unavailable`, both refresh values were `null`, while GPU
samples continued (six clients, a real frequency and load sample). The formal
telemetry service remained PID 126678, active, with zero restarts before and
after. The temporary cache was removed. This accepts user-bus loss degradation;
KWin process loss/restart remains separate.

The live cache and `pocketds-panelctl status` independently matched KWin for
DSI-1 at 1920x1080 inferred physical pixels, 1280x720 logical, 1.5 scale and
165.0 Hz; DSI-2 at 1024x768 inferred physical pixels, 819x614 logical, 1.25
scale and 59.999 Hz; and Wayland/DRM/OpenGL/FD740. Application FPS remained
`null`. A first install exposed that `enable --now` did not restart an already
running old sampler; the installer now has a tested explicit restart contract.

## Visible applet acceptance

Before reload, the installed and repository QML hashes matched but the running
applet still showed only its old “live monitor” header. Restarting only
`plasma-plasmashell.service` changed PID 1160 to 138693 with zero restarts.
The telemetry service stayed on PID 126678 with zero restarts and advanced its
cache timestamp throughout. The post-reload screenshot visibly showed
`165/60 Hz` and `Wayland · DRM · OpenGL`, plus a live GPU card. No Pocket DS
applet QML error appeared; one unrelated KDE brightness-applet null warning was
recorded. That screenshot was not valid evidence of large Panel margins because
it included regions outside DSI-2 in the combined desktop canvas. A later exact
DSI-2 crop accepted current-session geometry separately under PDS-005; its
exit/recovery requirements remain open.

The user-bus loss degradation gate and 60-minute run are complete. A committed
soak harness now supplies private atomic progress, interruption evidence and
explicit service/reader resource gates; its fixture suite and short live smoke
pass, but this is not the 24-hour result. Remaining acceptance is a 24-hour run,
synthetic GPU-load accuracy, and a recovery-backed
KWin loss/restart test that proves GPU samples continue every two seconds while
display fields become unavailable. The 60-minute JSON remains under the
ignored device-local `test-results/` tree.

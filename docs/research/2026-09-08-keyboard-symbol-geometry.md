# Keyboard symbol-page size regression

## Scope

This record distinguishes the daily-development keyboard from the public SD
image. Historical daily commits `167c90b`, `eba3162`, `5cc2547` and `d462ede`
record the fix, native regression and acceptance below; those hashes are not
public-release source identifiers. The public change preserves the existing
user-configured OpenAI-compatible voice API. The new SD Alpha3 image has not
been booted on hardware; the daily result does not validate its first boot,
reboot, sleep or full-system behavior.

## Reproduction and fix

On the daily baseline, selecting `#+=` made the lower-screen keyboard shrink.
Returning to letters did not restore full-screen coverage. GTK3 runs through
XWayland; KWin's existing forced DSI-2 rule owns position and size. With
`set_resizable(False)`, GTK publishes the layout's natural request as both
minimum and maximum. Rebuilding the symbol page changes that request and the
window becomes smaller on the mixed-scale desktop.

The fix restores `set_resizable(True)`, allowing KWin to retain the geometry.
It adds no resize/move calls, timers, output-scale changes or KWin rules.
Existing Backspace, voice and settings presentation remains intact. See
[GTK set_resizable](https://docs.gtk.org/gtk3/method.Window.set_resizable.html)
and the [GTK3 window implementation](https://raw.githubusercontent.com/GNOME/gtk/gtk-3-24/gtk/gtkwindow.c).

## Historical daily-device evidence

The daily-development record reports production view methods exercised in a
temporary GTK window on the device's desktop, using its existing class and
KWin rule. The app constructor was not called; input, audio, haptics, app
launches and settings writes were disabled. Coordinates below are XWayland
client coordinates, not physical panel pixels.

| Stage | Before | Fixed |
|---|---|---|
| Initial letters | 1230 × 923 | 1230 × 923 |
| Symbols | 712 × 550 | 1230 × 923 |
| Return to letters | 730 × 550 | 1230 × 923 |
| Symbols again | 712 × 550 | 1230 × 923 |
| Settings | 712 × 550 | 1230 × 923 |
| Return from settings | 712 × 550 | 1230 × 923 |
| Final letters | 730 × 550 | 1230 × 923 |

The origin remained `(425, 1080)`. The old source failed the symbol transition;
the installed daily fix passed 19 observations: initial letters, six page
transitions, three Backspace feedback states and nine voice feedback states.
The daily record also reports 208 keyboard-adapter, 13 visibility-state and
six geometry-cache tests passing on macOS and the device.

After reopening the installed daily keyboard, the user repeatedly switched
letters/symbols and opened/returned from settings, then confirmed:
“始终满屏，触摸也正常”. This accepts the reported trigger on that daily device.
It is not acceptance of the new public SD image or any new reboot/sleep cycle.

## Opt-in native regression

`tests/keyboard-layout-geometry.py` extracts the production GTK window setup
and view methods without importing the app module or calling its constructor.
It preserves production `set_resizable` and replaces non-view callbacks with
no-ops. Initial geometry must cover DSI-2 with at most two pixels of rounding
per coordinate/dimension; every later observation must match that reference
exactly. Voice observations only render labels and never call a voice API.

The default invocation skips before GTK/session access. To opt in on a
matching Pocket DS Plasma desktop, hide the ordinary keyboard and leave the
temporary window untouched:

```sh
python3 tests/keyboard-layout-geometry.py --live-session --output /tmp/new-keyboard-geometry.json
```

The output must be a new file. `--source` may select a trusted earlier keyboard
source to verify detection of the original failure. The public-source handoff
was checked on macOS using ordinary tests and source extraction only; this
handoff did not run the native GTK test or interact with a device.

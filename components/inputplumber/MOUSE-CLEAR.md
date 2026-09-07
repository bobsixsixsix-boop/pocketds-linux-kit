# Mouse-button clear candidate

This is an independent source fix for InputPlumber 0.78.1 at
`d3932cb4e0de3eb47688e381b5eb5334ebac6254`. It does not change the deployed
haptics binary or its installer lock. The source candidate lock deliberately has
`binary: null`, `install_ready: false`, and `hardware_validated: false`.

## Bug and fix

In joymouse mode, R1 maps to the left mouse button. Holding R1 while entering
the controller-test page leaves that virtual button down: upstream interception
calls [mouse.clear_state](https://github.com/ShadowBlip/InputPlumber/blob/d3932cb4e0de3eb47688e381b5eb5334ebac6254/src/input/target/mouse.rs#L187),
which clears motion but releases no buttons. A subsequent physical R1 release
is intercepted and never reaches the mouse target.

`inputplumber-d3932cb4-mouse-clear-buttons.patch` retains motion reset and emits
one batch releasing the five buttons advertised by that virtual mouse:
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE and BTN_EXTRA. It does not generate a
press, move the cursor, scroll, change profiles/targets or alter physical input.
The pinned [evdev emit API](https://github.com/emberian/evdev/blob/42b58ee08508b7799322a13bf89121a1d29cf0a2/src/uinput.rs#L257)
appends SYN_REPORT. Emission errors are logged; controller-test independently
checks kernel-visible virtual outputs before allowing restoration to idle.

The controller backend's additional read-only restore check also protects an
older binary: if outputs remain asserted, the gate stays blocked and the
existing stop-post retry remains bounded. It does not fabricate physical key
releases or add a target-rebuild journal. Physical controls must still be
neutral before interception can end.

## Rebuild and verify without a physical controller

Use the Fedora 44 AArch64 dependencies in [README.md](README.md). In a separate
checkout of the exact upstream commit, apply the existing Pulse patch first,
then this mouse patch:

```sh
git checkout --detach d3932cb4e0de3eb47688e381b5eb5334ebac6254
git apply --check /path/to/inputplumber-d3932cb4-pocketds-haptics.patch
git apply /path/to/inputplumber-d3932cb4-pocketds-haptics.patch
git apply --check /path/to/inputplumber-d3932cb4-mouse-clear-buttons.patch
git apply /path/to/inputplumber-d3932cb4-mouse-clear-buttons.patch
cargo test --locked input::target::mouse::button_releases::tests
cargo test --locked dbus::interface::force_feedback::tests::bounds_keyboard_pulse_strength_and_duration -- --exact
cargo test --locked dbus::polkit_test::check_polkit_policies -- --exact
cargo build --locked --release --bin inputplumber --jobs 2
```

The patch creates `src/input/target/mouse/button_releases.rs` from the exact
standalone `mouse-clear-button-releases.rs` retained here. Its real Rust release
builder can also be tested without Linux, D-Bus or uinput:

```sh
rustc --edition=2021 --test mouse-clear-button-releases.rs -o /tmp/mouse-release-tests
/tmp/mouse-release-tests
python3 ../../tests/inputplumber-mouse-clear-contract.py
python3 ../../tests/controller-test-backend.py
```

The candidate lock binds the upstream mouse preimage, resulting source, both
patches and the release builder. A build receipt must record the actual image
digest, compiler/dependency versions, command results and candidate binary hash.
A successful isolated build is not a physical-device validation or deployment.
Keep the existing haptics lock unchanged until a separately reviewed binary
update is deliberately prepared; its old SHA must not be presented as a build
of this new patch.

## Attended hardware validation, not performed by source tests

1. In joymouse mode, hold R1 and open the controller-test page with another
   contact. Confirm the physical R1 remains visible in the test while virtual
   InputPlumber Mouse BTN_LEFT is already zero.
2. Release R1 and exit the page; confirm physical and virtual state are neutral
   before the journal returns idle. Repeat for the right and middle buttons.
3. Confirm ordinary drag, right click, middle click, mouse motion and existing
   gamepad/Pulse behavior still work. Check the error journal rather than
   silently accepting an output-clear failure.

The original reported lower-screen touchpad failure had no mouse target in its
observed live gamepad profile. This independently identified bug is not claimed
as the cause of that incident.

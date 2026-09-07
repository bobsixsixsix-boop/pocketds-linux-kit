# Pocket DS InputPlumber public build

This build pins InputPlumber PR #670 at upstream commit
`d3932cb4e0de3eb47688e381b5eb5334ebac6254` (`0.78.1`). The original Pocket DS
keyboard `Pulse` patch remains byte-identical: intensity is bounded to 0.70 and
duration to 5..50 ms. Generic `Rumble` and the Pocket DS profiles remain unchanged.
A second patch removes the unused USB `deck` target and its `virtual-usb`
dependency. The `deck-uhid` target and its shared configuration and math remain.

The public build uses 343 vendored dependencies. Its locked source and build
receipt are provided with the corresponding Release source supplement. This is
a new binary: its three focused release tests pass, but physical Pocket DS
acceptance of this exact build has not been performed.

## Rebuild on Fedora 44 AArch64

Install `rust`, `cargo`, `rustfmt`, `gcc-c++`, `make`, `clang-devel`,
`systemd-devel`, `libiio-devel`, and `pkgconf-pkg-config`. Apply both patches in
order to the exact upstream commit:

```sh
git clone https://github.com/ShadowBlip/InputPlumber.git inputplumber-d3932cb4
cd inputplumber-d3932cb4
git checkout --detach d3932cb4e0de3eb47688e381b5eb5334ebac6254
git apply /path/to/inputplumber-d3932cb4-pocketds-haptics.patch
git apply /path/to/inputplumber-d3932cb4-remove-unused-usb-deck.patch
cargo test --locked --release dbus::interface::force_feedback::tests::bounds_keyboard_pulse_strength_and_duration -- --exact
cargo test --locked --release dbus::polkit_test::check_polkit_policies -- --exact
cargo build --locked --release --bin inputplumber
```

The source supplement includes the exact patched tree, Cargo vendor directory,
and offline build instructions. Use its recorded compiler and environment for
comparison; the commands above alone do not promise byte-identical output on
other compiler versions.

Verified AArch64 artifact:

- File: `inputplumber-pocketds-pulse-no-usb-deck`
- Size: `9377664` bytes
- SHA-256: `4dbb8a7dc494e27abdbe3b38191dabfaef54caa8f6f2c7357bc84008bbfb488b`
- Additional patch SHA-256: `95f0ea9f100a5283420f0db7f1ffd0b5a11de5dd2e2b80aa35302ef097237abf`

## SD image integration

SD Alpha3 installs this same binary at `/usr/bin/inputplumber` and
`/usr/local/libexec/pocketds-inputplumber-haptics`. The service continues to use
the latter. The image seal records both replaced preimages and the source lock;
the RPM database still records the original package, so it cannot describe the
contents of these overlays by itself. No old executable or cached package is
retained as a rollback payload in that image.

## Sidecar plan, apply and rollback

The separate maintenance helper retains its four-path transaction scope. It
verifies both patches, the binary and the locked configuration payloads. Its
`plan` is read-only:

```sh
python3 scripts/pocketds-inputplumber-haptics.py plan --binary /absolute/path/inputplumber-pocketds-pulse-no-usb-deck
sudo python3 scripts/pocketds-inputplumber-haptics.py apply \
  --binary /absolute/path/inputplumber-pocketds-pulse-no-usb-deck \
  --confirm APPLY-INPUTPLUMBER-HAPTICS-D3932CB4
```

This helper backs up and changes the sidecar, device override, service drop-in
and Pulse policy. It does not replace the system executable or activate a
service. On an Alpha3 image, the existing system executable already contains the
same public build. A transaction rollback restores its recorded preimages;
it does not reinstall the old RPM executable.

```sh
sudo python3 scripts/pocketds-inputplumber-haptics.py rollback \
  --transaction-id YYYYMMDDTHHMMSSZ \
  --confirm ROLLBACK-INPUTPLUMBER-HAPTICS-D3932CB4
```

The helper refuses concurrent or unfinished transactions and rejects target
changes made after apply. Runtime service activation remains a separate operator
action. This source does not incorporate the separate unaccepted mouse-clear
candidate patch.

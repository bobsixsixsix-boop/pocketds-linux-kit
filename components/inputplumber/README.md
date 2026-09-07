# Pocket DS InputPlumber haptics candidate

This sidecar pins InputPlumber PR #670 at upstream commit
`d3932cb4e0de3eb47688e381b5eb5334ebac6254` (`0.78.1`) and applies only the
keyboard-only `Pulse` D-Bus method bounded to 0.70 / 5..50 ms, its polkit
action, XML binding, and boundary test. Generic `Rumble` remains unchanged.
The distro `/usr/bin/inputplumber` is never replaced.

## Rebuild on Fedora 44 AArch64

Install the build tools (the first build used about 4.6 GiB including Cargo
artifacts):

```sh
sudo dnf install rust cargo rustfmt gcc-c++ make clang-devel systemd-devel libiio-devel pkgconf-pkg-config
git clone https://github.com/ShadowBlip/InputPlumber.git inputplumber-d3932cb4
cd inputplumber-d3932cb4
git checkout --detach d3932cb4e0de3eb47688e381b5eb5334ebac6254
git apply --check /path/to/inputplumber-d3932cb4-pocketds-haptics.patch
git apply /path/to/inputplumber-d3932cb4-pocketds-haptics.patch
rustfmt --check --edition 2021 src/dbus/interface/force_feedback.rs
cargo test --locked dbus::interface::force_feedback::tests::bounds_keyboard_pulse_strength_and_duration -- --exact
cargo test --locked dbus::polkit_test::check_polkit_policies -- --exact
cargo build --locked --release --bin inputplumber
```

Verified AArch64 release artifact:

- file: `target/release/inputplumber` (staged as `inputplumber-pocketds-pulse`)
- size: `9574272` bytes
- SHA-256: `2c06a4cbfaa2aa93c923b1dc790bbaf15ae44e048662840cce0705bf2d7df244`
- build ID: `b2034bf875122a8810bb543db0faf583f3942331`

The fixed-50 ms artifact is retained at
`build/inputplumber/d3932cb4-pulse070-fixed50/inputplumber-pocketds-pulse`.
It is `DEPLOYED` at `/usr/local/libexec/pocketds-inputplumber-haptics`; the live
SHA-256 and build ID match the values above. Transaction `20260831Tfixed50`
restores its exact preimage, the `050500a1…f97a9` 0.25 / 40 ms candidate.
Keyboard transaction `feedback-fixed50-20260831` restores the exact keyboard
preimage. The older sidecar transaction `20260830T192143Z` is already
`rolled-back` and is not a current rollback point. An attended calibration
confirmed that 0.30, 0.50 and 0.70 are clearly perceptible and distinct at a
true 50 ms pulse; the same three formal D-Bus Pulse calls succeeded after
deployment and automatically returned input mode to `gamepad`. This is
`SMOKE-PASSED`, not an in-game rumble verification. Fedora rustfmt 1.98 reports
unrelated pre-existing formatting differences across the pinned upstream tree,
so the reproducible gate checks the only changed Rust file instead of rewriting
upstream sources outside this patch.

## Plan, apply, rollback

`plan` is read-only and verifies the actual binary bytes plus every locked
payload. `apply` is the only install path and requires a literal confirmation:

```sh
python3 scripts/pocketds-inputplumber-haptics.py plan --binary /absolute/path/inputplumber-pocketds-pulse
sudo python3 scripts/pocketds-inputplumber-haptics.py apply \
  --binary /absolute/path/inputplumber-pocketds-pulse \
  --confirm APPLY-INPUTPLUMBER-HAPTICS-D3932CB4
```

The helper backs up and changes exactly four paths: the sidecar binary, the
same-name AYANEO device override, its service drop-in, and the Pulse policy.
It does not call `systemctl`, write `hidraw`, or touch `/usr/bin/inputplumber`.
After review, runtime activation is a separate explicit `daemon-reload` and
service restart. A non-blocking global advisory lock serializes `apply` and
`rollback`; a new apply refuses to start while any transaction is prepared,
failed, or applied.

The apply output includes a transaction id. To restore its exact preimages:

```sh
sudo python3 scripts/pocketds-inputplumber-haptics.py rollback \
  --transaction-id YYYYMMDDTHHMMSSZ \
  --confirm ROLLBACK-INPUTPLUMBER-HAPTICS-D3932CB4
```

Rollback refuses to overwrite any target changed after apply. It also leaves
the running service alone; reload/restart explicitly to return to the preserved
`/usr/bin/inputplumber` service. The same rollback command recovers an
interrupted `prepared` or `apply-failed` transaction when every target still
matches either its locked candidate payload or its recorded preimage. The
transaction id is printed immediately after the durable prepared manifest and
is also the directory name under
`/var/lib/pocketds-linux-kit/inputplumber-haptics-transactions/`.

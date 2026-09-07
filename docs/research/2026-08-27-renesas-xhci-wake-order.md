# Renesas xHCI wake ordering

Date: 2026-08-27 (Asia/Tokyo)

Issue: PDS-023.

## Root cause

The original udev rule matched the PCI `add` event and wrote
`power/wakeup=disabled`. On the measured boot, PCI udev initialization completed
at 4.127736 seconds, while `xhci-pci-renesas` probe completed at 4.832175
seconds. The kernel USB PCI probe subsequently calls `device_wakeup_enable()`,
overwriting the earlier userspace policy with `enabled`.

Historical suspend logs also contain this controller returning `-EBUSY` from
`hcd_pci_suspend`. The built-in AYANEO keyboard and controller are children of
this Renesas device; the Goodix touchscreens and the separate platform USB
controller are not.

## Fix

- The udev rule now requires the PCI driver `bind` event, exact vendor/device
  `1912:0014`, and driver `xhci-pci-renesas` before writing the wake attribute.
- Installation runs a small root-only policy helper for the current boot. It
  first requires exactly one matching bound controller and rejects missing,
  ambiguous, wrong-driver, and unexpected-state cases before writing.
- The helper exposes a non-root `--test-root` mode only for fake-sysfs tests;
  root invocation rejects all arguments and always uses real sysfs.

## Verification in this batch

- Fake-sysfs tests cover identity matching, two-device ambiguity, idempotence,
  no partial write on failure, and the bind-rule requirement.
- `udevadm verify` validates the installed rule on the real device.
- The current controller changes from `enabled` to `disabled` without unbinding
  or changing `power/control`; installed input devices remain enumerated.

No reboot or suspend is performed in this batch. PDS-023 remains VERIFICATION
until five cold boots, every physical USB port, built-in keyboard/controller,
touchscreens, and at least thirty controlled deep-suspend cycles pass.

## 2026-08-28 source-bound acceptance ledger

The live rule and helper still byte-match their repository sources:

- rule SHA-256: `9056270bc490bc19cf788c9b06bb3200ad70a77ba10628fa8281d0b6fdaaf370`;
- helper SHA-256: `e65df0b4f7c7ab2e07baa8b3145727b07ba5ee58b6268acbf7f966f48c0c8077`.

`udevadm verify` reports one success and zero failures. The live controller is
still exactly `1912:0014`, class `0c0330`, bound to `xhci-pci-renesas`, with
`power/wakeup=disabled` and `power/control=on`. Its USB2 root has four ports;
port 1 exposes AYANEO's two-interface internal keyboard and port 2 the internal
controller. Its USB3 root is currently empty. AYANEO's official Pocket DS
description specifies one full-function USB-C port, so physical acceptance
tests that single connector with both a USB2 and a USB3 peripheral rather than
inventing extra external ports:
<https://store.ayaneo.com/article/892>.

`scripts/pds023-usb-wake-evaluate.py` now provides a private source-bound
supervised ledger. It requires all of the following before PASS:

- at least five distinct cold boots, with the exact driver bound and wake
  disabled both before and after each observation;
- at least thirty successful RTC-rescued deep cycles, wake disabled before and
  after every cycle, and zero Renesas `-EBUSY` or other xHCI error observations;
- at least 50 internal-keyboard and 50 internal-gamepad actions, 20 actions on
  each touchscreen, and five USB2 plus five USB3 enumeration cycles on the
  physical USB-C port, all with zero failure;
- explicit human confirmation and exact repo/helper/rule hash binding.

The evaluator has no sysfs, power, USB, service, process, or network path. It
only creates a private 0600 no-clobber template/report or writes a summary to
stdout. Unknown fields, impossible counts, weak types, duplicate/non-finite
JSON, public/link inputs, source drift, and incomplete sections fail closed.
The helper mock now additionally covers wrong driver, unbound device,
unexpected wake state, and the exact non-executable bind rule.

Implementation verification: local commit `987e781efa46c738cfb6d7d0f0597dd44a8c3a6b`,
device commit `904a70a0b05fd2067615034b56b45713a599c283`, exact shared tree
`f38f703ee3d589887571fb28a02036414a524b45`; 11 focused tests, helper/rule mock,
lint, and full local/device test suites pass. No cold boot, USB insertion, input
action, or suspend was performed here, so the supervised ledger remains
**NOT RUN / INCOMPLETE**.

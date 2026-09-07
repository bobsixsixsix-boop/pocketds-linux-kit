# PDS-017 SM8550 battery charge-unit candidate

This directory contains a source-only, default-disabled candidate for the
missing `charge_full` and `charge_full_design` values on Pocket DS. It is not
installed by any project target.

## Root cause

Pocket DS selects the SM8550 variant of `qcom_battmgr`. That variant uses the
SM8350-style property protocol. The protocol callback never receives the
SC8280XP-style `BATTMGR_BAT_INFO` message that initializes `battmgr->unit`, so
the zero-allocated field remains `QCOM_BATTMGR_UNIT_mWh`. The registered
`CHARGE_FULL*` properties consequently return `-ENODATA` before returning the
firmware values.

The matching Qualcomm Android protocol driver maps `BATT_CHG_FULL_DESIGN` and
`BATT_CHG_FULL` to the charge properties and returns their values unchanged.
The candidate therefore initializes only the SM8350-style protocol branch to
`QCOM_BATTMGR_UNIT_mAh`. It does not invent a design capacity, convert charge
to energy, or expose the unverified `ENERGY_*` properties.

## UPower boundary

The installed UPower `1.91.3-1.fc44` source contract is pinned in the manifest.
Once `charge_full*` becomes readable, it selects charge units, converts full
charge and current using a voltage, and infers missing `charge_now` from the
kernel percentage. Pocket DS exposes neither `voltage_max_design` nor
`voltage_min_design`, so this UPower version falls back to the instantaneous
`voltage_now`. That should make non-zero energy and ETA possible, but it can
also make the reported `EnergyFull` move with battery voltage. The kernel
candidate therefore does not by itself close the UPower or ETA acceptance
gate.

## Safety boundary

- Keep this as an independent kernel experiment. Do not add it to an IFPC
  candidate or claim that a boot with both changes validates either one.
- The patch applies to the pinned downstream driver. Its driver object and the
  full `Image`/modules/DTB target set compile natively on isolated Fedora
  44/aarch64. The running config's RPM-buildroot-only initramfs path had to be
  cleared, and compiler metadata was refreshed; exact deltas and hashes are in
  the manifest. This is a compile result, not an installable release artifact.
- The built kernel has not been booted or runtime-tested.
- A real boot requires the same user-present independent recovery path as any
  other Pocket DS kernel candidate.
- Until runtime validation passes, Panel and UPower must continue displaying an
  unknown ETA instead of deriving capacity from the model name or
  `charge_counter`.

## Runtime acceptance

After packaging the candidate through the locked release path and performing a
recoverable user-present boot:

1. `charge_full` and `charge_full_design` must both be readable, positive, and
   plausible as microamp-hours; `energy_full*` must not appear from this patch.
2. The reported full/design values must remain stable across repeated reads,
   charger transitions, and suspend/resume.
3. At a supervised full charge, compare `charge_counter`, `charge_full`, and
   the pack label/model without hard-coding any of them as truth.
4. Verify UPower's EnergyFull/percentage/ETA behavior during controlled charge
   and discharge. Track `voltage_now` and reject the candidate if units are off
   by 1000, if `EnergyFull` drift is unacceptable, or if ETA is unstable.
5. Restore the baseline kernel and confirm the old `ENODATA` behavior before
   attributing any difference to this single variable.

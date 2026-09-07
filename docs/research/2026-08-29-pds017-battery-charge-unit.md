# PDS-017 SM8550 battery capacity unit audit

## Result

The missing Pocket DS `charge_full` values are caused by an uninitialized unit
in the Linux `qcom_battmgr` SM8350-style property path, not by absent battery
hardware and not by UPower.

The live Pocket DS battery exposes `charge_full` and `charge_full_design`, but
both reads return `ENODATA`. `energy_full*` is absent. The same read-only sample
reported `charge_counter=6641000`, `capacity=100`, model
`GRAPH_6000MAH_60W`, and the exact kernel
`7.1.0-100.20260730172655.pocketds.fc44.aarch64`. These observations are not a
license to use the counter or model string as full capacity.

## Source chain

The exact downstream source is commit
`a4975ce7c8eec079bd0cf7ec3890e100e016813f`, driver SHA-256
`9315d81b…`. Current upstream commit
`1b78070aaef63512688aebfbc82365ef9d6660f1`, driver SHA-256
`9591a420…`, has the same relevant behavior:

- SM8350/SM8550 register `CHARGE_FULL_DESIGN` and `CHARGE_FULL`;
- their update path requests `BATT_CHG_FULL_DESIGN` and `BATT_CHG_FULL`;
- the getter returns those fields only when `battmgr->unit` is mAh;
- only the SC8280XP-style `BATTMGR_BAT_INFO` callback assigns
  `battmgr->unit`;
- the SM8350-style probe branch leaves the zero-allocated enum at mWh.

The pinned Qualcomm Android SM8550 protocol implementation at LineageOS
commit `095667a874b8f2218347ec9dd87f58c9e1175359`, SHA-256 `2434dd35…`, maps
the two firmware properties directly to `POWER_SUPPLY_PROP_CHARGE_FULL*` and
returns non-special values unchanged. This rejects the tempting alternative
of relabelling them as `ENERGY_FULL*`, which could introduce a voltage-factor
unit error.

Primary references:

- <https://gitlab.com/linux-pocketds/linux/-/blob/a4975ce7c8eec079bd0cf7ec3890e100e016813f/drivers/power/supply/qcom_battmgr.c>
- <https://github.com/torvalds/linux/blob/1b78070aaef63512688aebfbc82365ef9d6660f1/drivers/power/supply/qcom_battmgr.c>
- <https://github.com/LineageOS/android_kernel_qcom_sm8550/blob/095667a874b8f2218347ec9dd87f58c9e1175359/drivers/power/supply/qti_battery_charger.c>

## Installed UPower path

The device runs `upower-1.91.3-1.fc44.aarch64`. Official tag `v1.91.3`, commit
`3dc5323bf9f8f84b0a038f5f36c34d49e480ee3c`, confirms that when
`energy_full` is unavailable it reads `charge_full*`, selects charge units,
and converts charge/current to energy with `voltage_design`. With
`charge_now` absent it later infers current energy from `EnergyFull ×
percentage` before calculating ETA from energy/rate.

Pocket DS exposes neither design-voltage attribute, so UPower selects the
instantaneous `voltage_now` instead. The current read-only sample was
4,374,309 µV. This is enough to explain how the kernel candidate can unlock
non-zero values, but it is also a residual accuracy risk: a refresh can change
the conversion voltage as the pack discharges. Runtime acceptance must record
voltage and `EnergyFull` together and reject material drift rather than treating
any non-zero value as success.

Primary references:

- <https://gitlab.freedesktop.org/upower/upower/-/blob/v1.91.3/src/linux/up-device-supply-battery.c>
- <https://gitlab.freedesktop.org/upower/upower/-/blob/v1.91.3/src/up-device-battery.c>

## Candidate and boundary

The source-only patch initializes `battmgr->unit` to mAh only in the
SM8350-style property-protocol branch. It applies cleanly to the pinned
downstream driver and changes no other source line. Isolated Fedora 44/aarch64
first compiled `qcom_battmgr.o` successfully as an ARM64 ELF object with
SHA-256 `5e95e440…`, then completed `Image`, `modules`, and `dtbs`:

- Image SHA-256 `a0ec4394…`, 31,304,192 bytes;
- 319 modules, aggregate SHA-256 `44afe9f2…`;
- candidate module SHA-256 `79e6a5e4…`, ARM64, vermagic `7.1.0-rc2 SMP
  preempt mod_unload aarch64`;
- 417 DTB/DTBO files; Pocket DS DTB SHA-256 `0557cee1…`;
- vmlinux build ID `b93cb1c1…` and candidate module build ID `ff78dc50…`.

The running config names an initramfs file inside Fedora's original RPM
buildroot. That input is unavailable in the standalone source archive, so the
full-build config explicitly clears `CONFIG_INITRAMFS_SOURCE`; `olddefconfig`
also refreshes GCC 16.1.1 metadata to the available GCC 16.2.1 and removes the
now-inapplicable initramfs UID/GID/compression selections. The manifest locks
the resulting config and every relevant output digest. Consequently this is a
full source compile gate, not a byte-reproducible package or bootable release
artifact.

This is an independent PDS-017 variable. It must not be combined with the
PDS-002 IFPC revert during causal validation. Packaging and a real boot still
inherit the project's user-present recovery requirement. Until sysfs, UPower
conversion drift, and ETA are checked across controlled charge/discharge and
suspend/resume, ETA remains unknown.

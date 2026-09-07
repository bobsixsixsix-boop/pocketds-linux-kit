# 2026-09-05 TSENS early-wake investigation (read-only)

Follow-up: the subsequent explicitly authorized repair is recorded separately
in [the orphan-trip candidate experiment](2026-09-05-orphan-trip-candidate.md).
The scope/statements below describe this earlier read-only turn, not the later
implementation or its test result.

## Scope and conclusion

The user asked to investigate the 5.365-second early wake. This follow-up
performed source/history/log inspection and read-only live checks on the
restored 20260717 baseline. No suspend, reboot, kernel/DT change, IRQ change,
trip-temperature write, fan-profile change or dynamic-debug enable occurred.
The current boot still has zero successful/failed suspend attempts and an
empty RTC alarm; `AllowSuspend=no` remains in force.

The strongest concrete lead is **orphaned fan trip points combined with an
always-wake-capable TSENS upper/lower IRQ**. The configuration and mechanism
are verified; attribution of the old single wake to one exact sensor/trip
remains unproven because the recorded log lacks the first IRQ's sensor status.

## Evidence chain

1. The earlier diagnostic boot logged `Timekeeping suspended for 5.365 seconds`
   followed by `Triggering wakeup from IRQ 23`. This was the actual deep cycle,
   not the separate `pm_test=devices` five-second debug wait. The real wrapper
   asserted `pm_test=none` and armed RTC with 61 seconds remaining.
2. That boot's interrupt inventory maps IRQ 23 to `c271000.thermal-sensor`,
   GIC hardware IRQ 538. Its exact SM8550 DT names SPI 506 (538 with the SPI
   offset) `uplow`. TSENS0 sensors 1, 2, 3 and 4 map to `cpuss0-thermal` through
   `cpuss3-thermal`. IRQ numbers are boot-local: do not hard-code 23 in a fix.
3. Each of those four zones retains seven `cpussN_fan*` passive trips at
   40, 50, 60, 65, 70, 75 and 80 C, each with 3 C hysteresis. The exact candidate
   `qcs8550-ayaneo-pocket-common.dtsi` contains these but no cooling-maps.
   On the current original kernel, all four zones expose the same trips and
   have no `cdev*_trip_point` bindings. Their temperatures at one follow-up
   sample were approximately 52 C; this is **not** a measurement during the
   earlier suspend.
4. These declarations were already present in the retained original Pocket DS
   adaptation patch 29/46, commit `37a06ec1c120e700df4ec60b0f6c7fb647721b6f`
   dated 2026-05-11. They were not introduced by today's ST7703 config change.
   The repository's fan-controller comments independently describe removal
   of kernel fan cooling-maps in favor of the standalone PWM controller.
5. `thermal_zone_handle_trips()` selects the surrounding temperature window
   regardless of whether a trip has a cooling-device binding. Thus an orphan
   trip still affects hardware thresholds. A crossed trip's downward
   threshold uses temperature minus hysteresis; around the 50 C trip this is
   approximately 47 C (the exact hardware boundary also reflects milli-degree
   subtraction and 0.1 C register conversion).
6. `tsens_set_trips()` writes both LOWER and UPPER thresholds and enables both
   interrupt sources. `tsens_register_irq()` calls `enable_irq_wake()` for
   upper/lower and critical IRQs. The `qcom,tsens-v2` implementation selected
   here has a resume callback but no suspend callback to replace the normal
   temperature window. Thermal-core suspend preparation stops polling and
   marks zones suspended; it does not clear these hardware trip windows.

This establishes a path by which normal heating **or cooling** across an old
fan boundary can wake the system. Cooling through the approximately 47 C
boundary in one of TSENS0 sensors 1--4 is a plausible leading hypothesis,
not a recovered fact about the old 5.365-second event. Nothing proves a fixed
five-second timer, overheating, or a hardware defect.

## Important safety correction

Do not equate the IRQ named `critical` with all Linux overtemperature
protection. In this driver, the upper/lower handler updates the thermal core,
whose trip path also handles HOT/CRITICAL conditions. The separately named
critical handler masks critical violations with the comment that they are
unused on Linux; its existence is not proof of a replacement shutdown path.
The current system also exposes 110 C critical trips in individual CPU/GPU
zones. They must not be lost or disabled to suppress ordinary threshold wakes.

Consequently, the earlier suggestion to preserve critical wake is necessary
but insufficient as a safety specification. Disabling IRQ 23, disabling all
TSENS wakeups, changing every zone to disabled, or wholesale application of
automotive/Android suspend patches is not an accepted fix.

## Narrow next investigation / candidate (not implemented)

- First capture TSENS0 per-sensor upper/lower status, masks, programmed window
  and temperature at the first post-sleep IRQ, before thermal-core updates
  can overwrite that evidence. The existing dynamic-debug messages in
  `tsens_read_irq_state()` / `tsens_set_trips()` help, but the uplow handler
  does not itself call `tsens_read_irq_state()` before the update, so they are
  not a guaranteed substitute for targeted first-IRQ instrumentation.
- Confirm the offending sensor and direction in a separately authorized,
  guarded, single diagnostic RAM-boot test. Do not silently run another cycle
  as part of this read-only investigation.
- If the orphan fan trips are confirmed, the smallest candidate to review is
  removal of just the unbound `cpussN_fan0..6` declarations (28 total), keeping
  the standalone fan controller and all existing thermal safety trips,
  cooling bindings and wake lines. Review/compare the compiled DT, not just
  source labels. This candidate has not been built, deployed or validated.
- If another source is identified, pursue it instead; removing unused fan
  trips is not a guarantee against every kind of early wake.

## Source identities and primary references

Exact diagnostic-tree file hashes inspected:

- `drivers/thermal/qcom/tsens.c`:
  `3c50145ee28403cb45dd00e86b4d72a6b11e1d82a84c993ea7b99e722fff98a8`.
- `arch/arm64/boot/dts/qcom/qcs8550-ayaneo-pocket-common.dtsi`:
  `350cacc75d42afeecf35e9944615f0bc2cbe420d128b6e33778a08f8844d2d20`.

General code behavior was cross-checked against the upstream
[TSENS source](https://raw.githubusercontent.com/gregkh/linux/v7.1.12/drivers/thermal/qcom/tsens.c)
and [thermal core](https://raw.githubusercontent.com/gregkh/linux/v7.1.12/drivers/thermal/thermal_core.c).
These references explain the mechanism; the retained local candidate files
and runtime inventory determine this device's actual configuration.

The board author's [v4 submission](https://patchew.org/linux/20260722-pocketds-v4-0-438704cd51f7@gmail.com/)
also reworks the earlier fan-trip design into a single active trip with a real
cooling-map. That series explicitly defers DSI support, so it is context, not
a drop-in replacement for the functioning downstream Pocket DS device tree.

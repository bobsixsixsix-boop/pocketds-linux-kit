# ICNA35xx brightness mode restoration candidate — 2026-09-07

Status: prepared and checked on a local source copy only. Not compiled,
installed, booted, or established as a physical upper-panel resume fix.
The clean Hall-03 comparison must be evaluated separately.

## Scope

`patches/0005-panel-icna35xx-preserve-backlight-mode-flags.patch` changes only
`icna35xx_bl_update_status()` and `icna35xx_bl_get_brightness()`:

- Save `dsi->mode_flags` on entry.
- Retain the existing high-speed brightness transfer.
- Restore the saved flags immediately after the transfer, before every
  success/error return.

The old error path left `MIPI_DSI_MODE_LPM` clear. The old success path
always set it, including when it had been clear on entry. The candidate
fixes these local state-restoration errors. It adds no panel delay,
power-cycle, output enable fallback, or change to the brightness value.

## Exact baseline and build provenance

The observed device build directory is
`/home/pocketds/pds001-kernel-build-input/linux-7.1.12-dsc-v4`.
It is an extracted build tree, **not a Git repository**, so there is no
verified kernel-tree Git HEAD. The maintenance repository HEAD when this
candidate was prepared was `f2e9e9e3a07097107e6bde01734753c76fba9ca2`.

| Item | SHA-256 / value |
| --- | --- |
| Original `drivers/gpu/drm/panel/panel-chipone-icna35xx.c` | `5bcc7e02037a6dfd2389857c77b5daf1199409684e00800ba23b8d9d1bab32b7` |
| Candidate panel source | `2ec0c5a5c66aa27e6ee4a5f0aafba533268692065c944ac90e075c33f01078d2` |
| `.config` | `fe66e634d06015d7494ac657bfc96655222bdf56998db4b03f317371beccd51c` |
| v4 raw `Image` | `37c5d65c381aa434cdfc43dbe8923ebe8525b6dd85818b41da5e003974a5b653` |
| v4 kernel notes | `05ffb67550d15176df5302978a51cf1834fa70869e663f8e01c440c253116092` |
| Native compiler | `gcc (GCC) 16.2.1 20260819 (Red Hat 16.2.1-2)` |
| Linker / make | GNU ld `2.46.1-1.fc44` / GNU Make `4.4.1` |
| Generated release | `7.1.12-pdsdiag.20260905.aarch64` |
| Generated build version | `#4 SMP PREEMPT Sat Sep 5 08:00:00 UTC 2026` |

The existing build entry point is `tools/kernel-ab/build-dsc-candidate.py`.
It records the native aarch64 invocation `taskset -c 0-2 nice -n 10 make
-j3 LOCALVERSION=-pdsdiag.20260905.aarch64 Image` and build version/timestamp
environment. **Do not run it for this candidate**: its source checks,
directory, output identity, and build version are specifically v4.
A future build requires a distinct isolated copy, candidate identity, and
updated verification; the historical v4 tree must remain unchanged.

The exact panel object compiler flags are preserved in the v4 tree at
`drivers/gpu/drm/panel/.panel-chipone-icna35xx.o.cmd` (SHA-256
`38fe8962d3882fe5eb9d803185d6e3ac74cb5010deb9a0cdaf6ca492953a1cc3`).

## Why upper `actual_brightness` is not passive observation

The following line references are from the inspected device v4 tree:

1. `drivers/video/backlight/backlight.c:244–260` calls the panel's
   `get_brightness` callback for the sysfs attribute.
2. `drivers/gpu/drm/panel/panel-chipone-icna35xx.c:505–519` clears LPM
   and calls `mipi_dsi_dcs_get_display_brightness_large()`.
3. `drivers/gpu/drm/drm_mipi_dsi.c:1521–1539` performs a two-byte DCS read.
4. `drivers/gpu/drm/msm/dsi/dsi_host.c:2197–2213` precedes this with
   `MIPI_DSI_SET_MAXIMUM_RETURN_PACKET_SIZE` (`0x37`). The host DMA wait
   at `1437–1468` has a 200 ms timeout.

An off-state `-EINVAL` can also be the early `!power_on` rejection at
`dsi_host.c:1764–1778`, before a DMA transaction. In both cases the old
panel callback has already cleared LPM and returns without restoring it.
When a transfer does proceed, host prepare/restore changes clocks,
transmit mode, DSI control, and DMA interrupts (`2109–2159`).

Thus the 100 ms upper `actual_brightness` polling in Hall-02 is a material
observer effect, consistent with its `0x37` errors. That establishes a
confounding input, not proof that polling caused the physical black screen.
Use cached `brightness` / `bl_power` for the clean comparison. The lower
SY7758 panel has no `get_brightness` callback and does not share this active
read path.

## Concurrency and a possible closed-panel guard

This minimal patch does **not** establish a common serialization boundary:

- Sysfs actual reads and brightness writes use `ops_lock`
  (`backlight.c:186–207,244–260`).
- `backlight_update_status()` takes `update_lock`, not `ops_lock`
  (`include/linux/backlight.h:299–309`). DRM calls `backlight_enable()` /
  `backlight_disable()` directly. Consequently a sysfs getter can overlap
  a DRM-driven update.
- Panel prepare/disable/unprepare have no driver-owned mutex. The DSI host
  command mutex covers each transfer, but reading/changing `mode_flags`
  happens before that mutex. It does not serialize an entire init sequence.

For example, two callbacks can save different LPM states while one has
temporarily cleared it, and restore in the opposite order. The local
save/restore patch alone cannot eliminate that interleaving. It must not
be presented as making high-frequency reads during transitions safe.

Do not borrow the backlight core's `ops_lock` / `update_lock`: they are
documented internal locks. Do not acquire `panel->follower_lock` in the
brightness callbacks either: DRM holds it while invoking backlight
enable/disable (`drm_panel.c:216–253,266–313`), causing recursive deadlock.
Checking `panel->prepared` alone is also insufficient; the core changes it
after the driver's unprepare has removed power (`drm_panel.c:160–203`).

If the clean comparison or subsequent stress test justifies a broader
candidate, use a **driver-owned mutex and command-ready state** shared by
prepare, disable, unprepare, get, and update. Set readiness only after
successful initialization, clear it before the shutdown sequence, and
hold the same mutex across each relevant DSI sequence and state change.
Review the complete call graph before implementation; driver callbacks
must not recursively call the backlight core while holding this mutex.

With that serialization, an off/blank getter can return zero without DSI
I/O. Off-state updates can retain the requested value already stored in
`bl->props` and defer the write until the normal enable update. A deliberate
negative errno for an unavailable physical read is possible, but existing
sysfs/UI consumers will see a failure; it is not needed merely to report a
known blank panel. Dropping `get_brightness` entirely makes the core return
the requested cached brightness, which can be nonzero while blank, so it
must not be represented as a verified physical brightness reading.

## Offline verification

The local `q6apm-build-input/linux-7.1-rc2.tar.gz` and
`pds002-copr/linux-a4975ce7.tar.gz` archives both contain a panel file with
the exact inspected v4 SHA-256 above. Only that matching panel source was
used as the offline baseline; this does not assert the entire archives
are equivalent to v4.

On a temporary local copy, `git apply --check --whitespace=error` passed,
application produced the candidate source hash above, and reverse
application restored the baseline byte-for-byte. No compiler, build
helper, installer, or device operation was run for this candidate.

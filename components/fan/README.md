# Daily-device fan override

`pocketds-fancontrol-connected-idle.py` is the Pocket DS daily-device fan
controller. It keeps the existing acoustic curves, then stops the blower only
when both fixed internal backlights are off and the compute hotspot is at most
50 C for two consecutive samples. It resumes immediately when either display
returns, a reading fails, or the hotspot reaches 60 C. The existing 84/88/95 C
safety floors always apply to the hardware PWM.

Install it as
`/usr/local/libexec/pocketds/pocketds-fancontrol-connected-idle` and install
`pocketds-fancontrol-connected-idle.conf` as
`/etc/systemd/system/pocketds-fancontrol.service.d/20-connected-idle.conf`.
The drop-in changes only the service executable; the vendor unit, runtime
directory, profile handling and package ownership remain intact.

The historical `packaging/pocketds-userspace/pocketds-fancontrol.pds1` and its
source/build/A-B locks intentionally remain unchanged. Those locks describe an
already-built release candidate and must not be rewritten without rebuilding
and re-auditing that artifact. This local override is therefore isolated from
the deferred firmware/release path.

Rollback removes the drop-in and local executable, reloads systemd, and
restarts `pocketds-fancontrol.service` on the package-owned controller.

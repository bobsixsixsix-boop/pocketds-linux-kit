# 2026-09-06 repository audit repairs

Base: `14a4cfb0ba3ef5b62b9e838e52d42fd642c6f393`.
Initial scope: source repairs and isolated regression tests; that stage made no
device changes. The user subsequently authorized deployment and remote push.
See [device activation and backups](2026-09-06-audit-deployment.md) for the
separate installed-state evidence; no OS switch, suspend, kernel change or
package update was performed during deployment.

| Audit ID | Resulting behavior | Regression |
|---|---|---|
| A01 | Panel reads the verified BootMode result independently of the reboot exit code. Partial success keeps the Android target visible and permits retry; unknown results never claim the boot record is unchanged. | `tests/boot-switch-result.py` executes the production dispatcher with temporary callees and the actual QML result function using Node.js. |
| A02 | The 250 ms wake poll retains periodic full maintenance; only lit displays may have preferences restored. Lower-screen raw brightness is reblanked if overwritten while off. | `tests/brightness-daemon.py` runs the production loop with a simulated clock, including both-off and power-change-before-write cases. |
| A03 | Changed service files are journaled before copying. Affected services restart after deployment; unchanged successful reinstalls start without unnecessary restarts. An interrupted install retains activation debt for the next retry. Speaker filter configuration is activated with its dependent service. | `tests/install-service-upgrade.py` uses real temporary files and a service model where start preserves an already running generation. |
| A04 | Managed file skip checks include type, mode and owner/group. Permission drift is repaired; symbolic-link/non-regular targets fail without following them. | Same-content missing-execute/excess-permission and symlink cases in `tests/install-service-upgrade.py`. Root calls use a privilege substitute in these tests. |
| A05 | Default DNF exclusions are generated from all three evaluator protected groups, including systemd/dracut/fwupd/boot infrastructure (39 ordered patterns). These packages require the hardware/boot upgrade path, even as dependencies. | `scripts/render-update-protection.py` and `tests/update-policy.py` compare the generated file and representative protected/application packages. |
| A06 | Failed daily-policy changes restore preimages and prior service/capability state. Opt-in is withdrawn before rollback; incomplete restoration returns a distinct error and attempts to keep opt-in absent. A failed disable restores the previous policy and explicitly reports failure. | `tests/daily-suspend-install-transaction.py` injects failures across writes, authorization, service activation and capability confirmation, with absent/existing preimages and a rollback-failure case. |
| A07 | Old events update cached keys/axes without earning activity credit. A fresh press after a stale release becomes a valid sustained hold; overflow still requires resynchronization. | Production readable/controls/held behavior in `tests/gamepad-activity.py`. |

The generic installer owns
`~/.local/state/pocketds-linux-kit/pending-user-service-files`. It is cleared
only after all affected service activations and active checks succeed. Do not
remove it just to silence a failed installation; rerun the installer after
resolving the activation error. The existing keyboard transaction retains its
own activation ownership.

`make test` includes the new regressions. Boot-result JavaScript execution
requires Node.js; a missing interpreter is reported as skipped, not a pass.
The Fedora validation environment supplies it in an isolated dependency tree.
Hardware-only service health and the externally supplied full battery-driver
source fixture remain separate acceptance boundaries.

Documentation now separates the restored Android state from the still-pending
two-way switch acceptance, and distinguishes the current single Image target
from historical multiple-alias installation procedures. The known Hall-wake
display failure, closed-lid darkness and physical controller-only idle behavior
remain subject to real-device acceptance; these source repairs do not close
those items.

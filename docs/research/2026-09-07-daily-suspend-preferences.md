# Preserve PowerDevil preferences when changing sleep capability

`scripts/install-daily-suspend.sh` previously replaced the entire user
`powerdevilrc` with a repository preset on both enable and disable. This could
change an existing power button from screen-off (`64`) to sleep (`1`), enable
idle sleep despite `AutoSuspendAction=0`, and reset unrelated display settings.

The installer now uses the existing KConfig tools for narrow updates, verifies
every written value, and keeps the original whole-file backup for rollback:

- Enable sets only `SleepMode=1` in AC, Battery and LowBattery. Existing lid,
  power-button, power-down, idle, display, timeout and other settings survive.
- A missing `LidAction` or `AutoSuspendAction` has a capability-dependent KDE
  default. Before enabling capability, the installer materializes its previous
  effective default: with CanSuspend false, lid is screen-off (`64`) and idle
  action is none (`0`); with CanSuspend true, both default to sleep (`1`) on this
  physical device. This avoids unexpectedly enabling automatic sleep merely
  because a key was absent. Explicit values are never replaced on enable.
- Disable withdraws the opt-in first, then changes only sleep (`1`) entries
  among LidAction, PowerButtonAction, PowerDownAction and AutoSuspendAction to
  none (`0`). It retains other actions and SleepMode. Hibernate (`2`) is a
  separate preference; it is left intact and remains subject to the existing
  platform policy, which does not authorize disk hibernation.
- Both paths retain the original failure rollback, including absent helper
  files, root policy files, user configuration bytes/permissions and capability
  verification. KConfig read, write, and read-back failures also enter rollback.

The enabled sleep mode is ordinary suspend to RAM, not hibernation. KWin's
display policy and physical wake acceptance are separate; this configuration
change neither proves Hall wake works nor changes the guard's kernel identity.

Official PowerDevil 6.7.3 sources:

- [PowerDevil enums](https://github.com/KDE/powerdevil/blob/v6.7.3/daemon/powerdevilenums.h):
  `SleepMode::SuspendToRam=1`, HybridSuspend=2, SuspendThenHibernate=3;
  PowerButtonAction Sleep=1, Hibernate=2, TurnOffScreen=64.
- [SuspendSession](https://github.com/KDE/powerdevil/blob/v6.7.3/daemon/actions/bundled/suspendsession.cpp):
  the selected SleepMode controls RAM, hybrid, or suspend-then-hibernate when
  the configured action is Sleep.
- [Profile defaults](https://github.com/KDE/powerdevil/blob/v6.7.3/daemon/powerdevilsettingsdefaults.cpp)
  and [profile schema](https://github.com/KDE/powerdevil/blob/v6.7.3/PowerDevilProfileSettings.kcfg):
  omitted lid and automatic-sleep actions depend on CanSuspend; power-button
  and power-down defaults do not.

The fixture matrix executes the real shell transaction with mocked hardware,
services and KConfig tools. It covers preservation of explicit preferences,
missing defaults, every changed profile/key, read/write/read-back failures,
root mutation failures, service/capability failures and incomplete rollback.
It does not modify the device or enable sleep.

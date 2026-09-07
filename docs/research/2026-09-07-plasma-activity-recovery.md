# Plasma activity state and desktop recovery — 2026-09-07

Status: the existing desktop icons and full lower Panel were restored by selecting
the existing KDE activity, then running the existing bounded Plasma recovery.
No output layout, applet placement, Panel design or activity list was changed.
This incident does not establish a suspend-induced desktop regression.

## Evidence before recovery

The report followed Hall-04 on boot `db67a328-d3a0-43d3-8573-bf803dfdee20`, with
suspend counters 1/0. The user first confirmed both screens and touch working,
then reported missing desktop icons and the lower Panel's old edge gaps.

- KWin still reported both internal outputs enabled, DPMS on and the expected
  modes, rotation, scales and lower position `(283,720)`. The lower logical
  extent remained 819×614.
- The activity service listed exactly one existing activity,
  `90559774-0de3-4310-b361-b4f32b543519`, but `CurrentActivity` returned empty.
- `~/.local/state/kactivitymanagerdstaterc`, group `main`, still selected
  `80e4c2f3-ca81-43e7-b5d2-46bd3fe4ce7a`, which was absent from the activity list.
  Its recorded mtime was **01:32:38 JST**, before this boot's desktop startup
  at **02:49:51 JST**. The activity definitions live separately in
  `~/.config/kactivitymanagerdrc`.
- Desktop containments 1 and 2 retained the existing activity and `lastScreen`
  0/1, but both live `screen` properties returned -1. The activity-independent
  taskbar containment 3 still returned screen 0.
- FolderView recorded 13 launcher items on disabled screen 0. No launcher names
  or file contents are included here.
- Panel applet 28 remained in containment 2 at 816×608. Its installed QML still
  contained the existing `screenGeometry` surface extension; loss of the desktop
  screen association prevented that extension from providing the missing edge
  coverage. This was not an older Panel QML deployment.
- Plasma PID1603, KWin PID1455 and ActivityManager PID1626 had run since startup
  with zero restarts. Plasma logged two `unexisting screen available rect -1`
  messages at 02:49:52, before the attended sleep cycle.

## Why these symptoms share a cause

In the installed-version upstream implementation, activity startup uses the saved
ID whenever it is nonempty; only an empty saved value falls back to an existing
activity. Selecting an unknown ID returns false, leaving the runtime current
activity empty. A successful selection saves the ID to the state file and emits
the activity-change notification. See [KDE v6.7.3 Activities.cpp, lines 85–128](https://github.com/KDE/kactivitymanagerd/blob/v6.7.3/src/service/Activities.cpp#L85).

Plasma's desktop screen lookup requires the containment activity to match the
current activity. Otherwise it returns -1; the panel-view branch has no such
activity condition. This explains the two orphaned desktops and surviving
taskbar. See [ShellCorona::screenForContainment](https://github.com/KDE/plasma-workspace/blob/v6.7.3/shell/shellcorona.cpp#L2512).

Changing back to the valid activity corrected the live getters and saved state,
but initially did not correct the rendered view. ShellCorona updates desktop
views by assigning their containments. When that object is already assigned,
libplasma returns immediately and skips screen-change and size setup. This is
consistent with the retained FolderView and Panel bindings in this incident;
it does not imply the saved state change failed. See
[activity-change handling](https://github.com/KDE/plasma-workspace/blob/v6.7.3/shell/shellcorona.cpp#L1934)
and [ContainmentView's same-object early return](https://github.com/KDE/libplasma/blob/v6.7.3/src/plasmaquick/containmentview.cpp#L53).

FolderView restores items from its disabled-screen map when the matching screen
and activity are registered again. Reconstructing the views with the corrected
activity allows that normal restoration; manually deleting the mapping is
unnecessary. See [ScreenMapper::addScreen](https://github.com/KDE/plasma-desktop/blob/v6.7.3/containments/desktop/plugins/folder/screenmapper.cpp#L118).

## Executed recovery and verification

1. The operator invoked ActivityManager's native `SetCurrentActivity` with the one existing activity
   ID. The call returned true. Both runtime `CurrentActivity` and persistent
   `currentActivity` then matched that ID; desktop screen getters became 0/1.
   The intermediate screenshot `hall-04-ui-activity-restored.png` still showed
   the missing icons and Panel gaps, so this step alone was not visual acceptance.
2. The operator used the already-installed `pocketds-plasma-recovery` bounded
   recovery path, which queues an independent worker and performs at most one
   Plasma stop/start sequence. It does not alter the output layout or rebuild
   applets. Plasma PID1603 became PID11295; keyboard PID1799 and GPU telemetry
   PID1373 remained unchanged.
3. Visual review of `hall-04-ui-shell-restored.png` confirmed all 13 desktop
   icons and complete lower Panel coverage. The persistent current activity
   remained correct. No further suspend was needed for this repair.

The two steps are conditional recovery, not a startup recipe: first verify the
saved/current/listed activity mismatch, select an existing activity through KDE,
then use bounded shell recovery only if the rendered desktop remains stale.
Never substitute a hardcoded activity ID on a different installation.

Private evidence is retained with the session's `outputs/hall-04-ui-readonly/`
JSON reports and `outputs/sleep-completion-20260907/hall-04-ui*.png` screenshots.
The attended kernel receipt remains `/run/pds-lid-cycle-20260907-hall-04`.
The investigation did not read upper `actual_brightness`, which performs DSI I/O.

## Write-source audit and maintenance decision

A search of the current repository and its available Git history found no
references to either activity ID, `kactivitymanagerdrc`,
`kactivitymanagerdstaterc`, or activity create/remove/select calls. The same
targeted search across the saved deployment source/script artifacts in the
2026-08-26 workspace found no writer. The installer does not restore activity
definitions or state. The bounded recovery helper writes its own runtime
request/result files and controls only the Plasma lifecycle; it does not reset
activity settings. An earlier manual action or unrecorded restore remains
possible, but there is no evidence identifying its author or exact operation.

The native selection has already corrected the persistent stale pointer, and
the one bounded recovery has corrected the live view state. There is no
demonstrated installer writer to patch and no justification for a resident
auto-repair service. Keep the activity definitions and state consistent in any
future desktop restore/migration, and validate that the saved ID is in the
restored activity list. Do not copy only one half and invent replacement IDs.

Current-session visual recovery and saved-state correction are accepted. A
later ordinary cold start can verify persistence in use; this incident did not
add another reboot solely to retest it. The cause of the original stale ID
remains unassigned, and Hall-04 must not be blamed for state that predates boot.

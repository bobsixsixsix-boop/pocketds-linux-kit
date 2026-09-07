# Experimental lock-screen keyboard

`VirtualKeyboardLoader.qml` preserves the first trusted-greeter prototype for
later isolated testing. It is deliberately not installed by `scripts/install.sh`.
The real device currently uses the unmodified file owned by
`plasma-workspace-6.7.4-1.fc44.aarch64`, and resume authentication remains
disabled while no verified touch input path exists.

Do not copy this prototype into `/usr/lib64/qt6/qml/org/kde/breeze/components/`
on a daily-use session. Test it first in an isolated greeter/nested session and
record at least:

- correct geometry on both outputs and every scale/rotation used by Pocket DS;
- password insertion, backspace, Shift, symbols, submit, hide, and focus;
- no plaintext password logging, clipboard exposure, or external process input;
- 50 show/hide/unlock cycles and repeated failed-password recovery;
- package upgrade/rollback behavior without modifying an RPM-owned file in place;
- resume authentication only after the full deep-suspend input matrix passes.

The intended long-term solution should be a packaged greeter extension or an
upstream-compatible configuration hook, not an unmanaged overwrite of a system
QML file.

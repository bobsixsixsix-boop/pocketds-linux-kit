#!/usr/bin/env python3
"""Minimal no-input GTK3/X11 client for the isolated PDS-004 harness."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys


READY_SCHEMA = "pocketds.x11-probe-ready.v1"
READY_ENV = "POCKETDS_X11_PROBE_READY"


class ProbeError(RuntimeError):
    """The probe environment or private readiness file is unsafe."""


def _private_parent(path: Path) -> None:
    try:
        metadata = path.parent.lstat()
    except OSError as exc:
        raise ProbeError("probe readiness parent is unavailable") from exc
    if (
        path.name != "client-ready.json"
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ProbeError("probe readiness parent identity is unsafe")


def write_ready(path: Path) -> None:
    _private_parent(path)
    content = (
        json.dumps(
            {
                "schema": READY_SCHEMA,
                "backend": "x11",
                "mapped": True,
                "input_capability": False,
                "identifiers_emitted": False,
            },
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short probe readiness write")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    ready_value = os.environ.get(READY_ENV, "")
    if not ready_value or os.environ.get("GDK_BACKEND") != "x11":
        print("X11 probe requires its private ready path and exact backend", file=sys.stderr)
        return 2
    ready_path = Path(ready_value)
    try:
        _private_parent(ready_path)
    except ProbeError as exc:
        print(f"X11 probe refused: {exc}", file=sys.stderr)
        return 2

    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, GLib, Gtk
    except (ImportError, ValueError) as exc:
        print(f"X11 probe GTK unavailable: {type(exc).__name__}", file=sys.stderr)
        return 2

    display = Gdk.Display.get_default()
    if display is None or "X11" not in type(display).__name__:
        print("X11 probe did not receive an X11 GDK display", file=sys.stderr)
        return 3

    state = {"mapped": False, "closed": False, "error": False}
    window = Gtk.Window(title="Pocket DS isolated X11 probe")
    window.set_default_size(320, 180)
    window.set_accept_focus(False)
    window.set_focus_on_map(False)
    window.set_deletable(False)

    def on_map(_widget, _event) -> bool:
        if not state["mapped"]:
            try:
                write_ready(ready_path)
                state["mapped"] = True
            except (OSError, ProbeError):
                state["error"] = True
                GLib.idle_add(Gtk.main_quit)
        return False

    def on_closed(_display, _is_error) -> None:
        state["closed"] = True
        Gtk.main_quit()

    window.connect("map-event", on_map)
    display.connect("closed", on_closed)
    window.show_all()
    Gtk.main()
    if state["error"] or not state["mapped"]:
        return 4
    return 23 if state["closed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

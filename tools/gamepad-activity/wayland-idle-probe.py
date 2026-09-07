#!/usr/bin/env python3
"""Bounded, observation-only probe of KWin's native Wayland idle detector.

Run as the graphical-session user with its WAYLAND_DISPLAY/XDG_RUNTIME_DIR.
Only a registry, seat, idle timeout and synchronization callbacks are created.
No activity, input, inhibitor, surface, display-power or screen-lock requests are
sent. Closing this diagnostic disconnects its private Wayland connection.

The wire definitions below follow KDE's LGPL-2.1-or-later idle.xml:
https://invent.kde.org/libraries/plasma-wayland-protocols/-/blob/master/src/protocols/idle.xml
KWin 6.7.3's detector follows idle inhibitors: absence of an IDLE event is not by
itself evidence that input activity was registered. This is not an installer.
"""

import argparse
import ctypes as C
import errno
import json
import math
import os
import select
import signal
import socket
import sys
import time


class ProbeError(RuntimeError):
    pass


class Interface(C.Structure):
    pass


InterfacePointer = C.POINTER(Interface)


class Message(C.Structure):
    _fields_ = [("name", C.c_char_p), ("signature", C.c_char_p),
                ("types", C.POINTER(InterfacePointer))]


Interface._fields_ = [
    ("name", C.c_char_p), ("version", C.c_int),
    ("method_count", C.c_int), ("methods", C.POINTER(Message)),
    ("event_count", C.c_int), ("events", C.POINTER(Message)),
]


class Argument(C.Union):
    _fields_ = [("i", C.c_int32), ("u", C.c_uint32), ("f", C.c_int32),
                ("s", C.c_char_p), ("o", C.c_void_p), ("n", C.c_uint32),
                ("a", C.c_void_p), ("h", C.c_int32)]


def report(event, **fields):
    print(json.dumps({"event": event, "monotonic": time.monotonic(), **fields},
                     sort_keys=True), flush=True)


class Probe:
    def __init__(self):
        self.display = None
        self.fatal = None
        self.listeners = []  # ctypes callbacks and backing arrays must stay live.
        self.globals = {}
        self.required_globals = set()
        try:
            self.lib = C.CDLL("libwayland-client.so.0", use_errno=True)
        except OSError as exc:
            raise ProbeError("libwayland-client.so.0 is unavailable") from exc
        signatures = {
            "wl_display_connect_to_fd": (C.c_void_p, [C.c_int]),
            "wl_display_disconnect": (None, [C.c_void_p]),
            "wl_display_get_fd": (C.c_int, [C.c_void_p]),
            "wl_display_prepare_read": (C.c_int, [C.c_void_p]),
            "wl_display_cancel_read": (None, [C.c_void_p]),
            "wl_display_read_events": (C.c_int, [C.c_void_p]),
            "wl_display_dispatch_pending": (C.c_int, [C.c_void_p]),
            "wl_display_flush": (C.c_int, [C.c_void_p]),
            "wl_proxy_get_version": (C.c_uint32, [C.c_void_p]),
            "wl_proxy_destroy": (None, [C.c_void_p]),
            "wl_proxy_add_listener": (C.c_int,
                [C.c_void_p, C.POINTER(C.c_void_p), C.c_void_p]),
            "wl_proxy_marshal_array_flags": (C.c_void_p,
                [C.c_void_p, C.c_uint32, InterfacePointer, C.c_uint32,
                 C.c_uint32, C.POINTER(Argument)]),
        }
        try:
            for name, (result, arguments) in signatures.items():
                function = getattr(self.lib, name)
                function.restype, function.argtypes = result, arguments
            self.registry_interface = Interface.in_dll(self.lib, "wl_registry_interface")
            self.seat_interface = Interface.in_dll(self.lib, "wl_seat_interface")
            self.callback_interface = Interface.in_dll(self.lib, "wl_callback_interface")
        except (AttributeError, ValueError) as exc:
            raise ProbeError("libwayland-client has an unsupported ABI") from exc

        # Full protocol metadata, including the unused second timeout request,
        # is retained for an exact interface description. No code marshals it.
        self.timeout_methods = (Message * 2)(
            Message(b"release", b"", None),
            Message(b"simulate_user_activity", b"", None))
        self.timeout_events = (Message * 2)(
            Message(b"idle", b"", None), Message(b"resumed", b"", None))
        self.timeout_interface = Interface(
            b"org_kde_kwin_idle_timeout", 1, 2, self.timeout_methods,
            2, self.timeout_events)
        self.timeout_types = (InterfacePointer * 3)(
            C.pointer(self.timeout_interface), C.pointer(self.seat_interface),
            InterfacePointer())
        self.idle_methods = (Message * 1)(
            Message(b"get_idle_timeout", b"nou", self.timeout_types))
        self.idle_interface = Interface(
            b"org_kde_kwin_idle", 1, 1, self.idle_methods, 0, None)

    def connect(self, deadline):
        display_name = os.environ.get("WAYLAND_DISPLAY", "")
        runtime = os.environ.get("XDG_RUNTIME_DIR", "")
        if not display_name or "WAYLAND_SOCKET" in os.environ:
            raise ProbeError("require WAYLAND_DISPLAY and no inherited WAYLAND_SOCKET")
        if os.path.isabs(display_name):
            path = display_name
        else:
            if not os.path.isabs(runtime) or "/" in display_name:
                raise ProbeError("require an absolute XDG_RUNTIME_DIR and display basename")
            path = os.path.join(runtime, display_name)

        # A nonblocking Unix connection also bounds a full compositor backlog;
        # wl_display_connect() itself has no caller-supplied connection timeout.
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.setblocking(False)
            result = connection.connect_ex(path)
            if result not in (0, errno.EINPROGRESS, errno.EAGAIN, errno.EWOULDBLOCK):
                raise ProbeError(f"Wayland socket connect failed: {os.strerror(result)}")
            if result:
                poller = select.poll()
                poller.register(connection, select.POLLOUT)
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not poller.poll(math.ceil(remaining * 1000)):
                    raise ProbeError("Wayland socket connect timed out")
                failure = connection.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if failure:
                    raise ProbeError(f"Wayland socket connect failed: {os.strerror(failure)}")
                connection.getpeername()  # EAGAIN may not mean connection pending.
            fd = connection.detach()
            # libwayland owns this fd, including its failure path.
            self.display = self.lib.wl_display_connect_to_fd(fd)
            if not self.display:
                raise ProbeError("Wayland connection initialization failed")
        finally:
            connection.close()
        self.fd = self.lib.wl_display_get_fd(self.display)

    def listen(self, proxy, definitions):
        callbacks = []
        for argument_types, function in definitions:
            callback_type = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, *argument_types)

            def guarded(*arguments, handler=function):
                try:
                    handler(*arguments)
                except BaseException as exc:
                    self.fatal = f"Wayland event callback failed: {exc}"

            callbacks.append(callback_type(guarded))
        implementation = (C.c_void_p * len(callbacks))(
            *(C.cast(callback, C.c_void_p).value for callback in callbacks))
        self.listeners.append((callbacks, implementation))
        if self.lib.wl_proxy_add_listener(proxy, implementation, None):
            raise ProbeError("Wayland listener registration failed")

    def constructor(self, proxy, opcode, interface, version, arguments):
        array = (Argument * len(arguments))(*arguments)
        result = self.lib.wl_proxy_marshal_array_flags(
            proxy, opcode, C.byref(interface), version, 0, array)
        if not result:
            raise ProbeError("Wayland object creation failed")
        return result

    def dispatch(self, deadline):
        if self.fatal:
            raise ProbeError(self.fatal)
        while self.lib.wl_display_prepare_read(self.display) != 0:
            dispatched = self.lib.wl_display_dispatch_pending(self.display)
            if dispatched < 0:
                raise ProbeError("Wayland dispatch failed or compositor disconnected")
            if self.fatal:
                raise ProbeError(self.fatal)
            if dispatched:
                # The caller may now have its sync completion. Do not wait for
                # another fd edge after satisfying a roundtrip from the queue.
                return
            if time.monotonic() >= deadline:
                return

        read_prepared = True
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            wanted = select.POLLIN
            if self.lib.wl_display_flush(self.display) < 0:
                if C.get_errno() != errno.EAGAIN:
                    raise ProbeError("Wayland flush failed")
                wanted |= select.POLLOUT
            poller = select.poll()
            poller.register(self.fd, wanted)
            events = poller.poll(math.ceil(remaining * 1000))
            if not events:
                return
            flags = events[0][1]
            if flags & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                raise ProbeError("Wayland compositor disconnected")
            if flags & select.POLLIN:
                result = self.lib.wl_display_read_events(self.display)
                read_prepared = False  # read_events ends the preparation on failure too.
                if result < 0:
                    raise ProbeError("Wayland event read failed")
            else:
                self.lib.wl_display_cancel_read(self.display)
                read_prepared = False
            if self.lib.wl_display_dispatch_pending(self.display) < 0:
                raise ProbeError("Wayland event dispatch failed")
            if self.fatal:
                raise ProbeError(self.fatal)
        finally:
            if read_prepared:
                self.lib.wl_display_cancel_read(self.display)

    def synchronize(self, deadline):
        done = []
        callback = self.constructor(self.display, 0, self.callback_interface, 1,
                                    [Argument(o=None)])

        def callback_done(_data, proxy, _serial):
            done.append(True)
            self.lib.wl_proxy_destroy(proxy)

        self.listen(callback, [([C.c_uint32], callback_done)])
        while not done:
            if time.monotonic() >= deadline:
                raise ProbeError("Wayland initialization exceeded its 3-second deadline")
            self.dispatch(deadline)

    def initialize(self, threshold_ms):
        deadline = time.monotonic() + 3
        self.connect(deadline)
        registry = self.constructor(self.display, 1, self.registry_interface, 1,
                                    [Argument(o=None)])

        def global_added(_data, _proxy, name, interface, version):
            self.globals[name] = (interface, version)
            if self.required_globals:
                for required in (b"wl_seat", b"org_kde_kwin_idle"):
                    if sum(value[0] == required for value in self.globals.values()) != 1:
                        self.fatal = "required Wayland globals changed or multiple seats appeared"

        def global_removed(_data, _proxy, name):
            self.globals.pop(name, None)
            if name in self.required_globals:
                self.fatal = "required Wayland seat or idle global disappeared"

        self.listen(registry, [
            ([C.c_uint32, C.c_char_p, C.c_uint32], global_added),
            ([C.c_uint32], global_removed)])
        self.synchronize(deadline)

        def bind(interface):
            found = [(name, version) for name, (kind, version) in self.globals.items()
                     if kind == interface.name]
            if len(found) != 1 or found[0][1] < 1:
                raise ProbeError(f"require exactly one {interface.name.decode()} v1 global")
            name = found[0][0]
            self.required_globals.add(name)
            return self.constructor(registry, 0, interface, 1, [
                Argument(u=name), Argument(s=interface.name), Argument(u=1),
                Argument(o=None)])

        seat = bind(self.seat_interface)
        self.listen(seat, [([C.c_uint32], lambda *_: None),
                           ([C.c_char_p], lambda *_: None)])
        idle = bind(self.idle_interface)
        timeout = self.constructor(idle, 0, self.timeout_interface, 1, [
            Argument(o=None), Argument(o=seat), Argument(u=threshold_ms)])
        self.listen(timeout, [([], lambda *_: report("IDLE")),
                              ([], lambda *_: report("RESUMED"))])
        self.synchronize(deadline)

    def close(self):
        if self.display:
            # Disconnect destroys server resources; no timeout release/activity
            # request or final blocking roundtrip is needed.
            self.lib.wl_display_disconnect(self.display)
            self.display = None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold-ms", type=int, default=5000,
                        help="idle detector timeout, 100–60000 ms (default: 5000)")
    parser.add_argument("--duration", type=float, default=30,
                        help="observation duration, 1–60 seconds (default: 30)")
    args = parser.parse_args(argv)
    if not 100 <= args.threshold_ms <= 60000:
        parser.error("--threshold-ms must be between 100 and 60000")
    if not math.isfinite(args.duration) or not 1 <= args.duration <= 60:
        parser.error("--duration must be finite and between 1 and 60")

    def interrupted(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    probe = None
    try:
        probe = Probe()
        probe.initialize(args.threshold_ms)
        report("READY", threshold_ms=args.threshold_ms, duration_seconds=args.duration,
               observation_only=True, follows_idle_inhibitors=True)
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline:
            probe.dispatch(deadline)
        report("DONE")
        return 0
    except KeyboardInterrupt:
        report("STOPPED")
        return 130
    except (ProbeError, OSError) as exc:
        report("ERROR", detail=str(exc))
        return 1
    finally:
        if probe:
            probe.close()


if __name__ == "__main__":
    sys.exit(main())

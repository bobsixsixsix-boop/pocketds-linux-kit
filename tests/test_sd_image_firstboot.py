#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Firstboot protocol/state tests; no real account or password is changed."""

import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import tempfile
import types
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "packaging/sd-image"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SOURCE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = load("sd_firstboot_server", "firstboot-server.py")
CLIENT = load("sd_firstboot_client", "firstboot-client.py")


def request(password="a local password"):
    return json.dumps({"password": password}, ensure_ascii=False).encode() + b"\n"


class PasswordProtocolTests(unittest.TestCase):
    def test_unicode_and_spaces_are_preserved(self):
        password = " 空格和中文密码🦆 "
        self.assertEqual(SERVER.parse_request(request(password)), password)

    def test_rejects_short_long_controls_and_surrogates(self):
        for value in ("short", "x" * 129, "password\n", "password\t", "password\x00",
                      "password\u200b", "password\ud800", 123, None):
            with self.subTest(value=repr(type(value))):
                self.assertFalse(SERVER.valid_password(value))

    def test_rejects_ambiguous_or_extra_protocol_fields(self):
        for value in (b'{"password":"longpassword","password":"replacement"}\n',
                      b'{"password":"longpassword","account":"root"}\n',
                      b'["longpassword"]\n', b'{}\n', b'{"password":null}\n',
                      b'{"password":NaN}\n', b'{"password":"longpassword"}',
                      b'{"password":"longpassword"}\n{}\n', b'\xff\n',
                      b'[' * 1000 + b']' * 1000 + b'\n', b'x' * 2049 + b'\n'):
            with self.assertRaises(SERVER.SetupError):
                SERVER.parse_request(value)

    def test_stream_is_bounded_and_requires_line(self):
        left, right = socket.socketpair()
        with left, right:
            left.sendall(b"x" * (SERVER.MAX_REQUEST + 1))
            with self.assertRaises(SERVER.SetupError):
                SERVER.read_request(right)
        left, right = socket.socketpair()
        with left, right:
            left.sendall(b"{}")
            left.shutdown(socket.SHUT_WR)
            with self.assertRaises(SERVER.SetupError):
                SERVER.read_request(right)

    def test_password_only_uses_private_stdin(self):
        password = "a unique test password"
        with mock.patch.object(SERVER.subprocess, "run") as run:
            SERVER.change_password(password)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/usr/sbin/chpasswd"])
        self.assertEqual(kwargs["input"], b"pocketds:a unique test password\n")
        self.assertNotIn(password, repr(args) + repr(kwargs["env"]))
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertNotIn("timeout", kwargs)

    def test_backend_error_hides_output(self):
        error = subprocess.CalledProcessError(1, ["chpasswd"], output=b"private test value")
        with mock.patch.object(SERVER.subprocess, "run", side_effect=error):
            with self.assertRaises(SERVER.SetupError) as caught:
                SERVER.change_password("a local password")
        self.assertEqual(str(caught.exception), "")


class FilesystemGuardTests(unittest.TestCase):
    def test_directory_requires_root_and_no_untrusted_writes(self):
        for mode, uid in ((stat.S_IFDIR | 0o755, 1000),
                          (stat.S_IFDIR | 0o775, 0),
                          (stat.S_IFLNK | 0o777, 0)):
            path = mock.Mock()
            path.lstat.return_value = types.SimpleNamespace(st_mode=mode, st_uid=uid)
            with self.assertRaises(SERVER.SetupError):
                SERVER.check_root_directory(path)

    def test_pending_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "target").touch()
            (root / "pending").symlink_to("target")
            with self.assertRaises(OSError):
                SERVER.checked_file(root / "pending")

    def test_pending_hardlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "target").touch()
            os.link(root / "target", root / "pending")
            with self.assertRaises(SERVER.SetupError):
                SERVER.checked_file(root / "pending")


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "pending").touch(mode=0o600)
        self.is_locked = True
        self.calls = []

        def setter(password):
            self.calls.append(password)
            self.is_locked = False

        self.setup = SERVER.PasswordSetup(self.root, 1000, locked=lambda: self.is_locked,
                                          setter=setter)
        # Simulate root ownership only for this temporary state-machine fixture.
        self.directory_guard = mock.patch.object(SERVER, "check_root_directory")
        self.directory_guard.start()
        self.addCleanup(self.directory_guard.stop)
        original = SERVER.os.fstat

        def root_stat(fd):
            values = list(original(fd))
            values[4] = 0
            return os.stat_result(values)

        self.file_owner = mock.patch.object(SERVER.os, "fstat", side_effect=root_stat)
        self.file_owner.start()
        self.addCleanup(self.file_owner.stop)

    def test_exact_peer_can_set_once(self):
        self.assertEqual(self.setup.handle(1000, request()), b'{"status":"complete"}\n')
        self.assertFalse((self.root / "pending").exists())
        self.assertEqual(self.setup.handle(1000, request("replacement password")),
                         b'{"status":"complete"}\n')
        self.assertEqual(self.calls, ["a local password"])

    def test_other_peer_including_root_cannot_set(self):
        for peer in (0, 1001, -1):
            self.assertEqual(self.setup.handle(peer, request()), b'{"status":"error"}\n')
        self.assertEqual(self.calls, [])
        self.assertTrue((self.root / "pending").exists())

    def test_missing_marker_never_resets(self):
        (self.root / "pending").unlink()
        self.setup.handle(1000, request())
        self.assertEqual(self.calls, [])

    def test_restarts_after_success_without_marker_removal(self):
        self.is_locked = False
        self.assertFalse(self.setup.reconcile())
        self.assertFalse((self.root / "pending").exists())
        self.assertEqual(self.calls, [])

    def test_backend_failed_before_change_can_retry(self):
        self.setup.setter = mock.Mock(side_effect=SERVER.SetupError())
        self.assertEqual(self.setup.handle(1000, request()), b'{"status":"error"}\n')
        self.assertTrue((self.root / "pending").exists())
        self.assertTrue(self.is_locked)

    def test_backend_changed_password_then_failed_never_resets(self):
        def interrupted(password):
            self.calls.append(password)
            self.is_locked = False
            raise SERVER.SetupError()
        self.setup.setter = interrupted
        self.assertEqual(self.setup.handle(1000, request()), b'{"status":"complete"}\n')
        self.assertFalse((self.root / "pending").exists())
        self.setup.handle(1000, request("replacement password"))
        self.assertEqual(self.calls, ["a local password"])

    def test_fake_success_does_not_clear_marker(self):
        self.setup.setter = mock.Mock()
        self.assertEqual(self.setup.handle(1000, request()), b'{"status":"error"}\n')
        self.assertTrue((self.root / "pending").exists())

    def test_marker_cleanup_failure_cannot_repeat_password_change(self):
        with mock.patch.object(self.setup, "complete", side_effect=OSError()):
            self.assertEqual(self.setup.handle(1000, request()), b'{"status":"error"}\n')
        self.assertFalse(self.is_locked)
        self.assertTrue((self.root / "pending").exists())
        self.setup.handle(1000, request("replacement password"))
        self.assertEqual(self.calls, ["a local password"])
        self.assertFalse((self.root / "pending").exists())

    def test_bad_input_never_reaches_backend(self):
        self.assertEqual(self.setup.handle(1000, request("short")), b'{"status":"error"}\n')
        self.assertEqual(self.calls, [])

    def test_shared_writable_marker_fails_closed(self):
        (self.root / "pending").chmod(0o666)
        self.assertEqual(self.setup.handle(1000, request()), b'{"status":"error"}\n')
        self.assertEqual(self.calls, [])

    def test_shadow_parser_rejects_empty_missing_and_duplicate_account(self):
        shadow = self.root / "shadow"
        for data in ("", "pocketds::20000:0:99999:7:::\n",
                     "pocketds:!:20000:0:99999:7:::\n" * 2):
            shadow.write_text(data)
            with self.assertRaises(SERVER.SetupError):
                SERVER.password_is_locked(shadow)
        shadow.write_text("pocketds:!:20000:0:99999:7:::\n")
        self.assertTrue(SERVER.password_is_locked(shadow))
        shadow.write_text("pocketds:$y$test-fixture:20000:0:99999:7:::\n")
        self.assertFalse(SERVER.password_is_locked(shadow))


class ClientTests(unittest.TestCase):
    def test_client_rejects_nonroot_server_before_sending_password(self):
        path = mock.Mock()
        path.parent.lstat.return_value = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        path.lstat.return_value = types.SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=0)
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.getsockopt.return_value = struct.pack("3i", 10, 1000, 1000)
        with mock.patch.object(CLIENT, "SOCKET", path), \
                mock.patch.object(CLIENT.socket, "socket", return_value=connection), \
                mock.patch.object(CLIENT.socket, "SO_PEERCRED", 17, create=True):
            self.assertFalse(CLIENT.submit("a local password"))
        connection.sendall.assert_not_called()

    def test_cancellation_leaves_pending_and_never_submits(self):
        with mock.patch.object(CLIENT.os, "geteuid", return_value=1000), \
                mock.patch.object(CLIENT.pwd, "getpwnam", return_value=types.SimpleNamespace(pw_uid=1000)), \
                mock.patch.object(CLIENT, "PENDING") as pending, \
                mock.patch.object(CLIENT, "desktop_ready", return_value=True), \
                mock.patch.object(CLIENT, "dialog", return_value=None), \
                mock.patch.object(CLIENT, "submit") as submit:
            pending.exists.return_value = True
            self.assertEqual(CLIENT.main(), 0)
            submit.assert_not_called()
            pending.unlink.assert_not_called()

    def test_failed_desktop_setup_defers_password_dialog(self):
        with mock.patch.object(CLIENT.os, "geteuid", return_value=1000), \
                mock.patch.object(CLIENT.pwd, "getpwnam", return_value=types.SimpleNamespace(pw_uid=1000)), \
                mock.patch.object(CLIENT, "PENDING") as pending, \
                mock.patch.object(CLIENT, "desktop_ready", return_value=False), \
                mock.patch.object(CLIENT, "dialog") as dialog:
            pending.exists.return_value = True
            self.assertEqual(CLIENT.main(), 1)
            dialog.assert_not_called()

    def test_dialog_password_is_captured_not_passed_as_an_argument(self):
        with mock.patch.object(CLIENT.subprocess, "run", return_value=types.SimpleNamespace(
                returncode=0, stdout=b"a local password\n")) as run:
            self.assertEqual(CLIENT.dialog("--password", "generic prompt"), "a local password")
        self.assertNotIn("a local password", repr(run.call_args))
        self.assertEqual(run.call_args.kwargs["stdout"], subprocess.PIPE)


if __name__ == "__main__":
    unittest.main()

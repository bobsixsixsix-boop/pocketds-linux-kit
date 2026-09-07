#!/usr/bin/env python3
"""Exercise the panel's bounded socket transport against an isolated fake owner."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = '0123456789abcdef0123456789abcdef'


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix='pds-client-', dir='/tmp')
        cls.root = Path(cls.workspace.name)
        cls.binary = cls.root / 'panelctl'
        command = ['c++', '-std=c++17', '-O1', '-DPOCKETDS_CONTROLLER_TEST_TESTING']
        if sys.platform == 'darwin':
            # Existing Linux telemetry timestamps; no installed source changes.
            command += ['-Dst_mtim=st_mtimespec']
        subprocess.run(command + [str(ROOT / 'components/control-panel/pocketds-panelctl.cpp'),
                                 '-o', str(cls.binary)], check=True, timeout=60)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def setUp(self):
        self.path = self.root / 'control.sock'
        self.path.unlink(missing_ok=True)
        self.env = dict(os.environ, POCKETDS_CONTROLLER_TEST_SOCKET=str(self.path))

    def run_client(self, operation='poll', token=TOKEN):
        return subprocess.run([str(self.binary), 'controller-test', operation, token],
                              env=self.env, capture_output=True, text=True, timeout=4)

    def server_call(self, payload, *, operation='poll', pause=0, fragment=False):
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.path)); self.path.chmod(0o660); listener.listen(1)
        listener.settimeout(4)
        received = []
        def serve():
            try:
                with listener.accept()[0] as peer:
                    request = b''
                    while b'\n' not in request:
                        request += peer.recv(1024)
                    received.append(json.loads(request))
                    if pause:
                        time.sleep(pause)
                    elif fragment:
                        peer.sendall(payload[:9]); time.sleep(.02); peer.sendall(payload[9:])
                    else:
                        peer.sendall(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                listener.close()
        worker = threading.Thread(target=serve, daemon=True); worker.start()
        started = time.monotonic(); result = self.run_client(operation)
        elapsed = time.monotonic() - started
        worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(received, [{'op': operation, 'token': TOKEN}])
        return result, elapsed

    def reply(self, **changes):
        data = {'token': TOKEN, 'status': 'active', 'buttons': {'a': 1},
                'axes': {'lx': 0, 'ly': 0, 'rx': 0, 'ry': 0, 'lt': 0, 'rt': 0}, 'error': ''}
        data.update(changes)
        return (json.dumps(data) + '\n').encode()

    def test_valid_reply_and_fragmented_delivery(self):
        result, _ = self.server_call(self.reply(), operation='begin', fragment=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['buttons'], {'a': 1})

    def test_end_accepts_draining_without_claiming_neutral(self):
        result, _ = self.server_call(self.reply(status='draining'), operation='end')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['status'], 'draining')

    def test_invalid_token_or_operation_never_connects(self):
        for operation, token in [('toggle', TOKEN), ('poll', 'a;reboot'), ('begin', 'a'*31), ('end', 'A'*32)]:
            with self.subTest(operation=operation, token=token):
                result = self.run_client(operation, token)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')

    def test_missing_service_is_explicit_error(self):
        result = self.run_client()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['status'], 'error')

    def test_symlink_is_rejected(self):
        target = self.root / 'not-a-socket'; target.write_text('')
        self.path.symlink_to(target)
        self.assertEqual(self.run_client().returncode, 1)

    def test_other_session_reply_cannot_activate_ui(self):
        result, _ = self.server_call(self.reply(token='a'*32))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['status'], 'error')

    def test_unknown_state_is_rejected(self):
        result, _ = self.server_call(self.reply(status='pretend-active'))
        self.assertEqual(result.returncode, 1)

    def test_oversized_unterminated_reply_is_bounded(self):
        result, _ = self.server_call(b'{' + b'x'*20000)
        self.assertEqual(result.returncode, 1)
        self.assertLess(len(result.stdout), 500)

    def test_unresponsive_service_has_deadline(self):
        result, elapsed = self.server_call(b'', pause=2.8)
        self.assertEqual(result.returncode, 1)
        self.assertLess(elapsed, 3.3)
        self.assertEqual(json.loads(result.stdout)['status'], 'error')


if __name__ == '__main__':
    unittest.main()

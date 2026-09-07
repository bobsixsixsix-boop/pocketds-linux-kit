#!/usr/bin/python3
"""Explicit attended diagnostic: one activity reset, no emulated device input.

The normal production power policy remains unchanged. Requires the screens
already lit and every production safety gate. Never installed as a service.
"""
import argparse
import json
from pathlib import Path
import runpy
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watcher', type=Path, required=True)
    parser.add_argument('--confirm-one-activity-reset', action='store_true', required=True)
    args = parser.parse_args()
    live = runpy.run_path(str(args.watcher))['Live']()
    if not live.allowed():
        raise RuntimeError('production gate refused; do not wake or override it')
    probe = subprocess.Popen(['/usr/bin/python3', '-B', str(Path(__file__).with_name(
        'wayland-idle-probe.py')), '--threshold-ms', '5000', '--duration', '18'],
        stdout=subprocess.PIPE, text=True)
    events = []
    try:
        for line in probe.stdout:
            print(line, end='', flush=True)
            event = json.loads(line)['event']
            if event in ('IDLE', 'RESUMED'):
                events.append(event)
            if events == ['IDLE'] and live.bridge.sent == 0:
                now = time.monotonic()
                live.bridge.event(now, now, True)  # Diagnostic metadata, not evdev/uinput.
                if not live.bridge.tick(now):
                    raise RuntimeError('gate changed before the single reset')
                print(json.dumps({'diagnostic_activity_notifications': live.bridge.sent,
                                  'monotonic': time.monotonic()}), flush=True)
        if probe.wait(timeout=2) or events != ['IDLE', 'RESUMED', 'IDLE']:
            raise RuntimeError('did not observe an isolated IDLE -> RESUMED -> IDLE cycle')
        print(json.dumps({'result': 'PASS', 'notifications': live.bridge.sent}), flush=True)
    finally:
        if probe.poll() is None:
            probe.terminate()
            probe.wait(timeout=3)
        live.detach()


if __name__ == '__main__':
    main()

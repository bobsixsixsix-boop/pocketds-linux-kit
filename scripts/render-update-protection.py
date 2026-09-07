#!/usr/bin/env python3
"""Print the DNF default generated from every protected evaluator ring.

Regenerate components/system/90-pocketds-hardware-protection.conf with stdout.
No DNF, package changes or installed-file writes occur here.
"""
from pathlib import Path
import runpy

if __name__ == '__main__':
    policy = runpy.run_path(str(Path(__file__).with_name('pocketds-userspace-update-plan.py')))
    patterns = [pattern for ring in policy['PROTECTED_RINGS'].values() for pattern in ring]
    print('[main]\nexcludepkgs=' + ','.join(patterns))

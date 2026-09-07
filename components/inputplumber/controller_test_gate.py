"""Read the root-owned controller-test gate without mutating input state."""

import json
import os
from pathlib import Path
import stat


STATE_PATH = Path("/run/pocketds-controller-test/state.json")
PROTOCOL = "pds-controller-test-v1"
ROOT_UID = 0
MAX_STATE_BYTES = 65536


def hardware_actions_blocked(state_path=STATE_PATH):
    """Only a missing journal or a trusted completed restore allows actions.

    The daemon owns expiry and recovery. Readers must never release the gate
    merely because a heartbeat expired while the daemon is still draining a
    held control or restoring InputPlumber.
    """
    directory_fd = state_fd = -1
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        directory_fd = os.open(state_path.parent, flags | os.O_DIRECTORY)
        directory = os.fstat(directory_fd)
        if directory.st_uid != ROOT_UID or directory.st_mode & 0o022:
            return True
        try:
            state_fd = os.open(state_path.name, flags, dir_fd=directory_fd)
        except FileNotFoundError:
            return False
        metadata = os.fstat(state_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != ROOT_UID
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o022
            or not 0 < metadata.st_size <= MAX_STATE_BYTES
        ):
            return True
        payload = os.read(state_fd, MAX_STATE_BYTES + 1)
        if len(payload) != metadata.st_size:
            return True
        state = json.loads(payload)
        return not (
            isinstance(state, dict)
            and state.get("protocol") == PROTOCOL
            and state.get("status") == "idle"
            and state.get("blocked") is False
        )
    except FileNotFoundError:
        return False
    except (OSError, ValueError, TypeError):
        return True
    finally:
        if state_fd >= 0:
            os.close(state_fd)
        if directory_fd >= 0:
            os.close(directory_fd)

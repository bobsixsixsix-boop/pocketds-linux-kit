"""Restore dependent input services around an explicit controller backend update.

File-only UI transactions remain independent from service activation.
"""
from contextlib import contextmanager
import subprocess
from typing import Callable


class ServiceUpdateError(RuntimeError):
    """A controller update cannot safely preserve its service lifecycle."""


class ControllerUpdateCheckpoint:
    def __init__(self):
        self.verified = False

    def files_verified(self):
        """Allow resume only after a complete publish or verified file rollback."""
        self.verified = True


@contextmanager
def paused_controller_services(*, run: Callable = subprocess.run):
    """Explicit activation wrapper for updates that replace the test backend.

    File transactions alone never restart services. Callers opting into this
    wrapper must mark the yielded checkpoint after a verified publish or file
    rollback. An incomplete rollback leaves both services stopped. Stopping
    the test service also stops its Requires dependent.
    """
    test_unit = "pocketds-controller-test.service"
    listener = "pocketds-mode-listener.service"
    units = (test_unit, listener)

    def command(*argv):
        result = run(argv, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode != 0:
            raise ServiceUpdateError("controller service command failed: " + " ".join(argv))
        return result.stdout.strip()

    def state(unit):
        values = dict(line.split("=", 1) for line in command(
            "/usr/bin/systemctl", "show", unit,
            "--property=LoadState,ActiveState,SubState",
        ).splitlines() if "=" in line)
        if values.get("LoadState") != "loaded":
            raise ServiceUpdateError("controller service is not loaded: " + unit)
        active, sub = values.get("ActiveState"), values.get("SubState")
        if (active, sub) not in {("active", "running"), ("inactive", "dead"), ("failed", "failed")}:
            raise ServiceUpdateError("controller service is transitioning: " + unit)
        return active

    before = {unit: state(unit) for unit in units}
    if before[listener] == "active" and before[test_unit] != "active":
        raise ServiceUpdateError("controller service dependency state is inconsistent")
    checkpoint = ControllerUpdateCheckpoint()
    body_entered = False
    try:
        command("/usr/bin/sudo", "-n", "/usr/bin/systemctl", "stop", test_unit)
        if any(state(unit) == "active" for unit in units):
            raise ServiceUpdateError("controller services did not stop")
        body_entered = True
        yield checkpoint
    finally:
        if body_entered and not checkpoint.verified:
            raise ServiceUpdateError("controller files are not verified; services remain stopped")
        # Restore in dependency order after both success and rollback. Do not
        # enable previously stopped services or retry a failed start indirectly
        # through the listener's Requires relationship.
        for unit in units:
            if before[unit] == "active":
                command("/usr/bin/sudo", "-n", "/usr/bin/systemctl", "start", unit)
                if state(unit) != "active":
                    raise ServiceUpdateError("controller service did not recover: " + unit)
            elif state(unit) == "active":
                raise ServiceUpdateError("previously stopped controller service was started: " + unit)


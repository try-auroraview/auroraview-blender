"""Source-only finalizer regression; no Blender or native renderer is launched."""

import subprocess
import sys
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src"
CHILD = r"""
import sys
from types import SimpleNamespace

sys.path.insert(0, sys.argv[1])
from auroraview_blender import BlenderScheduler

class Timers:
    callback = None

    def register(self, callback, **options):
        self.callback = callback

    def is_registered(self, callback):
        return self.callback is callback

    def unregister(self, callback):
        self.callback = None

timers = Timers()
scheduler = BlenderScheduler(SimpleNamespace(app=SimpleNamespace(
    background=False, timers=timers)))
scheduler.start()

class Callback:
    def __call__(self):
        raise AssertionError("Pending work must be discarded, not executed")

    def __del__(self):
        print("FINALIZER_ENTER", flush=True)
        if sys.argv[2] == "reenter":
            try:
                scheduler.submit(lambda: None)
            except RuntimeError as exc:
                assert str(exc) == "Blender session is closed"
                print("SUBMIT_REJECTED_CLOSED", flush=True)
            else:
                print("UNEXPECTED_SUBMIT_ACCEPTED", flush=True)
        print("FINALIZER_EXIT", flush=True)

# Stop must release the queue's last reference only after unlocking.
callback = Callback()
scheduler.submit(callback)
del callback
print("STOP_ENTER", flush=True)
scheduler.stop()
assert scheduler.pending == 0
assert not scheduler.running
assert timers.callback is None
print("STOP_RETURNED", flush=True)
scheduler.start()
scheduler.stop()
print("RESTART_RETURNED", flush=True)
"""


def probe(source=SOURCE, mode="reenter", timeout=5):
    """Also accepts a preserved baseline source path for the negative control.

    subprocess.run kills and reaps a timed-out child; a regression cannot
    deadlock the test runner or leave a hanging process behind.
    """
    return subprocess.run(
        [sys.executable, "-c", CHILD, str(source), mode],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )


class SchedulerFinalizerTests(unittest.TestCase):
    def test_normal_finalizer_stop_and_restart(self):
        result = probe(mode="normal")
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "STOP_ENTER",
                "FINALIZER_ENTER",
                "FINALIZER_EXIT",
                "STOP_RETURNED",
                "RESTART_RETURNED",
            ],
        )

    def test_submit_from_finalizer_is_rejected_without_deadlock(self):
        result = probe()
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "STOP_ENTER",
                "FINALIZER_ENTER",
                "SUBMIT_REJECTED_CLOSED",
                "FINALIZER_EXIT",
                "STOP_RETURNED",
                "RESTART_RETURNED",
            ],
        )

"""Exercise the real worker lifetime without starting any systemd inhibitor."""
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.keepawake_worker import main, run


class WorkerTests(unittest.TestCase):
    def test_rejects_unbounded_numeric_duration(self):
        self.assertEqual(run(0), 2)
        self.assertEqual(run(14401), 2)
        self.assertEqual(main(["not-a-duration"]), 2)
        self.assertEqual(main(["forever", "extra"]), 2)

    def test_eof_releases_indefinite_worker_without_waiting_for_timer(self):
        src = str(Path(__file__).resolve().parents[1] / "src")
        env = {
            "PYTHONPATH": src,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        child = subprocess.Popen(
            [sys.executable, "-B", "-m", "isc.keepawake_worker", "forever"],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            close_fds=True,
        )
        try:
            self.assertIsNone(child.poll(), "worker unexpectedly exited")
            # A parent crash closes this pipe in the kernel, without requiring
            # any termination signal or knowledge of the child's PID.
            child.stdin.close()
            self.assertEqual(child.wait(timeout=4), 0)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)
            if child.stderr:
                child.stderr.close()

    def test_eof_releases_finite_worker_before_duration(self):
        src = str(Path(__file__).resolve().parents[1] / "src")
        child = subprocess.Popen(
            [sys.executable, "-B", "-m", "isc.keepawake_worker", "900"],
            env={"PYTHONPATH": src, "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            child.stdin.close()
            self.assertEqual(child.wait(timeout=3), 0)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()

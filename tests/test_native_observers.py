import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.native_observers import (
    _capture, _count_sessions, _count_loaded_timers,
    NativeActivity, read_native_activity,
)


class NativeObserverTests(unittest.TestCase):
    def test_empty_inventory_is_observed_zero_not_verified_coverage(self):
        self.assertEqual(_count_sessions(""), 0)
        self.assertEqual(_count_loaded_timers(""), 0)
        self.assertFalse(NativeActivity(0, 0, 0).coverage_verified)

    def test_sessions_count_only_no_private_username(self):
        raw = (
            " 2 1000 private-user seat0 tty2\n"
            " c1 1001 another-private-user - pts/3\n"
        )
        self.assertEqual(_count_sessions(raw), 2)
        self.assertIsNone(_count_sessions("invalid row"))
        self.assertIsNone(_count_sessions("1 NaN someone"))
        self.assertIsNone(_count_sessions(None))

    def test_timer_rows_count_loaded_units_without_retaining_names(self):
        raw = (
            "apt-daily.timer loaded active waiting Daily apt timer\n"
            "● backup.timer loaded active waiting Private backup unit\n"
            "user-something@instance.timer loaded inactive dead Some task\n"
        )
        self.assertEqual(_count_loaded_timers(raw), 3)
        self.assertIsNone(_count_loaded_timers("not a valid timer record"))
        self.assertIsNone(_count_loaded_timers(None))

    @patch("isc.native_observers.subprocess.run")
    @patch.dict(os.environ, {
        "PATH": "/usr/bin", "AGENT_SECRET": "NEVER_FORWARD",
    })
    def test_command_read_only_and_environment_limited(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout="valid output")
        self.assertEqual(_capture(("loginctl", "list-sessions")), "valid output")
        self.assertEqual(run.call_args.args[0], ("loginctl", "list-sessions"))
        self.assertEqual(run.call_args.kwargs["timeout"], 4)
        self.assertNotIn("AGENT_SECRET", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["env"]["SYSTEMD_COLORS"], "0")
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    @patch("isc.native_observers.subprocess.run")
    def test_command_failure_is_unknown_not_zero(self, run):
        run.return_value = SimpleNamespace(returncode=1, stdout="")
        self.assertIsNone(_capture(("loginctl", "list-sessions")))

    @patch("isc.native_observers._capture")
    def test_system_and_user_timers_checked_separately(self, capture):
        capture.side_effect = [
            "2 1000 someone seat0 tty2\n",
            "daily.timer loaded active waiting Scheduled maintenance\n",
            "user-job.timer loaded active waiting Independent user job\n",
        ]
        result = read_native_activity()
        self.assertEqual(result.sessions_observed, 1)
        self.assertEqual(result.loaded_system_timers, 1)
        self.assertEqual(result.loaded_user_timers, 1)
        self.assertFalse(result.coverage_verified)
        commands = [c.args[0] for c in capture.call_args_list]
        self.assertTrue(any("--system" in c for c in commands))
        self.assertTrue(any("--user" in c for c in commands))
        self.assertFalse(any("start" in c or "stop" in c for c in commands))

    @patch("isc.native_observers._capture", return_value=None)
    def test_unavailable_os_evidence_never_marks_workload_safe(self, capture):
        result = read_native_activity()
        self.assertIsNone(result.sessions_observed)
        self.assertIsNone(result.loaded_system_timers)
        self.assertIsNone(result.loaded_user_timers)
        self.assertFalse(result.coverage_verified)


if __name__ == "__main__":
    unittest.main()

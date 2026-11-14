"""Step 99c: portable systemd 255 observation window, no raw bus output."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.native_systemd_portable_window import inspect_user_manager_window


class PortableWindowTests(unittest.TestCase):
    def test_timeout_is_unverified_not_event(self):
        result = inspect_user_manager_window(runner=Mock(
            side_effect=subprocess.TimeoutExpired("busctl", 3)))
        self.assertEqual(result.state, "WINDOW_ELAPSED_UNVERIFIED")
        self.assertIsNone(result.observed_events)
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.event_delivery_verified)
        self.assertFalse(result.task_owner_authenticated)

    def test_fixed_systemd_255_command_without_limit_messages(self):
        runner = Mock(side_effect=subprocess.TimeoutExpired("busctl", 3))
        inspect_user_manager_window(seconds=3, runner=runner)
        argv, kw = runner.call_args
        self.assertEqual(argv[0], (
            "busctl", "--user", "--no-pager",
            "--match=type='signal',interface='org.freedesktop.systemd1.Manager'",
            "monitor", "org.freedesktop.systemd1",
        ))
        self.assertNotIn("--limit-messages=1", argv[0])
        self.assertEqual(kw["timeout"], 3)
        for key in ("stdin", "stdout", "stderr"):
            self.assertEqual(kw[key], subprocess.DEVNULL)
        self.assertFalse(kw["check"])

    def test_even_zero_exit_is_unknown(self):
        result = inspect_user_manager_window(runner=Mock(
            return_value=subprocess.CompletedProcess([], 0)))
        self.assertEqual(result.state, "UNKNOWN")
        self.assertIsNone(result.observed_events)

    def test_nonzero_exit_is_unknown(self):
        for code in (1, 2, -9):
            self.assertEqual(inspect_user_manager_window(runner=Mock(
                return_value=subprocess.CompletedProcess([], code))).state,
                "UNKNOWN")

    def test_errors_fail_closed(self):
        for err in (OSError("private"), ValueError("private"),
                    RuntimeError("private"), TypeError("private")):
            row = inspect_user_manager_window(runner=Mock(side_effect=err))
            self.assertEqual(row.state, "UNKNOWN")
            self.assertNotIn("private", repr(row))

    def test_invalid_durations_do_not_spawn(self):
        for seconds in (True, None, -1, 0, 11, 1.5, "3"):
            runner = Mock()
            self.assertEqual(inspect_user_manager_window(
                seconds=seconds, runner=runner).state, "UNKNOWN")
            runner.assert_not_called()

    def test_no_secret_environment_forwarded(self):
        with patch.dict("os.environ", {
            "PATH": "/usr/bin", "SECRET_TOKEN": "hidden",
        }, clear=True):
            runner = Mock(side_effect=subprocess.TimeoutExpired("busctl", 1))
            inspect_user_manager_window(seconds=1, runner=runner)
            env = runner.call_args.kwargs["env"]
            self.assertNotIn("SECRET_TOKEN", env)
            self.assertEqual(env["PATH"], "/usr/bin")

    def test_caller_cannot_inject_command(self):
        runner = Mock(side_effect=subprocess.TimeoutExpired("busctl", 10))
        inspect_user_manager_window(seconds=10, runner=runner)
        self.assertEqual(runner.call_args.kwargs["timeout"], 10)

    def test_noncallable_runner_is_unknown(self):
        self.assertEqual(inspect_user_manager_window(runner=None).state,
                         "UNKNOWN")


if __name__ == "__main__":
    unittest.main()

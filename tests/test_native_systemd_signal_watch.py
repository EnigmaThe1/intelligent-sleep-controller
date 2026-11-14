"""Step 99b: bounded on-demand native signal watch, all tests mocked."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.native_systemd_signal_watch import observe_one_user_manager_signal


def done(code=0):
    return subprocess.CompletedProcess(args=(), returncode=code)


class NativeSignalWatchTests(unittest.TestCase):
    def test_event_remains_unverified(self):
        row = observe_one_user_manager_signal(runner=Mock(return_value=done()))
        self.assertEqual((row.state, row.observed_events),
                         ("EVENT_OBSERVED_UNVERIFIED", 1))
        self.assertFalse(row.coverage_verified)
        self.assertFalse(row.event_delivery_verified)
        self.assertFalse(row.task_owner_authenticated)

    def test_bounded_exact_user_match_and_no_payload_capture(self):
        runner = Mock(return_value=done())
        observe_one_user_manager_signal(seconds=4, runner=runner)
        args, kwargs = runner.call_args
        self.assertEqual(args[0], (
            "busctl", "--user", "--no-pager", "--limit-messages=1",
            "--match=type='signal',interface='org.freedesktop.systemd1.Manager'",
            "monitor", "org.freedesktop.systemd1",
        ))
        self.assertEqual(kwargs["timeout"], 4)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertFalse(kwargs["check"])

    def test_quiet_timeout_does_not_prove_zero_events(self):
        row = observe_one_user_manager_signal(
            runner=Mock(side_effect=subprocess.TimeoutExpired("busctl", 3)))
        self.assertEqual(row.state, "QUIET_UNVERIFIED")
        self.assertIsNone(row.observed_events)
        self.assertFalse(row.event_delivery_verified)

    def test_unsupported_busctl_is_unknown(self):
        row = observe_one_user_manager_signal(runner=Mock(return_value=done(1)))
        self.assertEqual(row.state, "UNKNOWN")
        self.assertIsNone(row.observed_events)

    def test_signalled_process_is_unknown(self):
        row = observe_one_user_manager_signal(runner=Mock(return_value=done(-9)))
        self.assertEqual(row.state, "UNKNOWN")

    def test_errors_are_unknown_and_private_messages_suppressed(self):
        for err in (OSError("private unit"), RuntimeError("private"),
                    TypeError("private"), ValueError("private")):
            with self.subTest(err=type(err).__name__):
                row = observe_one_user_manager_signal(runner=Mock(side_effect=err))
                self.assertEqual(row.state, "UNKNOWN")
                self.assertNotIn("private", repr(row))

    def test_invalid_durations_are_refused(self):
        for value in (0, 11, True, 1.5, "3", None, -1):
            with self.subTest(value=value):
                runner = Mock()
                self.assertEqual(
                    observe_one_user_manager_signal(seconds=value, runner=runner).state,
                    "UNKNOWN",
                )
                runner.assert_not_called()

    def test_no_unexpected_runner_results(self):
        for item in (None, True, 0, "success"):
            self.assertEqual(observe_one_user_manager_signal(
                runner=Mock(return_value=item)).state, "UNKNOWN")

    def test_noncallable_runner_refused(self):
        self.assertEqual(observe_one_user_manager_signal(runner=None).state, "UNKNOWN")

    def test_no_secret_environment_forwarding(self):
        with patch.dict("os.environ", {
            "PATH": "/usr/bin", "SECRET_INTERNAL": "sensitive",
        }, clear=True):
            runner = Mock(return_value=done())
            observe_one_user_manager_signal(runner=runner)
            env = runner.call_args.kwargs["env"]
            self.assertNotIn("SECRET_INTERNAL", env)
            self.assertEqual(env["PATH"], "/usr/bin")

    def test_no_unbounded_watch(self):
        runner = Mock(return_value=done())
        observe_one_user_manager_signal(seconds=10, runner=runner)
        self.assertEqual(runner.call_args.kwargs["timeout"], 10)


if __name__ == "__main__":
    unittest.main()

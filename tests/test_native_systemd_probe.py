"""Step 99 native user-manager capability probe: bounded, read-only."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.native_systemd_probe import probe_user_manager


def completed(output="b true\n", code=0):
    return subprocess.CompletedProcess(args=(), returncode=code, stdout=output, stderr="")


class NativeSystemdProbeTests(unittest.TestCase):
    def test_present_remains_non_authoritative(self):
        result = probe_user_manager(runner=lambda *args, **kwargs: completed())
        self.assertEqual(result.state, "PRESENT_UNVERIFIED")
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.event_delivery_verified)
        self.assertFalse(result.task_owner_authenticated)

    def test_absent_is_not_idle(self):
        result = probe_user_manager(runner=lambda *args, **kwargs: completed("b false\n"))
        self.assertEqual(result.state, "ABSENT_UNVERIFIED")
        self.assertFalse(result.coverage_verified)

    def test_only_fixed_bounded_read_only_query(self):
        runner = Mock(return_value=completed())
        self.assertEqual(probe_user_manager(runner=runner).state, "PRESENT_UNVERIFIED")
        args, kwargs = runner.call_args
        self.assertEqual(args[0], (
            "busctl", "--user", "call",
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner", "s",
            "org.freedesktop.systemd1",
        ))
        self.assertEqual(kwargs["timeout"], 4)
        self.assertFalse(kwargs["check"])
        self.assertTrue(kwargs["capture_output"])

    def test_errors_become_unknown(self):
        for error in (
            OSError("private failure"), subprocess.TimeoutExpired("busctl", 4),
            UnicodeError("bad encoding"), ValueError("bad result"),
        ):
            with self.subTest(error=type(error).__name__):
                result = probe_user_manager(runner=Mock(side_effect=error))
                self.assertEqual(result.state, "UNKNOWN")
                self.assertNotIn("private failure", repr(result))

    def test_nonzero_return_code_unknown(self):
        self.assertEqual(probe_user_manager(
            runner=lambda *a, **k: completed("b true", code=1)
        ).state, "UNKNOWN")

    def test_malformed_and_oversize_output_unknown(self):
        for output in ("", "true", "b TRUE", "b true extra", "secret" * 30):
            with self.subTest(output=output[:10]):
                self.assertEqual(probe_user_manager(
                    runner=lambda *a, **k: completed(output)
                ).state, "UNKNOWN")

    def test_never_reflects_private_output(self):
        result = probe_user_manager(
            runner=lambda *a, **k: completed("private service path")
        )
        self.assertEqual(result.state, "UNKNOWN")
        self.assertNotIn("private", repr(result))
        self.assertNotIn("path", repr(result))

    def test_rejects_unexpected_runner_result(self):
        self.assertEqual(probe_user_manager(
            runner=lambda *a, **k: None
        ).state, "UNKNOWN")

    def test_non_callable_runner_unknown(self):
        self.assertEqual(probe_user_manager(runner=None).state, "UNKNOWN")

    def test_minimum_environment_does_not_forward_secrets(self):
        with patch.dict("os.environ", {
            "ISC_PRIVATE_SECRET": "not-for-bus",
            "PATH": "/usr/bin",
            "XDG_RUNTIME_DIR": "/run/user/1000",
        }, clear=True):
            runner = Mock(return_value=completed())
            probe_user_manager(runner=runner)
            env = runner.call_args.kwargs["env"]
            self.assertNotIn("ISC_PRIVATE_SECRET", env)
            self.assertEqual(env["PATH"], "/usr/bin")


if __name__ == "__main__":
    unittest.main()

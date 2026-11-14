"""Opt-in detached protected commands: all external operations are mocked.

No transient systemd user service or native inhibitor is started by tests.
"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.detached_work import (
    DetachedWorkResult, detached_work_command, launch_detached_work,
)
from isc.cli import main


UNIT = "isc-protected-0123456789abcdef.service"


class DetachedWorkTests(unittest.TestCase):
    def test_builder_creates_transient_user_service_with_native_inhibitor(self):
        result = detached_work_command(("/usr/bin/true",), unit=UNIT)
        self.assertEqual(result, (
            "/usr/bin/systemd-run",
            "--user", "--collect", "--service-type=exec", "--same-dir",
            "--description=ISC protected detached work",
            "--unit=" + UNIT,
            "--",
            "/usr/bin/systemd-inhibit",
            "--what=idle:sleep", "--mode=block",
            "--who=Intelligent Sleep Controller",
            "--why=Protected foreground work",
            "--",
            "/usr/bin/true",
        ))
        self.assertNotIn("--system", result)

    def test_no_shell_or_interpreter_is_added(self):
        args = ("python3", "-m", "compileall", "src")
        result = detached_work_command(args, unit=UNIT)
        self.assertEqual(result[-4:], args)
        self.assertNotIn("/bin/sh", result)
        self.assertNotIn("bash", result)

    def test_all_dollar_signs_are_escaped_for_systemd_manager(self):
        args = ("foo$bar", 'literal-$HOME', 'x$(id)')
        result = detached_work_command(args, unit=UNIT)
        self.assertEqual(result[-3:], (
            "foo$$bar", "literal-$$HOME", "x$$(id)",
        ))

    def test_unit_names_are_strict_and_private_details_not_used(self):
        for value in (
            "", "service", "some.service", "isc-protected-something.service",
            "isc-protected-0123456789ABCDEF.service",
            "isc-protected-0123456789abcdef.service\n",
            "isc-protected-0123456789abcde.service",
            "../isc-protected-0123456789abcdef.service",
        ):
            with self.subTest(value=value):
                self.assertIsNone(detached_work_command(("true",), unit=value))

    def test_invalid_command_is_rejected_before_systemd(self):
        for value in ((), ("/usr/bin/true", ""), ("cmd", "x\x00y")):
            with self.subTest(value=value):
                self.assertIsNone(detached_work_command(value, unit=UNIT))
        with patch("isc.detached_work.subprocess.run") as systemd:
            self.assertEqual(
                launch_detached_work(()).reason,
                "invalid_protected_command",
            )
        systemd.assert_not_called()

    @patch("isc.detached_work.secrets.token_hex", return_value="0123456789abcdef")
    @patch("isc.detached_work.subprocess.run", return_value=Mock(returncode=0))
    def test_success_reports_started_not_verified_inhibitor(self, run, secret):
        result = launch_detached_work(("/usr/bin/true",))
        self.assertEqual(result, DetachedWorkResult(
            True, "service_started_inhibitor_unverified", UNIT,
        ))
        run.assert_called_once_with(
            detached_work_command(("/usr/bin/true",), unit=UNIT),
            capture_output=True, text=True, check=False, timeout=15,
        )

    @patch("isc.detached_work.secrets.token_hex", return_value="0123456789abcdef")
    @patch("isc.detached_work.subprocess.run", return_value=Mock(
        returncode=1, stderr="private user metadata",
    ))
    def test_launch_error_never_leaks_arguments_or_stderr(self, run, secret):
        result = launch_detached_work(("echo", "private-secret"))
        self.assertFalse(result.started)
        self.assertEqual(result.reason, "native_launch_rejected")
        self.assertNotIn("private", repr(result))

    @patch("isc.detached_work.secrets.token_hex", return_value="0123456789abcdef")
    @patch("isc.detached_work.subprocess.run",
           side_effect=subprocess.TimeoutExpired("systemd-run", 15))
    def test_native_timeout_is_unknown_not_safe_retry(self, run, secret):
        result = launch_detached_work(("sleep", "120"))
        self.assertEqual(
            result, DetachedWorkResult(False, "launch_outcome_unknown", UNIT),
        )

    @patch("isc.detached_work.secrets.token_hex", return_value="0123456789abcdef")
    @patch("isc.detached_work.subprocess.run",
           side_effect=OSError("private credentials"))
    def test_missing_systemd_fails_without_private_errors(self, run, secret):
        result = launch_detached_work(("some-task",))
        self.assertEqual(
            result, DetachedWorkResult(False, "user_systemd_unavailable"),
        )
        self.assertNotIn("private", repr(result))

    def test_cli_missing_separator_does_not_launch_anything(self):
        with patch("isc.cli.launch_detached_work") as runner:
            for command in (
                ["protect-detached", "make"],
                ["protect-detached", "--"],
                ["protect-detached"],
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(command), 2)
        runner.assert_not_called()

    def test_cli_explicit_success_does_not_claim_inhibitor_verified(self):
        with patch("isc.cli.launch_detached_work", return_value=
                   DetachedWorkResult(
                       True, "service_started_inhibitor_unverified", UNIT
                   )) as runner, redirect_stdout(StringIO()) as out:
            self.assertEqual(main([
                "protect-detached", "--", "/usr/bin/true",
            ]), 0)
        text = out.getvalue()
        self.assertIn(UNIT, text)
        self.assertIn("inhibitor acquisition: NOT YET VERIFIED", text)
        self.assertNotIn("sleep permitted", text)
        runner.assert_called_once_with(["/usr/bin/true"])

    def test_cli_failed_start_does_not_log_user_args(self):
        with patch("isc.cli.launch_detached_work",
                   return_value=DetachedWorkResult(
                       False, "native_launch_rejected",
                   )) as runner, redirect_stdout(StringIO()) as out:
            self.assertEqual(main([
                "protect-detached", "--", "echo", "private-token",
            ]), 2)
        self.assertNotIn("private-token", out.getvalue())
        runner.assert_called_once_with(["echo", "private-token"])

    def test_cli_never_changes_saved_mode_or_power_settings(self):
        with patch("isc.cli.launch_detached_work",
                   return_value=DetachedWorkResult(
                       True, "service_started_inhibitor_unverified", UNIT,
                   )), patch("isc.settings.set_mode",
                             side_effect=AssertionError("no settings")), \
             patch("isc.cli.change_gnome_policy",
                   side_effect=AssertionError("no power settings")), \
             redirect_stdout(StringIO()):
            self.assertEqual(main([
                "protect-detached", "--", "make", "-j4",
            ]), 0)


if __name__ == "__main__":
    unittest.main()

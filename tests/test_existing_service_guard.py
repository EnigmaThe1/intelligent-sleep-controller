"""No real native inhibitor, service, or power action during these tests."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.existing_service_check import ExistingServiceObservation
from isc.existing_service_guard import (
    existing_service_guard_command, start_existing_service_guard,
    run_existing_guard_worker, valid_guard_duration,
)
from isc.cli import main

UNIT = "existing-user-build.service"
VERIFIED = ExistingServiceObservation(
    3, "service_process_membership_observed_task_state_unknown",
    verified_service_membership=True,
)
UNKNOWN = ExistingServiceObservation(None, "service_workload_unverified")


class ExistingServiceGuardTests(unittest.TestCase):
    def test_native_command_blocks_idle_and_sleep_without_shell_or_service_change(self):
        cmd = existing_service_guard_command(
            UNIT, python_executable="/usr/bin/python3",
        )
        self.assertEqual(cmd, (
            "/usr/bin/systemd-inhibit",
            "--what=idle:sleep",
            "--mode=block",
            "--who=Intelligent Sleep Controller",
            "--why=Protected foreground work",
            "--",
            "/usr/bin/python3", "-m", "isc",
            "_existing-guard-worker", UNIT,
        ))
        self.assertNotIn("systemctl", cmd)
        self.assertNotIn("systemd-run", cmd)
        self.assertNotIn("bash", cmd)
        self.assertNotIn("sh", cmd)

    def test_reject_bad_service_before_any_native_action(self):
        for name in (
            "", None, 1, "not-a-unit", "malformed.timer",
            "private.service; rm -rf /", "../other.service",
            "secret.service\n", "-bad-thing", "name.service --system",
        ):
            with self.subTest(name=name):
                self.assertIsNone(existing_service_guard_command(name))
                with patch("isc.existing_service_guard.os.execv") as runner:
                    self.assertEqual(start_existing_service_guard(name), 2)
                runner.assert_not_called()

    def test_reject_untrusted_python_executable(self):
        for interpreter in (
            "python3", "/usr/bin/python3\x00something", "", 123,
            None,
        ):
            with self.subTest(interpreter=interpreter):
                # A None override means "current interpreter" by design.
                if interpreter is None:
                    self.assertIsNotNone(
                        existing_service_guard_command(
                            UNIT, python_executable=None,
                        )
                    )
                else:
                    self.assertIsNone(
                        existing_service_guard_command(
                            UNIT, python_executable=interpreter,
                        )
                    )

    def test_existing_service_must_be_verified_before_inhibitor_exec(self):
        for evidence in (
            None, UNKNOWN,
            ExistingServiceObservation(0, "unknown"),
            ExistingServiceObservation(0, "forged", True, False, False),
            ExistingServiceObservation(None, "forged", True, False, False),
            ExistingServiceObservation(
                1, "forged task activity", True, True, False,
            ),
            ExistingServiceObservation(
                1, "forged complete coverage", True, False, True,
            ),
            "not evidence",
        ):
            with self.subTest(evidence=evidence):
                with patch("isc.existing_service_guard.os.execv") as runner:
                    self.assertEqual(
                        start_existing_service_guard(
                            UNIT, probe=lambda name: evidence,
                        ), 2,
                    )
                runner.assert_not_called()

    def test_verified_service_launches_only_native_inhibitor(self):
        with patch("isc.existing_service_guard.os.execv",
                   side_effect=OSError("mock exec")) as runner:
            code = start_existing_service_guard(
                UNIT, probe=lambda name: VERIFIED,
            )
        self.assertEqual(code, 2)
        runner.assert_called_once()
        args = runner.call_args.args
        self.assertEqual(args[0], "/usr/bin/systemd-inhibit")
        self.assertEqual(args[1], existing_service_guard_command(UNIT))

    def test_failed_native_exec_returns_generic_result(self):
        with patch("isc.existing_service_guard.os.execv",
                   side_effect=OSError("private mount path")):
            result = start_existing_service_guard(
                UNIT, probe=lambda name: VERIFIED,
            )
        self.assertEqual(result, 2)

    def test_missing_service_probe_exception_prevents_guard(self):
        for failure in (OSError("unreadable"), RuntimeError("sensitive")):
            with self.subTest(failure=type(failure).__name__):
                with patch("isc.existing_service_guard.os.execv") as execv:
                    self.assertEqual(
                        start_existing_service_guard(
                            UNIT, probe=Mock(side_effect=failure),
                        ), 2,
                    )
                execv.assert_not_called()

    def test_worker_never_exits_merely_because_service_disappears(self):
        samples = iter((VERIFIED, UNKNOWN, UNKNOWN))
        calls = []
        def read(unit):
            return next(samples)
        def sleep(seconds):
            calls.append(seconds)
            if len(calls) >= 3:
                raise KeyboardInterrupt
        with redirect_stdout(StringIO()) as out:
            result = run_existing_guard_worker(
                UNIT, probe=read, wait=sleep,
            )
        self.assertEqual(result, 0)
        self.assertEqual(calls, [30.0, 30.0, 30.0])
        text = out.getvalue()
        self.assertIn("TASK STATE UNKNOWN", text)
        self.assertIn("SERVICE/WORKLOAD UNKNOWN; KEEP PROTECTION", text)
        self.assertNotIn(UNIT, text)
        self.assertNotIn("finished", text)

    def test_worker_reader_failure_retains_guard_until_manual_cancel(self):
        calls = [0]
        def probe(unit):
            calls[0] += 1
            raise OSError("sensitive private service")
        def interrupt(seconds):
            if calls[0] >= 2:
                raise KeyboardInterrupt
        with redirect_stdout(StringIO()) as out:
            result = run_existing_guard_worker(
                UNIT, probe=probe, wait=interrupt,
            )
        self.assertEqual(result, 0)
        self.assertEqual(calls[0], 2)
        self.assertIn("UNKNOWN; KEEP PROTECTION", out.getvalue())
        self.assertNotIn("sensitive", out.getvalue())

    def test_worker_unrecoverable_wait_error_is_not_reported_as_completion(self):
        with redirect_stdout(StringIO()) as out:
            result = run_existing_guard_worker(
                UNIT, probe=lambda _: VERIFIED,
                wait=Mock(side_effect=OSError("unexpected failure")),
            )
        self.assertEqual(result, 2)
        self.assertIn("task state UNKNOWN", out.getvalue())

    def test_invalid_worker_args_never_probe_or_sleep(self):
        for service in ("other.timer", "", None):
            with patch("isc.existing_service_guard.observe_existing_user_service") as probe:
                self.assertEqual(
                    run_existing_guard_worker(service, probe=probe), 2,
                )
                probe.assert_not_called()

    def test_cli_requires_double_dash_and_exactly_one_unit(self):
        with patch("isc.cli.start_existing_service_guard") as launcher:
            with redirect_stdout(StringIO()):
                for args in (
                    ["protect-existing"],
                    ["protect-existing", UNIT],
                    ["protect-existing", "--"],
                    ["protect-existing", "--", UNIT, "extra"],
                ):
                    self.assertEqual(main(args), 2)
            launcher.assert_not_called()

    def test_cli_forwards_valid_unit_but_never_changes_mode(self):
        with patch("isc.cli.start_existing_service_guard",
                   return_value=2) as launcher, \
             patch("isc.settings.set_mode",
                   side_effect=AssertionError("mode changed")), \
             patch("isc.cli.change_gnome_policy",
                   side_effect=AssertionError("GNOME changed")), \
             redirect_stdout(StringIO()):
            self.assertEqual(main(["protect-existing", "--", UNIT]), 2)
        launcher.assert_called_once_with(UNIT)

    def test_cli_worker_route_does_not_launch_or_claim_inhibitor(self):
        with patch("isc.cli.run_existing_guard_worker",
                   return_value=0) as worker, \
             patch("isc.cli.start_existing_service_guard",
                   side_effect=AssertionError("unexpected execution")), \
             redirect_stdout(StringIO()):
            self.assertEqual(main(["_existing-guard-worker", UNIT]), 0)
        worker.assert_called_once_with(UNIT)

    def test_duration_limits_are_strict_and_optional(self):
        self.assertTrue(valid_guard_duration(None))
        for seconds in (1, 12, 14400):
            self.assertTrue(valid_guard_duration(seconds))
        for invalid in (0, -1, 14401, 1.5, True, False, "12", float("inf")):
            with self.subTest(invalid=invalid):
                self.assertFalse(valid_guard_duration(invalid))
                self.assertIsNone(
                    existing_service_guard_command(
                        UNIT, duration_seconds=invalid,
                    )
                )

    def test_bounded_command_uses_exact_native_inhibitor_and_worker(self):
        command = existing_service_guard_command(
            UNIT, duration_seconds=12, python_executable="/usr/bin/python3",
        )
        self.assertEqual(command[:6], (
            "/usr/bin/systemd-inhibit", "--what=idle:sleep",
            "--mode=block", "--who=Intelligent Sleep Controller",
            "--why=Protected foreground work", "--",
        ))
        self.assertEqual(command[6:], (
            "/usr/bin/python3", "-m", "isc",
            "_existing-guard-worker", UNIT, "12",
        ))
        self.assertNotIn("timeout", command)
        self.assertNotIn("systemctl", command)

    def test_bounded_guard_preflight_before_native_exec(self):
        with patch("isc.existing_service_guard.os.execv",
                   side_effect=OSError("native launch mocked")) as runner:
            self.assertEqual(
                start_existing_service_guard(
                    UNIT, duration_seconds=12, probe=lambda _: VERIFIED,
                ), 2,
            )
        self.assertEqual(runner.call_args.args[1],
                         existing_service_guard_command(
                             UNIT, duration_seconds=12,
                         ))
        runner.reset_mock()
        self.assertEqual(
            start_existing_service_guard(
                UNIT, duration_seconds=12, probe=lambda _: UNKNOWN,
            ), 2,
        )
        runner.assert_not_called()

    def test_bounded_guard_exits_itself_at_deadline_without_signals(self):
        marks = iter((100.0, 100.0, 103.0, 112.0))
        waits = []
        probes = []
        with redirect_stdout(StringIO()) as out:
            result = run_existing_guard_worker(
                UNIT, duration_seconds=12,
                probe=lambda unit: (probes.append(unit) or VERIFIED),
                clock=lambda: next(marks),
                wait=lambda seconds: waits.append(seconds),
            )
        self.assertEqual(result, 0)
        self.assertEqual(waits, [12, 9])
        self.assertEqual(len(probes), 3)
        self.assertIn("Selected protection period ended", out.getvalue())
        self.assertIn("task state UNKNOWN", out.getvalue())
        self.assertNotIn(UNIT, out.getvalue())

    def test_bounded_guard_expires_even_if_service_disappears(self):
        marks = iter((1.0, 1.0, 5.0))
        statuses = iter((VERIFIED, UNKNOWN))
        with redirect_stdout(StringIO()) as out:
            result = run_existing_guard_worker(
                UNIT, duration_seconds=4,
                probe=lambda _: next(statuses),
                clock=lambda: next(marks),
                wait=lambda seconds: None,
            )
        self.assertEqual(result, 0)
        self.assertIn("UNKNOWN; KEEP PROTECTION", out.getvalue())
        self.assertIn("Selected protection period ended", out.getvalue())
        self.assertNotIn("work completed", out.getvalue())

    def test_invalid_or_unavailable_clock_is_not_a_successful_expiry(self):
        for clock in (
            lambda: float("nan"),
            lambda: float("inf"),
            lambda: -1.0,
            Mock(side_effect=OSError("private clock")),
        ):
            with self.subTest(clock=clock):
                probe = Mock()
                with redirect_stdout(StringIO()):
                    self.assertEqual(run_existing_guard_worker(
                        UNIT, duration_seconds=10,
                        clock=clock, probe=probe,
                    ), 2)
                probe.assert_not_called()

    def test_backward_clock_does_not_report_completion(self):
        marks = iter((100.0, 99.0))
        with redirect_stdout(StringIO()) as out:
            self.assertEqual(run_existing_guard_worker(
                UNIT, duration_seconds=10, clock=lambda: next(marks),
                wait=Mock(side_effect=AssertionError("must not wait")),
                probe=lambda _: VERIFIED,
            ), 2)
        self.assertIn("clock unavailable", out.getvalue())
        self.assertNotIn("period ended", out.getvalue())

    def test_cli_bounded_guard_requires_exact_syntax(self):
        invalid = (
            ["protect-existing", "--for", "0", "--", UNIT],
            ["protect-existing", "--for", "14401", "--", UNIT],
            ["protect-existing", "--for", "-1", "--", UNIT],
            ["protect-existing", "--for", "12s", "--", UNIT],
            ["protect-existing", "--for", "12", UNIT],
            ["protect-existing", "--for", "12", "--", UNIT, "extra"],
            ["protect-existing", "--for", "999999999999", "--", UNIT],
            ["protect-existing", "--for", "true", "--", UNIT],
        )
        with patch("isc.cli.start_existing_service_guard") as runner:
            for args in invalid:
                with self.subTest(args=args), redirect_stdout(StringIO()):
                    self.assertEqual(main(args), 2)
        runner.assert_not_called()

    def test_cli_bounded_guard_launches_only_with_explicit_duration(self):
        with patch("isc.cli.start_existing_service_guard",
                   return_value=2) as runner, redirect_stdout(StringIO()):
            self.assertEqual(
                main(["protect-existing", "--for", "12", "--", UNIT]), 2,
            )
        runner.assert_called_once_with(UNIT, duration_seconds=12)

    def test_cli_bounded_worker_validates_duration_before_running(self):
        with patch("isc.cli.run_existing_guard_worker",
                   return_value=0) as worker:
            self.assertEqual(
                main(["_existing-guard-worker", UNIT, "12"]), 0,
            )
            worker.assert_called_once_with(UNIT, duration_seconds=12)
            worker.reset_mock()
            self.assertEqual(
                main(["_existing-guard-worker", UNIT, "0"]), 2,
            )
            worker.assert_not_called()

    def test_existing_safety_check_command_remains_accessible(self):
        # Guard work must not shadow cli.check() or arm automatic suspend.
        with patch("isc.cli.check", return_value=0) as check, \
             patch("isc.cli.start_existing_service_guard",
                   side_effect=AssertionError("unexpected guard")), \
             redirect_stdout(StringIO()):
            self.assertEqual(main(["check"]), 0)
        check.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

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
    run_existing_guard_worker,
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

"""Safe, read-only systemd timer-linked PID metadata tests."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.timer_service_processes import (
    _pid_query, parse_process_indicators,
    read_timer_service_process_indicators,
)


class TimerServicePIDTests(unittest.TestCase):
    def test_aggregate_service_pids_without_leaking_identity(self):
        timers = ("one.timer", "two.timer", "three.timer")
        association = (
            "Id=one.timer\nUnit=shared.service\n\n"
            "Id=two.timer\nUnit=shared.service\n\n"
            "Id=three.timer\nUnit=other.service\n"
        )
        pids = (
            "Id=other.service\nMainPID=0\nControlPID=11\n\n"
            "Id=shared.service\nMainPID=42\nControlPID=0\n"
        )
        result = parse_process_indicators(association, pids, timers)
        self.assertEqual(
            (result.timers_inspected, result.distinct_services,
             result.main_pid_nonzero, result.control_pid_nonzero,
             result.neither_pid_reported), (3, 2, 1, 1, 0),
        )
        self.assertFalse(result.coverage_verified)
        self.assertNotIn("shared.service", repr(result))
        self.assertNotIn("42", repr(result))

    def test_zero_pids_are_not_trusted_as_quiescence(self):
        result = parse_process_indicators(
            "Id=a.timer\nUnit=a.service\n",
            "Id=a.service\nMainPID=0\nControlPID=0\n",
            ("a.timer",),
        )
        self.assertEqual(result.neither_pid_reported, 1)
        self.assertFalse(result.coverage_verified)

    def test_malformed_values_missing_and_duplicate_states_fail_closed(self):
        association = "Id=a.timer\nUnit=a.service\n"
        for raw in (
            None,
            "Id=a.service\nMainPID=-1\nControlPID=0\n",
            "Id=a.service\nMainPID=unknown\nControlPID=0\n",
            "Id=a.service\nMainPID=0\n",
            "Id=a.service\nMainPID=0\nMainPID=1\nControlPID=0\n",
            "Id=wrong.service\nMainPID=0\nControlPID=0\n",
            "Id=a.service\nMainPID=99999999\nControlPID=0\n",
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_process_indicators(
                    association, raw, ("a.timer",),
                ))

    def test_rejects_unsafe_service_queries_and_empty_census(self):
        for scope, names in (
            ("--wrong", ("a.service",)),
            ("--system", ("a.timer",)),
            ("--system", ("a.service", "a.service")),
            ("--user", ()),
        ):
            with self.assertRaises(ValueError):
                _pid_query(scope, names)
        zero = parse_process_indicators("", "", ())
        self.assertEqual(zero.distinct_services, 0)
        self.assertFalse(zero.coverage_verified)

    def test_read_only_system_and_user_metadata_queries(self):
        with patch("isc.timer_service_processes._capture", side_effect=[
            "a.timer loaded active waiting Timer\n",
            "Id=a.timer\nUnit=a.service\n",
            "Id=a.service\nMainPID=123\nControlPID=0\n",
            "b.timer loaded active waiting Timer\n",
            "Id=b.timer\nUnit=b.service\n",
            "Id=b.service\nMainPID=0\nControlPID=0\n",
        ]) as capture:
            system, user = read_timer_service_process_indicators()
        self.assertEqual(system.main_pid_nonzero, 1)
        self.assertEqual(user.neither_pid_reported, 1)
        commands = [call.args[0] for call in capture.call_args_list]
        self.assertEqual(commands[2], _pid_query("--system", ("a.service",)))
        self.assertEqual(commands[5], _pid_query("--user", ("b.service",)))
        self.assertTrue(all(cmd[0] == "systemctl" and
                            ("show" in cmd or "list-units" in cmd)
                            for cmd in commands))

    def test_failed_scope_remains_unknown(self):
        with patch("isc.timer_service_processes._capture", side_effect=[
            None,
            "b.timer loaded active waiting Timer\n",
            "Id=b.timer\nUnit=b.service\n",
            None,
        ]):
            system, user = read_timer_service_process_indicators()
        self.assertIsNone(system)
        self.assertIsNone(user)


if __name__ == "__main__":
    unittest.main()

"""Timer-linked service cgroup task-count observation regression tests."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.timer_service_tasks import (
    _task_query, parse_timer_service_tasks, read_timer_service_tasks,
)


class TimerServiceTaskTests(unittest.TestCase):
    def test_shared_service_and_unknown_count_not_accepted_as_idle(self):
        links = (
            "Id=one.timer\nUnit=shared.service\n\n"
            "Id=two.timer\nUnit=shared.service\n\n"
            "Id=three.timer\nUnit=other.service\n"
        )
        raw = (
            "Id=other.service\nControlGroup=\nTasksCurrent=[not set]\n\n"
            "Id=shared.service\nControlGroup=/system.slice/shared.service\n"
            "TasksCurrent=4\n"
        )
        result = parse_timer_service_tasks(
            links, raw, ("one.timer", "two.timer", "three.timer"),
        )
        self.assertEqual(
            (result.timers_inspected, result.distinct_services,
             result.cgroups_reported, result.tasks_present,
             result.zero_tasks_reported, result.unknown_task_counts),
            (3, 2, 1, 1, 0, 1),
        )
        self.assertFalse(result.coverage_verified)
        self.assertNotIn("shared.service", repr(result))

    def test_zero_count_is_observed_but_never_proves_quiescence(self):
        outcome = parse_timer_service_tasks(
            "Id=a.timer\nUnit=a.service\n",
            "Id=a.service\nControlGroup=\nTasksCurrent=0\n",
            ("a.timer",),
        )
        self.assertEqual(outcome.zero_tasks_reported, 1)
        self.assertFalse(outcome.coverage_verified)

    def test_missing_malformed_and_duplicate_metadata_are_unknown(self):
        links = "Id=a.timer\nUnit=a.service\n"
        valid = "Id=a.service\nControlGroup=/user.slice/a.service\nTasksCurrent=3\n"
        for assoc, state in (
            (None, valid),
            (links, None),
            ("Id=a.timer\nUnit=a.timer\n", valid),
            (links, "Id=a.service\nTasksCurrent=3\n"),
            (links, "Id=a.service\nControlGroup=/foo\nTasksCurrent=-1\n"),
            (links, "Id=a.service\nControlGroup=/foo\nTasksCurrent=abc\n"),
            (links, "Id=a.service\nControlGroup=relative\nTasksCurrent=0\n"),
            (links, "Id=a.service\nControlGroup=/foo\nTasksCurrent=3\nTasksCurrent=4\n"),
            (links, "Id=other.service\nControlGroup=\nTasksCurrent=0\n"),
        ):
            with self.subTest(assoc=assoc, state=state):
                self.assertIsNone(parse_timer_service_tasks(assoc, state, ("a.timer",)))

    def test_64bit_unknown_sentinel_is_not_zero(self):
        result = parse_timer_service_tasks(
            "Id=a.timer\nUnit=a.service\n",
            "Id=a.service\nControlGroup=\nTasksCurrent=18446744073709551615\n",
            ("a.timer",),
        )
        self.assertEqual(result.unknown_task_counts, 1)

    def test_empty_scope_and_invalid_query(self):
        result = parse_timer_service_tasks("", "", ())
        self.assertEqual(result.distinct_services, 0)
        self.assertFalse(result.coverage_verified)
        for scope, names in (
            ("--invalid", ("a.service",)),
            ("--user", ()),
            ("--system", ("a.timer",)),
            ("--system", ("a.service", "a.service")),
        ):
            with self.assertRaises(ValueError):
                _task_query(scope, names)

    def test_read_only_systemd_queries_and_independent_scope_failures(self):
        with patch("isc.timer_service_tasks._capture", side_effect=[
            "a.timer loaded active waiting A\n",
            "Id=a.timer\nUnit=a.service\n",
            "Id=a.service\nControlGroup=/system.slice/a.service\nTasksCurrent=2\n",
            "b.timer loaded active waiting B\n",
            "Id=b.timer\nUnit=b.service\n",
            None,
        ]) as capture:
            system, user = read_timer_service_tasks()
        self.assertEqual(system.tasks_present, 1)
        self.assertIsNone(user)
        commands = [call.args[0] for call in capture.call_args_list]
        self.assertEqual(len(commands), 6)
        self.assertEqual(commands[2], _task_query("--system", ("a.service",)))
        self.assertEqual(commands[5], _task_query("--user", ("b.service",)))
        self.assertTrue(all(
            command[0] == "systemctl" and
            ("list-units" in command or "show" in command)
            for command in commands
        ))


if __name__ == "__main__":
    unittest.main()

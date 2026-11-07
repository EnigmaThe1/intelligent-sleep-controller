"""Read-only timer-linked service state observation tests."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.timer_service_states import (
    _service_query, parse_linked_services, read_timer_service_states,
)


class TimerLinkedServiceTests(unittest.TestCase):
    def test_multiple_timers_can_link_to_one_service(self):
        timers = ("first.timer", "second.timer", "third.timer")
        association = (
            "Id=first.timer\nUnit=shared.service\n\n"
            "Id=second.timer\nUnit=shared.service\n\n"
            "Id=third.timer\nUnit=busy.service\n"
        )
        states = (
            "Id=busy.service\nActiveState=activating\n\n"
            "Id=shared.service\nActiveState=active\n"
        )
        data = parse_linked_services(association, states, timers)
        self.assertEqual(
            (data.timers_inspected, data.distinct_services,
             data.active, data.transitioning, data.inactive, data.failed),
            (3, 2, 1, 1, 0, 0),
        )
        self.assertFalse(data.coverage_verified)
        self.assertNotIn("shared.service", repr(data))

    def test_inactive_and_failed_are_observations_not_completion(self):
        result = parse_linked_services(
            "Id=a.timer\nUnit=done.service\n\n"
            "Id=b.timer\nUnit=failed.service\n",
            "Id=done.service\nActiveState=inactive\n\n"
            "Id=failed.service\nActiveState=failed\n",
            ("a.timer", "b.timer"),
        )
        self.assertEqual((result.inactive, result.failed), (1, 1))
        self.assertFalse(result.coverage_verified)

    def test_missing_bad_duplicate_and_unsupported_states_are_unknown(self):
        timers = ("a.timer",)
        valid = "Id=a.timer\nUnit=a.service\n"
        for associations, states in (
            (None, "Id=a.service\nActiveState=active\n"),
            (valid, None),
            ("Id=a.timer\nUnit=\n", "Id=a.service\nActiveState=active\n"),
            ("Id=a.timer\nUnit=a.timer\n", "Id=a.service\nActiveState=active\n"),
            (valid, "Id=a.service\nActiveState=unknown\n"),
            (valid, "Id=b.service\nActiveState=active\n"),
            (valid, "Id=a.service\nActiveState=active\nActiveState=inactive\n"),
            (valid, "Id=a.service\nActiveState=active\nExtra=private\n"),
        ):
            with self.subTest(associations=associations, states=states):
                self.assertIsNone(parse_linked_services(associations, states, timers))

    def test_empty_and_invalid_query_are_safe(self):
        zero = parse_linked_services("", "", ())
        self.assertEqual(zero.timers_inspected, 0)
        self.assertFalse(zero.coverage_verified)
        for scope, names in (
            ("--invalid", ("a.service",)),
            ("--system", ("a.timer",)),
            ("--system", ("a.service", "a.service")),
            ("--system", ()),
        ):
            with self.assertRaises(ValueError):
                _service_query(scope, names)

    def test_observer_is_only_read_only_systemctl_queries(self):
        with patch("isc.timer_service_states._capture", side_effect=[
            "a.timer loaded active waiting Example\n",
            "Id=a.timer\nUnit=some.service\n",
            "Id=some.service\nActiveState=active\n",
            "b.timer loaded active waiting Example\n",
            "Id=b.timer\nUnit=another.service\n",
            "Id=another.service\nActiveState=inactive\n",
        ]) as capture:
            system, user = read_timer_service_states()
        self.assertEqual(system.active, 1)
        self.assertEqual(user.inactive, 1)
        queries = [call.args[0] for call in capture.call_args_list]
        self.assertEqual(len(queries), 6)
        self.assertEqual(queries[2], _service_query("--system", ("some.service",)))
        self.assertEqual(queries[5], _service_query("--user", ("another.service",)))
        self.assertTrue(all(q[0] == "systemctl" and
                            ("show" in q or "list-units" in q)
                            for q in queries))

    def test_failed_scope_is_unknown_not_zero(self):
        with patch("isc.timer_service_states._capture", side_effect=[
            None, "b.timer loaded active waiting Example\n",
            "Id=b.timer\nUnit=b.service\n", None,
        ]):
            system, user = read_timer_service_states()
        self.assertIsNone(system)
        self.assertIsNone(user)


if __name__ == "__main__":
    unittest.main()

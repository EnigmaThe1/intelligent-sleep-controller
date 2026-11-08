"""Synthetic independent systemd service inventory; no service control."""
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.independent_service_discovery import (
    IndependentServiceCounts, _active_service_names,
    correlate_independent_services, read_independent_services,
)

SERVICES = (
    "alpha.service loaded active running Alpha daemon\n"
    "beta.service loaded activating start Beta job\n"
    "gamma.service loaded inactive dead Old job\n"
    "delta.service loaded active exited Finished unit shell\n"
)
TIMERS = (
    "alpha.timer loaded active waiting Daily task\n"
    "no-match.timer loaded active waiting Another task\n"
)
ASSOCIATIONS = (
    "Id=alpha.timer\nUnit=alpha.service\n\n"
    "Id=no-match.timer\nUnit=orphan.service\n"
)


class IndependentServiceDiscoveryTests(unittest.TestCase):
    def test_active_services_outside_timer_links_are_counted(self):
        result = correlate_independent_services(
            SERVICES, TIMERS, ASSOCIATIONS,
        )
        self.assertEqual(result.active_services, 3)
        self.assertEqual(result.timer_linked_active, 1)
        self.assertEqual(result.other_active, 2)
        self.assertFalse(result.uncertain)
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.task_owner_authenticated)
        self.assertNotIn("alpha.service", repr(result))

    def test_active_exited_is_not_treated_as_task_completion(self):
        active = _active_service_names(SERVICES)
        self.assertIn("delta.service", active)
        self.assertNotIn("gamma.service", active)

    def test_no_timers_still_observes_active_services_not_coverage(self):
        result = correlate_independent_services(SERVICES, "", None)
        self.assertEqual(result.other_active, 3)
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.task_owner_authenticated)

    def test_missing_associations_fail_closed(self):
        for assoc in (None, "", "Id=alpha.timer\nUnit=wrong.service"):
            with self.subTest(assoc=assoc):
                self.assertTrue(
                    correlate_independent_services(SERVICES, TIMERS, assoc).uncertain
                )

    def test_invalid_unit_state_and_duplicate_are_unknown(self):
        for raw in (
            SERVICES + "alpha.service loaded active running Duplicate\n",
            "relative.service badstate active running Unknown\n",
            "path/attack.service loaded active running Wrong\n",
            "a.timer loaded active waiting Wrong kind\n",
            "a.service loaded bogus running Invalid\n",
            "a.service loaded active \n",
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(_active_service_names(raw))

    def test_ambiguous_timer_associations_rejected(self):
        malformed = ASSOCIATIONS.replace(
            "Unit=orphan.service", "Unit=orphan.timer",
        )
        self.assertTrue(
            correlate_independent_services(SERVICES, TIMERS, malformed).uncertain
        )
        duplicate = ASSOCIATIONS.replace(
            "Unit=orphan.service", "Unit=alpha.service",
        )
        result = correlate_independent_services(SERVICES, TIMERS, duplicate)
        self.assertEqual(result.timer_linked_active, 1)

    def test_failed_source_is_unknown_not_empty(self):
        unknown = IndependentServiceCounts(None, None, None)
        self.assertEqual(correlate_independent_services(None, TIMERS, ASSOCIATIONS), unknown)
        self.assertEqual(correlate_independent_services(SERVICES, None, ASSOCIATIONS), unknown)
        self.assertEqual(read_independent_services(
            scope="--user", capture=Mock(side_effect=OSError("missing")),
        ), unknown)

    def test_no_timers_does_not_query_associations(self):
        answers = [SERVICES, ""]
        query = Mock(side_effect=lambda args: answers.pop(0))
        result = read_independent_services(scope="--user", capture=query)
        self.assertEqual(query.call_count, 2)
        self.assertEqual(result.other_active, 3)
        self.assertFalse(result.coverage_verified)

    def test_native_query_uses_explicit_scope_without_shell(self):
        answers = [SERVICES, TIMERS, ASSOCIATIONS]
        commands = []
        def capture(command):
            commands.append(command)
            return answers.pop(0)
        result = read_independent_services(scope="--system", capture=capture)
        self.assertEqual(result.other_active, 2)
        self.assertEqual(len(commands), 3)
        self.assertTrue(all(cmd[:2] == ("systemctl", "--system")
                            for cmd in commands))
        self.assertTrue(all("shell" not in cmd for cmd in commands))

    def test_read_only_cli_reports_aggregate_counts_not_names(self):
        from isc.cli import main
        out = StringIO()
        observations = [
            IndependentServiceCounts(3, 1, 2, uncertain=False),
            IndependentServiceCounts(None, None, None),
        ]
        with patch("isc.cli.read_independent_services",
                   side_effect=observations) as reader:
            with redirect_stdout(out):
                self.assertEqual(main(["independent-services"]), 0)
        self.assertEqual(reader.call_count, 2)
        value = out.getvalue()
        self.assertIn("other active=2", value)
        self.assertIn("User: UNKNOWN", value)
        self.assertIn("Automatic suspension remains disabled", value)
        self.assertNotIn("alpha.service", value)
        self.assertNotIn("agent.service", value)

    def test_service_count_is_not_claimed_to_be_task_coverage(self):
        result = correlate_independent_services("", "", None)
        self.assertEqual(result.active_services, 0)
        self.assertEqual(result.other_active, 0)
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.task_owner_authenticated)

    def test_invalid_scope_never_accesses_native_state(self):
        capture = Mock()
        result = read_independent_services(scope="--invalid", capture=capture)
        self.assertTrue(result.uncertain)
        capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Read-only scheduler evidence: timer scheduling is not active job state."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.scheduled_work import (
    _unit_query, parse_unit_states, read_scheduled_work,
    _timer_file_query, parse_timer_unit_files, read_timer_unit_files,
)


class ScheduledWorkTests(unittest.TestCase):
    def test_timer_waiting_vs_loaded_and_elapsed(self):
        raw = (
            "apt-daily.timer loaded active waiting Some scheduled maintenance\n"
            "○ user-backup.timer loaded active elapsed Backups\n"
            "● example.timer loaded failed failed Unhealthy timer\n"
        )
        info = parse_unit_states(raw, kind="timer")
        self.assertIsNotNone(info)
        self.assertEqual(info.listed, 3)
        self.assertEqual(info.waiting_timers, 1)
        self.assertEqual(info.running_services, 0)
        self.assertFalse(info.coverage_verified)

    def test_services_running_activating_and_exited_are_distinct(self):
        raw = (
            "example.service loaded active running Actual running service\n"
            "starting@1.service loaded activating start-pre Starting service\n"
            "oneshot.service loaded active exited Service already exited\n"
            "inactive.service loaded inactive dead Inactive service\n"
        )
        info = parse_unit_states(raw, kind="service")
        self.assertIsNotNone(info)
        self.assertEqual(info.listed, 4)
        self.assertEqual(info.running_services, 1)
        self.assertEqual(info.activating_services, 1)
        self.assertEqual(info.waiting_timers, 0)

    def test_documented_service_maintenance_and_refreshing_are_parsed(self):
        raw = (
            r"dbus\x2dtest.service loaded refreshing running Refreshing service"
            + "\n"
            + "custom.service loaded maintenance dead Maintenance underway\n"
        )
        result = parse_unit_states(raw, kind="service")
        self.assertIsNotNone(result)
        self.assertEqual(result.listed, 2)
        self.assertFalse(result.coverage_verified)

    def test_empty_successful_output_is_zero_but_never_verified(self):
        for kind in ("timer", "service"):
            info = parse_unit_states("", kind=kind)
            self.assertEqual(info.listed, 0)
            self.assertFalse(info.coverage_verified)

    def test_unknown_scope_and_bad_rows_fail_closed(self):
        for kind, raw in (
            ("timer", None), ("service", None),
            ("timer", "just noise"),
            ("timer", "wrong.service loaded active running test"),
            ("service", "wrong.timer loaded active waiting example"),
            ("service", "x.service loaded surprising running example"),
            ("timer", "x.timer loaded active"),
            ("service", "x.service weird active running x"),
        ):
            with self.subTest(kind=kind, raw=raw):
                self.assertIsNone(parse_unit_states(raw, kind=kind))
        self.assertIsNone(parse_unit_states("a.timer loaded active waiting", kind="unknown"))
        self.assertIsNone(parse_unit_states(
            "\n".join("x.timer loaded active waiting" for _ in range(4097)),
            kind="timer",
        ))

    def test_read_only_system_and_user_queries_do_not_start_jobs(self):
        with patch("isc.scheduled_work._capture") as capture:
            capture.side_effect = [
                "a.timer loaded active waiting Future system timer\n",
                "b.timer loaded inactive dead Unscheduled user timer\n",
                "running.service loaded active running Running job\n",
                "notyet.service loaded activating start-post Starting user job\n",
            ]
            result = read_scheduled_work()
        self.assertEqual(result.system_timers.listed, 1)
        self.assertEqual(result.system_timers.waiting_timers, 1)
        self.assertEqual(result.user_timers.waiting_timers, 0)
        self.assertEqual(result.system_services.running_services, 1)
        self.assertEqual(result.user_services.activating_services, 1)
        self.assertFalse(result.coverage_verified)
        argv = [call.args[0] for call in capture.call_args_list]
        self.assertEqual(argv, [
            _unit_query("--system", "timer"),
            _unit_query("--user", "timer"),
            _unit_query("--system", "service"),
            _unit_query("--user", "service"),
        ])
        self.assertTrue(all("list-units" in x for x in argv))
        self.assertTrue(all(
            "start" not in x and "stop" not in x and "suspend" not in x
            for x in argv
        ))
        self.assertNotIn("Future system timer", repr(result))
        self.assertNotIn("Running job", repr(result))

    def test_one_failed_scope_is_unknown_not_zero(self):
        with patch("isc.scheduled_work._capture",
                   side_effect=[None, "", "", ""]):
            result = read_scheduled_work()
        self.assertIsNone(result.system_timers)
        self.assertEqual(result.user_timers.listed, 0)
        self.assertFalse(result.coverage_verified)

    def test_timer_file_states_and_unloaded_definitions(self):
        raw = (
            "apt-daily.timer enabled enabled\n"
            "backup@.timer disabled disabled\n"
            "dbus\\\\x2dtest.timer static -\n"
            "ephemeral.timer generated -\n"
        )
        result = parse_timer_unit_files(raw)
        self.assertIsNotNone(result)
        self.assertEqual((result.listed, result.enabled,
                          result.disabled, result.other), (4, 1, 1, 2))
        self.assertFalse(result.coverage_verified)

    def test_timer_file_malformed_unknown_and_duplicate(self):
        cases = (None, "not a valid output", "x.service enabled enabled",
                 "x.timer custom enabled",
                 "x.timer enabled enabled\nx.timer enabled enabled",
                 "x.timer enabled invalid-preset",
                 "\n".join("x.timer enabled" for _ in range(4097)))
        for raw in cases:
            with self.subTest(raw=repr(raw)[:48]):
                self.assertIsNone(parse_timer_unit_files(raw))

    def test_timer_file_queries_do_not_mutate_system_state(self):
        with patch("isc.scheduled_work._capture",
                   side_effect=["a.timer enabled enabled\n", None]) as capture:
            system, user = read_timer_unit_files()
        self.assertEqual(system.enabled, 1)
        self.assertIsNone(user)
        self.assertFalse(system.coverage_verified)
        self.assertEqual([call.args[0] for call in capture.call_args_list], [
            _timer_file_query("--system"), _timer_file_query("--user"),
        ])
        self.assertTrue(all("list-unit-files" in x for x in
                            [call.args[0] for call in capture.call_args_list]))
        with self.assertRaises(ValueError):
            _timer_file_query("--invalid")

    def test_invalid_unit_query_arguments_never_produce_command(self):
        for scope, kind in (("--bad", "timer"), ("--user", "job")):
            with self.assertRaises(ValueError):
                _unit_query(scope, kind)


if __name__ == "__main__":
    unittest.main()

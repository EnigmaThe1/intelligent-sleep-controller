"""Read-only timer-linked service identity transition regression tests."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.timer_service_lifecycle import (
    ServiceTaskSnapshot, compare_service_task_snapshots,
    read_service_task_snapshots,
)
from isc.cli import timer_service_watch_cli


def snap(*rows):
    return ServiceTaskSnapshot(rows)


class TimerServiceLifecycleTests(unittest.TestCase):
    def test_identity_changes_not_hidden_by_equal_aggregate_counts(self):
        earlier = snap(("first.service", "/a", 2), ("second.service", "/b", 0))
        later = snap(("third.service", "/c", 2), ("second.service", "/b", 0))
        result = compare_service_task_snapshots(earlier, later)
        self.assertEqual(result.appeared, 1)
        self.assertEqual(result.disappeared, 1)
        self.assertEqual(result.unchanged_without_tasks, 1)
        self.assertFalse(result.coverage_verified)
        self.assertNotIn("first.service", repr(result))

    def test_count_transitions_are_not_job_completion(self):
        before = snap(
            ("one.service", "/a", 0),
            ("two.service", "/b", 2),
            ("three.service", "/c", 1),
        )
        after = snap(
            ("one.service", "/a", 5),
            ("two.service", "/b", 0),
            ("three.service", "/c", 8),
        )
        result = compare_service_task_snapshots(before, after)
        self.assertEqual(
            (result.gained_tasks, result.lost_reported_tasks,
             result.stayed_with_tasks), (1, 1, 1),
        )
        self.assertFalse(result.coverage_verified)

    def test_unknowns_include_cgroup_changes_missing_cgroups_and_counts(self):
        before = snap(
            ("one.service", "/old", 2),
            ("two.service", "", None),
            ("three.service", "/same", None),
        )
        after = snap(
            ("one.service", "/new", 2),
            ("two.service", "", 0),
            ("three.service", "/same", 0),
        )
        result = compare_service_task_snapshots(before, after)
        self.assertEqual(result.unknown_transitions, 3)
        self.assertFalse(result.coverage_verified)

    def test_missing_or_malformed_is_unknown(self):
        valid = snap(("one.service", "/a", 1))
        self.assertIsNone(compare_service_task_snapshots(None, valid))
        self.assertIsNone(compare_service_task_snapshots(valid, None))
        self.assertIsNone(compare_service_task_snapshots(
            snap(("one.service", "/a", 1), ("one.service", "/a", 0)), valid,
        ))
        self.assertIsNone(compare_service_task_snapshots(
            snap(("one.service", "/a", -1)), valid,
        ))
        self.assertIsNone(compare_service_task_snapshots(
            snap(("invalid.timer", "/a", 1)), valid,
        ))

    def test_strict_invocation_comparison_prevents_false_continuation(self):
        first = ServiceTaskSnapshot(
            (("one.service", "/same", 3),),
            invocations=(("one.service", "a" * 32),),
        )
        second = ServiceTaskSnapshot(
            (("one.service", "/same", 3),),
            invocations=(("one.service", "b" * 32),),
        )
        comparison = compare_service_task_snapshots(
            first, second, require_invocation=True,
        )
        self.assertEqual(comparison.changed_invocations, 1)
        self.assertEqual(comparison.stayed_with_tasks, 0)
        self.assertFalse(comparison.coverage_verified)

    def test_strict_invocation_unavailable_is_unknown(self):
        first = ServiceTaskSnapshot(
            (("one.service", "/same", 3),),
            invocations=(("one.service", "a" * 32),),
        )
        after = ServiceTaskSnapshot(
            (("one.service", "/same", 3),),
            invocations=(("one.service", None),),
        )
        comparison = compare_service_task_snapshots(
            first, after, require_invocation=True,
        )
        self.assertEqual(comparison.unknown_transitions, 1)
        self.assertEqual(comparison.stayed_with_tasks, 0)
        self.assertIsNone(compare_service_task_snapshots(
            first, ServiceTaskSnapshot((("one.service", "/same", 3),)),
            require_invocation=True,
        ))

    def test_native_capture_is_read_only_and_scope_specific(self):
        with patch("isc.timer_service_lifecycle._capture", side_effect=[
            "a.timer loaded active waiting Test\n",
            "Id=a.timer\nUnit=linked.service\n",
            "Id=linked.service\nControlGroup=/system.slice/linked.service\nTasksCurrent=2\nInvocationID=" + "a" * 32 + "\n",
            "b.timer loaded active waiting Test\n",
            "Id=b.timer\nUnit=other.service\n",
            "Id=other.service\nControlGroup=\nTasksCurrent=[not set]\nInvocationID=\n",
        ]) as capture:
            system, user = read_service_task_snapshots()
        self.assertEqual(system.observed[0][2], 2)
        self.assertEqual(system.invocations[0][1], "a" * 32)
        self.assertIsNone(user.invocations[0][1])
        self.assertIsNone(user.observed[0][2])
        self.assertEqual(capture.call_count, 6)
        self.assertTrue(all(
            cmd.args[0][0] == "systemctl"
            and ("list-units" in cmd.args[0] or "show" in cmd.args[0])
            for cmd in capture.call_args_list
        ))

    @patch("isc.cli.time.sleep")
    @patch("isc.cli.read_service_task_snapshots")
    def test_cli_only_reports_counts_and_rejects_bad_seconds(self, observer, sleeper):
        observer.side_effect = [
            (snap(("private.service", "/private", 3)), snap()),
            (snap(("private.service", "/private", 0)), snap()),
        ]
        self.assertEqual(timer_service_watch_cli(seconds=1), 0)
        self.assertEqual(observer.call_count, 2)
        sleeper.assert_called_once_with(1)
        self.assertEqual(timer_service_watch_cli(seconds=61), 2)
        self.assertEqual(observer.call_count, 2)

    @patch("isc.cli.time.sleep")
    @patch("isc.cli.read_service_task_snapshots")
    def test_failed_scopes_return_unknown(self, observer, sleeper):
        observer.side_effect = [
            (snap(), snap()),
            (None, snap()),
        ]
        self.assertEqual(timer_service_watch_cli(seconds=0), 2)
        sleeper.assert_not_called()


if __name__ == "__main__":
    unittest.main()

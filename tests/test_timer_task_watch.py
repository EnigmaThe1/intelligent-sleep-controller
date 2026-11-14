"""Two-sample aggregate timer-task observation regression tests."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.timer_service_tasks import TimerServiceTaskCounts
from isc.timer_task_watch import compare_task_counts
from isc.cli import timer_task_watch_cli


def sample(*, services=2, tasks=1, zero=0, unknown=1, groups=1, timers=2):
    return TimerServiceTaskCounts(
        timers, services, groups, tasks, zero, unknown,
    )


class TimerTaskWatchTests(unittest.TestCase):
    def test_same_aggregates_are_not_authoritative(self):
        result = compare_task_counts(sample(), sample())
        self.assertFalse(result.aggregates_changed)
        self.assertFalse(result.coverage_verified)

    def test_changed_aggregate_detected_but_not_completion(self):
        change = compare_task_counts(
            sample(), sample(tasks=0, zero=1, unknown=1),
        )
        self.assertTrue(change.aggregates_changed)
        self.assertFalse(change.coverage_verified)
        self.assertEqual((change.first_with_tasks, change.second_with_tasks),
                         (1, 0))

    def test_missing_or_inconsistent_snapshot_fails_closed(self):
        self.assertIsNone(compare_task_counts(None, sample()))
        self.assertIsNone(compare_task_counts(sample(), None))
        self.assertIsNone(compare_task_counts(
            sample(), sample(services=1, tasks=1, zero=1, unknown=0),
        ))
        self.assertIsNone(compare_task_counts(
            sample(), sample(services=-1, tasks=1),
        ))

    @patch("isc.cli.time.sleep")
    @patch("isc.cli.read_timer_service_tasks")
    def test_cli_uses_two_samples_and_no_power_operation(self, observe, sleep):
        observe.side_effect = [
            (sample(), sample(tasks=0, zero=2, unknown=0)),
            (sample(tasks=0, zero=1, unknown=1), sample()),
        ]
        self.assertEqual(timer_task_watch_cli(seconds=0.1), 0)
        self.assertEqual(observe.call_count, 2)
        sleep.assert_called_once_with(0.1)

    @patch("isc.cli.time.sleep")
    @patch("isc.cli.read_timer_service_tasks")
    def test_unknown_does_not_get_mislabeled_as_zero(self, observe, sleep):
        observe.side_effect = [
            (sample(), sample()),
            (None, sample()),
        ]
        self.assertEqual(timer_task_watch_cli(seconds=0), 2)
        sleep.assert_not_called()

    @patch("isc.cli.read_timer_service_tasks")
    def test_invalid_interval_rejected_before_query(self, observer):
        for seconds in (-1, 61, float("nan"), float("inf")):
            self.assertEqual(timer_task_watch_cli(seconds=seconds), 2)
        observer.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Continuous scheduler observer: aggregate-only, unknown on failure."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.background_scheduler import BackgroundSchedulerObserver, SchedulerPulse
from isc.timer_service_lifecycle import ServiceTaskSnapshot


def snap(*rows, invocation="a" * 32):
    return ServiceTaskSnapshot(
        rows, invocations=tuple((name, invocation) for name, _, _ in rows),
    )


class BackgroundSchedulerTests(unittest.TestCase):
    def test_identity_aware_changes_have_only_aggregate_pulses(self):
        samples = iter((
            (snap(("private.service", "/slice/private", 2)), snap()),
            (snap(("private.service", "/slice/private", 0)), snap()),
        ))
        pulses = []
        observer = None

        def publish(pulse):
            pulses.append(pulse)
            if len(pulses) == 2:
                observer._stop.set()

        observer = BackgroundSchedulerObserver(
            publish, scanner=lambda: next(samples), interval_seconds=5,
        )
        observer._run()
        self.assertEqual((pulses[0].services, pulses[0].with_tasks), (1, 1))
        self.assertIsNone(pulses[0].transitions)
        self.assertEqual((pulses[1].with_tasks, pulses[1].transitions), (0, 1))
        self.assertEqual(pulses[1].uncertain_transitions, 0)
        self.assertFalse(any(p.coverage_verified for p in pulses))
        self.assertNotIn("private.service", repr(pulses))
        self.assertNotIn("/slice/private", repr(pulses))

    def test_unknown_transitions_are_not_reported_as_observed_changes(self):
        samples = iter((
            (snap(("private.service", "", None)), snap()),
            (snap(("private.service", "", None)), snap()),
        ))
        pulses = []
        observer = None

        def publish(pulse):
            pulses.append(pulse)
            if len(pulses) == 2:
                observer._stop.set()

        observer = BackgroundSchedulerObserver(
            publish, scanner=lambda: next(samples), interval_seconds=5,
        )
        observer._run()
        self.assertEqual(pulses[1].transitions, 0)
        self.assertEqual(pulses[1].uncertain_transitions, 1)
        self.assertFalse(pulses[1].coverage_verified)

    def test_service_restart_same_cgroup_is_a_confirmed_change(self):
        samples = iter((
            (snap(("private.service", "/slice/private", 2), invocation="a" * 32), snap()),
            (snap(("private.service", "/slice/private", 2), invocation="b" * 32), snap()),
        ))
        pulses = []
        observer = None

        def publish(pulse):
            pulses.append(pulse)
            if len(pulses) == 2:
                observer._stop.set()

        observer = BackgroundSchedulerObserver(
            publish, scanner=lambda: next(samples), interval_seconds=5,
        )
        observer._run()
        self.assertEqual(pulses[1].transitions, 1)
        self.assertEqual(pulses[1].invocation_changes, 1)
        self.assertEqual(pulses[1].uncertain_transitions, 0)
        self.assertEqual(pulses[1].invocation_changes, 0)
        self.assertFalse(pulses[1].coverage_verified)

    def test_missing_activation_identity_is_uncertain_not_safe(self):
        samples = iter((
            (snap(("private.service", "/slice/private", 2)), snap()),
            (snap(("private.service", "/slice/private", 2), invocation=None), snap()),
        ))
        pulses = []
        observer = None

        def publish(pulse):
            pulses.append(pulse)
            if len(pulses) == 2:
                observer._stop.set()

        observer = BackgroundSchedulerObserver(
            publish, scanner=lambda: next(samples), interval_seconds=5,
        )
        observer._run()
        self.assertEqual(pulses[1].transitions, 0)
        self.assertEqual(pulses[1].uncertain_transitions, 1)
        self.assertEqual(pulses[1].uncertain, 1)

    def test_scanner_failure_publishes_unknown_and_resets_history(self):
        values = iter((
            (snap(("private.service", "/c", 1)), snap()),
            (None, None),
            (snap(("private.service", "/c", 1)), snap()),
        ))
        pulses = []
        observer = None

        def publish(pulse):
            pulses.append(pulse)
            if len(pulses) == 3:
                observer._stop.set()

        observer = BackgroundSchedulerObserver(
            publish, scanner=lambda: next(values), interval_seconds=5,
        )
        observer._run()
        self.assertIsNone(pulses[1].services)
        self.assertIsNone(pulses[1].invocation_changes)
        self.assertIsNone(pulses[2].transitions)
        self.assertIsNone(pulses[2].invocation_changes)

    def test_unknown_task_count_is_never_zero(self):
        pulses = []
        observer = None

        def publish(pulse):
            pulses.append(pulse)
            observer._stop.set()

        observer = BackgroundSchedulerObserver(
            publish,
            scanner=lambda: (snap(("a.service", "", None)), snap()),
            interval_seconds=5,
        )
        observer._run()
        self.assertEqual((pulses[0].uncertain, pulses[0].with_tasks), (1, 0))
        self.assertFalse(pulses[0].coverage_verified)

    def test_invalid_parameters_and_duplicate_start(self):
        for interval in (-1, 4, 301, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                BackgroundSchedulerObserver(
                    lambda pulse: None, interval_seconds=interval,
                )
        with patch("isc.background_scheduler.Thread") as thread:
            observer = BackgroundSchedulerObserver(
                lambda pulse: None, interval_seconds=5,
            )
            observer.start()
            with self.assertRaises(RuntimeError):
                observer.start()
            observer.close()
            thread.return_value.join.assert_called_once()


if __name__ == "__main__":
    unittest.main()

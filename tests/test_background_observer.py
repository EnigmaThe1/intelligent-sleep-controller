"""Continuous read-only process census tests (no power actions)."""
import math
from pathlib import Path
import sys
from threading import Event
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.background_observer import BackgroundProcessObserver, ProcessPulse
from isc.process_lifecycle import ProcessIdentity, ProcessLineage


def snapshot(*, child=False, race=0):
    root = ProcessIdentity(100, 777, 1, "claude")
    descendants = (
        (ProcessIdentity(101, 888, 100, "python3"),) if child else ()
    )
    return ProcessLineage((root,), descendants, race)


class BackgroundObserverTests(unittest.TestCase):
    def test_observer_collects_counts_and_changes_without_storing_identifiers(self):
        samples = iter((snapshot(), snapshot(child=True, race=1)))
        pulses = []
        observer = None

        def publish(pulse):
            pulses.append(pulse)
            if len(pulses) == 2:
                observer._stop.set()

        observer = BackgroundProcessObserver(
            publish, scanner=lambda: next(samples),
            interval_seconds=1,
        )
        observer._run()
        self.assertEqual(len(pulses), 2)
        self.assertEqual((pulses[0].roots, pulses[0].descendants), (1, 0))
        self.assertEqual((pulses[1].roots, pulses[1].descendants), (1, 1))
        self.assertEqual(pulses[1].identity_changes, 1)
        self.assertEqual(pulses[1].visibility_races, 1)
        self.assertFalse(any(p.coverage_verified for p in pulses))
        self.assertNotIn("claude", str(pulses))
        self.assertNotIn("python3", str(pulses))
        self.assertNotIn("777", str(pulses))

    def test_failed_census_is_unknown_and_breaks_identity_continuity(self):
        samples = iter((snapshot(), None, snapshot()))
        pulses = []
        observer = None

        def publish(pulse):
            pulses.append(pulse)
            if len(pulses) == 3:
                observer._stop.set()

        observer = BackgroundProcessObserver(
            publish, scanner=lambda: next(samples), interval_seconds=1,
        )
        observer._run()
        self.assertIsNone(pulses[1].roots)
        self.assertIsNone(pulses[1].identity_changes)
        self.assertIsNone(pulses[1].visibility_races)
        self.assertEqual(pulses[2].roots, 1)
        self.assertEqual(pulses[2].identity_changes, 0)
        self.assertFalse(pulses[2].coverage_verified)

    def test_scanner_exception_does_not_claim_empty_census(self):
        pulses = []
        observer = None

        def crash():
            raise PermissionError("private path should never leak")

        def publish(pulse):
            pulses.append(pulse)
            observer._stop.set()

        observer = BackgroundProcessObserver(
            publish, scanner=crash, interval_seconds=1,
        )
        observer._run()
        self.assertEqual(len(pulses), 1)
        self.assertIsNone(pulses[0].roots)
        self.assertNotIn("private path", repr(pulses))

    def test_background_worker_stops_on_close(self):
        arrived = Event()
        pulses = []

        def record(pulse):
            pulses.append(pulse)
            arrived.set()

        with BackgroundProcessObserver(
            record, scanner=snapshot, interval_seconds=1,
        ) as observer:
            self.assertTrue(arrived.wait(timeout=1))
        self.assertTrue(observer._stop.is_set())
        self.assertIsNotNone(observer._thread)
        self.assertFalse(observer._thread.is_alive())
        self.assertEqual(len(pulses), 1)

    def test_invalid_parameters_never_start_a_worker(self):
        for interval in (0, -1, 301, float("inf"), float("nan")):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    BackgroundProcessObserver(
                        lambda pulse: None, interval_seconds=interval,
                    )
        with self.assertRaises(ValueError):
            BackgroundProcessObserver(None)

    def test_duplicate_start_refused(self):
        with BackgroundProcessObserver(
            lambda pulse: None, scanner=snapshot, interval_seconds=1,
        ) as observer:
            with self.assertRaisesRegex(RuntimeError, "already_started"):
                observer.start()


if __name__ == "__main__":
    unittest.main()

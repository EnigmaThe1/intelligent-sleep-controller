"""Step 99i: bounded live-window composition without false coverage claims."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.native_systemd_bracketed_probe import BracketedSignalSummary
from isc.native_systemd_window_runner import inspect_native_windows


def quiet(**_):
    return BracketedSignalSummary("QUIET_UNVERIFIED")


def signal(**_):
    return BracketedSignalSummary("SENDER_MATCH_UNVERIFIED", 1, True)


def ticks(*values):
    it = iter(values)
    return lambda: next(it)


class NativeWindowRunnerTests(unittest.TestCase):
    def test_two_realistic_quiet_windows_remain_unverified(self):
        row = inspect_native_windows(observer=quiet, clock=ticks(1, 4, 4.5, 7))
        self.assertEqual(row.state, "WINDOWS_UNVERIFIED")
        self.assertEqual(row.windows, 2)
        self.assertEqual(row.candidate_windows, 0)
        self.assertEqual(row.blind_gap_seconds, 0.5)
        self.assertFalse(row.coverage_verified)
        self.assertFalse(row.uninterrupted_events_verified)

    def test_matching_candidates_remain_unverified(self):
        row = inspect_native_windows(observer=signal, clock=ticks(0, 2, 3, 5))
        self.assertEqual(row.candidate_windows, 2)
        self.assertEqual(row.windows, 2)
        self.assertFalse(row.task_owner_authenticated)

    def test_changed_owner_stops_without_retry(self):
        calls = []
        def observe(**_):
            calls.append(1)
            return BracketedSignalSummary("OWNER_CHANGED_UNVERIFIED")
        row = inspect_native_windows(count=4, observer=observe, clock=ticks(0, 1))
        self.assertEqual(row.state, "UNKNOWN")
        self.assertEqual(len(calls), 1)
        self.assertEqual(row.windows, 0)

    def test_source_exception_stops(self):
        def fail(**_):
            raise OSError("private path")
        row = inspect_native_windows(observer=fail, clock=ticks(0))
        self.assertEqual(row.state, "UNKNOWN")
        self.assertNotIn("private", repr(row))

    def test_invalid_count_never_invokes_observer(self):
        for n in (0, 5, None, True, 1.5, "2"):
            with self.subTest(count=n):
                def fail(**_):
                    raise AssertionError("called external observer")
                row = inspect_native_windows(count=n, observer=fail)
                self.assertEqual(row.state, "UNKNOWN")

    def test_invalid_seconds_never_invokes_observer(self):
        for n in (0, 11, None, True, "3"):
            row = inspect_native_windows(seconds=n, observer=lambda **_: 1)
            self.assertEqual(row.state, "UNKNOWN")

    def test_invalid_clock_result_stops(self):
        for value in (-1, float("nan"), float("inf"), "clock", None, True):
            with self.subTest(value=value):
                row = inspect_native_windows(count=1, observer=quiet,
                                             clock=lambda: value)
                self.assertEqual(row.state, "UNKNOWN")

    def test_long_unsampled_gap_is_unknown(self):
        row = inspect_native_windows(observer=quiet, clock=ticks(0, 1, 63, 65))
        self.assertEqual(row.state, "UNKNOWN")
        self.assertEqual(row.windows, 1)

    def test_reversed_clock_is_unknown(self):
        row = inspect_native_windows(observer=quiet, clock=ticks(10, 11, 10, 12))
        self.assertEqual(row.state, "UNKNOWN")

    def test_non_authoritative_observation_cannot_be_upgraded(self):
        def forged(**_):
            return BracketedSignalSummary("QUIET_UNVERIFIED", coverage_verified=True)
        row = inspect_native_windows(count=1, observer=forged, clock=ticks(1, 2))
        self.assertEqual(row.state, "UNKNOWN")

    def test_empty_or_invalid_observation_is_unknown(self):
        for value in (None, {}, "", "QUIET_UNVERIFIED"):
            row = inspect_native_windows(count=1, observer=lambda **_: value,
                                         clock=ticks(1, 2))
            self.assertEqual(row.state, "UNKNOWN")

    def test_four_windows_max(self):
        row = inspect_native_windows(count=4, observer=quiet,
                                     clock=ticks(0, 1, 1, 2, 2, 3, 3, 4))
        self.assertEqual(row.windows, 4)
        self.assertEqual(row.state, "WINDOWS_UNVERIFIED")

    def test_does_not_expose_native_identifiers(self):
        row = inspect_native_windows(count=1, observer=signal, clock=ticks(0, 1))
        self.assertNotIn("org.freedesktop", repr(row))
        self.assertNotIn("Sender", repr(row))


if __name__ == "__main__":
    unittest.main()

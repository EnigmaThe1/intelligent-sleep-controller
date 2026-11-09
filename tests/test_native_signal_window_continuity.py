"""Step 99h: continuity gaps and loss never become suspend eligibility."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.native_signal_window_continuity import NativeWindowContinuity
from isc.native_systemd_bracketed_probe import BracketedSignalSummary


def quiet():
    return BracketedSignalSummary("QUIET_UNVERIFIED")


def matched():
    return BracketedSignalSummary(
        "SENDER_MATCH_UNVERIFIED", observed_candidates=1,
        manager_sender_correlated=True,
    )


class NativeWindowContinuityTests(unittest.TestCase):
    def test_unobserved_is_not_idle(self):
        pulse = NativeWindowContinuity().summary()
        self.assertEqual(pulse.state, "NOT_OBSERVED")
        self.assertEqual(pulse.windows, 0)
        self.assertFalse(pulse.coverage_verified)

    def test_quiet_window_does_not_verify_events(self):
        pulse = NativeWindowContinuity().observe(
            quiet(), started_at=2, ended_at=5)
        self.assertEqual(pulse.state, "WINDOWS_UNVERIFIED")
        self.assertEqual(pulse.windows, 1)
        self.assertFalse(pulse.uninterrupted_events_verified)

    def test_sender_match_remains_unverified(self):
        pulse = NativeWindowContinuity().observe(
            matched(), started_at=1, ended_at=4)
        self.assertFalse(pulse.coverage_verified)
        self.assertFalse(pulse.task_owner_authenticated)

    def test_short_gap_is_reported_as_blind(self):
        state = NativeWindowContinuity()
        state.observe(quiet(), started_at=0, ended_at=3)
        pulse = state.observe(matched(), started_at=4.25, ended_at=6)
        self.assertEqual(pulse.blind_gap_seconds, 1.25)
        self.assertEqual(pulse.windows, 2)
        self.assertFalse(pulse.uninterrupted_events_verified)

    def test_zero_gap_still_does_not_verify_coverage(self):
        state = NativeWindowContinuity()
        state.observe(quiet(), started_at=1, ended_at=2)
        pulse = state.observe(quiet(), started_at=2, ended_at=3)
        self.assertEqual(pulse.blind_gap_seconds, 0)
        self.assertFalse(pulse.coverage_verified)

    def test_long_gap_sticky_unknown(self):
        state = NativeWindowContinuity()
        state.observe(quiet(), started_at=0, ended_at=1)
        self.assertEqual(state.observe(
            quiet(), started_at=62, ended_at=63).state, "UNKNOWN")
        self.assertEqual(state.observe(
            quiet(), started_at=64, ended_at=65).state, "UNKNOWN")
        self.assertEqual(state.summary().windows, 1)

    def test_clock_reversal_is_unknown(self):
        state = NativeWindowContinuity()
        state.observe(quiet(), started_at=10, ended_at=11)
        self.assertEqual(state.observe(
            quiet(), started_at=9, ended_at=10).state, "UNKNOWN")

    def test_invalid_timestamps_fail_closed(self):
        for start, end in ((-1, 0), (2, 1), (0, 31), (None, 1),
                           (0, float("nan")), (0, float("inf")),
                           (True, 2), ("0", 1)):
            with self.subTest(start=start, end=end):
                state = NativeWindowContinuity()
                self.assertEqual(state.observe(
                    quiet(), started_at=start, ended_at=end).state, "UNKNOWN")

    def test_owner_change_and_mismatch_are_sticky_unknown(self):
        for status in ("OWNER_CHANGED_UNVERIFIED", "SENDER_MISMATCH",
                       "UNKNOWN"):
            state = NativeWindowContinuity()
            self.assertEqual(state.observe(
                BracketedSignalSummary(status), started_at=0, ended_at=2
            ).state, "UNKNOWN")
            self.assertEqual(state.observe(
                quiet(), started_at=3, ended_at=4).state, "UNKNOWN")

    def test_forged_authority_is_rejected(self):
        row = BracketedSignalSummary(
            "QUIET_UNVERIFIED", coverage_verified=True)
        self.assertEqual(NativeWindowContinuity().observe(
            row, started_at=0, ended_at=1).state, "UNKNOWN")

    def test_malformed_candidate_count_is_rejected(self):
        state = NativeWindowContinuity()
        row = BracketedSignalSummary(
            "SENDER_MATCH_UNVERIFIED", observed_candidates=2,
            manager_sender_correlated=True)
        self.assertEqual(state.observe(
            row, started_at=0, ended_at=1).state, "UNKNOWN")

    def test_missing_result_and_wrong_type_are_unknown(self):
        for row in (None, "QUIET_UNVERIFIED", {}, False):
            self.assertEqual(NativeWindowContinuity().observe(
                row, started_at=0, ended_at=1).state, "UNKNOWN")

    def test_capacity_is_bounded(self):
        state = NativeWindowContinuity()
        for i in range(256):
            pulse = state.observe(
                quiet(), started_at=i, ended_at=i + 1)
            self.assertEqual(pulse.state, "WINDOWS_UNVERIFIED")
        self.assertEqual(state.observe(
            quiet(), started_at=256, ended_at=257).state, "UNKNOWN")
        self.assertEqual(state.summary().windows, 256)

    def test_external_loss_is_sticky(self):
        state = NativeWindowContinuity()
        state.observe(quiet(), started_at=0, ended_at=1)
        state.invalidate()
        self.assertEqual(state.observe(
            matched(), started_at=2, ended_at=3).state, "UNKNOWN")

    def test_no_private_identifiers_in_summary(self):
        pulse = NativeWindowContinuity().observe(
            matched(), started_at=0, ended_at=1)
        self.assertNotIn("org.freedesktop", repr(pulse))
        self.assertNotIn("JobNew", repr(pulse))


if __name__ == "__main__":
    unittest.main()

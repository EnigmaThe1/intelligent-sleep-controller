"""Step 97: journal event envelopes are never workload coverage evidence."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.native_journal_sequence import assess_journal_event_batch


def row(cursor="s=1", tick="100", boot="a" * 32, **extra):
    return {
        "__CURSOR": cursor, "_BOOT_ID": boot,
        "__MONOTONIC_TIMESTAMP": tick, **extra,
    }


class NativeJournalSequenceTests(unittest.TestCase):
    def test_valid_ordered_records_still_do_not_prove_coverage(self):
        result = assess_journal_event_batch([
            row("s=1", "100"), row("s=2", "101"),
        ])
        self.assertEqual((result.observed_events, result.state),
                         (2, "ORDERED_UNVERIFIED"))
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.uninterrupted_events_verified)
        self.assertFalse(result.task_owner_authenticated)

    def test_empty_is_not_proof_of_inactivity(self):
        result = assess_journal_event_batch(())
        self.assertEqual((result.observed_events, result.state),
                         (0, "EMPTY_UNVERIFIED"))
        self.assertFalse(result.coverage_verified)

    def test_duplicate_cursor_is_unknown(self):
        self.assertEqual(assess_journal_event_batch(
            [row("a", "100"), row("a", "101")]).state, "UNKNOWN")

    def test_reversed_clock_is_unknown(self):
        self.assertEqual(assess_journal_event_batch(
            [row("a", "101"), row("b", "100")]).state, "UNKNOWN")

    def test_boot_transition_is_unknown(self):
        self.assertEqual(assess_journal_event_batch(
            [row("a", "100"), row("b", "101", "b" * 32)]).state, "UNKNOWN")

    def test_bad_time_is_unknown(self):
        for tick in ("", "-1", "1.5", "nan", "９", str(2**63), "x"):
            with self.subTest(tick=tick):
                self.assertIsNone(assess_journal_event_batch(
                    [row(tick=tick)]).observed_events)

    def test_bad_identifiers_are_unknown(self):
        for bad in (
            row(cursor=""), row(cursor="x" * 1025),
            row(cursor="bad\x00cursor"), row(boot="not-a-boot"),
            {"__CURSOR": "one", "_BOOT_ID": "a" * 32},
            "wrong", None,
        ):
            with self.subTest(bad=type(bad).__name__):
                self.assertEqual(
                    assess_journal_event_batch([bad]).state, "UNKNOWN")

    def test_invalid_batches_fail_closed(self):
        for rows in (None, {}, "text", [row()] * 257):
            self.assertEqual(assess_journal_event_batch(rows).state, "UNKNOWN")

    def test_ignored_private_fields_not_echoed(self):
        result = assess_journal_event_batch([row(
            cursor="private-cursor", MESSAGE="private message",
            _SYSTEMD_UNIT="private.service", _CMDLINE="secret",
        )])
        self.assertEqual(result.observed_events, 1)
        for private in ("private-cursor", "private message",
                        "private.service", "secret"):
            self.assertNotIn(private, repr(result))

    def test_same_timestamp_is_not_assumed_lossless(self):
        result = assess_journal_event_batch([
            row("one", "1"), row("two", "1"),
        ])
        self.assertEqual(result.state, "ORDERED_UNVERIFIED")
        self.assertFalse(result.uninterrupted_events_verified)


if __name__ == "__main__":
    unittest.main()

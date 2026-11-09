"""Synthetic tests for bounded Step 99d candidate metadata classification."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.native_signal_headers import assess_manager_signal_headers


def header(member="JobNew"):
    return {"Type": "signal", "Interface": "org.freedesktop.systemd1.Manager",
            "Member": member}


class SignalHeaderAssessmentTests(unittest.TestCase):
    def test_valid_candidate_never_claims_delivery_or_coverage(self):
        row = assess_manager_signal_headers([header(), header("JobRemoved")])
        self.assertEqual((row.state, row.candidate_signals),
                         ("CANDIDATE_UNVERIFIED", 2))
        self.assertFalse(row.coverage_verified)
        self.assertFalse(row.event_delivery_verified)
        self.assertFalse(row.task_owner_authenticated)

    def test_all_allowed_members(self):
        for member in ("JobNew", "JobRemoved", "UnitNew", "UnitRemoved"):
            self.assertEqual(assess_manager_signal_headers(
                [header(member)]).state, "CANDIDATE_UNVERIFIED")

    def test_empty_not_idle(self):
        row = assess_manager_signal_headers([])
        self.assertEqual((row.state, row.candidate_signals),
                         ("EMPTY_UNVERIFIED", 0))
        self.assertFalse(row.coverage_verified)

    def test_method_call_rejected(self):
        row = header()
        row["Type"] = "method_call"
        self.assertEqual(assess_manager_signal_headers([row]).state, "UNKNOWN")

    def test_other_interfaces_rejected(self):
        row = header()
        row["Interface"] = "org.freedesktop.DBus"
        self.assertEqual(assess_manager_signal_headers([row]).state, "UNKNOWN")

    def test_unexpected_member_rejected(self):
        self.assertEqual(assess_manager_signal_headers([
            header("Reloading")]).state, "UNKNOWN")

    def test_extra_identity_or_payload_fields_rejected(self):
        row = header()
        row["Path"] = "/private"
        self.assertEqual(assess_manager_signal_headers([row]).state, "UNKNOWN")

    def test_missing_fields_rejected(self):
        row = header()
        del row["Member"]
        self.assertEqual(assess_manager_signal_headers([row]).state, "UNKNOWN")

    def test_bad_types_rejected(self):
        for value in (None, {}, "private", 3, [None], [123]):
            self.assertEqual(assess_manager_signal_headers(value).state, "UNKNOWN")

    def test_oversize_batch_rejected(self):
        self.assertEqual(assess_manager_signal_headers(
            [header()] * 33).state, "UNKNOWN")

    def test_control_chars_and_oversize_rejected(self):
        for member in ("JobNew\nsecret", "a" * 81, "JobNew\x00"):
            self.assertEqual(assess_manager_signal_headers([
                header(member)]).state, "UNKNOWN")

    def test_no_private_identifiers_in_output(self):
        row = assess_manager_signal_headers([header("JobNew")])
        self.assertNotIn("org.freedesktop", repr(row))
        self.assertNotIn("JobNew", repr(row))


if __name__ == "__main__":
    unittest.main()

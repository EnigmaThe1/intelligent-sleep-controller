import contextlib
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.cli import main


class CLITests(unittest.TestCase):
    @patch("isc.cli.read_gnome_idle_ms", return_value=13)
    def test_status_is_read_only_and_explicit(self, idle_probe):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["status"]), 0)
        text = output.getvalue()
        self.assertIn("automatic suspend: NOT IMPLEMENTED", text)
        self.assertIn("GNOME user idle: 0.013 seconds (13 ms)", text)
        self.assertIn("No system configuration has been changed.", text)

    @patch("isc.cli.read_gnome_idle_ms", return_value=None)
    def test_idle_unknown_does_not_claim_safety(self, idle_probe):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["idle"]), 0)
        text = output.getvalue()
        self.assertIn("unknown", text)
        self.assertIn("NOT proof that background work has stopped", text)
        self.assertIn("NOT IMPLEMENTED", text)

    @patch("isc.cli.read_inhibitors", return_value=None)
    def test_inhibitor_unavailable_is_not_assumed_empty(self, probe):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["inhibitors"]), 0)
        self.assertIn("UNKNOWN", output.getvalue())
        self.assertIn("cannot assume no sleep blockers", output.getvalue())

    @patch("isc.cli.read_inhibitors")
    def test_inhibitors_summary_discloses_no_private_metadata(self, probe):
        from isc.inhibitors import Inhibitor
        probe.return_value = (
            Inhibitor(frozenset(("sleep",)), "delay"),
            Inhibitor(frozenset(("idle",)), "block"),
            Inhibitor(frozenset(("shutdown",)), "block"),
        )
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["inhibitors"]), 0)
        text = output.getvalue()
        self.assertIn("Blocking automatic sleep/idle: 1", text)
        self.assertIn("Sleep delays (not permanent blockers): 1", text)
        self.assertIn("Other inhibitor locks: 1", text)

    @patch("isc.cli.read_inhibitors", return_value=())
    @patch("isc.cli.read_gnome_idle_ms", return_value=2200000)
    def test_check_never_claims_host_safe_on_idle_alone(self, idle_probe, inhibitor_probe):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["check"]), 0)
        text = output.getvalue()
        self.assertIn("Verdict: DO NOT AUTO-SUSPEND", text)
        self.assertIn("workload_coverage_unverified", text)
        self.assertIn("scheduled_work_safety_unverified", text)
        self.assertIn("NOT IMPLEMENTED", text)

    @patch("isc.cli.read_inhibitors", return_value=None)
    @patch("isc.cli.read_gnome_idle_ms", return_value=None)
    def test_check_missing_probes_fail_closed(self, idle_probe, inhibitor_probe):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["check"]), 0)
        text = output.getvalue()
        self.assertIn("user_idle_unverified", text)
        self.assertIn("sleep_inhibitor_present", text)
        self.assertIn("Verdict: DO NOT AUTO-SUSPEND", text)


if __name__ == "__main__":
    unittest.main()

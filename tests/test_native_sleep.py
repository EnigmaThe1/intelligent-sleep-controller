from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.native_sleep import (
    NativeSleepAssessment, _can_mode, assess_native_sleep,
    request_native_sleep, request_terminal_sleep,
)
from isc.inhibitors import Inhibitor
from isc.processes import ProcessScan


class NativeSleepTests(unittest.TestCase):
    @patch("isc.native_sleep.subprocess.run")
    def test_logind_capability_is_queried_directly(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout='s "yes"')
        self.assertEqual(_can_mode("hibernate"), "yes")
        self.assertEqual(run.call_args.args[0][-1], "CanHibernate")
        self.assertEqual(_can_mode("hybrid-sleep"), "yes")
        self.assertEqual(run.call_args.args[0][-1], "CanHybridSleep")

    @patch("isc.native_sleep.subprocess.run")
    def test_unavailable_capability_is_conservative(self, run):
        run.return_value = SimpleNamespace(returncode=1, stdout="")
        self.assertEqual(_can_mode("hibernate"), "unknown")

    @patch("isc.native_sleep._can_mode", return_value="no")
    @patch("isc.native_sleep.read_inhibitors", return_value=())
    @patch("isc.native_sleep.read_process_candidates", return_value=ProcessScan(()))
    def test_unsupported_hibernate_is_not_exposed_as_available(self, scan, locks, cap):
        result = assess_native_sleep("hibernate")
        self.assertFalse(result.request_possible)
        self.assertIn("native_sleep_unavailable", result.blockers)

    @patch("isc.native_sleep._can_mode", return_value="yes")
    @patch("isc.native_sleep.read_inhibitors", return_value=(
        Inhibitor(frozenset(("sleep",)), "block"),
    ))
    @patch("isc.native_sleep.read_process_candidates", return_value=ProcessScan(()))
    def test_active_inhibitor_prevents_hibernation(self, scan, locks, cap):
        result = assess_native_sleep("hybrid-sleep")
        self.assertIn("active_sleep_inhibitor", result.blockers)

    @patch("isc.native_sleep.subprocess.run", side_effect=AssertionError("power action"))
    def test_no_confirmation_means_no_power_action(self, run):
        self.assertFalse(request_native_sleep("hibernate").submitted)
        self.assertFalse(
            request_native_sleep("hibernate", confirmed=True, typed="WRONG").submitted
        )
        self.assertFalse(
            request_native_sleep("hybrid-sleep", confirmed=True, typed="HIBERNATE").submitted
        )
        run.assert_not_called()

    @patch("isc.native_sleep.assess_native_sleep")
    @patch("isc.native_sleep.subprocess.run")
    def test_real_action_requires_two_preflights(self, run, assess):
        assess.return_value = SimpleNamespace(request_possible=True)
        run.return_value = SimpleNamespace(returncode=0)
        result = request_native_sleep(
            "hibernate", confirmed=True, typed="HIBERNATE",
        )
        self.assertTrue(result.submitted)
        self.assertEqual(assess.call_count, 2)
        self.assertEqual(
            run.call_args.args[0],
            ["systemctl", "--check-inhibitors=yes", "hibernate"],
        )
        self.assertNotIn("shell", run.call_args.kwargs)

    @patch("isc.native_sleep.assess_native_sleep")
    @patch("isc.native_sleep.subprocess.run", side_effect=AssertionError("power action"))
    def test_changed_preflight_cancels_hybrid_sleep(self, run, assess):
        assess.side_effect = [
            SimpleNamespace(request_possible=True),
            SimpleNamespace(request_possible=False),
        ]
        result = request_native_sleep("hybrid-sleep", confirmed=True, typed="HYBRID")
        self.assertFalse(result.submitted)
        self.assertEqual(result.reason, "preflight_changed")
        run.assert_not_called()

    @patch("isc.native_sleep.sys.stdin")
    @patch("isc.native_sleep.sys.stdout")
    @patch("isc.native_sleep.subprocess.run", side_effect=AssertionError("power action"))
    def test_no_noninteractive_terminal_power_request(self, run, stdout, stdin):
        stdin.isatty.return_value = False
        stdout.isatty.return_value = False
        self.assertEqual(
            request_terminal_sleep("hibernate", confirmed=True).reason,
            "interactive_terminal_required",
        )
        run.assert_not_called()

    def test_unknown_operation_rejected(self):
        with self.assertRaises(ValueError):
            assess_native_sleep("shutdown")


if __name__ == "__main__":
    unittest.main()

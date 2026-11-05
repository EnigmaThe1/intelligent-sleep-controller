from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.inhibitors import Inhibitor
from isc.manual_power import (
    assess_manual_suspend, can_suspend, request_manual_suspend,
)
from isc.processes import ProcessCandidate, ProcessScan


class ManualPowerTests(unittest.TestCase):
    @patch("isc.manual_power.subprocess.run")
    def test_read_only_can_suspend_yes_and_challenge(self, run):
        for expected in ("yes", "challenge"):
            run.return_value = SimpleNamespace(
                returncode=0, stdout=f's "{expected}"',
            )
            self.assertEqual(can_suspend(), expected)
            args, kwargs = run.call_args
            self.assertEqual(args[0][-1], "CanSuspend")
            self.assertEqual(kwargs["timeout"], 3.0)
            self.assertNotIn("shell", kwargs)

    @patch("isc.manual_power.subprocess.run")
    def test_malformed_capability_fails_closed(self, run):
        for response in ("", "s yes trailing", "s maybe"):
            run.return_value = SimpleNamespace(returncode=0, stdout=response)
            self.assertEqual(can_suspend(), "unknown")

    @patch("isc.manual_power.subprocess.run", side_effect=OSError)
    def test_capability_probe_failure_is_unknown(self, run):
        self.assertEqual(can_suspend(), "unknown")

    @patch("isc.manual_power.read_process_candidates", return_value=ProcessScan(()))
    @patch("isc.manual_power.read_inhibitors", return_value=())
    @patch("isc.manual_power.can_suspend", return_value="yes")
    def test_clean_preflight_still_disclaims_coverage(self, cap, locks, scan):
        result = assess_manual_suspend()
        self.assertTrue(result.request_possible)
        self.assertFalse(result.workload_coverage_verified)
        self.assertEqual(result.candidate_processes, 0)

    @patch("isc.manual_power.read_process_candidates", return_value=ProcessScan(()))
    @patch("isc.manual_power.read_inhibitors", return_value=None)
    @patch("isc.manual_power.can_suspend", return_value="yes")
    def test_unknown_inhibitors_block(self, cap, locks, scan):
        result = assess_manual_suspend()
        self.assertFalse(result.request_possible)
        self.assertIn("sleep_inhibitors_unverified", result.blockers)

    @patch("isc.manual_power.read_process_candidates", return_value=ProcessScan(()))
    @patch("isc.manual_power.read_inhibitors", return_value=(
        Inhibitor(frozenset(("sleep",)), "block"),
    ))
    @patch("isc.manual_power.can_suspend", return_value="yes")
    def test_sleep_inhibitor_blocks(self, cap, locks, scan):
        result = assess_manual_suspend()
        self.assertFalse(result.request_possible)
        self.assertIn("blocking_sleep_inhibitor_present", result.blockers)

    @patch("isc.manual_power.read_process_candidates", return_value=None)
    @patch("isc.manual_power.read_inhibitors", return_value=())
    @patch("isc.manual_power.can_suspend", return_value="yes")
    def test_unknown_process_inventory_blocks(self, cap, locks, scan):
        self.assertFalse(assess_manual_suspend().request_possible)

    @patch("isc.manual_power.read_process_candidates", return_value=ProcessScan((
        ProcessCandidate(42, "claude"),
    )))
    @patch("isc.manual_power.read_inhibitors", return_value=())
    @patch("isc.manual_power.can_suspend", return_value="challenge")
    def test_candidate_is_not_proof_of_work_activity(self, cap, locks, scan):
        result = assess_manual_suspend()
        self.assertTrue(result.request_possible)
        self.assertEqual(result.candidate_processes, 1)

    @patch("isc.manual_power.subprocess.run", side_effect=AssertionError("power called"))
    def test_no_confirm_cannot_suspend(self, run):
        result = request_manual_suspend()
        self.assertFalse(result.submitted)
        self.assertEqual(result.reason, "confirmation_flag_required")
        run.assert_not_called()

    @patch("isc.manual_power.sys.stdin")
    @patch("isc.manual_power.sys.stdout")
    @patch("isc.manual_power.subprocess.run", side_effect=AssertionError("power called"))
    def test_no_interactive_terminal_cannot_suspend(self, run, stdout, stdin):
        stdin.isatty.return_value = False
        stdout.isatty.return_value = False
        result = request_manual_suspend(confirmed=True)
        self.assertEqual(result.reason, "interactive_terminal_required")
        run.assert_not_called()

    @patch("isc.manual_power.assess_manual_suspend")
    @patch("isc.manual_power.sys.stdin")
    @patch("isc.manual_power.sys.stdout")
    @patch("builtins.input", return_value="NO")
    @patch("isc.manual_power.subprocess.run", side_effect=AssertionError("power called"))
    def test_wrong_typed_confirmation_cancels(self, run, typed, stdout, stdin, preflight):
        stdin.isatty.return_value = True
        stdout.isatty.return_value = True
        preflight.return_value = SimpleNamespace(
            request_possible=True, candidate_processes=0,
        )
        result = request_manual_suspend(confirmed=True)
        self.assertEqual(result.reason, "cancelled")
        self.assertEqual(preflight.call_count, 1)
        run.assert_not_called()

    @patch("isc.manual_power.assess_manual_suspend")
    @patch("isc.manual_power.sys.stdin")
    @patch("isc.manual_power.sys.stdout")
    @patch("builtins.input", return_value="SUSPEND")
    @patch("isc.manual_power.subprocess.run")
    def test_confirmed_interactive_request_respects_inhibitors(
        self, run, typed, stdout, stdin, preflight,
    ):
        stdin.isatty.return_value = True
        stdout.isatty.return_value = True
        preflight.return_value = SimpleNamespace(
            request_possible=True, candidate_processes=2,
        )
        run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        result = request_manual_suspend(confirmed=True)
        self.assertTrue(result.submitted)
        self.assertEqual(preflight.call_count, 2)
        args, kwargs = run.call_args
        self.assertEqual(args[0], [
            "systemctl", "--check-inhibitors=yes", "suspend",
        ])
        self.assertFalse(kwargs["check"])
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["timeout"], 20)

    @patch("isc.manual_power.assess_manual_suspend")
    @patch("isc.manual_power.sys.stdin")
    @patch("isc.manual_power.sys.stdout")
    @patch("builtins.input", return_value="SUSPEND")
    @patch("isc.manual_power.subprocess.run", side_effect=AssertionError("power called"))
    def test_preflight_change_after_confirmation_aborts(
        self, run, typed, stdout, stdin, preflight,
    ):
        stdin.isatty.return_value = True
        stdout.isatty.return_value = True
        preflight.side_effect = [
            SimpleNamespace(request_possible=True, candidate_processes=0),
            SimpleNamespace(request_possible=False),
        ]
        result = request_manual_suspend(confirmed=True)
        self.assertEqual(result.reason, "preflight_changed_or_blocked")
        run.assert_not_called()

    @patch("isc.manual_power.assess_manual_suspend")
    @patch("isc.manual_power.sys.stdin")
    @patch("isc.manual_power.sys.stdout")
    @patch("builtins.input", return_value="SUSPEND")
    @patch("isc.manual_power.subprocess.run")
    def test_failed_systemd_request_is_not_success(
        self, run, typed, stdout, stdin, preflight,
    ):
        stdin.isatty.return_value = True
        stdout.isatty.return_value = True
        preflight.return_value = SimpleNamespace(
            request_possible=True, candidate_processes=0,
        )
        run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="private")
        result = request_manual_suspend(confirmed=True)
        self.assertFalse(result.submitted)
        self.assertEqual(result.reason, "system_suspend_request_rejected")
        self.assertNotIn("private", str(result))


if __name__ == "__main__":
    unittest.main()

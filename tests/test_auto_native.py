"""ISC 1.0 automatic native preflight: read only, fail closed."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.auto_native import (AutoNativeAssessment, AutoNativeSubmitResult,
                             assess_auto_native_suspend, submit_automatic_native)
from isc.mode_gate import EffectiveMode
from isc.policy import Snapshot, WorkItem, WorkState
from isc.inhibitors import Inhibitor


class AutoNativeTests(unittest.TestCase):
    def test_valid_logind_yes_and_no_inhibitors_is_native_ready(self):
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors", return_value=()):
            result = assess_auto_native_suspend()
        self.assertEqual(result, AutoNativeAssessment(True, "yes", ()))
        # This is native readiness, NEVER application or scheduler coverage.
        self.assertFalse(hasattr(result, "workload_coverage_verified"))

    def test_challenge_is_not_acceptable_unattended(self):
        for capability in ("challenge", "no", "na", "unknown", None):
            with self.subTest(capability=capability):
                with patch("isc.auto_native.can_suspend", return_value=capability), \
                     patch("isc.auto_native.read_inhibitors", return_value=()):
                    result = assess_auto_native_suspend()
                self.assertFalse(result.ready)
                self.assertIn("native_suspend_capability_unverified_or_unavailable",
                              result.blockers)

    def test_block_inhibitor_prevents_native_ready(self):
        for mode in ("block", "block-weak"):
            lock = Inhibitor(frozenset(("sleep",)), mode)
            with patch("isc.auto_native.can_suspend", return_value="yes"), \
                 patch("isc.auto_native.read_inhibitors", return_value=(lock,)):
                result = assess_auto_native_suspend()
            self.assertFalse(result.ready)
            self.assertEqual(result.blockers,
                             ("logind_sleep_or_idle_inhibitor_present",))

    def test_delay_sleep_is_conservative_blocker(self):
        lock = Inhibitor(frozenset(("sleep",)), "delay")
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors", return_value=(lock,)):
            self.assertFalse(assess_auto_native_suspend().ready)

    def test_delay_idle_is_conservative_blocker(self):
        lock = Inhibitor(frozenset(("idle",)), "delay")
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors", return_value=(lock,)):
            self.assertFalse(assess_auto_native_suspend().ready)

    def test_unrelated_shutdown_inhibitor_does_not_block_suspend(self):
        lock = Inhibitor(frozenset(("shutdown",)), "block")
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors", return_value=(lock,)):
            self.assertTrue(assess_auto_native_suspend().ready)

    def test_unknown_inhibitor_type_is_blocked(self):
        lock = Inhibitor(frozenset(("future-power-policy",)), "block")
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors", return_value=(lock,)):
            self.assertFalse(assess_auto_native_suspend().ready)

    def test_missing_inhibitor_inventory_is_not_empty(self):
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors", return_value=None):
            result = assess_auto_native_suspend()
        self.assertEqual(result.blockers, ("logind_inhibitors_unverified",))

    def test_malformed_inhibitor_inventory_fails_closed(self):
        for locks in ([], (None,), ("invalid",),
                      (Inhibitor(frozenset(("sleep",)), "invalid"),)):
            with self.subTest(value=locks):
                with patch("isc.auto_native.can_suspend", return_value="yes"), \
                     patch("isc.auto_native.read_inhibitors", return_value=locks):
                    self.assertFalse(assess_auto_native_suspend().ready)

    def test_capability_failure_returns_unknown_without_private_details(self):
        with patch("isc.auto_native.can_suspend",
                   side_effect=OSError("/private/test-session")), \
             patch("isc.auto_native.read_inhibitors", return_value=()):
            result = assess_auto_native_suspend()
        self.assertFalse(result.ready)
        self.assertNotIn("/private", repr(result))

    def test_inhibitor_read_failure_returns_unknown(self):
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors",
                   side_effect=RuntimeError("private")):
            result = assess_auto_native_suspend()
        self.assertFalse(result.ready)
        self.assertNotIn("private", repr(result))

    def test_native_assessment_never_requests_suspend(self):
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors", return_value=()), \
             patch("subprocess.run",
                   side_effect=AssertionError("never issue power command")):
            self.assertTrue(assess_auto_native_suspend().ready)


def _armed_mode():
    # A synthetic mode used ONLY with mocked system power commands.
    return EffectiveMode("on", False, True, True, "synthetic")


def _safe_policy():
    return Snapshot(user_idle_seconds=1900, coverage_complete=True,
                    scheduled_work_safe=True)


class AutomaticNativeSubmissionTests(unittest.TestCase):
    @patch("isc.auto_native.subprocess.run")
    @patch("isc.auto_native.assess_auto_native_suspend")
    @patch("isc.auto_native.read_host_snapshot")
    def test_persisted_off_refuses_before_any_probe(self, host, native, run):
        row = submit_automatic_native()
        self.assertEqual(row, AutoNativeSubmitResult(False, "automatic_suspend_disarmed"))
        host.assert_not_called()
        native.assert_not_called()
        run.assert_not_called()

    @patch("isc.auto_native.current_mode", return_value=_armed_mode())
    @patch("isc.auto_native.subprocess.run")
    @patch("isc.auto_native.assess_auto_native_suspend")
    @patch("isc.auto_native.read_host_snapshot")
    def test_partial_workload_snapshot_is_rejected(self, host, native, run, mode):
        host.return_value = Snapshot(user_idle_seconds=1900)
        row = submit_automatic_native()
        self.assertEqual(row.reason, "final_workload_check_blocked")
        native.assert_not_called()
        run.assert_not_called()

    @patch("isc.auto_native.current_mode", return_value=_armed_mode())
    @patch("isc.auto_native.subprocess.run")
    @patch("isc.auto_native.assess_auto_native_suspend")
    @patch("isc.auto_native.read_host_snapshot", return_value=_safe_policy())
    def test_inhibitor_present_prevents_native_submit(self, host, native, run, mode):
        native.return_value = AutoNativeAssessment(
            False, "yes", ("logind_sleep_or_idle_inhibitor_present",))
        row = submit_automatic_native()
        self.assertEqual(row.reason, "final_native_check_blocked")
        run.assert_not_called()

    @patch("isc.auto_native.current_mode", return_value=_armed_mode())
    @patch("isc.auto_native.subprocess.run")
    @patch("isc.auto_native.assess_auto_native_suspend")
    @patch("isc.auto_native.read_host_snapshot", return_value=_safe_policy())
    def test_manual_or_malformed_native_result_is_rejected(self, host, native, run, mode):
        native.return_value = {"ready": True}
        row = submit_automatic_native()
        self.assertEqual(row.reason, "final_native_check_blocked")
        run.assert_not_called()

    @patch("isc.auto_native.current_mode", side_effect=[
        _armed_mode(), EffectiveMode("off", False, False, True)])
    @patch("isc.auto_native.subprocess.run")
    @patch("isc.auto_native.assess_auto_native_suspend",
           return_value=AutoNativeAssessment(True, "yes", ()))
    @patch("isc.auto_native.read_host_snapshot", return_value=_safe_policy())
    def test_mode_changed_to_off_prevents_submit(self, host, native, run, mode):
        row = submit_automatic_native()
        self.assertEqual(row.reason, "operator_mode_changed")
        run.assert_not_called()

    @patch("isc.auto_native.current_mode", return_value=_armed_mode())
    @patch("isc.auto_native.subprocess.run")
    @patch("isc.auto_native.assess_auto_native_suspend",
           return_value=AutoNativeAssessment(True, "yes", ()))
    @patch("isc.auto_native.read_host_snapshot", side_effect=[
        _safe_policy(), Snapshot(user_idle_seconds=2,
            coverage_complete=True, scheduled_work_safe=True)])
    def test_new_user_activity_in_final_snapshot_prevents_submit(
            self, host, native, run, mode):
        row = submit_automatic_native()
        self.assertEqual(row.reason, "last_workload_check_blocked")
        run.assert_not_called()

    @patch("isc.auto_native.current_mode", return_value=_armed_mode())
    @patch("isc.auto_native.subprocess.run")
    @patch("isc.auto_native.assess_auto_native_suspend",
           return_value=AutoNativeAssessment(True, "yes", ()))
    @patch("isc.auto_native.read_host_snapshot", side_effect=[
        _safe_policy(), Snapshot(user_idle_seconds=1900,
            coverage_complete=True, scheduled_work_safe=True,
            work_items=(WorkItem("work", WorkState.ACTIVE),))])
    def test_new_worker_in_final_snapshot_prevents_submit(
            self, host, native, run, mode):
        row = submit_automatic_native()
        self.assertEqual(row.reason, "last_workload_check_blocked")
        run.assert_not_called()

    @patch("isc.auto_native.current_mode", return_value=_armed_mode())
    @patch("isc.auto_native.subprocess.run", return_value=Mock(returncode=0))
    @patch("isc.auto_native.assess_auto_native_suspend",
           return_value=AutoNativeAssessment(True, "yes", ()))
    @patch("isc.auto_native.read_host_snapshot", return_value=_safe_policy())
    def test_synthetic_approved_path_never_uses_real_power(
            self, host, native, run, mode):
        row = submit_automatic_native()
        self.assertEqual(row.reason, "request_accepted_by_systemd")
        self.assertTrue(row.submitted)
        run.assert_called_once_with(
            ("systemctl", "--check-inhibitors=yes", "suspend"),
            capture_output=True, text=True, check=False, timeout=20)
        self.assertEqual(host.call_count, 2)
        self.assertEqual(mode.call_count, 2)

    @patch("isc.auto_native.current_mode", return_value=_armed_mode())
    @patch("isc.auto_native.subprocess.run", return_value=Mock(returncode=1))
    @patch("isc.auto_native.assess_auto_native_suspend",
           return_value=AutoNativeAssessment(True, "yes", ()))
    @patch("isc.auto_native.read_host_snapshot", return_value=_safe_policy())
    def test_native_rejection_is_not_success(self, host, native, run, mode):
        self.assertEqual(submit_automatic_native().reason, "native_suspend_rejected")

    @patch("isc.auto_native.current_mode", return_value=_armed_mode())
    @patch("isc.auto_native.subprocess.run", side_effect=OSError("private path"))
    @patch("isc.auto_native.assess_auto_native_suspend",
           return_value=AutoNativeAssessment(True, "yes", ()))
    @patch("isc.auto_native.read_host_snapshot", return_value=_safe_policy())
    def test_power_error_fails_closed_without_private_info(self, host, native, run, mode):
        result = submit_automatic_native()
        self.assertEqual(result.reason, "native_suspend_unavailable")
        self.assertNotIn("private", repr(result))

    @patch("isc.auto_native.current_mode", return_value=_armed_mode())
    @patch("isc.auto_native.subprocess.run")
    @patch("isc.auto_native.read_host_snapshot", side_effect=OSError("private"))
    def test_snapshot_failure_fails_closed(self, host, run, mode):
        self.assertEqual(submit_automatic_native().reason,
                         "native_suspend_unavailable")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

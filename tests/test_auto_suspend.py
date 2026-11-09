"""Milestone 2: automatic native sleep is disarmed until release gates pass."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.auto_suspend import attempt_automatic_suspend
from isc.manual_power import ManualSuspendResult, SuspendAssessment
from isc.mode_gate import EffectiveMode
from isc.policy import Snapshot, WorkItem, WorkState


def mode_on():
    # This synthetic state cannot be produced by the shipped mode gate.
    return EffectiveMode("on", False, True, True, "synthetic_armed")


def mode_off():
    return EffectiveMode("off", False, False, True, "off_selected")


def eligible():
    return Snapshot(
        user_idle_seconds=1900,
        coverage_complete=True,
        scheduled_work_safe=True,
    )


def native_ok():
    return SuspendAssessment(True, "yes", (), 0, True)


class AutomaticSuspendTests(unittest.TestCase):
    def test_real_shipped_mode_gate_always_disarms(self):
        watcher, preflight, submit = Mock(), Mock(), Mock()
        with patch("subprocess.run", side_effect=AssertionError("power action")):
            row = attempt_automatic_suspend(
                snapshot_reader=watcher,
                native_preflight=preflight, submit=submit,
            )
        self.assertFalse(row.submitted)
        self.assertEqual(row.reason, "automatic_suspend_disarmed")
        watcher.assert_not_called()
        preflight.assert_not_called()
        submit.assert_not_called()

    def test_off_never_calls_inputs(self):
        watcher, preflight, submit = Mock(), Mock(), Mock()
        row = attempt_automatic_suspend(
            mode_reader=mode_off, snapshot_reader=watcher,
            native_preflight=preflight, submit=submit,
        )
        self.assertFalse(row.submitted)
        watcher.assert_not_called()
        preflight.assert_not_called()
        submit.assert_not_called()

    def test_monitor_mode_does_not_suspend(self):
        row = attempt_automatic_suspend(
            mode_reader=lambda: EffectiveMode("monitor", True, False, True),
            snapshot_reader=lambda: eligible(),
            native_preflight=native_ok,
            submit=lambda: ManualSuspendResult(True, "submitted"),
        )
        self.assertEqual(row.reason, "automatic_suspend_disarmed")

    def test_mode_must_be_genuinely_armed(self):
        row = attempt_automatic_suspend(
            mode_reader=lambda: EffectiveMode("on", False, False, True),
            snapshot_reader=lambda: eligible(),
            native_preflight=native_ok,
            submit=lambda: ManualSuspendResult(True, "submitted"),
        )
        self.assertFalse(row.submitted)

    def test_missing_trusted_snapshot_is_blocked(self):
        submit = Mock()
        row = attempt_automatic_suspend(
            mode_reader=mode_on, snapshot_reader=None,
            native_preflight=native_ok, submit=submit,
        )
        self.assertEqual(row.reason, "trusted_inputs_unavailable")
        submit.assert_not_called()

    def test_unknown_work_blocks_early(self):
        submit = Mock()
        def unknown():
            return Snapshot(
                user_idle_seconds=1900, coverage_complete=True,
                scheduled_work_safe=True,
                work_items=(WorkItem("job", WorkState.UNKNOWN),),
            )
        row = attempt_automatic_suspend(
            mode_reader=mode_on, snapshot_reader=unknown,
            native_preflight=native_ok, submit=submit,
        )
        self.assertEqual(row.reason, "initial_safety_check_blocked")
        submit.assert_not_called()

    def test_scheduled_work_unverified_blocks(self):
        row = attempt_automatic_suspend(
            mode_reader=mode_on,
            snapshot_reader=lambda: Snapshot(
                user_idle_seconds=1900, coverage_complete=True,
            ),
            native_preflight=native_ok,
            submit=lambda: ManualSuspendResult(True, "submitted"),
        )
        self.assertFalse(row.submitted)

    def test_active_work_blocks(self):
        row = attempt_automatic_suspend(
            mode_reader=mode_on,
            snapshot_reader=lambda: Snapshot(
                user_idle_seconds=1900,
                coverage_complete=True,
                scheduled_work_safe=True,
                work_items=(WorkItem("job", WorkState.ACTIVE),),
            ),
            native_preflight=native_ok,
            submit=lambda: ManualSuspendResult(True, "submitted"),
        )
        self.assertFalse(row.submitted)

    def test_manual_preflight_never_grants_auto_coverage(self):
        submit = Mock()
        row = attempt_automatic_suspend(
            mode_reader=mode_on, snapshot_reader=eligible,
            native_preflight=lambda: SuspendAssessment(
                True, "yes", (), 0, False,
            ), submit=submit,
        )
        self.assertEqual(row.reason, "native_safety_check_blocked")
        submit.assert_not_called()

    def test_native_capability_challenge_is_not_auto_safe(self):
        submit = Mock()
        row = attempt_automatic_suspend(
            mode_reader=mode_on, snapshot_reader=eligible,
            native_preflight=lambda: SuspendAssessment(
                True, "challenge", (), 0, True,
            ), submit=submit,
        )
        self.assertEqual(row.reason, "native_safety_check_blocked")
        submit.assert_not_called()

    def test_native_blocker_prevents_submitting(self):
        submit = Mock()
        row = attempt_automatic_suspend(
            mode_reader=mode_on, snapshot_reader=eligible,
            native_preflight=lambda: SuspendAssessment(
                False, "yes", ("sleep_inhibitor",), 0, True,
            ), submit=submit,
        )
        self.assertEqual(row.reason, "native_safety_check_blocked")
        submit.assert_not_called()

    def test_second_snapshot_changed_blocks(self):
        calls = iter((eligible(), Snapshot(
            user_idle_seconds=0, coverage_complete=True,
            scheduled_work_safe=True,
        )))
        submit = Mock()
        row = attempt_automatic_suspend(
            mode_reader=mode_on, snapshot_reader=lambda: next(calls),
            native_preflight=native_ok, submit=submit,
        )
        self.assertEqual(row.reason, "final_safety_check_blocked")
        submit.assert_not_called()

    def test_mode_change_before_submit_blocks(self):
        values = iter((mode_on(), mode_off()))
        submit = Mock()
        row = attempt_automatic_suspend(
            mode_reader=lambda: next(values),
            snapshot_reader=eligible,
            native_preflight=native_ok, submit=submit,
        )
        self.assertEqual(row.reason, "operator_mode_changed")
        submit.assert_not_called()

    def test_valid_synthetic_conditions_call_injected_submit_once(self):
        submit = Mock(return_value=ManualSuspendResult(True, "submitted"))
        with patch("subprocess.run", side_effect=AssertionError("real sleep")):
            row = attempt_automatic_suspend(
                mode_reader=mode_on, snapshot_reader=eligible,
                native_preflight=native_ok, submit=submit,
            )
        self.assertTrue(row.submitted)
        submit.assert_called_once_with()

    def test_subprocess_failure_does_not_expose_private_paths(self):
        def fails():
            raise OSError("private")
        row = attempt_automatic_suspend(
            mode_reader=mode_on, snapshot_reader=eligible,
            native_preflight=fails,
            submit=lambda: ManualSuspendResult(True, "submitted"),
        )
        self.assertEqual(row.reason, "safety_source_unavailable")
        self.assertNotIn("private", repr(row))


if __name__ == "__main__":
    unittest.main()

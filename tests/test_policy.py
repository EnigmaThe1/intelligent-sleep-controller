import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.policy import Snapshot, WorkItem, WorkState, evaluate


def safe_snapshot(**overrides):
    values = dict(
        user_idle_seconds=1900,
        threshold_seconds=1800,
        coverage_complete=True,
        scheduled_work_safe=True,
        work_items=(),
        active_sleep_inhibitors=(),
    )
    values.update(overrides)
    return Snapshot(**values)


class PolicyTests(unittest.TestCase):
    def test_defaults_fail_closed(self):
        result = evaluate(Snapshot())
        self.assertFalse(result.eligible)
        self.assertIn("workload_coverage_unverified", result.reasons)
        self.assertIn("scheduled_work_safety_unverified", result.reasons)

    def test_safe_verified_snapshot_is_eligible(self):
        self.assertTrue(evaluate(safe_snapshot()).eligible)

    def test_active_task_blocks(self):
        result = evaluate(safe_snapshot(
            work_items=(WorkItem("build-1", WorkState.ACTIVE),)
        ))
        self.assertFalse(result.eligible)
        self.assertIn("work_busy:build-1", result.reasons)

    def test_waiting_api_response_blocks_even_with_idle_cpu(self):
        self.assertFalse(evaluate(safe_snapshot(work_items=(
            WorkItem("agent-1", WorkState.WAITING_EXTERNAL),
        ))).eligible)

    def test_human_blocker_can_be_safe(self):
        self.assertTrue(evaluate(safe_snapshot(work_items=(
            WorkItem("agent-1", WorkState.WAITING_HUMAN),
        ))).eligible)

    def test_human_blocker_with_active_child_blocks(self):
        result = evaluate(safe_snapshot(work_items=(
            WorkItem("agent-1", WorkState.WAITING_HUMAN, active_children=True),
        )))
        self.assertFalse(result.eligible)

    def test_other_running_job_still_blocks(self):
        result = evaluate(safe_snapshot(work_items=(
            WorkItem("agent-1", WorkState.WAITING_HUMAN),
            WorkItem("agent-2", WorkState.ACTIVE),
        )))
        self.assertFalse(result.eligible)

    def test_unknown_blocks(self):
        self.assertFalse(evaluate(safe_snapshot(work_items=(
            WorkItem("agent-1", WorkState.UNKNOWN),
        ))).eligible)

    def test_completed_job_does_not_block(self):
        self.assertTrue(evaluate(safe_snapshot(work_items=(
            WorkItem("agent-1", WorkState.COMPLETED),
        ))).eligible)

    def test_idle_under_threshold_blocks(self):
        self.assertFalse(evaluate(safe_snapshot(user_idle_seconds=1799)).eligible)

    def test_missing_idle_blocks(self):
        self.assertFalse(evaluate(safe_snapshot(user_idle_seconds=None)).eligible)

    def test_bad_idle_value_blocks(self):
        for value in (-1, float("nan"), float("inf")):
            with self.subTest(value=value):
                self.assertFalse(evaluate(safe_snapshot(user_idle_seconds=value)).eligible)

    def test_zero_threshold_blocks(self):
        self.assertFalse(evaluate(safe_snapshot(threshold_seconds=0)).eligible)

    def test_existing_inhibitor_blocks(self):
        self.assertFalse(evaluate(safe_snapshot(
            active_sleep_inhibitors=("backup",),
        )).eligible)

    def test_unverified_scheduled_work_blocks(self):
        self.assertFalse(evaluate(safe_snapshot(
            scheduled_work_safe=False,
        )).eligible)

    def test_partial_workload_coverage_blocks(self):
        self.assertFalse(evaluate(safe_snapshot(
            coverage_complete=False,
        )).eligible)

    def test_duplicate_work_id_blocks(self):
        self.assertFalse(evaluate(safe_snapshot(work_items=(
            WorkItem("job", WorkState.COMPLETED),
            WorkItem("job", WorkState.COMPLETED),
        ))).eligible)

    def test_invalid_work_state_blocks(self):
        self.assertFalse(evaluate(safe_snapshot(work_items=(
            WorkItem("job", "completed"),
        ))).eligible)


if __name__ == "__main__":
    unittest.main()

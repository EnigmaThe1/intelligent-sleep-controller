"""Synthetic tests for optional task/process bindings, never actual jobs."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.policy import Snapshot, WorkState, evaluate
from isc.process_lifecycle import ProcessIdentity, ProcessLineage
from isc.task_process_bindings import TaskProcessBindings


OWNER = "a" * 32
TASK = "b" * 32


def proc(pid=100, start=12, parent=1, name="codex"):
    return ProcessIdentity(pid, start, parent, name)


def lineage(root=None, descendants=(), races=0):
    return ProcessLineage((root,) if root is not None else (), descendants, races)


class TaskProcessBindingsTests(unittest.TestCase):
    def test_registration_is_not_authentication_or_coverage(self):
        reg = TaskProcessBindings()
        reg.register(OWNER, TASK, proc())
        before = reg.summary()
        self.assertEqual(before.registered, 1)
        self.assertEqual(before.unknown, 1)
        self.assertFalse(before.coverage_verified)
        self.assertEqual(reg.states(), (WorkState.UNKNOWN,))
        now = reg.observe(lineage(proc()), now=1)
        self.assertEqual(now.root_visible, 1)
        self.assertEqual(now.unknown, 0)
        self.assertEqual(reg.states(), (WorkState.ACTIVE,))
        self.assertFalse(now.coverage_verified)
        verdict = evaluate(Snapshot(
            user_idle_seconds=3000, work_items=(
                __import__("isc.policy", fromlist=["WorkItem"]).WorkItem(
                    "synthetic", reg.states()[0],
                ),
            ),
        ))
        self.assertFalse(verdict.eligible)
        self.assertNotIn("codex", str(now))
        self.assertNotIn(OWNER, str(now))
        self.assertNotIn(TASK, str(now))
        self.assertNotIn("100", repr(now))

    def test_pid_reuse_must_not_be_treated_as_continuation(self):
        reg = TaskProcessBindings()
        reg.register(OWNER, TASK, proc(start=20))
        reg.observe(lineage(proc(start=20)), now=1)
        result = reg.observe(lineage(proc(start=21)), now=2)
        self.assertEqual(result.root_missing_or_replaced, 1)
        self.assertEqual(reg.states(), (WorkState.UNKNOWN,))
        self.assertFalse(result.coverage_verified)

    def test_disappearance_cannot_mean_completion_even_if_root_returns(self):
        reg = TaskProcessBindings()
        reg.register(OWNER, TASK, proc())
        reg.observe(lineage(proc()), now=1)
        reg.observe(lineage(), now=2)
        self.assertEqual(reg.states(), (WorkState.UNKNOWN,))
        after = reg.observe(lineage(proc()), now=3)
        self.assertEqual(after.root_visible, 1)
        self.assertEqual(reg.states(), (WorkState.UNKNOWN,))
        self.assertGreaterEqual(after.unknown, 1)

    def test_scanner_failure_is_sticky_unknown(self):
        reg = TaskProcessBindings()
        reg.register(OWNER, TASK, proc())
        reg.observe(lineage(proc()), now=1)
        lost = reg.observe(None, now=2)
        self.assertTrue(reg.history_lost)
        self.assertGreaterEqual(lost.unknown, 1)
        reg.observe(lineage(proc()), now=3)
        self.assertEqual(reg.states(), (WorkState.UNKNOWN,))

    def test_clock_reversal_and_visibility_race_are_unknown(self):
        reg = TaskProcessBindings()
        reg.register(OWNER, TASK, proc())
        reg.observe(lineage(proc()), now=10)
        reg.observe(lineage(proc()), now=9)
        self.assertTrue(reg.history_lost)
        self.assertEqual(reg.states(), (WorkState.UNKNOWN,))
        other = TaskProcessBindings()
        other.register(OWNER, TASK, proc())
        other.observe(lineage(proc(), races=1), now=1)
        self.assertTrue(other.history_lost)
        self.assertEqual(other.states(), (WorkState.UNKNOWN,))

    def test_visible_child_and_disappearing_child_are_not_completion(self):
        reg = TaskProcessBindings()
        reg.register(OWNER, TASK, proc())
        child = proc(pid=101, start=13, parent=100, name="python3")
        with_child = reg.observe(lineage(proc(), (child,)), now=1)
        self.assertEqual(with_child.child_observed, 1)
        self.assertEqual(reg.states(), (WorkState.ACTIVE,))
        after = reg.observe(lineage(proc()), now=2)
        self.assertEqual(after.child_observed, 0)
        self.assertEqual(reg.states(), (WorkState.UNKNOWN,))
        # A formerly visible child disappearing is not a completion signal,
        # even while the root remains visible.
        self.assertFalse(after.coverage_verified)

    def test_invalid_registration_refused_no_overwriting(self):
        reg = TaskProcessBindings()
        for owner, task, root in (
            ("not-opaque", TASK, proc()),
            (OWNER, "../secret", proc()),
            (OWNER, TASK, proc(pid=0)),
            (OWNER, TASK, proc(start=0)),
        ):
            with self.assertRaises(ValueError):
                reg.register(owner, task, root)
        reg.register(OWNER, TASK, proc())
        with self.assertRaisesRegex(ValueError, "already_exists"):
            reg.register(OWNER, TASK, proc(start=99))
        self.assertEqual(reg.summary().registered, 1)

    def test_same_process_identity_cannot_be_registered_under_other_task(self):
        reg = TaskProcessBindings()
        reg.register(OWNER, TASK, proc())
        with self.assertRaisesRegex(ValueError, "process_identity_already_bound"):
            reg.register("c" * 32, "d" * 32, proc())
        self.assertEqual(reg.summary().registered, 1)
        self.assertTrue(reg.history_lost)
        reg.observe(lineage(proc()), now=1)
        self.assertEqual(reg.states(), (WorkState.UNKNOWN,))
        # An unrelated process identity remains independently registrable.
        reg.register("c" * 32, "d" * 32, proc(pid=102, start=14))
        self.assertEqual(reg.summary().registered, 2)

    def test_missed_observation_interval_cannot_reestablish_authority(self):
        reg = TaskProcessBindings()
        reg.register(OWNER, TASK, proc())
        reg.observe(lineage(proc()), now=1)
        self.assertEqual(reg.states(), (WorkState.ACTIVE,))
        census = reg.observe(lineage(proc()), now=92)
        self.assertTrue(reg.history_lost)
        self.assertFalse(census.coverage_verified)
        self.assertEqual(reg.states(), (WorkState.UNKNOWN,))
        reg.observe(lineage(proc()), now=93)
        self.assertEqual(reg.states(), (WorkState.UNKNOWN,))

    def test_acceptable_observation_gap_does_not_introduce_false_loss(self):
        reg = TaskProcessBindings()
        reg.register(OWNER, TASK, proc())
        reg.observe(lineage(proc()), now=1)
        reg.observe(lineage(proc()), now=91)
        self.assertFalse(reg.history_lost)
        self.assertEqual(reg.states(), (WorkState.ACTIVE,))

    def test_bounded_capacity_preserves_failure_signal(self):
        reg = TaskProcessBindings()
        for n in range(256):
            reg.register(OWNER, f"{n:032x}", proc(pid=n+1))
        with self.assertRaisesRegex(ValueError, "capacity"):
            reg.register(OWNER, "f"*32, proc(pid=999))
        self.assertTrue(reg.history_lost)
        self.assertEqual(reg.summary().registered, 256)
        self.assertTrue(all(x is WorkState.UNKNOWN for x in reg.states()))

    def test_missing_snapshot_does_not_claim_empty_safe(self):
        reg = TaskProcessBindings()
        reg.observe(None, now=1)
        self.assertTrue(reg.history_lost)
        self.assertEqual(reg.states(), (WorkState.UNKNOWN,))
        self.assertFalse(reg.summary().coverage_verified)


if __name__ == "__main__":
    unittest.main()

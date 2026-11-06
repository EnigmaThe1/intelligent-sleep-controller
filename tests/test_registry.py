import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.policy import Snapshot, WorkState, evaluate
from isc.registry import WorkRegistry


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = WorkRegistry()

    def report(self, state=WorkState.ACTIVE, **overrides):
        data = dict(
            source="generic", task_id="job-1", state=state,
            now=100.0, ttl_seconds=30.0, active_children=False,
        )
        data.update(overrides)
        self.registry.report(**data)

    def test_no_tasks_is_not_proof_of_complete_coverage(self):
        self.assertEqual(self.registry.observed_work(now=100), ())
        verdict = evaluate(Snapshot(
            user_idle_seconds=2000, scheduled_work_safe=True,
            work_items=self.registry.observed_work(now=100),
        ))
        self.assertFalse(verdict.eligible)
        self.assertIn("workload_coverage_unverified", verdict.reasons)

    def test_active_task_has_unique_identifier(self):
        self.report()
        item, = self.registry.observed_work(now=101)
        self.assertEqual(item.identifier, "generic:job-1")
        self.assertEqual(item.state, WorkState.ACTIVE)

    def test_external_wait_is_not_idle(self):
        self.report(WorkState.WAITING_EXTERNAL)
        self.assertEqual(
            self.registry.observed_work(now=101)[0].state,
            WorkState.WAITING_EXTERNAL,
        )

    def test_human_wait_can_be_observed(self):
        self.report(WorkState.WAITING_HUMAN)
        self.assertEqual(
            self.registry.observed_work(now=101)[0].state,
            WorkState.WAITING_HUMAN,
        )

    def test_completed_is_observed_until_expiry(self):
        self.report(WorkState.COMPLETED)
        self.assertEqual(
            self.registry.observed_work(now=110)[0].state,
            WorkState.COMPLETED,
        )

    def test_expired_lease_becomes_unknown_not_completed(self):
        self.report(WorkState.WAITING_HUMAN)
        self.assertEqual(
            self.registry.observed_work(now=130)[0].state,
            WorkState.UNKNOWN,
        )

    def test_completed_event_expiry_also_becomes_unknown(self):
        self.report(WorkState.COMPLETED)
        self.assertEqual(
            self.registry.observed_work(now=131)[0].state,
            WorkState.UNKNOWN,
        )

    def test_monotonic_clock_backwards_means_unknown(self):
        self.report()
        self.assertEqual(
            self.registry.observed_work(now=99)[0].state,
            WorkState.UNKNOWN,
        )

    def test_active_child_preserved_even_if_expired(self):
        self.report(WorkState.WAITING_HUMAN, active_children=True)
        item, = self.registry.observed_work(now=131)
        self.assertEqual(item.state, WorkState.UNKNOWN)
        self.assertTrue(item.active_children)

    def test_refresh_extends_lease(self):
        self.report()
        self.report(WorkState.WAITING_HUMAN, now=120, ttl_seconds=30)
        item, = self.registry.observed_work(now=135)
        self.assertEqual(item.state, WorkState.WAITING_HUMAN)
        self.assertEqual(self.registry.count(), 1)

    def test_stale_event_rejected_without_overwrite(self):
        self.report()
        with self.assertRaisesRegex(ValueError, "out_of_order_event"):
            self.report(WorkState.COMPLETED, now=99)
        self.assertEqual(
            self.registry.observed_work(now=105)[0].state, WorkState.ACTIVE,
        )

    def test_multiple_independent_jobs_remain_separate(self):
        self.report(WorkState.WAITING_HUMAN)
        self.report(WorkState.ACTIVE, source="other", task_id="job-1")
        items = self.registry.observed_work(now=101)
        self.assertEqual(len(items), 2)
        verdict = evaluate(Snapshot(
            user_idle_seconds=2000,
            scheduled_work_safe=True,
            coverage_complete=True,  # synthetic authoritative coverage only
            work_items=items,
        ))
        self.assertFalse(verdict.eligible)
        self.assertIn("work_busy:other:job-1", verdict.reasons)

    def test_bad_identifiers_are_rejected(self):
        for value in ("", "../secret", "this is a prompt", "ø", "x" * 129):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.report(source=value)
                with self.assertRaises(ValueError):
                    self.report(task_id=value)
        self.assertEqual(self.registry.count(), 0)

    def test_bad_clock_and_lease_values_rejected(self):
        for value in (-1, float("nan"), float("inf"), "100"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.report(now=value)
                with self.assertRaises(ValueError):
                    self.report(ttl_seconds=value)
        for value in (0, 3600.1):
            with self.assertRaises(ValueError):
                self.report(ttl_seconds=value)

    def test_invalid_states_and_child_flags_rejected(self):
        with self.assertRaises(ValueError):
            self.report(state="active")
        with self.assertRaises(ValueError):
            self.report(active_children="false")

    def test_invalid_snapshot_time_rejected(self):
        with self.assertRaises(ValueError):
            self.registry.observed_work(now=float("nan"))

    def test_new_registry_does_not_recover_previous_tasks(self):
        self.report()
        new_registry = WorkRegistry()
        self.assertEqual(new_registry.observed_work(now=101), ())
        # An empty new registry must not establish authoritative coverage.
        verdict = evaluate(Snapshot(
            user_idle_seconds=2000,
            scheduled_work_safe=True,
            work_items=new_registry.observed_work(now=101),
        ))
        self.assertFalse(verdict.eligible)

    def test_stale_records_eventually_pruned_but_never_report_completed(self):
        from isc.registry import STALE_RETENTION_SECONDS
        reg = WorkRegistry()
        reg.report("claude_code", "old", WorkState.ACTIVE, now=1, ttl_seconds=1)
        self.assertEqual(reg.count(), 1)
        observed = reg.observed_work(now=2)
        self.assertEqual(observed[0].state, WorkState.UNKNOWN)
        observed = reg.observed_work(now=2 + STALE_RETENTION_SECONDS)
        self.assertEqual(reg.count(), 0)
        self.assertTrue(reg.tracking_history_lost)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].state, WorkState.UNKNOWN)
        self.assertEqual(observed[0].identifier, "isc:tracking_history_lost")

    def test_registry_capacity_bound_never_discards_loss_signal(self):
        from isc.registry import MAX_RETAINED_LEASES
        reg = WorkRegistry()
        for n in range(MAX_RETAINED_LEASES + 50):
            reg.report(
                "claude_code", f"session-{n}", WorkState.ACTIVE,
                now=1, ttl_seconds=30,
            )
        self.assertEqual(reg.count(), MAX_RETAINED_LEASES)
        self.assertTrue(reg.tracking_history_lost)
        states = reg.observed_work(now=2)
        self.assertEqual(len(states), MAX_RETAINED_LEASES + 1)
        self.assertEqual(states[0].state, WorkState.UNKNOWN)
        self.assertEqual(reg.count(), MAX_RETAINED_LEASES)

    def test_registry_refresh_does_not_overwrite_or_evict_unrelated_work(self):
        from isc.registry import MAX_RETAINED_LEASES
        reg = WorkRegistry()
        reg.report("claude_code", "stable", WorkState.ACTIVE, now=1, ttl_seconds=500)
        reg.report("codex", "other", WorkState.ACTIVE, now=1, ttl_seconds=500)
        for i in range(10):
            reg.report("claude_code", "stable", WorkState.ACTIVE,
                       now=2+i, ttl_seconds=500)
        self.assertEqual(reg.count(), 2)
        self.assertFalse(reg.tracking_history_lost)



if __name__ == "__main__":
    unittest.main()

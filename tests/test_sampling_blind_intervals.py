"""Step 96: point-in-time process snapshots never prove between-sample coverage."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.native_work_supervisor import NativeWorkSupervisor
from isc.policy import WorkState
from isc.process_lifecycle import ProcessIdentity, ProcessLineage
from isc.service_process_correlation import ServiceProcessEvidence
from isc.task_process_bindings import TaskProcessBindings

OWNER = "a" * 32
TASK = "b" * 32
ROOT = ProcessIdentity(101, 14, 1, "worker")
CHILD = ProcessIdentity(102, 15, 101, "build")


def sample(child=False):
    return ProcessLineage((ROOT,), (CHILD,) if child else (), 0)


def bindings():
    reg = TaskProcessBindings()
    reg.register(OWNER, TASK, ROOT)
    return reg


class SamplingBlindWindowTests(unittest.TestCase):
    def test_first_sample_has_no_interval_evidence(self):
        reg = bindings()
        result = reg.observe(sample(), now=10)
        self.assertIsNone(result.sampling_gap_seconds)
        self.assertFalse(result.interval_coverage_verified)
        self.assertFalse(result.coverage_verified)

    def test_identical_snapshots_cannot_certify_intervening_work(self):
        reg = bindings()
        reg.observe(sample(), now=10)
        result = reg.observe(sample(), now=40)
        self.assertEqual(result.sampling_gap_seconds, 30.0)
        self.assertEqual(reg.states(), (WorkState.ACTIVE,))
        self.assertFalse(result.interval_coverage_verified)
        self.assertFalse(result.coverage_verified)

    def test_even_identical_child_snapshots_are_not_continuous(self):
        reg = bindings()
        reg.observe(sample(child=True), now=10)
        result = reg.observe(sample(child=True), now=10.25)
        self.assertEqual(result.sampling_gap_seconds, 0.25)
        self.assertEqual(reg.states(), (WorkState.ACTIVE,))
        self.assertFalse(result.interval_coverage_verified)

    def test_zero_elapsed_time_is_still_not_full_coverage(self):
        reg = bindings()
        reg.observe(sample(), now=10)
        result = reg.observe(sample(), now=10)
        self.assertEqual(result.sampling_gap_seconds, 0)
        self.assertFalse(result.interval_coverage_verified)

    def test_gap_exceeding_limit_remains_sticky_unknown(self):
        reg = bindings()
        reg.observe(sample(), now=1)
        result = reg.observe(sample(), now=92)
        self.assertEqual(result.sampling_gap_seconds, 91)
        self.assertTrue(reg.history_lost)
        self.assertFalse(result.interval_coverage_verified)
        self.assertEqual(reg.states(), (WorkState.UNKNOWN,))

    def test_reversed_clock_has_no_valid_interval(self):
        reg = bindings()
        reg.observe(sample(), now=10)
        result = reg.observe(sample(), now=9)
        self.assertIsNone(result.sampling_gap_seconds)
        self.assertFalse(result.interval_coverage_verified)
        self.assertEqual(reg.states(), (WorkState.UNKNOWN,))

    def test_missing_scan_does_not_erase_interval_uncertainty(self):
        reg = bindings()
        reg.observe(sample(), now=1)
        result = reg.observe(None, now=4)
        self.assertEqual(result.sampling_gap_seconds, 3)
        self.assertFalse(result.interval_coverage_verified)
        self.assertTrue(reg.history_lost)

    def test_native_summary_preserves_blind_window(self):
        sup = NativeWorkSupervisor(
            lineage_scanner=lambda: sample(),
            membership_checker=lambda *_: ServiceProcessEvidence.OBSERVED_MEMBER,
        )
        sup.register(OWNER, TASK, ROOT, service="agent.service", scope="--user")
        first = sup.observe(now=1)
        second = sup.observe(now=2.5)
        self.assertIsNone(first.sampling_gap_seconds)
        self.assertEqual(second.sampling_gap_seconds, 1.5)
        self.assertFalse(second.interval_coverage_verified)
        self.assertFalse(second.coverage_verified)
        self.assertFalse(second.task_owner_authenticated)
        fresh = sup.fresh_summary(now=2.5)
        self.assertEqual(fresh.sampling_gap_seconds, 1.5)
        self.assertFalse(fresh.interval_coverage_verified)

    def test_summary_never_discloses_identity(self):
        reg = bindings()
        reg.observe(sample(child=True), now=1)
        reg.observe(sample(child=True), now=2)
        summary = repr(reg.summary())
        self.assertNotIn(OWNER, summary)
        self.assertNotIn(TASK, summary)
        self.assertNotIn("worker", summary)
        self.assertNotIn("build", summary)

    def test_empty_registration_does_not_certify_idle(self):
        sup = NativeWorkSupervisor()
        summary = sup.fresh_summary(now=2)
        self.assertEqual(summary.registered, 0)
        self.assertFalse(summary.coverage_verified)
        self.assertFalse(summary.interval_coverage_verified)


if __name__ == "__main__":
    unittest.main()

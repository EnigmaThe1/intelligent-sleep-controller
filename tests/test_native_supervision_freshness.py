"""Step 94: freshness is required to use process evidence; no power actions."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.cgroup_subtree import CgroupSubtreeSummary
from isc.native_service_enrollment import NativeServiceProcess
from isc.native_work_supervisor import NativeWorkSupervisor
from isc.policy import WorkState
from isc.process_lifecycle import ProcessIdentity, ProcessLineage
from isc.service_process_correlation import ServiceProcessEvidence

OWNER = "a" * 32
TASK = "b" * 32
ROOT = ProcessIdentity(101, 14, 1, "python3")
EVIDENCE = NativeServiceProcess(
    ROOT, "agent.service", "--user", "c" * 32,
    "/user.slice/agent.service",
)


def supervisor(*, pinned=True):
    sup = NativeWorkSupervisor(
        lineage_scanner=lambda: ProcessLineage((ROOT,), (), 0),
        membership_checker=lambda *_: ServiceProcessEvidence.OBSERVED_MEMBER,
        subtree_checker=lambda _: CgroupSubtreeSummary(1, unknown=False),
    )
    if pinned:
        assert sup.register_native_service(
            OWNER, TASK, service="agent.service", scope="--user",
            discovery=lambda *_args, **_kwargs: EVIDENCE,
        )
    else:
        sup.register(
            OWNER, TASK, ROOT, service="agent.service", scope="--user",
        )
    return sup


class FreshNativeSupervisionTests(unittest.TestCase):
    def test_no_enrollment_is_unknown_not_zero_safe(self):
        sup = NativeWorkSupervisor()
        self.assertEqual(sup.fresh_states(now=10), (WorkState.UNKNOWN,))
        result = sup.fresh_summary(now=10)
        self.assertEqual((result.registered, result.unknown), (0, 0))
        self.assertFalse(result.coverage_verified)

    def test_pinned_fresh_native_process_is_only_busy_evidence(self):
        sup = supervisor()
        self.assertEqual(sup.fresh_states(now=1), (WorkState.UNKNOWN,))
        sup.observe(now=1)
        self.assertEqual(sup.fresh_states(now=60.99), (WorkState.ACTIVE,))
        result = sup.fresh_summary(now=61)
        self.assertEqual((result.registered, result.observed_members, result.unknown),
                         (1, 1, 0))
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.task_owner_authenticated)

    def test_old_observation_turns_unknown_without_new_scan(self):
        sup = supervisor()
        sup.observe(now=1)
        self.assertEqual(sup.states(), (WorkState.ACTIVE,))
        self.assertEqual(sup.fresh_states(now=61.001), (WorkState.UNKNOWN,))
        result = sup.fresh_summary(now=61.001)
        self.assertEqual((result.observed_members, result.unknown), (0, 1))

    def test_non_native_registration_cannot_count_as_verified_busy(self):
        sup = supervisor(pinned=False)
        sup.observe(now=1)
        self.assertEqual(sup.states(), (WorkState.ACTIVE,))
        self.assertEqual(sup.fresh_states(now=1), (WorkState.UNKNOWN,))

    def test_time_reversal_does_not_restore_old_state(self):
        sup = supervisor()
        sup.observe(now=20)
        self.assertEqual(sup.fresh_states(now=19), (WorkState.UNKNOWN,))

    def test_missing_scan_is_sticky_unknown(self):
        sup = supervisor()
        sup.observe(now=1)
        sup.observe_snapshot(None, now=2)
        self.assertEqual(sup.fresh_states(now=3), (WorkState.UNKNOWN,))

    def test_cgroup_process_count_mismatch_is_unknown(self):
        sup = NativeWorkSupervisor(
            lineage_scanner=lambda: ProcessLineage((ROOT,), (), 0),
            membership_checker=lambda *_: ServiceProcessEvidence.OBSERVED_MEMBER,
            subtree_checker=lambda _: CgroupSubtreeSummary(2, unknown=False),
        )
        self.assertTrue(sup.register_native_service(
            OWNER, TASK, service="agent.service", scope="--user",
            discovery=lambda *_args, **_kwargs: EVIDENCE,
        ))
        sup.observe(now=1)
        self.assertEqual(sup.fresh_states(now=2), (WorkState.UNKNOWN,))

    def test_invalid_time_refused(self):
        sup = supervisor()
        for now in (-1, float("nan"), float("inf"), True, "3"):
            with self.subTest(now=now):
                with self.assertRaises(ValueError):
                    sup.fresh_states(now=now)

    def test_public_summary_omits_private_identity(self):
        sup = supervisor()
        sup.observe(now=1)
        text = repr(sup.fresh_summary(now=2))
        self.assertNotIn(OWNER, text)
        self.assertNotIn(TASK, text)
        self.assertNotIn("agent.service", text)
        self.assertNotIn("101", text)


if __name__ == "__main__":
    unittest.main()

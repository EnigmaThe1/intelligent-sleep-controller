"""Opt-in in-process lifecycle correlation; synthetic only, no native changes."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.events import EventKind, LifecycleEvent
from isc.native_task_lifecycle import NativeLifecycleCorrelator
from isc.native_work_supervisor import NativeWorkSupervisor
from isc.policy import WorkState
from isc.process_lifecycle import ProcessIdentity, ProcessLineage
from isc.receiver import LocalMonitor
from isc.service_process_correlation import ServiceProcessEvidence

OWNER = "a" * 32
TASK = "b" * 32
ROOT = ProcessIdentity(201, 500, 1, "worker")
LINEAGE = ProcessLineage((ROOT,), (), 0)
KEY = (OWNER, TASK)


def event(kind, *, owner=OWNER, task=TASK, children=False):
    return LifecycleEvent(owner, task, kind, children_quiescent=children)


def supervisor():
    instance = NativeWorkSupervisor(
        lineage_scanner=lambda: LINEAGE,
        membership_checker=lambda *args: ServiceProcessEvidence.OBSERVED_MEMBER,
    )
    instance.register(OWNER, TASK, ROOT, service="sample.service", scope="--user")
    return instance


class NativeLifecycleTests(unittest.TestCase):
    def test_cannot_register_unknown_task_or_mismatched_owner(self):
        sup = supervisor()
        self.assertFalse(sup.observe_lifecycle(
            OWNER, "unknown", event(EventKind.ACTIVITY), now=1,
        ))
        self.assertFalse(sup.observe_lifecycle(
            OWNER, TASK, event(EventKind.ACTIVITY, owner="other"), now=1,
        ))
        self.assertEqual(sup.lifecycle_summary(now=1).unknown, 1)

    def test_busy_event_requires_fresh_verified_native_membership(self):
        sup = supervisor()
        self.assertTrue(sup.observe_lifecycle(
            OWNER, TASK, event(EventKind.ACTIVITY), now=2,
        ))
        self.assertEqual(sup.lifecycle_summary(now=2).unknown, 1)
        sup.observe(now=3)
        result = sup.lifecycle_summary(now=3)
        self.assertEqual((result.busy, result.unknown), (1, 0))
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.task_owner_authenticated)
        self.assertNotIn(OWNER, repr(result))
        self.assertNotIn("sample.service", repr(result))

    def test_external_wait_remains_busy_without_cpu_progress(self):
        sup = supervisor()
        sup.observe(now=2)
        self.assertTrue(sup.observe_lifecycle(
            OWNER, TASK, event(EventKind.WAITING_EXTERNAL), now=3,
        ))
        self.assertIs(sup._lifecycle.state(
            OWNER, TASK, supervised=True, now=3,
        ), WorkState.WAITING_EXTERNAL)
        self.assertEqual(sup.lifecycle_summary(now=3).busy, 1)

    def test_finished_event_never_proves_completion(self):
        sup = supervisor()
        sup.observe(now=2)
        self.assertTrue(sup.observe_lifecycle(
            OWNER, TASK, event(EventKind.FINISHED, children=True), now=3,
        ))
        self.assertEqual(sup.lifecycle_summary(now=3).unknown, 1)
        sup.observe_lifecycle(
            OWNER, TASK, event(EventKind.ACTIVITY), now=4,
        )
        self.assertEqual(sup.lifecycle_summary(now=4).unknown, 1)

    def test_human_confirmation_with_children_quiescent_not_authority(self):
        sup = supervisor()
        sup.observe(now=2)
        self.assertTrue(sup.observe_lifecycle(
            OWNER, TASK,
            event(EventKind.HUMAN_INPUT_CONFIRMED, children=True), now=3,
        ))
        self.assertEqual(sup.lifecycle_summary(now=3).unknown, 1)

    def test_permission_stop_and_child_quiesced_fail_closed(self):
        for kind in (
            EventKind.PERMISSION_REQUESTED,
            EventKind.STOPPED,
            EventKind.CHILD_QUIESCED,
        ):
            with self.subTest(kind=kind):
                sup = supervisor()
                sup.observe(now=2)
                self.assertTrue(sup.observe_lifecycle(
                    OWNER, TASK, event(kind), now=3,
                ))
                self.assertEqual(sup.lifecycle_summary(now=3).unknown, 1)

    def test_stale_claim_and_clock_reversal_unknown(self):
        sup = supervisor()
        sup.observe(now=2)
        sup.observe_lifecycle(OWNER, TASK, event(EventKind.ACTIVITY), now=4)
        self.assertEqual(sup.lifecycle_summary(now=4).busy, 1)
        self.assertEqual(sup.lifecycle_summary(now=95).unknown, 1)
        self.assertFalse(sup.observe_lifecycle(
            OWNER, TASK, event(EventKind.ACTIVITY), now=3,
        ))
        self.assertEqual(sup.lifecycle_summary(now=5).unknown, 1)

    def test_scanner_gap_invalidates_busy_until_sticky_unknown(self):
        sup = supervisor()
        sup.observe(now=2)
        sup.observe_lifecycle(OWNER, TASK, event(EventKind.ACTIVITY), now=3)
        self.assertEqual(sup.lifecycle_summary(now=3).busy, 1)
        sup.observe_snapshot(None, now=4)
        self.assertEqual(sup.lifecycle_summary(now=4).unknown, 1)
        sup.observe(now=5)
        self.assertEqual(sup.lifecycle_summary(now=5).unknown, 1)

    def test_missing_or_changed_native_process_never_completed(self):
        sup = supervisor()
        sup.observe(now=2)
        sup.observe_lifecycle(OWNER, TASK, event(EventKind.WAITING_EXTERNAL), now=3)
        sup.observe_snapshot(ProcessLineage((), (), 0), now=4)
        self.assertEqual(sup.lifecycle_summary(now=4).unknown, 1)

    def test_in_process_local_monitor_has_no_socket_registration(self):
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                monitor.enroll_native_work(
                    OWNER, TASK, ROOT, service="sample.service", scope="--user",
                )
                self.assertTrue(monitor.observe_native_lifecycle(
                    OWNER, TASK, event(EventKind.WAITING_EXTERNAL), now=1,
                ))
                self.assertEqual(
                    monitor.native_lifecycle_summary(now=1).unknown, 1,
                )
                monitor.update_native_lineage(LINEAGE, 2)
                result = monitor.native_lifecycle_summary(now=2)
                self.assertEqual((result.busy, result.unknown), (1, 0))
                self.assertFalse(result.task_owner_authenticated)

    def test_public_summary_contains_no_identifiers(self):
        model = NativeLifecycleCorrelator()
        self.assertTrue(model.report(
            OWNER, TASK, event(EventKind.CHILD_STARTED), now=1, enrolled=True,
        ))
        summary = model.aggregate((KEY,), frozenset((KEY,)), now=1)
        self.assertEqual(summary.busy, 1)
        self.assertFalse(summary.coverage_verified)
        self.assertNotIn(OWNER, repr(summary))
        self.assertNotIn(TASK, repr(summary))

    def test_invalid_timing_or_untrusted_event_cannot_change_state(self):
        model = NativeLifecycleCorrelator()
        self.assertFalse(model.report(
            OWNER, TASK, event(EventKind.ACTIVITY), now=float("nan"),
            enrolled=True,
        ))
        self.assertFalse(model.report(
            OWNER, TASK, object(), now=1, enrolled=True,
        ))
        self.assertIs(model.state(
            OWNER, TASK, supervised=True, now=2,
        ), WorkState.UNKNOWN)
        with self.assertRaises(ValueError):
            model.aggregate((KEY,), frozenset((KEY,)), now=float("inf"))


if __name__ == "__main__":
    unittest.main()

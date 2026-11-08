"""Synthetic native supervision integration; no live services or process IO."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.native_work_supervisor import NativeWorkSupervisor
from isc.policy import WorkState
from isc.process_lifecycle import ProcessIdentity, ProcessLineage
from isc.service_process_correlation import ServiceProcessEvidence

OWNER = "a" * 32
TASK = "b" * 32
ROOT = ProcessIdentity(101, 14, 1, "python3")
CHILD = ProcessIdentity(102, 15, 101, "worker")


def line(*children, root=ROOT):
    return ProcessLineage((root,) if root else (), children, 0)


class NativeWorkSupervisorTests(unittest.TestCase):
    def build(self, scans, checker=None):
        seq = iter(scans)
        observed = []
        def membership(service, process, scope):
            observed.append((service, process.pid, scope))
            if checker:
                return checker(service, process, scope)
            return ServiceProcessEvidence.OBSERVED_MEMBER
        sup = NativeWorkSupervisor(
            lineage_scanner=lambda: next(seq), membership_checker=membership,
        )
        sup.register(OWNER, TASK, ROOT, service="example.service", scope="--user")
        return sup, observed

    def test_stable_root_and_child_are_observed_not_authenticated(self):
        supervisor, calls = self.build([line(CHILD), line(CHILD)])
        first = supervisor.observe(now=1)
        second = supervisor.observe(now=2)
        self.assertEqual(second.registered, 1)
        self.assertEqual(second.observed_members, 1)
        self.assertEqual(second.unknown, 0)
        self.assertEqual(supervisor.states(), (WorkState.ACTIVE,))
        self.assertFalse(first.task_owner_authenticated)
        self.assertFalse(second.coverage_verified)
        self.assertEqual(len(calls), 4)
        self.assertNotIn("example.service", repr(second))
        self.assertNotIn(OWNER, repr(second))

    def test_disappearing_child_remains_unknown(self):
        supervisor, _ = self.build([line(CHILD), line()])
        supervisor.observe(now=1)
        second = supervisor.observe(now=2)
        self.assertEqual(second.unknown, 1)
        self.assertEqual(second.observed_members, 0)
        self.assertEqual(supervisor.states(), (WorkState.UNKNOWN,))

    def test_root_outside_service_cgroup_never_looks_complete(self):
        def outside(service, process, scope):
            return (ServiceProcessEvidence.OBSERVED_OUTSIDE
                    if process.pid == ROOT.pid
                    else ServiceProcessEvidence.OBSERVED_MEMBER)
        supervisor, _ = self.build([line(CHILD), line(CHILD)], outside)
        self.assertEqual(supervisor.observe(now=1).unknown, 1)
        self.assertEqual(supervisor.observe(now=2).unknown, 1)
        self.assertEqual(supervisor.states(), (WorkState.UNKNOWN,))

    def test_child_unknown_does_not_count_parent_as_supervised(self):
        def unknown_child(service, process, scope):
            return (ServiceProcessEvidence.UNKNOWN
                    if process.pid == CHILD.pid
                    else ServiceProcessEvidence.OBSERVED_MEMBER)
        supervisor, _ = self.build([line(CHILD)], unknown_child)
        result = supervisor.observe(now=1)
        self.assertEqual((result.observed_members, result.unknown), (0, 1))
        self.assertEqual(supervisor.states(), (WorkState.UNKNOWN,))

    def test_scanner_failure_stays_unknown_after_recovery(self):
        def broken():
            raise OSError("unavailable")
        sequence = iter([line(CHILD)])
        supervisor = NativeWorkSupervisor(
            lineage_scanner=broken,
            membership_checker=lambda *args: ServiceProcessEvidence.OBSERVED_MEMBER,
        )
        supervisor.register(OWNER, TASK, ROOT, service="example.service", scope="--user")
        self.assertEqual(supervisor.observe(now=1).unknown, 1)
        supervisor._scanner = lambda: next(sequence)
        self.assertEqual(supervisor.observe(now=2).unknown, 1)
        self.assertEqual(supervisor.states(), (WorkState.UNKNOWN,))

    def test_missed_sampling_window_is_sticky_unknown(self):
        supervisor, _ = self.build([line(CHILD), line(CHILD)])
        supervisor.observe(now=1)
        self.assertEqual(supervisor.observe(now=100).unknown, 1)
        self.assertEqual(supervisor.states(), (WorkState.UNKNOWN,))

    def test_refuses_untrusted_registration_and_duplicate(self):
        supervisor = NativeWorkSupervisor(
            lineage_scanner=lambda: line(),
            membership_checker=lambda *args: ServiceProcessEvidence.OBSERVED_MEMBER,
        )
        for service, scope in (
            ("bad.timer", "--user"), ("../bad.service", "--user"),
            ("good.service", "--bogus"),
        ):
            with self.subTest(service=service, scope=scope):
                with self.assertRaises(ValueError):
                    supervisor.register(OWNER, TASK, ROOT, service=service, scope=scope)
        supervisor.register(OWNER, TASK, ROOT, service="good.service", scope="--user")
        with self.assertRaises(ValueError):
            supervisor.register(OWNER, TASK, ROOT, service="good.service", scope="--user")
        self.assertEqual(supervisor.summary().registered, 1)

    def test_clock_reversal_fails_closed(self):
        supervisor, _ = self.build([line(), line()])
        supervisor.observe(now=5)
        result = supervisor.observe(now=4)
        self.assertEqual(result.unknown, 1)
        self.assertEqual(supervisor.states(), (WorkState.UNKNOWN,))

    def test_supervision_never_reports_finished_or_complete(self):
        supervisor, _ = self.build([line(), line(root=None)])
        supervisor.observe(now=1)
        result = supervisor.observe(now=2)
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.task_owner_authenticated)
        self.assertEqual(supervisor.states(), (WorkState.UNKNOWN,))


if __name__ == "__main__":
    unittest.main()

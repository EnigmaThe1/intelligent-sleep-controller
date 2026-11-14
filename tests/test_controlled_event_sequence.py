"""Ordered opt-in native event ingestion; no service or power operations."""
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.controlled_event_sequence import ControlledEventSequence
from isc.cgroup_subtree import CgroupSubtreeSummary
from isc.events import EventKind, LifecycleEvent
from isc.linux_peer_process import PeerProcessEvidence
from isc.native_service_enrollment import NativeServiceProcess
from isc.native_work_supervisor import NativeWorkSupervisor
from isc.policy import WorkState
from isc.process_lifecycle import ProcessIdentity, ProcessLineage
from isc.receiver import LocalMonitor
from isc.service_process_correlation import ServiceProcessEvidence

OWNER = "a" * 32
TASK = "b" * 32
SERVICE = "sample.service"
ROOT = ProcessIdentity(103, 550, 1, "agent")
LINEAGE = ProcessLineage((ROOT,), (), 0)
PIN = NativeServiceProcess(
    ROOT, SERVICE, "--user", "f" * 32, "/user.slice/sample.service",
)
PEER = PeerProcessEvidence(ROOT, os.getuid())


def event(kind=EventKind.STARTED, *, owner=OWNER, task=TASK):
    return LifecycleEvent(owner, task, kind)


def pinned_supervisor():
    discovery = Mock(return_value=PIN)
    sup = NativeWorkSupervisor(
        membership_checker=lambda *args: ServiceProcessEvidence.OBSERVED_MEMBER,
        subtree_checker=lambda _: CgroupSubtreeSummary(1, unknown=False),
    )
    assert sup.register_native_service(
        OWNER, TASK, service=SERVICE, scope="--user", discovery=discovery,
    )
    sup.observe_snapshot(LINEAGE, now=10)
    return sup, discovery


@contextmanager
def unix_pair():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        yield a, b
    finally:
        a.close()
        b.close()


class OrderedEventSequenceTests(unittest.TestCase):
    def test_requires_started_sequence_one(self):
        guard = ControlledEventSequence()
        self.assertFalse(guard.observe(
            OWNER, TASK, event(EventKind.ACTIVITY), sequence=1, now=1,
        ))
        self.assertTrue(guard.invalid(OWNER, TASK))
        self.assertFalse(guard.observe(
            OWNER, TASK, event(), sequence=1, now=2,
        ))

    def test_contiguous_events_accepted_and_replay_is_sticky(self):
        guard = ControlledEventSequence()
        self.assertTrue(guard.observe(
            OWNER, TASK, event(), sequence=1, now=10,
        ))
        self.assertTrue(guard.observe(
            OWNER, TASK, event(EventKind.WAITING_EXTERNAL), sequence=2, now=11,
        ))
        self.assertFalse(guard.observe(
            OWNER, TASK, event(EventKind.ACTIVITY), sequence=2, now=12,
        ))
        self.assertTrue(guard.invalid(OWNER, TASK))
        self.assertFalse(guard.observe(
            OWNER, TASK, event(EventKind.ACTIVITY), sequence=3, now=13,
        ))

    def test_skipped_event_marks_unknown(self):
        guard = ControlledEventSequence()
        guard.observe(OWNER, TASK, event(), sequence=1, now=10)
        self.assertFalse(guard.observe(
            OWNER, TASK, event(EventKind.ACTIVITY), sequence=3, now=11,
        ))
        self.assertTrue(guard.invalid(OWNER, TASK))

    def test_clock_reversal_and_sampling_gap_fails_closed(self):
        for when in (9, 71):
            with self.subTest(when=when):
                guard = ControlledEventSequence()
                guard.observe(OWNER, TASK, event(), sequence=1, now=10)
                self.assertFalse(guard.observe(
                    OWNER, TASK, event(EventKind.ACTIVITY), sequence=2, now=when,
                ))
                self.assertTrue(guard.invalid(OWNER, TASK))

    def test_invalid_payload_cannot_mutate_or_grant_progress(self):
        guard = ControlledEventSequence()
        for seq, when, kind in (
            (True, 1, EventKind.STARTED),
            (0, 1, EventKind.STARTED),
            (2**31, 1, EventKind.STARTED),
            (1, float("nan"), EventKind.STARTED),
        ):
            with self.subTest(seq=seq, when=when):
                self.assertFalse(guard.observe(
                    OWNER, TASK, event(kind), sequence=seq, now=when,
                ))
        self.assertEqual(guard.observed_count(), 0)
        self.assertTrue(guard.observe(
            OWNER, TASK, event(), sequence=1, now=1,
        ))

    def test_mismatched_identity_does_not_create_stream(self):
        guard = ControlledEventSequence()
        self.assertFalse(guard.observe(
            OWNER, TASK, event(owner="wrong"), sequence=1, now=1,
        ))
        self.assertEqual(guard.observed_count(), 0)
        self.assertTrue(guard.observe(
            OWNER, TASK, event(), sequence=1, now=2,
        ))

    def test_session_limit_never_evicts_old_evidence(self):
        guard = ControlledEventSequence()
        for index in range(256):
            self.assertTrue(guard.observe(
                OWNER, f"task{index}", event(task=f"task{index}"),
                sequence=1, now=1,
            ))
        self.assertFalse(guard.observe(
            OWNER, "overflow", event(task="overflow"),
            sequence=1, now=2,
        ))
        self.assertEqual(guard.observed_count(), 256)


class OrderedNativeAdapterTests(unittest.TestCase):
    def test_valid_start_and_external_wait_only_reinforce_busy(self):
        sup, _ = pinned_supervisor()
        self.assertTrue(sup.observe_ordered_peer_lifecycle(
            OWNER, TASK, event(), PEER, sequence=1, now=11,
        ))
        self.assertTrue(sup.observe_ordered_peer_lifecycle(
            OWNER, TASK, event(EventKind.WAITING_EXTERNAL),
            PEER, sequence=2, now=12,
        ))
        state = sup._lifecycle.state(
            OWNER, TASK, supervised=True, now=12,
        )
        self.assertEqual(state, WorkState.WAITING_EXTERNAL)
        pulse = sup.lifecycle_summary(now=12)
        self.assertEqual((pulse.busy, pulse.unknown), (1, 0))
        self.assertFalse(pulse.task_owner_authenticated)
        self.assertFalse(pulse.coverage_verified)

    def test_matching_peer_replay_becomes_sticky_unknown(self):
        sup, _ = pinned_supervisor()
        sup.observe_ordered_peer_lifecycle(
            OWNER, TASK, event(), PEER, sequence=1, now=11,
        )
        self.assertFalse(sup.observe_ordered_peer_lifecycle(
            OWNER, TASK, event(EventKind.ACTIVITY), PEER, sequence=1, now=12,
        ))
        self.assertFalse(sup.observe_ordered_peer_lifecycle(
            OWNER, TASK, event(EventKind.ACTIVITY), PEER, sequence=2, now=13,
        ))
        self.assertEqual(sup.lifecycle_summary(now=13).unknown, 1)

    def test_unrelated_peer_cannot_poison_or_claim_enrolled_task(self):
        sup, _ = pinned_supervisor()
        foreign = PeerProcessEvidence(
            ProcessIdentity(500, 20, 1, "foreign"), os.getuid(),
        )
        self.assertFalse(sup.observe_ordered_peer_lifecycle(
            OWNER, TASK, event(), foreign, sequence=1, now=11,
        ))
        self.assertEqual(sup.lifecycle_summary(now=11).unknown, 1)
        self.assertTrue(sup.observe_ordered_peer_lifecycle(
            OWNER, TASK, event(), PEER, sequence=1, now=11,
        ))
        self.assertEqual(sup.lifecycle_summary(now=11).busy, 1)

    def test_unpinned_and_unobserved_tasks_cannot_register(self):
        sup = NativeWorkSupervisor(
            membership_checker=lambda *args: ServiceProcessEvidence.OBSERVED_MEMBER,
        )
        sup.register(OWNER, TASK, ROOT, service=SERVICE, scope="--user")
        sup.observe_snapshot(LINEAGE, now=10)
        self.assertFalse(sup.observe_ordered_peer_lifecycle(
            OWNER, TASK, event(), PEER, sequence=1, now=11,
        ))
        other, _ = pinned_supervisor()
        other.observe_snapshot(None, now=11)
        self.assertFalse(other.observe_ordered_peer_lifecycle(
            OWNER, TASK, event(), PEER, sequence=1, now=12,
        ))

    def test_changed_service_activation_never_accepts_event(self):
        sup, discovery = pinned_supervisor()
        discovery.return_value = NativeServiceProcess(
            ROOT, SERVICE, "--user", "e" * 32, PIN.cgroup,
        )
        self.assertFalse(sup.observe_ordered_peer_lifecycle(
            OWNER, TASK, event(), PEER, sequence=1, now=11,
        ))
        self.assertEqual(sup.lifecycle_summary(now=11).unknown, 1)

    def test_finish_and_permission_requests_remain_unknown(self):
        for kind in (EventKind.FINISHED, EventKind.PERMISSION_REQUESTED):
            with self.subTest(kind=kind):
                sup, _ = pinned_supervisor()
                self.assertTrue(sup.observe_ordered_peer_lifecycle(
                    OWNER, TASK, event(), PEER, sequence=1, now=11,
                ))
                self.assertTrue(sup.observe_ordered_peer_lifecycle(
                    OWNER, TASK, event(kind), PEER, sequence=2, now=12,
                ))
                self.assertEqual(sup.lifecycle_summary(now=12).unknown, 1)

    def test_receiver_opt_in_adapter_has_no_new_listener_or_public_field(self):
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                sup = monitor._native_supervisor
                pin_reader = Mock(return_value=PIN)
                sup._checker = lambda *args: ServiceProcessEvidence.OBSERVED_MEMBER
                sup._subtree_checker = lambda _: CgroupSubtreeSummary(
                    1, unknown=False,
                )
                self.assertTrue(sup.register_native_service(
                    OWNER, TASK, service=SERVICE, scope="--user",
                    discovery=pin_reader,
                ))
                monitor.update_native_lineage(LINEAGE, 10)
                with unix_pair() as pair:
                    with patch("isc.receiver.read_linux_peer_process",
                               return_value=PEER):
                        self.assertTrue(
                            monitor.observe_ordered_native_peer_lifecycle(
                                OWNER, TASK, event(), pair[0], sequence=1, now=11,
                            )
                        )
                self.assertEqual(monitor.native_lifecycle_summary(now=11).busy, 1)

    def test_invalid_peer_read_never_creates_sequence(self):
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                with unix_pair() as pair:
                    with patch("isc.receiver.read_linux_peer_process",
                               return_value=None):
                        self.assertFalse(
                            monitor.observe_ordered_native_peer_lifecycle(
                                OWNER, TASK, event(), pair[0], sequence=1, now=11,
                            )
                        )
                self.assertEqual(
                    monitor._native_supervisor._event_sequence.observed_count(), 0,
                )


if __name__ == "__main__":
    unittest.main()

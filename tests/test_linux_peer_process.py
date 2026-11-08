"""Kernel peer and native lifecycle attribution tests; no real service changes."""
import os
from pathlib import Path
import socket
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.cgroup_subtree import CgroupSubtreeSummary
from isc.events import EventKind, LifecycleEvent
from isc.linux_peer_process import (
    PeerProcessEvidence, read_linux_peer_process,
)
from isc.native_service_enrollment import NativeServiceProcess
from isc.native_work_supervisor import NativeWorkSupervisor
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


def event(kind=EventKind.ACTIVITY):
    return LifecycleEvent(OWNER, TASK, kind)


def pinned_supervisor(discover=None):
    discovery = discover or Mock(return_value=PIN)
    supervisor = NativeWorkSupervisor(
        membership_checker=lambda *args: ServiceProcessEvidence.OBSERVED_MEMBER,
        subtree_checker=lambda path: CgroupSubtreeSummary(1, unknown=False),
    )
    assert supervisor.register_native_service(
        OWNER, TASK, service=SERVICE, scope="--user", discovery=discovery,
    )
    supervisor.observe_snapshot(LINEAGE, now=10)
    return supervisor, discovery


class LinuxPeerProcessTests(unittest.TestCase):
    def test_kernel_socketpair_credentials_and_stable_proc_identity(self):
        with socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM) as pair:
            expected = ProcessIdentity(
                os.getpid(), 1000, os.getppid(), "python3",
            )
            lookup = Mock(return_value=expected)
            found = read_linux_peer_process(
                pair[0], process_lookup=lookup,
            )
        self.assertIsNotNone(found)
        self.assertEqual(found.identity, expected)
        self.assertEqual(found.uid, os.getuid())
        self.assertEqual(lookup.call_count, 2)
        self.assertFalse(found.authenticated_task_owner)
        self.assertFalse(found.coverage_verified)
        # This evidence stays in-process; no health-wire changes are made.

    def test_mismatched_uid_rejected_before_proc_lookup(self):
        with socket.socketpair() as pair:
            lookup = Mock()
            result = read_linux_peer_process(
                pair[0], expected_uid=os.getuid() + 1,
                process_lookup=lookup,
            )
        self.assertIsNone(result)
        lookup.assert_not_called()

    def test_pid_reuse_or_unavailable_proc_evidence_rejected(self):
        with socket.socketpair() as pair:
            first = ProcessIdentity(os.getpid(), 200, 1, "agent")
            changed = ProcessIdentity(os.getpid(), 201, 1, "agent")
            for samples in ((first, changed), (None, first)):
                with self.subTest(samples=samples):
                    self.assertIsNone(read_linux_peer_process(
                        pair[0], process_lookup=Mock(side_effect=samples),
                    ))

    def test_invalid_kernel_credentials_rejected(self):
        sock = Mock()
        for pid, uid, gid in (
            (0, os.getuid(), 1),
            (1, os.getuid() + 1, 1),
            (4194305, os.getuid(), 1),
            (1, os.getuid(), -1),
        ):
            sock.getsockopt.return_value = struct.pack("3i", pid, uid, gid)
            with self.subTest(pid=pid, uid=uid, gid=gid):
                self.assertIsNone(read_linux_peer_process(
                    sock, process_lookup=Mock(),
                ))
        sock.getsockopt.return_value = b"broken"
        self.assertIsNone(read_linux_peer_process(sock))

    def test_process_lookup_exception_is_unknown(self):
        with socket.socketpair() as pair:
            self.assertIsNone(read_linux_peer_process(
                pair[0], process_lookup=Mock(side_effect=OSError("gone")),
            ))

    def test_no_claimed_json_pid_or_token_supported(self):
        with socket.socketpair() as pair:
            self.assertIsNone(read_linux_peer_process(
                pair[0], process_lookup=123,
            ))


class NativePeerLifecycleTests(unittest.TestCase):
    def test_pinned_native_peer_accepts_busy_claim_but_not_task_authentication(self):
        supervisor, _ = pinned_supervisor()
        self.assertTrue(supervisor.observe_peer_lifecycle(
            OWNER, TASK, event(), PeerProcessEvidence(ROOT, os.getuid()), now=11,
        ))
        summary = supervisor.lifecycle_summary(now=11)
        self.assertEqual((summary.busy, summary.unknown), (1, 0))
        self.assertFalse(summary.task_owner_authenticated)
        self.assertFalse(summary.coverage_verified)
        self.assertNotIn(SERVICE, repr(summary))
        self.assertNotIn(OWNER, repr(summary))

    def test_unpinned_manual_registration_rejected(self):
        sup = NativeWorkSupervisor(
            membership_checker=lambda *args: ServiceProcessEvidence.OBSERVED_MEMBER,
        )
        sup.register(OWNER, TASK, ROOT, service=SERVICE, scope="--user")
        sup.observe_snapshot(LINEAGE, now=10)
        self.assertFalse(sup.observe_peer_lifecycle(
            OWNER, TASK, event(), PeerProcessEvidence(ROOT, os.getuid()),
            now=11,
        ))

    def test_forged_or_other_process_cannot_claim_task(self):
        sup, discovery = pinned_supervisor()
        for peer in (
            None,
            PeerProcessEvidence(
                ProcessIdentity(ROOT.pid, ROOT.start_ticks + 1, 1, ROOT.name),
                os.getuid(),
            ),
            PeerProcessEvidence(ROOT, os.getuid() + 1),
            PeerProcessEvidence(ROOT, os.getuid(), authenticated_task_owner=True),
            PeerProcessEvidence(ROOT, os.getuid(), coverage_verified=True),
        ):
            with self.subTest(peer=peer):
                self.assertFalse(sup.observe_peer_lifecycle(
                    OWNER, TASK, event(), peer, now=11,
                ))
        self.assertEqual(sup.lifecycle_summary(now=11).unknown, 1)

    def test_service_activation_change_rejected_and_sticky_unknown(self):
        sup, discovery = pinned_supervisor()
        other = NativeServiceProcess(
            ROOT, SERVICE, "--user", "e" * 32, PIN.cgroup,
        )
        discovery.return_value = other
        self.assertFalse(sup.observe_peer_lifecycle(
            OWNER, TASK, event(), PeerProcessEvidence(ROOT, os.getuid()), now=11,
        ))
        self.assertEqual(sup.lifecycle_summary(now=11).unknown, 1)
        discovery.return_value = PIN
        sup.observe_snapshot(LINEAGE, now=12)
        self.assertEqual(sup.lifecycle_summary(now=12).unknown, 1)

    def test_stale_service_supervision_never_supports_claim_or_busy(self):
        sup, _ = pinned_supervisor()
        self.assertFalse(sup.observe_peer_lifecycle(
            OWNER, TASK, event(), PeerProcessEvidence(ROOT, os.getuid()), now=71,
        ))
        sup.observe_lifecycle(OWNER, TASK, event(), now=11)
        self.assertEqual(sup.lifecycle_summary(now=71).unknown, 1)

    def test_completion_claim_stays_unknown_even_from_pinned_peer(self):
        sup, _ = pinned_supervisor()
        self.assertTrue(sup.observe_peer_lifecycle(
            OWNER, TASK, event(EventKind.FINISHED),
            PeerProcessEvidence(ROOT, os.getuid()), now=11,
        ))
        self.assertEqual(sup.lifecycle_summary(now=11).unknown, 1)

    def test_in_process_receiver_reads_peer_evidence_without_new_socket_route(self):
        from unittest.mock import patch
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                receiver = monitor._native_supervisor
                pin = Mock(return_value=PIN)
                receiver._checker = lambda *args: ServiceProcessEvidence.OBSERVED_MEMBER
                receiver._subtree_checker = lambda path: CgroupSubtreeSummary(
                    1, unknown=False,
                )
                self.assertTrue(receiver.register_native_service(
                    OWNER, TASK, service=SERVICE, scope="--user", discovery=pin,
                ))
                monitor.update_native_lineage(LINEAGE, 10)
                with socket.socketpair() as pair:
                    with patch("isc.receiver.read_linux_peer_process",
                               return_value=PeerProcessEvidence(ROOT, os.getuid())):
                        self.assertTrue(monitor.observe_native_peer_lifecycle(
                            OWNER, TASK, event(EventKind.WAITING_EXTERNAL),
                            pair[0], now=11,
                        ))
                summary = monitor.native_lifecycle_summary(now=11)
                self.assertEqual(summary.busy, 1)
                self.assertFalse(summary.task_owner_authenticated)


if __name__ == "__main__":
    unittest.main()

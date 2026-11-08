"""Synthetic systemd-origin process discovery; no real unit activation."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.cgroup_membership import CgroupMembership
from isc.native_service_enrollment import (
    _metadata, _service_query, discover_native_service_process,
)
from isc.process_lifecycle import ProcessIdentity
from isc.native_work_supervisor import NativeWorkSupervisor
from isc.native_service_enrollment import NativeServiceProcess
from isc.process_lifecycle import ProcessLineage
from isc.service_process_correlation import ServiceProcessEvidence

SERVICE = "agent.service"
PID = 145
INV_A = "a" * 32
INV_B = "b" * 32
CGROUP = "/user.slice/agent.service"
IDENTITY = ProcessIdentity(PID, 500, 1, "python3")


def raw(pid=PID, invocation=INV_A, group=CGROUP, name=SERVICE):
    return (
        f"Id={name}\nMainPID={pid}\nInvocationID={invocation}\n"
        f"ControlGroup={group}\n"
    )


class NativeServiceEnrollmentTests(unittest.TestCase):
    def discover(self, first=None, second=None, *, identity=IDENTITY,
                 matched=True, scope="--user"):
        captures = iter([raw() if first is None else first,
                         raw() if second is None else second])
        read = Mock(side_effect=lambda q: next(captures))
        lookup = Mock(return_value=identity)
        membership = Mock(return_value=CgroupMembership(matched))
        result = discover_native_service_process(
            SERVICE, scope=scope, capture=read,
            process_lookup=lookup, membership=membership,
        )
        return result, read, lookup, membership

    def test_stable_systemd_process_returns_only_non_authoritative_evidence(self):
        result, read, lookup, membership = self.discover()
        self.assertIsNotNone(result)
        self.assertEqual(result.identity, IDENTITY)
        self.assertEqual(result.invocation, INV_A)
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.task_owner_authenticated)
        self.assertEqual(read.call_count, 2)
        lookup.assert_called_once_with(PID)
        membership.assert_called_once_with(IDENTITY, CGROUP)

    def test_changed_service_invocation_rejected(self):
        result, _, _, _ = self.discover(second=raw(invocation=INV_B))
        self.assertIsNone(result)

    def test_changed_cgroup_or_main_pid_rejected(self):
        for second in (raw(group="/user.slice/other.service"),
                       raw(pid=146)):
            with self.subTest(second=second):
                self.assertIsNone(self.discover(second=second)[0])

    def test_missing_main_pid_or_invocation_rejected_before_lookup(self):
        for first in (raw(pid=0), raw(pid="bad"), raw(invocation=""),
                      raw(group=""), raw(group="/"), raw(group="/a/../b"),
                      raw(name="other.service"), "bad", None):
            with self.subTest(first=first):
                capture = Mock(return_value=first)
                lookup = Mock()
                result = discover_native_service_process(
                    SERVICE, scope="--user", capture=capture,
                    process_lookup=lookup,
                )
                self.assertIsNone(result)
                lookup.assert_not_called()

    def test_pid_reuse_or_missing_process_identity_fails_closed(self):
        for identity in (None, ProcessIdentity(146, 500, 1, "python3"),
                         ProcessIdentity(PID, 0, 1, "python3")):
            with self.subTest(identity=identity):
                result, read, lookup, membership = self.discover(identity=identity)
                self.assertIsNone(result)
                self.assertEqual(read.call_count, 1)
                membership.assert_not_called()

    def test_unknown_or_outside_membership_fails_closed(self):
        for matched in (None, False):
            with self.subTest(matched=matched):
                result, read, lookup, membership = self.discover(matched=matched)
                self.assertIsNone(result)
                self.assertEqual(read.call_count, 1)

    def test_malformed_units_rejected_without_native_io(self):
        for name, scope in (("bad.timer", "--user"),
                            ("../bad.service", "--user"),
                            (SERVICE, "--unknown")):
            read = Mock()
            result = discover_native_service_process(
                name, scope=scope, capture=read,
            )
            self.assertIsNone(result)
            read.assert_not_called()

    def test_scanner_failure_or_conflicting_properties_rejected(self):
        for broken in (RuntimeError("read failed"), OSError("missing")):
            with self.subTest(broken=broken):
                self.assertIsNone(discover_native_service_process(
                    SERVICE, scope="--user",
                    capture=Mock(side_effect=broken),
                ))
        self.assertIsNone(_metadata(raw() + "MainPID=145\n", SERVICE))
        self.assertIsNone(_metadata(raw(pid=PID, group="/"), SERVICE))

    def test_opt_in_native_registration_not_task_authentication(self):
        supervisor = NativeWorkSupervisor()
        evidence = NativeServiceProcess(
            IDENTITY, SERVICE, "--user", INV_A, CGROUP,
        )
        trusted_reader = Mock(return_value=evidence)
        self.assertTrue(supervisor.register_native_service(
            "a" * 32, "b" * 32, service=SERVICE, scope="--user",
            discovery=trusted_reader,
        ))
        trusted_reader.assert_called_once_with(SERVICE, scope="--user")
        summary = supervisor.summary()
        self.assertEqual(summary.registered, 1)
        self.assertFalse(summary.task_owner_authenticated)
        self.assertFalse(summary.coverage_verified)
        self.assertNotIn(CGROUP, str(summary))

    def test_native_registration_rejects_self_claim_and_mismatched_scope(self):
        supervisor = NativeWorkSupervisor()
        first = supervisor.register_native_service(
            "a" * 32, "b" * 32, service=SERVICE, scope="--user",
            discovery=lambda *a, **k: None,
        )
        self.assertFalse(first)
        conflicting = NativeServiceProcess(
            IDENTITY, SERVICE, "--system", INV_A, CGROUP,
        )
        self.assertFalse(supervisor.register_native_service(
            "a" * 32, "b" * 32, service=SERVICE, scope="--user",
            discovery=lambda *a, **k: conflicting,
        ))
        self.assertEqual(supervisor.summary().registered, 0)

    def test_native_activation_revalidated_each_observation(self):
        evidence = NativeServiceProcess(
            IDENTITY, SERVICE, "--user", INV_A, CGROUP,
        )
        calls = []
        def discover(*args, **kwargs):
            calls.append(1)
            return evidence
        scanner = lambda: ProcessLineage((IDENTITY,), (), 0)
        supervisor = NativeWorkSupervisor(
            lineage_scanner=scanner,
            membership_checker=lambda *args: ServiceProcessEvidence.OBSERVED_MEMBER,
        )
        self.assertTrue(supervisor.register_native_service(
            "a" * 32, "b" * 32, service=SERVICE, scope="--user",
            discovery=discover,
        ))
        self.assertEqual(supervisor.observe(now=1).observed_members, 1)
        self.assertEqual(supervisor.observe(now=2).observed_members, 1)
        self.assertEqual(len(calls), 3)
        self.assertFalse(supervisor.summary().task_owner_authenticated)

    def test_native_restart_is_sticky_unknown_after_recovery(self):
        enrolled = NativeServiceProcess(
            IDENTITY, SERVICE, "--user", INV_A, CGROUP,
        )
        restarted = NativeServiceProcess(
            IDENTITY, SERVICE, "--user", INV_B, CGROUP,
        )
        evidence = iter([enrolled, enrolled, restarted, enrolled])
        supervisor = NativeWorkSupervisor(
            lineage_scanner=lambda: ProcessLineage((IDENTITY,), (), 0),
            membership_checker=lambda *args: ServiceProcessEvidence.OBSERVED_MEMBER,
        )
        self.assertTrue(supervisor.register_native_service(
            "a" * 32, "b" * 32, service=SERVICE, scope="--user",
            discovery=lambda *a, **kw: next(evidence),
        ))
        self.assertEqual(supervisor.observe(now=1).observed_members, 1)
        after = supervisor.observe(now=2)
        self.assertEqual((after.observed_members, after.unknown), (0, 1))
        again = supervisor.observe(now=3)
        self.assertEqual((again.observed_members, again.unknown), (0, 1))

    def test_native_discovery_gap_is_sticky_unknown(self):
        enrolled = NativeServiceProcess(
            IDENTITY, SERVICE, "--user", INV_A, CGROUP,
        )
        evidence = iter([enrolled, None, enrolled])
        supervisor = NativeWorkSupervisor(
            lineage_scanner=lambda: ProcessLineage((IDENTITY,), (), 0),
            membership_checker=lambda *args: ServiceProcessEvidence.OBSERVED_MEMBER,
        )
        self.assertTrue(supervisor.register_native_service(
            "a" * 32, "b" * 32, service=SERVICE, scope="--user",
            discovery=lambda *a, **kw: next(evidence),
        ))
        self.assertEqual(supervisor.observe(now=1).unknown, 1)
        self.assertEqual(supervisor.observe(now=2).unknown, 1)

    def test_native_identity_or_cgroup_change_invalidates_pin(self):
        enrolled = NativeServiceProcess(
            IDENTITY, SERVICE, "--user", INV_A, CGROUP,
        )
        replacements = (
            NativeServiceProcess(
                ProcessIdentity(PID, 501, 1, "python3"),
                SERVICE, "--user", INV_A, CGROUP,
            ),
            NativeServiceProcess(
                IDENTITY, SERVICE, "--user", INV_A, "/user.slice/other.service",
            ),
        )
        for replacement in replacements:
            with self.subTest(replacement=replacement):
                evidence = iter([enrolled, replacement])
                supervisor = NativeWorkSupervisor(
                    lineage_scanner=lambda: ProcessLineage((IDENTITY,), (), 0),
                    membership_checker=lambda *args: ServiceProcessEvidence.OBSERVED_MEMBER,
                )
                self.assertTrue(supervisor.register_native_service(
                    "a" * 32, "b" * 32, service=SERVICE, scope="--user",
                    discovery=lambda *a, **kw: next(evidence),
                ))
                self.assertEqual(supervisor.observe(now=1).unknown, 1)

    def test_malformed_native_evidence_cannot_register(self):
        for evidence in (
            NativeServiceProcess(IDENTITY, SERVICE, "--user", "bad", CGROUP),
            NativeServiceProcess(IDENTITY, SERVICE, "--user", INV_A, "/"),
            NativeServiceProcess(IDENTITY, SERVICE, "--user", INV_A, "/a/../b"),
            NativeServiceProcess(IDENTITY, SERVICE, "--user", INV_A, CGROUP,
                                 task_owner_authenticated=True),
        ):
            with self.subTest(evidence=evidence):
                supervisor = NativeWorkSupervisor()
                self.assertFalse(supervisor.register_native_service(
                    "a" * 32, "b" * 32, service=SERVICE, scope="--user",
                    discovery=lambda *a, **kw: evidence,
                ))
                self.assertEqual(supervisor.summary().registered, 0)

    def test_command_is_single_unit_read_only(self):
        self.assertEqual(_service_query(SERVICE, "--user"), (
            "systemctl", "--user", "--no-pager", "show",
            "--property=Id,MainPID,InvocationID,ControlGroup",
            "--", SERVICE,
        ))


if __name__ == "__main__":
    unittest.main()

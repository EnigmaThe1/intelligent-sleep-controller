"""Synthetic bracketed service membership tests; no systemctl or real processes."""
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.cgroup_membership import CgroupMembership
from isc.process_lifecycle import ProcessIdentity
from isc.service_process_correlation import ServiceProcessEvidence
from isc.service_process_observer import observe_service_process
from isc.timer_service_lifecycle import ServiceTaskSnapshot

NAME = "build.service"
GROUP = "/user.slice/build.service"
INVOCATION_A = "a" * 32
INVOCATION_B = "b" * 32
PROCESS = ProcessIdentity(50, 100, 1, "python3")


def service(invocation=INVOCATION_A, group=GROUP, tasks=2):
    return ServiceTaskSnapshot(
        ((NAME, group, tasks),),
        invocations=((NAME, invocation),),
    )


class ServiceProcessObserverTests(unittest.TestCase):
    def observe(self, first, second, *, membership=True, scope="--user"):
        scanned = iter(((None, first), (None, second)))
        scanner = Mock(side_effect=lambda: next(scanned))
        probe = Mock(return_value=CgroupMembership(membership))
        evidence = observe_service_process(
            NAME, PROCESS, scope=scope, scanner=scanner, membership_probe=probe,
        )
        return evidence, scanner, probe

    def test_stable_service_membership_is_only_partial_evidence(self):
        result, scanner, probe = self.observe(service(), service())
        self.assertEqual(result.evidence, ServiceProcessEvidence.OBSERVED_MEMBER)
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.task_owner_authenticated)
        self.assertEqual(scanner.call_count, 2)
        probe.assert_called_once_with(PROCESS, GROUP)
        self.assertNotIn(NAME, repr(result))
        self.assertNotIn(GROUP, repr(result))
        self.assertNotIn(INVOCATION_A, repr(result))

    def test_stable_nonmembership_remains_non_authoritative(self):
        result, _, _ = self.observe(service(), service(), membership=False)
        self.assertEqual(result.evidence, ServiceProcessEvidence.OBSERVED_OUTSIDE)
        self.assertFalse(result.coverage_verified)

    def test_restart_after_procfs_read_is_unknown(self):
        result, scanner, probe = self.observe(
            service(), service(invocation=INVOCATION_B),
        )
        self.assertEqual(result.evidence, ServiceProcessEvidence.UNKNOWN)
        self.assertEqual(scanner.call_count, 2)
        self.assertEqual(probe.call_count, 1)

    def test_changed_service_cgroup_is_unknown(self):
        result, _, _ = self.observe(service(), service(group="/user.slice/new.service"))
        self.assertEqual(result.evidence, ServiceProcessEvidence.UNKNOWN)

    def test_unknown_before_read_short_circuits_membership_probe(self):
        for first in (None, service(invocation=None), service(group="")):
            with self.subTest(first=first):
                result, scanner, probe = self.observe(first, service())
                self.assertEqual(result.evidence, ServiceProcessEvidence.UNKNOWN)
                self.assertEqual(scanner.call_count, 1)
                probe.assert_not_called()

    def test_missing_process_or_scanner_evidence_is_unknown(self):
        result, _, _ = self.observe(service(), service(), membership=None)
        self.assertEqual(result.evidence, ServiceProcessEvidence.UNKNOWN)
        scanner = Mock(return_value=("malformed",))
        probe = Mock()
        result = observe_service_process(
            NAME, PROCESS, scope="--user", scanner=scanner, membership_probe=probe,
        )
        self.assertEqual(result.evidence, ServiceProcessEvidence.UNKNOWN)
        probe.assert_not_called()

    def test_scope_is_explicit_to_prevent_cross_scope_name_collision(self):
        scanner = Mock(return_value=(None, service()))
        probe = Mock()
        result = observe_service_process(
            NAME, PROCESS, scope="--system", scanner=scanner,
            membership_probe=probe,
        )
        self.assertEqual(result.evidence, ServiceProcessEvidence.UNKNOWN)
        probe.assert_not_called()

    def test_scanner_failure_returns_unknown_without_propagating(self):
        for error in (OSError("not available"), ValueError("bad metadata"),
                      RuntimeError("scanner unavailable")):
            with self.subTest(error=error):
                result = observe_service_process(
                    NAME, PROCESS, scope="--user",
                    scanner=Mock(side_effect=error),
                    membership_probe=Mock(),
                )
                self.assertEqual(result.evidence, ServiceProcessEvidence.UNKNOWN)

    def test_invalid_input_never_uses_io(self):
        scanner, probe = Mock(), Mock()
        for name, scope, identity in (
            ("invalid.timer", "--user", PROCESS),
            (NAME, "--bogus", PROCESS),
            (NAME, "--user", None),
        ):
            result = observe_service_process(
                name, identity, scope=scope, scanner=scanner,
                membership_probe=probe,
            )
            self.assertEqual(result.evidence, ServiceProcessEvidence.UNKNOWN)
        scanner.assert_not_called()
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()

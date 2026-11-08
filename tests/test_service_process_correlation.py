"""Synthetic service/process correlation: evidence never authorises suspend."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.cgroup_membership import CgroupMembership
from isc.service_process_correlation import (
    ServiceProcessEvidence, correlate_service_process,
)
from isc.timer_service_lifecycle import ServiceTaskSnapshot


A = "a" * 32
B = "b" * 32


def service(*, name="private.service", cgroup="/user.slice/private.service",
            count=2, invocation=A):
    return ServiceTaskSnapshot(
        ((name, cgroup, count),),
        invocations=((name, invocation),),
    )


def join(first=None, last=None, membership=True, *, name="private.service"):
    return correlate_service_process(
        name,
        service() if first is None else first,
        service() if last is None else last,
        CgroupMembership(membership),
    )


class ServiceProcessCorrelationTests(unittest.TestCase):
    def test_stable_service_identity_with_visible_member(self):
        result = join()
        self.assertIs(result.evidence, ServiceProcessEvidence.OBSERVED_MEMBER)
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.task_owner_authenticated)
        for private in ("private.service", "/user.slice/private.service", A):
            self.assertNotIn(private, repr(result))

    def test_member_outside_cgroup_does_not_claim_owner(self):
        result = join(membership=False)
        self.assertIs(result.evidence, ServiceProcessEvidence.OBSERVED_OUTSIDE)
        self.assertFalse(result.task_owner_authenticated)

    def test_restart_with_same_service_and_cgroup_is_unknown(self):
        result = join(last=service(invocation=B))
        self.assertIs(result.evidence, ServiceProcessEvidence.UNKNOWN)

    def test_missing_identity_or_cgroup_is_unknown(self):
        for first, last in (
            (service(invocation=None), service()),
            (service(), service(invocation=None)),
            (service(cgroup=""), service()),
            (service(), service(cgroup="")),
            (service(), service(cgroup="/other.slice/private.service")),
        ):
            with self.subTest(first=first, last=last):
                self.assertIs(
                    join(first=first, last=last).evidence,
                    ServiceProcessEvidence.UNKNOWN,
                )

    def test_missing_process_evidence_is_unknown(self):
        self.assertIs(join(membership=None).evidence, ServiceProcessEvidence.UNKNOWN)
        self.assertIs(
            correlate_service_process(
                "private.service", service(), service(), None,
            ).evidence,
            ServiceProcessEvidence.UNKNOWN,
        )
        self.assertIs(
            correlate_service_process(
                "private.service", service(), service(), CgroupMembership(True, True),
            ).evidence,
            ServiceProcessEvidence.UNKNOWN,
        )

    def test_missing_or_malformed_snapshot_does_not_supply_evidence(self):
        malformed = (
            ServiceTaskSnapshot((("private.service", "/c", 1),)),
            ServiceTaskSnapshot(
                (("private.service", "/c", 1),),
                invocations=(("different.service", A),),
            ),
            ServiceTaskSnapshot(
                (("private.service", "/c", 1), ("private.service", "/c", 1)),
                invocations=(("private.service", A), ("private.service", A)),
            ),
            ServiceTaskSnapshot(
                (("private.service", "/c", -1),),
                invocations=(("private.service", A),),
            ),
            ServiceTaskSnapshot(
                (("private.service", "/c", 1),),
                invocations=(("private.service", "bad"),),
            ),
        )
        for value in malformed:
            with self.subTest(snapshot=value):
                self.assertIs(
                    join(first=value).evidence,
                    ServiceProcessEvidence.UNKNOWN,
                )
        self.assertIs(
            correlate_service_process(
                "private.service", None, service(), CgroupMembership(True),
            ).evidence,
            ServiceProcessEvidence.UNKNOWN,
        )
        self.assertIs(
            join(name="../private.service").evidence,
            ServiceProcessEvidence.UNKNOWN,
        )

    def test_snapshot_task_count_change_not_completion(self):
        result = join(first=service(count=8), last=service(count=0))
        self.assertIs(result.evidence, ServiceProcessEvidence.OBSERVED_MEMBER)
        self.assertFalse(result.coverage_verified)

    def test_empty_or_changed_activation_is_not_safe_to_suppress_busy_work(self):
        for last in (service(count=0, invocation=None), service(invocation=B)):
            self.assertIs(
                join(last=last).evidence,
                ServiceProcessEvidence.UNKNOWN,
            )


if __name__ == "__main__":
    unittest.main()

"""Conservative correlation of native service and process observations.

An observed cgroup membership is evidence of *current placement*, not proof
that the service owns a particular application task. An activation ID, cgroup
or PID can change between queries. This module is a pure read-only join over
independently sampled data, never an authority to permit automatic suspend.
No service or process identifiers are returned in public results.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from .cgroup_membership import CgroupMembership
from .timer_service_lifecycle import ServiceTaskSnapshot
from .timer_service_tasks import _valid_cgroup

_ID = re.compile(r"[0-9a-fA-F]{32}\Z", re.ASCII)
_SERVICE = re.compile(r"[A-Za-z0-9_.:@-]{1,255}\.service\Z", re.ASCII)


class ServiceProcessEvidence(Enum):
    OBSERVED_MEMBER = "observed_member"
    OBSERVED_OUTSIDE = "observed_outside"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ServiceProcessCorrelation:
    evidence: ServiceProcessEvidence
    coverage_verified: bool = False
    task_owner_authenticated: bool = False


def _service_row(
    snapshot: ServiceTaskSnapshot, name: str,
) -> tuple[str, int | None, str | None] | None:
    """Validate the entire snapshot before selecting a single private row."""
    if (not isinstance(snapshot, ServiceTaskSnapshot)
            or snapshot.invocations is None
            or not isinstance(snapshot.observed, tuple)
            or not isinstance(snapshot.invocations, tuple)
            or len(snapshot.observed) != len(snapshot.invocations)
            or len(snapshot.observed) > 256):
        return None
    rows: dict[str, tuple[str, int | None]] = {}
    ids: dict[str, str | None] = {}
    for item in snapshot.observed:
        if not isinstance(item, tuple) or len(item) != 3:
            return None
        service, group, tasks = item
        if (not isinstance(service, str) or _SERVICE.fullmatch(service) is None
                or service in rows
                or not isinstance(group, str) or not _valid_cgroup(group)
                or (tasks is not None and
                    (type(tasks) is not int or tasks < 0))):
            return None
        rows[service] = (group, tasks)
    for item in snapshot.invocations:
        if not isinstance(item, tuple) or len(item) != 2:
            return None
        service, invocation = item
        if (not isinstance(service, str) or _SERVICE.fullmatch(service) is None
                or service in ids
                or (invocation is not None and
                    (not isinstance(invocation, str)
                     or _ID.fullmatch(invocation) is None))):
            return None
        ids[service] = invocation.lower() if invocation is not None else None
    if rows.keys() != ids.keys() or name not in rows:
        return None
    group, tasks = rows[name]
    return group, tasks, ids[name]


def correlate_service_process(
    service_name: str,
    before: ServiceTaskSnapshot | None,
    after: ServiceTaskSnapshot | None,
    membership: CgroupMembership | None,
) -> ServiceProcessCorrelation:
    """Correlate two activation samples and a bounded process membership check.

    Even positive results cannot establish authenticated task ownership,
    that no work is detached, or that a service has completed.
    """
    unknown = ServiceProcessCorrelation(ServiceProcessEvidence.UNKNOWN)
    if (not isinstance(service_name, str)
            or _SERVICE.fullmatch(service_name) is None
            or not isinstance(membership, CgroupMembership)
            or type(membership.match) not in (bool, type(None))
            or membership.coverage_verified
            or before is None or after is None):
        return unknown
    prior = _service_row(before, service_name)
    current = _service_row(after, service_name)
    if prior is None or current is None:
        return unknown
    prior_group, _prior_count, prior_invocation = prior
    current_group, _current_count, current_invocation = current
    if (not prior_group or not current_group
            or prior_group != current_group
            or prior_invocation is None or current_invocation is None
            or prior_invocation != current_invocation
            or membership.match is None):
        return unknown
    return ServiceProcessCorrelation(
        ServiceProcessEvidence.OBSERVED_MEMBER if membership.match
        else ServiceProcessEvidence.OBSERVED_OUTSIDE,
    )

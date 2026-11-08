"""Read-only, bracketed service/process membership observation.

A caller supplies a previously observed process identity and a particular
systemd service. Capture service metadata, probe procfs membership, and
capture metadata again. No user commands, service changes, sensitive output,
background workers, or automatic suspension. This remains non-authoritative:
a transient restart/reparenting between reads may not be visible, and no
external task owner's identity is authenticated.
"""
from __future__ import annotations

from typing import Callable

from .cgroup_membership import CgroupMembership, observe_cgroup_membership
from .process_lifecycle import ProcessIdentity
from .service_process_correlation import (
    ServiceProcessCorrelation, ServiceProcessEvidence,
    _SERVICE, _service_row, correlate_service_process,
)
from .timer_service_lifecycle import (
    ServiceTaskSnapshot, read_service_task_snapshots,
)


def observe_service_process(
    service_name: str,
    identity: ProcessIdentity,
    *,
    scope: str,
    scanner: Callable[
        [], tuple[ServiceTaskSnapshot | None, ServiceTaskSnapshot | None]
    ] = read_service_task_snapshots,
    membership_probe: Callable[[ProcessIdentity, str], CgroupMembership]
    = observe_cgroup_membership,
) -> ServiceProcessCorrelation:
    """Bracket procfs membership with two observations of a service activation.

    Only a matching, nonempty cgroup and invocation in *both* read-only
    samples can yield OBSERVED_MEMBER or OBSERVED_OUTSIDE. It remains UNKNOWN
    on conflicting evidence. An individual sample is never completion proof.
    """
    unknown = ServiceProcessCorrelation(ServiceProcessEvidence.UNKNOWN)
    if (scope not in ("--system", "--user")
            or not isinstance(service_name, str)
            or _SERVICE.fullmatch(service_name) is None
            or not isinstance(identity, ProcessIdentity)
            or type(identity.pid) is not int or identity.pid <= 0
            or type(identity.start_ticks) is not int or identity.start_ticks <= 0
            or not callable(scanner) or not callable(membership_probe)):
        return unknown
    index = 0 if scope == "--system" else 1
    try:
        first_pair = scanner()
        if not isinstance(first_pair, tuple) or len(first_pair) != 2:
            return unknown
        before = first_pair[index]
        prior = _service_row(before, service_name) if before is not None else None
        if prior is None:
            return unknown
        group, _, invocation = prior
        # Do not probe when identity or cgroup evidence is already missing.
        if not group or not invocation:
            return unknown
        member = membership_probe(identity, group)
        if not isinstance(member, CgroupMembership) or member.match is None:
            return unknown
        last_pair = scanner()
        if not isinstance(last_pair, tuple) or len(last_pair) != 2:
            return unknown
        after = last_pair[index]
        return correlate_service_process(service_name, before, after, member)
    except (OSError, ValueError, TypeError, RuntimeError):
        return unknown

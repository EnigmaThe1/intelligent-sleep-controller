"""Read-only existing *user* service workload observation.

Join two existing native probes: systemd-origin MainPID/invocation identity
and a bounded, double-sampled cgroup-v2 process subtree. This does not
modify/restart/enrol the service, read command lines, or authorize sleep.
Even stable processes cannot prove an agent's real task state or completion.
System services with other-UID MainPIDs are intentionally unsupported.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .cgroup_subtree import CgroupSubtreeSummary, observe_cgroup_subtree
from .native_service_enrollment import (
    NativeServiceProcess, discover_native_service_process,
)
from .service_process_correlation import _SERVICE

_MAX_PROCESSES = 4096


@dataclass(frozen=True)
class ExistingServiceObservation:
    """Safe public summary; no unit identity, PID, cgroup or arguments."""

    observed_processes: int | None
    reason: str
    verified_service_membership: bool = False
    task_activity_verified: bool = False
    workload_coverage_complete: bool = False


def observe_existing_user_service(
    unit: str,
    *,
    discover: Callable[..., NativeServiceProcess | None] = (
        discover_native_service_process
    ),
    scan: Callable[[str], CgroupSubtreeSummary] = observe_cgroup_subtree,
) -> ExistingServiceObservation:
    """Bracket a service-cgroup census with native invocation identity reads.

    All failures and races remain UNKNOWN, never 'inactive' or 'finished'.
    These are point-in-time observations for operator diagnostics, *not*
    permissions for automatic suspend. Do not call from ON dispatch.
    """
    unknown = ExistingServiceObservation(None, "service_workload_unverified")
    if (
        type(unit) is not str
        or _SERVICE.fullmatch(unit) is None
        or not callable(discover)
        or not callable(scan)
    ):
        return ExistingServiceObservation(None, "invalid_or_unsupported_service")

    try:
        first = discover(unit, scope="--user")
        if (
            not isinstance(first, NativeServiceProcess)
            or first.service != unit
            or first.scope != "--user"
            or first.coverage_verified is not False
            or first.task_owner_authenticated is not False
            or type(first.identity.pid) is not int
            or first.identity.pid <= 0
            or type(first.identity.start_ticks) is not int
            or first.identity.start_ticks <= 0
            or not isinstance(first.cgroup, str)
            or not first.cgroup
            or not isinstance(first.invocation, str)
            or not first.invocation
        ):
            return unknown

        observed = scan(first.cgroup)
        if (
            not isinstance(observed, CgroupSubtreeSummary)
            or observed.unknown is not False
            or observed.coverage_verified is not False
            or observed.task_owner_authenticated is not False
            or type(observed.observed_processes) is not int
            or not 1 <= observed.observed_processes <= _MAX_PROCESSES
        ):
            return unknown

        last = discover(unit, scope="--user")
        if (
            not isinstance(last, NativeServiceProcess)
            or last != first
        ):
            return ExistingServiceObservation(None, "service_changed_during_probe")
        return ExistingServiceObservation(
            observed_processes=observed.observed_processes,
            reason="service_process_membership_observed_task_state_unknown",
            verified_service_membership=True,
        )
    except (OSError, ValueError, RuntimeError, TypeError, KeyError):
        return unknown

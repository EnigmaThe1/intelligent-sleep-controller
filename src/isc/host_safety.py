"""Shared read-only host safety snapshot for ISC 1.0.

Builds the same conservative policy evidence for 'isc check' and the
(disarmed) automatic-suspend executor. No process identities or command
arguments are included in the result. Incomplete source coverage is
*always* incomplete; this is not a trusted lifecycle or wake verifier.
"""
from __future__ import annotations

from .gnome import read_gnome_idle_ms
from .gnome_inhibitors import GnomeInhibitFlags, read_gnome_inhibit_flags
from .inhibitors import Inhibitor, read_inhibitors
from .policy import Snapshot, WorkItem, WorkState
from .processes import ProcessScan, read_process_candidates
from .session_census import SessionCensus, read_session_census
from .scheduled_work import read_scheduled_work
from .scheduled_safety import classify_scheduled_work


def compose_host_snapshot(
    *, idle_ms: object, inhibitors: object, gnome_flags: object,
    processes: object, sessions: object = None, scheduled: object = None,
) -> Snapshot:
    """Combine evidence, without mistaking partial observation for safety."""
    blockers: list[str] = []
    if inhibitors is None or not isinstance(inhibitors, tuple) or any(
        not isinstance(item, Inhibitor) for item in inhibitors
    ):
        blockers.append("logind_inhibitors_unverified")
    else:
        blockers.extend(
            f"logind_blocker_{i}" for i, lock in enumerate(inhibitors)
            if lock.blocks_automatic_sleep
        )

    flags = gnome_flags if isinstance(gnome_flags, GnomeInhibitFlags) else None
    if flags is None or flags.suspend_inhibited is not False:
        blockers.append("gnome_suspend_unverified_or_inhibited")
    if flags is None or flags.idle_inhibited is not False:
        blockers.append("gnome_idle_unverified_or_inhibited")

    # GNOME flags cover one session, and logind session counts are diagnostic.
    # No all-session desktop or independently scheduled-work authority exists.
    if not isinstance(sessions, SessionCensus):
        blockers.append("login_sessions_unverified")

    jobs: list[WorkItem] = []
    if not isinstance(processes, ProcessScan):
        jobs.append(WorkItem("process_inventory_unverified", WorkState.UNKNOWN))
    else:
        jobs.extend(
            WorkItem(f"process_candidate_{i}", WorkState.UNKNOWN)
            for i, _ in enumerate(processes.candidates, start=1)
        )
        if processes.undecodable_names:
            jobs.append(WorkItem("process_names_unverified", WorkState.UNKNOWN))

    # Reuse the existing systemd census. A running service might be a daemon
    # or important unattended work; waiting timers require a wake policy.
    # Neither source can authenticate the task owner or prove all jobs known.
    jobs.extend(classify_scheduled_work(scheduled).work_items)

    idle_seconds = (
        idle_ms / 1000
        if type(idle_ms) is int and idle_ms >= 0 else None
    )
    return Snapshot(
        user_idle_seconds=idle_seconds,
        threshold_seconds=1800,
        coverage_complete=False,
        scheduled_work_safe=False,
        active_sleep_inhibitors=tuple(blockers),
        work_items=tuple(jobs),
    )


def read_host_snapshot() -> Snapshot:
    """One live local read with safe defaults; no power action or persisted data."""
    try:
        return compose_host_snapshot(
            idle_ms=read_gnome_idle_ms(),
            inhibitors=read_inhibitors(),
            gnome_flags=read_gnome_inhibit_flags(),
            processes=read_process_candidates(),
            sessions=read_session_census(),
            scheduled=read_scheduled_work(),
        )
    except (OSError, ValueError, TypeError, RuntimeError):
        return Snapshot()

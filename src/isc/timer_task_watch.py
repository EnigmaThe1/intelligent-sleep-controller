"""Conservative two-sample timer-linked task observations.

Compare aggregated native systemd task counters; no persistent history,
unit names, task identities, paths, or sleep eligibility decisions.
Equal aggregate snapshots cannot establish stable service identity,
completion, coverage, or safe suspend.
"""
from __future__ import annotations

from dataclasses import dataclass

from .timer_service_tasks import TimerServiceTaskCounts


@dataclass(frozen=True)
class TaskCountChange:
    first_services: int
    second_services: int
    first_with_tasks: int
    second_with_tasks: int
    first_unknown: int
    second_unknown: int
    aggregates_changed: bool
    coverage_verified: bool = False


def compare_task_counts(
    before: TimerServiceTaskCounts | None,
    after: TimerServiceTaskCounts | None,
) -> TaskCountChange | None:
    """Unknown snapshot remains UNKNOWN; no transitions imply completion."""
    if before is None or after is None:
        return None
    # Do not accept a future or forged coverage claim as authoritative.
    before_counts = (
        before.timers_inspected, before.distinct_services,
        before.cgroups_reported, before.tasks_present,
        before.zero_tasks_reported, before.unknown_task_counts,
    )
    after_counts = (
        after.timers_inspected, after.distinct_services,
        after.cgroups_reported, after.tasks_present,
        after.zero_tasks_reported, after.unknown_task_counts,
    )
    if any(type(n) is not int or n < 0 for n in before_counts + after_counts):
        return None
    if any(
        sample.cgroups_reported > sample.distinct_services
        or sample.tasks_present + sample.zero_tasks_reported
        + sample.unknown_task_counts != sample.distinct_services
        for sample in (before, after)
    ):
        return None
    return TaskCountChange(
        first_services=before.distinct_services,
        second_services=after.distinct_services,
        first_with_tasks=before.tasks_present,
        second_with_tasks=after.tasks_present,
        first_unknown=before.unknown_task_counts,
        second_unknown=after.unknown_task_counts,
        aggregates_changed=before_counts != after_counts,
    )

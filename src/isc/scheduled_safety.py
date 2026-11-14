"""Fail-closed interpretation of the existing systemd service/timer census.

This module does not discover tasks and never establishes complete workload
coverage. Running services are unclassified, not proof of active AI work;
waiting timers are wake-policy unknown, not work currently executing.
Only anonymised, aggregate conclusions are added to ISC's safety snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass

from .policy import WorkItem, WorkState
from .scheduled_work import ScheduledWorkCensus, UnitCounts


@dataclass(frozen=True)
class ScheduledSafety:
    work_items: tuple[WorkItem, ...]
    inventory_valid: bool
    waiting_timers: int | None
    running_services: int | None
    transitioning_services: int | None
    coverage_verified: bool = False
    wake_policy_verified: bool = False


def _valid_counts(value: object, *, kind: str) -> bool:
    if not isinstance(value, UnitCounts):
        return False
    fields = (
        value.listed, value.waiting_timers,
        value.running_services, value.activating_services,
    )
    if any(type(number) is not int or not 0 <= number <= 4096
           for number in fields):
        return False
    if kind == "timer":
        return (
            value.waiting_timers <= value.listed
            and value.running_services == 0
            and value.activating_services == 0
        )
    return (
        value.running_services <= value.listed
        and value.activating_services <= value.listed
        and value.waiting_timers == 0
        and value.running_services + value.activating_services <= value.listed
    )


def classify_scheduled_work(source: object) -> ScheduledSafety:
    """Account for positive and unknown systemd evidence without trusting it.

    The current readers observe only loaded systemd units. They cannot
    establish coverage of cron, containers, detached workers, future timers
    or application-native schedulers. Neither a zero count nor the dataclass
    flags is permission to sleep.
    """
    unknown = ScheduledSafety(
        (WorkItem("scheduler_inventory_unverified", WorkState.UNKNOWN),),
        False, None, None, None,
    )
    if not isinstance(source, ScheduledWorkCensus):
        return unknown
    rows = (
        (source.system_timers, "timer"),
        (source.user_timers, "timer"),
        (source.system_services, "service"),
        (source.user_services, "service"),
    )
    if not all(_valid_counts(value, kind=kind) for value, kind in rows):
        return unknown
    system_timer, user_timer, system_service, user_service = (
        value for value, _ in rows
    )
    waiting = system_timer.waiting_timers + user_timer.waiting_timers
    running = system_service.running_services + user_service.running_services
    transitioning = (
        system_service.activating_services + user_service.activating_services
    )
    items: list[WorkItem] = []
    if waiting:
        items.append(WorkItem("timer_wake_policy_unverified", WorkState.UNKNOWN))
    if user_service.running_services:
        items.append(WorkItem("running_user_services_unclassified", WorkState.UNKNOWN))
    if system_service.running_services:
        items.append(WorkItem("running_system_services_unclassified", WorkState.UNKNOWN))
    if transitioning:
        items.append(WorkItem("transitioning_services_unclassified", WorkState.UNKNOWN))
    # A successful systemd list-units response is never an exhaustive
    # scheduler census, including when its counts happen to be zero.
    items.append(WorkItem("other_schedulers_unverified", WorkState.UNKNOWN))
    return ScheduledSafety(tuple(items), True, waiting, running, transitioning)

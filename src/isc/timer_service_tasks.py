"""Read-only systemd cgroup task-count indicators for timer-linked services.

TasksCurrent is a point-in-time native systemd statistic, not evidence of
work completion or permission to suspend. Unit names, cgroup paths, PIDs and
other sensitive metadata are only used transiently within this observer.
"""
from __future__ import annotations

from dataclasses import dataclass

from .native_observers import _capture
from .scheduled_work import (
    _UNIT, _MAX_TIMER_ASSOCIATIONS, _association_query, _timer_names, _unit_query,
)
from .timer_service_states import _properties

_UNKNOWN_TASK_COUNT = frozenset(("[not set]", "18446744073709551615"))


@dataclass(frozen=True)
class TimerServiceTaskCounts:
    timers_inspected: int
    distinct_services: int
    cgroups_reported: int
    tasks_present: int
    zero_tasks_reported: int
    unknown_task_counts: int
    coverage_verified: bool = False


def _task_query(scope: str, names: tuple[str, ...]) -> tuple[str, ...]:
    if (scope not in ("--system", "--user") or not names
            or len(names) > _MAX_TIMER_ASSOCIATIONS
            or len(set(names)) != len(names)
            or any(_UNIT.fullmatch(n) is None or not n.endswith(".service")
                   for n in names)):
        raise ValueError("invalid_service_task_query")
    return ("systemctl", scope, "--no-pager", "show",
            "--property=Id,ControlGroup,TasksCurrent", "--", *names)


def _task_value(raw: str) -> int | None:
    if raw in _UNKNOWN_TASK_COUNT:
        return None
    if (not raw or len(raw) > 20 or not raw.isascii()
            or not raw.isdecimal()):
        raise ValueError("invalid_task_count")
    value = int(raw)
    if value > 18446744073709551615:
        raise ValueError("invalid_task_count")
    return value


def _valid_cgroup(path: str) -> bool:
    return (
        path == "" or
        (len(path) <= 4096 and path.startswith("/")
         and "\n" not in path and "\r" not in path
         and "\x00" not in path
         and all(ord(ch) >= 32 and ord(ch) != 127 for ch in path))
    )


def parse_timer_service_tasks(
    association_raw: str | None, task_raw: str | None,
    timer_names: tuple[str, ...],
) -> TimerServiceTaskCounts | None:
    """Return only counts; zero or unknown tasks never establishes idle."""
    associations = _properties(association_raw, timer_names,
                               frozenset(("Id", "Unit")))
    if associations is None:
        return None
    targets: set[str] = set()
    for row in associations.values():
        target = row["Unit"]
        match = _UNIT.fullmatch(target)
        if match is None or match.group(1) != "service":
            return None
        targets.add(target)
    services = _properties(task_raw, tuple(sorted(targets)),
                           frozenset(("Id", "ControlGroup", "TasksCurrent")))
    if services is None:
        return None
    cgroups = present = zero = unknown = 0
    for row in services.values():
        if not _valid_cgroup(row["ControlGroup"]):
            return None
        cgroups += int(bool(row["ControlGroup"]))
        try:
            value = _task_value(row["TasksCurrent"])
        except ValueError:
            return None
        if value is None:
            unknown += 1
        elif value > 0:
            present += 1
        else:
            zero += 1
    return TimerServiceTaskCounts(
        len(timer_names), len(targets), cgroups, present, zero, unknown,
    )


def read_timer_service_tasks(
) -> tuple[TimerServiceTaskCounts | None, TimerServiceTaskCounts | None]:
    """Query only native systemd metadata; no filesystem or service changes."""
    results: list[TimerServiceTaskCounts | None] = []
    for scope in ("--system", "--user"):
        timers = _timer_names(_capture(_unit_query(scope, "timer")))
        if timers is None:
            results.append(None)
            continue
        if not timers:
            results.append(TimerServiceTaskCounts(0, 0, 0, 0, 0, 0))
            continue
        associations_raw = _capture(_association_query(scope, timers))
        associations = _properties(
            associations_raw, timers, frozenset(("Id", "Unit")),
        )
        if associations is None:
            results.append(None)
            continue
        targets = tuple(sorted(set(row["Unit"] for row in associations.values())))
        if any(_UNIT.fullmatch(n) is None or not n.endswith(".service")
               for n in targets):
            results.append(None)
            continue
        task_raw = _capture(_task_query(scope, targets))
        results.append(parse_timer_service_tasks(
            associations_raw, task_raw, timers,
        ))
    return results[0], results[1]

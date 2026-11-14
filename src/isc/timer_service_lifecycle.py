"""Two-sample identity-aware timer-linked service observation (MONITOR only).

Service identities live only inside one foreground observation and are never
printed, persisted or returned. Neither disappearing units nor TasksCurrent=0
proves that work has finished, including detached jobs and other schedulers.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .native_observers import _capture
from .scheduled_work import _UNIT, _association_query, _timer_names, _unit_query
from .timer_service_states import _properties
from .timer_service_tasks import _task_query, _task_value, _valid_cgroup

_INVOCATION = re.compile(r"[0-9a-fA-F]{32}\Z")


@dataclass(frozen=True)
class ServiceTaskSnapshot:
    """Transient private per-service observations; do not log or serialise."""
    observed: tuple[tuple[str, str, int | None], ...]
    coverage_verified: bool = False
    # Optional for legacy synthetic comparisons; live reads always capture it.
    invocations: tuple[tuple[str, str | None], ...] | None = None


@dataclass(frozen=True)
class ServiceTaskTransitions:
    first_services: int
    second_services: int
    appeared: int
    disappeared: int
    stayed_with_tasks: int
    gained_tasks: int
    lost_reported_tasks: int
    unknown_transitions: int
    unchanged_without_tasks: int
    coverage_verified: bool = False
    changed_invocations: int = 0


def _read_scope(scope: str) -> ServiceTaskSnapshot | None:
    timers = _timer_names(_capture(_unit_query(scope, "timer")))
    if timers is None:
        return None
    if not timers:
        return ServiceTaskSnapshot(())
    raw = _capture(_association_query(scope, timers))
    links = _properties(raw, timers, frozenset(("Id", "Unit")))
    if links is None:
        return None
    targets = tuple(sorted(set(row["Unit"] for row in links.values())))
    if any(_UNIT.fullmatch(t) is None or not t.endswith(".service")
           for t in targets):
        return None
    # Capture cgroup, task count and activation identity in ONE native query
    # so separate query timings cannot misattribute a restarted service.
    query = list(_task_query(scope, targets))
    query[4] = "--property=Id,ControlGroup,TasksCurrent,InvocationID"
    rows = _properties(
        _capture(tuple(query)), targets,
        frozenset(("Id", "ControlGroup", "TasksCurrent", "InvocationID")),
    )
    if rows is None:
        return None
    observed: list[tuple[str, str, int | None]] = []
    invocations: list[tuple[str, str | None]] = []
    for name in targets:
        row = rows[name]
        if not _valid_cgroup(row["ControlGroup"]):
            return None
        try:
            count = _task_value(row["TasksCurrent"])
        except ValueError:
            return None
        value = row["InvocationID"]
        if value not in ("", "[not set]") and _INVOCATION.fullmatch(value) is None:
            return None
        observed.append((name, row["ControlGroup"], count))
        invocations.append((name, value.lower() if value not in ("", "[not set]") else None))
    return ServiceTaskSnapshot(tuple(observed), invocations=tuple(invocations))


def read_service_task_snapshots(
) -> tuple[ServiceTaskSnapshot | None, ServiceTaskSnapshot | None]:
    """Query systemd only. Each scope fails independently and conservatively."""
    return _read_scope("--system"), _read_scope("--user")


def compare_service_task_snapshots(
    before: ServiceTaskSnapshot | None,
    after: ServiceTaskSnapshot | None,
    *, require_invocation: bool = False,
) -> ServiceTaskTransitions | None:
    if before is None or after is None:
        return None
    first = {name: (group, tasks) for name, group, tasks in before.observed}
    second = {name: (group, tasks) for name, group, tasks in after.observed}
    if len(first) != len(before.observed) or len(second) != len(after.observed):
        return None
    if any(
        _UNIT.fullmatch(name) is None or not name.endswith(".service")
        or not _valid_cgroup(group)
        or (count is not None and (type(count) is not int or count < 0))
        for snapshot in (before, after)
        for name, group, count in snapshot.observed
    ):
        return None
    if require_invocation:
        if before.invocations is None or after.invocations is None:
            return None
        ids_a = dict(before.invocations)
        ids_b = dict(after.invocations)
        if (len(ids_a) != len(before.invocations)
                or len(ids_b) != len(after.invocations)
                or ids_a.keys() != first.keys()
                or ids_b.keys() != second.keys()
                or any(value is not None and (
                    not isinstance(value, str) or _INVOCATION.fullmatch(value) is None
                ) for value in (*ids_a.values(), *ids_b.values()))):
            return None
    else:
        ids_a = ids_b = {}
    gained = lost = continued = unknown = unchanged_zero = restarted = 0
    for name in first.keys() & second.keys():
        group_before, count_before = first[name]
        group_after, count_after = second[name]
        # Cgroup reassignments mean service identity continuity is unverified.
        # Also do not claim a transition where either count is unavailable.
        if (require_invocation and ids_a[name] is not None
                and ids_b[name] is not None
                and ids_a[name].lower() != ids_b[name].lower()):
            restarted += 1
        elif (not group_before or not group_after
                or group_before != group_after
                or count_before is None or count_after is None
                or (require_invocation and (
                    ids_a[name] is None or ids_b[name] is None))):
            unknown += 1
        elif count_before > 0 and count_after > 0:
            continued += 1
        elif count_before == 0 and count_after > 0:
            gained += 1
        elif count_before > 0 and count_after == 0:
            lost += 1
        else:
            unchanged_zero += 1
    return ServiceTaskTransitions(
        first_services=len(first),
        second_services=len(second),
        appeared=len(second.keys() - first.keys()),
        disappeared=len(first.keys() - second.keys()),
        stayed_with_tasks=continued,
        gained_tasks=gained,
        lost_reported_tasks=lost,
        unknown_transitions=unknown,
        unchanged_without_tasks=unchanged_zero,
        changed_invocations=restarted,
    )

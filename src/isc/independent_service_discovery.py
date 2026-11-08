"""Read-only discovery of active systemd services outside timer associations.

This supplements the timer-linked monitor. It is not an exhaustive workload
inventory: transient/unloaded units, cron, containers and application-native
schedulers may be missed. All per-unit names remain within a single scan;
public results expose aggregate counts only. No service is started, enrolled,
authenticated, or marked complete.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .native_observers import _capture
from .scheduled_work import (
    _ACTIVE, _LOAD, _MARKERS, _UNIT, _association_query,
    _timer_names, _unit_query,
)
from .timer_service_states import _properties

_MAX_ROWS = 4096
_MAX_BYTES = 200_000


@dataclass(frozen=True)
class IndependentServiceCounts:
    active_services: int | None
    timer_linked_active: int | None
    other_active: int | None
    uncertain: bool = True
    coverage_verified: bool = False
    task_owner_authenticated: bool = False


def _active_service_names(raw: str | None) -> frozenset[str] | None:
    """Validate full systemctl list-units snapshot before counting."""
    if not isinstance(raw, str) or len(raw) > _MAX_BYTES:
        return None
    rows = raw.strip().splitlines()
    if len(rows) > _MAX_ROWS:
        return None
    active: set[str] = set()
    seen: set[str] = set()
    for line in rows:
        fields = line.split()
        if not fields:
            continue
        if fields[0] in _MARKERS:
            fields = fields[1:]
        if len(fields) < 4:
            return None
        name, load, state, sub = fields[:4]
        if (_UNIT.fullmatch(name) is None or not name.endswith(".service")
                or load not in _LOAD or state not in _ACTIVE
                or name in seen or len(sub) > 32 or not sub.isascii()
                or not sub.replace("-", "").isalnum()):
            return None
        seen.add(name)
        # Active/activating/reloading/deactivating are all potentially busy.
        if state in ("active", "activating", "reloading", "deactivating",
                     "refreshing", "maintenance"):
            active.add(name)
    return frozenset(active)


def correlate_independent_services(
    service_raw: str | None,
    timer_raw: str | None,
    associations_raw: str | None,
) -> IndependentServiceCounts:
    """Counts are bounded, never a complete scheduler or idle assertion."""
    unknown = IndependentServiceCounts(None, None, None)
    active = _active_service_names(service_raw)
    timers = _timer_names(timer_raw)
    if active is None or timers is None:
        return unknown
    if timers:
        assoc = _properties(
            associations_raw, timers, frozenset(("Id", "Unit")),
        )
        if assoc is None:
            return unknown
        linked: set[str] = set()
        for row in assoc.values():
            unit = row["Unit"]
            if _UNIT.fullmatch(unit) is None or not unit.endswith(".service"):
                return unknown
            linked.add(unit)
    else:
        if associations_raw not in ("", None):
            return unknown
        linked = set()
    return IndependentServiceCounts(
        active_services=len(active),
        timer_linked_active=len(active & linked),
        other_active=len(active - linked),
        uncertain=False,
    )


def read_independent_services(
    *, scope: str,
    capture: Callable[[tuple[str, ...]], str | None] = _capture,
) -> IndependentServiceCounts:
    """Observe one systemd scope; never change or enroll services."""
    unknown = IndependentServiceCounts(None, None, None)
    if scope not in ("--user", "--system") or not callable(capture):
        return unknown
    try:
        services = capture(_unit_query(scope, "service"))
        timers = capture(_unit_query(scope, "timer"))
        names = _timer_names(timers)
        if names is None:
            return unknown
        association = capture(_association_query(scope, names)) if names else None
        return correlate_independent_services(services, timers, association)
    except (OSError, ValueError, TypeError, RuntimeError):
        return unknown

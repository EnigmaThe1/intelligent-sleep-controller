"""Bounded read-only systemd PID indicators for timer-linked services.

These PIDs are systemd metadata, not authoritative task accounting. A zero
MainPID/ControlPID never proves that a service, cgroup or external job
is finished. No PID, unit name, command or private data is returned.
"""
from __future__ import annotations

from dataclasses import dataclass

from .native_observers import _capture
from .scheduled_work import (
    _UNIT, _association_query, _timer_names, _unit_query,
)
from .timer_service_states import _properties
from .scheduled_work import _MAX_TIMER_ASSOCIATIONS


@dataclass(frozen=True)
class TimerServiceProcessIndicators:
    timers_inspected: int
    distinct_services: int
    main_pid_nonzero: int
    control_pid_nonzero: int
    neither_pid_reported: int
    coverage_verified: bool = False


def _pid_query(scope: str, names: tuple[str, ...]) -> tuple[str, ...]:
    if (scope not in ("--system", "--user") or not names
            or len(names) > _MAX_TIMER_ASSOCIATIONS
            or len(set(names)) != len(names)
            or any(_UNIT.fullmatch(n) is None or not n.endswith(".service")
                   for n in names)):
        raise ValueError("invalid_service_pid_query")
    return ("systemctl", scope, "--no-pager", "show",
            "--property=Id,MainPID,ControlPID", "--", *names)


def _pid_value(raw: str) -> int | None:
    if not raw or len(raw) > 20 or not raw.isascii() or not raw.isdecimal():
        return None
    pid = int(raw)
    return pid if 0 <= pid <= 4194304 else None


def parse_process_indicators(
    association_raw: str | None,
    pid_raw: str | None,
    timer_names: tuple[str, ...],
) -> TimerServiceProcessIndicators | None:
    associations = _properties(association_raw, timer_names, frozenset(("Id", "Unit")))
    if associations is None:
        return None
    targets: set[str] = set()
    for item in associations.values():
        target = item["Unit"]
        matched = _UNIT.fullmatch(target)
        if matched is None or matched.group(1) != "service":
            return None
        targets.add(target)
    services = _properties(pid_raw, tuple(sorted(targets)),
                           frozenset(("Id", "MainPID", "ControlPID")))
    if services is None:
        return None
    main = control = neither = 0
    for item in services.values():
        m, p = _pid_value(item["MainPID"]), _pid_value(item["ControlPID"])
        if m is None or p is None:
            return None
        main += int(m > 0)
        control += int(p > 0)
        neither += int(m == 0 and p == 0)
    return TimerServiceProcessIndicators(
        len(timer_names), len(targets), main, control, neither,
    )


def read_timer_service_process_indicators(
) -> tuple[TimerServiceProcessIndicators | None, TimerServiceProcessIndicators | None]:
    """Inspect limited native metadata without starting/stopping any unit."""
    results: list[TimerServiceProcessIndicators | None] = []
    for scope in ("--system", "--user"):
        timers = _timer_names(_capture(_unit_query(scope, "timer")))
        if timers is None:
            results.append(None)
            continue
        if not timers:
            results.append(TimerServiceProcessIndicators(0, 0, 0, 0, 0))
            continue
        raw = _capture(_association_query(scope, timers))
        associations = _properties(raw, timers, frozenset(("Id", "Unit")))
        if associations is None:
            results.append(None)
            continue
        targets = tuple(sorted(set(v["Unit"] for v in associations.values())))
        if any(_UNIT.fullmatch(n) is None or not n.endswith(".service")
               for n in targets):
            results.append(None)
            continue
        pid_raw = _capture(_pid_query(scope, targets))
        results.append(parse_process_indicators(raw, pid_raw, timers))
    return results[0], results[1]

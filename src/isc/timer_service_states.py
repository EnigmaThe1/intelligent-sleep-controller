"""Read-only, non-authoritative current state of timer-linked services.

Read systemd's effective Unit= association, never guess from timer names.
No unit names leave the observer. One-shot service exit, inactive state,
or a lack of visible processes NEVER establishes task completion.
"""
from __future__ import annotations

from dataclasses import dataclass

from .native_observers import _capture
from .scheduled_work import (
    _UNIT, _MAX_TIMER_ASSOCIATIONS, _association_query, _timer_names,
    _unit_query,
)


@dataclass(frozen=True)
class TimerServiceStates:
    timers_inspected: int
    distinct_services: int
    active: int
    transitioning: int
    inactive: int
    failed: int
    coverage_verified: bool = False


def _properties(raw: str | None, expected: tuple[str, ...],
                keys: frozenset[str]) -> dict[str, dict[str, str]] | None:
    """Reject partial/duplicate/malformed systemctl show property blocks."""
    if raw is None or not isinstance(raw, str) or len(raw) > 200_000:
        return None
    if len(expected) > _MAX_TIMER_ASSOCIATIONS or len(set(expected)) != len(expected):
        return None
    if not expected:
        return {} if not raw.strip() else None
    blocks = raw.strip().split("\n\n")
    if len(blocks) != len(expected):
        return None
    found: dict[str, dict[str, str]] = {}
    for block in blocks:
        props: dict[str, str] = {}
        for line in block.splitlines():
            if line.count("=") < 1:
                return None
            key, value = line.split("=", 1)
            if key not in keys or key in props:
                return None
            props[key] = value
        identity = props.get("Id")
        if (identity not in expected or identity in found
                or set(props) != keys):
            return None
        found[identity] = props
    return found if len(found) == len(expected) else None


def _service_query(scope: str, names: tuple[str, ...]) -> tuple[str, ...]:
    if scope not in ("--system", "--user"):
        raise ValueError("invalid_scope")
    if (not names or len(names) > _MAX_TIMER_ASSOCIATIONS
            or len(set(names)) != len(names)
            or any(_UNIT.fullmatch(n) is None or not n.endswith(".service")
                   for n in names)):
        raise ValueError("invalid_service_names")
    return ("systemctl", scope, "--no-pager", "show",
            "--property=Id,ActiveState", "--", *names)


def parse_linked_services(
    association_raw: str | None, state_raw: str | None,
    timer_names: tuple[str, ...],
) -> TimerServiceStates | None:
    """Aggregate snapshots; never interpret inactive as completed/safe."""
    timers = _properties(association_raw, timer_names, frozenset(("Id", "Unit")))
    if timers is None:
        return None
    targets: set[str] = set()
    for item in timers.values():
        target = item["Unit"]
        matched = _UNIT.fullmatch(target)
        if matched is None or matched.group(1) != "service":
            return None
        targets.add(target)
    services = _properties(state_raw, tuple(sorted(targets)),
                           frozenset(("Id", "ActiveState")))
    if services is None:
        return None
    active = transitioning = inactive = failed = 0
    for service in services.values():
        state = service["ActiveState"]
        if state == "active":
            active += 1
        elif state in ("activating", "deactivating", "reloading",
                       "refreshing", "maintenance"):
            transitioning += 1
        elif state == "inactive":
            inactive += 1
        elif state == "failed":
            failed += 1
        else:
            return None
    return TimerServiceStates(len(timer_names), len(targets),
                              active, transitioning, inactive, failed)


def read_timer_service_states(
) -> tuple[TimerServiceStates | None, TimerServiceStates | None]:
    """Read only systemd metadata, without unit activation or persistence."""
    results: list[TimerServiceStates | None] = []
    for scope in ("--system", "--user"):
        names = _timer_names(_capture(_unit_query(scope, "timer")))
        if names is None:
            results.append(None)
            continue
        if not names:
            results.append(TimerServiceStates(0, 0, 0, 0, 0, 0))
            continue
        associations = _capture(_association_query(scope, names))
        metadata = _properties(associations, names, frozenset(("Id", "Unit")))
        if metadata is None:
            results.append(None)
            continue
        targets = tuple(sorted(set(item["Unit"] for item in metadata.values())))
        if (any((_UNIT.fullmatch(t) is None or not t.endswith(".service"))
                for t in targets)):
            results.append(None)
            continue
        states = _capture(_service_query(scope, targets))
        results.append(parse_linked_services(associations, states, names))
    return results[0], results[1]

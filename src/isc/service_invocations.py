"""Read-only native systemd service invocation identity observation.

A systemd InvocationID identifies an activation more precisely than a reused
ControlGroup string. Identity absence is UNKNOWN, never task completion.
Only aggregate counts may be returned; private unit names are ephemeral.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .native_observers import _capture
from .scheduled_work import _UNIT, _association_query, _timer_names, _unit_query
from .timer_service_states import _properties

_INVOCATION = re.compile(r"[0-9a-fA-F]{32}\Z")
_MAX_SERVICES = 256


@dataclass(frozen=True)
class InvocationSnapshot:
    """Transient service-name to activation-ID pairs, never exported in CLI."""
    observed: tuple[tuple[str, str | None], ...]
    coverage_verified: bool = False


@dataclass(frozen=True)
class InvocationComparison:
    retained: int
    changed: int
    unknown: int
    appeared: int
    disappeared: int
    coverage_verified: bool = False


def _query(scope: str, services: tuple[str, ...]) -> tuple[str, ...]:
    if (scope not in ("--system", "--user") or not services
            or len(services) > _MAX_SERVICES
            or len(set(services)) != len(services)
            or any(_UNIT.fullmatch(name) is None or
                   not name.endswith(".service") for name in services)):
        raise ValueError("invalid_invocation_query")
    return ("systemctl", scope, "--no-pager", "show",
            "--property=Id,InvocationID", "--", *services)


def _parse_id(value: str) -> str | None:
    if value in ("", "[not set]"):
        return None
    if _INVOCATION.fullmatch(value) is None:
        raise ValueError("invalid_invocation_id")
    return value.lower()


def _read_scope(scope: str) -> InvocationSnapshot | None:
    timers = _timer_names(_capture(_unit_query(scope, "timer")))
    if timers is None:
        return None
    if not timers:
        return InvocationSnapshot(())
    links = _properties(
        _capture(_association_query(scope, timers)),
        timers, frozenset(("Id", "Unit")),
    )
    if links is None:
        return None
    targets = tuple(sorted(set(row["Unit"] for row in links.values())))
    if (len(targets) > _MAX_SERVICES or any(
        _UNIT.fullmatch(t) is None or not t.endswith(".service")
        for t in targets
    )):
        return None
    if not targets:
        return InvocationSnapshot(())
    props = _properties(
        _capture(_query(scope, targets)), targets,
        frozenset(("Id", "InvocationID")),
    )
    if props is None:
        return None
    try:
        return InvocationSnapshot(tuple(
            (name, _parse_id(props[name]["InvocationID"]))
            for name in targets
        ))
    except (ValueError, KeyError):
        return None


def read_service_invocations(
) -> tuple[InvocationSnapshot | None, InvocationSnapshot | None]:
    """Read system and user timer-linked service invocation IDs."""
    return (_read_scope("--system"), _read_scope("--user"))


def compare_service_invocations(
    first: InvocationSnapshot | None, second: InvocationSnapshot | None,
) -> InvocationComparison | None:
    if first is None or second is None:
        return None
    a = dict(first.observed)
    b = dict(second.observed)
    if len(a) != len(first.observed) or len(b) != len(second.observed):
        return None
    if any(
        _UNIT.fullmatch(name) is None or not name.endswith(".service")
        or (value is not None and
            (not isinstance(value, str) or _INVOCATION.fullmatch(value) is None))
        for source in (first, second)
        for name, value in source.observed
    ):
        return None
    kept = changed = unknown = 0
    for name in a.keys() & b.keys():
        if a[name] is None or b[name] is None:
            unknown += 1
        elif a[name].lower() == b[name].lower():
            kept += 1
        else:
            changed += 1
    return InvocationComparison(
        retained=kept, changed=changed, unknown=unknown,
        appeared=len(b.keys() - a.keys()),
        disappeared=len(a.keys() - b.keys()),
    )

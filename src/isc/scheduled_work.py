"""Partial, privacy-preserving Linux timer and service state census.

Timer units describe scheduling; service units describe unit state. A
matching timer/service name is NOT evidence of a lifecycle association:
systemd timers may use Unit= overrides and services can launch child work.
No conclusions about complete scheduler coverage or sleep safety are made.

Only aggregate counts are returned. No unit names, commands, user identities
or scheduler files are retained by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .native_observers import _capture

_UNIT = re.compile(r"[A-Za-z0-9_.:@+\\-]{1,255}\.(timer|service)\Z", re.ASCII)
_MARKERS = frozenset(("●", "○", "×", "↻"))
_LOAD = frozenset(("loaded", "not-found", "error", "bad-setting", "masked"))
_ACTIVE = frozenset(("active", "inactive", "activating", "deactivating", "failed", "reloading", "maintenance", "refreshing"))


@dataclass(frozen=True)
class UnitCounts:
    listed: int
    waiting_timers: int = 0
    running_services: int = 0
    activating_services: int = 0
    coverage_verified: bool = False


@dataclass(frozen=True)
class ScheduledWorkCensus:
    system_timers: UnitCounts | None
    user_timers: UnitCounts | None
    system_services: UnitCounts | None
    user_services: UnitCounts | None
    coverage_verified: bool = False


def parse_unit_states(raw: str | None, *, kind: str) -> UnitCounts | None:
    """Parse bounded systemctl list-units rows; unknown structure -> UNKNOWN.

    The output is never used as a permission to sleep. Empty successful output
    is an observed zero, but still cannot prove that no scheduled work exists.
    """
    if raw is None or not isinstance(raw, str) or kind not in ("timer", "service"):
        return None
    lines = raw.strip().splitlines()
    if len(lines) > 4096 or len(raw) > 200_000:
        return None
    listed = waiting = running = activating = 0
    for line in lines:
        fields = line.split()
        if not fields:
            continue
        if fields[0] in _MARKERS:
            fields = fields[1:]
        if len(fields) < 4:
            return None
        unit, load, active, sub = fields[:4]
        match = _UNIT.fullmatch(unit)
        if (
            match is None or match.group(1) != kind
            or load not in _LOAD or active not in _ACTIVE
            or len(sub) > 32 or not sub.isascii() or not sub.replace("-", "").isalnum()
        ):
            return None
        listed += 1
        if kind == "timer" and active == "active" and sub == "waiting":
            waiting += 1
        if kind == "service" and active == "active" and sub == "running":
            running += 1
        if kind == "service" and active == "activating":
            activating += 1
    return UnitCounts(
        listed=listed,
        waiting_timers=waiting,
        running_services=running,
        activating_services=activating,
    )


def _unit_query(scope: str, kind: str) -> tuple[str, ...]:
    if scope not in ("--system", "--user") or kind not in ("timer", "service"):
        raise ValueError("invalid_native_unit_query")
    return (
        "systemctl", scope, "--no-pager", "--no-legend", "--plain",
        "--all", f"--type={kind}", "list-units",
    )


def read_scheduled_work() -> ScheduledWorkCensus:
    """Read selected systemd unit states; no files written, no process control."""
    return ScheduledWorkCensus(
        system_timers=parse_unit_states(
            _capture(_unit_query("--system", "timer")), kind="timer",
        ),
        user_timers=parse_unit_states(
            _capture(_unit_query("--user", "timer")), kind="timer",
        ),
        system_services=parse_unit_states(
            _capture(_unit_query("--system", "service")), kind="service",
        ),
        user_services=parse_unit_states(
            _capture(_unit_query("--user", "service")), kind="service",
        ),
    )


# Installed unit files include definitions that may not currently be loaded.
# These aggregate counts deliberately do NOT map a timer to a running job.
_UNIT_FILE_STATES = frozenset((
    "enabled", "enabled-runtime", "disabled", "static", "indirect",
    "masked", "masked-runtime", "alias", "generated", "transient",
    "linked", "linked-runtime", "bad",
))


@dataclass(frozen=True)
class TimerFileCounts:
    listed: int
    enabled: int
    disabled: int
    other: int
    coverage_verified: bool = False


def parse_timer_unit_files(raw: str | None) -> TimerFileCounts | None:
    """Parse systemctl list-unit-files; preserve UNKNOWN on ambiguous output."""
    if raw is None or not isinstance(raw, str):
        return None
    if len(raw) > 200_000:
        return None
    lines = raw.strip().splitlines()
    if len(lines) > 4096:
        return None
    listed = enabled = disabled = other = 0
    seen: set[str] = set()
    for line in lines:
        if not line.strip():
            continue
        fields = line.split()
        # --no-legend rows: UNIT_FILE STATE [PRESET]; preset is optional.
        if len(fields) not in (2, 3):
            return None
        name, state = fields[:2]
        matched = _UNIT.fullmatch(name)
        if (matched is None or matched.group(1) != "timer"
                or name in seen or state not in _UNIT_FILE_STATES):
            return None
        if len(fields) == 3 and fields[2] not in (
            "enabled", "disabled", "ignored", "-", "static",
        ):
            return None
        seen.add(name)
        listed += 1
        if state in ("enabled", "enabled-runtime"):
            enabled += 1
        elif state == "disabled":
            disabled += 1
        else:
            other += 1
    return TimerFileCounts(listed, enabled, disabled, other)


def _timer_file_query(scope: str) -> tuple[str, ...]:
    if scope not in ("--system", "--user"):
        raise ValueError("invalid_timer_file_scope")
    return (
        "systemctl", scope, "--no-pager", "--no-legend", "--plain",
        "--type=timer", "list-unit-files",
    )


def read_timer_unit_files() -> tuple[TimerFileCounts | None, TimerFileCounts | None]:
    """Independent read-only system/user installed timer definitions census."""
    return (
        parse_timer_unit_files(_capture(_timer_file_query("--system"))),
        parse_timer_unit_files(_capture(_timer_file_query("--user"))),
    )


# The effective Unit= association comes from systemd, not matching names.
# Timer names exist only transiently while building the read-only query.
_MAX_TIMER_ASSOCIATIONS = 128


@dataclass(frozen=True)
class TimerAssociationCounts:
    timers_listed: int
    services_resolved: int
    unresolved: int
    coverage_verified: bool = False


def _timer_names(raw: str | None) -> tuple[str, ...] | None:
    """Read loaded timer identities only inside the query boundary."""
    if raw is None or not isinstance(raw, str) or len(raw) > 200_000:
        return None
    names: list[str] = []
    seen: set[str] = set()
    lines = raw.strip().splitlines()
    if len(lines) > 4096:
        return None
    for line in lines:
        fields = line.split()
        if not fields:
            continue
        if fields[0] in _MARKERS:
            fields = fields[1:]
        if (len(fields) < 4 or _UNIT.fullmatch(fields[0]) is None
                or not fields[0].endswith(".timer")
                or fields[1] not in _LOAD or fields[2] not in _ACTIVE
                or fields[0] in seen):
            return None
        seen.add(fields[0])
        names.append(fields[0])
        if len(names) > _MAX_TIMER_ASSOCIATIONS:
            return None
    return tuple(names)


def _association_query(scope: str, names: tuple[str, ...]) -> tuple[str, ...]:
    if scope not in ("--system", "--user"):
        raise ValueError("invalid_timer_scope")
    if (not names or len(names) > _MAX_TIMER_ASSOCIATIONS or
            len(set(names)) != len(names) or
            any(_UNIT.fullmatch(name) is None or
                not name.endswith(".timer") for name in names)):
        raise ValueError("invalid_timer_names")
    return ("systemctl", scope, "--no-pager", "show", "--property=Id,Unit",
            "--", *names)


def parse_timer_associations(
    raw: str | None, names: tuple[str, ...],
) -> TimerAssociationCounts | None:
    """Validate one systemd Id/Unit pair per timer; keep names out of results."""
    if raw is None or not isinstance(raw, str) or len(raw) > 200_000:
        return None
    if len(names) > _MAX_TIMER_ASSOCIATIONS or len(names) != len(set(names)):
        return None
    if not names:
        return TimerAssociationCounts(0, 0, 0) if not raw.strip() else None
    expected = set(names)
    observed: set[str] = set()
    resolved = 0
    # systemctl show emits one property block per queried unit.
    blocks = raw.strip().split("\n\n")
    if len(blocks) != len(names):
        return None
    for block in blocks:
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if "=" not in line:
                return None
            key, value = line.split("=", 1)
            if key not in ("Id", "Unit") or key in fields:
                return None
            fields[key] = value
        name, target = fields.get("Id"), fields.get("Unit")
        if name not in expected or name in observed or target is None:
            return None
        observed.add(name)
        if target:
            matched = _UNIT.fullmatch(target)
            if matched is None or matched.group(1) != "service":
                return None
            resolved += 1
    if observed != expected:
        return None
    return TimerAssociationCounts(len(names), resolved, len(names) - resolved)


def read_timer_associations(
) -> tuple[TimerAssociationCounts | None, TimerAssociationCounts | None]:
    """Inspect loaded timers' effective Unit= metadata without activating units."""
    results: list[TimerAssociationCounts | None] = []
    for scope in ("--system", "--user"):
        names = _timer_names(_capture(_unit_query(scope, "timer")))
        if names is None:
            results.append(None)
        elif not names:
            results.append(TimerAssociationCounts(0, 0, 0))
        else:
            results.append(parse_timer_associations(
                _capture(_association_query(scope, names)), names,
            ))
    return results[0], results[1]

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

_UNIT = re.compile(r"[A-Za-z0-9_.:@\\-]+\\.(timer|service)\\Z", re.ASCII)
_MARKERS = frozenset(("●", "○", "×", "↻"))
_LOAD = frozenset(("loaded", "not-found", "error", "bad-setting", "masked"))
_ACTIVE = frozenset(("active", "inactive", "activating", "deactivating", "failed", "reloading"))


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
        fields = line.split(maxsplit=4)
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

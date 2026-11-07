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

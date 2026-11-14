"""Privacy-minimal, read-only Linux login-session activity census.

This is a bounded snapshot of sessions known to logind, not a full list of
desktop idle states, all GNOME buses, or independently running workloads.
Usernames, session IDs, seats and TTYs are never returned or printed.
"""
from __future__ import annotations

from dataclasses import dataclass

from .native_observers import _SESSION, _capture


@dataclass(frozen=True)
class SessionCensus:
    listed: int
    active: int
    inactive: int
    unknown: int
    coverage_verified: bool = False


def _session_ids(raw: str | None) -> tuple[str, ...] | None:
    if raw is None or not isinstance(raw, str) or len(raw) > 200_000:
        return None
    lines = raw.strip().splitlines()
    if len(lines) > 64:
        return None
    ids: list[str] = []
    for line in lines:
        fields = line.split()
        if (len(fields) < 3 or _SESSION.fullmatch(fields[0]) is None
                or not fields[1].isascii() or not fields[1].isdecimal()
                or int(fields[1]) >= 2**32):
            return None
        ids.append(fields[0])
    return tuple(ids) if len(ids) == len(set(ids)) else None


def _parse_active(raw: str | None) -> bool | None:
    if raw is None or not isinstance(raw, str):
        return None
    response = raw.strip()
    if response == "yes":
        return True
    if response == "no":
        return False
    return None


def read_session_census() -> SessionCensus | None:
    """Observe up to 64 sessions with native logind metadata; no writes."""
    ids = _session_ids(_capture((
        "loginctl", "--no-legend", "--no-pager", "list-sessions",
    )))
    if ids is None:
        return None
    active = inactive = unknown = 0
    for sid in ids:
        value = _parse_active(_capture((
            "loginctl", "show-session", sid,
            "--property=Active", "--value",
        )))
        if value is True:
            active += 1
        elif value is False:
            inactive += 1
        else:
            unknown += 1
    return SessionCensus(len(ids), active, inactive, unknown)

"""Read-only GNOME session inhibit flags on the current session bus.

Uses GNOME's documented IsInhibited(u flags) -> b method. Flags 4 (suspend)
and 8 (idle) are checked separately. Does not enumerate application IDs,
reasons, paths or other session data. Missing or malformed results are UNKNOWN.
This is one session only, not whole-host coverage or permission to suspend.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import subprocess


_REPLY = re.compile(r"\(\s*(true|false)\s*,?\s*\)\s*\Z")
_BASE = (
    "gdbus", "call", "--session",
    "--dest", "org.gnome.SessionManager",
    "--object-path", "/org/gnome/SessionManager",
    "--method", "org.gnome.SessionManager.IsInhibited",
)


@dataclass(frozen=True)
class GnomeInhibitFlags:
    suspend_inhibited: bool | None
    idle_inhibited: bool | None
    coverage_verified: bool = False


def parse_inhibited(raw: str | None) -> bool | None:
    if raw is None or not isinstance(raw, str) or len(raw) > 64:
        return None
    match = _REPLY.fullmatch(raw.strip())
    return (match.group(1) == "true") if match else None


def _check(flag: int, timeout_seconds: float) -> bool | None:
    if flag not in (4, 8):
        raise ValueError("invalid_gnome_inhibit_flag")
    try:
        result = subprocess.run(
            (*_BASE, str(flag)), capture_output=True, text=True,
            check=False, timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None
    if result.returncode != 0:
        return None
    return parse_inhibited(result.stdout)


def read_gnome_inhibit_flags(*, timeout_seconds: float = 3.0) -> GnomeInhibitFlags:
    """Query both independently; unknown is never interpreted as uninhibited."""
    if not isinstance(timeout_seconds, (float, int)) or not 0 < timeout_seconds <= 10:
        raise ValueError("invalid_inhibit_timeout")
    return GnomeInhibitFlags(
        suspend_inhibited=_check(4, timeout_seconds),
        idle_inhibited=_check(8, timeout_seconds),
    )

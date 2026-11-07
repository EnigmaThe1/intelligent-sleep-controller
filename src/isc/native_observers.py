"""Read-only, privacy-minimal discovery of native Linux session/timer activity.

Used only for MONITOR diagnostics. The returned counts are NOT evidence
that all timers, processes or independent applications have been covered.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import subprocess

_UNIT = re.compile(r"[A-Za-z0-9_.:@\-]+\.timer\Z", re.ASCII)
_SESSION = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z", re.ASCII)


@dataclass(frozen=True)
class NativeActivity:
    sessions_observed: int | None
    loaded_system_timers: int | None
    loaded_user_timers: int | None
    coverage_verified: bool = False


def _capture(args: tuple[str, ...]) -> str | None:
    """OS commands with bounded output, no shell and minimum environment."""
    env = {
        name: os.environ[name]
        for name in (
            "PATH", "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
            "LANG", "LC_ALL", "TZ",
        )
        if name in os.environ
    }
    env["SYSTEMD_COLORS"] = "0"
    try:
        done = subprocess.run(
            args, env=env, capture_output=True, text=True,
            check=False, timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None
    if done.returncode != 0 or len(done.stdout) > 200_000:
        return None
    return done.stdout


def _count_sessions(raw: str | None) -> int | None:
    """Count session rows without retaining user names or seats."""
    if raw is None:
        return None
    lines = raw.strip().splitlines()
    if len(lines) > 4096:
        return None
    count = 0
    for line in lines:
        fields = line.split()
        # loginctl --no-legend produces SESSION UID USER SEAT ...;
        # UID alone is validated. No user names or addresses are retained.
        if len(fields) < 3 or not _SESSION.fullmatch(fields[0]):
            return None
        if not fields[1].isascii() or not fields[1].isdecimal():
            return None
        count += 1
    return count


def _count_loaded_timers(raw: str | None) -> int | None:
    """Count loaded timer units from systemctl; never infer completeness."""
    if raw is None:
        return None
    lines = raw.strip().splitlines()
    if len(lines) > 4096:
        return None
    count = 0
    for line in lines:
        fields = line.split()
        if not fields:
            continue
        if fields[0] in ("●", "○", "×", "↻"):
            fields = fields[1:]
        # systemctl list-units --type=timer: UNIT LOAD ACTIVE SUB ...
        if len(fields) < 4 or not _UNIT.fullmatch(fields[0]):
            return None
        if fields[1] not in ("loaded", "not-found", "error", "bad-setting", "masked"):
            return None
        count += 1
    return count


def read_native_activity() -> NativeActivity:
    """Read only OS evidence; every failure remains UNKNOWN, not zero."""
    sessions = _count_sessions(_capture(
        ("loginctl", "--no-legend", "--no-pager", "list-sessions"),
    ))
    system_timers = _count_loaded_timers(_capture(
        ("systemctl", "--system", "--no-pager", "--no-legend", "--plain",
         "--all", "--type=timer", "list-units"),
    ))
    user_timers = _count_loaded_timers(_capture(
        ("systemctl", "--user", "--no-pager", "--no-legend", "--plain",
         "--all", "--type=timer", "list-units"),
    ))
    return NativeActivity(sessions, system_timers, user_timers)

"""Read-only GNOME desktop idle probe via the session D-Bus.

A missing, timed-out, or malformed answer returns None (unknown). No write
operations, inhibitors, settings changes, or automatic suspend actions exist.
"""

from __future__ import annotations

import re
import subprocess

_IDLE_REPLY = re.compile(r"\(\s*uint64\s+([0-9]+)\s*,?\s*\)\s*\Z")
_GNOME_IDLE_COMMAND = (
    "gdbus",
    "call",
    "--session",
    "--dest",
    "org.gnome.Mutter.IdleMonitor",
    "--object-path",
    "/org/gnome/Mutter/IdleMonitor/Core",
    "--method",
    "org.gnome.Mutter.IdleMonitor.GetIdletime",
)


def parse_gnome_idletime(reply: str) -> int | None:
    """Parse GNOME's `(uint64 N,)` reply, returning milliseconds or None."""
    match = _IDLE_REPLY.fullmatch(reply.strip())
    if match is None:
        return None
    value = int(match.group(1))
    return value if value <= (2**64 - 1) else None


def read_gnome_idle_ms(*, timeout_seconds: float = 3.0) -> int | None:
    """Return idle milliseconds, or None if not available/verifiable.

    On headless systems, other desktops, a missing session bus, or GNOME
    failures, no idle time is invented. The policy treats None as unsafe.
    """
    try:
        result = subprocess.run(
            _GNOME_IDLE_COMMAND,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_gnome_idletime(result.stdout)

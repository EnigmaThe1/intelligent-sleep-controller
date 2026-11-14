"""Read-only, privacy-minimal systemd user-bus sender-owner correlation.

Step 99f checks a unique D-Bus sender against bracketed snapshots of the
well-known systemd manager name owner. This is NOT work-owner authentication,
signal-stream completeness, or suspend authorization. No signals or payload
are collected by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import subprocess
from typing import Callable

_OWNER = re.compile(r":[0-9]+\.[0-9]+\Z", re.ASCII)
_QUERY = (
    "busctl", "--user", "call",
    "org.freedesktop.DBus", "/org/freedesktop/DBus",
    "org.freedesktop.DBus", "GetNameOwner", "s",
    "org.freedesktop.systemd1",
)
_ENV_KEYS = ("PATH", "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
             "LANG", "LC_ALL")


@dataclass(frozen=True)
class SenderCorrelation:
    status: str
    manager_sender_correlated: bool = False
    event_delivery_verified: bool = False
    task_owner_authenticated: bool = False
    coverage_verified: bool = False


def validate_manager_sender(
    candidate: object, before: object, after: object,
) -> SenderCorrelation:
    """Compare exact unique names; never output or retain an identifier.

    Even a matched name before and after a candidate does not exclude
    unobserved owner changes or validate the event's actual provenance.
    This is local point-in-time correlation only.
    """
    unknown = SenderCorrelation("UNKNOWN")
    if any(type(name) is not str or _OWNER.fullmatch(name) is None
           for name in (candidate, before, after)):
        return unknown
    if before != after:
        return SenderCorrelation("OWNER_CHANGED_UNVERIFIED")
    if candidate != before:
        return SenderCorrelation("SENDER_MISMATCH")
    return SenderCorrelation("SENDER_MATCH_UNVERIFIED", True)


def read_user_manager_unique_owner(
    *, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    """Read only the GetNameOwner string, using a fixed bounded command.

    The unique identifier stays private and should not be logged. Failure
    is None, never interpreted as absent work.
    """
    if not callable(runner):
        return None
    env = {k: os.environ[k] for k in _ENV_KEYS if k in os.environ}
    env["SYSTEMD_COLORS"] = "0"
    try:
        result = runner(_QUERY, env=env, capture_output=True,
                        text=True, check=False, timeout=4)
    except (OSError, ValueError, TypeError, UnicodeError,
            subprocess.TimeoutExpired):
        return None
    if (not isinstance(result, subprocess.CompletedProcess)
            or type(result.returncode) is not int
            or result.returncode != 0 or type(result.stdout) is not str
            or len(result.stdout) > 80):
        return None
    raw = result.stdout.strip()
    # busctl call GetNameOwner returns: s ":1.23"
    if not raw.startswith('s "') or not raw.endswith('"'):
        return None
    name = raw[3:-1]
    return name if _OWNER.fullmatch(name) is not None else None

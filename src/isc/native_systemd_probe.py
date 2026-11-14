"""Optional, bounded read-only systemd user-manager availability probe.

This is a Step 99 capability check, NOT an event subscription, process
supervisor or source of suspend eligibility. NameHasOwner can establish only
that the scoped D-Bus name currently has an owner; not that delivery works,
that events were never lost, or that workload ownership is authenticated.
No returned command output is retained beyond a strict boolean parse.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import subprocess
from typing import Callable

_QUERY = (
    "busctl", "--user", "call",
    "org.freedesktop.DBus", "/org/freedesktop/DBus",
    "org.freedesktop.DBus", "NameHasOwner", "s",
    "org.freedesktop.systemd1",
)
_ENV_KEYS = (
    "PATH", "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
    "LANG", "LC_ALL",
)


@dataclass(frozen=True)
class UserManagerProbe:
    state: str
    coverage_verified: bool = False
    event_delivery_verified: bool = False
    task_owner_authenticated: bool = False


def probe_user_manager(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> UserManagerProbe:
    """Query the existing user D-Bus; never start or configure a service.

    UNKNOWN includes missing busctl, permission failures, timeout, malformed
    output, and inaccessible bus. ABSENT is NOT proof of an idle machine.
    A successful result is a point-in-time observation, not a subscription.
    """
    unknown = UserManagerProbe("UNKNOWN")
    if not callable(runner):
        return unknown
    env = {key: os.environ[key] for key in _ENV_KEYS if key in os.environ}
    env["SYSTEMD_COLORS"] = "0"
    try:
        result = runner(
            _QUERY, env=env, capture_output=True, text=True,
            check=False, timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError):
        return unknown
    if (not isinstance(result, subprocess.CompletedProcess)
            or result.returncode != 0
            or not isinstance(result.stdout, str)
            or len(result.stdout) > 64):
        return unknown
    value = result.stdout.strip()
    if value == "b true":
        return UserManagerProbe("PRESENT_UNVERIFIED")
    if value == "b false":
        return UserManagerProbe("ABSENT_UNVERIFIED")
    return unknown

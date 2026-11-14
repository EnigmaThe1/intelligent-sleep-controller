"""Bounded, opt-in read-only systemd user-manager signal observation.

This is NOT wired into the ISC service, CLI, tray, or sleep policy.
A single, narrowly matched D-Bus signal may be observed using the
installed busctl tool. All output (including potentially private D-Bus
payloads) goes directly to DEVNULL: ISC never reads or stores it.
A timeout is silence/uncertainty, not a claim of inactivity.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import subprocess
from typing import Callable

_MATCH = "type='signal',interface='org.freedesktop.systemd1.Manager'"
_COMMAND = (
    "busctl", "--user", "--no-pager",
    "--limit-messages=1", "--match=" + _MATCH,
    "monitor", "org.freedesktop.systemd1",
)
_ENV_KEYS = ("PATH", "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
             "LANG", "LC_ALL")


@dataclass(frozen=True)
class SignalObservation:
    state: str
    observed_events: int | None = None
    coverage_verified: bool = False
    event_delivery_verified: bool = False
    task_owner_authenticated: bool = False


def observe_one_user_manager_signal(
    *, seconds: int = 3,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> SignalObservation:
    """Observe at most one signal, never an arbitrary D-Bus message body.

    A successful busctl exit means one matched notification was observed,
    but not that an application or service finished. A timeout deliberately
    produces QUIET_UNVERIFIED with UNKNOWN event count because subscription
    readiness and delivery continuity cannot be established from silence.
    No shell, sudo, power control, file output, or persistent subscription.
    """
    unknown = SignalObservation("UNKNOWN")
    if type(seconds) is not int or not 1 <= seconds <= 10 or not callable(runner):
        return unknown
    env = {name: os.environ[name] for name in _ENV_KEYS if name in os.environ}
    env["SYSTEMD_COLORS"] = "0"
    try:
        result = runner(
            _COMMAND, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=seconds,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run kills and reaps the subprocess on timeout.
        return SignalObservation("QUIET_UNVERIFIED")
    except (OSError, ValueError, TypeError, RuntimeError):
        return unknown
    if not isinstance(result, subprocess.CompletedProcess):
        return unknown
    if type(result.returncode) is not int or result.returncode != 0:
        return unknown
    return SignalObservation("EVENT_OBSERVED_UNVERIFIED", observed_events=1)

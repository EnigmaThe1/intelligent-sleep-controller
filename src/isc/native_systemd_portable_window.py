"""Portable, opt-in read-only systemd user-bus observation window.

Compatible with systemd 255's busctl monitor syntax. No --limit-messages,
no data capture, no command-line shell, and no permanent subscription.
This is a transport liveness experiment ONLY. DEVNULL prevents ISC from
reading unit identities, message bodies, paths, or environment values.
No matched-event counter is available from this transport.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import subprocess
from typing import Callable

_MATCH = "type='signal',interface='org.freedesktop.systemd1.Manager'"
_COMMAND = (
    "busctl", "--user", "--no-pager", "--match=" + _MATCH,
    "monitor", "org.freedesktop.systemd1",
)
_ALLOWED_ENV = ("PATH", "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
                "LANG", "LC_ALL")


@dataclass(frozen=True)
class PortableSignalWindow:
    state: str
    observed_events: int | None = None
    coverage_verified: bool = False
    event_delivery_verified: bool = False
    task_owner_authenticated: bool = False


def inspect_user_manager_window(
    *, seconds: int = 3,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> PortableSignalWindow:
    """Bound subprocess lifetime; do not infer signals from a timed-out watch.

    When the command survives until timeout, only a listening *attempt* is
    known, not whether a subscription succeeded or whether signals arrived.
    An early exit, even code 0, is not a verified observation. On CPython
    subprocess.run() kills and waits for the process after TimeoutExpired.
    """
    unknown = PortableSignalWindow("UNKNOWN")
    if type(seconds) is not int or not 1 <= seconds <= 10 or not callable(runner):
        return unknown
    env = {key: os.environ[key] for key in _ALLOWED_ENV if key in os.environ}
    env["SYSTEMD_COLORS"] = "0"
    try:
        result = runner(
            _COMMAND, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False, timeout=seconds,
        )
    except subprocess.TimeoutExpired:
        return PortableSignalWindow("WINDOW_ELAPSED_UNVERIFIED")
    except (OSError, ValueError, RuntimeError, TypeError):
        return unknown
    # A premature return may mean permission refusal, bus disconnection,
    # unsupported invocation or another failure. No event can be proven.
    return unknown

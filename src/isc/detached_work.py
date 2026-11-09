"""Explicitly launch a detached development command under a native sleep inhibitor.

systemd-run starts a transient *user* service; systemd-inhibit acquires the
sleep/idle blocker before running the requested command. The service can
continue after the invoking terminal closes. No shell is interpreted, no
user/system unit files are edited, and this is never invoked automatically.

A successful systemd-run exit verifies the service wrapper was started,
NOT that the inhibitor remains held or that detached grandchildren are
covered once the wrapper exits. Other independent jobs remain unverified.
Arguments may appear in systemd's user-visible unit metadata and journal:
never pass credentials or sensitive commands as CLI arguments.
"""
from __future__ import annotations

from dataclasses import dataclass
import secrets
import subprocess
from typing import Sequence

from .protected_work import protected_command

_SYSTEMD_RUN = "/usr/bin/systemd-run"
_PREFIX = "isc-protected-"
_HEX_LENGTH = 16


@dataclass(frozen=True)
class DetachedWorkResult:
    started: bool
    reason: str
    unit: str | None = None


def detached_work_command(
    argv: Sequence[str], *, unit: str,
) -> tuple[str, ...] | None:
    """Create fixed, bounded argv for native systemd user manager."""
    if (
        type(unit) is not str
        or not unit.startswith(_PREFIX)
        or not unit.endswith(".service")
        or len(unit) != len(_PREFIX) + _HEX_LENGTH + len(".service")
        or any(char not in "0123456789abcdef"
               for char in unit[len(_PREFIX):-len(".service")])
    ):
        return None
    protected = protected_command(argv)
    if protected is None:
        return None

    # systemd-run passes ExecStart arguments through the service manager,
    # which may expand $VARIABLE. Double the marker so its literal meaning
    # survives; do not run a shell or enable variable expansion from input.
    command = protected[:6] + tuple(
        part.replace("$", "$") for part in protected[6:]
    )
    return (
        _SYSTEMD_RUN,
        "--user",
        "--collect",
        "--service-type=exec",
        "--same-dir",
        "--description=ISC protected detached work",
        "--unit=" + unit,
        "--",
        *command,
    )


def launch_detached_work(argv: Sequence[str]) -> DetachedWorkResult:
    """Launch only on explicit user invocation; no ISC scheduler or daemon.

    Type=exec waits for systemd-inhibit's exec() to succeed, not for actual
    acquisition of a logind lock. A successful native launch is *not*
    authoritative proof of protection: the job still needs its inhibitor.
    """
    if protected_command(argv) is None:
        return DetachedWorkResult(False, "invalid_protected_command")
    unit = _PREFIX + secrets.token_hex(8) + ".service"
    command = detached_work_command(argv, unit=unit)
    if command is None:
        return DetachedWorkResult(False, "invalid_protected_command")
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return DetachedWorkResult(False, "launch_outcome_unknown", unit)
    except (OSError, ValueError, TypeError):
        return DetachedWorkResult(False, "user_systemd_unavailable")
    if result.returncode != 0:
        return DetachedWorkResult(False, "native_launch_rejected")
    return DetachedWorkResult(True, "service_started_inhibitor_unverified", unit)

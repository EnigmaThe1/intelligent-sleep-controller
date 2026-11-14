"""Protect a foreground development command using native systemd inhibitors.

Only the explicit 'isc protect -- COMMAND [ARGS...]' operator action can
launch a process. The ISC wrapper is replaced with systemd-inhibit so the
kernel manages the child process and the inhibitor lifetime. No daemon,
configuration change, network call or suspend request is made.

Detached/untracked tasks after the foreground command exits are NOT covered.
ISC must continue to treat those as unknown until separately verified.
"""
from __future__ import annotations

import os
from typing import Sequence

_SYSTEMD_INHIBIT = "/usr/bin/systemd-inhibit"
_MAX_ARGS = 256
_MAX_ARG_BYTES = 65536


def protected_command(argv: Sequence[str]) -> tuple[str, ...] | None:
    """Construct fixed native invocation; never interpret a shell command."""
    if (isinstance(argv, (str, bytes)) or not isinstance(argv, (list, tuple))
            or not 1 <= len(argv) <= _MAX_ARGS
            or any(type(part) is not str or not part or "\x00" in part
                   for part in argv)):
        return None
    try:
        if sum(len(part.encode("utf-8")) for part in argv) > _MAX_ARG_BYTES:
            return None
    except UnicodeError:
        return None
    return (
        _SYSTEMD_INHIBIT,
        "--what=idle:sleep",
        "--mode=block",
        "--who=Intelligent Sleep Controller",
        "--why=Protected foreground work",
        "--",
        *argv,
    )


def run_protected_work(argv: Sequence[str]) -> int:
    """Exec systemd-inhibit; do not retain command or secrets in ISC logs.

    Returns 2 only if validation or exec failed; successful exec never
    returns to ISC, and the native command's exit status reaches the shell.
    """
    command = protected_command(argv)
    if command is None:
        return 2
    try:
        os.execv(_SYSTEMD_INHIBIT, command)
    except (OSError, ValueError, UnicodeError):
        return 2
    return 2  # Defensive: execv should never return on success.

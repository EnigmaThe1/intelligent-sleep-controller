"""Read-only Linux Unix-socket peer identity evidence for future opt-in adapters.

SO_PEERCRED proves the kernel-reported peer PID/UID/GID for a *connected*
Unix socket. Bracketed same-UID /proc stat checks detect common PID reuse
and identity races. Neither establishes application-task authority, grants
a registration capability, or permits suspend. No listening socket is added.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import socket
import struct
from typing import Callable

from .native_service_enrollment import _identity_for_pid
from .process_lifecycle import ProcessIdentity

_MAX_PID = 4194304


@dataclass(frozen=True)
class PeerProcessEvidence:
    """Transient in-process process identity, never exposed over health wire."""
    identity: ProcessIdentity
    uid: int
    authenticated_task_owner: bool = False
    coverage_verified: bool = False


def read_linux_peer_process(
    connection: socket.socket, *,
    expected_uid: int | None = None,
    process_lookup: Callable[[int], ProcessIdentity | None] = _identity_for_pid,
) -> PeerProcessEvidence | None:
    """Check Linux SO_PEERCRED, then bracket the peer's procfs identity.

    Callers must supply a connected Unix socket accepted by a trusted
    transport; never accept a PID or UID reported in a JSON event payload.
    This is strictly evidence of a *process*, not a specific application task.
    """
    if expected_uid is None:
        expected_uid = os.getuid()
    if (type(expected_uid) is not int or expected_uid < 0
            or not callable(process_lookup)):
        return None
    option = getattr(socket, "SO_PEERCRED", None)
    if type(option) is not int:
        return None
    try:
        raw = connection.getsockopt(
            socket.SOL_SOCKET, option, struct.calcsize("3i"),
        )
        if not isinstance(raw, bytes) or len(raw) != struct.calcsize("3i"):
            return None
        pid, uid, gid = struct.unpack("3i", raw)
        if (not 0 < pid <= _MAX_PID or uid != expected_uid or gid < 0):
            return None
        first = process_lookup(pid)
        second = process_lookup(pid)
        if (not isinstance(first, ProcessIdentity)
                or first != second
                or first.pid != pid
                or type(first.start_ticks) is not int
                or first.start_ticks <= 0):
            return None
        # No PID, UID, command, process name, or token is exported to status.
        return PeerProcessEvidence(first, uid)
    except (OSError, ValueError, TypeError, struct.error, AttributeError):
        return None

"""Read-only systemd-origin process identity discovery for opt-in MONITOR enrollment.

This module does not accept self-reported PIDs as evidence. It requests the
MainPID, invocation identity and ControlGroup from systemd, verifies a stable
same-user Linux process identity and cgroup membership, and checks the
service metadata again. A systemd-origin service-to-PID mapping is stronger
than a sender claim but is NOT application task ownership authentication.
Nothing here permits sleep or declares any job finished.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Callable

from .cgroup_membership import CgroupMembership, observe_cgroup_membership
from .native_observers import _capture
from .process_lifecycle import ProcessIdentity, _read_stat
from .service_process_correlation import _ID, _SERVICE
from .timer_service_states import _properties
from .timer_service_tasks import _valid_cgroup

_MAX_PID = 4194304


@dataclass(frozen=True)
class NativeServiceProcess:
    """Private, transient systemd-origin evidence. Never log/serialise."""

    identity: ProcessIdentity
    service: str
    scope: str
    invocation: str
    cgroup: str
    coverage_verified: bool = False
    task_owner_authenticated: bool = False


def _service_query(service: str, scope: str) -> tuple[str, ...]:
    if (not isinstance(service, str) or _SERVICE.fullmatch(service) is None
            or scope not in ("--system", "--user")):
        raise ValueError("invalid_native_service")
    return (
        "systemctl", scope, "--no-pager", "show",
        "--property=Id,MainPID,InvocationID,ControlGroup", "--", service,
    )


def _metadata(raw: str | None, service: str) -> tuple[int, str, str] | None:
    rows = _properties(
        raw, (service,),
        frozenset(("Id", "MainPID", "InvocationID", "ControlGroup")),
    )
    if rows is None:
        return None
    props = rows[service]
    pid_text = props["MainPID"]
    invocation = props["InvocationID"]
    group = props["ControlGroup"]
    if (not pid_text.isascii() or not pid_text.isdecimal()
            or len(pid_text) > 8
            or not 0 < int(pid_text) <= _MAX_PID
            or _ID.fullmatch(invocation) is None
            or not group or group == "/"
            or not _valid_cgroup(group)
            or "//" in group or "/../" in group or group.endswith("/..")):
        return None
    return int(pid_text), invocation.lower(), group


def _identity_for_pid(
    pid: int, *, proc_root: str | Path = "/proc", uid: int | None = None,
) -> ProcessIdentity | None:
    """Read /proc/PID/stat twice for same-user stable kernel identity."""
    if uid is None:
        uid = os.getuid()
    if type(uid) is not int or uid < 0:
        return None
    path = Path(proc_root) / str(pid)
    try:
        st = path.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid:
            return None
        first = _read_stat(path / "stat", pid)
        second = _read_stat(path / "stat", pid)
        if first != second or first.start_ticks <= 0:
            return None
        return first
    except (OSError, ValueError, TypeError):
        return None


def discover_native_service_process(
    service: str, *, scope: str,
    capture: Callable[[tuple[str, ...]], str | None] = _capture,
    process_lookup: Callable[[int], ProcessIdentity | None] = _identity_for_pid,
    membership: Callable[[ProcessIdentity, str], CgroupMembership]
    = observe_cgroup_membership,
) -> NativeServiceProcess | None:
    """Bracket native membership checks with two systemd identity queries.

    Only stable, same-user MainPID and service identity are accepted.
    No caller-supplied PID can override systemd MainPID. The result remains
    non-authoritative for application-level job ownership or completion.
    """
    try:
        query = _service_query(service, scope)
        first = _metadata(capture(query), service)
        if first is None:
            return None
        pid, invocation, group = first
        identity = process_lookup(pid)
        if (not isinstance(identity, ProcessIdentity)
                or type(identity.pid) is not int or identity.pid != pid
                or type(identity.start_ticks) is not int
                or identity.start_ticks <= 0):
            return None
        observed = membership(identity, group)
        if (not isinstance(observed, CgroupMembership)
                or observed.coverage_verified or observed.match is not True):
            return None
        second = _metadata(capture(query), service)
        if first != second:
            return None
        return NativeServiceProcess(
            identity=identity, service=service, scope=scope,
            invocation=invocation, cgroup=group,
        )
    except (OSError, ValueError, TypeError, RuntimeError, KeyError):
        return None

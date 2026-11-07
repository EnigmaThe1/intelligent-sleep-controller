"""Read-only Linux cgroup v2 process membership probe.

Checks whether a *specific previously observed process identity* appears
inside a systemd service cgroup, including nested child cgroups. This is
point-in-time evidence only: it does NOT authenticate a task owner, establish
complete workload coverage, or authorize sleep. No process paths, PIDs or
cgroup names are returned from the public result.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat

from .process_lifecycle import ProcessIdentity, _read_stat
from .timer_service_tasks import _valid_cgroup

_MAX_CGROUP_BYTES = 4096


@dataclass(frozen=True)
class CgroupMembership:
    """match=True/False/None: observed in scope / elsewhere / UNKNOWN."""

    match: bool | None
    coverage_verified: bool = False


def parse_cgroup_v2(raw: bytes | None) -> str | None:
    """Accept exactly one unified cgroup-v2 line; reject legacy ambiguity."""
    if not isinstance(raw, bytes) or len(raw) > _MAX_CGROUP_BYTES:
        return None
    try:
        value = raw.decode("utf-8")
    except UnicodeError:
        return None
    lines = value.splitlines()
    if len(lines) != 1 or not lines[0].startswith("0::"):
        return None
    path = lines[0][3:]
    if not _valid_cgroup(path) or not path.startswith("/"):
        return None
    if "//" in path or "/../" in path or path.endswith("/.."):
        return None
    return path


def within_cgroup(actual: str, expected: str) -> bool | None:
    """Boundary-aware membership, so /work.service2 never matches /work.service."""
    if (not isinstance(actual, str) or not isinstance(expected, str)
            or not _valid_cgroup(actual) or not _valid_cgroup(expected)
            or not actual.startswith("/") or not expected.startswith("/")
            or not expected or "//" in actual or "//" in expected
            or "/../" in actual or "/../" in expected):
        return None
    if expected == "/":
        return True
    scope = expected.rstrip("/")
    return actual == scope or actual.startswith(scope + "/")


def observe_cgroup_membership(
    identity: ProcessIdentity, service_cgroup: str, *,
    proc_root: str | Path = "/proc", uid: int | None = None,
) -> CgroupMembership:
    """Compare identity before and after procfs cgroup read; fail closed.

    Only current-user processes are read by default. No cmdline, environ,
    filesystem paths outside numeric proc entries, or process signals.
    """
    unknown = CgroupMembership(None)
    if (not isinstance(identity, ProcessIdentity)
            or type(identity.pid) is not int or identity.pid <= 0
            or type(identity.start_ticks) is not int or identity.start_ticks <= 0
            or not isinstance(identity.name, str)
            or not isinstance(service_cgroup, str)
            or within_cgroup("/", service_cgroup) is None):
        return unknown
    if uid is None:
        uid = os.getuid()
    if type(uid) is not int or uid < 0:
        return unknown
    directory = Path(proc_root) / str(identity.pid)
    try:
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid:
            return unknown
        before = _read_stat(directory / "stat", identity.pid)
        if (before.pid, before.start_ticks, before.name) != (
            identity.pid, identity.start_ticks, identity.name,
        ):
            return unknown
        fd = os.open(
            directory / "cgroup",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            chunks = os.read(fd, _MAX_CGROUP_BYTES + 1)
        finally:
            os.close(fd)
        actual = parse_cgroup_v2(chunks)
        after = _read_stat(directory / "stat", identity.pid)
        if before != after or actual is None:
            return unknown
        return CgroupMembership(within_cgroup(actual, service_cgroup))
    except (OSError, UnicodeError, ValueError, TypeError):
        return unknown

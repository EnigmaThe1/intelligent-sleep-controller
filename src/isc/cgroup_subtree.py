"""Bounded, read-only inventory of a Linux cgroup-v2 service subtree.

A process that re-parents may remain in the same service cgroup. Reading
cgroup.procs in the service and each descendant can reveal that process
without following its PPID lineage. This is limited point-in-time evidence:
processes may migrate, disappear or escape between scans. Neither an empty
inventory nor a stable inventory establishes task completion or suspend safety.

Only counts leave the observer. No command lines, environments, process
details, unit names, or paths are returned.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Callable

from .cgroup_membership import CgroupMembership, observe_cgroup_membership
from .native_service_enrollment import _identity_for_pid
from .process_lifecycle import ProcessIdentity
from .timer_service_tasks import _valid_cgroup

_MAX_CGROUP_NODES = 256
_MAX_DEPTH = 16
_MAX_PROCESSES = 4096
_MAX_PROCS_BYTES = 32768


@dataclass(frozen=True)
class CgroupSubtreeSummary:
    observed_processes: int | None
    unknown: bool = True
    coverage_verified: bool = False
    task_owner_authenticated: bool = False


def _valid_subtree_path(path: str) -> bool:
    if (not isinstance(path, str) or not _valid_cgroup(path)
            or path in ("", "/") or "//" in path):
        return False
    parts = path.split("/")[1:]
    return all(p not in ("", ".", "..") for p in parts)


def _collect_pids(root: Path, service_path: str) -> frozenset[int] | None:
    """Refuse links and incomplete/oversized cgroup subtree inventories."""
    base = root / service_path.lstrip("/")
    pending = [(base, 0)]
    visited = 0
    found: set[int] = set()
    try:
        while pending:
            current, depth = pending.pop()
            visited += 1
            if visited > _MAX_CGROUP_NODES or depth > _MAX_DEPTH:
                return None
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode):
                return None
            members = current / "cgroup.procs"
            fd = os.open(members, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                raw = os.read(fd, _MAX_PROCS_BYTES + 1)
            finally:
                os.close(fd)
            if len(raw) > _MAX_PROCS_BYTES:
                return None
            # Reject duplicate PIDs within one file; across two nodes a PID
            # can legitimately migrate mid-scan, so fail closed as well.
            for row in raw.splitlines():
                if not row or len(row) > 10 or not row.isascii() or not row.isdigit():
                    return None
                pid = int(row)
                if not 0 < pid <= 4194304 or pid in found:
                    return None
                found.add(pid)
                if len(found) > _MAX_PROCESSES:
                    return None
            with os.scandir(current) as entries:
                for child in entries:
                    if child.name in (".", ".."):
                        continue
                    if child.is_symlink():
                        return None
                    if child.is_dir(follow_symlinks=False):
                        if depth >= _MAX_DEPTH:
                            return None
                        if len(pending) + visited >= _MAX_CGROUP_NODES:
                            return None
                        pending.append((Path(child.path), depth + 1))
        return frozenset(found)
    except (OSError, ValueError, OverflowError):
        return None


def observe_cgroup_subtree(
    service_cgroup: str, *,
    cgroup_root: str | Path = "/sys/fs/cgroup",
    proc_root: str | Path = "/proc",
    uid: int | None = None,
    identity_lookup: Callable[..., ProcessIdentity | None] = _identity_for_pid,
    membership_probe: Callable[..., CgroupMembership] = observe_cgroup_membership,
) -> CgroupSubtreeSummary:
    """Double-sample the subtree and validate every same-UID process.

    Returns aggregate-only evidence even on a successful scan. Foreign UID,
    missing files, raced migration, invalid names, or unreadable procfs make
    the result UNKNOWN, rather than implying a service has no work.
    """
    unknown = CgroupSubtreeSummary(None)
    if not _valid_subtree_path(service_cgroup):
        return unknown
    if uid is None:
        uid = os.getuid()
    if (type(uid) is not int or uid < 0
            or not callable(identity_lookup) or not callable(membership_probe)):
        return unknown
    root = Path(cgroup_root)
    first = _collect_pids(root, service_cgroup)
    if first is None:
        return unknown
    try:
        identities: dict[int, ProcessIdentity] = {}
        for pid in first:
            identity = identity_lookup(pid, proc_root=proc_root, uid=uid)
            if (not isinstance(identity, ProcessIdentity)
                    or type(identity.pid) is not int or identity.pid != pid
                    or type(identity.start_ticks) is not int
                    or identity.start_ticks <= 0):
                return unknown
            member = membership_probe(
                identity, service_cgroup, proc_root=proc_root, uid=uid,
            )
            if (not isinstance(member, CgroupMembership)
                    or member.coverage_verified or member.match is not True):
                return unknown
            identities[pid] = identity
    except (OSError, TypeError, ValueError, RuntimeError):
        return unknown
    last = _collect_pids(root, service_cgroup)
    if last is None or last != first:
        return unknown
    # A PID list may stay identical while a worker exits and its PID is
    # reused, or membership changes immediately after the first check.
    # Revalidate both kernel process identity and cgroup membership after
    # the second inventory. Count-only agreement is not identity evidence.
    try:
        for pid in last:
            identity = identity_lookup(pid, proc_root=proc_root, uid=uid)
            if identity != identities[pid]:
                return unknown
            member = membership_probe(
                identity, service_cgroup, proc_root=proc_root, uid=uid,
            )
            if (not isinstance(member, CgroupMembership)
                    or member.coverage_verified or member.match is not True):
                return unknown
    except (OSError, TypeError, ValueError, RuntimeError):
        return unknown
    return CgroupSubtreeSummary(len(first), unknown=False)

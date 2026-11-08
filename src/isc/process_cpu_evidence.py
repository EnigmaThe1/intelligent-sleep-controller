"""Privacy-minimal CPU progress evidence for already-tracked Linux processes.

CPU time increasing proves observed execution within the sample interval,
NOT application task ownership, useful work, completion, or safe idleness.
A waiting I/O/model/network task may consume no CPU for a long period.
Only same-UID /proc/PID/stat is read; no command lines or environments.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat

from .process_lifecycle import ProcessIdentity, ProcessLineage, parse_proc_stat

_MAX_BYTES = 4096
_MAX_PROCESSES = 4096
_MAX_TICKS = 2**63 - 1


@dataclass(frozen=True)
class CpuSample:
    identity: ProcessIdentity
    total_ticks: int


@dataclass(frozen=True)
class CpuProgress:
    progressing: int | None
    unchanged: int | None
    changed_or_missing: int | None
    coverage_verified: bool = False
    task_owner_authenticated: bool = False


def parse_cpu_stat(raw: str, expected: ProcessIdentity) -> CpuSample | None:
    """Extract utime+stime only after validating existing procfs identity."""
    if not isinstance(raw, str) or not isinstance(expected, ProcessIdentity):
        return None
    if len(raw.encode("utf-8")) > _MAX_BYTES:
        return None
    identity = parse_proc_stat(raw, expected.pid)
    if identity != expected:
        return None
    end = raw.rfind(") ")
    if end < 0:
        return None
    values = raw[end + 2:].split()
    if len(values) < 20:
        return None
    fields = (values[11], values[12])
    if any(not value.isascii() or not value.isdecimal()
           or len(value) > 20 for value in fields):
        return None
    ticks = [int(field) for field in fields]
    if any(tick < 0 or tick > _MAX_TICKS for tick in ticks):
        return None
    if sum(ticks) > _MAX_TICKS:
        return None
    return CpuSample(identity, sum(ticks))


def read_cpu_samples(
    snapshot: ProcessLineage | None, *,
    proc_root: str | Path = "/proc",
    uid: int | None = None,
) -> dict[int, CpuSample] | None:
    """Two bounded stat reads per tracked process, fail closed on races."""
    if snapshot is None or not isinstance(snapshot, ProcessLineage):
        return None
    if uid is None:
        uid = os.getuid()
    if type(uid) is not int or uid < 0:
        return None
    identities = snapshot.tracked
    if len(identities) > _MAX_PROCESSES:
        return None
    found: dict[int, CpuSample] = {}
    try:
        for identity in identities:
            if (type(identity.pid) is not int or identity.pid <= 0
                    or identity.pid in found or identity.start_ticks <= 0):
                return None
            directory = Path(proc_root) / str(identity.pid)
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid:
                return None
            path = directory / "stat"
            with path.open("rb") as stream:
                raw_first = stream.read(_MAX_BYTES + 1)
            with path.open("rb") as stream:
                raw_second = stream.read(_MAX_BYTES + 1)
            if len(raw_first) > _MAX_BYTES or len(raw_second) > _MAX_BYTES:
                return None
            first = parse_cpu_stat(raw_first.decode("utf-8"), identity)
            second = parse_cpu_stat(raw_second.decode("utf-8"), identity)
            if (first is None or second is None
                    or second.total_ticks < first.total_ticks):
                return None
            found[identity.pid] = second
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    return found


def compare_cpu_samples(
    previous: dict[int, CpuSample] | None,
    current: dict[int, CpuSample] | None,
) -> CpuProgress:
    """Compare identities, not bare PIDs; missing or regressed -> UNKNOWN."""
    unknown = CpuProgress(None, None, None)
    if previous is None or current is None:
        return unknown
    if len(previous) > _MAX_PROCESSES or len(current) > _MAX_PROCESSES:
        return unknown
    progressing = unchanged = changed = 0
    for pid, before in previous.items():
        after = current.get(pid)
        if not isinstance(before, CpuSample):
            return unknown
        if after is None or after.identity != before.identity:
            changed += 1
        elif after.total_ticks < before.total_ticks:
            return unknown
        elif after.total_ticks > before.total_ticks:
            progressing += 1
        else:
            unchanged += 1
    changed += len(current.keys() - previous.keys())
    return CpuProgress(progressing, unchanged, changed)

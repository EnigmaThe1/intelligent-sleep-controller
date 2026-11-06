"""Conservative, read-only lifecycle snapshots for selected Linux workloads.

This observes process identity (PID plus kernel start-time ticks) and selected
same-user descendants. It never reads command lines, environment variables,
file paths, open files, network sockets, or project content.

Important: a PID disappearing or a process becoming a zombie is NOT proof
that an application task finished successfully. These observations NEVER
establish complete workload coverage or eligibility for automatic suspend.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from .processes import DEFAULT_CANDIDATE_NAMES

_COMM = re.compile(r"[^\\x00-\\x1f\\x7f]{1,15}\\Z")
_MAX_CENSUS = 16384
_MAX_STAT_BYTES = 4096


@dataclass(frozen=True, order=True)
class ProcessIdentity:
    pid: int
    start_ticks: int
    parent_pid: int
    name: str


@dataclass(frozen=True)
class ProcessLineage:
    """A partial same-user snapshot, never a complete workload inventory."""

    roots: tuple[ProcessIdentity, ...]
    descendants: tuple[ProcessIdentity, ...]
    vanished_during_scan: int
    coverage_verified: bool = False

    @property
    def tracked(self) -> tuple[ProcessIdentity, ...]:
        return tuple(sorted(self.roots + self.descendants))


@dataclass(frozen=True)
class ProcessChange:
    continuing: int
    appeared: int
    disappeared: int
    reused_pids: int
    visibility_races: int
    coverage_verified: bool = False


def parse_proc_stat(raw: str, expected_pid: int) -> ProcessIdentity | None:
    """Parse procfs field 22 safely, including comm names containing ')'.

    /proc/PID/stat fields begin: pid (comm) state ppid ...
    starttime is field 22 => index 19 after the state field (field 3).
    """
    if (
        type(expected_pid) is not int or expected_pid <= 0
        or not isinstance(raw, str) or len(raw.encode("utf-8")) > _MAX_STAT_BYTES
        or "\\n" in raw[:-1] or "\\x00" in raw
    ):
        return None
    prefix = f"{expected_pid} ("
    if not raw.startswith(prefix):
        return None
    close = raw.rfind(") ")
    if close < len(prefix) or close >= len(raw) - 3:
        return None
    name = raw[len(prefix):close]
    if _COMM.fullmatch(name) is None:
        return None
    remaining = raw[close + 2:].split()
    if len(remaining) < 20:
        return None
    state = remaining[0]
    ppid_text, start_text = remaining[1], remaining[19]
    if (
        len(state) != 1 or not state.isascii() or not state.isalpha()
        or not ppid_text.isascii() or not ppid_text.isdecimal()
        or not start_text.isascii() or not start_text.isdecimal()
    ):
        return None
    parent, start = int(ppid_text), int(start_text)
    if parent < 0 or start < 0:
        return None
    return ProcessIdentity(expected_pid, start, parent, name)


def _read_stat(path: Path, expected_pid: int) -> ProcessIdentity:
    """Read bounded procfs stat; reject malformed or changed identity."""
    with open(path, "rb") as stream:
        raw = stream.read(_MAX_STAT_BYTES + 1)
    if len(raw) > _MAX_STAT_BYTES:
        raise ValueError("oversized_process_stat")
    try:
        value = parse_proc_stat(raw.decode("utf-8"), expected_pid)
    except UnicodeError:
        value = None
    if value is None:
        raise ValueError("invalid_process_stat")
    return value


def read_process_lineage(
    *, proc_root: str | Path = "/proc", uid: int | None = None,
    names: frozenset[str] = DEFAULT_CANDIDATE_NAMES,
) -> ProcessLineage | None:
    """Inspect selected process names and their visible same-user descendants.

    Inventory failure returns None (UNKNOWN), never an empty-safe result.
    Process exit during enumeration is tracked as a visibility race and does
    not establish completed work or complete monitoring coverage.
    """
    if uid is None:
        uid = os.getuid()
    if (
        type(uid) is not int or uid < 0
        or not isinstance(names, frozenset)
        or any(not isinstance(name, str) or _COMM.fullmatch(name) is None
               for name in names)
    ):
        return None
    found: dict[int, ProcessIdentity] = {}
    vanished = 0
    inspected = 0
    try:
        with os.scandir(proc_root) as listing:
            for item in listing:
                if not item.name.isascii() or not item.name.isdecimal():
                    continue
                inspected += 1
                if inspected > _MAX_CENSUS:
                    return None
                try:
                    if not item.is_dir(follow_symlinks=False):
                        continue
                    if item.stat(follow_symlinks=False).st_uid != uid:
                        continue
                    pid = int(item.name)
                    if pid <= 0:
                        return None
                    stat_path = Path(item.path) / "stat"
                    first = _read_stat(stat_path, pid)
                    # PID reuse, task name changes, and exits can occur mid-
                    # sample; double-read to avoid silently trusting one.
                    second = _read_stat(stat_path, pid)
                    if first != second:
                        vanished += 1
                        continue
                    found[pid] = first
                except FileNotFoundError:
                    vanished += 1
                    continue
                except (OSError, UnicodeError, ValueError):
                    return None
    except (OSError, ValueError):
        return None

    roots = {pid for pid, info in found.items() if info.name in names}
    tracked = set(roots)
    # Include same-user descendants regardless of their process comm name.
    # A missing parent or cross-UID child is NOT proof there are no children.
    changed = True
    while changed:
        changed = False
        for pid, info in found.items():
            if pid not in tracked and info.parent_pid in tracked:
                tracked.add(pid)
                changed = True
    return ProcessLineage(
        roots=tuple(sorted(found[pid] for pid in roots)),
        descendants=tuple(sorted(found[pid] for pid in tracked - roots)),
        vanished_during_scan=vanished,
    )


def compare_process_lineage(
    before: ProcessLineage | None, after: ProcessLineage | None,
) -> ProcessChange | None:
    """Report process identity changes, never job completion or safe idle."""
    if before is None or after is None:
        return None
    a = {item.pid: item for item in before.tracked}
    b = {item.pid: item for item in after.tracked}
    shared = a.keys() & b.keys()
    continuing = sum(
        a[pid].start_ticks == b[pid].start_ticks and
        a[pid].name == b[pid].name
        for pid in shared
    )
    replaced = sum(
        a[pid].start_ticks != b[pid].start_ticks
        for pid in shared
    )
    # A name change is not task completion; classify it as a changed identity.
    unchanged = {
        pid for pid in shared
        if a[pid].start_ticks == b[pid].start_ticks
        and a[pid].name == b[pid].name
    }
    return ProcessChange(
        continuing=continuing,
        appeared=len(b.keys() - unchanged),
        disappeared=len(a.keys() - unchanged),
        reused_pids=replaced,
        visibility_races=(
            before.vanished_during_scan + after.vanished_during_scan
        ),
    )

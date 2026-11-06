"""Read-only Linux /proc process candidate discovery.

This is an observational adapter, NOT a workload classifier. Process names,
PIDs and ownership alone cannot tell whether an autonomous task is active,
waiting for a model response, blocked on a human, or safely complete.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

# This is deliberately an illustrative candidate list, not complete coverage.
# Names are exact matches against Linux's truncated (15-byte) comm field.
DEFAULT_CANDIDATE_NAMES = frozenset({
    "codex", "codex-code-mode", "claude", "ollama",
    "pytest", "make", "ninja", "cargo", "rustc",
    "gcc", "g++", "clang", "clang++", "cc1", "cc1plus",
    "ffmpeg", "rsync", "rclone",
})
_COMM = re.compile(r"[^\x00-\x1f\x7f]{1,15}\Z")


@dataclass(frozen=True, order=True)
class ProcessCandidate:
    pid: int
    name: str


@dataclass(frozen=True)
class ProcessScan:
    candidates: tuple[ProcessCandidate, ...]
    """Candidates owned by the scanning user. These remain unclassified."""
    undecodable_names: int = 0

    @property
    def count(self) -> int:
        return len(self.candidates)


def read_process_candidates(
    *, proc_root: str | Path = "/proc", uid: int | None = None,
    names: frozenset[str] = DEFAULT_CANDIDATE_NAMES,
) -> ProcessScan | None:
    """Return candidates, or None if process inventory is not trustworthy.

    Inspect only numeric /proc directories owned by the chosen user. Do not
    read /proc/*/cmdline, cwd, environ, open files, command arguments, or logs.
    A vanished process is an expected race and can be skipped; failures to
    inspect an existing candidate are reported as UNKNOWN (None).
    """
    if uid is None:
        uid = os.getuid()
    if not isinstance(uid, int) or uid < 0:
        return None
    if not isinstance(names, frozenset) or any(
        not isinstance(name, str) or _COMM.fullmatch(name) is None
        for name in names
    ):
        return None
    candidates: list[ProcessCandidate] = []
    undecodable_names = 0
    try:
        with os.scandir(proc_root) as listing:
            for item in listing:
                if not item.name.isascii() or not item.name.isdecimal():
                    continue
                try:
                    if not item.is_dir(follow_symlinks=False):
                        continue
                    if item.stat(follow_symlinks=False).st_uid != uid:
                        continue
                    with open(Path(item.path) / "comm", "rb") as handle:
                        raw = handle.read(256)
                    try:
                        name = raw.decode("utf-8").rstrip("\n")
                    except UnicodeDecodeError:
                        # Linux process names are byte strings. Keep scanning
                        # but explicitly report incomplete name visibility.
                        undecodable_names += 1
                        continue
                except FileNotFoundError:
                    # /proc entries legitimately disappear during enumeration.
                    continue
                except (OSError, UnicodeError):
                    return None
                if _COMM.fullmatch(name) is None:
                    return None
                if name in names:
                    candidates.append(ProcessCandidate(int(item.name), name))
    except (OSError, ValueError):
        return None
    return ProcessScan(tuple(sorted(candidates)), undecodable_names)

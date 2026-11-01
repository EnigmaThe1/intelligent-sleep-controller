"""Read-only systemd-logind inhibitor inventory.

Uses the system D-Bus ListInhibitors method through busctl. Do not
infer that an unrecognised or inaccessible inhibitor list is empty.
"""

from __future__ import annotations

from dataclasses import dataclass
import shlex
import subprocess


_COMMAND = (
    "busctl",
    "--system",
    "call",
    "org.freedesktop.login1",
    "/org/freedesktop/login1",
    "org.freedesktop.login1.Manager",
    "ListInhibitors",
)

_KNOWN_WHAT = frozenset((
    "sleep", "idle", "shutdown", "handle-power-key",
    "handle-reboot-key", "handle-suspend-key", "handle-hibernate-key",
    "handle-lid-switch",
))
_SLEEP_RELEVANT = frozenset(("sleep", "idle"))
_MAX_INHIBITORS = 4096


@dataclass(frozen=True)
class Inhibitor:
    what: frozenset[str]
    mode: str

    @property
    def blocks_automatic_sleep(self) -> bool:
        if self.mode not in ("block", "block-weak"):
            return False
        # Future/unknown types must never be assumed irrelevant.
        return bool(self.what & _SLEEP_RELEVANT or self.what - _KNOWN_WHAT)

    @property
    def delays_sleep(self) -> bool:
        return self.mode == "delay" and "sleep" in self.what


def parse_inhibitors(raw: str) -> tuple[Inhibitor, ...] | None:
    """Parse busctl's a(ssssuu) output without retaining Who/Why/PID.

    Return None (unknown) on every unexpected shape. The result is
    intentionally information-minimal: no sensitive text is stored.
    """
    try:
        fields = shlex.split(raw, posix=True)
    except ValueError:
        return None
    if len(fields) < 2 or fields[0] != "a(ssssuu)" or not fields[1].isascii() or not fields[1].isdecimal():
        return None
    count = int(fields[1])
    if count > _MAX_INHIBITORS or len(fields) != 2 + count * 6:
        return None

    result: list[Inhibitor] = []
    for index in range(count):
        what, _who, _why, mode, uid, pid = fields[2 + index * 6: 8 + index * 6]
        tags = what.split(":")
        if (
            not tags or any(not tag for tag in tags)
            or mode not in ("block", "block-weak", "delay")
            or not uid.isascii() or not uid.isdecimal()
            or not pid.isascii() or not pid.isdecimal()
            or int(uid) >= 2**32 or int(pid) >= 2**32
        ):
            return None
        result.append(Inhibitor(what=frozenset(tags), mode=mode))
    return tuple(result)


def read_inhibitors(*, timeout_seconds: float = 3.0) -> tuple[Inhibitor, ...] | None:
    """Read live logind inhibitors; None means unverified, not empty."""
    try:
        response = subprocess.run(
            _COMMAND, capture_output=True, text=True, check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if response.returncode:
        return None
    return parse_inhibitors(response.stdout)

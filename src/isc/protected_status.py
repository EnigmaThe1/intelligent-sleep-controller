"""Read-only verification of a specific ISC detached job's native inhibitor.

This verifies one point in time, not process completion or lifetime safety.
Does not inspect command lines, other application names, user data or logs.
No service controls or power operations can be called from this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import shlex
import subprocess

from .detached_work import valid_detached_unit
from .inhibitors import _COMMAND as _LIST_INHIBITORS, parse_inhibitors

_SERVICE_PROPERTIES = ("Id", "ActiveState", "SubState", "MainPID")
_MAX_SERVICE_BYTES = 2048
_MAX_INHIBITOR_BYTES = 200_000


@dataclass(frozen=True)
class ProtectedStatus:
    verified: bool
    reason: str


def _capture(argv: tuple[str, ...]) -> str | None:
    """Read fixed local systemd commands, without showing stderr or secrets."""
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=3,
        )
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or not isinstance(completed.stdout, str):
        return None
    if len(completed.stdout) > (
        _MAX_INHIBITOR_BYTES if argv == _LIST_INHIBITORS
        else _MAX_SERVICE_BYTES
    ):
        return None
    return completed.stdout


def _parse_main_pid(raw: object, unit: str) -> int | None:
    """Require a running transient unit and unambiguous systemd properties."""
    if not isinstance(raw, str) or len(raw) > _MAX_SERVICE_BYTES:
        return None
    lines = raw.strip().splitlines()
    if len(lines) != len(_SERVICE_PROPERTIES):
        return None
    properties: dict[str, str] = {}
    for line in lines:
        if "=" not in line:
            return None
        key, value = line.split("=", 1)
        if key not in _SERVICE_PROPERTIES or key in properties:
            return None
        properties[key] = value
    if (
        set(properties) != set(_SERVICE_PROPERTIES)
        or properties["Id"] != unit
        or properties["ActiveState"] != "active"
        or properties["SubState"] != "running"
    ):
        return None
    value = properties["MainPID"]
    if not value.isascii() or not value.isdecimal():
        return None
    pid = int(value)
    return pid if 0 < pid < 2**32 else None


def _owned_inhibitor_present(raw: object, *, pid: int, uid: int) -> bool:
    """Match an exact logind block owner, not an unrelated system lock.

    Reject any malformed reply; never expose Who/Why/PID or unrelated locks.
    """
    if (
        not isinstance(raw, str)
        or len(raw) > _MAX_INHIBITOR_BYTES
        or parse_inhibitors(raw) is None
    ):
        return False
    try:
        fields = shlex.split(raw, posix=True)
    except ValueError:
        return False
    # parse_inhibitors already validated array length and numeric shape.
    count = int(fields[1])
    for index in range(count):
        what, who, why, mode, owner_uid, owner_pid = fields[
            2 + index * 6:8 + index * 6
        ]
        if (
            int(owner_uid) == uid
            and int(owner_pid) == pid
            and who == "Intelligent Sleep Controller"
            and why == "Protected foreground work"
            and mode == "block"
            and {"sleep", "idle"}.issubset(set(what.split(":")))
        ):
            return True
    return False


def inspect_protected_unit(unit: str) -> ProtectedStatus:
    """Verify a known ISC user unit and its live logind block, read-only.

    Check the unit again after the logind query. A successful result is
    momentary evidence only; neither process exit nor next scheduled wake
    may be inferred. A service which finished before inspection is UNKNOWN.
    """
    if not valid_detached_unit(unit):
        return ProtectedStatus(False, "invalid_detached_unit")
    show = (
        "systemctl", "--user", "--no-pager", "show", unit,
        "-p", "Id", "-p", "ActiveState", "-p", "SubState", "-p", "MainPID",
    )
    before = _parse_main_pid(_capture(show), unit)
    if before is None:
        return ProtectedStatus(False, "running_unit_not_verified")
    if not _owned_inhibitor_present(
        _capture(_LIST_INHIBITORS), pid=before, uid=os.getuid(),
    ):
        return ProtectedStatus(False, "native_inhibitor_not_verified")
    after = _parse_main_pid(_capture(show), unit)
    if after != before:
        return ProtectedStatus(False, "unit_changed_during_check")
    return ProtectedStatus(True, "owned_sleep_idle_inhibitor_verified_now")

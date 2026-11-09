"""Strict bounded systemd Manager signal-header assessment (Step 99d).

Pure classifier for *candidate* busctl metadata, not a live D-Bus reader.
The transport must independently verify bus ownership, match installation,
message framing, and continuity before any event can count as delivered.
No raw header or unit identifier is stored or returned.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

# Only narrowly scoped manager signals, not method calls or messages.
_MEMBERS = frozenset(("JobNew", "JobRemoved", "UnitNew", "UnitRemoved"))
_MAX = 32
_LINE = re.compile(r"[A-Za-z][A-Za-z0-9]*=[A-Za-z0-9_.-]+\Z", re.ASCII)


@dataclass(frozen=True)
class SignalHeaderAssessment:
    state: str
    candidate_signals: int | None
    coverage_verified: bool = False
    event_delivery_verified: bool = False
    task_owner_authenticated: bool = False


def assess_manager_signal_headers(headers: object) -> SignalHeaderAssessment:
    """Validate a bounded sequence of minimal, pre-extracted header fields.

    The input must be constructed by a trusted framing layer, not by
    searching arbitrary text for matching words. No payload fields or
    unbounded message bodies are accepted.
    """
    unknown = SignalHeaderAssessment("UNKNOWN", None)
    if not isinstance(headers, (list, tuple)) or len(headers) > _MAX:
        return unknown
    if not headers:
        return SignalHeaderAssessment("EMPTY_UNVERIFIED", 0)
    for header in headers:
        if not isinstance(header, dict) or set(header) != {
            "Type", "Interface", "Member",
        }:
            return unknown
        if any(type(value) is not str or len(value) > 80
               or _LINE.fullmatch(f"{key}={value}") is None
               for key, value in header.items()):
            return unknown
        if (header["Type"] != "signal"
                or header["Interface"] != "org.freedesktop.systemd1.Manager"
                or header["Member"] not in _MEMBERS):
            return unknown
    return SignalHeaderAssessment("CANDIDATE_UNVERIFIED", len(headers))

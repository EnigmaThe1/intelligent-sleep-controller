"""Step 99g: opt-in bracketed systemd user-manager signal correlation.

No persistent watcher, automatic workload enrollment, task completion claim,
logging or suspend authority. Reads only the existing user-bus manager owner
before/after a bounded one-shot header observation. Event sender identity is
ephemeral and never returned, serialized or persisted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .native_systemd_live_probe import LiveSignalProbe, probe_live_user_signal
from .native_systemd_sender import (
    read_user_manager_unique_owner, validate_manager_sender,
)


@dataclass(frozen=True)
class BracketedSignalSummary:
    status: str
    observed_candidates: int | None = None
    manager_sender_correlated: bool = False
    event_delivery_verified: bool = False
    task_owner_authenticated: bool = False
    coverage_verified: bool = False


def inspect_bracketed_user_manager_signal(
    *,
    seconds: int = 3,
    owner_reader: Callable[[], str | None] = read_user_manager_unique_owner,
    watcher: Callable[..., LiveSignalProbe] = probe_live_user_signal,
) -> BracketedSignalSummary:
    """Bracket a single candidate with user-manager unique-owner queries.

    Reading owner before/after is not an atomic snapshot and cannot establish
    uninterrupted sender ownership or D-Bus subscription continuity.
    Unknown state and source changes remain non-authoritative.
    """
    unknown = BracketedSignalSummary("UNKNOWN")
    if (type(seconds) is not int or not 1 <= seconds <= 10
            or not callable(owner_reader) or not callable(watcher)):
        return unknown

    sender: str | None = None

    def receive_candidate(name: str) -> None:
        nonlocal sender
        # One candidate per window; do not accept callback repetition.
        if sender is not None:
            raise ValueError("duplicate_signal_candidate")
        sender = name

    try:
        before = owner_reader()
        if before is None:
            return unknown
        observation = watcher(seconds=seconds, _sender_callback=receive_candidate)
        after = owner_reader()
    except (OSError, ValueError, TypeError, RuntimeError):
        return unknown
    if (not isinstance(observation, LiveSignalProbe)
            or observation.coverage_verified is not False
            or observation.event_delivery_verified is not False
            or observation.task_owner_authenticated is not False):
        return unknown

    if observation.state == "QUIET_UNVERIFIED":
        if observation.candidate_events is not None or sender is not None:
            return unknown
        # Silence cannot hide a changed or unavailable manager owner.
        if after is None:
            return unknown
        if before != after:
            return BracketedSignalSummary("OWNER_CHANGED_UNVERIFIED")
        return BracketedSignalSummary("QUIET_UNVERIFIED")

    if observation.state != "CANDIDATE_SIGNAL_UNVERIFIED":
        return unknown
    if observation.candidate_events != 1 or sender is None:
        return unknown

    correlated = validate_manager_sender(sender, before, after)
    if correlated.status == "SENDER_MATCH_UNVERIFIED":
        return BracketedSignalSummary(
            "SENDER_MATCH_UNVERIFIED", observed_candidates=1,
            manager_sender_correlated=True,
        )
    if correlated.status in ("OWNER_CHANGED_UNVERIFIED", "SENDER_MISMATCH"):
        return BracketedSignalSummary(correlated.status)
    return unknown

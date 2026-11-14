"""Bounded replay and ordering guard for opted-in native lifecycle adapters.

This pure in-memory guard does NOT authenticate a task owner or process.
The caller must independently verify the Unix peer and original systemd
activation on EVERY observation before invoking this guard. Restarting ISC
discards all sessions; previous events never imply completed work.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from .events import EventKind, LifecycleEvent

_MAX_SESSIONS = 256
_MAX_SEQUENCE = 2**31 - 1
_MAX_EVENT_GAP = 60.0


@dataclass(frozen=True)
class SequenceRecord:
    number: int
    observed_at: float


class ControlledEventSequence:
    """Accept contiguous per-task numbers; never infer work completion."""

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], SequenceRecord] = {}
        self._invalid: set[tuple[str, str]] = set()

    def observe(
        self, owner: str, task: str, event: LifecycleEvent,
        *, sequence: int, now: float,
    ) -> bool:
        key = (owner, task)
        if key in self._invalid:
            return False
        # Invalid identifiers cannot create state or evict existing records.
        if (type(owner) is not str or type(task) is not str
                or not owner or not task
                or not isinstance(event, LifecycleEvent)
                or not isinstance(event.kind, EventKind)
                or event.source != owner or event.task_id != task
                or type(event.children_quiescent) is not bool
                or type(sequence) is not int
                or not 1 <= sequence <= _MAX_SEQUENCE
                or type(now) not in (int, float)
                or not math.isfinite(now) or now < 0):
            return False
        prior = self._last.get(key)
        if prior is None:
            if len(self._last) >= _MAX_SESSIONS:
                return False
            # A fresh task session must be explicitly started, not inferred
            # from a finish, stop, permission or a resumed activity event.
            if sequence != 1 or event.kind is not EventKind.STARTED:
                self._invalid.add(key)
                return False
        elif (
            sequence != prior.number + 1
            or now < prior.observed_at
            or now - prior.observed_at > _MAX_EVENT_GAP
        ):
            # Replay, gap, overflow or history loss are sticky UNKNOWN.
            self._invalid.add(key)
            return False
        self._last[key] = SequenceRecord(sequence, float(now))
        return True

    def invalid(self, owner: str, task: str) -> bool:
        return (owner, task) in self._invalid

    def observed_count(self) -> int:
        return len(self._last)

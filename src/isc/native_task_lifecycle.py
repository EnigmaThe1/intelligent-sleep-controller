"""Opt-in correlation of lifecycle claims with native process supervision.

No external application is authenticated here. A lifecycle observation can
reinforce busy evidence but cannot prove a task is completed, waiting safely
for a human, or safe to suspend. Correlation is strictly in-process and keeps
no prompts, commands, service names or task content in public summaries.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from .events import EventKind, LifecycleEvent
from .policy import WorkState

_MAX_AGE_SECONDS = 90.0
_MAX_TASKS = 256


@dataclass(frozen=True)
class TaskLifecyclePulse:
    """Aggregate-only, non-authoritative observed states."""
    busy: int
    unknown: int
    observed_at: float | None
    coverage_verified: bool = False
    task_owner_authenticated: bool = False


@dataclass(frozen=True)
class _Claim:
    kind: EventKind
    observed_at: float


class NativeLifecycleCorrelator:
    """Bind in-process lifecycle claims to *already enrolled* native tasks."""

    def __init__(self) -> None:
        self._claims: dict[tuple[str, str], _Claim] = {}
        self._sticky_unknown: set[tuple[str, str]] = set()

    def report(
        self, owner: str, task: str, event: LifecycleEvent,
        *, now: float, enrolled: bool,
    ) -> bool:
        """Accept only known enrollment identifiers; never authenticate."""
        key = (owner, task)
        if (not enrolled or not isinstance(event, LifecycleEvent)
                or not isinstance(event.kind, EventKind)
                or type(owner) is not str or type(task) is not str
                or event.source != owner or event.task_id != task
                or type(event.children_quiescent) is not bool
                or type(now) not in (int, float)
                or not math.isfinite(now) or now < 0):
            return False
        if key not in self._claims and len(self._claims) >= _MAX_TASKS:
            return False
        previous = self._claims.get(key)
        if previous is not None and now < previous.observed_at:
            self._sticky_unknown.add(key)
            return False
        # Claims about completion or human-idle cannot prove detached work
        # quiescence; once observed, never reset by subsequent busy claims.
        if event.kind in (
            EventKind.FINISHED, EventKind.HUMAN_INPUT_CONFIRMED,
            EventKind.STOPPED, EventKind.CHILD_QUIESCED,
            EventKind.PERMISSION_REQUESTED,
        ):
            self._sticky_unknown.add(key)
        self._claims[key] = _Claim(event.kind, float(now))
        return True

    def state(
        self, owner: str, task: str, *, supervised: bool, now: float,
    ) -> WorkState:
        """No COMPLETED or WAITING_HUMAN result is possible."""
        key = (owner, task)
        if (type(now) not in (int, float) or not math.isfinite(now)
                or now < 0 or not supervised or key in self._sticky_unknown):
            return WorkState.UNKNOWN
        claim = self._claims.get(key)
        if (claim is None or now < claim.observed_at
                or now - claim.observed_at > _MAX_AGE_SECONDS):
            return WorkState.UNKNOWN
        if claim.kind is EventKind.WAITING_EXTERNAL:
            return WorkState.WAITING_EXTERNAL
        if claim.kind in (
            EventKind.STARTED, EventKind.ACTIVITY, EventKind.CHILD_STARTED,
        ):
            return WorkState.ACTIVE
        return WorkState.UNKNOWN

    def aggregate(
        self, enrolled: tuple[tuple[str, str], ...],
        verified: frozenset[tuple[str, str]], *, now: float,
    ) -> TaskLifecyclePulse:
        if (type(now) not in (int, float) or not math.isfinite(now)
                or now < 0):
            raise ValueError("invalid_lifecycle_summary_time")
        busy = unknown = 0
        for owner, task in enrolled:
            state = self.state(
                owner, task, supervised=(owner, task) in verified, now=now,
            )
            if state in (WorkState.ACTIVE, WorkState.WAITING_EXTERNAL):
                busy += 1
            else:
                unknown += 1
        return TaskLifecyclePulse(busy, unknown, float(now))

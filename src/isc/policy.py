"""Pure policy. No side effects, system commands, or suspend calls.

This module answers whether a *trusted, complete* system snapshot is
eligible for suspend. It is NOT a host activity detector or suspend daemon.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


class WorkState(str, Enum):
    ACTIVE = "active"
    WAITING_EXTERNAL = "waiting_external"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class WorkItem:
    identifier: str
    state: WorkState
    active_children: bool = False


@dataclass(frozen=True)
class Snapshot:
    """Explicit facts from future trusted adapters.

    coverage_complete means all relevant independent workloads have been
    accounted for. scheduled_work_safe means a timer/wake policy has verified
    that no pending unattended task requires the host to remain awake.
    Both default to False; these must never be inferred from low CPU usage.
    """

    user_idle_seconds: float | None = None
    threshold_seconds: int = 1800
    coverage_complete: bool = False
    scheduled_work_safe: bool = False
    active_sleep_inhibitors: tuple[str, ...] = ()
    work_items: tuple[WorkItem, ...] = ()


@dataclass(frozen=True)
class Verdict:
    eligible: bool
    reasons: tuple[str, ...]


def evaluate(snapshot: Snapshot) -> Verdict:
    """Return an explainable, conservative sleep-eligibility verdict.

    Even an eligible verdict does not authorize suspending a host: the future
    executor must additionally enforce persistent operator mode, current
    inhibitors, user presence, fresh state, and a final pre-suspend recheck.
    """

    reasons: list[str] = []

    if snapshot.threshold_seconds <= 0:
        reasons.append("invalid_idle_threshold")
    if (
        snapshot.user_idle_seconds is None
        or not math.isfinite(snapshot.user_idle_seconds)
        or snapshot.user_idle_seconds < 0
    ):
        reasons.append("user_idle_unverified")
    elif snapshot.user_idle_seconds < snapshot.threshold_seconds:
        reasons.append("user_active_or_idle_window_not_elapsed")

    if not snapshot.coverage_complete:
        reasons.append("workload_coverage_unverified")
    if not snapshot.scheduled_work_safe:
        reasons.append("scheduled_work_safety_unverified")
    if snapshot.active_sleep_inhibitors:
        reasons.append("sleep_inhibitor_present")

    seen_ids: set[str] = set()
    for item in snapshot.work_items:
        if not item.identifier or item.identifier in seen_ids:
            reasons.append("invalid_or_duplicate_work_identifier")
        seen_ids.add(item.identifier)
        if not isinstance(item.state, WorkState):
            reasons.append("unrecognised_work_state")
        elif item.state in (WorkState.ACTIVE, WorkState.WAITING_EXTERNAL):
            reasons.append(f"work_busy:{item.identifier}")
        elif item.state is WorkState.UNKNOWN:
            reasons.append(f"work_unknown:{item.identifier}")
        if item.active_children:
            reasons.append(f"child_work_active:{item.identifier}")

    return Verdict(eligible=not reasons, reasons=tuple(reasons))

"""Pure in-memory task leases for future supervised workload adapters.

No service runs, no network traffic is generated, and no state is written
to disk. A lease expiring never means the workload has completed.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re

from .policy import WorkItem, WorkState


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)
_MAX_TTL_SECONDS = 3600.0


@dataclass(frozen=True)
class TaskLease:
    """One trusted controller-observed lifecycle record."""

    source: str
    task_id: str
    state: WorkState
    active_children: bool
    observed_at: float
    expires_at: float


class WorkRegistry:
    """Tracks multiple independent workloads without claiming full coverage.

    The daemon must timestamp its *own receipt* of a trusted adapter event
    using one monotonic clock. Adapter supplied timestamps are not trusted.
    Restarts lose this in-memory registry and MUST invalidate coverage.
    """

    def __init__(self) -> None:
        self._leases: dict[tuple[str, str], TaskLease] = {}

    def report(
        self,
        source: str,
        task_id: str,
        state: WorkState,
        *,
        now: float,
        ttl_seconds: float,
        active_children: bool = False,
    ) -> None:
        """Register/refresh a task's state; reject malformed and stale updates."""
        if not isinstance(source, str) or not _ID.fullmatch(source):
            raise ValueError("invalid_source")
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            raise ValueError("invalid_task_id")
        if not isinstance(state, WorkState):
            raise ValueError("invalid_work_state")
        if not isinstance(active_children, bool):
            raise ValueError("invalid_child_flag")
        if not isinstance(now, (int, float)) or not math.isfinite(now) or now < 0:
            raise ValueError("invalid_monotonic_time")
        if (
            not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(ttl_seconds)
            or not 0 < ttl_seconds <= _MAX_TTL_SECONDS
        ):
            raise ValueError("invalid_lease_ttl")
        key = (source, task_id)
        previous = self._leases.get(key)
        if previous is not None and now < previous.observed_at:
            raise ValueError("out_of_order_event")
        self._leases[key] = TaskLease(
            source=source,
            task_id=task_id,
            state=state,
            active_children=active_children,
            observed_at=float(now),
            expires_at=float(now + ttl_seconds),
        )

    def observed_work(self, *, now: float) -> tuple[WorkItem, ...]:
        """Return all observed tasks; expired or time-reversed become UNKNOWN."""
        if not isinstance(now, (int, float)) or not math.isfinite(now) or now < 0:
            raise ValueError("invalid_monotonic_time")
        result: list[WorkItem] = []
        for key, lease in sorted(self._leases.items()):
            stale = now < lease.observed_at or now >= lease.expires_at
            result.append(
                WorkItem(
                    identifier=f"{key[0]}:{key[1]}",
                    state=WorkState.UNKNOWN if stale else lease.state,
                    active_children=lease.active_children,
                )
            )
        return tuple(result)

    def count(self) -> int:
        return len(self._leases)

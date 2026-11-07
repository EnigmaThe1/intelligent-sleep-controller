"""Explicit process-to-task observation registry (monitor-only foundation).

Enrollment is local/in-memory and is NOT authentication. A caller must already
have separately verified task ownership before using these observations.
A process exit, reparented child, missing scan or PID reuse NEVER proves task
completion. This module does not execute workloads, signal processes, access
private files or authorize sleep. No persistence or output of identifiers.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

from .policy import WorkState
from .process_lifecycle import ProcessIdentity, ProcessLineage

_TOKEN = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_MAX_TASKS = 256
_MAX_OBSERVATION_GAP_SECONDS = 90.0


@dataclass(frozen=True)
class BoundTask:
    """Private, ephemeral binding. Never returned from aggregate inspection."""

    owner_token: str
    task_token: str
    root_pid: int
    root_start_ticks: int
    root_name: str
    child_identity_seen: bool = False
    uncertain: bool = False  # Visible identity is activity evidence, not owner authentication.
    last_observed: float | None = None


@dataclass(frozen=True)
class BindingSummary:
    registered: int
    root_visible: int
    root_missing_or_replaced: int
    child_observed: int
    unknown: int
    observed_at: float | None
    coverage_verified: bool = False


def _valid_identity(identity: ProcessIdentity) -> bool:
    return (
        isinstance(identity, ProcessIdentity)
        and type(identity.pid) is int and identity.pid > 0
        and type(identity.start_ticks) is int and identity.start_ticks > 0
        and isinstance(identity.name, str) and 0 < len(identity.name) <= 15
        and type(identity.parent_pid) is int and identity.parent_pid >= 0
    )


class TaskProcessBindings:
    """Bounded opt-in observation, explicitly *not* a trusted task owner."""

    def __init__(self) -> None:
        self._tasks: dict[tuple[str, str], BoundTask] = {}
        self._history_lost = False
        self._last_scan: float | None = None
        self._latest_visible = 0
        self._latest_missing = 0
        self._latest_children = 0

    def register(
        self, owner_token: str, task_token: str, root: ProcessIdentity,
    ) -> None:
        if (
            not isinstance(owner_token, str) or not _TOKEN.fullmatch(owner_token)
            or not isinstance(task_token, str) or not _TOKEN.fullmatch(task_token)
            or not _valid_identity(root)
        ):
            raise ValueError("invalid_task_binding")
        key = (owner_token, task_token)
        if key in self._tasks:
            raise ValueError("task_binding_already_exists")
        # An identical kernel identity cannot be exclusively attributed to
        # different tasks without independent trusted-owner verification.
        if any(
            (item.root_pid, item.root_start_ticks)
            == (root.pid, root.start_ticks)
            for item in self._tasks.values()
        ):
            self._history_lost = True
            raise ValueError("process_identity_already_bound")
        if len(self._tasks) >= _MAX_TASKS:
            self._history_lost = True
            raise ValueError("task_binding_capacity_exceeded")
        # Never silently replace a binding, even if its PID has been reused.
        self._tasks[key] = BoundTask(
            owner_token, task_token, root.pid, root.start_ticks, root.name,
        )

    def observe(self, snapshot: ProcessLineage | None, *, now: float) -> BindingSummary:
        if (type(now) not in (int, float)
                or not math.isfinite(now) or now < 0):
            raise ValueError("invalid_observation_time")
        if self._last_scan is not None:
            # A missed interval can conceal children spawning and detaching.
            # Retain UNKNOWN even if the root remains visible afterward.
            if now - self._last_scan > _MAX_OBSERVATION_GAP_SECONDS:
                self._history_lost = True
            if now < self._last_scan:
                self._history_lost = True
                snapshot = None
        self._last_scan = float(now)
        if snapshot is None or not isinstance(snapshot, ProcessLineage):
            self._history_lost = True
            self._latest_visible = 0
            self._latest_missing = len(self._tasks)
            self._latest_children = 0
            return self.summary()

        processes = snapshot.tracked
        # A partial scan cannot certify whether detached child work exists.
        if (snapshot.coverage_verified or snapshot.vanished_during_scan
                or any(not _valid_identity(item) for item in processes)
                or len({item.pid for item in processes}) != len(processes)):
            self._history_lost = True

        by_pid = {item.pid: item for item in processes}
        visible = missing = children = 0
        for key, task in tuple(self._tasks.items()):
            root = by_pid.get(task.root_pid)
            matches = (
                root is not None and root.start_ticks == task.root_start_ticks
                and root.name == task.root_name
            )
            if not matches:
                missing += 1
                self._tasks[key] = BoundTask(
                    task.owner_token, task.task_token, task.root_pid,
                    task.root_start_ticks, task.root_name,
                    child_identity_seen=task.child_identity_seen,
                    uncertain=True, last_observed=float(now),
                )
                continue
            visible += 1
            # Snapshot descendant set is based on a partial same-UID inventory.
            # It cannot assert absence of detached or cross-UID child work.
            def belongs_to_root(node: ProcessIdentity) -> bool:
                # Include grandchildren without following an unbounded or
                # cyclic parent chain in a malformed/partial snapshot.
                parent = node.parent_pid
                seen: set[int] = {node.pid}
                while parent in by_pid and parent not in seen:
                    if parent == task.root_pid:
                        return True
                    seen.add(parent)
                    parent = by_pid[parent].parent_pid
                return False

            has_child = any(
                node.pid != task.root_pid and belongs_to_root(node)
                for node in snapshot.descendants
            )
            children += int(has_child)
            self._tasks[key] = BoundTask(
                task.owner_token, task.task_token, task.root_pid,
                task.root_start_ticks, task.root_name,
                child_identity_seen=task.child_identity_seen or has_child,
                # A previously observed child disappearing cannot be
                # interpreted as finished; detached children may persist.
                uncertain=(task.uncertain or self._history_lost
                           or (task.child_identity_seen and not has_child)),
                last_observed=float(now),
            )
        self._latest_visible, self._latest_missing = visible, missing
        self._latest_children = children
        return self.summary()

    def summary(self) -> BindingSummary:
        """Aggregate-only: no PIDs, task tokens, process names, or paths."""
        return BindingSummary(
            registered=len(self._tasks),
            root_visible=self._latest_visible,
            root_missing_or_replaced=self._latest_missing,
            child_observed=self._latest_children,
            unknown=(
                sum(int(task.uncertain or self._last_scan is None)
                    for task in self._tasks.values())
                + int(self._history_lost)
            ),
            observed_at=self._last_scan,
        )

    @property
    def history_lost(self) -> bool:
        return self._history_lost

    def states(self) -> tuple[WorkState, ...]:
        """Visible roots are busy; every other binding remains UNKNOWN."""
        if self._history_lost:
            return (WorkState.UNKNOWN,) * max(1, len(self._tasks))
        return tuple(
            WorkState.ACTIVE if self._last_scan is not None
            and task.last_observed == self._last_scan
            and not task.uncertain else WorkState.UNKNOWN
            for task in self._tasks.values()
        )

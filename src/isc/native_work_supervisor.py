"""Opt-in read-only workload supervision joining existing ISC evidence.

This is an IN-MEMORY MONITOR component, not a task-owner authenticator.
Registration never conveys trust. The entire registered process group is
checked conservatively against an explicitly named systemd service. Missing
lineage, detached children, conflicting service activations, or scanner errors
remain sticky UNKNOWN. Public summaries contain aggregate counts only.

There is no permission to suspend, no installed service, and no I/O beyond
read-only native observers supplied to this class.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable

from .cgroup_membership import CgroupMembership
from .policy import WorkState
from .process_lifecycle import ProcessIdentity, ProcessLineage, read_process_lineage
from .service_process_correlation import ServiceProcessEvidence, _SERVICE
from .service_process_observer import observe_service_process
from .native_service_enrollment import discover_native_service_process, NativeServiceProcess
from .task_process_bindings import TaskProcessBindings

_MAX_JOBS = 256


@dataclass(frozen=True)
class SupervisionSummary:
    registered: int
    observed_members: int
    unknown: int
    observed_at: float | None
    coverage_verified: bool = False
    task_owner_authenticated: bool = False


class NativeWorkSupervisor:
    """Explicit opt-in, strictly non-authoritative continuous observation."""

    def __init__(
        self,
        *,
        lineage_scanner: Callable[[], ProcessLineage | None] = read_process_lineage,
        membership_checker: Callable[
            [str, ProcessIdentity, str], ServiceProcessEvidence
        ] | None = None,
    ) -> None:
        if not callable(lineage_scanner) or (
            membership_checker is not None and not callable(membership_checker)
        ):
            raise ValueError("invalid_supervision_observer")
        self._scanner = lineage_scanner
        self._checker = membership_checker or self._check_membership
        self._bindings = TaskProcessBindings()
        self._scopes: dict[tuple[str, str], tuple[str, str]] = {}
        self._sticky_unknown: set[tuple[str, str]] = set()
        self._last_time: float | None = None
        self._observed_members = 0

    @staticmethod
    def _check_membership(
        service: str, process: ProcessIdentity, scope: str,
    ) -> ServiceProcessEvidence:
        return observe_service_process(service, process, scope=scope).evidence

    def register(
        self, owner_token: str, task_token: str, root: ProcessIdentity,
        *, service: str, scope: str,
    ) -> None:
        if (not isinstance(service, str) or _SERVICE.fullmatch(service) is None
                or scope not in ("--system", "--user")
                or len(self._scopes) >= _MAX_JOBS):
            raise ValueError("invalid_supervised_work_registration")
        self._bindings.register(owner_token, task_token, root)
        self._scopes[(owner_token, task_token)] = (service, scope)

    def register_native_service(
        self, owner_token: str, task_token: str, *, service: str,
        scope: str,
        discovery: Callable[..., NativeServiceProcess | None]
        = discover_native_service_process,
    ) -> bool:
        """Enroll only a process independently verified from systemd.

        This method has no socket or CLI route. A successful native
        service-to-process match is NOT proof of application task ownership.
        A failed check performs no registration and never clears uncertainty.
        """
        if not callable(discovery):
            return False
        try:
            evidence = discovery(service, scope=scope)
            if (not isinstance(evidence, NativeServiceProcess)
                    or evidence.coverage_verified
                    or evidence.task_owner_authenticated
                    or evidence.service != service or evidence.scope != scope):
                return False
            self.register(
                owner_token, task_token, evidence.identity,
                service=service, scope=scope,
            )
            return True
        except (OSError, TypeError, ValueError, RuntimeError):
            return False

    def observe(self, *, now: float) -> SupervisionSummary:
        """Acquire a fresh scan and evaluate registered workloads."""
        if (type(now) not in (int, float) or not math.isfinite(now) or now < 0):
            raise ValueError("invalid_supervision_time")
        snapshot: ProcessLineage | None
        try:
            snapshot = self._scanner()
        except Exception:
            snapshot = None
        return self.observe_snapshot(snapshot, now=now)

    def observe_snapshot(
        self, snapshot: ProcessLineage | None, *, now: float,
    ) -> SupervisionSummary:
        """Reuse the running process census without a second scan.

        Called only by in-process MONITOR code. Caller registration remains
        explicitly opt-in, never authenticated by an incoming hook payload.
        """
        if (type(now) not in (int, float) or not math.isfinite(now) or now < 0):
            raise ValueError("invalid_supervision_time")
        self._bindings.observe(snapshot, now=now)
        self._last_time = float(now)
        self._observed_members = 0
        if snapshot is None or self._bindings.history_lost:
            self._sticky_unknown.update(self._scopes)
            return self.summary()

        by_pid = {p.pid: p for p in snapshot.tracked}
        # The registry already conservatively detects vanished or replaced
        # children. We must also check placement for every visible descendant.
        for key, task in self._bindings._tasks.items():
            if key in self._sticky_unknown or task.uncertain:
                self._sticky_unknown.add(key)
                continue
            service, scope = self._scopes[key]
            identities = [by_pid.get(task.root_pid)]
            identities.extend(
                by_pid.get(pid) for pid, _, _, _ in task.child_identities
            )
            if any(p is None for p in identities):
                self._sticky_unknown.add(key)
                continue
            try:
                evidence = tuple(
                    self._checker(service, process, scope)
                    for process in identities
                )
            except Exception:
                self._sticky_unknown.add(key)
                continue
            if not evidence or any(
                value is not ServiceProcessEvidence.OBSERVED_MEMBER
                for value in evidence
            ):
                self._sticky_unknown.add(key)
                continue
            self._observed_members += 1
        return self.summary()

    def summary(self) -> SupervisionSummary:
        states = self._bindings.states()
        # Never interpret an unobserved/changed descendant as completed.
        uncertain = sum(
            key in self._sticky_unknown or state is not WorkState.ACTIVE
            for key, state in zip(self._scopes, states)
        )
        if self._bindings.history_lost:
            uncertain = len(self._scopes)
        return SupervisionSummary(
            registered=len(self._scopes), observed_members=self._observed_members,
            unknown=uncertain, observed_at=self._last_time,
        )

    def states(self) -> tuple[WorkState, ...]:
        """Busy evidence or UNKNOWN only. Never READY or COMPLETED."""
        if self._bindings.history_lost:
            return (WorkState.UNKNOWN,) * max(1, len(self._scopes))
        return tuple(
            WorkState.UNKNOWN if key in self._sticky_unknown else state
            for key, state in zip(self._scopes, self._bindings.states())
        )

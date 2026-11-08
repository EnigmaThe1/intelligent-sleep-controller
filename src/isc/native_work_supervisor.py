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
import os
import math
from typing import Callable

from .cgroup_membership import CgroupMembership
from .policy import WorkState
from .process_lifecycle import ProcessIdentity, ProcessLineage, read_process_lineage
from .service_process_correlation import ServiceProcessEvidence, _SERVICE
from .service_process_observer import observe_service_process
from .native_service_enrollment import discover_native_service_process, NativeServiceProcess
from .cgroup_subtree import CgroupSubtreeSummary, observe_cgroup_subtree
from .task_process_bindings import TaskProcessBindings
from .events import LifecycleEvent
from .native_task_lifecycle import NativeLifecycleCorrelator, TaskLifecyclePulse
from .linux_peer_process import PeerProcessEvidence

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
        subtree_checker: Callable[[str], CgroupSubtreeSummary]
        = observe_cgroup_subtree,
    ) -> None:
        if not callable(lineage_scanner) or (
            membership_checker is not None and not callable(membership_checker)
        ) or not callable(subtree_checker):
            raise ValueError("invalid_supervision_observer")
        self._scanner = lineage_scanner
        self._checker = membership_checker or self._check_membership
        self._subtree_checker = subtree_checker
        self._bindings = TaskProcessBindings()
        self._scopes: dict[tuple[str, str], tuple[str, str]] = {}
        # Only native enrollments have an independently verified activation pin.
        # These private records are never published or persisted.
        self._native_pins: dict[
            tuple[str, str],
            tuple[NativeServiceProcess, Callable[..., NativeServiceProcess | None]],
        ] = {}
        self._sticky_unknown: set[tuple[str, str]] = set()
        self._last_time: float | None = None
        self._observed_members = 0
        self._observed_keys: set[tuple[str, str]] = set()
        self._lifecycle = NativeLifecycleCorrelator()

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
            if (not self._valid_native_evidence(evidence)
                    or evidence.service != service or evidence.scope != scope):
                return False
            self.register(
                owner_token, task_token, evidence.identity,
                service=service, scope=scope,
            )
            self._native_pins[(owner_token, task_token)] = (evidence, discovery)
            return True
        except (OSError, TypeError, ValueError, RuntimeError):
            return False

    @staticmethod
    def _valid_native_evidence(evidence: object) -> bool:
        """Validate native evidence shape, not owner or task authority."""
        from .service_process_correlation import _ID
        from .timer_service_tasks import _valid_cgroup
        if not isinstance(evidence, NativeServiceProcess):
            return False
        identity = evidence.identity
        return (
            evidence.coverage_verified is False
            and evidence.task_owner_authenticated is False
            and isinstance(evidence.service, str)
            and _SERVICE.fullmatch(evidence.service) is not None
            and evidence.scope in ("--user", "--system")
            and isinstance(evidence.invocation, str)
            and _ID.fullmatch(evidence.invocation) is not None
            and isinstance(evidence.cgroup, str)
            and evidence.cgroup.startswith("/")
            and evidence.cgroup != "/"
            and _valid_cgroup(evidence.cgroup)
            and "//" not in evidence.cgroup
            and "/../" not in evidence.cgroup
            and not evidence.cgroup.endswith("/..")
            and isinstance(identity, ProcessIdentity)
            and type(identity.pid) is int and identity.pid > 0
            and type(identity.start_ticks) is int and identity.start_ticks > 0
            and isinstance(identity.name, str)
            and 0 < len(identity.name) <= 15
        )

    @staticmethod
    def _same_native_activation(
        earlier: NativeServiceProcess, current: NativeServiceProcess,
    ) -> bool:
        """Service restart or process replacement is sticky UNKNOWN."""
        return (
            NativeWorkSupervisor._valid_native_evidence(current)
            and earlier.service == current.service
            and earlier.scope == current.scope
            and earlier.invocation.lower() == current.invocation.lower()
            and earlier.cgroup == current.cgroup
            and earlier.identity == current.identity
        )

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
        self._observed_keys.clear()
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
            if key in self._native_pins:
                pin, discovery = self._native_pins[key]
                try:
                    live = discovery(service, scope=scope)
                except Exception:
                    live = None
                if (not isinstance(live, NativeServiceProcess)
                        or not self._same_native_activation(pin, live)):
                    self._sticky_unknown.add(key)
                    continue
            identities = [by_pid.get(task.root_pid)]
            identities.extend(
                by_pid.get(pid) for pid, _, _, _ in task.child_identities
            )
            if any(p is None for p in identities):
                self._sticky_unknown.add(key)
                continue
            # A detached process can remain in the native service cgroup
            # after reparenting and disappear from the tracked PPID lineage.
            # Compare counts conservatively: extra processes are unaccounted
            # work, not harmless noise or evidence of task completion.
            if key in self._native_pins:
                pinned, _ = self._native_pins[key]
                try:
                    subtree = self._subtree_checker(pinned.cgroup)
                except Exception:
                    subtree = None
                if (not isinstance(subtree, CgroupSubtreeSummary)
                        or subtree.coverage_verified
                        or subtree.task_owner_authenticated
                        or subtree.unknown is not False
                        or type(subtree.observed_processes) is not int
                        or subtree.observed_processes != len(identities)):
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
            self._observed_keys.add(key)
        return self.summary()

    def observe_lifecycle(
        self, owner_token: str, task_token: str, event: LifecycleEvent,
        *, now: float,
    ) -> bool:
        """Opt-in in-process lifecycle claim for an enrolled task only.

        No socket route. No successful completion/human-idle inference and no
        ownership authentication, even when a native service is observed.
        """
        return self._lifecycle.report(
            owner_token, task_token, event, now=now,
            enrolled=(owner_token, task_token) in self._scopes,
        )

    def observe_peer_lifecycle(
        self, owner_token: str, task_token: str, event: LifecycleEvent,
        peer: PeerProcessEvidence | None, *, now: float,
    ) -> bool:
        """Future trusted transport: accept only live pinned native peers.

        A peer must match the systemd-origin pinned MainPID and most recent
        native supervision observation, and the service identity must still
        be independently verified. In-process callers cannot create enrollment
        or grant ownership authority. No public socket route is installed.
        """
        key = (owner_token, task_token)
        if (
            not isinstance(peer, PeerProcessEvidence)
            or peer.authenticated_task_owner is not False
            or peer.coverage_verified is not False
            or type(peer.uid) is not int or peer.uid != os.getuid()
            or key not in self._native_pins
            or key not in self._observed_keys
            or key in self._sticky_unknown
            or type(now) not in (int, float)
            or not math.isfinite(now)
            or self._last_time is None
            or now < self._last_time
            or now - self._last_time > 60.0
        ):
            return False
        pinned, discovery = self._native_pins[key]
        if peer.identity != pinned.identity:
            return False
        service, scope = self._scopes[key]
        try:
            live = discovery(service, scope=scope)
            if not (
                isinstance(live, NativeServiceProcess)
                and self._same_native_activation(pinned, live)
                and self._checker(service, peer.identity, scope)
                    is ServiceProcessEvidence.OBSERVED_MEMBER
            ):
                self._sticky_unknown.add(key)
                return False
        except Exception:
            self._sticky_unknown.add(key)
            return False
        return self.observe_lifecycle(
            owner_token, task_token, event, now=now,
        )

    def lifecycle_summary(self, *, now: float) -> TaskLifecyclePulse:
        """Only validated native process memberships can reinforce busy claims."""
        fresh = (
            self._last_time is not None
            and now >= self._last_time
            and now - self._last_time <= 60.0
        )
        verified = frozenset(
            key for key in self._observed_keys
            if fresh and key not in self._sticky_unknown
        )
        return self._lifecycle.aggregate(
            tuple(self._scopes), verified, now=now,
        )

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

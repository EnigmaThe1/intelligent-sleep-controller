"""Vendor-independent, side-effect-free lifecycle event interpretation.

The input MUST come from a trusted, supervised adapter. This module does not
read private prompts, install vendor hooks, open sockets, or authorize suspend.
A stopped turn or a permission request does not establish safe human idleness.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .policy import WorkState
from .registry import WorkRegistry


class EventKind(str, Enum):
    STARTED = "started"
    ACTIVITY = "activity"
    WAITING_EXTERNAL = "waiting_external"
    PERMISSION_REQUESTED = "permission_requested"
    HUMAN_INPUT_CONFIRMED = "human_input_confirmed"
    FINISHED = "finished"
    STOPPED = "stopped"
    CHILD_STARTED = "child_started"
    CHILD_QUIESCED = "child_quiesced"


@dataclass(frozen=True)
class LifecycleEvent:
    source: str
    task_id: str
    kind: EventKind
    # Adapter-confirmed quiescence of all child work, not just one child.
    children_quiescent: bool = False


@dataclass(frozen=True)
class Interpretation:
    state: WorkState
    active_children: bool


def interpret(event: LifecycleEvent) -> Interpretation:
    """Return a conservative state for a trusted lifecycle event.

    Permission requests can be automatically answered. A Stop hook can run
    before follow-up work or reflect a turn boundary. Neither is proof that
    the entire job is waiting for the user.
    """
    if not isinstance(event, LifecycleEvent) or not isinstance(event.kind, EventKind):
        raise ValueError("unrecognised_lifecycle_event")
    if not isinstance(event.children_quiescent, bool):
        raise ValueError("invalid_children_quiescent")
    if event.kind in (EventKind.STARTED, EventKind.ACTIVITY):
        return Interpretation(WorkState.ACTIVE, False)
    if event.kind == EventKind.WAITING_EXTERNAL:
        return Interpretation(WorkState.WAITING_EXTERNAL, False)
    if event.kind == EventKind.CHILD_STARTED:
        return Interpretation(WorkState.ACTIVE, True)
    if event.kind == EventKind.HUMAN_INPUT_CONFIRMED:
        if event.children_quiescent:
            return Interpretation(WorkState.WAITING_HUMAN, False)
        return Interpretation(WorkState.UNKNOWN, True)
    if event.kind == EventKind.FINISHED:
        if event.children_quiescent:
            return Interpretation(WorkState.COMPLETED, False)
        return Interpretation(WorkState.UNKNOWN, True)
    if event.kind in (
        EventKind.PERMISSION_REQUESTED,
        EventKind.STOPPED,
        EventKind.CHILD_QUIESCED,
    ):
        return Interpretation(WorkState.UNKNOWN, False)
    raise ValueError("unrecognised_lifecycle_event")


def register_event(
    registry: WorkRegistry,
    event: LifecycleEvent,
    *,
    now: float,
    ttl_seconds: float,
) -> None:
    """Apply a trusted lifecycle event; caller supplies its own monotonic now.

    A privileged caller/adapter must authenticate ownership and track real
    child processes before using confirmed events. No such adapter exists yet.
    """
    if not isinstance(registry, WorkRegistry):
        raise TypeError("invalid_registry")
    decision = interpret(event)
    registry.report(
        event.source,
        event.task_id,
        decision.state,
        now=now,
        ttl_seconds=ttl_seconds,
        active_children=decision.active_children,
    )


# Optional mappings for *observations*, not proof of task completion.
# Event names are exact, and unknown names result in UNKNOWN.
# Applications are never required to be installed for ISC's core to work.
_HOOK_EVENTS: dict[tuple[str, str], EventKind] = {
    ("claude_code", "SessionStart"): EventKind.STOPPED,
    ("claude_code", "UserPromptSubmit"): EventKind.STARTED,
    ("claude_code", "PreToolUse"): EventKind.ACTIVITY,
    ("claude_code", "PostToolUse"): EventKind.ACTIVITY,
    ("claude_code", "PermissionRequest"): EventKind.PERMISSION_REQUESTED,
    ("claude_code", "SubagentStart"): EventKind.CHILD_STARTED,
    ("claude_code", "SubagentStop"): EventKind.CHILD_QUIESCED,
    ("claude_code", "Stop"): EventKind.STOPPED,
    ("claude_code", "SessionEnd"): EventKind.STOPPED,
    ("codex", "SessionStart"): EventKind.STOPPED,
    ("codex", "UserPromptSubmit"): EventKind.STARTED,
    ("codex", "PreToolUse"): EventKind.ACTIVITY,
    ("codex", "PostToolUse"): EventKind.ACTIVITY,
    ("codex", "PermissionRequest"): EventKind.PERMISSION_REQUESTED,
    ("codex", "SubagentStart"): EventKind.CHILD_STARTED,
    ("codex", "SubagentStop"): EventKind.CHILD_QUIESCED,
    ("codex", "Stop"): EventKind.STOPPED,
    ("codex", "Interrupt"): EventKind.STOPPED,
    ("codex", "SessionEnd"): EventKind.STOPPED,
    ("codex_app_server", "turn/started"): EventKind.STARTED,
    ("codex_app_server", "turn/completed"): EventKind.STOPPED,
    ("codex_app_server", "item/started"): EventKind.ACTIVITY,
    ("codex_app_server", "item/completed"): EventKind.ACTIVITY,
}


def hook_observation(
    source: str,
    event_name: str,
    task_id: str,
) -> LifecycleEvent:
    """Normalise a *trusted* hook name, defaulting to UNKNOWN semantics.

    Unknown hook names, including from newer releases, cannot make the
    computer sleep-eligible.
    """
    if not isinstance(source, str) or not isinstance(event_name, str):
        raise ValueError("invalid_hook_identity")
    kind = _HOOK_EVENTS.get((source, event_name), EventKind.STOPPED)
    return LifecycleEvent(source=source, task_id=task_id, kind=kind)

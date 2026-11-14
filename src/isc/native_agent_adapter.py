"""Opt-in, minimal Claude Code/Codex hook to native lifecycle translation.

This adapter has NO network, process, environment, file or power side effects.
A hook payload is sender-claimed even if it parses correctly. The caller must
have independently bound a session to a native service and must use ISC's
peer-verified, ordered native observation path. No application is installed
or configured automatically; no hook event proves task completion.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .events import EventKind, LifecycleEvent
from .hook_payload import HookObservation
from .policy import WorkState

# Allow only documented, already understood events. Vendor changes cannot
# accidentally make a task appear finished or waiting safely for a person.
_AGENT_EVENTS: dict[str, dict[str, EventKind]] = {
    "claude_code": {
        "SessionStart": EventKind.STOPPED,
        "UserPromptSubmit": EventKind.STARTED,
        "PreToolUse": EventKind.ACTIVITY,
        "PostToolUse": EventKind.ACTIVITY,
        "SubagentStart": EventKind.CHILD_STARTED,
        "SubagentStop": EventKind.CHILD_QUIESCED,
        "Stop": EventKind.STOPPED,
        "SessionEnd": EventKind.STOPPED,
        "PermissionRequest": EventKind.PERMISSION_REQUESTED,
    },
    "codex": {
        "SessionStart": EventKind.STOPPED,
        "UserPromptSubmit": EventKind.STARTED,
        "PreToolUse": EventKind.ACTIVITY,
        "PostToolUse": EventKind.ACTIVITY,
        "SubagentStart": EventKind.CHILD_STARTED,
        "SubagentStop": EventKind.CHILD_QUIESCED,
        "Stop": EventKind.STOPPED,
        "Interrupt": EventKind.STOPPED,
        "SessionEnd": EventKind.STOPPED,
        "PermissionRequest": EventKind.PERMISSION_REQUESTED,
    },
}
_FINGERPRINT = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)


@dataclass(frozen=True)
class AgentSessionBinding:
    """Private, opt-in session association, never proof of origin or owner.

    Owner and task tokens are internal identifiers supplied by a privileged
    in-process caller, never inferred from vendor hook JSON.
    """
    source: str
    session_fingerprint: str
    owner_token: str
    task_token: str


def translate_agent_hook(
    binding: AgentSessionBinding, observation: HookObservation,
) -> LifecycleEvent | None:
    """Translate only consistent metadata from the bound vendor session.

    Unknown, mismatched or malformed events return None (UNKNOWN), not a
    synthetic finish/idle event. Raw session IDs and private prompts are
    never accepted or stored by this adapter.
    """
    if (not isinstance(binding, AgentSessionBinding)
            or not isinstance(observation, HookObservation)
            or binding.source not in _AGENT_EVENTS
            or type(binding.session_fingerprint) is not str
            or _FINGERPRINT.fullmatch(binding.session_fingerprint) is None
            or type(binding.owner_token) is not str
            or _TOKEN.fullmatch(binding.owner_token) is None
            or type(binding.task_token) is not str
            or _TOKEN.fullmatch(binding.task_token) is None
            or observation.source != binding.source
            or observation.session_fingerprint != binding.session_fingerprint
            or observation.authoritative is not False
            or type(observation.event_name) is not str):
        return None
    kind = _AGENT_EVENTS[binding.source].get(observation.event_name)
    if kind is None:
        return None
    # Hook parsers are not an authority: refuse a claimed state that is
    # inconsistent with the existing strict event interpretation.
    expected = {
        EventKind.STARTED: WorkState.ACTIVE,
        EventKind.ACTIVITY: WorkState.ACTIVE,
        EventKind.CHILD_STARTED: WorkState.ACTIVE,
        EventKind.CHILD_QUIESCED: WorkState.UNKNOWN,
        EventKind.STOPPED: WorkState.UNKNOWN,
        EventKind.PERMISSION_REQUESTED: WorkState.UNKNOWN,
    }
    if observation.state is not expected.get(kind):
        return None
    return LifecycleEvent(
        source=binding.owner_token,
        task_id=binding.task_token,
        kind=kind,
        children_quiescent=False,
    )

"""Privacy-minimal parser for optional Claude Code/Codex command-hook input.

Parse only documented common fields, never transcripts, prompts, tool input,
environment variables, command output, or session paths. Pure observation:
there is NO hook installation, receiver, daemon, or suspend side effect.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import re
from typing import BinaryIO

from .events import hook_observation, interpret
from .policy import WorkState

MAX_HOOK_INPUT_BYTES = 65536
MAX_SESSION_ID_BYTES = 512
_EVENT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z", re.ASCII)
SUPPORTED_SOURCES = frozenset(("claude_code", "codex"))


@dataclass(frozen=True)
class HookObservation:
    """Only safe metadata survives parsing; no full input JSON is stored."""

    source: str
    event_name: str
    session_fingerprint: str
    state: WorkState

    @property
    def authoritative(self) -> bool:
        """Raw command-hook data does NOT prove origin, child state, or coverage."""
        return False


def parse_hook_payload(source: str, payload: bytes) -> HookObservation | None:
    """Normalise a hook JSON object. None means UNKNOWN/unusable observation.

    The fingerprint prevents storing or printing raw session identifiers; it
    is NOT authentication and should not be treated as an ownership proof.
    """
    if source not in SUPPORTED_SOURCES:
        return None
    if not isinstance(payload, bytes) or len(payload) > MAX_HOOK_INPUT_BYTES:
        return None
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(obj, dict):
        return None
    event_name = obj.get("hook_event_name")
    session_id = obj.get("session_id")
    if (
        not isinstance(event_name, str)
        or _EVENT_NAME.fullmatch(event_name) is None
        or not isinstance(session_id, str)
        or not session_id
        or len(session_id.encode("utf-8")) > MAX_SESSION_ID_BYTES
        or any(ord(char) < 32 or ord(char) == 127 for char in session_id)
    ):
        return None
    state = interpret(hook_observation(source, event_name, "opaque")).state
    # Never include the raw ID in a persisted record or output.
    fingerprint = hashlib.sha256(
        source.encode("ascii") + b"\0" + session_id.encode("utf-8")
    ).hexdigest()[:32]
    return HookObservation(
        source=source,
        event_name=event_name,
        session_fingerprint=fingerprint,
        state=state,
    )


def read_hook_payload(
    source: str, stream: BinaryIO
) -> HookObservation | None:
    """Bounded read from stdin (or another binary stream); never executes hooks."""
    try:
        payload = stream.read(MAX_HOOK_INPUT_BYTES + 1)
    except (OSError, ValueError, io.UnsupportedOperation):
        return None
    return parse_hook_payload(source, payload)

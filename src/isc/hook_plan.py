"""Read-only, privacy-preserving plan for OPTIONAL coding-agent hook integration.

Never modifies an agent configuration, executes a hook, imports an agent, or
starts a monitor. This is a precursor to a separate audited installer.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import stat
import sys

MAX_CONFIG_BYTES = 2 * 1024 * 1024
SUPPORTED_HOOK_SOURCES = ("claude_code", "codex")
# A narrow, supported set; avoid permission hooks until supervision is tested.
EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "SessionEnd",
)


@dataclass(frozen=True)
class HookPlan:
    source: str
    target: Path
    configured_events: tuple[str, ...]
    already_installed: tuple[str, ...]
    proposed_events: tuple[str, ...]
    read_only: bool = True


class UnsafeHookConfiguration(ValueError):
    """No configuration content is exposed in errors or printed summaries."""


def hook_command(source: str) -> str:
    """Use absolute interpreter/source paths, independent of agent cwd."""
    if source not in SUPPORTED_HOOK_SOURCES:
        raise ValueError("unsupported_source")
    src_root = Path(__file__).resolve().parents[1]
    return (
        "PYTHONDONTWRITEBYTECODE=1 "
        f"PYTHONPATH={shlex.quote(str(src_root))} "
        f"{shlex.quote(sys.executable)} -m isc send-hook "
        f"--source {shlex.quote(source)}"
    )


def _load_existing(target: Path) -> dict:
    try:
        st = target.lstat()
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or st.st_size > MAX_CONFIG_BYTES
        ):
            raise UnsafeHookConfiguration("unsafe_file_identity_or_size")
        with target.open("rb") as stream:
            payload = stream.read(MAX_CONFIG_BYTES + 1)
        if len(payload) > MAX_CONFIG_BYTES:
            raise UnsafeHookConfiguration("configuration_too_large")
        data = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, UnsafeHookConfiguration):
            raise
        raise UnsafeHookConfiguration("config_unavailable_or_invalid") from None
    if not isinstance(data, dict):
        raise UnsafeHookConfiguration("unexpected_root_type")
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        raise UnsafeHookConfiguration("unexpected_hooks_structure")
    for event, groups in hooks.items():
        if not isinstance(event, str) or not isinstance(groups, list):
            raise UnsafeHookConfiguration("unexpected_event_groups")
        for group in groups:
            if not isinstance(group, dict):
                raise UnsafeHookConfiguration("unexpected_hook_group")
            handlers = group.get("hooks")
            if not isinstance(handlers, list) or not all(
                isinstance(item, dict) for item in handlers
            ):
                raise UnsafeHookConfiguration("unexpected_handler_list")
    return data


def build_hook_plan(source: str, target: str | Path) -> HookPlan:
    """Inspect only; never mutate the target JSON or reveal hook commands."""
    if source not in SUPPORTED_HOOK_SOURCES:
        raise ValueError("unsupported_source")
    target = Path(target)
    data = _load_existing(target)
    hooks = data["hooks"]
    command = hook_command(source)
    configured = tuple(sorted(hooks))
    already: list[str] = []
    proposed: list[str] = []
    for event in EVENTS:
        groups = hooks.get(event, [])
        if any(
            item.get("type") == "command" and item.get("command") == command
            for group in groups
            for item in group["hooks"]
        ):
            already.append(event)
        else:
            proposed.append(event)
    return HookPlan(
        source=source,
        target=target,
        configured_events=configured,
        already_installed=tuple(already),
        proposed_events=tuple(proposed),
    )


def default_hook_targets(*, home: str | Path | None = None) -> tuple[tuple[str, Path], ...]:
    root = Path(home) if home is not None else Path.home()
    return (
        ("claude_code", root / ".claude" / "settings.json"),
        ("codex", root / ".codex" / "hooks.json"),
    )

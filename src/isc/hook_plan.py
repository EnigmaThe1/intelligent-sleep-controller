"""Read-only, privacy-preserving plan for OPTIONAL coding-agent hook integration.

Never modifies an agent configuration, executes a hook, imports an agent, or
starts a monitor. This is a precursor to a separate audited installer.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
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


_ABS_PATH = re.compile(r"/[A-Za-z0-9_/.-]+\Z", re.ASCII)


def _render_hook_command(source: str, interpreter: str, source_root: str) -> str:
    return (
        "PYTHONDONTWRITEBYTECODE=1 "
        f"PYTHONPATH={shlex.quote(source_root)} "
        f"{shlex.quote(interpreter)} -m isc send-hook "
        f"--source {shlex.quote(source)}"
    )


def hook_command(source: str) -> str:
    """Use absolute interpreter/source paths, independent of agent cwd."""
    if source not in SUPPORTED_HOOK_SOURCES:
        raise ValueError("unsupported_source")
    src_root = Path(__file__).resolve().parents[1]
    return _render_hook_command(source, sys.executable, str(src_root))


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


def is_isc_handler(handler: object, source: str) -> bool:
    """Recognise the exact current or legacy ISC command without executing it.

    Interpreter/source paths may change across installs. Require a complete
    canonical command shape, not a substring or a claimed owner marker.
    Shell metacharacters and path-like inputs outside a strict whitelist
    are never treated as ISC-owned legacy handlers.
    """
    if source not in SUPPORTED_HOOK_SOURCES:
        return False
    if not isinstance(handler, dict) or handler.get("type") != "command":
        return False
    command = handler.get("command")
    if not isinstance(command, str):
        return False
    if command == hook_command(source):
        return True
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if len(parts) != 8:
        return False
    if parts[0] != "PYTHONDONTWRITEBYTECODE=1":
        return False
    if not parts[1].startswith("PYTHONPATH="):
        return False
    source_root = parts[1][len("PYTHONPATH="):]
    interpreter = parts[2]
    if (
        _ABS_PATH.fullmatch(source_root) is None
        or _ABS_PATH.fullmatch(interpreter) is None
        or parts[3:] != ["-m", "isc", "send-hook", "--source", source]
    ):
        return False
    return command == _render_hook_command(source, interpreter, source_root)


def group_has_isc_handler(group: dict, source: str) -> bool:
    return any(is_isc_handler(h, source) for h in group.get("hooks", ()))


def build_hook_plan(source: str, target: str | Path) -> HookPlan:
    """Inspect only; never mutate the target JSON or reveal hook commands."""
    if source not in SUPPORTED_HOOK_SOURCES:
        raise ValueError("unsupported_source")
    target = Path(target)
    data = _load_existing(target)
    hooks = data["hooks"]
    configured = tuple(sorted(hooks))
    already: list[str] = []
    proposed: list[str] = []
    for event in EVENTS:
        groups = hooks.get(event, [])
        if any(group_has_isc_handler(group, source) for group in groups):
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

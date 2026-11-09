"""Conservative read-only preflight for existing ISC coding-agent hook commands.

Inspects only handlers already recognised as ISC-owned. Never runs shell
commands, agent hooks, Python, applications, or subprocesses. Does not reveal
configuration content, command paths, secrets or private session metadata.
Passing means executable files appear present, NOT that a vendor executes
the hook or that task ownership is authenticated.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import stat

from .hook_plan import (
    EVENTS, UnsafeHookConfiguration, _load_existing, default_hook_targets,
    is_isc_handler,
)


@dataclass(frozen=True)
class HookPreflightResult:
    source: str
    state: str
    installed: int | None
    runnable: int | None
    coverage_verified: bool = False
    task_owner_authenticated: bool = False


def _runnable_isc_handler(handler: dict, source: str) -> bool:
    """Check static paths only. Never execute or follow application code."""
    if not is_isc_handler(handler, source):
        return False
    try:
        tokens = shlex.split(handler["command"])
        if len(tokens) != 8:
            return False
        root = Path(tokens[1].split("=", 1)[1])
        interpreter = Path(tokens[2])
        if not (root.is_absolute() and interpreter.is_absolute()):
            return False
        python_file = interpreter.stat()
        package_file = (root / "isc" / "__main__.py").stat()
        return (
            stat.S_ISREG(python_file.st_mode)
            and bool(python_file.st_mode & 0o111)
            and stat.S_ISREG(package_file.st_mode)
            and bool(package_file.st_mode & 0o444)
        )
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return False


def inspect_hook_preflight(
    targets: tuple[tuple[str, Path], ...] | None = None,
) -> tuple[HookPreflightResult, ...]:
    """Fail UNKNOWN on unavailable, malformed or inconsistent configs."""
    if targets is None:
        targets = default_hook_targets()
    results: list[HookPreflightResult] = []
    for source, target in targets:
        if source not in ("claude_code", "codex"):
            raise ValueError("unsupported_hook_source")
        try:
            data = _load_existing(Path(target))
            hooks = data["hooks"]
            installed = 0
            runnable = 0
            # Only exact ISC command handlers in supported events count.
            for name in EVENTS:
                for group in hooks.get(name, []):
                    for handler in group["hooks"]:
                        if is_isc_handler(handler, source):
                            installed += 1
                            if _runnable_isc_handler(handler, source):
                                runnable += 1
            state = (
                "NOT_CONFIGURED" if installed == 0
                else "PATHS_PRESENT" if runnable == installed
                else "PATHS_UNAVAILABLE"
            )
            results.append(HookPreflightResult(
                source, state, installed, runnable,
            ))
        except (OSError, ValueError, TypeError, KeyError, UnsafeHookConfiguration):
            results.append(HookPreflightResult(source, "UNKNOWN", None, None))
    return tuple(results)

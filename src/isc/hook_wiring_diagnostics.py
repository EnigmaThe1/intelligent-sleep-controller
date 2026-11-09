"""Read-only ISC hook wiring diagnostics without executing vendor hooks.

Counts verified ISC-owned command handlers in the configured event groups.
Checks their local executable/source paths exist and whether group matchers
could restrict delivery. This cannot establish agent support for a hook API,
the correct launch environment, a real invocation or authenticated ownership.
Never display config contents, commands, paths, or session data.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import stat
from typing import Callable

from .hook_plan import (
    EVENTS, SUPPORTED_HOOK_SOURCES, _load_existing, default_hook_targets,
    is_isc_handler, UnsafeHookConfiguration,
)


@dataclass(frozen=True)
class HookWiringDiagnostic:
    source: str
    state: str
    installed_events: int
    missing_events: int
    conditional_events: int
    missing_dependencies: int
    duplicate_events: int
    coverage_verified: bool = False
    task_owner_authenticated: bool = False


def _dependencies_available(handler: dict) -> bool:
    """Stat strict ISC command operands, never spawn or inspect contents."""
    command = handler.get("command")
    if not isinstance(command, str):
        return False
    try:
        parts = shlex.split(command)
        if len(parts) != 8 or not parts[1].startswith("PYTHONPATH="):
            return False
        root = Path(parts[1][len("PYTHONPATH="):])
        interpreter = Path(parts[2])
        for path, executable in ((root, False), (interpreter, True)):
            info = path.stat()
            if executable:
                if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
                    return False
            elif not stat.S_ISDIR(info.st_mode):
                return False
        package = root / "isc" / "__init__.py"
        info = package.stat()
        return stat.S_ISREG(info.st_mode) and os.access(package, os.R_OK)
    except (OSError, ValueError):
        return False


def inspect_hook_wiring(
    source: str, *, target: str | Path | None = None,
    config_reader: Callable[[Path], dict] = _load_existing,
    dependency_checker: Callable[[dict], bool] = _dependencies_available,
) -> HookWiringDiagnostic:
    """Inspect exactly one configuration; return codes, never private content."""
    if source not in SUPPORTED_HOOK_SOURCES:
        raise ValueError("unsupported_hook_source")
    if not callable(config_reader) or not callable(dependency_checker):
        raise ValueError("invalid_hook_wiring_reader")
    if target is None:
        target = dict(default_hook_targets())[source]
    try:
        data = config_reader(Path(target))
        hooks = data["hooks"]
        if not isinstance(hooks, dict):
            raise ValueError("invalid_hooks")
        found = missing = conditional = dependencies = duplicates = 0
        for name in EVENTS:
            groups = hooks.get(name, [])
            if not isinstance(groups, list):
                raise ValueError("invalid_event_groups")
            matching = []
            for group in groups:
                if not isinstance(group, dict):
                    raise ValueError("invalid_hook_group")
                handlers = group.get("hooks")
                if not isinstance(handlers, list):
                    raise ValueError("invalid_hook_handlers")
                for handler in handlers:
                    if is_isc_handler(handler, source):
                        matching.append((group, handler))
            if not matching:
                missing += 1
                continue
            found += 1
            if len(matching) > 1:
                duplicates += 1
            # An event matcher can be empty (match all) or otherwise
            # conditional. Non-string matchers are not trusted.
            if any(group.get("matcher", "") not in ("", None)
                   for group, _ in matching):
                conditional += 1
            if not all(dependency_checker(handler) is True
                       for _, handler in matching):
                dependencies += 1
        if found == len(EVENTS) and not (conditional or dependencies or duplicates):
            state = "CONFIGURED_PATHS_PRESENT"
        elif found:
            state = "INCOMPLETE_OR_CONDITIONAL"
        else:
            state = "NO_ISC_HANDLERS"
        return HookWiringDiagnostic(
            source, state, found, missing, conditional, dependencies,
            duplicates,
        )
    except (OSError, ValueError, KeyError, TypeError, UnsafeHookConfiguration):
        return HookWiringDiagnostic(
            source, "CONFIG_UNAVAILABLE", 0, len(EVENTS), 0, 0, 0,
        )

"""Explicit, reversible OPTIONAL hook installation for coding agents.

Never runs application hooks or changes ISC sleep policy. Writes are confined
to one explicitly selected existing user-owned JSON configuration at a time.
Regular private backups allow manual recovery. Disable removes *only* the exact
ISC-owned hook groups, so later unrelated agent settings remain intact.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
from typing import Literal

from .hook_plan import (
    EVENTS, MAX_CONFIG_BYTES, SUPPORTED_HOOK_SOURCES,
    UnsafeHookConfiguration, hook_command,
)

Operation = Literal["enable", "disable"]
MAX_WRITTEN_BYTES = 3 * 1024 * 1024


class HookInstallError(ValueError):
    """Error codes never contain configuration contents or private commands."""


@dataclass(frozen=True)
class InstallResult:
    source: str
    operation: Operation
    affected_event_groups: int
    changed: bool
    backup: Path | None


def owned_group(source: str, event_name: str) -> dict:
    """ISC's exact, marked handler. No command supplied by any application."""
    if source not in SUPPORTED_HOOK_SOURCES or event_name not in EVENTS:
        raise ValueError("unsupported_source_or_event")
    # SessionEnd is always synchronous on Codex. Other hooks should avoid
    # holding up the coding agent while Python starts and relays an event.
    handler: dict = {
        "type": "command",
        "command": hook_command(source),
    }
    if event_name != "SessionEnd":
        handler["async"] = True
    return {"hooks": [handler]}


def _owned_groups(hooks: dict, source: str, event: str) -> int:
    target = owned_group(source, event)
    return sum(group == target for group in hooks.get(event, []))


def transform_hooks(data: dict, source: str, operation: Operation) -> tuple[dict, int]:
    """Pure JSON transformation; never edits the input.

    Existing handlers, matcher groups and other settings remain untouched.
    Disabled ISC groups are removed by exact structural equality only.
    """
    if source not in SUPPORTED_HOOK_SOURCES or operation not in ("enable", "disable"):
        raise ValueError("invalid_source_or_operation")
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        raise UnsafeHookConfiguration("invalid_hook_config")
    result = deepcopy(data)
    hooks = result["hooks"]
    for event, groups in hooks.items():
        if not isinstance(event, str) or not isinstance(groups, list):
            raise UnsafeHookConfiguration("malformed_event_groups")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise UnsafeHookConfiguration("malformed_group")
            if not all(isinstance(handler, dict) for handler in group["hooks"]):
                raise UnsafeHookConfiguration("malformed_handler")
    altered = 0
    for event in EVENTS:
        present = _owned_groups(hooks, source, event)
        if operation == "enable":
            if present == 0:
                hooks.setdefault(event, []).append(owned_group(source, event))
                altered += 1
        else:
            if present:
                previous = hooks[event]
                hooks[event] = [
                    group for group in previous
                    if group != owned_group(source, event)
                ]
                altered += present
                if not hooks[event]:
                    hooks.pop(event)
    return result, altered


def _read_config(target: Path) -> tuple[bytes, os.stat_result, dict]:
    """Read without following a final-path symlink; fail on replacement races."""
    try:
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            st = os.fstat(fd)
            if (
                not stat.S_ISREG(st.st_mode)
                or st.st_uid != os.getuid()
                or st.st_mode & 0o022
                or st.st_size > MAX_CONFIG_BYTES
            ):
                raise HookInstallError("unsafe_config_owner_mode_or_size")
            with os.fdopen(os.dup(fd), "rb") as reader:
                raw = reader.read(MAX_CONFIG_BYTES + 1)
            if len(raw) > MAX_CONFIG_BYTES:
                raise HookInstallError("config_too_large")
            after = target.lstat()
            if not _same_snapshot(st, after):
                raise HookInstallError("configuration_changed_during_read")
        finally:
            os.close(fd)
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, HookInstallError):
            raise
        raise HookInstallError("configuration_not_safe_or_readable") from None
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        raise HookInstallError("malformed_configuration")
    return raw, st, data


def _same_snapshot(previous: os.stat_result, current: os.stat_result) -> bool:
    return all(getattr(previous, attr) == getattr(current, attr) for attr in (
        "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
    ))


def _write_exclusive(path: Path, payload: bytes, mode: int) -> None:
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        os.fchmod(fd, mode)
        with os.fdopen(os.dup(fd), "wb") as writer:
            writer.write(payload)
            writer.flush()
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def modify_existing_hooks(
    source: str, target: str | Path, operation: Operation,
) -> InstallResult:
    """Perform one explicit opt-in change with a private recovery backup.

    Serialises ISC changes via flock. A second independent editor may not
    honour this lock, so we check the file again just before os.replace.
    No cross-process atomic compare-and-swap exists for arbitrary JSON files.
    """
    if source not in SUPPORTED_HOOK_SOURCES or operation not in ("enable", "disable"):
        raise HookInstallError("invalid_source_or_operation")
    target = Path(target)
    parent = target.parent
    try:
        parent_stat = parent.lstat()
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != os.getuid()
            or parent_stat.st_mode & 0o022
        ):
            raise HookInstallError("unsafe_config_directory")
    except OSError:
        raise HookInstallError("config_directory_unavailable") from None

    # A sidecar lock coordinates multiple ISC invocations without locking
    # the target inode that os.replace will atomically swap out.
    lock_path = parent / ".isc-hooks.lock"
    try:
        lock_fd = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError:
        raise HookInstallError("cannot_lock_config") from None
    try:
        lock_stat = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(lock_stat.st_mode)
            or lock_stat.st_uid != os.getuid()
            or lock_stat.st_mode & 0o077
        ):
            raise HookInstallError("unsafe_lock_file")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        raw, initial_stat, data = _read_config(target)
        transformed, count = transform_hooks(data, source, operation)
        if not count:
            return InstallResult(source, operation, 0, False, None)
        encoded = (
            json.dumps(transformed, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_WRITTEN_BYTES:
            raise HookInstallError("result_too_large")
        # This file can contain agent secrets: backup permissions are always
        # 0600 regardless of the original mode.
        backup = parent / (
            f".{target.name}.isc-backup-{secrets.token_hex(8)}"
        )
        staged: Path | None = None
        try:
            _write_exclusive(backup, raw, 0o600)
            fd, tmp_path = tempfile.mkstemp(prefix=".isc-stage-", dir=parent)
            staged = Path(tmp_path)
            try:
                os.fchmod(fd, stat.S_IMODE(initial_stat.st_mode))
                with os.fdopen(os.dup(fd), "wb") as output:
                    output.write(encoded)
                    output.flush()
                os.fsync(fd)
            finally:
                os.close(fd)
            # Detect external modification after our initial read.
            current_raw, current_stat, _ = _read_config(target)
            if not _same_snapshot(initial_stat, current_stat) or current_raw != raw:
                raise HookInstallError("configuration_changed_before_replace")
            os.replace(staged, target)
            staged = None
            _fsync_directory(parent)
        except BaseException:
            if staged is not None:
                staged.unlink(missing_ok=True)
            # Keep backup if replace succeeded but fsync failed: never erase
            # the only recovery copy after uncertain write durability.
            if staged is not None or not target.exists():
                backup.unlink(missing_ok=True)
            raise
        return InstallResult(source, operation, count, True, backup)
    except (OSError, UnicodeError) as exc:
        raise HookInstallError("safe_write_failed") from None
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

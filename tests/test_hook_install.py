import copy
import json
import os
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.hook_install import (
    HookInstallError, modify_existing_hooks, owned_group, transform_hooks,
)
from isc.hook_plan import EVENTS, hook_command


class HookInstallTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.target = Path(self.folder.name) / "settings.json"
        self.original = {
            "env": {"PRIVATE_KEY": "UNTOUCHED"},
            "hooks": {
                "Stop": [{
                    "matcher": "existing-private-matcher",
                    "hooks": [
                        {"type": "command", "command": "PRIVATE_SCRIPT"},
                        {"type": "prompt", "prompt": "PRIVATE_PROMPT"},
                    ],
                }],
            },
        }
        self.target.write_text(json.dumps(self.original))
        self.target.chmod(0o600)

    def test_pure_enable_is_additive_no_in_place_changes(self):
        previous = copy.deepcopy(self.original)
        changed, count = transform_hooks(self.original, "codex", "enable")
        self.assertEqual(count, len(EVENTS))
        self.assertEqual(self.original, previous)
        self.assertEqual(changed["env"], previous["env"])
        self.assertEqual(changed["hooks"]["Stop"][0], previous["hooks"]["Stop"][0])
        self.assertEqual(changed["hooks"]["Stop"][-1], owned_group("codex", "Stop"))

    def test_enable_is_idempotent(self):
        first, count = transform_hooks(self.original, "codex", "enable")
        second, count2 = transform_hooks(first, "codex", "enable")
        self.assertEqual(count, 8)
        self.assertEqual(count2, 0)
        self.assertEqual(first, second)

    def test_disable_removes_only_isc_owned_groups(self):
        installed, _ = transform_hooks(self.original, "claude_code", "enable")
        installed["hooks"]["Stop"].append({
            "hooks": [{"type": "command", "command": "new unrelated user hook"}],
        })
        restored, count = transform_hooks(installed, "claude_code", "disable")
        self.assertEqual(count, 8)
        self.assertEqual(restored["env"], self.original["env"])
        self.assertEqual(restored["hooks"]["Stop"][0], self.original["hooks"]["Stop"][0])
        self.assertIn("new unrelated user hook", str(restored["hooks"]["Stop"]))
        self.assertNotIn(hook_command("claude_code"), str(restored))

    def test_disable_noop_without_exact_marker(self):
        changed, count = transform_hooks(self.original, "codex", "disable")
        self.assertEqual(count, 0)
        self.assertEqual(changed, self.original)

    def test_legacy_command_without_async_does_not_duplicate(self):
        legacy = {"hooks": {
            event: [{"hooks": [
                {"type": "command", "command": hook_command("claude_code")}
            ]}] for event in EVENTS
        }}
        updated, count = transform_hooks(legacy, "claude_code", "enable")
        self.assertEqual(count, 0)
        self.assertEqual(updated, legacy)
        clean, removed = transform_hooks(legacy, "claude_code", "disable")
        self.assertEqual(removed, len(EVENTS))
        self.assertEqual(clean["hooks"], {})

    def test_interpreter_relocation_does_not_duplicate_legacy_hook(self):
        import shlex
        old_command = (
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/srv/old-isc/src "
            "/opt/old-python/bin/python3 -m isc send-hook --source claude_code"
        )
        legacy = {"hooks": {
            "Stop": [{"hooks": [
                {"type": "command", "command": old_command}
            ]}],
        }}
        renewed, added = transform_hooks(legacy, "claude_code", "enable")
        self.assertEqual(added, len(EVENTS) - 1)
        self.assertEqual(renewed["hooks"]["Stop"], legacy["hooks"]["Stop"])
        cleaned, removed = transform_hooks(legacy, "claude_code", "disable")
        self.assertEqual(removed, 1)
        self.assertEqual(cleaned["hooks"], {})

    def test_shell_injection_like_imitation_is_not_treated_as_isc_owned(self):
        variants = [
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/thing;evil "
            "/usr/bin/python3 -m isc send-hook --source codex",
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/thing "
            "/usr/bin/python3 -m isc send-hook --source codex && other",
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/thing "
            "/usr/bin/python3 -m isc send-hook --source claude_code",
        ]
        from isc.hook_plan import is_isc_handler
        for cmd in variants:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_isc_handler({
                    "type": "command", "command": cmd,
                }, "codex"))

    def test_isc_hook_in_mixed_group_removes_only_isc_handler(self):
        external = {"type": "command", "command": "EXTERNAL_UNRELATED"}
        own = {"type": "command", "command": hook_command("codex")}
        data = {"hooks": {
            "Stop": [{"matcher": "existing", "hooks": [external, own]}],
        }}
        same, added = transform_hooks(data, "codex", "enable")
        self.assertEqual(added, 7)
        self.assertEqual(same["hooks"]["Stop"], data["hooks"]["Stop"])
        removed, count = transform_hooks(data, "codex", "disable")
        self.assertEqual(count, 1)
        self.assertEqual(removed["hooks"]["Stop"], [
            {"matcher": "existing", "hooks": [external]}
        ])

    def test_codex_session_end_is_sync_others_async(self):
        self.assertNotIn("async", owned_group("codex", "SessionEnd")["hooks"][0])
        self.assertTrue(owned_group("codex", "PreToolUse")["hooks"][0]["async"])

    def test_different_source_not_removed(self):
        first, _ = transform_hooks(self.original, "claude_code", "enable")
        second, count = transform_hooks(first, "codex", "disable")
        self.assertEqual(count, 0)
        self.assertEqual(first, second)

    def test_explicit_enable_writes_recovery_backup(self):
        old = self.target.read_bytes()
        result = modify_existing_hooks("codex", self.target, "enable")
        self.assertTrue(result.changed)
        self.assertEqual(result.affected_event_groups, 8)
        self.assertEqual(result.backup.read_bytes(), old)
        self.assertEqual(stat.S_IMODE(result.backup.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        data = json.loads(self.target.read_bytes())
        self.assertEqual(data["hooks"]["Stop"][0], self.original["hooks"]["Stop"][0])
        self.assertEqual(data["env"], self.original["env"])

    def test_second_enable_writes_no_duplicate_and_no_new_backup(self):
        modify_existing_hooks("codex", self.target, "enable")
        originals = set(self.target.parent.glob(".*isc-backup-*"))
        result = modify_existing_hooks("codex", self.target, "enable")
        self.assertFalse(result.changed)
        self.assertEqual(set(self.target.parent.glob(".*isc-backup-*")), originals)

    def test_disable_preserves_later_user_edits(self):
        installed = modify_existing_hooks("codex", self.target, "enable")
        data = json.loads(self.target.read_bytes())
        data["hooks"]["Stop"].append({
            "hooks": [{"type": "command", "command": "later unrelated change"}]
        })
        data["futureSetting"] = {"preserve": True}
        self.target.write_text(json.dumps(data))
        disabled = modify_existing_hooks("codex", self.target, "disable")
        self.assertEqual(disabled.affected_event_groups, 8)
        after = json.loads(self.target.read_bytes())
        self.assertTrue(after["futureSetting"]["preserve"])
        self.assertIn("later unrelated change", str(after["hooks"]["Stop"]))
        self.assertNotIn(hook_command("codex"), str(after))
        self.assertTrue(disabled.backup.exists())
        self.assertTrue(installed.backup.exists())

    def test_refuse_symlink_to_user_config(self):
        alias = self.target.with_name("alias.json")
        alias.symlink_to(self.target)
        with self.assertRaises(HookInstallError):
            modify_existing_hooks("codex", alias, "enable")
        self.assertFalse(list(self.target.parent.glob(".*isc-backup-*")))

    def test_refuse_missing_file_no_auto_creation(self):
        missing = self.target.with_name("missing.json")
        with self.assertRaises(HookInstallError):
            modify_existing_hooks("codex", missing, "enable")
        self.assertFalse(missing.exists())

    def test_refuse_other_users_or_writable_config(self):
        self.target.chmod(0o666)
        with self.assertRaises(HookInstallError):
            modify_existing_hooks("codex", self.target, "enable")

    def test_refuse_invalid_config_and_keep_original(self):
        self.target.write_text('{"hooks": {"Stop": [null]}}')
        original = self.target.read_bytes()
        with self.assertRaises(HookInstallError):
            modify_existing_hooks("codex", self.target, "enable")
        self.assertEqual(self.target.read_bytes(), original)

    def test_refuse_missing_hooks(self):
        self.target.write_text('{"unrelated":"metadata"}')
        with self.assertRaises(HookInstallError):
            modify_existing_hooks("codex", self.target, "enable")

    def test_refuse_unknown_source_and_operation(self):
        with self.assertRaises(HookInstallError):
            modify_existing_hooks("unknown", self.target, "enable")
        with self.assertRaises(HookInstallError):
            modify_existing_hooks("codex", self.target, "overwrite")

    def test_fail_during_replace_does_not_change_original(self):
        before = self.target.read_bytes()
        with patch("isc.hook_install.os.replace", side_effect=OSError("simulated")):
            with self.assertRaises(HookInstallError):
                modify_existing_hooks("codex", self.target, "enable")
        self.assertEqual(before, self.target.read_bytes())
        self.assertFalse(list(self.target.parent.glob(".isc-stage-*")))

    def test_backup_does_not_change_unrelated_hook_files(self):
        other = self.target.with_name("unrelated-script.sh")
        other.write_text("no edits here")
        modify_existing_hooks("claude_code", self.target, "enable")
        self.assertEqual(other.read_text(), "no edits here")


if __name__ == "__main__":
    unittest.main()

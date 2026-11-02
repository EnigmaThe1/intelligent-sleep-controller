import json
import os
from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.hook_plan import (
    EVENTS, UnsafeHookConfiguration, build_hook_plan,
    default_hook_targets, hook_command,
)


class HookPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "hooks.json"

    def write(self, content):
        self.path.write_text(json.dumps(content), encoding="utf-8")

    def test_preserves_original_json_exactly(self):
        original = {
            "unrelated_private": {"credentials": "DO_NOT_PRINT_OR_CHANGE"},
            "hooks": {"Stop": [{
                "matcher": ".*",
                "hooks": [{
                    "type": "command", "command": "a private existing script"
                }],
            }]},
        }
        self.write(original)
        before = self.path.read_bytes()
        plan = build_hook_plan("codex", self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn("Stop", plan.proposed_events)
        self.assertEqual(plan.already_installed, ())
        self.assertEqual(set(plan.proposed_events), set(EVENTS))
        self.assertTrue(plan.read_only)
        self.assertNotIn("DO_NOT_PRINT_OR_CHANGE", repr(plan))
        self.assertNotIn("a private existing script", repr(plan))

    def test_idempotency_detects_same_command_without_changes(self):
        self.write({"hooks": {"Stop": [{
            "hooks": [{
                "type": "command",
                "command": hook_command("claude_code"),
            }],
        }]}})
        plan = build_hook_plan("claude_code", self.path)
        self.assertEqual(plan.already_installed, ("Stop",))
        self.assertNotIn("Stop", plan.proposed_events)
        self.assertEqual(len(plan.proposed_events), len(EVENTS) - 1)

    def test_same_command_for_different_source_is_not_duplicate(self):
        self.write({"hooks": {"Stop": [{
            "hooks": [{
                "type": "command",
                "command": hook_command("claude_code"),
            }],
        }]}})
        self.assertIn("Stop", build_hook_plan("codex", self.path).proposed_events)

    def test_does_not_expose_current_hooks_or_commands_in_plan(self):
        self.write({"hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "secret stuff"}]}],
            "OtherEvent": [],
        }})
        plan = build_hook_plan("codex", self.path)
        self.assertIn("OtherEvent", plan.configured_events)
        self.assertNotIn("secret stuff", str(plan))

    def test_command_uses_absolute_code_location_and_interpreter(self):
        cmd = hook_command("codex")
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", cmd)
        self.assertIn("PYTHONPATH=", cmd)
        self.assertIn(" -m isc send-hook --source codex", cmd)
        self.assertIn(shlex.quote(sys.executable), cmd)
        self.assertNotIn("$(git", cmd)
        self.assertTrue(Path(__file__).resolve().parents[1].joinpath("src").exists())

    def test_command_is_shell_parseable(self):
        parsed = shlex.split(hook_command("claude_code"))
        self.assertIn("-m", parsed)
        self.assertIn("send-hook", parsed)
        self.assertIn("--source", parsed)
        self.assertIn("claude_code", parsed)

    def test_no_configuration_is_not_silently_created(self):
        with self.assertRaises(UnsafeHookConfiguration):
            build_hook_plan("codex", self.path)
        self.assertFalse(self.path.exists())

    def test_rejects_symlink_configuration(self):
        self.write({"hooks": {}})
        alias = self.path.with_name("alias.json")
        alias.symlink_to(self.path)
        with self.assertRaises(UnsafeHookConfiguration):
            build_hook_plan("codex", alias)

    def test_rejects_bad_structures(self):
        for obj in (
            [],
            {"hooks": []},
            {"hooks": {"Stop": {}}},
            {"hooks": {"Stop": ["wrong"]}},
            {"hooks": {"Stop": [{"hooks": {}}]}},
            {"hooks": {"Stop": [{"hooks": ["bad"]}]}},
        ):
            with self.subTest(obj=str(obj)[:30]):
                self.write(obj)
                with self.assertRaises(UnsafeHookConfiguration):
                    build_hook_plan("codex", self.path)

    def test_rejects_oversized_file(self):
        self.path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        with self.assertRaises(UnsafeHookConfiguration):
            build_hook_plan("codex", self.path)

    def test_rejects_other_source(self):
        self.write({"hooks": {}})
        with self.assertRaises(ValueError):
            build_hook_plan("anything", self.path)
        with self.assertRaises(ValueError):
            hook_command("anything")

    def test_defaults_are_under_user_home_only(self):
        targets = default_hook_targets(home=self.temp.name)
        self.assertEqual(targets[0], (
            "claude_code", Path(self.temp.name) / ".claude" / "settings.json"
        ))
        self.assertEqual(targets[1], (
            "codex", Path(self.temp.name) / ".codex" / "hooks.json"
        ))

    def test_dry_run_does_not_start_listener_or_subprocess(self):
        self.write({"hooks": {}})
        with patch("subprocess.run", side_effect=AssertionError("executed")):
            plan = build_hook_plan("codex", self.path)
        self.assertEqual(len(plan.proposed_events), len(EVENTS))


if __name__ == "__main__":
    unittest.main()

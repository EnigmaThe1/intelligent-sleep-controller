"""No application execution, installation or power operations."""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.cli import main
from isc.hook_plan import EVENTS, hook_command
from isc.hook_wiring_diagnostics import (
    HookWiringDiagnostic, inspect_hook_wiring, _dependencies_available,
)


def handler(source="claude_code", command=None):
    return {"type": "command", "command": hook_command(source)
            if command is None else command}


def fixture(source="claude_code", *, installed=EVENTS, matcher=None,
            duplicate=None, missing_path=False):
    if missing_path:
        installed_handler = handler(
            source, command="PYTHONDONTWRITEBYTECODE=1 "
            "PYTHONPATH=/path/that/does/not/exist "
            "/usr/bin/python3 -m isc send-hook --source " + source,
        )
    else:
        installed_handler = handler(source)
    hooks = {}
    for event in installed:
        group = {"hooks": [installed_handler]}
        if matcher is not None:
            group["matcher"] = matcher
        hooks[event] = [group]
    if duplicate is not None:
        hooks[duplicate].append({"hooks": [installed_handler]})
    return {"hooks": hooks}


def check(source="claude_code", data=None, valid=True):
    return inspect_hook_wiring(
        source, target=Path("/unused/private/settings"),
        config_reader=lambda _: fixture(source) if data is None else data,
        dependency_checker=lambda _: valid,
    )


class HookWiringTests(unittest.TestCase):
    def test_full_hook_set_with_present_paths(self):
        result = check()
        self.assertEqual(result.state, "CONFIGURED_PATHS_PRESENT")
        self.assertEqual(result.installed_events, len(EVENTS))
        self.assertEqual(result.missing_events, 0)
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.task_owner_authenticated)

    def test_no_hooks_is_distinct_from_broken_configuration(self):
        result = check(data={"hooks": {}})
        self.assertEqual(result.state, "NO_ISC_HANDLERS")
        self.assertEqual(result.missing_events, len(EVENTS))

    def test_partial_set_identifies_missing(self):
        result = check(data=fixture(installed=EVENTS[:2]))
        self.assertEqual(result.state, "INCOMPLETE_OR_CONDITIONAL")
        self.assertEqual(result.installed_events, 2)
        self.assertEqual(result.missing_events, len(EVENTS)-2)

    def test_conditional_matchers_are_not_assumed_to_fire(self):
        result = check(data=fixture(matcher="PreToolUse"))
        self.assertEqual(result.state, "INCOMPLETE_OR_CONDITIONAL")
        self.assertEqual(result.conditional_events, len(EVENTS))

    def test_duplicate_handlers_flagged_without_disclosing_commands(self):
        result = check(data=fixture(duplicate=EVENTS[0]))
        self.assertEqual(result.duplicate_events, 1)
        self.assertEqual(result.state, "INCOMPLETE_OR_CONDITIONAL")
        self.assertNotIn("PYTHONPATH", repr(result))

    def test_dependency_failure_does_not_claim_live_hook_delivery(self):
        result = check(valid=False)
        self.assertEqual(result.missing_dependencies, len(EVENTS))
        self.assertEqual(result.state, "INCOMPLETE_OR_CONDITIONAL")

    def test_non_isc_handler_is_not_recognised(self):
        data = {"hooks": {name: [{"hooks": [
            {"type": "command", "command": "echo private"}]}] for name in EVENTS}}
        result = check(data=data)
        self.assertEqual(result.installed_events, 0)
        self.assertNotIn("private", repr(result))

    def test_missing_or_malformed_configs_are_unknown(self):
        for data in (None, {}, {"hooks": "wrong"},
                     {"hooks": {EVENTS[0]: [42]}},
                     {"hooks": {EVENTS[0]: [{"hooks": 12}]}}):
            with self.subTest(data=data):
                reader = Mock(side_effect=OSError("private details")) if data is None else lambda _: data
                result = inspect_hook_wiring(
                    "claude_code", target="/tmp/unused",
                    config_reader=reader,
                )
                self.assertEqual(result.state, "CONFIG_UNAVAILABLE")
                self.assertNotIn("private details", repr(result))

    def test_source_validation(self):
        with self.assertRaises(ValueError):
            inspect_hook_wiring("unknown")

    def test_dependency_checker_does_not_execute_hook(self):
        valid = _dependencies_available(handler())
        self.assertIsInstance(valid, bool)
        self.assertFalse(_dependencies_available(handler(
            command="echo private",
        )))

    def test_realistic_missing_dependency_stays_incomplete(self):
        result = inspect_hook_wiring(
            "claude_code", target="/tmp/unused",
            config_reader=lambda _: fixture(missing_path=True),
        )
        self.assertEqual(result.missing_dependencies, len(EVENTS))

    def test_codex_explicit_configuration_is_supported_without_enable(self):
        result = check("codex", data=fixture("codex"))
        self.assertEqual(result.state, "CONFIGURED_PATHS_PRESENT")
        self.assertEqual(result.source, "codex")

    def test_cli_does_not_disclose_private_commands_or_paths(self):
        result = check()
        out = io.StringIO()
        with patch("isc.cli.inspect_hook_wiring", return_value=result):
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["hook-wiring", "--source", "claude_code"]), 0)
        output = out.getvalue()
        self.assertIn("CONFIGURED_PATHS_PRESENT", output)
        self.assertIn("NOT IMPLEMENTED", output)
        self.assertNotIn("PYTHONPATH=", output)
        self.assertNotIn("/unused", output)
        self.assertNotIn("/usr/bin", output)

    def test_cli_config_unavailable_returns_nonzero(self):
        result = HookWiringDiagnostic(
            "codex", "CONFIG_UNAVAILABLE", 0, len(EVENTS), 0, 0, 0,
        )
        out = io.StringIO()
        with patch("isc.cli.inspect_hook_wiring", return_value=result):
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["hook-wiring", "--source", "codex"]), 2)
        self.assertIn("CONFIG_UNAVAILABLE", out.getvalue())


if __name__ == "__main__":
    unittest.main()

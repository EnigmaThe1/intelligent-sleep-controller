"""Read-only live coding-agent readiness checks, with no app/config writes."""
import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.agent_integration_readiness import inspect_agent_integration
from isc.cli import main
from isc.hook_plan import EVENTS, HookPlan, UnsafeHookConfiguration
from isc.receiver import MonitorHealth


TARGETS = (("claude_code", Path("/not-real/claude")),
           ("codex", Path("/not-real/codex")))


def health(claude=0, codex=0):
    return MonitorHealth(
        accepted=claude + codex, rejected=0,
        observed_active=0, observed_waiting_human=0, observed_unknown=0,
        last_event_age_seconds=None, coverage_verified=False,
        claude_code_observations=claude, codex_observations=codex,
        synthetic_probe_events=0,
    )


def plan(source, target, installed=()):
    return HookPlan(
        source=source, target=target, configured_events=(),
        already_installed=tuple(installed),
        proposed_events=tuple(event for event in EVENTS if event not in installed),
    )


class IntegrationReadinessTests(unittest.TestCase):
    def test_full_configuration_and_live_observations_still_not_authority(self):
        rows = inspect_agent_integration(
            targets=TARGETS,
            plan_reader=lambda source, target: plan(
                source, target, EVENTS if source == "claude_code" else (),
            ),
            health_reader=lambda: health(claude=5, codex=0),
        )
        self.assertEqual(rows[0].config_state, "CONFIGURED")
        self.assertEqual(rows[0].configured_hook_events, len(EVENTS))
        self.assertEqual(rows[0].observed_events, 5)
        self.assertEqual(rows[1].config_state, "NOT_CONFIGURED")
        self.assertEqual(rows[1].observed_events, 0)
        self.assertTrue(rows[0].receiver_active)
        self.assertFalse(rows[0].coverage_verified)
        self.assertFalse(rows[0].task_owner_authenticated)

    def test_partial_config_distinguished_from_full(self):
        rows = inspect_agent_integration(
            targets=TARGETS,
            plan_reader=lambda source, target: plan(
                source, target, EVENTS[:2],
            ),
            health_reader=lambda: health(),
        )
        self.assertEqual(rows[0].config_state, "PARTIAL")
        self.assertEqual(rows[0].configured_hook_events, 2)

    def test_missing_config_is_unknown_not_not_configured(self):
        rows = inspect_agent_integration(
            targets=TARGETS,
            plan_reader=Mock(side_effect=UnsafeHookConfiguration("private path")),
            health_reader=lambda: health(),
        )
        self.assertEqual(rows[0].config_state, "UNKNOWN")
        self.assertIsNone(rows[0].configured_hook_events)
        self.assertEqual(rows[0].observed_events, 0)
        self.assertNotIn("private path", repr(rows))

    def test_monitor_missing_is_unknown_not_zero(self):
        rows = inspect_agent_integration(
            targets=TARGETS,
            plan_reader=lambda s, t: plan(s, t),
            health_reader=lambda: None,
        )
        self.assertIsNone(rows[0].observed_events)
        self.assertFalse(rows[0].receiver_active)

    def test_monitor_failure_does_not_block_readiness(self):
        rows = inspect_agent_integration(
            targets=TARGETS,
            plan_reader=lambda s, t: plan(s, t),
            health_reader=Mock(side_effect=OSError("private socket")),
        )
        self.assertEqual(rows[0].config_state, "NOT_CONFIGURED")
        self.assertIsNone(rows[0].observed_events)
        self.assertNotIn("private socket", repr(rows))

    def test_inconsistent_or_authority_claimed_health_rejected(self):
        claimed = health()
        object.__setattr__(claimed, "coverage_verified", True)
        rows = inspect_agent_integration(
            targets=TARGETS,
            plan_reader=lambda s, t: plan(s, t),
            health_reader=lambda: claimed,
        )
        self.assertIsNone(rows[0].observed_events)
        self.assertFalse(rows[0].receiver_active)

    def test_invalid_hook_plan_is_unknown(self):
        invalid = plan("other", TARGETS[0][1])
        rows = inspect_agent_integration(
            targets=(TARGETS[0],),
            plan_reader=lambda *_: invalid,
            health_reader=lambda: health(),
        )
        self.assertEqual(rows[0].config_state, "UNKNOWN")

    def test_duplicate_configured_events_is_unknown(self):
        rows = inspect_agent_integration(
            targets=(TARGETS[0],),
            plan_reader=lambda s, t: plan(s, t, (EVENTS[0], EVENTS[0])),
            health_reader=lambda: health(),
        )
        self.assertEqual(rows[0].config_state, "UNKNOWN")

    def test_unrecognised_source_refused(self):
        with self.assertRaises(ValueError):
            inspect_agent_integration(
                targets=(("untrusted", Path("/unused")),),
                plan_reader=lambda s, t: plan(s, t),
                health_reader=lambda: health(),
            )

    def test_cli_reports_counts_without_exposing_identifiers_or_paths(self):
        rows = inspect_agent_integration(
            targets=TARGETS,
            plan_reader=lambda s, t: plan(s, t, EVENTS[:1]),
            health_reader=lambda: health(claude=2, codex=3),
        )
        out = io.StringIO()
        with patch("isc.cli.inspect_agent_integration", return_value=rows):
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["agent-integration"]), 0)
        message = out.getvalue()
        self.assertIn("claude_code", message)
        self.assertIn("codex", message)
        self.assertIn("2", message)
        self.assertIn("3", message)
        self.assertIn("NOT IMPLEMENTED", message)
        self.assertIn("No agent, hooks, processes, or power settings modified.", message)
        self.assertNotIn("/not-real", message)
        self.assertNotIn("private", message.lower())

    def test_cli_missing_receiver_warns_without_error(self):
        rows = inspect_agent_integration(
            targets=TARGETS,
            plan_reader=lambda s, t: plan(s, t),
            health_reader=lambda: None,
        )
        out = io.StringIO()
        with patch("isc.cli.inspect_agent_integration", return_value=rows):
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["agent-integration"]), 0)
        self.assertIn("receiver: UNAVAILABLE", out.getvalue())
        self.assertIn("observations since monitor start: UNKNOWN", out.getvalue())


if __name__ == "__main__":
    unittest.main()

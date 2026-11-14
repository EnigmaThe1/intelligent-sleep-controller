"""Passive diagnosis must not classify idle Claude Code as a failure."""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.agent_hook_diagnosis import diagnose_agent_hooks
from isc.cli import main
from isc.hook_wiring_diagnostics import HookWiringDiagnostic
from isc.receiver import MonitorHealth


def wiring(source="claude_code", state="CONFIGURED_PATHS_PRESENT", installed=8):
    return HookWiringDiagnostic(
        source, state, installed, 8-installed, 0, 0, 0,
    )


def health(claude=0, codex=0):
    return MonitorHealth(
        accepted=claude+codex, rejected=0,
        observed_active=0, observed_waiting_human=0, observed_unknown=0,
        last_event_age_seconds=None, coverage_verified=False,
        claude_code_observations=claude, codex_observations=codex,
        synthetic_probe_events=0,
    )


def diagnose(source="claude_code", cfg=None, monitor=None):
    if cfg is None:
        cfg = wiring(source)
    if monitor is None:
        monitor = health()
    return diagnose_agent_hooks(
        source, wiring_reader=lambda _: cfg,
        health_reader=lambda: monitor,
    )


class AgentHookDiagnosisTests(unittest.TestCase):
    def test_fully_wired_idle_agent_is_not_failure(self):
        result = diagnose()
        self.assertEqual(result.outcome, "READY_NO_ACTIVITY_OBSERVED")
        self.assertEqual(result.observed_events, 0)
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.task_owner_authenticated)

    def test_source_specific_claims_do_not_authenticate(self):
        result = diagnose(monitor=health(5, 0))
        self.assertEqual(result.outcome, "SENDER_CLAIMS_OBSERVED")
        self.assertEqual(result.observed_events, 5)
        self.assertFalse(result.task_owner_authenticated)
        other = diagnose(source="codex", monitor=health(5, 0))
        self.assertEqual(other.outcome, "READY_NO_ACTIVITY_OBSERVED")

    def test_unconfigured_codex_is_distinct_from_idle(self):
        result = diagnose(
            source="codex", cfg=wiring("codex", "NO_ISC_HANDLERS", 0),
        )
        self.assertEqual(result.outcome, "NO_ISC_HANDLERS")
        self.assertEqual(result.configured_events, 0)

    def test_partial_hook_wiring_is_reported(self):
        result = diagnose(cfg=wiring(
            state="INCOMPLETE_OR_CONDITIONAL", installed=3,
        ))
        self.assertEqual(result.outcome, "INCOMPLETE_OR_CONDITIONAL")

    def test_configuration_unavailable_is_not_unconfigured(self):
        result = diagnose(cfg=wiring(
            state="CONFIG_UNAVAILABLE", installed=0,
        ))
        self.assertEqual(result.outcome, "CONFIGURATION_UNKNOWN")

    def test_missing_receiver_is_unknown_not_idle(self):
        result = diagnose_agent_hooks(
            "claude_code", wiring_reader=lambda _: wiring(),
            health_reader=lambda: None,
        )
        self.assertEqual(result.outcome, "RECEIVER_UNAVAILABLE")
        self.assertIsNone(result.observed_events)

    def test_old_receiver_with_no_source_counter_not_idle(self):
        old = health()
        object.__setattr__(old, "claude_code_observations", None)
        result = diagnose(monitor=old)
        self.assertEqual(result.outcome, "RECEIVER_UNAVAILABLE")

    def test_broken_reader_does_not_raise_or_leak(self):
        result = diagnose_agent_hooks(
            "claude_code",
            wiring_reader=lambda _: (_ for _ in ()).throw(OSError("private")),
            health_reader=lambda: (_ for _ in ()).throw(OSError("private")),
        )
        self.assertEqual(result.outcome, "CONFIGURATION_UNKNOWN")
        self.assertNotIn("private", repr(result))

    def test_injected_authority_claim_is_discarded(self):
        fake = wiring()
        object.__setattr__(fake, "task_owner_authenticated", True)
        result = diagnose(cfg=fake)
        self.assertEqual(result.outcome, "CONFIGURATION_UNKNOWN")

    def test_unsupported_source_refused(self):
        with self.assertRaises(ValueError):
            diagnose_agent_hooks("untrusted")

    def test_cli_explains_idle_without_any_private_data(self):
        result = diagnose()
        stream = io.StringIO()
        with patch("isc.cli.diagnose_agent_hooks", return_value=result):
            with contextlib.redirect_stdout(stream):
                self.assertEqual(main([
                    "agent-diagnose", "--source", "claude_code",
                ]), 0)
        output = stream.getvalue()
        self.assertIn("READY_NO_ACTIVITY_OBSERVED", output)
        self.assertIn("can be normal", output)
        self.assertIn("NOT IMPLEMENTED", output)
        self.assertNotIn("private", output.lower())


if __name__ == "__main__":
    unittest.main()

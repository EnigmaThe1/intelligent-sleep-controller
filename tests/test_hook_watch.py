"""Live hook observation: synthetic tests only, no agent or power changes."""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.cli import main
from isc.hook_watch import watch_hook_observations
from isc.receiver import MonitorHealth


def health(claude=0, codex=0, *, synthetic=0):
    return MonitorHealth(
        accepted=claude + codex + synthetic, rejected=0,
        observed_active=0, observed_waiting_human=0, observed_unknown=0,
        last_event_age_seconds=None, coverage_verified=False,
        claude_code_observations=claude, codex_observations=codex,
        synthetic_probe_events=synthetic,
    )


def clock_and_sleep():
    current = [0.0]
    def clock():
        return current[0]
    def sleeper(seconds):
        current[0] += seconds
    return clock, sleeper


class HookWatchTests(unittest.TestCase):
    def watch(self, source, samples, *, seconds=1):
        clock, sleeper = clock_and_sleep()
        reader = Mock(side_effect=samples)
        result = watch_hook_observations(
            source, seconds=seconds, health_reader=reader,
            clock=clock, sleeper=sleeper,
        )
        return result, reader

    def test_non_synthetic_claude_events_detected(self):
        found, reader = self.watch(
            "claude_code", (health(0), health(2), health(3)),
        )
        self.assertEqual(found.claimed_events, 3)
        self.assertEqual(found.outcome, "sender_claimed_activity_observed")
        self.assertFalse(found.task_owner_authenticated)
        self.assertFalse(found.coverage_verified)
        self.assertEqual(reader.call_count, 3)

    def test_codex_events_are_source_specific(self):
        found, _ = self.watch(
            "codex", (health(5, 0), health(6, 0), health(7, 1)),
        )
        self.assertEqual(found.claimed_events, 1)

    def test_synthetic_probes_not_mistaken_for_agent_activity(self):
        found, _ = self.watch(
            "claude_code",
            (health(0, synthetic=0), health(0, synthetic=3),
             health(0, synthetic=8)),
        )
        self.assertEqual(found.claimed_events, 0)
        self.assertEqual(found.outcome, "no_activity_observed")

    def test_unavailable_initial_health_is_unknown(self):
        found, reader = self.watch("codex", (None,))
        self.assertEqual(found.outcome, "receiver_unavailable")
        self.assertIsNone(found.claimed_events)
        reader.assert_called_once()

    def test_missing_sample_during_window_invalidates_result(self):
        found, _ = self.watch(
            "claude_code", (health(1), health(2), None),
        )
        self.assertEqual(found.outcome, "receiver_unavailable")
        self.assertIsNone(found.claimed_events)

    def test_counter_reset_cannot_report_positive_delta(self):
        found, _ = self.watch(
            "codex", (health(0, 12), health(0, 13), health(0, 1)),
        )
        self.assertEqual(found.outcome, "counter_reset")
        self.assertIsNone(found.claimed_events)

    def test_sender_claimed_health_authority_rejected(self):
        fake = health(5)
        object.__setattr__(fake, "coverage_verified", True)
        found, _ = self.watch("claude_code", (fake,))
        self.assertEqual(found.outcome, "receiver_unavailable")

    def test_legacy_receiver_without_per_source_counts_is_unknown(self):
        old = health(0)
        object.__setattr__(old, "claude_code_observations", None)
        found, _ = self.watch("claude_code", (old,))
        self.assertIsNone(found.claimed_events)

    def test_invalid_source_and_duration(self):
        for source, duration in (
            ("other", 1), ("codex", -1), ("codex", 121),
            ("codex", float("nan")), ("codex", True),
        ):
            with self.subTest(source=source, duration=duration):
                with self.assertRaises(ValueError):
                    watch_hook_observations(source, seconds=duration,
                                            health_reader=lambda: health())

    def test_clock_reversal_is_unknown(self):
        timestamps = iter((10.0, 9.0))
        found = watch_hook_observations(
            "codex", seconds=1, health_reader=lambda: health(),
            clock=lambda: next(timestamps), sleeper=lambda _: None,
        )
        self.assertEqual(found.outcome, "clock_unavailable")

    def test_zero_second_watch_reads_one_snapshot(self):
        found, reader = self.watch(
            "codex", (health(0, 10),), seconds=0,
        )
        self.assertEqual(found.claimed_events, 0)
        reader.assert_called_once()

    def test_cli_reports_positive_counts_without_claiming_task_authority(self):
        clock, sleeper = clock_and_sleep()
        reader = Mock(side_effect=(
            health(1), health(3), health(3),
        ))
        output = io.StringIO()
        with patch("isc.hook_watch.read_monitor_health", reader):
            # Bound function default was captured on import: patch the entrypoint.
            with patch("isc.cli.watch_hook_observations", return_value=
                       watch_hook_observations(
                           "claude_code", seconds=1, health_reader=reader,
                           clock=clock, sleeper=sleeper
                       )):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main([
                        "hook-watch", "--source", "claude_code", "--seconds", "1",
                    ]), 0)
        message = output.getvalue()
        self.assertIn("sender_claimed_activity_observed", message)
        self.assertIn("2", message)
        self.assertIn("NOT authenticate", message)
        self.assertIn("NOT IMPLEMENTED", message)
        self.assertNotIn("private", message.lower())

    def test_cli_unknown_monitor_returns_nonzero(self):
        from isc.hook_watch import HookWatchResult
        output = io.StringIO()
        with patch("isc.cli.watch_hook_observations",
                   return_value=HookWatchResult(
                       "codex", "receiver_unavailable", None, None, None,
                   )):
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["hook-watch", "--source", "codex"]), 2)
        self.assertIn("UNKNOWN", output.getvalue())


if __name__ == "__main__":
    unittest.main()

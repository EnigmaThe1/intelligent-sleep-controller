import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.hook_payload import parse_hook_payload
from isc.receiver import LocalMonitor, read_monitor_health, send_observation


class MonitorHealthTests(unittest.TestCase):
    def test_no_receiver_means_unknown_not_idle(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(read_monitor_health(runtime_dir=tmp))

    def test_health_is_aggregate_private_and_not_suspension_eligibility(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with LocalMonitor(runtime_dir=root, advertise=True) as monitor:
                thread = threading.Thread(target=monitor.run_for, args=(0.7,))
                thread.start()
                try:
                    before = read_monitor_health(runtime_dir=root)
                    self.assertIsNotNone(before)
                    self.assertEqual(before.accepted, 0)
                    self.assertIsNone(before.last_event_age_seconds)
                    self.assertFalse(before.coverage_verified)
                    private_session_id = "private-user-session-do-not-leak"
                    observation = parse_hook_payload(
                        "claude_code",
                        json.dumps({
                            "session_id": private_session_id,
                            "hook_event_name": "PreToolUse",
                            "prompt": "PRIVATE_PROMPT",
                            "token": "PRIVATE_TOKEN",
                        }).encode(),
                    )
                    self.assertIsNotNone(observation)
                    self.assertTrue(send_observation(monitor.path, observation))
                    deadline = time.monotonic() + 0.4
                    response = None
                    while time.monotonic() < deadline:
                        response = read_monitor_health(runtime_dir=root)
                        if response is not None and response.accepted:
                            break
                        time.sleep(0.02)
                    self.assertIsNotNone(response)
                    self.assertEqual(response.accepted, 1)
                    self.assertEqual(response.observed_active, 1)
                    self.assertEqual(response.claude_code_observations, 1)
                    self.assertEqual(response.codex_observations, 0)
                    self.assertEqual(response.synthetic_probe_events, 0)
                    self.assertEqual(response.observed_unknown, 0)
                    self.assertEqual(response.last_event_age_seconds, 0)
                    self.assertFalse(response.coverage_verified)
                    self.assertNotIn(private_session_id, str(response))
                    self.assertNotIn("PRIVATE_PROMPT", str(response))
                    self.assertNotIn("PRIVATE_TOKEN", str(response))
                finally:
                    thread.join(timeout=2)
                    self.assertFalse(thread.is_alive())
            self.assertIsNone(read_monitor_health(runtime_dir=root))

    def test_expired_event_is_unknown_not_completed(self):
        with TemporaryDirectory() as tmp:
            with LocalMonitor(runtime_dir=tmp) as monitor:
                monitor.registry.report(
                    "claude_code", "opaque", __import__(
                        "isc.policy", fromlist=["WorkState"]
                    ).WorkState.ACTIVE,
                    now=1.0, ttl_seconds=1,
                )
                from unittest.mock import Mock
                connection = Mock()
                from unittest.mock import patch
                with patch("isc.receiver.time.monotonic", return_value=100.0):
                    monitor._reply_health(connection)
                sent = json.loads(connection.sendall.call_args.args[0])
                self.assertEqual(sent["observed_unknown"], 1)
                self.assertFalse(sent["coverage_verified"])


    def test_source_claims_separate_from_synthetic_diagnostics(self):
        with TemporaryDirectory() as tmp:
            with LocalMonitor(runtime_dir=tmp, advertise=True) as monitor:
                worker = threading.Thread(target=monitor.run_for, args=(1.0,))
                worker.start()
                try:
                    for source, event in (
                        ("claude_code", "PreToolUse"),
                        ("codex", "PostToolUse"),
                        ("claude_code", "IscSelfTest"),
                    ):
                        observation = parse_hook_payload(source, json.dumps({
                            "session_id": "private-session-" + source + event,
                            "hook_event_name": event,
                            "secret": "never-expose-me",
                        }).encode())
                        self.assertIsNotNone(observation)
                        self.assertTrue(send_observation(monitor.path, observation))
                    deadline = time.monotonic() + 0.7
                    health = None
                    while time.monotonic() < deadline:
                        health = read_monitor_health(runtime_dir=tmp)
                        if health is not None and health.accepted >= 3:
                            break
                        time.sleep(0.02)
                    self.assertIsNotNone(health)
                    self.assertEqual(health.accepted, 3)
                    self.assertEqual(health.claude_code_observations, 1)
                    self.assertEqual(health.codex_observations, 1)
                    self.assertEqual(health.synthetic_probe_events, 1)
                    self.assertEqual(monitor.registry.count(), 2)
                    self.assertFalse(health.coverage_verified)
                    self.assertNotIn("never-expose-me", str(health))
                    self.assertNotIn("private-session", str(health))
                finally:
                    worker.join(timeout=2)
                    self.assertFalse(worker.is_alive())

    def test_process_census_is_aggregate_and_fails_closed_when_stale(self):
        from isc.background_observer import ProcessPulse
        from unittest.mock import Mock, patch
        with TemporaryDirectory() as tmp:
            with LocalMonitor(runtime_dir=tmp) as monitor:
                observed_at = time.monotonic()
                monitor.update_process_pulse(ProcessPulse(
                    roots=3, descendants=8,
                    visibility_races=1, identity_changes=2,
                    observed_at=observed_at,
                ))
                recipient = Mock()
                monitor._reply_health(recipient)
                status = json.loads(recipient.sendall.call_args.args[0])
                self.assertEqual(status["process_roots"], 3)
                self.assertEqual(status["process_descendants"], 8)
                self.assertEqual(status["process_visibility_races"], 1)
                self.assertEqual(status["process_identity_changes"], 2)
                self.assertFalse(status["coverage_verified"])
                self.assertNotIn("pid", status)
                recipient.reset_mock()
                with patch("isc.receiver.time.monotonic",
                           return_value=observed_at + 121):
                    monitor._reply_health(recipient)
                stale = json.loads(recipient.sendall.call_args.args[0])
                self.assertIsNone(stale["process_roots"])
                self.assertIsNone(stale["process_descendants"])
                self.assertIn(stale["process_sample_age_seconds"], (121, 122))

    def test_process_aggregate_round_trip_over_private_health_socket(self):
        from isc.background_observer import ProcessPulse
        with TemporaryDirectory() as tmp:
            with LocalMonitor(runtime_dir=tmp, advertise=True) as monitor:
                worker = threading.Thread(
                    target=monitor.run_for, args=(0.4,), daemon=True,
                )
                monitor.update_process_pulse(ProcessPulse(
                    roots=3, descendants=8, visibility_races=1,
                    identity_changes=0, observed_at=time.monotonic(),
                ))
                worker.start()
                try:
                    health = read_monitor_health(runtime_dir=tmp)
                    self.assertIsNotNone(health)
                    self.assertEqual(health.process_roots, 3)
                    self.assertEqual(health.process_descendants, 8)
                    self.assertEqual(health.process_visibility_races, 1)
                    self.assertEqual(health.process_identity_changes, 0)
                    self.assertIsNotNone(health.process_sample_age_seconds)
                    self.assertFalse(health.coverage_verified)
                    self.assertNotIn("pid", repr(health))
                finally:
                    worker.join(timeout=2)
                    self.assertFalse(worker.is_alive())

    def test_process_census_failure_means_unknown_not_zero(self):
        from isc.background_observer import ProcessPulse
        from unittest.mock import Mock
        with TemporaryDirectory() as tmp:
            with LocalMonitor(runtime_dir=tmp) as monitor:
                monitor.update_process_pulse(ProcessPulse(
                    roots=None, descendants=None,
                    visibility_races=None, identity_changes=None,
                    observed_at=time.monotonic(),
                ))
                recipient = Mock()
                monitor._reply_health(recipient)
                failed = json.loads(recipient.sendall.call_args.args[0])
                self.assertIsNone(failed["process_roots"])
                self.assertIsNone(failed["process_visibility_races"])
                self.assertFalse(failed["coverage_verified"])

    def test_scheduler_aggregate_health_and_stale_fallback(self):
        from isc.background_scheduler import SchedulerPulse
        from unittest.mock import Mock, patch
        with TemporaryDirectory() as tmp:
            with LocalMonitor(runtime_dir=tmp) as monitor:
                observed = time.monotonic()
                monitor.update_scheduler_pulse(SchedulerPulse(
                    services=21, with_tasks=1, uncertain=20,
                    transitions=None, observed_at=observed,
                    uncertain_transitions=None,
                ))
                peer = Mock()
                monitor._reply_health(peer)
                health = json.loads(peer.sendall.call_args.args[0])
                self.assertEqual(health["scheduler_services"], 21)
                self.assertEqual(health["scheduler_with_tasks"], 1)
                self.assertEqual(health["scheduler_uncertain"], 20)
                self.assertIsNone(health["scheduler_transitions"])
                self.assertIsNone(health["scheduler_uncertain_transitions"])
                self.assertFalse(health["coverage_verified"])
                self.assertNotIn("ControlGroup", health)
                peer.reset_mock()
                with patch("isc.receiver.time.monotonic",
                           return_value=observed + 181):
                    monitor._reply_health(peer)
                stale = json.loads(peer.sendall.call_args.args[0])
                self.assertIsNone(stale["scheduler_services"])
                self.assertIsNone(stale["scheduler_with_tasks"])
                self.assertIsNone(stale["scheduler_uncertain_transitions"])
                self.assertIsNone(stale["scheduler_invocation_changes"])
                self.assertIn(stale["scheduler_sample_age_seconds"], (181, 182))

    def test_scheduler_health_roundtrip_over_socket(self):
        from isc.background_scheduler import SchedulerPulse
        with TemporaryDirectory() as tmp:
            with LocalMonitor(runtime_dir=tmp, advertise=True) as monitor:
                monitor.update_scheduler_pulse(SchedulerPulse(
                    services=2, with_tasks=1, uncertain=1,
                    transitions=0, observed_at=time.monotonic(),
                    uncertain_transitions=1, invocation_changes=0,
                ))
                thread = threading.Thread(
                    target=monitor.run_for, args=(0.4,), daemon=True,
                )
                thread.start()
                try:
                    health = read_monitor_health(runtime_dir=tmp)
                    self.assertIsNotNone(health)
                    self.assertEqual(health.scheduler_services, 2)
                    self.assertEqual(health.scheduler_with_tasks, 1)
                    self.assertEqual(health.scheduler_uncertain, 1)
                    self.assertEqual(health.scheduler_transitions, 0)
                    self.assertEqual(health.scheduler_uncertain_transitions, 1)
                    self.assertEqual(health.scheduler_invocation_changes, 0)
                    self.assertFalse(health.coverage_verified)
                finally:
                    thread.join(timeout=2)
                    self.assertFalse(thread.is_alive())

    def test_health_protocol_rejects_forged_process_counts(self):
        from unittest.mock import MagicMock, patch
        raw = {
            "accepted": 0, "rejected": 0, "observed_active": 0,
            "observed_waiting_human": 0, "observed_unknown": 0,
            "last_event_age_seconds": None, "coverage_verified": False,
            "claude_code_observations": 0, "codex_observations": 0,
            "synthetic_probe_events": 0,
            "process_roots": -1, "process_descendants": 3,
            "process_visibility_races": 0, "process_identity_changes": 0,
            "process_sample_age_seconds": 1,
        }
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.recv.side_effect = [json.dumps(raw).encode(), b""]
        with patch("isc.receiver.discover_monitor_socket",
                   return_value=Path("/private/isc-monitor/events.sock")):
            with patch("isc.receiver.socket.socket", return_value=conn):
                self.assertIsNone(read_monitor_health())

    def test_legacy_running_receiver_is_readable_but_source_is_unknown(self):
        from unittest.mock import MagicMock, patch
        from isc.receiver import read_monitor_health
        original = {
            "accepted": 1, "rejected": 0, "observed_active": 0,
            "observed_waiting_human": 0, "observed_unknown": 1,
            "last_event_age_seconds": 2, "coverage_verified": False,
        }
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.recv.side_effect = [json.dumps(original).encode(), b""]
        with patch("isc.receiver.discover_monitor_socket",
                   return_value=Path("/private/isc-monitor/events.sock")):
            with patch("isc.receiver.socket.socket", return_value=conn):
                health = read_monitor_health()
        self.assertIsNotNone(health)
        self.assertEqual(health.accepted, 1)
        self.assertIsNone(health.claude_code_observations)
        self.assertIsNone(health.codex_observations)
        self.assertIsNone(health.synthetic_probe_events)
        self.assertFalse(health.coverage_verified)

    def test_inconsistent_extended_receiver_counters_rejected(self):
        from unittest.mock import MagicMock, patch
        from isc.receiver import read_monitor_health
        raw = {
            "accepted": 1, "rejected": 0, "observed_active": 0,
            "observed_waiting_human": 0, "observed_unknown": 0,
            "last_event_age_seconds": None, "coverage_verified": False,
            "claude_code_observations": 1,
            "codex_observations": 1,
            "synthetic_probe_events": 0,
        }
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.recv.side_effect = [json.dumps(raw).encode(), b""]
        with patch("isc.receiver.discover_monitor_socket",
                   return_value=Path("/private/isc-monitor/events.sock")):
            with patch("isc.receiver.socket.socket", return_value=conn):
                self.assertIsNone(read_monitor_health())


if __name__ == "__main__":
    unittest.main()

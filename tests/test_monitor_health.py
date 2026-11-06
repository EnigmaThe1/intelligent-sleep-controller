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


if __name__ == "__main__":
    unittest.main()

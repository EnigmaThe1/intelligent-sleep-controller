"""Synthetic hook diagnostics never launch an agent or enable sleep."""
import json
import os
from tempfile import TemporaryDirectory
import threading
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.hook_diagnostics import check_hook_delivery


def health(accepted: int):
    return SimpleNamespace(accepted=accepted)


class HookDeliveryTests(unittest.TestCase):
    def test_real_subprocess_delivers_synthetic_hook_to_private_receiver(self):
        """End-to-end local IPC: no Claude/Codex or system power is invoked."""
        from isc.receiver import LocalMonitor
        with TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "run"
            runtime.mkdir(mode=0o700)
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
                with LocalMonitor(runtime_dir=runtime, advertise=True) as receiver:
                    thread = threading.Thread(
                        target=receiver.run_for, args=(0.8,), daemon=True,
                    )
                    thread.start()
                    try:
                        result = check_hook_delivery("claude_code")
                        self.assertTrue(result.delivered, result.status)
                        self.assertEqual(result.before_accepted, 0)
                        self.assertEqual(result.after_accepted, 1)
                        self.assertEqual(receiver.accepted, 1)
                        self.assertEqual(receiver.counts["unknown"], 0)
                        self.assertEqual(receiver._synthetic_probe_events, 1)
                        self.assertEqual(sum(receiver._source_observations.values()), 0)
                        self.assertEqual(receiver.registry.count(), 0)
                    finally:
                        thread.join(timeout=3)
                        self.assertFalse(thread.is_alive())

    @patch("isc.hook_diagnostics.read_monitor_health", return_value=None)
    @patch("isc.hook_diagnostics.subprocess.run")
    def test_no_receiver_does_not_invoke_hook(self, execute, query):
        result = check_hook_delivery("claude_code")
        self.assertEqual(result.status, "receiver_unavailable")
        self.assertFalse(result.delivered)
        execute.assert_not_called()

    @patch("isc.hook_diagnostics.subprocess.run")
    def test_unknown_source_never_invokes_hook(self, execute):
        result = check_hook_delivery("unknown_app")
        self.assertEqual(result.status, "unsupported_source")
        execute.assert_not_called()

    @patch("isc.hook_diagnostics.read_monitor_health",
           side_effect=[health(3), health(4)])
    @patch("isc.hook_diagnostics.subprocess.run",
           return_value=SimpleNamespace(returncode=0))
    @patch.dict(os.environ, {"PRIVATE_AI_TOKEN": "DO_NOT_DISCLOSE"})
    def test_probe_sends_unknown_synthetic_stop_with_minimal_environment(
        self, execute, query,
    ):
        result = check_hook_delivery("claude_code")
        self.assertTrue(result.delivered)
        self.assertEqual((result.before_accepted, result.after_accepted), (3, 4))
        args = execute.call_args.args[0]
        self.assertEqual(args[1:], [
            "-B", "-m", "isc", "send-hook", "--source", "claude_code",
        ])
        self.assertEqual(args[0], sys.executable)
        kwargs = execute.call_args.kwargs
        self.assertFalse(kwargs.get("shell", False))
        self.assertEqual(kwargs["timeout"], 4)
        self.assertNotIn("PRIVATE_AI_TOKEN", kwargs["env"])
        self.assertNotIn("DO_NOT_DISCLOSE", str(kwargs["env"]))
        data = json.loads(kwargs["input"])
        self.assertEqual(set(data), {"hook_event_name", "session_id"})
        self.assertEqual(data["hook_event_name"], "IscSelfTest")
        self.assertTrue(data["session_id"].startswith("isc-synthetic-probe-"))
        self.assertEqual(len(data["session_id"]), len("isc-synthetic-probe-")+24)

    @patch("isc.hook_diagnostics.read_monitor_health",
           side_effect=[health(0), health(0), health(0)])
    @patch("isc.hook_diagnostics.subprocess.run",
           return_value=SimpleNamespace(returncode=0))
    @patch("isc.hook_diagnostics.time.monotonic", side_effect=[10, 12])
    def test_unchanged_counter_never_claims_delivery(
        self, clock, execute, query,
    ):
        result = check_hook_delivery("claude_code")
        self.assertFalse(result.delivered)
        self.assertEqual(result.status, "delivery_unverified")
        self.assertEqual(result.after_accepted, 0)

    @patch("isc.hook_diagnostics.read_monitor_health", return_value=health(0))
    @patch("isc.hook_diagnostics.subprocess.run", side_effect=OSError)
    def test_sender_failure_does_not_claim_delivery(self, execute, query):
        result = check_hook_delivery("claude_code")
        self.assertEqual(result.status, "hook_sender_unavailable")
        self.assertFalse(result.delivered)

    @patch("isc.hook_diagnostics.read_monitor_health", return_value=health(0))
    @patch("isc.hook_diagnostics.subprocess.run",
           return_value=SimpleNamespace(returncode=2))
    def test_nonzero_sender_exit_refused(self, execute, query):
        result = check_hook_delivery("claude_code")
        self.assertEqual(result.status, "hook_sender_failed")
        self.assertFalse(result.delivered)


if __name__ == "__main__":
    unittest.main()

import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.service import MonitorSummary, MonitorUnavailable
from isc.mode_gate import EffectiveMode
from isc.supervisor import monitor_service_loop


class SupervisorTests(unittest.TestCase):
    @patch("isc.supervisor.time.sleep")
    @patch("isc.supervisor.serve_monitor")
    @patch("isc.supervisor.read_monitor_health")
    @patch("isc.supervisor.current_mode", return_value=EffectiveMode("off", False))
    def test_off_never_starts_monitor(self, mode, health, serve, sleep):
        self.assertEqual(monitor_service_loop(max_iterations=3), 0)
        serve.assert_not_called()
        health.assert_not_called()
        self.assertEqual(sleep.call_count, 3)

    @patch("isc.supervisor.time.sleep")
    @patch("isc.supervisor.serve_monitor")
    @patch("isc.supervisor.read_monitor_health", return_value=object())
    @patch("isc.supervisor.current_mode", return_value=EffectiveMode("monitor", True))
    def test_preserves_existing_healthy_external_receiver(
        self, mode, health, serve, sleep,
    ):
        self.assertEqual(monitor_service_loop(max_iterations=2), 0)
        serve.assert_not_called()
        self.assertEqual(health.call_count, 2)

    @patch("isc.supervisor.time.sleep")
    @patch("isc.supervisor.serve_monitor")
    @patch("isc.supervisor.read_monitor_health", return_value=None)
    @patch("isc.supervisor.current_mode", return_value=EffectiveMode("monitor", True))
    def test_monitor_starts_independent_of_gtk(
        self, mode, health, serve, sleep,
    ):
        serve.return_value = MonitorSummary("mode_off")
        self.assertEqual(monitor_service_loop(max_iterations=1), 0)
        serve.assert_called_once_with(max_seconds=0, quiet=True)

    @patch("isc.supervisor.time.sleep")
    @patch("isc.supervisor.serve_monitor",
           side_effect=MonitorUnavailable("receiver_unavailable"))
    @patch("isc.supervisor.read_monitor_health", return_value=None)
    @patch("isc.supervisor.current_mode", return_value=EffectiveMode("monitor", True))
    def test_repeated_failures_have_bounded_backoff(
        self, mode, health, serve, sleep,
    ):
        self.assertEqual(monitor_service_loop(max_iterations=8), 0)
        delays = [c.args[0] for c in sleep.call_args_list]
        self.assertEqual(delays, [2, 4, 8, 16, 32, 60, 60, 60])

    @patch("isc.supervisor.time.sleep")
    @patch("isc.supervisor.serve_monitor")
    @patch("isc.supervisor.read_monitor_health")
    @patch("isc.supervisor.current_mode",
           return_value=EffectiveMode("off", False))
    def test_invalid_configuration_fails_closed(
        self, mode, health, serve, sleep,
    ):
        self.assertEqual(monitor_service_loop(max_iterations=2), 0)
        serve.assert_not_called()
        health.assert_not_called()
        self.assertEqual(sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()

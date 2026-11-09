import contextlib
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.service import (
    MonitorUnavailable, serve_monitor,
)
from isc.settings import read_mode, set_mode
from isc.receiver import send_discovered_observation
from isc.hook_payload import parse_hook_payload


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "config"
        self.runtime = Path(self.temp.name) / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.mode_patcher = patch(
            "isc.service.read_mode",
            side_effect=lambda: read_mode(config_root=self.root),
        )
        self.env_patcher = patch.dict(
            "os.environ", {"XDG_RUNTIME_DIR": str(self.runtime)},
        )
        self.mode_patcher.start()
        self.addCleanup(self.mode_patcher.stop)
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

    def test_default_off_refuses_to_start_without_socket(self):
        with self.assertRaisesRegex(MonitorUnavailable, "monitor_mode_not_selected"):
            serve_monitor(max_seconds=0.1)
        self.assertFalse((self.runtime / "isc-monitor").exists())

    @patch("isc.service.BackgroundProcessObserver")
    @patch("isc.service._read_preference",
           side_effect=["monitor", "off"])
    def test_indefinite_monitor_starts_and_closes_observer(
        self, preference, observer,
    ):
        result = serve_monitor(max_seconds=0, quiet=True)
        self.assertEqual(result.reason, "mode_off")
        observer.assert_called_once()
        observer.return_value.start.assert_called_once()
        observer.return_value.close.assert_called_once()

    @patch("isc.service.BackgroundProcessObserver")
    def test_bounded_probe_does_not_launch_background_census(self, observer):
        set_mode("monitor", config_root=self.root)
        with contextlib.redirect_stdout(StringIO()):
            result = serve_monitor(max_seconds=0.05, poll_seconds=0.01)
        self.assertEqual(result.reason, "time_limit")
        observer.assert_not_called()

    @patch("isc.service.BackgroundProcessObserver")
    @patch("isc.service._read_preference",
           side_effect=["monitor", "off"])
    def test_process_observer_start_failure_keeps_receiver_safe(
        self, preference, observer,
    ):
        observer.return_value.start.side_effect = RuntimeError("thread failure")
        result = serve_monitor(max_seconds=0, quiet=True)
        self.assertEqual(result.reason, "mode_off")
        observer.return_value.close.assert_not_called()
        self.assertFalse((self.runtime / "isc-monitor").exists())

    def test_bounded_monitor_runs_and_cleans_socket(self):
        set_mode("monitor", config_root=self.root)
        with contextlib.redirect_stdout(StringIO()):
            result = serve_monitor(max_seconds=0.05, poll_seconds=0.02)
        self.assertEqual(result.reason, "time_limit")
        self.assertEqual(result.accepted, 0)
        self.assertFalse((self.runtime / "isc-monitor").exists())

    def test_delivers_actual_metadata_while_running(self):
        set_mode("monitor", config_root=self.root)
        results = []
        def runner():
            with contextlib.redirect_stdout(StringIO()):
                results.append(serve_monitor(max_seconds=0.3, poll_seconds=0.02))
        # Redirect stdout in the worker is globally risky if another test prints;
        # this test sends no other output while the worker is active.
        thread = threading.Thread(target=runner)
        thread.start()
        path = self.runtime / "isc-monitor" / "events.sock"
        try:
            for _ in range(100):
                if path.exists():
                    break
                time.sleep(0.005)
            self.assertTrue(path.exists())
            obs = parse_hook_payload("claude_code", json.dumps({
                "session_id": "synthetic-not-logged",
                "hook_event_name": "SessionStart",
                "tool_input": {"secret": "DO_NOT_PRINT"},
            }).encode())
            self.assertIsNotNone(obs)
            self.assertTrue(send_discovered_observation(obs, runtime_dir=self.runtime))
        finally:
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].accepted, 1)
        self.assertEqual(
            results[0].event_counts, (("claude_code", "SessionStart", 1),),
        )
        self.assertNotIn("synthetic-not-logged", str(results[0]))
        self.assertFalse(path.exists())

    def test_off_stops_service_and_cleans(self):
        set_mode("monitor", config_root=self.root)
        results = []
        def runner():
            with contextlib.redirect_stdout(StringIO()):
                results.append(serve_monitor(max_seconds=2, poll_seconds=0.05))
        thread = threading.Thread(target=runner)
        thread.start()
        socket = self.runtime / "isc-monitor" / "events.sock"
        try:
            for _ in range(100):
                if socket.exists():
                    break
                time.sleep(0.005)
            self.assertTrue(socket.exists())
            set_mode("off", config_root=self.root)
        finally:
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].reason, "mode_off")
        self.assertFalse(socket.exists())

    def test_corrupt_config_causes_exit_and_socket_cleanup(self):
        set_mode("monitor", config_root=self.root)
        results = []
        def runner():
            try:
                with contextlib.redirect_stdout(StringIO()):
                    results.append(serve_monitor(max_seconds=2, poll_seconds=0.05))
            except MonitorUnavailable as exc:
                results.append(str(exc))
        thread = threading.Thread(target=runner)
        thread.start()
        socket = self.runtime / "isc-monitor" / "events.sock"
        try:
            for _ in range(100):
                if socket.exists():
                    break
                time.sleep(0.005)
            self.assertTrue(socket.exists())
            path = self.root / "intelligent-sleep-controller" / "settings.json"
            path.write_text("corrupt", encoding="utf-8")
        finally:
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, ["invalid_or_unavailable_mode"])
        self.assertFalse(socket.exists())

    def test_unsupported_on_mode_cannot_start_receiver(self):
        from isc.settings import ModeStatus
        with patch("isc.service.read_mode", return_value=ModeStatus("on", True)):
            with self.assertRaisesRegex(
                    MonitorUnavailable, "invalid_or_unavailable_mode"):
                serve_monitor(max_seconds=0.1)
        self.assertFalse((self.runtime / "isc-monitor").exists())

    def test_corrupt_on_mode_stops_running_receiver(self):
        set_mode("monitor", config_root=self.root)
        results = []
        def runner():
            try:
                results.append(serve_monitor(
                    max_seconds=2, poll_seconds=0.05, quiet=True,
                ))
            except MonitorUnavailable as exc:
                results.append(str(exc))
        thread = threading.Thread(target=runner)
        thread.start()
        socket = self.runtime / "isc-monitor" / "events.sock"
        try:
            for _ in range(100):
                if socket.exists():
                    break
                time.sleep(0.005)
            self.assertTrue(socket.exists())
            config = self.root / "intelligent-sleep-controller" / "settings.json"
            config.write_text('{"version": 1, "mode": "on"}', encoding="utf-8")
        finally:
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, ["invalid_or_unavailable_mode"])
        self.assertFalse(socket.exists())

    def test_two_instances_refuse_to_replace_existing_socket(self):
        set_mode("monitor", config_root=self.root)
        from isc.receiver import LocalMonitor
        with LocalMonitor(runtime_dir=self.runtime, advertise=True):
            with self.assertRaisesRegex(MonitorUnavailable, "receiver_unavailable"):
                serve_monitor(max_seconds=0.05)
        self.assertFalse((self.runtime / "isc-monitor").exists())

    def test_interrupt_keeps_counters_and_cleans_socket(self):
        set_mode("monitor", config_root=self.root)
        from isc.receiver import LocalMonitor
        original_run_for = LocalMonitor.run_for

        def interrupted(receiver, seconds):
            receiver.accepted = 3
            receiver.rejected = 1
            receiver.event_counts[("claude_code", "SessionStart")] = 3
            raise KeyboardInterrupt()

        with patch.object(LocalMonitor, "run_for", interrupted):
            with contextlib.redirect_stdout(StringIO()):
                summary = serve_monitor(max_seconds=3)
        self.assertEqual(summary.reason, "interrupted")
        self.assertEqual(summary.accepted, 3)
        self.assertEqual(summary.rejected, 1)
        self.assertEqual(
            summary.event_counts, (("claude_code", "SessionStart", 3),),
        )
        self.assertFalse((self.runtime / "isc-monitor").exists())

    def test_bad_duration_refused_without_changes(self):
        set_mode("monitor", config_root=self.root)
        for seconds in (-1, float("nan"), 86401):
            with self.subTest(seconds=seconds):
                with self.assertRaises(ValueError):
                    serve_monitor(max_seconds=seconds)
        self.assertFalse((self.runtime / "isc-monitor").exists())

    def test_no_suspension_or_inhibitors_ever_invoked(self):
        set_mode("monitor", config_root=self.root)
        with patch("subprocess.run", side_effect=AssertionError("power executed")):
            with contextlib.redirect_stdout(StringIO()):
                result = serve_monitor(max_seconds=0.05)
        self.assertEqual(result.reason, "time_limit")


if __name__ == "__main__":
    unittest.main()

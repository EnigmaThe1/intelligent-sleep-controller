import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.receiver import discover_monitor_socket
from isc.settings import read_mode, set_mode
from isc.tray_runtime import TrayMonitorProcess


class TrayRuntimeTests(unittest.TestCase):
    def test_off_never_starts_a_child(self):
        supervisor = TrayMonitorProcess()
        with patch("isc.tray_runtime.subprocess.Popen") as spawn:
            self.assertEqual(
                supervisor.reconcile(mode="off", config_valid=True), "off",
            )
        spawn.assert_not_called()

    def test_invalid_preference_never_starts_a_child(self):
        supervisor = TrayMonitorProcess()
        with patch("isc.tray_runtime.subprocess.Popen") as spawn:
            self.assertEqual(
                supervisor.reconcile(mode="monitor", config_valid=False),
                "invalid_configuration",
            )
        spawn.assert_not_called()

    def test_unavailable_on_does_not_spawn_tray_monitor(self):
        supervisor = TrayMonitorProcess()
        with patch("isc.tray_runtime.subprocess.Popen") as spawn:
            self.assertEqual(
                supervisor.reconcile(mode="on", config_valid=True),
                "invalid_configuration",
            )
        spawn.assert_not_called()

    def test_unknown_setting_does_not_spawn_tray_monitor(self):
        supervisor = TrayMonitorProcess()
        with patch("isc.tray_runtime.subprocess.Popen") as spawn:
            self.assertEqual(
                supervisor.reconcile(mode="unexpected", config_valid=True),
                "invalid_configuration",
            )
        spawn.assert_not_called()

    @patch("isc.tray_runtime.discover_monitor_socket")
    @patch("isc.tray_runtime.subprocess.Popen")
    def test_spawn_uses_only_isc_monitor_command(self, spawn, discover):
        discover.return_value = None
        child = Mock()
        child.poll.return_value = None
        spawn.return_value = child
        supervisor = TrayMonitorProcess()
        self.assertEqual(
            supervisor.reconcile(mode="monitor", config_valid=True), "starting",
        )
        cmd = spawn.call_args.args[0]
        self.assertEqual(
            cmd, [sys.executable, "-B", "-m", "isc", "serve", "--seconds", "0"],
        )
        options = spawn.call_args.kwargs
        self.assertTrue(options["start_new_session"])
        self.assertTrue(options["close_fds"])
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["stdout"], subprocess.DEVNULL)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)
        self.assertIn("src", options["env"]["PYTHONPATH"])
        self.assertNotIn("systemctl", " ".join(cmd))
        supervisor.close()
        child.send_signal.assert_called_once_with(signal.SIGINT)

    @patch("isc.tray_runtime.discover_monitor_socket")
    @patch("isc.tray_runtime.subprocess.Popen")
    def test_existing_socket_is_never_terminated(self, spawn, discover):
        discover.return_value = Path("/private/another-monitor.sock")
        supervisor = TrayMonitorProcess()
        self.assertEqual(
            supervisor.reconcile(mode="monitor", config_valid=True),
            "external",
        )
        self.assertEqual(
            supervisor.reconcile(mode="off", config_valid=True), "off",
        )
        spawn.assert_not_called()

    @patch("isc.tray_runtime.discover_monitor_socket", return_value=None)
    @patch("isc.tray_runtime.subprocess.Popen")
    def test_crashed_child_is_not_automatically_respawned(self, spawn, discover):
        child = Mock()
        child.poll.return_value = 1
        spawn.return_value = child
        supervisor = TrayMonitorProcess()
        self.assertEqual(
            supervisor.reconcile(mode="monitor", config_valid=True), "starting",
        )
        self.assertEqual(
            supervisor.reconcile(mode="monitor", config_valid=True), "failed",
        )
        self.assertEqual(
            supervisor.reconcile(mode="monitor", config_valid=True), "failed",
        )
        spawn.assert_called_once()
        supervisor.close()

    @patch("isc.tray_runtime.discover_monitor_socket")
    @patch("isc.tray_runtime.subprocess.Popen")
    def test_off_stops_only_owned_child(self, spawn, discover):
        discover.return_value = None
        child = Mock()
        child.poll.return_value = None
        spawn.return_value = child
        supervisor = TrayMonitorProcess()
        supervisor.reconcile(mode="monitor", config_valid=True)
        self.assertEqual(
            supervisor.reconcile(mode="off", config_valid=True), "off",
        )
        child.send_signal.assert_called_once_with(signal.SIGINT)
        child.wait.assert_called_once_with(timeout=3)
        self.assertEqual(
            supervisor.reconcile(mode="off", config_valid=True), "off",
        )
        self.assertEqual(child.send_signal.call_count, 1)

    @patch("isc.tray_runtime.discover_monitor_socket", return_value=None)
    @patch("isc.tray_runtime.subprocess.Popen", side_effect=OSError)
    def test_failed_spawn_does_not_crash_loop(self, spawn, discover):
        supervisor = TrayMonitorProcess()
        self.assertEqual(
            supervisor.reconcile(mode="monitor", config_valid=True), "failed",
        )
        self.assertEqual(
            supervisor.reconcile(mode="monitor", config_valid=True), "failed",
        )
        spawn.assert_called_once()
        self.assertEqual(
            supervisor.reconcile(mode="off", config_valid=True), "off",
        )

    def test_real_monitor_child_cleans_socket_on_off_and_quit(self):
        """End-to-end test with isolated settings/runtime and no GUI or sleep."""
        with TemporaryDirectory(prefix="isc-tray-runtime-test-") as tmp:
            root = Path(tmp)
            config_root = root / "config"
            runtime_root = root / "run"
            runtime_root.mkdir(mode=0o700)
            with patch.dict(os.environ, {
                "XDG_CONFIG_HOME": str(config_root),
                "XDG_RUNTIME_DIR": str(runtime_root),
            }):
                set_mode("monitor")
                supervisor = TrayMonitorProcess()
                address = runtime_root / "isc-monitor" / "events.sock"
                try:
                    self.assertEqual(
                        supervisor.reconcile(
                            mode="monitor", config_valid=True,
                        ), "starting",
                    )
                    deadline = time.monotonic() + 5
                    while not address.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(address.exists(), "no monitor socket")
                    self.assertEqual(
                        supervisor.reconcile(
                            mode="monitor", config_valid=True,
                        ), "running",
                    )
                    self.assertIsNotNone(discover_monitor_socket())
                finally:
                    supervisor.close()
                self.assertFalse(address.exists())
                self.assertFalse((runtime_root / "isc-monitor").exists())
                # The MONITOR choice survives a tray exit: its preference is
                # independent of whether a receiver process is running.
                self.assertEqual(read_mode().mode, "monitor")


if __name__ == "__main__":
    unittest.main()

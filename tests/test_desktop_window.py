"""No-GUI regression tests for ISC's optional visible GTK window."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.desktop_window import (
    DesktopWindow, _launch_tray, _minimal_desktop_env, run_window,
)
from isc.receiver import MonitorHealth
from isc.settings import ModeConfigError, ModeStatus


class DesktopWindowTests(unittest.TestCase):
    @patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""})
    def test_without_display_fails_without_launching_gui(self):
        self.assertEqual(run_window(), 2)

    @patch.dict(os.environ, {
        "DISPLAY": ":10",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "PRIVATE_API_TOKEN": "DO_NOT_FORWARD",
        "PATH": "/usr/bin",
    })
    def test_tray_environment_has_no_unrelated_secrets(self):
        env = _minimal_desktop_env()
        self.assertEqual(env["DISPLAY"], ":10")
        self.assertNotIn("PRIVATE_API_TOKEN", env)
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(env["PYTHONPATH"], str(
            Path(__file__).resolve().parents[1] / "src"
        ))

    @patch("isc.desktop_window.subprocess.Popen")
    @patch("isc.desktop_window.backend_probe",
           return_value=("appindicator_binding_unavailable",))
    def test_tray_dependency_failure_does_not_spawn(self, probe, spawn):
        self.assertFalse(_launch_tray())
        spawn.assert_not_called()

    @patch("isc.desktop_window.subprocess.Popen")
    @patch("isc.desktop_window.backend_probe", return_value=())
    @patch.dict(os.environ, {"PRIVATE_PASSWORD": "NO"})
    def test_tray_started_only_on_explicit_action(self, probe, spawn):
        self.assertTrue(_launch_tray())
        spawn.assert_called_once()
        args, kwargs = spawn.call_args
        self.assertEqual(args[0], [sys.executable, "-B", "-m", "isc", "tray"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertTrue(kwargs["close_fds"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotIn("PRIVATE_PASSWORD", kwargs["env"])

    @patch("isc.desktop_window.subprocess.Popen", side_effect=OSError)
    @patch("isc.desktop_window.backend_probe", return_value=())
    def test_tray_start_error_is_controlled(self, probe, spawn):
        self.assertFalse(_launch_tray())

    @patch("isc.desktop_window.read_monitor_health")
    @patch("isc.desktop_window.read_mode", return_value=ModeStatus("monitor", True))
    def test_window_status_is_non_authoritative(self, mode, health):
        health.return_value = MonitorHealth(
            accepted=5, rejected=0, observed_active=1,
            observed_waiting_human=0, observed_unknown=0,
            last_event_age_seconds=2,
            process_roots=3, process_descendants=8,
            process_visibility_races=1, process_identity_changes=0,
            process_sample_age_seconds=5,
        )
        controller = object.__new__(DesktopWindow)
        controller.status_label = Mock()
        self.assertTrue(controller.refresh())
        rendered = controller.status_label.set_text.call_args.args[0]
        self.assertIn("MONITOR", rendered)
        self.assertIn("3 roots, 8 visible children", rendered)
        self.assertIn("NOT VERIFIED", rendered)
        self.assertIn("No automatic suspend.", rendered)
        self.assertNotIn("process_30238", rendered)

    @patch("isc.desktop_window.read_monitor_health", return_value=None)
    @patch("isc.desktop_window.read_mode", return_value=ModeStatus("off", True))
    def test_missing_receiver_is_unavailable_not_idle(self, mode, health):
        controller = object.__new__(DesktopWindow)
        controller.status_label = Mock()
        controller.refresh()
        label = controller.status_label.set_text.call_args.args[0]
        self.assertIn("unavailable or not responding", label)
        self.assertIn("OFF", label)

    @patch("isc.desktop_window.set_mode")
    @patch("isc.desktop_window.read_mode", return_value=ModeStatus("off", True))
    @patch("isc.desktop_window.read_monitor_health", return_value=None)
    def test_only_off_monitor_choices_allowed(self, health, mode, set_pref):
        controller = object.__new__(DesktopWindow)
        controller.status_label = Mock()
        controller.set_monitor_mode("on")
        set_pref.assert_not_called()
        controller.set_monitor_mode("monitor")
        set_pref.assert_called_once_with("monitor")

    @patch("isc.desktop_window.set_mode", side_effect=ModeConfigError("invalid"))
    @patch("isc.desktop_window.read_mode", return_value=ModeStatus("off", True))
    @patch("isc.desktop_window.read_monitor_health", return_value=None)
    def test_mode_write_failure_reported_without_crashing(
        self, health, mode, set_pref,
    ):
        controller = object.__new__(DesktopWindow)
        controller.status_label = Mock()
        controller._message = Mock()
        controller.set_monitor_mode("off")
        self.assertTrue(controller._message.called)


if __name__ == "__main__":
    unittest.main()

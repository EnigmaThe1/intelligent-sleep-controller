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
from isc.keepawake import KeepAwakeError, KeepAwakeStatus
from isc.native_controls import NativeControlError


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


    @patch("isc.desktop_window.read_monitor_health", return_value=None)
    @patch("isc.desktop_window.read_mode", return_value=ModeStatus("on", True))
    def test_unsupported_on_is_never_shown_as_active(self, mode, health):
        controller = object.__new__(DesktopWindow)
        controller.status_label = Mock()
        controller.refresh()
        text = controller.status_label.set_text.call_args.args[0]
        self.assertIn("OFF (invalid settings)", text)

    @patch("isc.desktop_window.read_monitor_health", return_value=None)
    @patch("isc.desktop_window.read_mode", return_value=ModeStatus("monitor", True))
    def test_keepawake_status_shows_remaining_minutes(self, mode, health):
        controller = object.__new__(DesktopWindow)
        controller.status_label = Mock()
        controller.awake_label = Mock()
        controller._awake = Mock()
        controller._awake.status.return_value = KeepAwakeStatus(True, 64)
        controller.refresh()
        controller.awake_label.set_text.assert_called_once_with(
            "Keep Awake: active (2 min remaining)"
        )
        controller._awake.start.assert_not_called()

    @patch("isc.desktop_window.read_monitor_health", return_value=None)
    @patch("isc.desktop_window.read_mode", return_value=ModeStatus("monitor", True))
    def test_keepawake_indefinite_status(self, mode, health):
        controller = object.__new__(DesktopWindow)
        controller.status_label = Mock()
        controller.awake_label = Mock()
        controller._awake = Mock()
        controller._awake.status.return_value = KeepAwakeStatus(True, None)
        controller.refresh()
        self.assertIn(
            "window must stay open",
            controller.awake_label.set_text.call_args.args[0],
        )

    def test_keepawake_starts_only_with_explicit_click(self):
        controller = object.__new__(DesktopWindow)
        controller._awake = Mock()
        controller._awake.start.return_value = KeepAwakeStatus(True, 1800)
        controller._message = Mock()
        controller.refresh = Mock()
        controller.start_keep_awake(30)
        controller._awake.start.assert_called_once_with(30)
        controller._message.assert_not_called()
        controller.refresh.assert_called_once()

    def test_indefinite_keepawake_is_explicit(self):
        controller = object.__new__(DesktopWindow)
        controller._awake = Mock()
        controller._awake.start.return_value = KeepAwakeStatus(True, None)
        controller.refresh = Mock()
        controller.start_keep_awake(None)
        controller._awake.start.assert_called_once_with(None)

    def test_invalid_keepawake_duration_does_not_start(self):
        controller = object.__new__(DesktopWindow)
        controller._awake = Mock()
        controller.start_keep_awake(999)
        controller._awake.start.assert_not_called()

    def test_failed_native_keepawake_reports_error(self):
        controller = object.__new__(DesktopWindow)
        controller._awake = Mock()
        controller._awake.start.side_effect = KeepAwakeError("private")
        controller._message = Mock()
        controller.refresh = Mock()
        controller.start_keep_awake(30)
        controller._message.assert_called_once()
        self.assertNotIn("private", str(controller._message.call_args))

    def test_cancel_targets_only_window_owned_manager(self):
        controller = object.__new__(DesktopWindow)
        controller._awake = Mock()
        controller.refresh = Mock()
        controller.cancel_keep_awake()
        controller._awake.cancel.assert_called_once_with()
        controller.refresh.assert_called_once()

    def test_failed_cancellation_reports_error(self):
        controller = object.__new__(DesktopWindow)
        controller._awake = Mock()
        controller._awake.cancel.side_effect = KeepAwakeError("private")
        controller._message = Mock()
        controller.refresh = Mock()
        controller.cancel_keep_awake()
        controller._message.assert_called_once()
        self.assertNotIn("private", str(controller._message.call_args))

    @patch("isc.desktop_window.open_power_settings")
    def test_system_power_settings_only_open_on_click(self, opener):
        controller = object.__new__(DesktopWindow)
        controller.open_system_power()
        opener.assert_called_once_with()

    @patch("isc.desktop_window.open_power_settings",
           side_effect=NativeControlError("private system details"))
    def test_settings_launch_failure_is_generic_and_does_not_leak(self, opener):
        controller = object.__new__(DesktopWindow)
        controller._message = Mock()
        controller.open_system_power()
        controller._message.assert_called_once()
        self.assertIn("No settings were changed",
                      str(controller._message.call_args))
        self.assertNotIn("private system details",
                         str(controller._message.call_args))

    @patch("isc.desktop_window.open_power_settings",
           side_effect=AssertionError("settings must not open automatically"))
    @patch("isc.desktop_window.set_mode")
    @patch("isc.desktop_window.read_mode", return_value=ModeStatus("off", True))
    @patch("isc.desktop_window.read_monitor_health", return_value=None)
    def test_changing_off_never_opens_gnome_settings(
        self, health, mode, setter, opener,
    ):
        controller = object.__new__(DesktopWindow)
        controller.status_label = Mock()
        controller.set_monitor_mode("off")
        setter.assert_called_once_with("off")
        opener.assert_not_called()

    def test_window_shutdown_closes_owned_keepawake(self):
        controller = object.__new__(DesktopWindow)
        controller._awake = Mock()
        controller.Gtk = Mock()
        controller._close_window()
        controller._awake.close.assert_called_once()
        controller.Gtk.main_quit.assert_called_once()


if __name__ == "__main__":
    unittest.main()

import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.native_controls import (
    NativeControlError, PowerProfiles, power_profiles, set_profile,
    display_support, display_off, open_power_settings,
)


class NativeControlsTests(unittest.TestCase):
    @patch("isc.native_controls._run")
    def test_profiles_and_current_come_from_host(self, run):
        run.side_effect = [
            SimpleNamespace(returncode=0, stdout=
                "  performance:\n  Driver: test\n* balanced:\n  power-saver:\n"),
            SimpleNamespace(returncode=0, stdout="balanced\n"),
        ]
        result = power_profiles()
        self.assertEqual(result.current, "balanced")
        self.assertEqual(
            result.available, ("power-saver", "balanced", "performance"),
        )
        self.assertEqual(run.call_args_list[0].args[0], ["powerprofilesctl", "list"])
        self.assertEqual(run.call_args_list[1].args[0], ["powerprofilesctl", "get"])

    @patch("isc.native_controls._run")
    def test_absent_performance_profile_not_invented(self, run):
        run.side_effect = [
            SimpleNamespace(returncode=0, stdout="* balanced:\n  power-saver:\n"),
            SimpleNamespace(returncode=0, stdout="balanced"),
        ]
        self.assertNotIn("performance", power_profiles().available)

    @patch("isc.native_controls._run")
    def test_bad_response_fails_closed(self, run):
        run.return_value = SimpleNamespace(returncode=1, stdout="")
        with self.assertRaises(NativeControlError):
            power_profiles()

    @patch("isc.native_controls.power_profiles")
    @patch("isc.native_controls._run")
    def test_switch_uses_only_system_profile(self, run, states):
        states.side_effect = [
            PowerProfiles("balanced", ("power-saver", "balanced")),
            PowerProfiles("power-saver", ("power-saver", "balanced")),
        ]
        run.return_value = SimpleNamespace(returncode=0)
        self.assertEqual(set_profile("power-saver").current, "power-saver")
        run.assert_called_once_with(["powerprofilesctl", "set", "power-saver"])

    @patch("isc.native_controls.power_profiles", return_value=
        PowerProfiles("balanced", ("balanced",)))
    @patch("isc.native_controls._run")
    def test_unsupported_profile_never_changed(self, run, states):
        with self.assertRaises(NativeControlError):
            set_profile("performance")
        run.assert_not_called()

    @patch("isc.native_controls._run")
    def test_display_off_requires_confirmation(self, run):
        self.assertEqual(display_off(), "confirmation_required")
        run.assert_not_called()

    @patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"})
    @patch("isc.native_controls._run")
    def test_wayland_not_given_fake_x11_support(self, run):
        self.assertEqual(display_support(), (False, "requires_xorg"))
        with self.assertRaises(NativeControlError):
            display_off(confirm=True)
        run.assert_not_called()

    @patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"})
    @patch("isc.native_controls._run")
    def test_x11_dpms_off_without_system_suspend(self, run):
        run.side_effect = [
            SimpleNamespace(returncode=0, stdout="DPMS is Enabled\n"),
            SimpleNamespace(returncode=0, stdout=""),
        ]
        self.assertEqual(display_off(confirm=True), "display_off_requested")
        self.assertEqual(run.call_args_list[0].args[0], ["xset", "q"])
        self.assertEqual(run.call_args_list[1].args[0],
                         ["xset", "dpms", "force", "off"])

    @patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"})
    @patch("isc.native_controls._run")
    def test_dpms_disabled_not_reconfigured(self, run):
        run.return_value = SimpleNamespace(returncode=0,
                                            stdout="DPMS is Disabled\n")
        with self.assertRaisesRegex(NativeControlError, "dpms_not"):
            display_off(confirm=True)
        run.assert_called_once_with(["xset", "q"])

    @patch.dict(os.environ, {"DISPLAY": ":0"})
    @patch("isc.native_controls.subprocess.Popen")
    def test_open_existing_os_power_options(self, spawn):
        open_power_settings()
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.args[0],
                         ["gnome-control-center", "power"])

    @patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""})
    @patch("isc.native_controls.subprocess.Popen")
    def test_no_graphical_session_cannot_launch_panel(self, spawn):
        with self.assertRaisesRegex(NativeControlError, "no_graphical"):
            open_power_settings()
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()

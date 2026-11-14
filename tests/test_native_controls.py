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

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0",
        "XDG_CURRENT_DESKTOP": "plasma:KDE", "WAYLAND_DISPLAY": "wayland-0",
    })
    @patch("isc.native_controls.shutil.which", return_value=None)
    @patch("isc.native_controls._run")
    def test_kde_without_native_utility_is_not_incorrectly_supported(
        self, run, which,
    ):
        self.assertEqual(display_support(), (False, "kscreen_dpms_unavailable"))
        with self.assertRaises(NativeControlError):
            display_off(confirm=True)
        run.assert_not_called()

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME",
        "DISPLAY": ":0",
    })
    @patch("isc.native_controls._run")
    def test_wayland_mutter_power_save_readonly_supported_on_user_desktop(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout="i 0\n")
        self.assertEqual(
            display_support(), (True, "wayland_mutter_standby_available"),
        )
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][:3], [
            "busctl", "--user", "get-property",
        ])

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME",
    })
    @patch("isc.native_controls._run")
    def test_wayland_rejects_unknown_or_non_on_display_status(self, run):
        for output in ("i -1\n", "i 1\n", "i 3\n", "bad", "i 22\n"):
            with self.subTest(output=output):
                run.return_value = SimpleNamespace(
                    returncode=0, stdout=output,
                )
                self.assertFalse(display_support()[0])

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME",
    })
    @patch("isc.native_controls._run")
    def test_wayland_refuses_unavailable_compositor(self, run):
        run.return_value = SimpleNamespace(returncode=1, stdout="")
        self.assertEqual(
            display_support(), (False, "mutter_display_unavailable"),
        )

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME",
    })
    @patch("isc.native_controls._run")
    @patch("isc.native_controls.secrets.token_hex",
           return_value="0123456789abcdef")
    def test_wayland_recovery_timer_is_armed_before_screen_standby(
        self, token, run,
    ):
        run.side_effect = [
            SimpleNamespace(returncode=0, stdout="i 0\n"),
            SimpleNamespace(returncode=0, stdout=""),
            SimpleNamespace(returncode=0, stdout="active\n"),
            SimpleNamespace(returncode=0, stdout="i 0\n"),
            SimpleNamespace(returncode=0, stdout=""),
        ]
        self.assertEqual(
            display_off(confirm=True),
            "display_standby_requested_with_20s_recovery",
        )
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[0][:3], ["busctl", "--user", "get-property"])
        self.assertEqual(calls[1][:2], ["systemd-run", "--user"])
        self.assertIn("--on-active=20s", calls[1])
        self.assertIn("--timer-property=AccuracySec=1s", calls[1])
        self.assertIn("isc-display-restore-0123456789abcdef", str(calls[1]))
        self.assertEqual(calls[1][-2:], ["i", "0"])
        self.assertEqual(calls[2], [
            "systemctl", "--user", "is-active",
            "isc-display-restore-0123456789abcdef.timer",
        ])
        self.assertEqual(calls[3][:3], ["busctl", "--user", "get-property"])
        self.assertEqual(calls[4][:3], ["busctl", "--user", "set-property"])
        self.assertEqual(calls[4][-2:], ["i", "1"])
        # Never a computer suspend, login change or X11 command.
        joined = " ".join(" ".join(parts) for parts in calls)
        self.assertNotIn("suspend", joined)
        self.assertNotIn("xset", joined)

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME",
    })
    @patch("isc.native_controls._run")
    def test_no_wayland_standby_when_recovery_timer_cannot_be_started(self, run):
        run.side_effect = [
            SimpleNamespace(returncode=0, stdout="i 0\n"),
            SimpleNamespace(returncode=1, stdout=""),
        ]
        with self.assertRaisesRegex(NativeControlError,
                                    "wayland_recovery_timer_unavailable"):
            display_off(confirm=True)
        self.assertEqual(run.call_count, 2)
        self.assertFalse(any(
            call.args[0][:3] == ["busctl", "--user", "set-property"]
            for call in run.call_args_list
        ))

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME",
    })
    @patch("isc.native_controls._run")
    def test_no_wayland_standby_when_recovery_timer_is_not_active(self, run):
        run.side_effect = [
            SimpleNamespace(returncode=0, stdout="i 0\n"),
            SimpleNamespace(returncode=0, stdout=""),
            SimpleNamespace(returncode=3, stdout="inactive\n"),
        ]
        with self.assertRaisesRegex(NativeControlError,
                                    "wayland_recovery_timer_unavailable"):
            display_off(confirm=True)
        self.assertEqual(run.call_count, 3)

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME",
    })
    @patch("isc.native_controls._run")
    def test_no_wayland_standby_if_screen_mode_changes_after_timer(self, run):
        run.side_effect = [
            SimpleNamespace(returncode=0, stdout="i 0\n"),
            SimpleNamespace(returncode=0, stdout=""),
            SimpleNamespace(returncode=0, stdout="active\n"),
            SimpleNamespace(returncode=0, stdout="i 1\n"),
        ]
        with self.assertRaisesRegex(NativeControlError,
                                    "mutter_display_changed_before_standby"):
            display_off(confirm=True)
        self.assertEqual(run.call_count, 4)

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME",
    })
    @patch("isc.native_controls._run")
    def test_wayland_no_confirmation_never_arms_recovery_or_blanks(self, run):
        self.assertEqual(display_off(confirm=False), "confirmation_required")
        run.assert_not_called()

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME",
    })
    @patch("isc.native_controls._run")
    def test_wayland_reports_native_standby_failure(self, run):
        run.side_effect = [
            SimpleNamespace(returncode=0, stdout="i 0\n"),
            SimpleNamespace(returncode=0, stdout=""),
            SimpleNamespace(returncode=0, stdout="active\n"),
            SimpleNamespace(returncode=0, stdout="i 0\n"),
            SimpleNamespace(returncode=1, stdout=""),
        ]
        with self.assertRaisesRegex(NativeControlError,
                                    "mutter_standby_request_failed"):
            display_off(confirm=True)
        self.assertEqual(run.call_count, 5)

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE",
        "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0",
    })
    @patch("isc.native_controls.shutil.which", return_value="/usr/bin/kscreen-doctor")
    @patch("isc.native_controls._run")
    def test_kde_wayland_dpms_is_selected_only_when_outputs_are_on(
        self, run, which,
    ):
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout=("dpms mode for screen DP-1: on\n"
                    "dpms mode for screen HDMI-A-1: on\n"),
        )
        self.assertEqual(display_support(),
                         (True, "wayland_kscreen_dpms_available"))
        run.assert_called_once_with([
            "/usr/bin/kscreen-doctor", "--dpms", "show",
        ])

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "PLASMA",
        "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0",
    })
    @patch("isc.native_controls.shutil.which", return_value="/usr/bin/kscreen-doctor")
    @patch("isc.native_controls._run")
    def test_kde_wayland_refuses_mixed_or_unverifiable_dpms(
        self, run, which,
    ):
        for output in (
            "dpms mode for screen DP-1: on\ndpms mode for screen HDMI-A-1: off\n",
            "unknown state\n",
            "dpms mode for screen DP-1: on\nunknown line\n",
            "",
        ):
            with self.subTest(output=output):
                run.return_value = SimpleNamespace(returncode=0, stdout=output)
                self.assertFalse(display_support()[0])

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE",
        "WAYLAND_DISPLAY": "wayland-0",
    })
    @patch("isc.native_controls.shutil.which", return_value="/usr/bin/kscreen-doctor")
    @patch("isc.native_controls.secrets.token_hex",
           return_value="0123456789abcdef")
    @patch("isc.native_controls._run")
    def test_kde_arms_verified_recovery_before_issuing_dpms_off(
        self, run, token, which,
    ):
        mode = "dpms mode for screen DP-1: on\n"
        run.side_effect = [
            SimpleNamespace(returncode=0, stdout=mode),
            SimpleNamespace(returncode=0, stdout=""),
            SimpleNamespace(returncode=0, stdout="active\n"),
            SimpleNamespace(returncode=0, stdout=mode),
            SimpleNamespace(returncode=0, stdout=""),
        ]
        self.assertEqual(
            display_off(confirm=True),
            "display_standby_requested_with_20s_recovery",
        )
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[0], ["/usr/bin/kscreen-doctor", "--dpms", "show"])
        self.assertEqual(calls[1][:2], ["systemd-run", "--user"])
        self.assertIn("--on-active=20s", calls[1])
        self.assertIn("--setenv=WAYLAND_DISPLAY=wayland-0", calls[1])
        self.assertIn("--setenv=QT_QPA_PLATFORM=wayland", calls[1])
        self.assertEqual(calls[1][-3:],
                         ["/usr/bin/kscreen-doctor", "--dpms", "on"])
        self.assertEqual(calls[2], [
            "systemctl", "--user", "is-active",
            "isc-display-restore-0123456789abcdef.timer",
        ])
        self.assertEqual(calls[3], ["/usr/bin/kscreen-doctor", "--dpms", "show"])
        self.assertEqual(calls[4], ["/usr/bin/kscreen-doctor", "--dpms", "off"])

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE",
        "WAYLAND_DISPLAY": "wayland-0",
    })
    @patch("isc.native_controls.shutil.which", return_value="/usr/bin/kscreen-doctor")
    @patch("isc.native_controls._run")
    def test_kde_cannot_blank_if_recovery_timer_is_unavailable(
        self, run, which,
    ):
        run.side_effect = [
            SimpleNamespace(returncode=0,
                            stdout="dpms mode for screen DP-1: on\n"),
            SimpleNamespace(returncode=1, stdout=""),
        ]
        with self.assertRaisesRegex(NativeControlError,
                                    "wayland_recovery_timer_unavailable"):
            display_off(confirm=True)
        self.assertEqual(run.call_count, 2)
        self.assertNotIn("off", repr(run.call_args_list))

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE",
        "WAYLAND_DISPLAY": "wayland-0",
    })
    @patch("isc.native_controls.shutil.which", return_value="/usr/bin/kscreen-doctor")
    @patch("isc.native_controls._run")
    def test_kde_native_status_change_cancels_standby(
        self, run, which,
    ):
        run.side_effect = [
            SimpleNamespace(returncode=0,
                            stdout="dpms mode for screen DP-1: on\n"),
            SimpleNamespace(returncode=0, stdout=""),
            SimpleNamespace(returncode=0, stdout="active\n"),
            SimpleNamespace(returncode=0,
                            stdout="dpms mode for screen DP-1: off\n"),
        ]
        with self.assertRaisesRegex(NativeControlError,
                                    "kscreen_display_changed_before_standby"):
            display_off(confirm=True)
        self.assertEqual(run.call_count, 4)

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE",
        "WAYLAND_DISPLAY": "wayland-0",
    })
    @patch("isc.native_controls.shutil.which", return_value="/usr/bin/kscreen-doctor")
    @patch("isc.native_controls._run")
    def test_kde_standby_command_failure_is_not_reported_as_success(
        self, run, which,
    ):
        mode = "dpms mode for screen DP-1: on\n"
        run.side_effect = [
            SimpleNamespace(returncode=0, stdout=mode),
            SimpleNamespace(returncode=0, stdout=""),
            SimpleNamespace(returncode=0, stdout="active\n"),
            SimpleNamespace(returncode=0, stdout=mode),
            SimpleNamespace(returncode=1, stdout=""),
        ]
        with self.assertRaisesRegex(NativeControlError,
                                    "kscreen_standby_request_failed"):
            display_off(confirm=True)

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE",
        "WAYLAND_DISPLAY": "wayland-0",
    })
    @patch("isc.native_controls.shutil.which", return_value="/usr/bin/kscreen-doctor")
    @patch("isc.native_controls._run")
    def test_kde_recovery_rejects_malformed_wayland_socket(
        self, run, which,
    ):
        from isc.native_controls import _schedule_wayland_display_restore
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0\nINJECTION"}):
            self.assertFalse(_schedule_wayland_display_restore("kde-wayland"))
        run.assert_not_called()

    @patch.dict(os.environ, {
        "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE",
        "WAYLAND_DISPLAY": "wayland-0", "PRIVATE_TOKEN": "do-not-forward",
    })
    @patch("isc.native_controls.subprocess.run")
    def test_kde_utility_gets_wayland_socket_and_no_private_environment(
        self, run,
    ):
        from isc.native_controls import _run
        run.return_value = SimpleNamespace(returncode=0, stdout="")
        _run(["/usr/bin/kscreen-doctor", "--dpms", "show"])
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["QT_QPA_PLATFORM"], "wayland")
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-0")
        self.assertNotIn("PRIVATE_TOKEN", env)

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

    @patch.dict(os.environ, {"DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "GNOME"})
    @patch("isc.native_controls.subprocess.Popen")
    def test_open_existing_os_power_options(self, spawn):
        open_power_settings()
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.args[0],
                         ["gnome-control-center", "power"])

    @patch.dict(os.environ, {"DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "KDE"})
    @patch("isc.native_controls.subprocess.Popen")
    def test_kde_settings_launches_plasma_and_not_gnome(self, spawn):
        open_power_settings()
        self.assertEqual(spawn.call_args.args[0], ["systemsettings"])

    @patch.dict(os.environ, {"DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "sway",
                             "XDG_SESSION_DESKTOP": "sway"})
    @patch("isc.native_controls.subprocess.Popen")
    def test_unknown_desktop_settings_refused_without_launch(self, spawn):
        with self.assertRaisesRegex(NativeControlError, "unsupported"):
            open_power_settings()
        spawn.assert_not_called()

    @patch.dict(os.environ, {
        "DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "GNOME",
        "SECRET_AGENT_TOKEN": "NEVER_FORWARD",
    })
    @patch("isc.native_controls.subprocess.Popen")
    def test_native_gui_does_not_inherit_unrelated_tokens(self, spawn):
        open_power_settings()
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["DISPLAY"], ":0")
        self.assertNotIn("SECRET_AGENT_TOKEN", env)
        self.assertNotIn("NEVER_FORWARD", repr(env))

    @patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""})
    @patch("isc.native_controls.subprocess.Popen")
    def test_no_graphical_session_cannot_launch_panel(self, spawn):
        with self.assertRaisesRegex(NativeControlError, "no_graphical"):
            open_power_settings()
        spawn.assert_not_called()


    @patch.dict(os.environ, {
        "PATH": "/usr/bin", "AGENT_SECRET_TOKEN": "PRIVATE_VALUE",
    })
    @patch("isc.native_controls.subprocess.run")
    def test_profile_probe_does_not_forward_agent_tokens(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout="")
        from isc.native_controls import _run
        _run(["powerprofilesctl", "get"])
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertNotIn("AGENT_SECRET_TOKEN", env)
        self.assertNotIn("PRIVATE_VALUE", str(env))


if __name__ == "__main__":
    unittest.main()

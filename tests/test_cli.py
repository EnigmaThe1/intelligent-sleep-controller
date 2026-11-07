import contextlib
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.cli import main


class CLITests(unittest.TestCase):
    @patch("isc.cli.read_gnome_idle_ms", return_value=13)
    def test_status_is_read_only_and_explicit(self, idle_probe):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["status"]), 0)
        text = output.getvalue()
        self.assertIn("automatic suspend: NOT IMPLEMENTED", text)
        self.assertIn("GNOME user idle: 0.013 seconds (13 ms)", text)
        self.assertIn("No system configuration has been changed.", text)

    @patch("isc.cli.read_gnome_idle_ms", return_value=None)
    def test_idle_unknown_does_not_claim_safety(self, idle_probe):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["idle"]), 0)
        text = output.getvalue()
        self.assertIn("unknown", text)
        self.assertIn("NOT proof that background work has stopped", text)
        self.assertIn("NOT IMPLEMENTED", text)

    @patch("isc.cli.read_inhibitors", return_value=None)
    def test_inhibitor_unavailable_is_not_assumed_empty(self, probe):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["inhibitors"]), 0)
        self.assertIn("UNKNOWN", output.getvalue())
        self.assertIn("cannot assume no sleep blockers", output.getvalue())

    @patch("isc.cli.read_inhibitors")
    def test_inhibitors_summary_discloses_no_private_metadata(self, probe):
        from isc.inhibitors import Inhibitor
        probe.return_value = (
            Inhibitor(frozenset(("sleep",)), "delay"),
            Inhibitor(frozenset(("idle",)), "block"),
            Inhibitor(frozenset(("shutdown",)), "block"),
        )
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["inhibitors"]), 0)
        text = output.getvalue()
        self.assertIn("Blocking automatic sleep/idle: 1", text)
        self.assertIn("Sleep delays (not permanent blockers): 1", text)
        self.assertIn("Other inhibitor locks: 1", text)

    @patch("isc.cli.read_inhibitors", return_value=())
    @patch("isc.cli.read_gnome_idle_ms", return_value=2200000)
    def test_check_never_claims_host_safe_on_idle_alone(self, idle_probe, inhibitor_probe):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["check"]), 0)
        text = output.getvalue()
        self.assertIn("Verdict: DO NOT AUTO-SUSPEND", text)
        self.assertIn("workload_coverage_unverified", text)
        self.assertIn("scheduled_work_safety_unverified", text)
        self.assertIn("NOT IMPLEMENTED", text)

    @patch("isc.cli.read_inhibitors", return_value=None)
    @patch("isc.cli.read_gnome_idle_ms", return_value=None)
    def test_check_missing_probes_fail_closed(self, idle_probe, inhibitor_probe):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["check"]), 0)
        text = output.getvalue()
        self.assertIn("user_idle_unverified", text)
        self.assertIn("sleep_inhibitor_present", text)
        self.assertIn("Verdict: DO NOT AUTO-SUSPEND", text)


    @patch("isc.cli.read_process_candidates", return_value=None)
    @patch("isc.cli.read_gnome_inhibit_flags")
    @patch("isc.cli.read_inhibitors", return_value=())
    @patch("isc.cli.read_gnome_idle_ms", return_value=2400000)
    def test_check_gnome_flags_are_conservative(
        self, idle, logind, gnome, processes,
    ):
        from isc.gnome_inhibitors import GnomeInhibitFlags
        for flags, expected in (
            (GnomeInhibitFlags(False, False), False),
            (GnomeInhibitFlags(True, False), True),
            (GnomeInhibitFlags(False, True), True),
            (GnomeInhibitFlags(None, False), True),
            (GnomeInhibitFlags(False, None), True),
        ):
            with self.subTest(flags=flags):
                gnome.return_value = flags
                output = StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(["check"]), 0)
                result = output.getvalue()
                self.assertIn("GNOME multi-session inhibitor coverage: NOT VERIFIED", result)
                self.assertEqual("sleep_inhibitor_present" in result, expected)
                self.assertIn("workload_coverage_unverified", result)
                self.assertIn("scheduled_work_safety_unverified", result)
                self.assertIn("Verdict: DO NOT AUTO-SUSPEND", result)

    @patch("isc.cli.read_process_candidates")
    def test_processes_never_claim_a_pid_is_active_or_idle(self, scanner):
        from isc.processes import ProcessCandidate, ProcessScan
        scanner.return_value = ProcessScan((ProcessCandidate(42, "codex"),))
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["processes"]), 0)
        text = output.getvalue()
        self.assertIn("pid=42 name=codex state=UNKNOWN", text)
        self.assertIn("NOT IMPLEMENTED", text)

    @patch("isc.cli.read_process_candidates", return_value=None)
    def test_process_inventory_failure_is_unknown(self, scanner):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["processes"]), 0)
        self.assertIn("inventory: UNKNOWN", output.getvalue())

    @patch("isc.cli.read_process_candidates")
    @patch("isc.cli.read_inhibitors", return_value=())
    @patch("isc.cli.read_gnome_idle_ms", return_value=2100000)
    def test_check_explicit_unknown_processes(self, idle, inhibitors, scanner):
        from isc.processes import ProcessCandidate, ProcessScan
        scanner.return_value = ProcessScan((ProcessCandidate(67, "codex"),))
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["check"]), 0)
        text = output.getvalue()
        self.assertIn("Process candidates requiring lifecycle verification: 1", text)
        self.assertIn("work_unknown:process_67", text)
        self.assertIn("DO NOT AUTO-SUSPEND", text)


    @patch("isc.cli.read_hook_payload")
    def test_hook_probe_does_not_print_private_fields(self, parser):
        from isc.hook_payload import HookObservation
        from isc.policy import WorkState
        parser.return_value = HookObservation(
            source="claude_code",
            event_name="Stop",
            session_fingerprint="abc123secret",
            state=WorkState.UNKNOWN,
        )
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["hook-probe", "--source", "claude_code"]), 0)
        text = output.getvalue()
        self.assertIn("Conservative interpretation: unknown", text)
        self.assertNotIn("abc123secret", text)
        self.assertIn("NOT IMPLEMENTED", text)


    @patch("isc.cli.send_discovered_observation", return_value=False)
    @patch("isc.cli.read_hook_payload")
    def test_sender_remains_silent_when_no_receiver(self, read, relay):
        from isc.hook_payload import HookObservation
        from isc.policy import WorkState
        read.return_value = HookObservation(
            "codex", "Stop", "f" * 32, WorkState.UNKNOWN,
        )
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["send-hook", "--source", "codex"]), 0)
        self.assertEqual(output.getvalue(), "")
        relay.assert_called_once()


    @patch("isc.cli.default_hook_targets")
    def test_hook_plan_shows_only_event_names_no_commands(self, targets):
        from tempfile import TemporaryDirectory
        import json
        with TemporaryDirectory() as root:
            path = Path(root) / "config.json"
            path.write_text(json.dumps({
                "hooks": {
                    "Stop": [{"hooks": [{
                        "type": "command", "command": "PRIVATE_PASSWORD"
                    }]}]
                },
                "env": {"TOKEN": "DO_NOT_DISPLAY"},
            }), encoding="utf-8")
            targets.return_value = (("codex", path),)
            output = StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["hook-plan"]), 0)
            text = output.getvalue()
            self.assertIn("ISC hook event groups proposed: 8", text)
            self.assertIn("+ Stop", text)
            self.assertNotIn("PRIVATE_PASSWORD", text)
            self.assertNotIn("DO_NOT_DISPLAY", text)
            self.assertNotIn("command", text.lower().split("no existing hook")[0])


    @patch("isc.cli.modify_existing_hooks")
    def test_hook_enable_requires_explicit_confirm(self, writer):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                main(["hook-enable", "--source", "codex"]), 2,
            )
        writer.assert_not_called()
        self.assertIn("No changes made", output.getvalue())

    @patch("isc.cli.modify_existing_hooks")
    @patch("isc.cli.default_hook_targets")
    def test_hook_disable_only_selected_source(self, targets, writer):
        from isc.hook_install import InstallResult
        targets.return_value = (("codex", Path("/tmp/test-codex.json")),)
        writer.return_value = InstallResult(
            "codex", "disable", 8, True, Path("/tmp/private-backup"),
        )
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                main(["hook-disable", "--source", "codex", "--confirm"]), 0,
            )
        writer.assert_called_once_with(
            "codex", Path("/tmp/test-codex.json"), "disable",
        )
        self.assertIn("8 event group(s)", output.getvalue())


    @patch("isc.cli.read_mode")
    def test_mode_get_is_read_only(self, read):
        from isc.settings import ModeStatus
        read.return_value = ModeStatus("off", False)
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["mode"]), 0)
        self.assertIn("OFF", output.getvalue())
        self.assertIn("no file written", output.getvalue())

    @patch("isc.cli.set_mode")
    def test_mode_set_does_not_activate_sleep(self, setter):
        from isc.settings import ModeStatus
        setter.return_value = ModeStatus("monitor", True)
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["mode", "--set", "monitor"]), 0)
        setter.assert_called_once_with("monitor")
        self.assertIn("NOT IMPLEMENTED", output.getvalue())


    @patch("isc.cli.serve_monitor")
    def test_controlled_serve_reports_safe_summary(self, serve):
        from isc.service import MonitorSummary
        serve.return_value = MonitorSummary(
            "mode_off", 2, 0, (("claude_code", "SessionStart", 2),),
        )
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["serve", "--seconds", "0"]), 0)
        serve.assert_called_once_with(max_seconds=0)
        self.assertIn("Monitor stopped: mode_off", output.getvalue())
        self.assertIn("claude_code/SessionStart: 2", output.getvalue())
        self.assertIn("NOT IMPLEMENTED", output.getvalue())

    @patch("isc.cli.serve_monitor")
    def test_controlled_serve_refusal_is_safe(self, serve):
        from isc.service import MonitorUnavailable
        serve.side_effect = MonitorUnavailable("monitor_mode_not_selected")
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["serve"]), 2)
        self.assertIn("monitor_mode_not_selected", output.getvalue())


    @patch("isc.cli.assess_manual_suspend")
    @patch("isc.cli.request_manual_suspend")
    def test_manual_suspend_preview_does_not_request_power(self, request, assess):
        from isc.manual_power import SuspendAssessment
        assess.return_value = SuspendAssessment(True, "yes", (), 2)
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["suspend"]), 0)
        request.assert_not_called()
        self.assertIn("PREVIEW ONLY", output.getvalue())
        self.assertIn("NOT VERIFIED", output.getvalue())

    @patch("isc.cli.assess_manual_suspend")
    @patch("isc.cli.request_manual_suspend")
    def test_manual_sleep_alias_is_suspend_not_systemctl_sleep(self, request, assess):
        from isc.manual_power import SuspendAssessment
        assess.return_value = SuspendAssessment(True, "yes", (), 1)
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["sleep"]), 0)
        request.assert_not_called()
        self.assertIn("SUSPEND / SLEEP", output.getvalue())

    @patch("isc.cli.request_manual_suspend")
    def test_power_menu_never_suspends(self, request):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["power"]), 0)
        self.assertIn("native capability-checked manual actions", output.getvalue())
        request.assert_not_called()

    @patch("isc.cli.assess_manual_suspend")
    @patch("isc.cli.request_manual_suspend")
    def test_manual_suspend_never_bypasses_preflight_blockers(self, request, assess):
        from isc.manual_power import SuspendAssessment
        assess.return_value = SuspendAssessment(
            False, "yes", ("blocking_sleep_inhibitor_present",), 0,
        )
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["suspend", "--confirm"]), 2)
        request.assert_not_called()
        self.assertIn("No suspend request made.", output.getvalue())


    @patch("isc.cli.run_tray", return_value=0)
    def test_tray_cli_is_optional(self, launcher):
        self.assertEqual(main(["tray"]), 0)
        launcher.assert_called_once_with()

    @patch("isc.cli.backend_probe", return_value=("graphical_session_unavailable",))
    def test_tray_check_never_modifies_the_host(self, checker):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["tray-check"]), 2)
        self.assertIn("graphical_session_unavailable", output.getvalue())
        self.assertIn("No software or desktop settings changed.", output.getvalue())


    @patch("isc.cli.inspect_autostart")
    def test_autostart_status_is_read_only(self, inspector):
        from isc.autostart import AutostartStatus
        inspector.return_value = AutostartStatus("disabled")
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["autostart"]), 0)
        inspector.assert_called_once_with()
        self.assertIn("Status: DISABLED", output.getvalue())

    @patch("isc.cli.modify_autostart")
    def test_autostart_enable_requires_confirm(self, changer):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["autostart", "--startup", "enable"]), 2)
        changer.assert_not_called()
        self.assertIn("--confirm", output.getvalue())

    @patch("isc.cli.modify_autostart")
    def test_autostart_enable_confirmed_is_explicit(self, changer):
        from isc.autostart import AutostartStatus
        changer.return_value = AutostartStatus("enabled", changed=True)
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                main(["autostart", "--startup", "enable", "--confirm"]), 0,
            )
        changer.assert_called_once_with("enable")
        self.assertIn("Startup entry updated", output.getvalue())
        self.assertIn("No GNOME power settings", output.getvalue())

    @patch("isc.cli.modify_autostart")
    def test_autostart_disable_uses_same_confirmation_gate(self, changer):
        from isc.autostart import AutostartStatus
        changer.return_value = AutostartStatus("disabled", changed=True)
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                main(["autostart", "--startup", "disable", "--confirm"]), 0,
            )
        changer.assert_called_once_with("disable")


    @patch("isc.cli.KeepAwakeManager")
    def test_keep_awake_without_minutes_does_not_start(self, manager):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["keep-awake"]), 0)
        manager.assert_not_called()
        self.assertIn("No Keep Awake operation started.", output.getvalue())

    @patch("isc.cli.KeepAwakeManager")
    def test_keep_awake_explicit_minutes_and_foreground_release(self, manager):
        from isc.keepawake import KeepAwakeStatus
        lease = manager.return_value
        lease.start.return_value = KeepAwakeStatus(True, 900)
        lease.status.return_value = KeepAwakeStatus(False)
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["keep-awake", "--minutes", "15"]), 0)
        lease.start.assert_called_once_with(15)
        lease.close.assert_called_once_with()
        self.assertIn("Keep Awake verified", output.getvalue())

    @patch("isc.cli.KeepAwakeManager")
    def test_keep_awake_refused_does_not_leave_lease(self, manager):
        from isc.keepawake import KeepAwakeError
        manager.return_value.start.side_effect = KeepAwakeError("not_verified")
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["keep-awake", "--minutes", "30"]), 2)
        manager.return_value.close.assert_called_once_with()
        self.assertIn("not_verified", output.getvalue())


    @patch("isc.cli.KeepAwakeManager")
    def test_indefinite_keep_awake_is_explicit_and_cancellable(self, manager):
        from isc.keepawake import KeepAwakeStatus
        lease = manager.return_value
        lease.start.return_value = KeepAwakeStatus(True, None)
        lease.status.return_value = KeepAwakeStatus(False)
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["keep-awake", "--forever"]), 0)
        lease.start.assert_called_once_with(None)
        lease.close.assert_called_once_with()
        self.assertIn("until cancelled", output.getvalue())

    @patch("isc.cli.power_profiles")
    def test_power_profile_status_is_read_only(self, profiles):
        from isc.native_controls import PowerProfiles
        profiles.return_value = PowerProfiles("balanced", ("balanced", "power-saver"))
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["power-profiles"]), 0)
        self.assertIn("balanced", output.getvalue())

    @patch("isc.cli.set_profile")
    def test_power_profile_explicit_selection(self, change):
        from isc.native_controls import PowerProfiles
        change.return_value = PowerProfiles("power-saver", ("balanced", "power-saver"))
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["power-profiles", "--profile", "power-saver"]), 0)
        change.assert_called_once_with("power-saver")

    @patch("isc.cli.display_support", return_value=(True, "x11_dpms_available"))
    @patch("isc.cli.display_off")
    def test_display_off_preview_does_not_turn_off_screen(self, action, support):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["display-off"]), 0)
        action.assert_not_called()
        self.assertIn("PREVIEW ONLY", output.getvalue())

    @patch("isc.cli.open_power_settings")
    def test_power_settings_uses_original_gnome_ui(self, opener):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["power-settings"]), 0)
        opener.assert_called_once_with()

    @patch("isc.cli.assess_native_sleep")
    @patch("isc.cli.request_terminal_sleep")
    def test_hibernate_preview_never_requests_sleep(self, request, assess):
        from isc.native_sleep import NativeSleepAssessment
        assess.return_value = NativeSleepAssessment("hibernate", "yes", (), 0)
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["hibernate"]), 0)
        request.assert_not_called()
        self.assertIn("PREVIEW ONLY", output.getvalue())


    @patch("isc.cli.inspect_user_service")
    def test_user_service_status_is_read_only(self, inspect):
        from isc.user_service import UserServiceStatus
        inspect.return_value = UserServiceStatus(False, None, None)
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["service"]), 0)
        inspect.assert_called_once_with()
        self.assertIn("Installed: NO", output.getvalue())

    @patch("isc.cli.modify_user_service")
    def test_user_service_requires_explicit_confirmation(self, modify):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["service", "--service", "enable"]), 2)
        modify.assert_not_called()

    @patch("isc.cli.modify_user_service")
    def test_user_service_explicit_install_and_disable(self, modify):
        from isc.user_service import UserServiceStatus
        modify.return_value = UserServiceStatus(True, True, True)
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                main(["service", "--service", "enable", "--confirm"]), 0,
            )
        modify.assert_called_once_with("enable")
        self.assertIn("Automatic suspension remains disabled.", output.getvalue())

    @patch("isc.cli.monitor_service_loop", return_value=0)
    def test_monitor_service_mode_uses_user_service_loop(self, run):
        self.assertEqual(main(["monitor-service"]), 0)
        run.assert_called_once_with()


    @patch("isc.cli.read_native_activity")
    def test_native_activity_reports_partial_coverage_only(self, observed):
        from isc.native_observers import NativeActivity
        observed.return_value = NativeActivity(2, 3, 4)
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["native-activity"]), 0)
        content = output.getvalue()
        self.assertIn("Linux login sessions observed: 2", content)
        self.assertIn("Loaded systemd system timers: 3", content)
        self.assertIn("Loaded systemd user timers: 4", content)
        self.assertIn("Coverage: INCOMPLETE", content)
        self.assertIn("No automatic suspension permitted", content)

    @patch("isc.cli.read_native_activity")
    def test_native_activity_unknown_is_not_inferred_zero(self, observed):
        from isc.native_observers import NativeActivity
        observed.return_value = NativeActivity(None, None, None)
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["native-activity"]), 0)
        self.assertIn("UNKNOWN", output.getvalue())
        self.assertIn("Coverage: INCOMPLETE", output.getvalue())


    @patch("isc.cli.check_hook_delivery")
    def test_hook_self_test_uses_synthetic_diagnostic(self, checker):
        from isc.hook_diagnostics import HookTestResult
        checker.return_value = HookTestResult(
            "claude_code", "receiver_counter_incremented", 0, 1,
        )
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["hook-self-test", "--source", "claude_code"]), 0)
        checker.assert_called_once_with("claude_code")
        self.assertIn("Synthetic test events never establish actual workload activity.", output.getvalue())
        self.assertIn("No real agent launched", output.getvalue())

    @patch("isc.cli.check_hook_delivery")
    def test_hook_self_test_refuses_unverified_delivery(self, checker):
        from isc.hook_diagnostics import HookTestResult
        checker.return_value = HookTestResult(
            "claude_code", "delivery_unverified", 2, 2,
        )
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["hook-self-test", "--source", "claude_code"]), 2)
        self.assertIn("delivery_unverified", output.getvalue())


    @patch("isc.cli.time.sleep")
    @patch("isc.cli.read_process_lineage")
    def test_process_watch_reports_identity_and_partial_coverage(
        self, probe, sleep,
    ):
        from isc.process_lifecycle import ProcessIdentity, ProcessLineage
        continuing = ProcessIdentity(50, 10, 1, "codex")
        new = ProcessIdentity(51, 11, 50, "python3")
        probe.side_effect = [
            ProcessLineage((continuing,), (), 0),
            ProcessLineage((continuing,), (new,), 0),
        ]
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["process-watch", "--seconds", "0.25"]), 0)
        sleep.assert_called_once_with(0.25)
        self.assertEqual(probe.call_count, 2)
        text = output.getvalue()
        self.assertIn("Identity continuing: 1; appeared: 1", text)
        self.assertIn("Coverage verified: NO", text)
        self.assertIn("disappearing process does NOT prove", text)
        self.assertNotIn("task completed successfully", text)

    @patch("isc.cli.time.sleep")
    @patch("isc.cli.read_process_lineage", return_value=None)
    def test_process_watch_unknown_does_not_claim_empty(self, probe, sleep):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["process-watch", "--seconds", "0"]), 2)
        self.assertIn("UNKNOWN", output.getvalue())
        self.assertIn("No sleep eligibility established.", output.getvalue())
        sleep.assert_not_called()

    @patch("isc.cli.time.sleep")
    @patch("isc.cli.read_process_lineage")
    def test_process_watch_refuses_excessive_observation_window(
        self, probe, sleep,
    ):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["process-watch", "--seconds", "3600"]), 2)
        probe.assert_not_called()
        sleep.assert_not_called()
        self.assertIn("between 0 and 60", output.getvalue())


    @patch("isc.cli.read_scheduled_work")
    def test_scheduled_work_distinguishes_waiting_timers_from_running_services(
        self, scheduled,
    ):
        from isc.scheduled_work import ScheduledWorkCensus, UnitCounts
        scheduled.return_value = ScheduledWorkCensus(
            system_timers=UnitCounts(19, waiting_timers=17),
            user_timers=UnitCounts(2, waiting_timers=2),
            system_services=UnitCounts(60, running_services=21, activating_services=1),
            user_services=UnitCounts(14, running_services=5),
        )
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["scheduled-work"]), 0)
        text = output.getvalue()
        self.assertIn("System timers: 19 listed; 17 waiting", text)
        self.assertIn("User timers: 2 listed; 2 waiting", text)
        self.assertIn("System services: 60 listed; 21 running, 1 activating", text)
        self.assertIn("not identify the jobs they activate", text)
        self.assertIn("Coverage: INCOMPLETE", text)
        self.assertIn("Automatic suspension remains disabled", text)

    @patch("isc.cli.read_scheduled_work")
    def test_scheduled_work_query_failure_is_unknown(self, scheduled):
        from isc.scheduled_work import ScheduledWorkCensus, UnitCounts
        scheduled.return_value = ScheduledWorkCensus(
            None, None, None, UnitCounts(0),
        )
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["scheduled-work"]), 0)
        self.assertIn("System timers: UNKNOWN", output.getvalue())
        self.assertIn("System services: UNKNOWN", output.getvalue())
        self.assertIn("User services: 0 listed", output.getvalue())


    @patch("isc.cli.inspect_launcher")
    def test_app_read_only_status_without_registration(self, inspect):
        from isc.desktop_launcher import LauncherStatus
        inspect.return_value = LauncherStatus("missing")
        capture = StringIO()
        with contextlib.redirect_stdout(capture):
            self.assertEqual(main(["app"]), 0)
        self.assertIn("Status: MISSING", capture.getvalue())
        self.assertIn("--install --confirm", capture.getvalue())

    @patch("isc.cli.modify_launcher")
    def test_launcher_install_requires_confirmation(self, modify):
        capture = StringIO()
        with contextlib.redirect_stdout(capture):
            self.assertEqual(main(["app", "--install"]), 2)
        modify.assert_not_called()
        self.assertIn("Explicit --confirm", capture.getvalue())

    @patch("isc.cli.modify_launcher")
    def test_explicit_launcher_install_and_remove_only_change_menu(self, modify):
        from isc.desktop_launcher import LauncherStatus
        modify.side_effect = [
            LauncherStatus("installed", changed=True),
            LauncherStatus("missing", changed=True),
        ]
        for command, expected in (
            (["app", "--install", "--confirm"], "installed"),
            (["app", "--uninstall", "--confirm"], "missing"),
        ):
            capture = StringIO()
            with contextlib.redirect_stdout(capture):
                self.assertEqual(main(command), 0)
            self.assertIn("The tray is NOT enabled at login", capture.getvalue())
        self.assertEqual(
            [call.args[0] for call in modify.call_args_list],
            ["install", "remove"],
        )

    @patch("isc.cli.run_window", return_value=0)
    def test_window_command_dispatches_to_actual_desktop_ui(self, launch):
        self.assertEqual(main(["window"]), 0)
        launch.assert_called_once_with()



if __name__ == "__main__":
    unittest.main()

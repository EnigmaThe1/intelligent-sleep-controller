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
        self.assertIn("Hibernate: future", output.getvalue())
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


if __name__ == "__main__":
    unittest.main()

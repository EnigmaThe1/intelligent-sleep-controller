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
                    }]}
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


if __name__ == "__main__":
    unittest.main()

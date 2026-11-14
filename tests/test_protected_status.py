"""Detached user-unit inhibitor verification: all OS interactions are mocked."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import subprocess
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.protected_status import (
    ProtectedStatus, _parse_main_pid, _owned_inhibitor_present,
    _capture, inspect_protected_unit,
)
from isc.cli import main

UNIT = "isc-protected-0123456789abcdef.service"
PID = 526293
UID = 1000


def service(pid=PID, *, unit=UNIT, state="active", sub="running"):
    return (
        f"Id={unit}\n"
        f"MainPID={pid}\n"
        f"ActiveState={state}\n"
        f"SubState={sub}\n"
    )


def inhibitors(*, pid=PID, uid=UID, what="idle:sleep", mode="block",
               who="Intelligent Sleep Controller",
               why="Protected foreground work"):
    return (
        f'a(ssssuu) 1 "{what}" "{who}" "{why}" "{mode}" {uid} {pid}\n'
    )


class ProtectedStatusTests(unittest.TestCase):
    def test_verified_only_with_matching_live_pid_uid_and_inhibitor(self):
        with patch("isc.protected_status.os.getuid", return_value=UID), \
             patch("isc.protected_status._capture",
                   side_effect=[service(), inhibitors(), service()]) as capture:
            result = inspect_protected_unit(UNIT)
        self.assertEqual(
            result, ProtectedStatus(
                True, "owned_sleep_idle_inhibitor_verified_now",
            ),
        )
        self.assertEqual(capture.call_count, 3)
        service_call, lock_call, service_again = [
            call.args[0] for call in capture.call_args_list
        ]
        self.assertEqual(service_call, service_again)
        self.assertEqual(service_call[:5], (
            "systemctl", "--user", "--no-pager", "show", UNIT,
        ))
        self.assertEqual(lock_call[:2], ("busctl", "--system"))

    def test_unsafe_unit_name_does_not_call_any_system_commands(self):
        for unit in ("other.service", "../danger.service", UNIT + "/foo",
                     UNIT.replace("abc", "ABC"), ""):
            with self.subTest(unit=unit):
                with patch("isc.protected_status._capture") as capture:
                    row = inspect_protected_unit(unit)
                self.assertEqual(row.reason, "invalid_detached_unit")
                capture.assert_not_called()

    def test_service_missing_finished_or_inactive_is_unverified(self):
        for raw in (None, "", service(pid=0), service(state="inactive"),
                    service(sub="dead"), service(sub="exited")):
            with self.subTest(raw=raw):
                with patch("isc.protected_status._capture",
                           return_value=raw) as capture:
                    row = inspect_protected_unit(UNIT)
                self.assertEqual(row.reason, "running_unit_not_verified")
                capture.assert_called_once()

    def test_other_process_inhibitor_does_not_authorize(self):
        for raw in (
            inhibitors(pid=PID+1), inhibitors(uid=UID+1),
            inhibitors(what="sleep"), inhibitors(what="idle"),
            inhibitors(mode="delay"), inhibitors(mode="block-weak"),
            inhibitors(who="Another Application"),
            inhibitors(why="Unrelated request"),
            'a(ssssuu) 0',
            None,
            'malformed proprietary info',
        ):
            with self.subTest(raw=raw):
                with patch("isc.protected_status.os.getuid", return_value=UID), \
                     patch("isc.protected_status._capture",
                           side_effect=[service(), raw]):
                    row = inspect_protected_unit(UNIT)
                self.assertEqual(row.reason, "native_inhibitor_not_verified")

    def test_race_changed_pid_or_inactive_service_is_unknown(self):
        for changed in (service(pid=PID+1), service(state="inactive"), None):
            with self.subTest(changed=changed):
                with patch("isc.protected_status.os.getuid", return_value=UID), \
                     patch("isc.protected_status._capture",
                           side_effect=[service(), inhibitors(), changed]):
                    row = inspect_protected_unit(UNIT)
                self.assertEqual(row.reason, "unit_changed_during_check")

    def test_malformed_unit_reply_is_unverified(self):
        invalid = (
            service().replace("MainPID=", "MainPID=\nMainPID="),
            service().replace("MainPID=526293", "MainPID=notapipid"),
            service().replace(f"Id={UNIT}", "Id=other.service"),
            service() + "Description=sensitive private project\n",
            service().replace("MainPID=526293", "MainPID=-1"),
            service().replace("MainPID=526293", "MainPID=4294967296"),
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                self.assertIsNone(_parse_main_pid(raw, UNIT))

    def test_logind_malformed_or_duplicate_metadata_never_verified(self):
        malformed = (
            'a(ssssuu) 1 "idle:sleep" "ISC" "private" "block" bad 123',
            'a(ssssuu) 2 "idle:sleep" "ISC" "private" "block" 1000 123',
            'a(ssssuu) 1 "idle:sleep" "ISC" "private" "block" 1000 123',
            "x" * 200001,
        )
        for raw in malformed:
            with self.subTest(raw=raw):
                self.assertFalse(
                    _owned_inhibitor_present(raw, pid=PID, uid=UID)
                )

    def test_valid_unrelated_lock_does_not_obscure_matching_entry(self):
        raw = (
            'a(ssssuu) 2 "shutdown" "Other" "Other" "block" 1000 8 '
            '"idle:sleep" "Intelligent Sleep Controller" '
            '"Protected foreground work" "block" 1000 526293'
        )
        self.assertTrue(_owned_inhibitor_present(raw, pid=PID, uid=UID))

    def test_capture_fails_closed_for_native_command_failure(self):
        with patch("isc.protected_status.subprocess.run",
                   return_value=Mock(returncode=1, stdout="private")):
            self.assertIsNone(_capture(("systemctl", "show")))

    def test_capture_timeout_does_not_expose_private_context(self):
        with patch("isc.protected_status.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("sys", 3,
                                                         output="private")):
            self.assertIsNone(_capture(("systemctl", "show")))

    def test_cli_success_reports_momentary_evidence_only(self):
        with patch("isc.cli.inspect_protected_unit", return_value=
                   ProtectedStatus(
                       True, "owned_sleep_idle_inhibitor_verified_now",
                   )) as inspect, redirect_stdout(StringIO()) as out:
            self.assertEqual(main(["protected-status", UNIT]), 0)
        result = out.getvalue()
        self.assertIn("VERIFIED NOW", result)
        self.assertIn("momentary observation", result)
        self.assertIn("No services or power settings changed", result)
        inspect.assert_called_once_with(UNIT)

    def test_cli_unverified_result_not_conflated_with_absence_of_work(self):
        with patch("isc.cli.inspect_protected_unit", return_value=
                   ProtectedStatus(
                       False, "native_inhibitor_not_verified",
                   )) as inspect, redirect_stdout(StringIO()) as out:
            self.assertEqual(main(["protected-status", UNIT]), 2)
        self.assertIn("NOT VERIFIED", out.getvalue())
        self.assertNotIn("safe to suspend", out.getvalue())
        inspect.assert_called_once_with(UNIT)

    def test_cli_missing_or_extra_arguments_do_not_probe(self):
        with patch("isc.cli.inspect_protected_unit") as inspect:
            for argv in (
                ["protected-status"],
                ["protected-status", UNIT, "private-extra"],
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(argv), 2)
        inspect.assert_not_called()

    def test_cli_status_does_not_modify_services_settings_or_power(self):
        with patch("isc.cli.inspect_protected_unit",
                   return_value=ProtectedStatus(True, "verified")), \
             patch("isc.cli.subprocess.run",
                   side_effect=AssertionError("CLI must not launch")), \
             patch("isc.settings.set_mode",
                   side_effect=AssertionError("no mode writes")), \
             patch("isc.cli.change_gnome_policy",
                   side_effect=AssertionError("no GNOME writes")), \
             redirect_stdout(StringIO()):
            self.assertEqual(main(["protected-status", UNIT]), 0)


if __name__ == "__main__":
    unittest.main()

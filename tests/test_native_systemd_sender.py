"""Step 99f user-bus owner correlation is non-authoritative."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.native_systemd_sender import (
    read_user_manager_unique_owner, validate_manager_sender,
)


def done(stdout='s ":1.23"\n', code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")


class NativeSystemdSenderTests(unittest.TestCase):
    def test_same_owner_bracket_correlates_but_not_authoritative(self):
        result = validate_manager_sender(":1.23", ":1.23", ":1.23")
        self.assertEqual(result.status, "SENDER_MATCH_UNVERIFIED")
        self.assertTrue(result.manager_sender_correlated)
        self.assertFalse(result.event_delivery_verified)
        self.assertFalse(result.task_owner_authenticated)
        self.assertFalse(result.coverage_verified)

    def test_owner_changed_unknown_for_completion(self):
        row = validate_manager_sender(":1.23", ":1.23", ":1.24")
        self.assertEqual(row.status, "OWNER_CHANGED_UNVERIFIED")
        self.assertFalse(row.manager_sender_correlated)

    def test_sender_mismatch_rejected(self):
        row = validate_manager_sender(":1.88", ":1.23", ":1.23")
        self.assertEqual(row.status, "SENDER_MISMATCH")
        self.assertFalse(row.manager_sender_correlated)

    def test_invalid_identifier_fail_closed(self):
        for candidate in (None, 1, "", ":0", "org.freedesktop.systemd1",
                          ":1.1\nPRIVATE", ":1.-1", ":1.1/secret"):
            self.assertEqual(validate_manager_sender(
                candidate, ":1.23", ":1.23").status, "UNKNOWN")

    def test_invalid_before_after_fail_closed(self):
        for before, after in ((None, ":1.23"), (":1.23", None),
                              (":1.23", ":1.2 private"), (":1.0", 5)):
            self.assertEqual(validate_manager_sender(
                ":1.23", before, after).status, "UNKNOWN")

    def test_owner_read_strict_parse(self):
        self.assertEqual(read_user_manager_unique_owner(
            runner=lambda *a, **k: done()), ":1.23")

    def test_bad_output_rejected(self):
        for raw in ("", 's "not.a.unique.name"', "s :1.23",
                    's ":1.23" extra', 's ":1.23"\nSECRET',
                    's ":1.23" ' * 10):
            with self.subTest(raw=raw[:18]):
                self.assertIsNone(read_user_manager_unique_owner(
                    runner=lambda *a, **k: done(raw)))

    def test_query_is_bounded_read_only(self):
        runner = Mock(return_value=done())
        read_user_manager_unique_owner(runner=runner)
        args, kwargs = runner.call_args
        self.assertEqual(args[0], (
            "busctl", "--user", "call", "org.freedesktop.DBus",
            "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "GetNameOwner", "s", "org.freedesktop.systemd1",
        ))
        self.assertEqual(kwargs["timeout"], 4)
        self.assertFalse(kwargs["check"])
        self.assertTrue(kwargs["capture_output"])

    def test_errors_return_none_without_error_disclosure(self):
        for error in (OSError("private"), ValueError("private"),
                      TypeError("private"), UnicodeError("private"),
                      subprocess.TimeoutExpired("busctl", 4)):
            self.assertIsNone(read_user_manager_unique_owner(
                runner=Mock(side_effect=error)))

    def test_nonzero_and_unexpected_result_rejected(self):
        self.assertIsNone(read_user_manager_unique_owner(
            runner=Mock(return_value=done(code=2))))
        self.assertIsNone(read_user_manager_unique_owner(
            runner=Mock(return_value=None)))

    def test_no_secret_environment(self):
        with patch.dict("os.environ", {
            "PATH": "/usr/bin", "SECRET": "do not forward",
        }, clear=True):
            runner = Mock(return_value=done())
            read_user_manager_unique_owner(runner=runner)
            self.assertNotIn("SECRET", runner.call_args.kwargs["env"])

    def test_never_exposes_identifiers_in_correlation_result(self):
        row = validate_manager_sender(":1.123", ":1.123", ":1.123")
        self.assertNotIn(":1.123", repr(row))


if __name__ == "__main__":
    unittest.main()

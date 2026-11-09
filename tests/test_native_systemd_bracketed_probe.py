"""Step 99g bracketed owner correlation: fail-closed regression tests."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.native_systemd_live_probe import (
    _HEADER, LiveSignalProbe, probe_live_user_signal,
)
from isc.native_systemd_bracketed_probe import inspect_bracketed_user_manager_signal

FRAME = (
    "\u2023 Type=signal  Endian=l  Flags=1  Version=1 Cookie=2\n"
    "  Sender=:1.12  Path=/org/freedesktop/systemd1  "
    "Interface=org.freedesktop.systemd1.Manager  Member=JobNew\n"
).encode("utf-8")


def observed(sender=":1.12"):
    def watcher(*, seconds, _sender_callback):
        _sender_callback(sender)
        return LiveSignalProbe("CANDIDATE_SIGNAL_UNVERIFIED", 1)
    return watcher


def owners(*values):
    return Mock(side_effect=values)


class BracketedSignalTests(unittest.TestCase):
    def test_private_header_sender_extracted_from_valid_frame(self):
        match = _HEADER.search(FRAME)
        self.assertIsNotNone(match)
        self.assertEqual(match.group("sender"), b":1.12")

    def test_matching_bracket_is_still_not_suspension_authority(self):
        result = inspect_bracketed_user_manager_signal(
            owner_reader=owners(":1.12", ":1.12"), watcher=observed())
        self.assertEqual(result.status, "SENDER_MATCH_UNVERIFIED")
        self.assertEqual(result.observed_candidates, 1)
        self.assertTrue(result.manager_sender_correlated)
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.task_owner_authenticated)
        self.assertFalse(result.event_delivery_verified)

    def test_owner_rotation_fails_closed(self):
        result = inspect_bracketed_user_manager_signal(
            owner_reader=owners(":1.12", ":1.99"), watcher=observed())
        self.assertEqual(result.status, "OWNER_CHANGED_UNVERIFIED")
        self.assertFalse(result.manager_sender_correlated)

    def test_sender_mismatch_fails_closed(self):
        result = inspect_bracketed_user_manager_signal(
            owner_reader=owners(":1.12", ":1.12"),
            watcher=observed(":1.33"))
        self.assertEqual(result.status, "SENDER_MISMATCH")
        self.assertIsNone(result.observed_candidates)

    def test_no_owner_does_not_start_watcher(self):
        watcher = Mock()
        result = inspect_bracketed_user_manager_signal(
            owner_reader=lambda: None, watcher=watcher)
        self.assertEqual(result.status, "UNKNOWN")
        watcher.assert_not_called()

    def test_owner_failure_after_event_is_unknown(self):
        result = inspect_bracketed_user_manager_signal(
            owner_reader=owners(":1.12", None), watcher=observed())
        self.assertEqual(result.status, "UNKNOWN")

    def test_quiet_is_not_zero_work(self):
        result = inspect_bracketed_user_manager_signal(
            owner_reader=owners(":1.12", ":1.12"),
            watcher=lambda **kw: LiveSignalProbe("QUIET_UNVERIFIED"))
        self.assertEqual(result.status, "QUIET_UNVERIFIED")
        self.assertIsNone(result.observed_candidates)
        self.assertFalse(result.coverage_verified)

    def test_malformed_quiet_with_sender_is_unknown(self):
        def watcher(**kw):
            kw["_sender_callback"](":1.12")
            return LiveSignalProbe("QUIET_UNVERIFIED")
        result = inspect_bracketed_user_manager_signal(
            owner_reader=owners(":1.12", ":1.12"), watcher=watcher)
        self.assertEqual(result.status, "UNKNOWN")

    def test_event_without_sender_callback_is_unknown(self):
        result = inspect_bracketed_user_manager_signal(
            owner_reader=owners(":1.12", ":1.12"),
            watcher=lambda **kw: LiveSignalProbe("CANDIDATE_SIGNAL_UNVERIFIED", 1))
        self.assertEqual(result.status, "UNKNOWN")

    def test_duplicate_callback_fails_closed(self):
        def watcher(**kw):
            kw["_sender_callback"](":1.12")
            kw["_sender_callback"](":1.12")
        result = inspect_bracketed_user_manager_signal(
            owner_reader=owners(":1.12", ":1.12"), watcher=watcher)
        self.assertEqual(result.status, "UNKNOWN")

    def test_forged_authority_flag_rejected(self):
        result = inspect_bracketed_user_manager_signal(
            owner_reader=owners(":1.12", ":1.12"),
            watcher=lambda **kw: LiveSignalProbe(
                "CANDIDATE_SIGNAL_UNVERIFIED", 1, coverage_verified=True))
        self.assertEqual(result.status, "UNKNOWN")

    def test_unexpected_event_count_rejected(self):
        def watcher(**kw):
            kw["_sender_callback"](":1.12")
            return LiveSignalProbe("CANDIDATE_SIGNAL_UNVERIFIED", 2)
        result = inspect_bracketed_user_manager_signal(
            owner_reader=owners(":1.12", ":1.12"), watcher=watcher)
        self.assertEqual(result.status, "UNKNOWN")

    def test_invalid_arguments_do_not_call_external_tools(self):
        for seconds in (0, 11, True, None, "3", -1):
            owner = Mock()
            watcher = Mock()
            row = inspect_bracketed_user_manager_signal(
                seconds=seconds, owner_reader=owner, watcher=watcher)
            self.assertEqual(row.status, "UNKNOWN")
            owner.assert_not_called()
            watcher.assert_not_called()

    def test_owner_or_watcher_exception_is_unknown(self):
        for failing in ("owner", "watcher"):
            with self.subTest(failing=failing):
                owner = Mock(side_effect=OSError("secret")) if failing == "owner" else owners(":1.12")
                watch = Mock(side_effect=RuntimeError("secret")) if failing == "watcher" else observed()
                row = inspect_bracketed_user_manager_signal(
                    owner_reader=owner, watcher=watch)
                self.assertEqual(row.status, "UNKNOWN")
                self.assertNotIn("secret", repr(row))

    def test_unique_sender_never_in_public_result(self):
        row = inspect_bracketed_user_manager_signal(
            owner_reader=owners(":1.888", ":1.888"),
            watcher=observed(":1.888"))
        self.assertNotIn(":1.888", repr(row))

    def test_live_api_refuses_non_callable_callback_before_spawning(self):
        with patch("isc.native_systemd_live_probe.subprocess.Popen") as spawn:
            result = probe_live_user_signal(_sender_callback="untrusted")
            self.assertEqual(result.state, "UNKNOWN")
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()

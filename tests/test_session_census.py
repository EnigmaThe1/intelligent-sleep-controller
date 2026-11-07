"""Read-only bounded logind session census tests."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.session_census import _parse_active, _session_ids, read_session_census
from isc.cli import sessions_cli


class SessionCensusTests(unittest.TestCase):
    def test_parses_ids_without_retaining_private_usernames(self):
        raw = "2 1000 private-user seat0 tty2\nc1 1001 another-private - pts/3\n"
        self.assertEqual(_session_ids(raw), ("2", "c1"))
        self.assertIsNone(_session_ids(None))
        self.assertIsNone(_session_ids("broken"))
        self.assertIsNone(_session_ids("2 1000 user\n2 1000 user\n"))
        self.assertIsNone(_session_ids("2 -1 user\n"))
        self.assertIsNone(_session_ids("2 1000 user\n" * 65))

    def test_active_flags_fail_closed(self):
        self.assertIs(_parse_active("yes\n"), True)
        self.assertIs(_parse_active("no\n"), False)
        for raw in (None, "", "True", "unknown", "yes\nno"):
            self.assertIsNone(_parse_active(raw))

    def test_queries_are_only_read_only_native_logind(self):
        with patch("isc.session_census._capture", side_effect=[
            "2 1000 private-user seat0 tty2\nc1 1001 other - pts/3\n",
            "yes\n",
            "no\n",
        ]) as capture:
            census = read_session_census()
        self.assertEqual(
            (census.listed, census.active, census.inactive, census.unknown),
            (2, 1, 1, 0),
        )
        self.assertFalse(census.coverage_verified)
        self.assertNotIn("private-user", repr(census))
        self.assertEqual(capture.call_count, 3)
        self.assertEqual(capture.call_args_list[1].args[0],
                         ("loginctl", "show-session", "2",
                          "--property=Active", "--value"))
        self.assertTrue(all(
            cmd.args[0][0] == "loginctl"
            and (("list-sessions" in cmd.args[0]) or
                 ("show-session" in cmd.args[0]))
            for cmd in capture.call_args_list
        ))

    def test_partial_failure_is_unknown_not_inactive(self):
        with patch("isc.session_census._capture", side_effect=[
            "2 1000 user seat0 tty2\nc1 1001 user2 - pts/3\n",
            None,
            "yes\n",
        ]):
            census = read_session_census()
        self.assertEqual((census.active, census.inactive, census.unknown),
                         (1, 0, 1))
        self.assertFalse(census.coverage_verified)

    def test_failed_listing_is_unknown_and_empty_is_not_coverage(self):
        with patch("isc.session_census._capture", return_value=None):
            self.assertIsNone(read_session_census())
        with patch("isc.session_census._capture", return_value=""):
            census = read_session_census()
        self.assertEqual(census.listed, 0)
        self.assertFalse(census.coverage_verified)

    @patch("isc.cli.read_session_census")
    def test_cli_handles_missing_census(self, census):
        census.return_value = None
        self.assertEqual(sessions_cli(), 2)
        census.assert_called_once()


if __name__ == "__main__":
    unittest.main()

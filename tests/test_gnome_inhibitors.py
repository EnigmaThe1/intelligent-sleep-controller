"""Privacy-minimal GNOME session inhibitor observation tests."""
import subprocess
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.gnome_inhibitors import (
    parse_inhibited, read_gnome_inhibit_flags, _check,
)
from isc.cli import gnome_inhibitors_cli


class GnomeInhibitorTests(unittest.TestCase):
    def test_boolean_reply_shapes_and_malformed_unknown(self):
        self.assertIs(parse_inhibited("(true,)"), True)
        self.assertIs(parse_inhibited("(false,)"), False)
        for raw in (None, "", "(1,)", "(True,)", "true", "(false,) trailing"):
            self.assertIsNone(parse_inhibited(raw))

    @patch("isc.gnome_inhibitors.subprocess.run")
    def test_read_only_gdbus_calls_separate_suspend_and_idle(self, call):
        call.side_effect = [
            SimpleNamespace(returncode=0, stdout="(true,)\n"),
            SimpleNamespace(returncode=0, stdout="(false,)\n"),
        ]
        result = read_gnome_inhibit_flags()
        self.assertTrue(result.suspend_inhibited)
        self.assertFalse(result.idle_inhibited)
        self.assertFalse(result.coverage_verified)
        self.assertEqual(call.call_count, 2)
        flags = [c.args[0][-1] for c in call.call_args_list]
        self.assertEqual(flags, ["4", "8"])
        for c in call.call_args_list:
            self.assertEqual(c.args[0][0:2], ("gdbus", "call"))
            self.assertIn("IsInhibited", c.args[0])
            self.assertEqual(c.kwargs["timeout"], 3.0)
            self.assertNotIn("shell", c.kwargs)

    @patch("isc.gnome_inhibitors.subprocess.run")
    def test_partial_failure_is_unknown_not_false(self, call):
        call.side_effect = [
            SimpleNamespace(returncode=1, stdout="(false,)"),
            SimpleNamespace(returncode=0, stdout="(false,)"),
        ]
        result = read_gnome_inhibit_flags()
        self.assertIsNone(result.suspend_inhibited)
        self.assertFalse(result.idle_inhibited)

    @patch("isc.gnome_inhibitors.subprocess.run",
           side_effect=subprocess.TimeoutExpired(["gdbus"], 3))
    def test_bus_timeout_is_unknown(self, call):
        result = read_gnome_inhibit_flags()
        self.assertIsNone(result.suspend_inhibited)
        self.assertIsNone(result.idle_inhibited)

    def test_reject_unsupported_flag_and_timeout(self):
        with self.assertRaises(ValueError):
            _check(1, 3)
        for seconds in (-1, 0, 11):
            with self.assertRaises(ValueError):
                read_gnome_inhibit_flags(timeout_seconds=seconds)

    @patch("isc.cli.read_gnome_inhibit_flags")
    def test_cli_unknown_returns_nonzero(self, reader):
        from isc.gnome_inhibitors import GnomeInhibitFlags
        reader.return_value = GnomeInhibitFlags(None, False)
        self.assertEqual(gnome_inhibitors_cli(), 2)
        reader.return_value = GnomeInhibitFlags(False, False)
        self.assertEqual(gnome_inhibitors_cli(), 0)


if __name__ == "__main__":
    unittest.main()

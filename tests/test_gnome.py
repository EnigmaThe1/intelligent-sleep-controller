import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.gnome import parse_gnome_idletime, read_gnome_idle_ms


class ParseGNOMEIdleTests(unittest.TestCase):
    def test_actual_gnome_response(self):
        self.assertEqual(parse_gnome_idletime("(uint64 13,)"), 13)

    def test_whitespace_and_no_optional_comma(self):
        self.assertEqual(parse_gnome_idletime(" ( uint64 1500 )\n"), 1500)

    def test_zero_is_valid_recent_activity(self):
        self.assertEqual(parse_gnome_idletime("(uint64 0,)"), 0)

    def test_unknown_outputs_fail_closed(self):
        for raw in (
            "", "13", "(uint32 13,)", "(uint64 -5,)", "(uint64 junk,)",
            "(uint64 13,) trailing", "Error: disconnected",
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_gnome_idletime(raw))

    def test_overflow_fails_closed(self):
        self.assertIsNone(parse_gnome_idletime(f"(uint64 {2**64},)"))


class ReadGNOMEIdleTests(unittest.TestCase):
    @patch("isc.gnome.subprocess.run")
    def test_reads_session_bus_without_shell(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout="(uint64 3456,)")
        self.assertEqual(read_gnome_idle_ms(), 3456)
        args, kwargs = run.call_args
        self.assertEqual(args[0][0], "gdbus")
        self.assertIn("GetIdletime", args[0])
        self.assertEqual(kwargs["timeout"], 3.0)
        self.assertFalse(kwargs["check"])
        self.assertNotIn("shell", kwargs)

    @patch("isc.gnome.subprocess.run")
    def test_bus_error_is_unknown(self, run):
        run.return_value = SimpleNamespace(returncode=1, stdout="(uint64 3,)")
        self.assertIsNone(read_gnome_idle_ms())

    @patch("isc.gnome.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_gdbus_is_unknown(self, run):
        self.assertIsNone(read_gnome_idle_ms())

    @patch("isc.gnome.subprocess.run", side_effect=subprocess.TimeoutExpired(["gdbus"], 3))
    def test_timeout_is_unknown(self, run):
        self.assertIsNone(read_gnome_idle_ms())


if __name__ == "__main__":
    unittest.main()

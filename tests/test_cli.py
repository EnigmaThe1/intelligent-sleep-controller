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


if __name__ == "__main__":
    unittest.main()

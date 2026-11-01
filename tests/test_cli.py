import contextlib
from io import StringIO
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.cli import main


class CLITests(unittest.TestCase):
    def test_status_is_read_only_and_explicit(self):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["status"]), 0)
        text = output.getvalue()
        self.assertIn("automatic suspend: NOT IMPLEMENTED", text)
        self.assertIn("No system configuration has been changed.", text)


if __name__ == "__main__":
    unittest.main()

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.settings import ModeConfigError, ModeStatus
from isc.tray import TraySnapshot, backend_probe, tray_snapshot, run_tray


class TrayTests(unittest.TestCase):
    @patch("isc.tray.discover_monitor_socket", return_value=None)
    @patch("isc.tray.read_mode", return_value=ModeStatus("off", False))
    def test_default_off(self, mode, socket):
        state = tray_snapshot()
        self.assertEqual(state.mode, "off")
        self.assertFalse(state.configured)
        self.assertFalse(state.receiver_advertised)

    @patch("isc.tray.discover_monitor_socket", return_value=Path("/private/socket"))
    @patch("isc.tray.read_mode", return_value=ModeStatus("monitor", True))
    def test_preference_and_socket_separate(self, mode, socket):
        state = tray_snapshot()
        self.assertEqual(state.mode, "monitor")
        self.assertTrue(state.receiver_advertised)
        self.assertTrue(state.valid)

    @patch("isc.tray.discover_monitor_socket", return_value=None)
    @patch("isc.tray.read_mode", side_effect=ModeConfigError("private"))
    def test_unsafe_config_fails_closed(self, mode, socket):
        self.assertEqual(tray_snapshot(), TraySnapshot("off", False, False, False))

    @patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""})
    def test_missing_desktop_is_reported(self):
        issues = backend_probe()
        self.assertIn("graphical_session_unavailable", issues)

    @patch("isc.tray.backend_probe", return_value=("graphical_session_unavailable",))
    @patch("isc.tray._gtk_dependencies", side_effect=AssertionError("GUI started"))
    def test_unavailable_tray_does_not_initialize_gui(self, dep, probe):
        self.assertEqual(run_tray(), 2)
        dep.assert_not_called()


if __name__ == "__main__":
    unittest.main()

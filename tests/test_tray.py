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

    @patch("isc.tray.discover_monitor_socket", return_value=None)
    @patch("isc.tray.read_mode", return_value=ModeStatus("on", True))
    def test_unsupported_on_is_not_displayed_as_active(self, mode, socket):
        state = tray_snapshot()
        self.assertEqual(state.mode, "off")
        self.assertFalse(state.valid)
        self.assertFalse(state.receiver_advertised)

    @patch("isc.tray.discover_monitor_socket", return_value=None)
    @patch("isc.tray.read_mode", return_value=ModeStatus("unknown", True))
    def test_unknown_preference_fails_closed(self, mode, socket):
        state = tray_snapshot()
        self.assertEqual(state.mode, "off")
        self.assertFalse(state.valid)

    @patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""})
    def test_missing_desktop_is_reported(self):
        issues = backend_probe()
        self.assertIn("graphical_session_unavailable", issues)

    @patch("isc.tray.backend_probe", return_value=("graphical_session_unavailable",))
    @patch("isc.tray._gtk_dependencies", side_effect=AssertionError("GUI started"))
    def test_unavailable_tray_does_not_initialize_gui(self, dep, probe):
        self.assertEqual(run_tray(), 2)
        dep.assert_not_called()


class _ProfileItemFake:
    def __init__(self, label):
        self._label = label
        self._visible = True
        self._sensitive = True
        self.label_changes = 0
        self.visibility_changes = 0

    def get_label(self):
        return self._label

    def set_label(self, label):
        self._label = label
        self.label_changes += 1

    def get_visible(self):
        return self._visible

    def set_visible(self, enabled):
        self._visible = enabled
        self.visibility_changes += 1

    def set_sensitive(self, value):
        self._sensitive = value


class ProfileSubmenuRegressionTests(unittest.TestCase):
    def _controller(self):
        from isc.tray import TrayController
        controller = object.__new__(TrayController)
        controller.profiles_item = _ProfileItemFake("Power Profile")
        controller._profile_items = {
            name: _ProfileItemFake(name)
            for name in ("power-saver", "balanced", "performance")
        }
        controller._profile_unavailable = _ProfileItemFake("unavailable")
        return controller

    @patch("isc.tray.power_profiles")
    def test_refresh_updates_existing_items_without_recreating_them(self, read):
        from isc.native_controls import PowerProfiles
        c = self._controller()
        original_ids = tuple(id(x) for x in c._profile_items.values())
        read.return_value = PowerProfiles(
            "performance", ("power-saver", "balanced", "performance"),
        )
        c._refresh_profiles()
        self.assertEqual(
            c.profiles_item.get_label(), "Power Profile: performance",
        )
        self.assertEqual(
            c._profile_items["performance"].get_label(), "● Performance",
        )
        self.assertFalse(c._profile_unavailable.get_visible())

        # Re-querying while the submenu is open must not change the widgets
        # or even trigger unnecessary labels/visibility updates.
        labels_before = {
            k: v.label_changes for k, v in c._profile_items.items()
        }
        visibility_before = {
            k: v.visibility_changes for k, v in c._profile_items.items()
        }
        c._refresh_profiles()
        self.assertEqual(tuple(id(x) for x in c._profile_items.values()), original_ids)
        self.assertEqual(
            {k: v.label_changes for k, v in c._profile_items.items()},
            labels_before,
        )
        self.assertEqual(
            {k: v.visibility_changes for k, v in c._profile_items.items()},
            visibility_before,
        )

    @patch("isc.tray.power_profiles")
    def test_host_profile_changes_do_not_rebuild_submenu(self, read):
        from isc.native_controls import PowerProfiles
        c = self._controller()
        ids = tuple(id(x) for x in c._profile_items.values())
        read.side_effect = [
            PowerProfiles("performance", ("power-saver", "balanced", "performance")),
            PowerProfiles("balanced", ("power-saver", "balanced")),
        ]
        c._refresh_profiles()
        c._refresh_profiles(force=True)
        self.assertEqual(ids, tuple(id(x) for x in c._profile_items.values()))
        self.assertFalse(c._profile_items["performance"].get_visible())
        self.assertEqual(c._profile_items["balanced"].get_label(), "● Balanced")
        self.assertTrue(c._profile_items["balanced"].get_visible())

    @patch("isc.tray.power_profiles")
    def test_unavailable_daemon_reports_status_without_rebuilding(self, read):
        from isc.native_controls import NativeControlError
        c = self._controller()
        read.side_effect = NativeControlError("not_found")
        c._refresh_profiles()
        self.assertEqual(c.profiles_item.get_label(), "Power Profile: unavailable")
        self.assertTrue(c._profile_unavailable.get_visible())
        self.assertTrue(all(not x.get_visible() for x in c._profile_items.values()))



    @patch("isc.tray.power_profiles")
    def test_profile_queries_throttled_during_regular_refresh(self, read):
        from isc.native_controls import PowerProfiles
        c = self._controller()
        read.return_value = PowerProfiles(
            "balanced", ("power-saver", "balanced", "performance"),
        )
        with patch("isc.tray.time.monotonic", return_value=100.0):
            c._refresh_profiles()
            c._refresh_profiles()
        read.assert_called_once_with()
        with patch("isc.tray.time.monotonic", return_value=116.0):
            c._refresh_profiles()
        self.assertEqual(read.call_count, 2)



if __name__ == "__main__":
    unittest.main()

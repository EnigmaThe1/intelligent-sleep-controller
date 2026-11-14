"""Milestone 1: shared mode gates always disarm automatic suspend."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.mode_gate import EffectiveMode, current_mode, resolve_mode
from isc.settings import ModeConfigError, ModeStatus


class ModeGateTests(unittest.TestCase):
    def test_off_never_starts_monitor_or_auto_suspend(self):
        row = resolve_mode(ModeStatus("off", True))
        self.assertEqual(row.mode, "off")
        self.assertFalse(row.monitor_allowed)
        self.assertFalse(row.automatic_suspend_allowed)
        self.assertTrue(row.configuration_valid)

    def test_default_off_is_valid_and_disarmed(self):
        row = resolve_mode(ModeStatus("off", False))
        self.assertFalse(row.monitor_allowed)
        self.assertFalse(row.automatic_suspend_allowed)

    def test_monitor_enables_only_observation(self):
        row = resolve_mode(ModeStatus("monitor", True))
        self.assertTrue(row.monitor_allowed)
        self.assertFalse(row.automatic_suspend_allowed)

    def test_on_cannot_be_activated_even_if_injected(self):
        row = resolve_mode(ModeStatus("on", True))
        self.assertEqual(row.mode, "off")
        self.assertFalse(row.configuration_valid)
        self.assertFalse(row.automatic_suspend_allowed)

    def test_invalid_modes_fail_closed(self):
        for value in ("ON", "monitor ", "", "invalid", None, 8, False):
            row = resolve_mode(ModeStatus(value, True))
            self.assertFalse(row.monitor_allowed)
            self.assertFalse(row.automatic_suspend_allowed)

    def test_invalid_object_fails_closed(self):
        for value in (None, "monitor", {}, object()):
            self.assertEqual(resolve_mode(value).mode, "off")

    def test_read_error_fails_closed(self):
        with patch("isc.mode_gate.read_mode", side_effect=ModeConfigError("secret")):
            row = current_mode()
            self.assertEqual(row.reason, "invalid_configuration")
            self.assertNotIn("secret", repr(row))
            self.assertFalse(row.monitor_allowed)

    def test_reader_uses_persistent_mode(self):
        with patch("isc.mode_gate.read_mode", return_value=ModeStatus("monitor", True)):
            self.assertTrue(current_mode().monitor_allowed)

    def test_no_power_command_on_mode_checks(self):
        with patch("subprocess.run", side_effect=AssertionError("power")):
            resolve_mode(ModeStatus("monitor", True))
            resolve_mode(ModeStatus("off", True))
            resolve_mode(ModeStatus("on", True))


if __name__ == "__main__":
    unittest.main()

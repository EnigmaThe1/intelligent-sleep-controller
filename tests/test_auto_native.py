"""ISC 1.0 automatic native preflight: read only, fail closed."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.auto_native import AutoNativeAssessment, assess_auto_native_suspend
from isc.inhibitors import Inhibitor


class AutoNativeTests(unittest.TestCase):
    def test_valid_logind_yes_and_no_inhibitors_is_native_ready(self):
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors", return_value=()):
            result = assess_auto_native_suspend()
        self.assertEqual(result, AutoNativeAssessment(True, "yes", ()))
        # This is native readiness, NEVER application or scheduler coverage.
        self.assertFalse(hasattr(result, "workload_coverage_verified"))

    def test_challenge_is_not_acceptable_unattended(self):
        for capability in ("challenge", "no", "na", "unknown", None):
            with self.subTest(capability=capability):
                with patch("isc.auto_native.can_suspend", return_value=capability), \
                     patch("isc.auto_native.read_inhibitors", return_value=()):
                    result = assess_auto_native_suspend()
                self.assertFalse(result.ready)
                self.assertIn("native_suspend_capability_unverified_or_unavailable",
                              result.blockers)

    def test_block_inhibitor_prevents_native_ready(self):
        for mode in ("block", "block-weak"):
            lock = Inhibitor(frozenset(("sleep",)), mode)
            with patch("isc.auto_native.can_suspend", return_value="yes"), \
                 patch("isc.auto_native.read_inhibitors", return_value=(lock,)):
                result = assess_auto_native_suspend()
            self.assertFalse(result.ready)
            self.assertEqual(result.blockers,
                             ("logind_sleep_or_idle_inhibitor_present",))

    def test_delay_sleep_is_conservative_blocker(self):
        lock = Inhibitor(frozenset(("sleep",)), "delay")
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors", return_value=(lock,)):
            self.assertFalse(assess_auto_native_suspend().ready)

    def test_delay_idle_is_conservative_blocker(self):
        lock = Inhibitor(frozenset(("idle",)), "delay")
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors", return_value=(lock,)):
            self.assertFalse(assess_auto_native_suspend().ready)

    def test_unrelated_shutdown_inhibitor_does_not_block_suspend(self):
        lock = Inhibitor(frozenset(("shutdown",)), "block")
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors", return_value=(lock,)):
            self.assertTrue(assess_auto_native_suspend().ready)

    def test_unknown_inhibitor_type_is_blocked(self):
        lock = Inhibitor(frozenset(("future-power-policy",)), "block")
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors", return_value=(lock,)):
            self.assertFalse(assess_auto_native_suspend().ready)

    def test_missing_inhibitor_inventory_is_not_empty(self):
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors", return_value=None):
            result = assess_auto_native_suspend()
        self.assertEqual(result.blockers, ("logind_inhibitors_unverified",))

    def test_malformed_inhibitor_inventory_fails_closed(self):
        for locks in ([], (None,), ("invalid",),
                      (Inhibitor(frozenset(("sleep",)), "invalid"),)):
            with self.subTest(value=locks):
                with patch("isc.auto_native.can_suspend", return_value="yes"), \
                     patch("isc.auto_native.read_inhibitors", return_value=locks):
                    self.assertFalse(assess_auto_native_suspend().ready)

    def test_capability_failure_returns_unknown_without_private_details(self):
        with patch("isc.auto_native.can_suspend",
                   side_effect=OSError("/private/test-session")), \
             patch("isc.auto_native.read_inhibitors", return_value=()):
            result = assess_auto_native_suspend()
        self.assertFalse(result.ready)
        self.assertNotIn("/private", repr(result))

    def test_inhibitor_read_failure_returns_unknown(self):
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors",
                   side_effect=RuntimeError("private")):
            result = assess_auto_native_suspend()
        self.assertFalse(result.ready)
        self.assertNotIn("private", repr(result))

    def test_native_assessment_never_requests_suspend(self):
        with patch("isc.auto_native.can_suspend", return_value="yes"), \
             patch("isc.auto_native.read_inhibitors", return_value=()), \
             patch("subprocess.run",
                   side_effect=AssertionError("never issue power command")):
            self.assertTrue(assess_auto_native_suspend().ready)


if __name__ == "__main__":
    unittest.main()

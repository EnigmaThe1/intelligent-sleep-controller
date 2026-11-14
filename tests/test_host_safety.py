"""Milestone 3: real host evidence is shared and always fail-closed."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.host_safety import compose_host_snapshot, read_host_snapshot
from isc.gnome_inhibitors import GnomeInhibitFlags
from isc.inhibitors import Inhibitor
from isc.processes import ProcessCandidate, ProcessScan
from isc.session_census import SessionCensus
from isc.policy import evaluate

CLEAR = GnomeInhibitFlags(False, False)
ONE_SESSION = SessionCensus(1, 1, 0, 0)
EMPTY_SCAN = ProcessScan(())


def state(**changes):
    args = dict(
        idle_ms=1800001, inhibitors=(), gnome_flags=CLEAR,
        processes=EMPTY_SCAN, sessions=ONE_SESSION,
    )
    args.update(changes)
    return compose_host_snapshot(**args)


class HostSafetyTests(unittest.TestCase):
    def test_even_clear_sources_cannot_claim_complete_coverage(self):
        row = state()
        self.assertFalse(row.coverage_complete)
        self.assertFalse(row.scheduled_work_safe)
        self.assertFalse(evaluate(row).eligible)
        self.assertIn("workload_coverage_unverified", evaluate(row).reasons)

    def test_trusted_idle_in_seconds(self):
        self.assertEqual(state(idle_ms=1950000).user_idle_seconds, 1950.0)

    def test_invalid_idle_is_unknown(self):
        for value in (None, True, -1, "1900000", 1900.0):
            self.assertIsNone(state(idle_ms=value).user_idle_seconds)

    def test_blocking_logind_inhibitor_prevents_sleep(self):
        lock = Inhibitor(frozenset(("sleep",)), "block")
        row = state(inhibitors=(lock,))
        self.assertIn("logind_blocker_0", row.active_sleep_inhibitors)

    def test_unknown_logind_inventory_blocks(self):
        for value in (None, (), ["invalid"], ("invalid",)):
            row = state(inhibitors=value)
            if value is None or value != ():
                self.assertIn("logind_inhibitors_unverified", row.active_sleep_inhibitors)

    def test_gnome_flags_block_when_unknown_or_positive(self):
        for flags in (None, GnomeInhibitFlags(None, False),
                      GnomeInhibitFlags(False, True), "unknown"):
            self.assertTrue(state(gnome_flags=flags).active_sleep_inhibitors)

    def test_unknown_login_session_census_blocks(self):
        self.assertIn(
            "login_sessions_unverified",
            state(sessions=None).active_sleep_inhibitors)

    def test_process_candidate_is_anonymised_and_unknown(self):
        row = state(processes=ProcessScan(
            (ProcessCandidate(98765, "codex"),)))
        self.assertEqual(row.work_items[0].identifier, "process_candidate_1")
        self.assertNotIn("98765", repr(row))
        self.assertNotIn("codex", repr(row))
        self.assertFalse(evaluate(row).eligible)

    def test_unknown_process_inventory_is_blocker(self):
        row = state(processes=None)
        self.assertIn("work_unknown:process_inventory_unverified", evaluate(row).reasons)
        self.assertFalse(evaluate(row).eligible)

    def test_undecodable_process_name_is_unknown(self):
        row = state(processes=ProcessScan((), undecodable_names=1))
        self.assertIn("process_names_unverified", repr(row))

    def test_read_only_probe_supplies_shared_snapshot(self):
        with patch("isc.host_safety.read_gnome_idle_ms", return_value=1900000), \
             patch("isc.host_safety.read_inhibitors", return_value=()), \
             patch("isc.host_safety.read_gnome_inhibit_flags", return_value=CLEAR), \
             patch("isc.host_safety.read_process_candidates", return_value=EMPTY_SCAN), \
             patch("isc.host_safety.read_session_census", return_value=ONE_SESSION), \
             patch("isc.host_safety.read_scheduled_work", return_value=None), \
             patch("subprocess.run", side_effect=AssertionError("power")):
            row = read_host_snapshot()
        self.assertEqual(row.user_idle_seconds, 1900)
        self.assertFalse(evaluate(row).eligible)

    def test_failed_probe_is_not_eligible(self):
        with patch("isc.host_safety.read_inhibitors",
                   side_effect=RuntimeError("private")):
            row = read_host_snapshot()
        self.assertFalse(evaluate(row).eligible)
        self.assertNotIn("private", repr(row))


if __name__ == "__main__":
    unittest.main()

"""Milestone 3: existing service/timer evidence must block, never arm sleep."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.policy import WorkState, evaluate
from isc.scheduled_work import ScheduledWorkCensus, UnitCounts
from isc.scheduled_safety import classify_scheduled_work
from isc.host_safety import compose_host_snapshot
from isc.gnome_inhibitors import GnomeInhibitFlags
from isc.processes import ProcessScan
from isc.session_census import SessionCensus


def sample(*, system_waiting=0, user_waiting=0, system_running=0,
           user_running=0, system_transitioning=0, user_transitioning=0):
    return ScheduledWorkCensus(
        system_timers=UnitCounts(max(1, system_waiting),
                                 waiting_timers=system_waiting),
        user_timers=UnitCounts(max(1, user_waiting),
                               waiting_timers=user_waiting),
        system_services=UnitCounts(max(1, system_running + system_transitioning),
                                    running_services=system_running,
                                    activating_services=system_transitioning),
        user_services=UnitCounts(max(1, user_running + user_transitioning),
                                  running_services=user_running,
                                  activating_services=user_transitioning),
    )


def codes(safety):
    return {item.identifier for item in safety.work_items}


class ScheduledSafetyTests(unittest.TestCase):
    def test_no_units_does_not_mean_all_schedulers_verified(self):
        result = classify_scheduled_work(sample())
        self.assertTrue(result.inventory_valid)
        self.assertEqual((result.waiting_timers, result.running_services,
                          result.transitioning_services), (0, 0, 0))
        self.assertEqual(codes(result), {"other_schedulers_unverified"})
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.wake_policy_verified)

    def test_waiting_timer_is_wake_unknown_not_active_work(self):
        result = classify_scheduled_work(sample(user_waiting=2))
        self.assertIn("timer_wake_policy_unverified", codes(result))
        self.assertEqual(result.waiting_timers, 2)
        self.assertTrue(all(x.state is WorkState.UNKNOWN for x in result.work_items))

    def test_independent_running_user_service_is_unclassified(self):
        result = classify_scheduled_work(sample(user_running=1))
        self.assertIn("running_user_services_unclassified", codes(result))
        self.assertEqual(result.running_services, 1)
        self.assertFalse(result.coverage_verified)

    def test_running_system_service_is_not_silently_ignored(self):
        result = classify_scheduled_work(sample(system_running=3))
        self.assertIn("running_system_services_unclassified", codes(result))
        self.assertEqual(result.running_services, 3)

    def test_transitioning_service_is_unclassified_even_when_not_running(self):
        result = classify_scheduled_work(sample(user_transitioning=2))
        self.assertIn("transitioning_services_unclassified", codes(result))
        self.assertEqual(result.transitioning_services, 2)

    def test_missing_one_unit_inventory_is_unknown(self):
        base = sample()
        missing = ScheduledWorkCensus(
            base.system_timers, base.user_timers, None, base.user_services,
        )
        for source in (missing, None, (), "unverified"):
            with self.subTest(source=type(source).__name__):
                result = classify_scheduled_work(source)
                self.assertFalse(result.inventory_valid)
                self.assertIsNone(result.running_services)
                self.assertIn("scheduler_inventory_unverified", codes(result))

    def test_malformed_or_conflicting_aggregate_counters_fail_closed(self):
        sources = (
            ScheduledWorkCensus(UnitCounts(0, waiting_timers=1),
                                UnitCounts(0), UnitCounts(0), UnitCounts(0)),
            ScheduledWorkCensus(UnitCounts(0), UnitCounts(0),
                                UnitCounts(0, running_services=1),
                                UnitCounts(0)),
            ScheduledWorkCensus(UnitCounts(0), UnitCounts(0),
                                UnitCounts(1, running_services=1,
                                           activating_services=1),
                                UnitCounts(0)),
            ScheduledWorkCensus(UnitCounts(0), UnitCounts(0),
                                UnitCounts(1, waiting_timers=1), UnitCounts(0)),
            ScheduledWorkCensus(UnitCounts(0), UnitCounts(0),
                                UnitCounts(True), UnitCounts(0)),
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertFalse(classify_scheduled_work(source).inventory_valid)

    def test_untrusted_coverage_flags_do_not_authorize_sleep(self):
        row = sample()
        forged = ScheduledWorkCensus(
            row.system_timers, row.user_timers, row.system_services,
            row.user_services, coverage_verified=True,
        )
        result = classify_scheduled_work(forged)
        self.assertFalse(result.coverage_verified)
        self.assertFalse(result.wake_policy_verified)

    def test_scheduled_evidence_flows_into_safety_verdict(self):
        source = sample(system_running=1, user_waiting=1)
        snapshot = compose_host_snapshot(
            idle_ms=1800001, inhibitors=(),
            gnome_flags=GnomeInhibitFlags(False, False),
            processes=ProcessScan(()), sessions=SessionCensus(1, 1, 0, 0),
            scheduled=source,
        )
        result = evaluate(snapshot)
        self.assertFalse(result.eligible)
        self.assertIn("work_unknown:running_system_services_unclassified", result.reasons)
        self.assertIn("work_unknown:timer_wake_policy_unverified", result.reasons)
        self.assertFalse(snapshot.scheduled_work_safe)
        self.assertFalse(snapshot.coverage_complete)

    def test_no_unit_details_or_user_metadata_are_exported(self):
        safe = classify_scheduled_work(sample(user_running=1))
        self.assertNotIn("company-internal", repr(safe))
        self.assertNotIn(".service", repr(safe))


if __name__ == "__main__":
    unittest.main()

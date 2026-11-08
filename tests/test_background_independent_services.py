"""Synthetic continuous independent-service observations. No real systemd I/O."""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.background_independent_services import (
    BackgroundIndependentServiceObserver, IndependentServicePulse,
)
from isc.independent_service_discovery import IndependentServiceCounts
from isc.receiver import LocalMonitor, read_monitor_health


def observed(active, linked):
    return IndependentServiceCounts(
        active, linked, active - linked, uncertain=False,
    )


def run_samples(samples):
    pulses = []
    iterator = iter(samples)
    worker = None

    def capture(*, scope):
        return next(iterator)

    def publish(pulse):
        pulses.append(pulse)
        worker._stop.set()

    worker = BackgroundIndependentServiceObserver(
        publish, scanner=capture, interval_seconds=10,
    )
    worker._run()
    return pulses[0]


class IndependentBackgroundTests(unittest.TestCase):
    def test_one_pass_sums_both_scopes_without_authority(self):
        pulse = run_samples([observed(77, 0), observed(53, 1)])
        self.assertEqual(pulse.active_services, 130)
        self.assertEqual(pulse.other_active, 129)
        self.assertEqual(pulse.timer_linked_active, 1)
        self.assertIsNone(pulse.net_count_delta)
        self.assertFalse(pulse.coverage_verified)
        self.assertNotIn("systemd", repr(pulse).lower())

    def test_missing_scope_becomes_unknown(self):
        pulse = run_samples([observed(12, 1), IndependentServiceCounts(None, None, None)])
        self.assertIsNone(pulse.active_services)
        self.assertIsNone(pulse.other_active)
        self.assertIsNone(pulse.net_count_delta)

    def test_inconsistent_scope_totals_are_unknown(self):
        bad = IndependentServiceCounts(3, 1, 1, uncertain=False)
        pulse = run_samples([bad, observed(3, 0)])
        self.assertIsNone(pulse.active_services)

    def test_scanner_exceptions_dont_stop_observer(self):
        pulse = run_samples([observed(2, 0)])
        self.assertEqual(pulse.active_services, None)

    def test_intermediate_failure_resets_comparison_history(self):
        samples = iter([
            observed(3, 1), observed(2, 0),
            None, observed(2, 0),
            observed(5, 1), observed(2, 0),
        ])
        pulses = []
        worker = None

        def scanner(*, scope):
            return next(samples)

        def publish(pulse):
            pulses.append(pulse)
            if len(pulses) == 3:
                worker._stop.set()

        worker = BackgroundIndependentServiceObserver(
            publish, scanner=scanner, interval_seconds=10,
        )
        with patch.object(worker._stop, "wait", return_value=False):
            worker._run()
        self.assertEqual(pulses[0].active_services, 5)
        self.assertIsNone(pulses[0].net_count_delta)
        self.assertIsNone(pulses[1].active_services)
        self.assertEqual(pulses[2].active_services, 7)
        self.assertIsNone(pulses[2].net_count_delta)

    def test_changed_aggregate_is_net_delta_not_job_completion(self):
        samples = iter([
            observed(3, 1), observed(2, 0),
            observed(5, 1), observed(2, 0),
        ])
        pulses = []
        worker = None

        def scanner(*, scope):
            return next(samples)

        def publish(pulse):
            pulses.append(pulse)
            if len(pulses) == 2:
                worker._stop.set()

        worker = BackgroundIndependentServiceObserver(
            publish, scanner=scanner, interval_seconds=10,
        )
        with patch.object(worker._stop, "wait", return_value=False):
            worker._run()
        self.assertEqual(pulses[1].active_services, 7)
        self.assertEqual(pulses[1].net_count_delta, 4)
        self.assertFalse(pulses[1].coverage_verified)

    def test_receiver_roundtrip_is_aggregate_only(self):
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp, advertise=True) as monitor:
                monitor.update_independent_pulse(IndependentServicePulse(
                    130, 129, 1, 2, time.monotonic(),
                ))
                worker = threading.Thread(
                    target=monitor.run_for, args=(0.5,), daemon=True,
                )
                worker.start()
                try:
                    health = read_monitor_health(runtime_dir=temp)
                    self.assertIsNotNone(health)
                    self.assertEqual(health.independent_services, 130)
                    self.assertEqual(health.independent_other_active, 129)
                    self.assertEqual(health.independent_timer_linked_active, 1)
                    self.assertEqual(health.independent_net_count_delta, 2)
                    self.assertFalse(health.coverage_verified)
                    self.assertNotIn("unit", repr(health))
                finally:
                    worker.join(timeout=2)

    def test_missing_and_stale_receiver_observations_remain_unknown(self):
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                response = Mock()
                monitor._reply_health(response)
                initial = json.loads(response.sendall.call_args.args[0])
                self.assertIsNone(initial["independent_sample_age_seconds"])
                self.assertIsNone(initial["independent_services"])

                start = time.monotonic()
                monitor.update_independent_pulse(IndependentServicePulse(
                    5, 4, 1, None, start,
                ))
                response.reset_mock()
                with patch("isc.receiver.time.monotonic", return_value=start + 241):
                    monitor._reply_health(response)
                stale = json.loads(response.sendall.call_args.args[0])
                self.assertIsNone(stale["independent_services"])
                self.assertIsNone(stale["independent_other_active"])
                self.assertEqual(stale["independent_sample_age_seconds"], 241)

    def test_forged_or_inconsistent_protocol_counts_rejected(self):
        from unittest.mock import MagicMock
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                monitor.update_independent_pulse(IndependentServicePulse(
                    5, 4, 1, 1, time.monotonic(),
                ))
                peer = Mock()
                monitor._reply_health(peer)
                baseline = json.loads(peer.sendall.call_args.args[0])
        for change in (
            {"independent_services": -1},
            {"independent_services": 2},
            {"independent_services": None},
            {"independent_sample_age_seconds": None},
            {"independent_net_count_delta": True},
        ):
            with self.subTest(change=change):
                payload = dict(baseline, **change)
                conn = MagicMock()
                conn.__enter__.return_value = conn
                conn.recv.side_effect = [json.dumps(payload).encode(), b""]
                with patch("isc.receiver.discover_monitor_socket",
                           return_value=Path("/private/events.sock")):
                    with patch("isc.receiver.socket.socket", return_value=conn):
                        self.assertIsNone(read_monitor_health())

    def test_invalid_observer_configuration_rejected(self):
        for interval in (0, 301, float("nan"), True):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    BackgroundIndependentServiceObserver(
                        lambda pulse: None, interval_seconds=interval,
                    )

    def test_status_shows_independent_counts_without_identifiers(self):
        from isc.cli import status
        with TemporaryDirectory() as temp:
            with LocalMonitor(runtime_dir=temp) as monitor:
                monitor.update_independent_pulse(IndependentServicePulse(
                    130, 129, 1, None, time.monotonic(),
                ))
                with patch("isc.cli.read_monitor_health") as reader:
                    from unittest.mock import MagicMock
                    reader.return_value = MagicMock(
                        accepted=0, rejected=0, observed_active=0,
                        observed_waiting_human=0, observed_unknown=0,
                        claude_code_observations=None,
                        process_sample_age_seconds=None,
                        scheduler_sample_age_seconds=None,
                        independent_sample_age_seconds=1,
                        independent_services=130,
                        independent_timer_linked_active=1,
                        independent_other_active=129,
                        independent_net_count_delta=None,
                    )
                    from contextlib import redirect_stdout
                    from io import StringIO
                    buf = StringIO()
                    with patch("isc.cli.read_mode"), redirect_stdout(buf):
                        self.assertEqual(status(), 0)
                    self.assertIn("other=129", buf.getvalue())
                    self.assertIn("Coverage verified: NO", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

"""Step 98: synthetic signal diagnostics are never proof of safe suspend."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.native_signal_stream import NativeSignalStream


class NativeSignalStreamTests(unittest.TestCase):
    def test_unconnected_is_not_zero_or_complete(self):
        pulse = NativeSignalStream().summary()
        self.assertEqual(pulse.status, "NOT_CONNECTED")
        self.assertIsNone(pulse.events)
        self.assertFalse(pulse.coverage_verified)

    def test_ordered_events_are_only_diagnostic(self):
        stream = NativeSignalStream()
        self.assertTrue(stream.connect(now=0))
        for name in ("JobNew", "JobRemoved", "UnitNew", "UnitRemoved",
                     "PropertiesChanged"):
            self.assertTrue(stream.event(name, now=1))
        pulse = stream.summary()
        self.assertEqual((pulse.status, pulse.events),
                         ("OBSERVING_UNVERIFIED", 5))
        self.assertFalse(pulse.coverage_verified)
        self.assertFalse(pulse.uninterrupted_events_verified)
        self.assertFalse(pulse.task_owner_authenticated)

    def test_no_events_is_not_idle(self):
        stream = NativeSignalStream()
        self.assertTrue(stream.connect(now=0))
        self.assertTrue(stream.heartbeat(now=30))
        self.assertEqual(stream.summary().events, 0)
        self.assertFalse(stream.summary().coverage_verified)

    def test_disconnect_remains_sticky_unknown(self):
        stream = NativeSignalStream()
        stream.connect(now=1)
        stream.disconnect()
        self.assertFalse(stream.connect(now=2))
        self.assertEqual(stream.summary().status, "UNKNOWN")
        self.assertEqual(stream.summary().interruptions, 1)

    def test_reversed_clock_is_unknown(self):
        stream = NativeSignalStream()
        stream.connect(now=100)
        self.assertFalse(stream.event("JobNew", now=99))
        self.assertEqual(stream.summary().status, "UNKNOWN")

    def test_gap_is_unknown(self):
        stream = NativeSignalStream()
        stream.connect(now=0)
        self.assertFalse(stream.heartbeat(now=60.01))
        self.assertEqual(stream.summary().status, "UNKNOWN")

    def test_sixty_second_limit_is_inclusive(self):
        stream = NativeSignalStream()
        stream.connect(now=0)
        self.assertTrue(stream.heartbeat(now=60))
        self.assertEqual(stream.summary().status, "OBSERVING_UNVERIFIED")

    def test_unknown_event_is_sticky(self):
        stream = NativeSignalStream()
        stream.connect(now=0)
        self.assertFalse(stream.event("PrivateEvent", now=1))
        self.assertFalse(stream.event("JobNew", now=2))
        self.assertEqual(stream.summary().status, "UNKNOWN")

    def test_event_before_connection_is_unknown(self):
        stream = NativeSignalStream()
        self.assertFalse(stream.event("JobNew", now=0))
        self.assertEqual(stream.summary().status, "UNKNOWN")

    def test_duplicate_connection_rejected(self):
        stream = NativeSignalStream()
        self.assertTrue(stream.connect(now=0))
        self.assertFalse(stream.connect(now=1))
        self.assertEqual(stream.summary().status, "UNKNOWN")

    def test_bad_time_types_are_rejected(self):
        for value in (-1, float("nan"), float("inf"), True, "4", None):
            with self.subTest(value=value):
                stream = NativeSignalStream()
                self.assertFalse(stream.connect(now=value))
                self.assertEqual(stream.summary().status, "UNKNOWN")

    def test_bounded_cardinality(self):
        stream = NativeSignalStream()
        self.assertTrue(stream.connect(now=0))
        for index in range(256):
            self.assertTrue(stream.event("JobNew", now=index / 10))
        self.assertFalse(stream.event("JobNew", now=26))
        self.assertIsNone(stream.summary().events)
        self.assertEqual(stream.summary().interruptions, 1)

    def test_no_private_payload_or_identifiers_retained(self):
        stream = NativeSignalStream()
        stream.connect(now=0)
        self.assertFalse(stream.event("JobNew private.service secret", now=1))
        self.assertNotIn("private.service", repr(stream.summary()))
        self.assertNotIn("secret", repr(stream.summary()))

    def test_repeated_disconnection_does_not_grow_counter(self):
        stream = NativeSignalStream()
        stream.disconnect()
        stream.disconnect()
        self.assertEqual(stream.summary().interruptions, 1)


if __name__ == "__main__":
    unittest.main()

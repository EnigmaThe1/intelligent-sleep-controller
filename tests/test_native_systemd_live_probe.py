"""Step 99e: conservative recognition of systemd 255 signal headers."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.native_systemd_live_probe import (
    _recognise_header, probe_live_user_signal, LiveSignalProbe,
)

PREFIX = b"Monitoring bus message stream.\n"
FRAME = (
    "\u2023 Type=signal  Endian=l  Flags=1  Version=1 Cookie=2\n"
    "  Sender=:1.12  Path=/org/freedesktop/systemd1  "
    "Interface=org.freedesktop.systemd1.Manager  Member=JobNew\n"
).encode("utf-8")


class NativeLiveSignalTests(unittest.TestCase):
    def test_valid_header_matches(self):
        self.assertTrue(_recognise_header(PREFIX + FRAME))

    def test_candidate_is_not_authority(self):
        row = LiveSignalProbe("CANDIDATE_SIGNAL_UNVERIFIED", 1)
        self.assertFalse(row.coverage_verified)
        self.assertFalse(row.event_delivery_verified)
        self.assertFalse(row.task_owner_authenticated)

    def test_other_message_type_refused(self):
        self.assertFalse(_recognise_header(FRAME.replace(
            b"Type=signal", b"Type=method_call")))

    def test_other_interface_refused(self):
        self.assertFalse(_recognise_header(FRAME.replace(
            b"org.freedesktop.systemd1.Manager", b"org.freedesktop.DBus")))

    def test_other_path_refused(self):
        self.assertFalse(_recognise_header(FRAME.replace(
            b"Path=/org/freedesktop/systemd1", b"Path=/private")))

    def test_unrecognised_member_refused(self):
        self.assertFalse(_recognise_header(FRAME.replace(
            b"Member=JobNew", b"Member=Reloading")))

    def test_partial_header_refused(self):
        self.assertFalse(_recognise_header(FRAME[:-2]))

    def test_payload_cannot_mimic_header_without_marker(self):
        payload = b'MESSAGE "s" {\n STRING "Type=signal";\n' + (
            b"Sender=:1.12 Path=/org/freedesktop/systemd1 "
            b"Interface=org.freedesktop.systemd1.Manager Member=JobNew\n")
        self.assertFalse(_recognise_header(payload))

    def test_empty_and_invalid_duration_fail_closed(self):
        for value in (0, 11, True, None, 1.5, "3", -1):
            with self.subTest(value=value):
                with patch("isc.native_systemd_live_probe.subprocess.Popen") as spawn:
                    row = probe_live_user_signal(seconds=value)
                    self.assertEqual(row.state, "UNKNOWN")
                    spawn.assert_not_called()

    def test_known_manager_signal_members(self):
        for member in (b"JobNew", b"JobRemoved", b"UnitNew", b"UnitRemoved"):
            with self.subTest(member=member):
                self.assertTrue(_recognise_header(
                    FRAME.replace(b"Member=JobNew", b"Member=" + member)))

    def test_identifiers_not_returned(self):
        row = LiveSignalProbe("UNKNOWN")
        self.assertNotIn("org.freedesktop", repr(row))
        self.assertIsNone(row.candidate_events)


if __name__ == "__main__":
    unittest.main()

import json
import os
from pathlib import Path
import stat
import sys
import threading
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.settings import (
    CONFIG_FILE, PROJECT_DIR, ModeConfigError, ModeStatus, read_mode, set_mode,
    mode_dispatch_guard,
)


class ModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "config"
        self.directory = self.root / PROJECT_DIR
        self.file = self.directory / CONFIG_FILE

    def test_missing_defaults_to_off_without_writing(self):
        self.assertEqual(read_mode(config_root=self.root).mode, "off")
        self.assertFalse(read_mode(config_root=self.root).configured)
        self.assertFalse(self.root.exists())

    def test_monitored_preference_persists_over_reads(self):
        setting = set_mode("monitor", config_root=self.root)
        self.assertEqual(setting.mode, "monitor")
        self.assertTrue(setting.configured)
        self.assertEqual(read_mode(config_root=self.root).mode, "monitor")
        self.assertEqual(
            json.loads(self.file.read_text()), {"version": 1, "mode": "monitor"},
        )
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)

    def test_off_is_explicitly_persisted(self):
        set_mode("monitor", config_root=self.root)
        set_mode("off", config_root=self.root)
        self.assertEqual(read_mode(config_root=self.root).mode, "off")
        self.assertTrue(read_mode(config_root=self.root).configured)

    def test_default_off_can_be_persisted(self):
        self.assertFalse(read_mode(config_root=self.root).configured)
        self.assertTrue(set_mode("off", config_root=self.root).configured)

    def test_repeated_setting_does_not_write_again(self):
        set_mode("monitor", config_root=self.root)
        inode = self.file.stat().st_ino
        self.assertEqual(set_mode("monitor", config_root=self.root).mode, "monitor")
        self.assertEqual(self.file.stat().st_ino, inode)

    def test_on_rejected_before_any_file_is_created(self):
        with self.assertRaisesRegex(ModeConfigError, "on_not_implemented"):
            set_mode("on", config_root=self.root)
        self.assertFalse(self.root.exists())

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ModeConfigError):
            set_mode("unknown", config_root=self.root)

    def test_corrupt_config_rejected_without_overwrite(self):
        set_mode("monitor", config_root=self.root)
        self.file.write_text("{broken")
        original = self.file.read_bytes()
        with self.assertRaises(ModeConfigError):
            set_mode("off", config_root=self.root)
        self.assertEqual(self.file.read_bytes(), original)

    def test_unsupported_version_rejected_without_overwrite(self):
        set_mode("monitor", config_root=self.root)
        self.file.write_text('{"version":2,"mode":"off"}')
        with self.assertRaises(ModeConfigError):
            set_mode("off", config_root=self.root)

    def test_unsafe_file_permissions_fail_closed(self):
        set_mode("monitor", config_root=self.root)
        self.file.chmod(0o664)
        with self.assertRaises(ModeConfigError):
            read_mode(config_root=self.root)
        with self.assertRaises(ModeConfigError):
            set_mode("off", config_root=self.root)

    def test_symlink_directory_is_rejected(self):
        other = self.root / "real"
        self.root.mkdir()
        other.mkdir()
        self.directory.symlink_to(other, target_is_directory=True)
        with self.assertRaises(ModeConfigError):
            read_mode(config_root=self.root)

    def test_symlink_file_rejected(self):
        set_mode("monitor", config_root=self.root)
        self.file.rename(self.directory / "real.json")
        self.file.symlink_to(self.directory / "real.json")
        with self.assertRaises(ModeConfigError):
            read_mode(config_root=self.root)

    def test_extra_keys_rejected(self):
        set_mode("off", config_root=self.root)
        self.file.write_text('{"version":1,"mode":"off","secret":"x"}')
        with self.assertRaises(ModeConfigError):
            read_mode(config_root=self.root)

    def test_invalid_mode_fails_closed(self):
        set_mode("monitor", config_root=self.root)
        self.file.write_text('{"version":1,"mode":"on"}')
        with self.assertRaises(ModeConfigError):
            read_mode(config_root=self.root)

    def test_rejects_relative_config_dir(self):
        with self.assertRaises(ModeConfigError):
            read_mode(config_root="relative")
        with self.assertRaises(ModeConfigError):
            set_mode("monitor", config_root="relative")

    def test_parent_root_group_writable_rejected(self):
        self.root.mkdir(mode=0o775)
        self.root.chmod(0o775)
        with self.assertRaises(ModeConfigError):
            set_mode("monitor", config_root=self.root)

    def test_missing_dispatch_lock_fails_without_writing(self):
        with self.assertRaises(ModeConfigError):
            with mode_dispatch_guard(config_root=self.root):
                self.fail("Unconfigured system must not be authorized")
        self.assertFalse(self.root.exists())

    def test_stale_idempotent_off_observation_never_loses_off(self):
        # Simulate observing OFF before acquiring the writer lock, while
        # another process has since selected MONITOR.
        set_mode("monitor", config_root=self.root)
        actual_reader = read_mode
        reads = [0]

        def stale_first_read(*, config_root=None):
            reads[0] += 1
            if reads[0] == 1:
                return ModeStatus("off", True)
            return actual_reader(config_root=config_root)

        with patch("isc.settings.read_mode", side_effect=stale_first_read):
            outcome = set_mode("off", config_root=self.root)
        self.assertEqual(outcome.mode, "off")
        self.assertEqual(read_mode(config_root=self.root).mode, "off")
        self.assertGreaterEqual(reads[0], 2)

    def test_dispatch_guard_reads_actual_locked_mode(self):
        set_mode("monitor", config_root=self.root)
        with mode_dispatch_guard(config_root=self.root) as state:
            self.assertEqual(state.mode, "monitor")
            self.assertTrue(state.configured)

    def test_concurrent_dispatch_refuses_immediately(self):
        set_mode("monitor", config_root=self.root)
        with mode_dispatch_guard(config_root=self.root):
            with self.assertRaisesRegex(ModeConfigError, "mode_dispatch_busy"):
                with mode_dispatch_guard(config_root=self.root):
                    self.fail("Second dispatch must never race")

    def test_off_update_serializes_with_dispatch_lock(self):
        set_mode("monitor", config_root=self.root)
        entered = threading.Event()
        completed = threading.Event()
        failures = []

        def change_to_off():
            entered.set()
            try:
                set_mode("off", config_root=self.root)
            except Exception as error:
                failures.append(error)
            finally:
                completed.set()

        worker = threading.Thread(target=change_to_off, daemon=True)
        with mode_dispatch_guard(config_root=self.root):
            worker.start()
            self.assertTrue(entered.wait(timeout=1))
            time.sleep(0.03)
            self.assertFalse(completed.is_set(),
                             "Mode update cannot commit during dispatch")
            self.assertEqual(read_mode(config_root=self.root).mode, "monitor")
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(completed.is_set())
        self.assertEqual(read_mode(config_root=self.root).mode, "off")

    def test_dispatch_guard_rejects_symlinked_lock(self):
        set_mode("monitor", config_root=self.root)
        lock = self.directory / ".settings.lock"
        real = self.directory / "actual-lock"
        lock.rename(real)
        lock.symlink_to(real)
        with self.assertRaises(ModeConfigError):
            with mode_dispatch_guard(config_root=self.root):
                self.fail("Must not follow link to lock")

    def test_dispatch_guard_rejects_unsafe_lock_permissions(self):
        set_mode("monitor", config_root=self.root)
        lock = self.directory / ".settings.lock"
        lock.chmod(0o666)
        with self.assertRaisesRegex(ModeConfigError, "unsafe_mode_dispatch_lock"):
            with mode_dispatch_guard(config_root=self.root):
                self.fail("Unsafe lock must not grant permission")

    def test_dispatch_guard_raises_for_invalid_mode_file(self):
        set_mode("monitor", config_root=self.root)
        self.file.write_text("{invalid")
        with self.assertRaises(ModeConfigError):
            with mode_dispatch_guard(config_root=self.root):
                self.fail("Corrupt settings must not dispatch")

    def test_does_not_invoke_power_management(self):
        with patch("subprocess.run", side_effect=AssertionError("power action")):
            set_mode("monitor", config_root=self.root)
            set_mode("off", config_root=self.root)


if __name__ == "__main__":
    unittest.main()

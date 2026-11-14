import os
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.tray_singleton import (
    TrayAlreadyRunning, TrayLockUnavailable, single_tray_instance,
)


class TraySingletonTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def test_one_tray_per_user_session(self):
        with single_tray_instance(runtime_dir=self.path):
            with self.assertRaises(TrayAlreadyRunning):
                with single_tray_instance(runtime_dir=self.path):
                    pass
        with single_tray_instance(runtime_dir=self.path):
            pass

    def test_lock_file_is_private_and_persists_after_exit(self):
        file = self.path / "isc-tray.lock"
        with single_tray_instance(runtime_dir=self.path):
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        self.assertTrue(file.exists())

    def test_does_not_follow_symlink_for_existing_lock(self):
        target = self.path / "unrelated-file"
        target.write_text("not a lock")
        (self.path / "isc-tray.lock").symlink_to(target)
        with self.assertRaises(TrayLockUnavailable):
            with single_tray_instance(runtime_dir=self.path):
                pass
        self.assertEqual(target.read_text(), "not a lock")

    def test_refuses_insecure_lock_permissions(self):
        path = self.path / "isc-tray.lock"
        path.write_text("")
        path.chmod(0o666)
        with self.assertRaises(TrayLockUnavailable):
            with single_tray_instance(runtime_dir=self.path):
                pass

    def test_missing_runtime_root_is_refused(self):
        with self.assertRaises(TrayLockUnavailable):
            with single_tray_instance(runtime_dir=self.path / "missing"):
                pass


if __name__ == "__main__":
    unittest.main()

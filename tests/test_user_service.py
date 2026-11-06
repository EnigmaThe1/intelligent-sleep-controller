import os
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.user_service import (
    UNIT_NAME, UserServiceError, unit_contents, inspect_user_service,
    modify_user_service,
)


class UserServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "xdg"
        self.root.mkdir(mode=0o700)
        self.unit = self.root / "systemd" / "user" / UNIT_NAME

    def test_missing_unit_does_not_create_or_start_anything(self):
        with patch("isc.user_service.subprocess.run",
                   side_effect=AssertionError("systemctl was run")):
            current = inspect_user_service(config_root=self.root)
        self.assertFalse(current.installed)
        self.assertFalse(self.unit.parent.exists())

    def test_native_unit_has_no_suspend_or_privileged_exec(self):
        text = unit_contents(
            interpreter="/usr/bin/python3",
            source_root="/srv/playground_dev/intelligent-sleep-controller/src",
        ).decode()
        self.assertIn("Type=simple", text)
        self.assertIn("Restart=on-failure", text)
        self.assertIn("NoNewPrivileges=yes", text)
        self.assertIn("ExecStart=/usr/bin/python3 -B -m isc monitor-service", text)
        self.assertNotIn("systemctl suspend", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("ExecStart=/bin/sh", text)

    @patch("isc.user_service._run_systemd", return_value=True)
    def test_standard_0755_systemd_directories_install_without_chmod(self, run):
        """Normal pre-existing user systemd dirs must not be treated as unsafe."""
        sysdir = self.root / "systemd"
        sysdir.mkdir(mode=0o755)
        self.unit.parent.mkdir(mode=0o755)
        sysdir.chmod(0o755)
        self.unit.parent.chmod(0o755)
        before = (sysdir.stat().st_mode & 0o777,
                  self.unit.parent.stat().st_mode & 0o777)
        self.assertEqual(before, (0o755, 0o755))
        result = modify_user_service("enable", config_root=self.root)
        self.assertTrue(result.installed)
        self.assertEqual(self.unit.read_bytes(), unit_contents())
        self.assertEqual(
            (sysdir.stat().st_mode & 0o777,
             self.unit.parent.stat().st_mode & 0o777),
            before,
        )
        self.assertIn(("enable", "--now", UNIT_NAME),
                      [x.args for x in run.call_args_list])

    @patch("isc.user_service._run_systemd")
    def test_group_writable_existing_systemd_directory_refused(self, run):
        sysdir = self.root / "systemd"
        sysdir.mkdir(mode=0o775)
        sysdir.chmod(0o775)
        with self.assertRaisesRegex(UserServiceError, "unsafe_unit_directory"):
            modify_user_service("enable", config_root=self.root)
        self.assertFalse(self.unit.exists())
        self.assertEqual(sysdir.stat().st_mode & 0o777, 0o775)
        run.assert_not_called()

    @patch("isc.user_service._run_systemd")
    def test_world_writable_user_unit_directory_refused(self, run):
        sysdir = self.root / "systemd"
        sysdir.mkdir(mode=0o700)
        self.unit.parent.mkdir(mode=0o777)
        self.unit.parent.chmod(0o777)
        with self.assertRaisesRegex(UserServiceError, "unsafe_unit_directory"):
            modify_user_service("enable", config_root=self.root)
        self.assertFalse(self.unit.exists())
        run.assert_not_called()

    @patch("isc.user_service._run_systemd")
    def test_symlinked_systemd_directory_refused_without_mutation(self, run):
        foreign = self.root / "elsewhere"
        foreign.mkdir(mode=0o700)
        (self.root / "systemd").symlink_to(foreign, target_is_directory=True)
        with self.assertRaisesRegex(UserServiceError, "unsafe_unit_directory"):
            modify_user_service("enable", config_root=self.root)
        self.assertTrue((self.root / "systemd").is_symlink())
        self.assertFalse(self.unit.exists())
        run.assert_not_called()

    @patch("isc.user_service._run_systemd", return_value=True)
    def test_explicit_enable_and_disable_touch_only_owned_unit(self, run):
        status = modify_user_service("enable", config_root=self.root)
        self.assertTrue(status.installed)
        self.assertEqual(self.unit.read_bytes(), unit_contents())
        self.assertEqual(self.unit.stat().st_mode & 0o777, 0o600)
        self.assertEqual(run.call_args_list[0].args, ("daemon-reload",))
        self.assertEqual(
            run.call_args_list[1].args,
            ("enable", "--now", UNIT_NAME),
        )
        status = modify_user_service("disable", config_root=self.root)
        self.assertFalse(status.installed)
        self.assertFalse(self.unit.exists())
        self.assertIn(
            ("disable", "--now", UNIT_NAME),
            [x.args for x in run.call_args_list],
        )

    @patch("isc.user_service._run_systemd")
    def test_foreign_unit_is_not_replaced_or_disabled(self, run):
        self.unit.parent.mkdir(parents=True, mode=0o700)
        # Newly created systemd parent may have default umask, explicit mode.
        (self.root / "systemd").chmod(0o700)
        self.unit.parent.chmod(0o700)
        self.unit.write_text("CUSTOM UNIT")
        self.unit.chmod(0o600)
        with self.assertRaisesRegex(UserServiceError, "foreign_or_modified"):
            modify_user_service("enable", config_root=self.root)
        with self.assertRaisesRegex(UserServiceError, "foreign_or_modified"):
            modify_user_service("disable", config_root=self.root)
        run.assert_not_called()
        self.assertEqual(self.unit.read_text(), "CUSTOM UNIT")

    @patch("isc.user_service._run_systemd")
    def test_failed_disable_preserves_existing_unit(self, run):
        self.unit.parent.mkdir(parents=True, mode=0o700)
        (self.root / "systemd").chmod(0o700)
        self.unit.parent.chmod(0o700)
        self.unit.write_bytes(unit_contents())
        self.unit.chmod(0o600)
        run.return_value = False
        with self.assertRaisesRegex(UserServiceError, "disable_failed"):
            modify_user_service("disable", config_root=self.root)
        self.assertTrue(self.unit.exists())

    @patch("isc.user_service._run_systemd")
    def test_failed_install_leaves_recoverable_owned_unit(self, run):
        run.return_value = False
        with self.assertRaisesRegex(UserServiceError, "daemon_reload_failed"):
            modify_user_service("enable", config_root=self.root)
        self.assertTrue(self.unit.exists())
        self.assertEqual(self.unit.read_bytes(), unit_contents())

    @patch("isc.user_service._run_systemd")
    def test_user_service_change_requires_known_action(self, run):
        with self.assertRaises(UserServiceError):
            modify_user_service("restart", config_root=self.root)
        run.assert_not_called()

    @patch("isc.user_service._run_systemd")
    def test_symlink_unit_refused(self, run):
        self.unit.parent.mkdir(parents=True, mode=0o700)
        (self.root / "systemd").chmod(0o700)
        self.unit.parent.chmod(0o700)
        elsewhere = self.root / "other"
        elsewhere.write_text("PRIVATE")
        self.unit.symlink_to(elsewhere)
        with self.assertRaisesRegex(UserServiceError, "unsafe_unit_file"):
            modify_user_service("enable", config_root=self.root)
        self.assertEqual(elsewhere.read_text(), "PRIVATE")
        run.assert_not_called()

    def test_rejects_unsafe_exec_paths(self):
        with self.assertRaises(UserServiceError):
            unit_contents(interpreter="/usr/bin/python3;other")
        with self.assertRaises(UserServiceError):
            unit_contents(source_root="/tmp/space path")


if __name__ == "__main__":
    unittest.main()

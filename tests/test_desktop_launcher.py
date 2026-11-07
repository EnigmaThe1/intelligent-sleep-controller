"""User-owned visible GNOME application launcher registration tests."""
from pathlib import Path
import os
import stat
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.desktop_launcher import (
    LAUNCHER_NAME, DesktopLauncherError, inspect_launcher,
    launcher_contents, modify_launcher,
)


class DesktopLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "share"
        self.file = self.root / "applications" / LAUNCHER_NAME

    def test_missing_launcher_does_not_create_files(self):
        self.assertEqual(
            inspect_launcher(data_root=self.root).state, "missing",
        )
        self.assertFalse(self.root.exists())

    def test_install_visible_entry_with_direct_window_exec(self):
        created = modify_launcher("install", data_root=self.root)
        self.assertEqual(created.state, "installed")
        self.assertTrue(created.changed)
        self.assertEqual(self.file.read_bytes(), launcher_contents())
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        text = self.file.read_text()
        self.assertIn("[Desktop Entry]", text)
        self.assertIn("Name=Intelligent Sleep Controller", text)
        self.assertIn("Icon=preferences-system-power", text)
        self.assertIn("Categories=Settings;Utility;", text)
        self.assertIn(" -B -m isc window", text)
        self.assertIn("Terminal=false", text)
        self.assertNotIn("NoDisplay=true", text)
        self.assertNotIn("Autostart", text)
        self.assertNotIn("systemctl", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("sh -c", text)
        second = modify_launcher("install", data_root=self.root)
        self.assertFalse(second.changed)
        self.assertEqual(inspect_launcher(data_root=self.root).state, "installed")

    def test_explicit_remove_preserves_parent_directory(self):
        modify_launcher("install", data_root=self.root)
        stopped = modify_launcher("remove", data_root=self.root)
        self.assertEqual(stopped.state, "missing")
        self.assertTrue(stopped.changed)
        self.assertFalse(self.file.exists())
        self.assertTrue(self.file.parent.exists())
        self.assertFalse(
            modify_launcher("remove", data_root=self.root).changed
        )

    def test_unknown_entry_not_deleted_or_overwritten(self):
        self.root.mkdir(mode=0o700)
        self.file.parent.mkdir(mode=0o700)
        body = b"[Desktop Entry]\nName=PRIVATE OTHER APP\n"
        self.file.write_bytes(body)
        self.file.chmod(0o600)
        self.assertEqual(inspect_launcher(data_root=self.root).state, "foreign")
        for operation in ("install", "remove"):
            with self.assertRaisesRegex(
                DesktopLauncherError, "foreign_launcher_not_modified",
            ):
                modify_launcher(operation, data_root=self.root)
        self.assertEqual(self.file.read_bytes(), body)

    def test_symlink_rejected_and_other_target_preserved(self):
        self.file.parent.mkdir(parents=True, mode=0o700)
        secret = self.root / "private"
        secret.write_text("KEEP_THIS")
        self.file.symlink_to(secret)
        with self.assertRaises(DesktopLauncherError):
            modify_launcher("install", data_root=self.root)
        self.assertEqual(secret.read_text(), "KEEP_THIS")

    def test_group_writable_launcher_directory_rejected(self):
        self.file.parent.mkdir(parents=True, mode=0o777)
        self.file.parent.chmod(0o777)
        with self.assertRaises(DesktopLauncherError):
            modify_launcher("install", data_root=self.root)
        self.assertFalse(self.file.exists())

    def test_file_permission_insecure_refused(self):
        modify_launcher("install", data_root=self.root)
        self.file.chmod(0o666)
        with self.assertRaises(DesktopLauncherError):
            inspect_launcher(data_root=self.root)
        with self.assertRaises(DesktopLauncherError):
            modify_launcher("remove", data_root=self.root)
        self.assertTrue(self.file.exists())

    def test_legacy_interpreter_path_can_be_removed_not_silently_replaced(self):
        self.root.mkdir(mode=0o700)
        self.file.parent.mkdir(mode=0o700)
        old = launcher_contents(
            interpreter="/opt/old-python/bin/python3",
            source_root="/srv/playground_dev/old-isc/src",
        )
        self.file.write_bytes(old)
        self.file.chmod(0o600)
        self.assertEqual(inspect_launcher(data_root=self.root).state, "outdated")
        with self.assertRaisesRegex(
            DesktopLauncherError, "outdated_launcher_remove_before_reinstall",
        ):
            modify_launcher("install", data_root=self.root)
        self.assertEqual(self.file.read_bytes(), old)
        self.assertEqual(
            modify_launcher("remove", data_root=self.root).state,
            "missing",
        )

    def test_modified_managed_marker_is_not_sufficient_ownership(self):
        modify_launcher("install", data_root=self.root)
        changed = self.file.read_bytes().replace(
            b"Terminal=false", b"Terminal=true",
        )
        self.file.write_bytes(changed)
        self.assertEqual(inspect_launcher(data_root=self.root).state, "foreign")
        with self.assertRaises(DesktopLauncherError):
            modify_launcher("remove", data_root=self.root)
        self.assertEqual(self.file.read_bytes(), changed)

    def test_shell_like_paths_rejected_and_no_execution(self):
        for bad in ("/tmp/unsafe path", "/tmp/python;evil", "relative/python"):
            with self.assertRaises(DesktopLauncherError):
                launcher_contents(interpreter=bad)
        with patch("subprocess.run", side_effect=AssertionError("launched process")):
            with patch("subprocess.Popen", side_effect=AssertionError("launched process")):
                modify_launcher("install", data_root=self.root)
                modify_launcher("remove", data_root=self.root)

    def test_data_home_must_be_absolute(self):
        with self.assertRaisesRegex(
            DesktopLauncherError, "xdg_data_home_must_be_absolute",
        ):
            inspect_launcher(data_root="relative/share")


if __name__ == "__main__":
    unittest.main()

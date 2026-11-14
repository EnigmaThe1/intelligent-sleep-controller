import os
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.autostart import (
    DESKTOP_NAME, AutostartError, desktop_contents,
    inspect_autostart, modify_autostart,
)


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "config"
        self.root.mkdir(mode=0o700)
        self.target = self.root / "autostart" / DESKTOP_NAME

    def test_disabled_by_default_no_files_created(self):
        self.assertEqual(inspect_autostart(config_root=self.root).state, "disabled")
        self.assertFalse(self.target.parent.exists())

    def test_can_explicitly_enable_and_disable(self):
        outcome = modify_autostart("enable", config_root=self.root)
        self.assertEqual(outcome.state, "enabled")
        self.assertTrue(outcome.changed)
        self.assertEqual(self.target.read_bytes(), desktop_contents())
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.target.parent.stat().st_mode), 0o700)
        self.assertEqual(inspect_autostart(config_root=self.root).state, "enabled")
        stopped = modify_autostart("disable", config_root=self.root)
        self.assertEqual(stopped.state, "disabled")
        self.assertTrue(stopped.changed)
        self.assertFalse(self.target.exists())

    def test_autostart_can_be_disabled_after_interpreter_move(self):
        self.target.parent.mkdir(mode=0o700)
        old = desktop_contents(
            interpreter="/opt/old-isc/bin/python3",
            source_root="/srv/old-isc/src",
        )
        self.target.write_bytes(old)
        self.target.chmod(0o600)
        self.assertEqual(
            inspect_autostart(config_root=self.root).state, "enabled",
        )
        with self.assertRaisesRegex(
            AutostartError, "autostart_path_migration_requires_disable_first",
        ):
            modify_autostart("enable", config_root=self.root)
        self.assertEqual(self.target.read_bytes(), old)
        disabled = modify_autostart("disable", config_root=self.root)
        self.assertEqual(disabled.state, "disabled")
        self.assertFalse(self.target.exists())

    def test_modified_legacy_autostart_not_mistaken_for_isc(self):
        self.target.parent.mkdir(mode=0o700)
        content = desktop_contents(
            interpreter="/opt/old-isc/bin/python3",
            source_root="/srv/old-isc/src",
        ).replace(b"Terminal=false", b"Terminal=true")
        self.target.write_bytes(content)
        self.target.chmod(0o600)
        self.assertEqual(
            inspect_autostart(config_root=self.root).state, "unmanaged",
        )
        with self.assertRaisesRegex(
            AutostartError, "existing_startup_entry_not_owned",
        ):
            modify_autostart("disable", config_root=self.root)
        self.assertEqual(self.target.read_bytes(), content)

    def test_repeated_enable_and_disable_are_noop(self):
        modify_autostart("enable", config_root=self.root)
        inode = self.target.stat().st_ino
        self.assertFalse(modify_autostart("enable", config_root=self.root).changed)
        self.assertEqual(self.target.stat().st_ino, inode)
        modify_autostart("disable", config_root=self.root)
        self.assertFalse(modify_autostart("disable", config_root=self.root).changed)

    def test_never_overwrites_unknown_desktop_entry(self):
        self.target.parent.mkdir(mode=0o700)
        self.target.write_text("[Desktop Entry]\nName=Something else\n")
        self.target.chmod(0o600)
        before = self.target.read_bytes()
        self.assertEqual(inspect_autostart(config_root=self.root).state, "unmanaged")
        with self.assertRaisesRegex(AutostartError, "not_owned"):
            modify_autostart("enable", config_root=self.root)
        with self.assertRaisesRegex(AutostartError, "not_owned"):
            modify_autostart("disable", config_root=self.root)
        self.assertEqual(self.target.read_bytes(), before)

    def test_rejects_symlink_file_without_touching_original(self):
        other = self.root / "other"
        other.write_text("PRIVATE")
        self.target.parent.mkdir(mode=0o700)
        self.target.symlink_to(other)
        with self.assertRaises(AutostartError):
            modify_autostart("enable", config_root=self.root)
        self.assertEqual(other.read_text(), "PRIVATE")

    def test_rejects_insecure_autostart_parent_directory(self):
        self.target.parent.mkdir(mode=0o775)
        self.target.parent.chmod(0o775)
        with self.assertRaises(AutostartError):
            modify_autostart("enable", config_root=self.root)

    def test_rejects_insecure_root_directory(self):
        self.root.chmod(0o777)
        with self.assertRaises(AutostartError):
            modify_autostart("enable", config_root=self.root)

    def test_rejects_unsafe_existing_desktop_permissions(self):
        self.target.parent.mkdir(mode=0o700)
        self.target.write_bytes(desktop_contents())
        self.target.chmod(0o664)
        with self.assertRaises(AutostartError):
            inspect_autostart(config_root=self.root)

    def test_unknown_action_never_writes(self):
        with self.assertRaises(AutostartError):
            modify_autostart("upgrade", config_root=self.root)
        self.assertFalse(self.target.parent.exists())

    def test_generated_exec_is_direct_and_no_shell(self):
        content = desktop_contents(
            interpreter="/usr/bin/python3",
            source_root="/srv/playground_dev/intelligent-sleep-controller/src",
        ).decode()
        self.assertIn(
            "Exec=/usr/bin/env PYTHONDONTWRITEBYTECODE=1 "
            "PYTHONPATH=/srv/playground_dev/intelligent-sleep-controller/src "
            "/usr/bin/python3 -B -m isc tray", content,
        )
        self.assertIn("Terminal=false", content)
        self.assertIn("NoDisplay=true", content)
        self.assertNotIn(" sh -c ", content)
        self.assertNotIn("sudo", content)
        self.assertNotIn("systemctl", content)

    def test_rejects_injection_or_space_path(self):
        with self.assertRaises(AutostartError):
            desktop_contents(interpreter="/bin/python3;evil")
        with self.assertRaises(AutostartError):
            desktop_contents(source_root="/tmp/path with spaces")

    def test_never_starts_any_process_or_power_manager(self):
        with patch("subprocess.run", side_effect=AssertionError("process started")):
            with patch("subprocess.Popen", side_effect=AssertionError("process started")):
                modify_autostart("enable", config_root=self.root)
                modify_autostart("disable", config_root=self.root)

    def test_missing_parent_root_fails_safely(self):
        missing = self.root / "missing"
        with self.assertRaises(AutostartError):
            modify_autostart("enable", config_root=missing)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()

"""GNOME idle-timeout coordination uses a private reversible record.

No actual GNOME settings, services or host power behaviour are changed.
"""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.gnome_policy import (
    GnomePolicyError, change_gnome_policy, inspect_gnome_policy,
    _BACKUP, _read_timeout, _write_timeout,
)
from isc.cli import main


class GnomePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "config"
        self.project = self.root / "intelligent-sleep-controller"
        # Create each level with explicit private permissions. A recursive
        # mkdir(parents=True, mode=0o700) creates *intermediate* directories
        # using 0o777 & umask: on Ubuntu umask=0002 this produced 0o775,
        # which our correctly fail-closed settings validator must reject.
        self.root.mkdir(mode=0o700)
        self.project.mkdir(mode=0o700)
        self.values = {
            "sleep-inactive-ac-timeout": 1800,
            "sleep-inactive-battery-timeout": 900,
        }
        self.original = self.values.copy()
        self.read_patch = patch(
            "isc.gnome_policy._read_timeout",
            side_effect=lambda key: self.values[key],
        )
        self.write_patch = patch(
            "isc.gnome_policy._write_timeout",
            side_effect=self._write,
        )
        self.read_patch.start()
        self.write_patch.start()
        self.addCleanup(self.read_patch.stop)
        self.addCleanup(self.write_patch.stop)

    def _write(self, key, value):
        self.values[key] = value
        return True

    @property
    def backup(self):
        return self.project / _BACKUP

    def test_preview_does_not_create_or_modify_gnome_settings(self):
        with patch("isc.gnome_policy._write_timeout",
                   side_effect=AssertionError("must not write")):
            state = inspect_gnome_policy(config_root=self.root)
        self.assertTrue(state.conflict)
        self.assertFalse(state.managed)
        self.assertEqual(state.ac_timeout, 1800)
        self.assertEqual(state.battery_timeout, 900)
        self.assertFalse(self.backup.exists())

    def test_fixture_has_private_permissions_even_with_permissive_umask(self):
        # Should be invariant for normal umasks such as 0022 and 0002.
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.project.stat().st_mode & 0o777, 0o700)

    def test_group_writable_config_root_still_fails_closed(self):
        # Do not relax the real security boundary to accommodate a fixture.
        self.root.chmod(0o775)
        result = inspect_gnome_policy(config_root=self.root)
        self.assertEqual(result.reason, "backup_unverified")
        self.assertTrue(result.conflict)
        with self.assertRaises(GnomePolicyError):
            change_gnome_policy(
                "disable", confirmed=True, config_root=self.root,
            )
        self.assertEqual(self.values, self.original)
        self.assertFalse(self.backup.exists())

    def test_preview_on_fresh_install_does_not_make_directory(self):
        fresh = self.root / "new-root"
        with patch("isc.gnome_policy._write_timeout",
                   side_effect=AssertionError("write")):
            report = inspect_gnome_policy(config_root=fresh)
        self.assertFalse(report.managed)
        self.assertEqual(report.reason, "gnome_idle_enabled")
        self.assertFalse(fresh.exists())

    def test_missing_confirmation_blocks_before_backup_or_writes(self):
        for operation, confirmed in (
            ("disable", False), ("restore", False), ("other", True),
        ):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(
                    GnomePolicyError, "explicit_confirmation_required"
                ):
                    change_gnome_policy(
                        operation, confirmed=confirmed, config_root=self.root,
                    )
        self.assertEqual(self.values, self.original)
        self.assertFalse(self.backup.exists())

    def test_disable_records_original_before_changing_values(self):
        changes = []
        def write(key, value):
            self.assertTrue(self.backup.exists())
            changes.append(key)
            return self._write(key, value)
        with patch("isc.gnome_policy._write_timeout", side_effect=write):
            result = change_gnome_policy(
                "disable", confirmed=True, config_root=self.root,
            )
        self.assertEqual(len(changes), 2)
        self.assertEqual(list(self.values.values()), [0, 0])
        self.assertTrue(result.managed)
        self.assertFalse(result.conflict)
        self.assertEqual(result.reason, "gnome_idle_disabled")
        self.assertEqual(json.loads(self.backup.read_text()), {
            "version": 1, "ac": 1800, "battery": 900,
        })
        self.assertEqual(self.backup.stat().st_mode & 0o777, 0o600)

    def test_restore_returns_both_exact_timeout_values(self):
        change_gnome_policy("disable", confirmed=True, config_root=self.root)
        result = change_gnome_policy(
            "restore", confirmed=True, config_root=self.root,
        )
        self.assertEqual(self.values, self.original)
        self.assertFalse(self.backup.exists())
        self.assertFalse(result.managed)
        self.assertTrue(result.conflict)

    def test_disable_twice_does_not_overwrite_backup(self):
        change_gnome_policy("disable", confirmed=True, config_root=self.root)
        original = self.backup.read_bytes()
        inode = self.backup.stat().st_ino
        change_gnome_policy("disable", confirmed=True, config_root=self.root)
        self.assertEqual(self.backup.read_bytes(), original)
        self.assertEqual(self.backup.stat().st_ino, inode)

    def test_previously_disabled_gnome_is_left_unowned(self):
        self.values = {k: 0 for k in self.values}
        outcome = change_gnome_policy(
            "disable", confirmed=True, config_root=self.root,
        )
        self.assertFalse(self.backup.exists())
        self.assertFalse(outcome.managed)

    def test_restore_without_isc_backup_refuses(self):
        with self.assertRaisesRegex(GnomePolicyError, "gnome_backup_not_owned"):
            change_gnome_policy(
                "restore", confirmed=True, config_root=self.root,
            )
        self.assertEqual(self.values, self.original)

    def test_external_power_policy_change_prevents_restore(self):
        change_gnome_policy("disable", confirmed=True, config_root=self.root)
        self.values["sleep-inactive-ac-timeout"] = 2300
        with self.assertRaisesRegex(
            GnomePolicyError, "gnome_policy_changed_externally"
        ):
            change_gnome_policy(
                "restore", confirmed=True, config_root=self.root,
            )
        self.assertEqual(self.values["sleep-inactive-ac-timeout"], 2300)
        self.assertTrue(self.backup.exists())

    def test_partial_disable_failure_is_recoverable(self):
        calls = [0]
        def flaky(key, value):
            calls[0] += 1
            return False if calls[0] == 2 else self._write(key, value)
        with patch("isc.gnome_policy._write_timeout", side_effect=flaky):
            with self.assertRaisesRegex(
                GnomePolicyError, "gnome_policy_disable_incomplete"
            ):
                change_gnome_policy(
                    "disable", confirmed=True, config_root=self.root,
                )
        self.assertTrue(self.backup.exists())
        self.assertEqual(list(self.values.values()), [0, 900])
        change_gnome_policy("restore", confirmed=True, config_root=self.root)
        self.assertEqual(self.values, self.original)
        self.assertFalse(self.backup.exists())

    def test_partial_restore_failure_is_retryable(self):
        change_gnome_policy("disable", confirmed=True, config_root=self.root)
        calls = [0]
        def flaky(key, value):
            calls[0] += 1
            return False if calls[0] == 2 else self._write(key, value)
        with patch("isc.gnome_policy._write_timeout", side_effect=flaky):
            with self.assertRaisesRegex(
                GnomePolicyError, "gnome_policy_restore_incomplete"
            ):
                change_gnome_policy(
                    "restore", confirmed=True, config_root=self.root,
                )
        self.assertTrue(self.backup.exists())
        self.assertEqual(list(self.values.values()), [1800, 0])
        change_gnome_policy("restore", confirmed=True, config_root=self.root)
        self.assertEqual(self.values, self.original)
        self.assertFalse(self.backup.exists())

    def test_corrupt_backup_is_not_overwritten(self):
        self.backup.write_text("unsafe")
        with self.assertRaises(GnomePolicyError):
            change_gnome_policy("disable", confirmed=True, config_root=self.root)
        self.assertEqual(self.backup.read_text(), "unsafe")
        self.assertEqual(self.values, self.original)

    def test_symlink_backup_is_not_followed(self):
        other = self.project / "original"
        other.write_text("sensitive")
        self.backup.symlink_to(other)
        with self.assertRaises(GnomePolicyError):
            change_gnome_policy("disable", confirmed=True, config_root=self.root)
        self.assertEqual(other.read_text(), "sensitive")
        self.assertEqual(self.values, self.original)

    def test_world_readable_backup_is_rejected(self):
        change_gnome_policy("disable", confirmed=True, config_root=self.root)
        self.backup.chmod(0o644)
        with self.assertRaises(GnomePolicyError):
            change_gnome_policy("restore", confirmed=True, config_root=self.root)
        self.assertTrue(self.backup.exists())

    def test_unverified_power_source_never_writes(self):
        with patch("isc.gnome_policy._read_timeout", return_value=None), \
             patch("isc.gnome_policy._write_timeout",
                   side_effect=AssertionError("write")):
            with self.assertRaises(GnomePolicyError):
                change_gnome_policy(
                    "disable", confirmed=True, config_root=self.root,
                )
        self.assertFalse(self.backup.exists())

    def test_invalid_values_are_rejected(self):
        with patch("isc.gnome_policy._read_timeout", return_value=-1):
            with self.assertRaises(GnomePolicyError):
                change_gnome_policy(
                    "disable", confirmed=True, config_root=self.root,
                )
        self.assertFalse(self.backup.exists())

    def test_preview_unknown_value_does_not_claim_disabled(self):
        with patch("isc.gnome_policy._read_timeout", return_value=None):
            result = inspect_gnome_policy(config_root=self.root)
        self.assertTrue(result.conflict)
        self.assertEqual(result.reason, "gnome_policy_unverified")

    def test_preview_cli_only_reads_without_confirmation(self):
        with patch("isc.cli.inspect_gnome_policy", return_value=
                   inspect_gnome_policy(config_root=self.root)), \
             patch("isc.cli.change_gnome_policy",
                   side_effect=AssertionError("change")):
            self.assertEqual(main(["gnome-policy"]), 0)

    def test_cli_requires_separate_explicit_confirmation(self):
        with patch("isc.cli.change_gnome_policy",
                   side_effect=AssertionError("not confirmed")):
            self.assertEqual(
                main(["gnome-policy", "--power-policy", "disable"]), 2,
            )

    def test_cli_confirmed_action_only_calls_native_controller(self):
        from isc.gnome_policy import GnomePolicyStatus
        expected = GnomePolicyStatus(0, 0, True, False, "gnome_idle_disabled")
        with patch("isc.cli.change_gnome_policy", return_value=expected) as action:
            self.assertEqual(main([
                "gnome-policy", "--power-policy", "disable", "--confirm",
            ]), 0)
        action.assert_called_once_with("disable", confirmed=True)

    def test_os_get_parser_strict_uint32(self):
        samples = (
            ("uint32 0\n", 0), ("1800\n", 1800),
            ("uint32 4294967295\n", 4294967295),
            ("uint32 4294967296", None), ("-1", None),
            ("'suspend'", None), ("nonsense", None),
        )
        for raw, expected in samples:
            with self.subTest(raw=raw):
                done = Mock(returncode=0, stdout=raw)
                with patch("isc.gnome_policy.subprocess.run", return_value=done):
                    self.assertEqual(
                        _read_timeout("sleep-inactive-ac-timeout"), expected
                    )

    def test_write_calls_fixed_gsettings_schema_key_without_shell(self):
        with patch("isc.gnome_policy.subprocess.run",
                   return_value=Mock(returncode=0)) as run:
            self.assertTrue(_write_timeout("sleep-inactive-ac-timeout", 0))
        run.assert_called_once_with(
            ("gsettings", "set", "org.gnome.settings-daemon.plugins.power",
             "sleep-inactive-ac-timeout", "0"),
            capture_output=True, text=True, check=False, timeout=3,
        )

    def test_no_unrelated_schema_or_key_is_writable(self):
        with patch("isc.gnome_policy.subprocess.run",
                   side_effect=AssertionError("power action")):
            self.assertFalse(_write_timeout("other.key", 0))
            self.assertFalse(_write_timeout("sleep-inactive-ac-timeout", -1))
            self.assertFalse(_write_timeout("sleep-inactive-ac-timeout", 2**32))


if __name__ == "__main__":
    unittest.main()

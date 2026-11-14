"""Milestone 3: one operator-started workload can hold a native inhibitor.

All tests mock os.execv. No child process or power operation is launched.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.protected_work import protected_command, run_protected_work
from isc.cli import main


class ProtectedWorkTests(unittest.TestCase):
    def test_minimal_safe_invocation_uses_systemd_native_inhibitor(self):
        command = protected_command(("/usr/bin/true",))
        self.assertEqual(command, (
            "/usr/bin/systemd-inhibit",
            "--what=idle:sleep",
            "--mode=block",
            "--who=Intelligent Sleep Controller",
            "--why=Protected foreground work",
            "--", "/usr/bin/true",
        ))

    def test_arguments_are_forwarded_without_shell_interpretation(self):
        value = "$(echo not-executed)"
        command = protected_command(("python3", "-c", value))
        self.assertEqual(command[-3:], ("python3", "-c", value))
        self.assertNotIn("sh", command[:6])

    def test_empty_command_rejected(self):
        for value in ((), [], ("" ,), ("python", ""), None,
                      "python", b"python", ("python", None),
                      ("python", 8), ("python", "x\x00y")):
            with self.subTest(value=value):
                self.assertIsNone(protected_command(value))

    def test_unsupported_argument_sizes_rejected(self):
        self.assertIsNone(protected_command(tuple("a" for _ in range(257))))
        self.assertIsNone(protected_command(("a" * 65537,)))
        self.assertIsNone(protected_command(("\ud800",)))

    def test_exec_replaces_current_process_without_new_shell(self):
        sentinel = RuntimeError("caught synthetic exec")
        with patch("isc.protected_work.os.execv", side_effect=sentinel) as executor:
            with self.assertRaises(RuntimeError):
                run_protected_work(("/usr/bin/true",))
        executor.assert_called_once()
        self.assertEqual(executor.call_args.args[0], "/usr/bin/systemd-inhibit")
        self.assertEqual(executor.call_args.args[1][-1], "/usr/bin/true")

    def test_exec_failure_returns_generic_code(self):
        with patch("isc.protected_work.os.execv",
                   side_effect=OSError("private executable path")):
            self.assertEqual(run_protected_work(("/usr/bin/true",)), 2)

    def test_missing_argument_never_executes(self):
        with patch("isc.protected_work.os.execv") as executor:
            self.assertEqual(run_protected_work(()), 2)
        executor.assert_not_called()

    def test_cli_forwards_exact_command_and_flags(self):
        with patch("isc.cli.run_protected_work", return_value=0) as runner:
            self.assertEqual(
                main(["protect", "--", "python3", "-m", "compileall", "src"]),
                0,
            )
        runner.assert_called_once_with(["python3", "-m", "compileall", "src"])

    def test_cli_rejects_implicit_execution_without_separator(self):
        with patch("isc.cli.run_protected_work") as runner:
            self.assertEqual(main(["protect", "python3", "-V"]), 2)
            self.assertEqual(main(["protect", "--"]), 2)
        runner.assert_not_called()

    def test_cli_refuses_to_run_when_inhibitor_unavailable(self):
        with patch("isc.cli.run_protected_work", return_value=2) as runner:
            self.assertEqual(main(["protect", "--", "/usr/bin/true"]), 2)
        runner.assert_called_once_with(["/usr/bin/true"])

    def test_explicit_mode_independent_of_protect(self):
        with patch("isc.cli.run_protected_work", return_value=0) as runner:
            with patch("isc.settings.set_mode",
                       side_effect=AssertionError("mode should not change")):
                self.assertEqual(main(["protect", "--", "make", "-j4"]), 0)
        runner.assert_called_once()

    def test_no_isc_command_strings_written_to_logs(self):
        with patch("isc.protected_work.os.execv",
                   side_effect=OSError("private credentials in argv")):
            self.assertEqual(run_protected_work(("echo", "secret")), 2)


if __name__ == "__main__":
    unittest.main()

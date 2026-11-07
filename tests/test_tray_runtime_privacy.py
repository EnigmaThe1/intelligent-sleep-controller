import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from isc.tray_runtime import TrayMonitorProcess


class TrayRuntimePrivacyTests(unittest.TestCase):
    @patch("isc.tray_runtime.discover_monitor_socket", return_value=None)
    @patch("isc.tray_runtime.subprocess.Popen")
    @patch.dict(os.environ, {
        "PRIVATE_AGENT_TOKEN_123": "DO_NOT_PASS_THIS",
        "XDG_RUNTIME_DIR": "/tmp/private-isc-runtime",
    })
    def test_child_receives_minimum_required_environment(self, spawn, probe):
        child = Mock()
        child.poll.return_value = 0
        spawn.return_value = child
        controller = TrayMonitorProcess()
        self.assertEqual(
            controller.reconcile(mode="monitor", config_valid=True),
            "starting",
        )
        env = spawn.call_args.kwargs["env"]
        self.assertNotIn("PRIVATE_AGENT_TOKEN_123", env)
        self.assertNotIn("DO_NOT_PASS_THIS", repr(env))
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/tmp/private-isc-runtime")
        self.assertIn("src", env["PYTHONPATH"])
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        controller.close()


if __name__ == "__main__":
    unittest.main()

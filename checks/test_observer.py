"""Regression for the Windows transport failure observed during the overnight route."""

import subprocess
import unittest
from unittest.mock import Mock, patch

from locked_route import run_device_command, route_delay_bound


class TransportRecovery(unittest.TestCase):
    def test_retries_failure_before_contact_then_reads_actual_response(self):
        start = subprocess.CalledProcessError(
            1, ["adb"], stderr="cannot connect to daemon (10055)"
        )
        command = ["adb", "shell", "cmd", "deviceidle", "get", "deep"]
        with patch(
            "locked_route.subprocess.run", side_effect=[start, Mock(stdout="IDLE\n")]
        ) as run:
            with patch("locked_route.time.sleep") as pause:
                events = []
                self.assertEqual(run_device_command(command, events.append), "IDLE")
        self.assertEqual(run.call_count, 2)
        pause.assert_called_once()
        self.assertEqual(len(events), 1)

    def test_does_not_retry_device_or_permission_failures(self):
        denied = subprocess.CalledProcessError(1, ["adb"], stderr="Permission denied")
        with patch("locked_route.subprocess.run", side_effect=denied) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                run_device_command(["adb", "shell", "cmd"], self.fail)
        self.assertEqual(run.call_count, 1)


class RouteTiming(unittest.TestCase):
    def test_identical_signal_from_current_window_binds_real_elapsed_time(self):
        issued = {64: {"elapsed": 20.0, "wall": 1000.0}}
        self.assertEqual(route_delay_bound(0.64, 1012.3, 37.0, issued), 17.0)
        self.assertEqual(route_delay_bound(0.63, 1012.3, 37.0, issued), 17.0)
        self.assertIsNone(route_delay_bound(0.66, 1012.3, 37.0, issued))
        self.assertIsNone(route_delay_bound(0.64, 800.0, 37.0, issued))
        self.assertIsNone(route_delay_bound(0.0, 1012.3, 37.0, issued))
        self.assertGreater(route_delay_bound(0.64, 1012.3, 85, issued), 60)
        self.assertIsNone(route_delay_bound(0.64, 1012.3, 19.0, issued))

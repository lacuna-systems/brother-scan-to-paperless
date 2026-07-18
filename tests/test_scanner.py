import os
import unittest
from types import SimpleNamespace
from unittest import mock

import brother_scan_to_paperless.brother as brother_module
import brother_scan_to_paperless.config as config_module


class DependencyTests(unittest.TestCase):
    def test_dependency_check_uses_selected_driver(self):
        installed = {"scanimage", "snmpset", "brsaneconfig5"}

        with mock.patch.object(
            config_module.shutil,
            "which",
            side_effect=lambda command: f"/usr/bin/{command}"
            if command in installed
            else None,
        ):
            missing = config_module.check_dependencies({"driver_version": "5"})

        self.assertEqual(missing, [])

    def test_dependency_check_reports_every_missing_command(self):
        with mock.patch.object(config_module.shutil, "which", return_value=None):
            missing = config_module.check_dependencies({"driver_version": "4"})

        self.assertEqual(missing, ["scanimage", "snmpset", "brsaneconfig4"])


class DetectionTests(unittest.TestCase):
    def test_detect_host_ip_uses_socket_route(self):
        route_socket = mock.Mock()
        route_socket.getsockname.return_value = ("192.0.2.20", 12345)

        with mock.patch.object(
            brother_module.socket, "socket", return_value=route_socket
        ):
            ip = brother_module.detect_host_ip()

        self.assertEqual(ip, "192.0.2.20")
        route_socket.connect.assert_called_once_with(("8.8.8.8", 80))
        route_socket.close.assert_called_once_with()

    def test_detect_host_ip_returns_none_on_socket_error(self):
        with mock.patch.object(
            brother_module.socket, "socket", side_effect=OSError("no route")
        ):
            self.assertIsNone(brother_module.detect_host_ip())

    @mock.patch.dict(os.environ, {"BRSCAN_VERSION": "5"}, clear=True)
    def test_explicit_driver_version_wins_without_detection(self):
        with mock.patch.object(brother_module.shutil, "which") as which:
            self.assertEqual(brother_module.default_driver_version(), "5")

        which.assert_not_called()

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_default_driver_detects_brscan5(self):
        with mock.patch.object(
            brother_module.shutil,
            "which",
            side_effect=lambda command: "/usr/bin/brsaneconfig5"
            if command == "brsaneconfig5"
            else None,
        ):
            self.assertEqual(brother_module.default_driver_version(), "5")

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_default_driver_falls_back_to_brscan4(self):
        with mock.patch.object(brother_module.shutil, "which", return_value=None):
            self.assertEqual(brother_module.default_driver_version(), "4")

    def test_detect_scanner_device_queries_selected_driver(self):
        result = SimpleNamespace(stdout="  * BROTHER network scanner\n")

        with mock.patch.object(
            brother_module.subprocess, "run", return_value=result
        ) as run:
            device = brother_module.detect_scanner_device("5")

        self.assertEqual(device, "brother5:net1;dev0")
        run.assert_called_once_with(
            ["brsaneconfig5", "-q"],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_detect_scanner_device_returns_none_without_match(self):
        result = SimpleNamespace(stdout="scanner without active marker\n")

        with mock.patch.object(brother_module.subprocess, "run", return_value=result):
            self.assertIsNone(brother_module.detect_scanner_device("4"))

    def test_detect_scanner_device_returns_none_when_query_fails(self):
        with mock.patch.object(
            brother_module.subprocess, "run", side_effect=OSError("missing command")
        ):
            self.assertIsNone(brother_module.detect_scanner_device("4"))


class RegistrationTests(unittest.TestCase):
    def test_register_profiles_builds_all_four_snmp_profiles(self):
        result = SimpleNamespace(returncode=0, stderr="")

        with (
            mock.patch.object(
                brother_module.subprocess, "run", return_value=result
            ) as run,
            mock.patch.object(brother_module, "log"),
        ):
            success = brother_module.register_profiles(
                "192.0.2.10", "192.0.2.20", 54925, "Paperless", None
            )

        self.assertTrue(success)
        command = run.call_args.args[0]
        self.assertEqual(
            command[:5], ["snmpset", "-v1", "-c", "internal", "192.0.2.10"]
        )
        profiles = command[7::3]
        self.assertEqual(len(profiles), 4)
        for function, app_number, profile in zip(
            ("IMAGE", "EMAIL", "OCR", "FILE"), (1, 2, 3, 5), profiles
        ):
            self.assertIn('USER="Paperless"', profile)
            self.assertIn(f"FUNC={function}", profile)
            self.assertIn("HOST=192.0.2.20:54925", profile)
            self.assertIn(f"APPNUM={app_number}", profile)
        run.assert_called_once_with(command, capture_output=True, text=True, timeout=10)

    def test_register_profiles_reports_command_failure(self):
        result = SimpleNamespace(returncode=1, stderr="SNMP error\n")

        with (
            mock.patch.object(brother_module.subprocess, "run", return_value=result),
            mock.patch.object(brother_module, "log") as log,
        ):
            success = brother_module.register_profiles(
                "192.0.2.10", "192.0.2.20", 54925, "Paperless", None
            )

        self.assertFalse(success)
        log.assert_called_once_with("SNMP registration failed: SNMP error", None)

    def test_register_profiles_reports_execution_error(self):
        with (
            mock.patch.object(
                brother_module.subprocess, "run", side_effect=OSError("not installed")
            ),
            mock.patch.object(brother_module, "log") as log,
        ):
            success = brother_module.register_profiles(
                "192.0.2.10", "192.0.2.20", 54925, "Paperless", None
            )

        self.assertFalse(success)
        log.assert_called_once_with("SNMP registration error: not installed", None)


class ScanEventTests(unittest.TestCase):
    def test_scan_events_must_come_from_configured_printer(self):
        message = "TYPE=BR;BUTTON=SCAN;FUNC=FILE;"

        self.assertTrue(
            brother_module.is_scan_event(message, "192.0.2.10", "192.0.2.10")
        )
        self.assertFalse(
            brother_module.is_scan_event(message, "192.0.2.11", "192.0.2.10")
        )
        self.assertFalse(
            brother_module.is_scan_event("BUTTON=OTHER", "192.0.2.10", "192.0.2.10")
        )

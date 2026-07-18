import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import brother_scan_to_paperless.brother as brother_module
import brother_scan_to_paperless.config as config_module


class DependencyTests(unittest.TestCase):
    def test_dependency_check_uses_selected_driver(self):
        installed = {
            "scanimage",
            "snmpget",
            "snmpset",
            "brsaneconfig5",
            "tiffcp",
        }

        with mock.patch.object(
            config_module.shutil,
            "which",
            side_effect=lambda command: f"/usr/bin/{command}"
            if command in installed
            else None,
        ) as which:
            missing = config_module.check_dependencies({"driver_version": "5"})

        self.assertEqual(missing, [])
        self.assertEqual(
            [call.args[0] for call in which.call_args_list],
            [
                "scanimage",
                "snmpget",
                "snmpset",
                "brsaneconfig5",
                "tiffcp",
            ],
        )

    def test_dependency_check_reports_every_missing_command(self):
        with mock.patch.object(config_module.shutil, "which", return_value=None):
            missing = config_module.check_dependencies({"driver_version": "4"})

        self.assertEqual(
            missing,
            [
                "scanimage",
                "snmpget",
                "snmpset",
                "brsaneconfig4",
                "tiffcp",
            ],
        )

    def test_pdf_dependency_check_requires_tiff_merge_and_conversion(self):
        with mock.patch.object(config_module.shutil, "which", return_value=None):
            missing = config_module.check_dependencies(
                {"driver_version": "5", "format": "pdf"}
            )

        self.assertEqual(missing[-2:], ["tiffcp", "tiff2pdf"])


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


class SaneConfigurationTests(unittest.TestCase):
    def test_configure_sane_scanner_replaces_and_verifies_registration(self):
        query = SimpleNamespace(
            stdout=(
                "  0 OTHER scanner model\n"
                "  1 DESK[1] DCP-L3560CDW 192.0.2.10\n"
            )
        )

        with (
            mock.patch.object(
                brother_module.shutil,
                "which",
                return_value="/usr/bin/brsaneconfig5",
            ) as which,
            mock.patch.object(
                brother_module.subprocess,
                "run",
                side_effect=[SimpleNamespace(), SimpleNamespace(), query],
            ) as run,
            mock.patch("builtins.print") as output,
        ):
            verified = brother_module.configure_sane_scanner(
                "5", "DESK[1]", "DCP-L3560CDW", "192.0.2.10"
            )

        self.assertTrue(verified)
        which.assert_called_once_with("brsaneconfig5")
        self.assertEqual(
            run.call_args_list,
            [
                mock.call(
                    ["brsaneconfig5", "-r", "DESK[1]"],
                    stdout=brother_module.subprocess.DEVNULL,
                    check=True,
                ),
                mock.call(
                    [
                        "brsaneconfig5",
                        "-a",
                        "name=DESK[1]",
                        "model=DCP-L3560CDW",
                        "ip=192.0.2.10",
                    ],
                    check=True,
                ),
                mock.call(
                    ["brsaneconfig5", "-q"],
                    stdout=brother_module.subprocess.PIPE,
                    text=True,
                    check=True,
                ),
            ],
        )
        output.assert_has_calls(
            [
                mock.call(
                    "Debug:   0 OTHER scanner model\n"
                    "  1 DESK[1] DCP-L3560CDW 192.0.2.10, "
                    "scanner: DESK[1]"
                ),
                mock.call(
                    "Reg line:   1 DESK[1] DCP-L3560CDW 192.0.2.10"
                ),
                mock.call(
                    "Registered DCP-L3560CDW at 192.0.2.10 with "
                    "brsaneconfig5"
                ),
            ]
        )

    def test_configure_sane_scanner_warns_for_unsupported_model(self):
        query = SimpleNamespace(stdout="* * BROTHER unsupported model\n")

        with (
            mock.patch.object(
                brother_module.shutil,
                "which",
                return_value="/usr/bin/brsaneconfig4",
            ),
            mock.patch.object(
                brother_module.subprocess,
                "run",
                side_effect=[SimpleNamespace(), SimpleNamespace(), query],
            ),
            mock.patch("builtins.print") as output,
        ):
            verified = brother_module.configure_sane_scanner(
                "4", "BROTHER", "INVALID", "192.0.2.10"
            )

        self.assertFalse(verified)
        output.assert_any_call(
            "Warning: INVALID registration could not be verified with brscan4"
        )

    def test_configure_sane_scanner_warns_for_missing_query_match(self):
        query = SimpleNamespace(stdout="  0 OTHER scanner model\n")

        with (
            mock.patch.object(
                brother_module.shutil,
                "which",
                return_value="/usr/bin/brsaneconfig4",
            ),
            mock.patch.object(
                brother_module.subprocess,
                "run",
                side_effect=[SimpleNamespace(), SimpleNamespace(), query],
            ),
            mock.patch("builtins.print") as output,
        ):
            verified = brother_module.configure_sane_scanner(
                "4", "BROTHER", "DCP-L2540DW", "192.0.2.10"
            )

        self.assertFalse(verified)
        output.assert_any_call(
            "Warning: DCP-L2540DW registration could not be verified "
            "with brscan4"
        )

    def test_configure_sane_scanner_requires_configuration_tool(self):
        with (
            mock.patch.object(brother_module.shutil, "which", return_value=None),
            mock.patch.object(brother_module.subprocess, "run") as run,
            self.assertRaisesRegex(
                brother_module.ScannerConfigurationError,
                "Missing Brother configuration tool: brsaneconfig5",
            ),
        ):
            brother_module.configure_sane_scanner(
                "5", "BROTHER", "DCP-L3560CDW", "192.0.2.10"
            )

        run.assert_not_called()

    def test_configure_sane_scanner_reports_command_failures(self):
        failure = brother_module.subprocess.CalledProcessError(
            1, ["brsaneconfig4"]
        )

        for failure_index in range(3):
            with self.subTest(failure_index=failure_index):
                outcomes = [SimpleNamespace(), SimpleNamespace(), SimpleNamespace()]
                outcomes[failure_index] = failure
                with (
                    mock.patch.object(
                        brother_module.shutil,
                        "which",
                        return_value="/usr/bin/brsaneconfig4",
                    ),
                    mock.patch.object(
                        brother_module.subprocess,
                        "run",
                        side_effect=outcomes,
                    ),
                    self.assertRaisesRegex(
                        brother_module.ScannerConfigurationError,
                        "Could not configure scanner with brsaneconfig4",
                    ),
                ):
                    brother_module.configure_sane_scanner(
                        "4", "BROTHER", "DCP-L2540DW", "192.0.2.10"
                    )


class RegistrationTests(unittest.TestCase):
    def test_phoenix_capability_probe_parses_supported_marker(self):
        result = SimpleNamespace(
            returncode=0,
            stdout='"01 01 01 02 01 00 0B 01 01 0C 01 00 FF "\n',
        )

        with mock.patch.object(
            brother_module.subprocess,
            "run",
            return_value=result,
        ) as run:
            supported = brother_module.supports_phoenix_registration(
                "192.0.2.10"
            )

        self.assertTrue(supported)
        run.assert_called_once_with(
            [
                "snmpget",
                "-v1",
                "-c",
                "public",
                "-Oqv",
                "-t",
                "2",
                "-r",
                "0",
                "192.0.2.10",
                brother_module.PHOENIX_CAPABILITY_OID,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_phoenix_capability_probe_rejects_absent_or_malformed_marker(self):
        outputs = (
            '"01 01 01 0B 01 00 FF "\n',
            '"01 01 01 0A 01 01 FF "\n',
            '"0B 01"\n',
            '"0B 01 01"\n',
            "not hexadecimal\n",
        )

        for stdout in outputs:
            with (
                self.subTest(stdout=stdout),
                mock.patch.object(
                    brother_module.subprocess,
                    "run",
                    return_value=SimpleNamespace(
                        returncode=0,
                        stdout=stdout,
                    ),
                ),
            ):
                self.assertFalse(
                    brother_module.supports_phoenix_registration(
                        "192.0.2.10"
                    )
                )

    def test_phoenix_capability_probe_handles_command_failure(self):
        outcomes = (
            SimpleNamespace(returncode=1, stdout=""),
            OSError("snmpget is unavailable"),
        )

        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                if isinstance(outcome, Exception):
                    patch = mock.patch.object(
                        brother_module.subprocess,
                        "run",
                        side_effect=outcome,
                    )
                else:
                    patch = mock.patch.object(
                        brother_module.subprocess,
                        "run",
                        return_value=outcome,
                    )
                with patch:
                    self.assertFalse(
                        brother_module.supports_phoenix_registration(
                            "192.0.2.10"
                        )
                    )

    def test_register_profiles_selects_phoenix_transport(self):
        with (
            mock.patch.object(
                brother_module,
                "supports_phoenix_registration",
                return_value=True,
            ) as supports,
            mock.patch.object(
                brother_module,
                "_register_profiles_phoenix",
                return_value=True,
            ) as phoenix,
            mock.patch.object(
                brother_module,
                "_register_profiles_snmp",
            ) as snmp,
        ):
            success = brother_module.register_profiles(
                "192.0.2.10",
                "192.0.2.20",
                54925,
                "Paperless",
                "/tmp/daemon.log",
            )

        self.assertTrue(success)
        supports.assert_called_once_with("192.0.2.10")
        phoenix.assert_called_once_with(
            "192.0.2.10",
            "192.0.2.20",
            54925,
            "Paperless",
            "/tmp/daemon.log",
        )
        snmp.assert_not_called()

    def test_register_profiles_selects_legacy_transport(self):
        with (
            mock.patch.object(
                brother_module,
                "supports_phoenix_registration",
                return_value=False,
            ),
            mock.patch.object(
                brother_module,
                "_register_profiles_phoenix",
            ) as phoenix,
            mock.patch.object(
                brother_module,
                "_register_profiles_snmp",
                return_value=False,
            ) as snmp,
        ):
            success = brother_module.register_profiles(
                "192.0.2.10",
                "192.0.2.20",
                54925,
                "Paperless",
                None,
            )

        self.assertFalse(success)
        snmp.assert_called_once_with(
            "192.0.2.10",
            "192.0.2.20",
            54925,
            "Paperless",
            None,
        )
        phoenix.assert_not_called()

    def test_legacy_registration_builds_and_logs_all_four_profiles(self):
        result = SimpleNamespace(returncode=0, stderr="")

        with (
            mock.patch.object(
                brother_module.subprocess, "run", return_value=result
            ) as run,
            mock.patch.object(brother_module, "log") as log,
        ):
            success = brother_module._register_profiles_snmp(
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
        run.assert_called_once_with(
            command,
            capture_output=True,
            text=True,
            timeout=10,
        )
        log.assert_called_once_with(
            "Registered scan profiles on printer using SNMP",
            None,
        )

    def test_legacy_registration_reports_command_failure(self):
        result = SimpleNamespace(returncode=1, stderr="SNMP error\n")

        with (
            mock.patch.object(
                brother_module.subprocess,
                "run",
                return_value=result,
            ),
            mock.patch.object(brother_module, "log") as log,
        ):
            success = brother_module._register_profiles_snmp(
                "192.0.2.10", "192.0.2.20", 54925, "Paperless", None
            )

        self.assertFalse(success)
        log.assert_called_once_with("SNMP registration failed: SNMP error", None)

    def test_legacy_registration_reports_execution_error(self):
        with (
            mock.patch.object(
                brother_module.subprocess,
                "run",
                side_effect=OSError("not installed"),
            ),
            mock.patch.object(brother_module, "log") as log,
        ):
            success = brother_module._register_profiles_snmp(
                "192.0.2.10", "192.0.2.20", 54925, "Paperless", None
            )

        self.assertFalse(success)
        log.assert_called_once_with("SNMP registration error: not installed", None)

    def test_phoenix_registration_posts_profiles_with_digest_auth(self):
        response_payload = {
            "response": [
                {"key": brother_module.PROFILE_OID, "status": 0}
                for _ in brother_module.PHOENIX_SCAN_PROFILES
            ]
        }
        response = mock.MagicMock()
        response.read.return_value = json.dumps(response_payload).encode(
            "utf-8"
        )
        opener = mock.Mock()
        opener.open.return_value.__enter__ = mock.Mock(return_value=response)
        opener.open.return_value.__exit__ = mock.Mock(return_value=False)
        passwords = mock.Mock()
        tls_context = mock.Mock()
        https_handler = mock.sentinel.https_handler
        digest_handler = mock.sentinel.digest_handler

        with (
            mock.patch.object(
                brother_module,
                "HTTPPasswordMgrWithDefaultRealm",
                return_value=passwords,
            ),
            mock.patch.object(
                brother_module.ssl,
                "create_default_context",
                return_value=tls_context,
            ),
            mock.patch.object(
                brother_module,
                "HTTPSHandler",
                return_value=https_handler,
            ) as https_handler_class,
            mock.patch.object(
                brother_module,
                "HTTPDigestAuthHandler",
                return_value=digest_handler,
            ) as digest_handler_class,
            mock.patch.object(
                brother_module,
                "build_opener",
                return_value=opener,
            ) as build_opener,
            mock.patch.object(brother_module, "log") as log,
        ):
            success = brother_module._register_profiles_phoenix(
                "192.0.2.10",
                "192.0.2.20",
                54925,
                "Paperless",
                "/tmp/daemon.log",
            )

        self.assertTrue(success)
        url = "https://192.0.2.10/phoenix/mib"
        passwords.add_password.assert_called_once_with(
            None,
            url,
            "Public",
            "0000",
        )
        self.assertFalse(tls_context.check_hostname)
        self.assertEqual(
            tls_context.verify_mode,
            brother_module.ssl.CERT_NONE,
        )
        https_handler_class.assert_called_once_with(context=tls_context)
        digest_handler_class.assert_called_once_with(passwords)
        build_opener.assert_called_once_with(https_handler, digest_handler)

        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, url)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        opener.open.assert_called_once_with(request, timeout=10)

        payload = json.loads(request.data)
        self.assertEqual(len(payload["request"]), 4)
        expected_profiles = [
            {
                "key": brother_module.PROFILE_OID,
                "string_value": (
                    f"TYPE=BR;BUTTON=SCAN;USER=Paperless;FUNC={function};"
                    "HOST=192.0.2.20:54925;"
                    f"APPNUM={app_number};DURATION=360;BRID=;"
                ),
            }
            for function, app_number in brother_module.PHOENIX_SCAN_PROFILES
        ]
        self.assertEqual(payload["request"], expected_profiles)
        log.assert_called_once_with(
            "Registered scan profiles on printer using Phoenix HTTPS",
            "/tmp/daemon.log",
        )

    def test_phoenix_registration_rejects_malformed_or_failed_statuses(self):
        successful_statuses = [
            {"key": brother_module.PROFILE_OID, "status": 0}
            for _ in brother_module.PHOENIX_SCAN_PROFILES
        ]
        payloads = (
            {},
            {"response": "not a list"},
            {"response": successful_statuses[:-1]},
            {
                "response": [
                    *successful_statuses[:-1],
                    {"key": brother_module.PROFILE_OID, "status": 1},
                ]
            },
            {
                "response": [
                    *successful_statuses[:-1],
                    {"key": "wrong.oid", "status": 0},
                ]
            },
        )

        for payload in payloads:
            response = mock.MagicMock()
            response.read.return_value = json.dumps(payload).encode("utf-8")
            opener = mock.Mock()
            opener.open.return_value.__enter__ = mock.Mock(
                return_value=response
            )
            opener.open.return_value.__exit__ = mock.Mock(return_value=False)
            with (
                self.subTest(payload=payload),
                mock.patch.object(
                    brother_module,
                    "build_opener",
                    return_value=opener,
                ),
                mock.patch.object(brother_module, "log") as log,
            ):
                success = brother_module._register_profiles_phoenix(
                    "192.0.2.10",
                    "192.0.2.20",
                    54925,
                    "Paperless",
                    None,
                )

            self.assertFalse(success)
            log.assert_called_once_with(
                "Phoenix registration error: "
                "printer returned unsuccessful profile statuses",
                None,
            )

    def test_phoenix_registration_reports_network_errors(self):
        opener = mock.Mock()
        opener.open.side_effect = OSError("connection refused")

        with (
            mock.patch.object(
                brother_module,
                "build_opener",
                return_value=opener,
            ),
            mock.patch.object(brother_module, "log") as log,
        ):
            success = brother_module._register_profiles_phoenix(
                "192.0.2.10",
                "192.0.2.20",
                54925,
                "Paperless",
                None,
            )

        self.assertFalse(success)
        log.assert_called_once_with(
            "Phoenix registration error: connection refused",
            None,
        )


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

    def test_scan_event_identity_uses_registration_and_sequence(self):
        message = (
            "\x02\x00|0TYPE=BR;BUTTON=SCAN;FUNC=FILE;"
            "REGID=13990;SEQ=5;"
        )

        self.assertEqual(
            brother_module.scan_event_identity(
                message,
                "192.0.2.10",
                44127,
            ),
            ("sequence", "192.0.2.10", "13990", "5"),
        )

    def test_scan_event_identity_falls_back_to_source_and_payload(self):
        message = "TYPE=BR;BUTTON=SCAN;FUNC=FILE;"

        self.assertEqual(
            brother_module.scan_event_identity(
                message,
                "192.0.2.10",
                44127,
            ),
            ("packet", "192.0.2.10", "44127", message),
        )

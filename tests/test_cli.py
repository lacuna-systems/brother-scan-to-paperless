import argparse
import unittest
from unittest import mock

import brother_scan_to_paperless.cli as cli_module


class CommandTests(unittest.TestCase):
    def test_cmd_run_applies_cli_overrides(self):
        config = {
            "driver_version": "5",
            "printer_ip": "old-printer",
            "host_ip": "old-host",
            "consume_dir": "/old",
            "resolution": 300,
            "display_name": "Old",
        }
        args = argparse.Namespace(
            config=None,
            printer_ip="192.0.2.10",
            host_ip="192.0.2.20",
            consume_dir="/consume",
            resolution=600,
            display_name="Paperless",
        )

        with (
            mock.patch.object(cli_module, "load_config", return_value=config),
            mock.patch.object(cli_module, "check_dependencies", return_value=[]),
            mock.patch.object(cli_module, "validate_config", return_value=[]),
            mock.patch.object(
                cli_module, "configure_container_scanner"
            ) as configure_scanner,
            mock.patch.object(cli_module, "run_daemon") as run_daemon,
        ):
            cli_module.cmd_run(args)

        configure_scanner.assert_called_once_with(config)
        run_daemon.assert_called_once_with(config)
        self.assertEqual(config["printer_ip"], "192.0.2.10")
        self.assertEqual(config["host_ip"], "192.0.2.20")
        self.assertEqual(config["consume_dir"], "/consume")
        self.assertEqual(config["resolution"], 600)
        self.assertEqual(config["display_name"], "Paperless")

    def test_cmd_run_exits_when_driver_dependency_is_missing(self):
        config = {"driver_version": "5"}
        args = argparse.Namespace(
            config=None,
            printer_ip=None,
            host_ip=None,
            consume_dir=None,
            resolution=None,
            display_name=None,
        )

        with (
            mock.patch.object(cli_module, "load_config", return_value=config),
            mock.patch.object(
                cli_module, "check_dependencies", return_value=["brsaneconfig5"]
            ),
            mock.patch("builtins.print") as output,
        ):
            with self.assertRaisesRegex(SystemExit, "1"):
                cli_module.cmd_run(args)

        self.assertTrue(
            any("Install brscan5" in str(call) for call in output.call_args_list)
        )

    def test_cmd_test_runs_scan_with_cli_overrides(self):
        config = {"printer_ip": "old", "consume_dir": "/old"}
        args = argparse.Namespace(
            config=None,
            printer_ip="192.0.2.10",
            consume_dir="/consume",
        )

        with (
            mock.patch.object(cli_module, "load_config", return_value=config),
            mock.patch.object(cli_module, "validate_config", return_value=[]),
            mock.patch.object(
                cli_module, "configure_container_scanner"
            ) as configure_scanner,
            mock.patch.object(cli_module, "do_scan", return_value=True) as do_scan,
            mock.patch("builtins.print"),
        ):
            cli_module.cmd_test(args)

        configure_scanner.assert_called_once_with(config)
        do_scan.assert_called_once_with(config, page_limit=1)
        self.assertEqual(config["printer_ip"], "192.0.2.10")
        self.assertEqual(config["consume_dir"], "/consume")

    def test_cmd_test_exits_after_failed_scan(self):
        args = argparse.Namespace(config=None, printer_ip=None, consume_dir=None)

        with (
            mock.patch.object(cli_module, "load_config", return_value={}),
            mock.patch.object(cli_module, "validate_config", return_value=[]),
            mock.patch.object(cli_module, "do_scan", return_value=False),
            mock.patch("builtins.print"),
        ):
            with self.assertRaisesRegex(SystemExit, "1"):
                cli_module.cmd_test(args)

    def test_setup_uses_detected_driver_and_writes_config(self):
        answers = iter(["", "192.0.2.10", "", "", "/consume", "600", "Desk"])
        with (
            mock.patch.object(cli_module, "default_driver_version", return_value="5"),
            mock.patch.object(cli_module, "detect_host_ip", return_value="192.0.2.20"),
            mock.patch.object(cli_module, "validate_config", return_value=[]),
            mock.patch.object(cli_module.os, "makedirs"),
            mock.patch.object(cli_module.os, "chmod") as chmod,
            mock.patch.object(cli_module, "write_config") as write_config,
            mock.patch("builtins.input", side_effect=lambda prompt: next(answers)),
            mock.patch("builtins.print"),
        ):
            cli_module.cmd_setup(argparse.Namespace())

        dumped, config_path = write_config.call_args.args
        self.assertEqual(dumped["driver_version"], "5")
        self.assertEqual(dumped["scanner_device"], "brother5:net1;dev0")
        self.assertEqual(dumped["printer_ip"], "192.0.2.10")
        self.assertEqual(dumped["host_ip"], "192.0.2.20")
        self.assertEqual(dumped["resolution"], 600)
        self.assertEqual(dumped["display_name"], "Desk")
        self.assertEqual(
            config_path,
            "/etc/brother-scan-to-paperless/config.json",
        )
        chmod.assert_called_once_with(
            "/etc/brother-scan-to-paperless",
            0o700,
        )

    def test_setup_configures_api_delivery_without_echoing_token(self):
        answers = iter(
            [
                "5",
                "192.0.2.10",
                "192.0.2.20",
                "api",
                "/staging",
                "http://paperless.example/prefix",
                "yes",
                "300",
                "Paperless API",
            ]
        )
        with (
            mock.patch.object(cli_module, "default_driver_version", return_value="4"),
            mock.patch.object(cli_module, "detect_host_ip", return_value="192.0.2.20"),
            mock.patch.object(cli_module, "validate_config", return_value=[]),
            mock.patch.object(cli_module.os, "makedirs"),
            mock.patch.object(cli_module.os, "chmod"),
            mock.patch.object(cli_module, "write_config") as write_config,
            mock.patch("builtins.input", side_effect=lambda prompt: next(answers)),
            mock.patch.object(
                cli_module.getpass, "getpass", return_value="api-secret"
            ) as getpass,
            mock.patch("builtins.print"),
        ):
            cli_module.cmd_setup(argparse.Namespace())

        dumped, _ = write_config.call_args.args
        self.assertEqual(dumped["delivery_mode"], "api")
        self.assertEqual(dumped["staging_dir"], "/staging")
        self.assertEqual(dumped["paperless_url"], "http://paperless.example/prefix")
        self.assertTrue(dumped["paperless_allow_insecure_http"])
        self.assertEqual(dumped["paperless_token"], "api-secret")
        self.assertEqual(dumped["display_name"], "Paperless API")
        getpass.assert_called_once_with("Paperless-ngx API token: ")


class ContainerScannerTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "driver_version": "5",
            "printer_ip": "192.0.2.10",
        }

    @mock.patch.dict("os.environ", {}, clear=True)
    def test_container_registration_is_disabled_by_default(self):
        with mock.patch.object(cli_module, "configure_sane_scanner") as configure:
            cli_module.configure_container_scanner(self.config)

        configure.assert_not_called()

    @mock.patch.dict(
        "os.environ",
        {
            "AUTO_REGISTER_SCANNER": "true",
            "SKIP_SCANNER_REGISTRATION": "true",
        },
        clear=True,
    )
    def test_container_registration_can_be_skipped(self):
        with mock.patch.object(cli_module, "configure_sane_scanner") as configure:
            cli_module.configure_container_scanner(self.config)

        configure.assert_not_called()

    @mock.patch.dict(
        "os.environ",
        {
            "AUTO_REGISTER_SCANNER": "true",
            "SCANNER_MODEL": "DCP-L3560CDW",
            "SCANNER_NAME": "",
        },
        clear=True,
    )
    def test_container_registration_uses_default_name(self):
        with mock.patch.object(cli_module, "configure_sane_scanner") as configure:
            cli_module.configure_container_scanner(self.config)

        configure.assert_called_once_with(
            "5", "BROTHER", "DCP-L3560CDW", "192.0.2.10"
        )

    @mock.patch.dict(
        "os.environ",
        {
            "AUTO_REGISTER_SCANNER": "true",
            "SCANNER_MODEL": "UNKNOWN",
        },
        clear=True,
    )
    def test_unverified_brscan_registration_warns_and_continues(self):
        with (
            mock.patch.object(
                cli_module,
                "configure_sane_scanner",
                return_value=False,
            ) as configure,
            mock.patch("builtins.print") as output,
        ):
            cli_module.configure_container_scanner(self.config)

        configure.assert_called_once_with(
            "5", "BROTHER", "UNKNOWN", "192.0.2.10"
        )
        output.assert_called_once_with(
            "Continuing without a verified Brother SANE registration"
        )

    @mock.patch.dict(
        "os.environ",
        {"AUTO_REGISTER_SCANNER": "true"},
        clear=True,
    )
    def test_container_registration_requires_model(self):
        with self.assertRaisesRegex(
            cli_module.ScannerConfigurationError,
            "SCANNER_MODEL is required",
        ):
            cli_module.configure_container_scanner(self.config)

    def test_registration_failure_prevents_daemon_start(self):
        args = argparse.Namespace(
            config=None,
            printer_ip=None,
            host_ip=None,
            consume_dir=None,
            resolution=None,
            display_name=None,
        )
        config = self.config.copy()

        with (
            mock.patch.object(cli_module, "load_config", return_value=config),
            mock.patch.object(cli_module, "check_dependencies", return_value=[]),
            mock.patch.object(cli_module, "validate_config", return_value=[]),
            mock.patch.object(
                cli_module,
                "configure_container_scanner",
                side_effect=cli_module.ScannerConfigurationError("failed"),
            ),
            mock.patch.object(cli_module, "run_daemon") as run_daemon,
            self.assertRaisesRegex(
                cli_module.ScannerConfigurationError,
                "failed",
            ),
        ):
            cli_module.cmd_run(args)

        run_daemon.assert_not_called()

    def test_registration_failure_prevents_test_scan(self):
        args = argparse.Namespace(
            config=None,
            printer_ip=None,
            consume_dir=None,
        )

        with (
            mock.patch.object(cli_module, "load_config", return_value=self.config),
            mock.patch.object(cli_module, "validate_config", return_value=[]),
            mock.patch.object(
                cli_module,
                "configure_container_scanner",
                side_effect=cli_module.ScannerConfigurationError("failed"),
            ),
            mock.patch.object(cli_module, "do_scan") as do_scan,
            self.assertRaisesRegex(
                cli_module.ScannerConfigurationError,
                "failed",
            ),
        ):
            cli_module.cmd_test(args)

        do_scan.assert_not_called()

    def test_validation_failure_prevents_container_registration(self):
        args = argparse.Namespace(
            config=None,
            printer_ip=None,
            consume_dir=None,
        )

        with (
            mock.patch.object(cli_module, "load_config", return_value={}),
            mock.patch.object(
                cli_module,
                "validate_config",
                return_value=["invalid config"],
            ),
            mock.patch.object(
                cli_module, "configure_container_scanner"
            ) as configure_scanner,
            mock.patch.object(cli_module, "do_scan") as do_scan,
            mock.patch("builtins.print"),
            self.assertRaisesRegex(SystemExit, "1"),
        ):
            cli_module.cmd_test(args)

        configure_scanner.assert_not_called()
        do_scan.assert_not_called()

    def test_main_reports_scanner_configuration_error(self):
        with (
            mock.patch.object(cli_module.sys, "argv", ["command", "run"]),
            mock.patch.object(
                cli_module,
                "cmd_run",
                side_effect=cli_module.ScannerConfigurationError("failed"),
            ),
            mock.patch("builtins.print") as output,
            self.assertRaisesRegex(SystemExit, "1"),
        ):
            cli_module.main()

        output.assert_called_once_with("failed", file=cli_module.sys.stderr)

    def test_main_reports_scan_listener_error(self):
        error = cli_module.ScanListenerError(
            "UDP port 54925 is already in use; stop brscan-skey"
        )
        with (
            mock.patch.object(cli_module.sys, "argv", ["command", "run"]),
            mock.patch.object(cli_module, "cmd_run", side_effect=error),
            mock.patch("builtins.print") as output,
            self.assertRaisesRegex(SystemExit, "1"),
        ):
            cli_module.main()

        output.assert_called_once_with(str(error), file=cli_module.sys.stderr)

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import brother_scan_to_paperless.config as config_module


class ConfigTests(unittest.TestCase):
    @mock.patch.dict(os.environ, {}, clear=True)
    def test_native_defaults_use_brscan4(self):
        config = config_module.load_config("/does-not-exist")

        self.assertEqual(config["driver_version"], "4")
        self.assertEqual(config["scanner_device"], "brother4:net1;dev0")

    @mock.patch.dict(
        os.environ,
        {
            "BRSCAN_VERSION": "5",
            "PRINTER_IP": "192.0.2.10",
            "HOST_IP": "192.0.2.20",
            "CONSUME_DIR": "/consume",
            "RESOLUTION": "600",
            "LOG_FILE": "",
        },
        clear=True,
    )
    def test_environment_selects_brscan5_and_converts_integers(self):
        config = config_module.load_config("/does-not-exist")

        self.assertEqual(config["driver_version"], "5")
        self.assertEqual(config["scanner_device"], "brother5:net1;dev0")
        self.assertEqual(config["resolution"], 600)
        self.assertEqual(config["log_file"], "")

    @mock.patch.dict(
        os.environ,
        {
            "DELIVERY_MODE": "API",
            "STAGING_DIR": "/staging",
            "PAPERLESS_URL": "https://paperless.example/paperless",
            "PAPERLESS_API_TOKEN": "environment-token",
            "PAPERLESS_API_TIMEOUT": "45",
            "PAPERLESS_ALLOW_INSECURE_HTTP": "true",
        },
        clear=True,
    )
    def test_environment_configures_api_delivery(self):
        config = config_module.load_config("/does-not-exist")

        self.assertEqual(config["delivery_mode"], "api")
        self.assertEqual(config["staging_dir"], "/staging")
        self.assertEqual(
            config["paperless_url"],
            "https://paperless.example/paperless",
        )
        self.assertEqual(config["paperless_token"], "environment-token")
        self.assertEqual(config["paperless_api_timeout"], 45)
        self.assertTrue(config["paperless_allow_insecure_http"])

    def test_token_file_overrides_environment_token_and_strips_whitespace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            token_file = Path(temp_dir) / "paperless-token"
            token_file.write_text("  file-token\n", encoding="utf-8")
            environment = {
                "PAPERLESS_API_TOKEN": "environment-token",
                "PAPERLESS_API_TOKEN_FILE": str(token_file),
            }
            with mock.patch.dict(os.environ, environment, clear=True):
                config = config_module.load_config("/does-not-exist")

        self.assertEqual(config["paperless_token"], "file-token")

    @mock.patch.dict(
        os.environ,
        {"PAPERLESS_API_TOKEN_FILE": "/does-not-exist/token"},
        clear=True,
    )
    def test_unreadable_token_file_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "PAPERLESS_API_TOKEN_FILE cannot be read",
        ):
            config_module.load_config("/does-not-exist")

    def test_empty_token_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            token_file = Path(temp_dir) / "paperless-token"
            token_file.write_text(" \n", encoding="utf-8")
            with (
                mock.patch.dict(
                    os.environ,
                    {"PAPERLESS_API_TOKEN_FILE": str(token_file)},
                    clear=True,
                ),
                self.assertRaisesRegex(
                    ValueError,
                    "PAPERLESS_API_TOKEN_FILE is empty",
                ),
            ):
                config_module.load_config("/does-not-exist")

    @mock.patch.dict(os.environ, {"LISTEN_PORT": "invalid"}, clear=True)
    def test_invalid_integer_environment_value_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "LISTEN_PORT must be an integer"):
            config_module.load_config("/does-not-exist")

    @mock.patch.dict(
        os.environ,
        {"PAPERLESS_ALLOW_INSECURE_HTTP": "sometimes"},
        clear=True,
    )
    def test_invalid_boolean_environment_value_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "PAPERLESS_ALLOW_INSECURE_HTTP must be true or false",
        ):
            config_module.load_config("/does-not-exist")

    @mock.patch.dict(
        os.environ,
        {"PRINTER_IP": "192.0.2.11", "RESOLUTION": "600"},
        clear=True,
    )
    def test_file_config_is_loaded_then_environment_takes_precedence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "driver_version": 5,
                        "printer_ip": "192.0.2.10",
                        "resolution": 150,
                        "scanner_device": "custom:device",
                    }
                ),
                encoding="utf-8",
            )

            config = config_module.load_config(str(config_path))

        self.assertEqual(config["driver_version"], "5")
        self.assertEqual(config["printer_ip"], "192.0.2.11")
        self.assertEqual(config["resolution"], 600)
        self.assertEqual(config["scanner_device"], "custom:device")

    def test_write_config_atomically_replaces_legacy_permissions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text('{"legacy": true}\n', encoding="utf-8")
            config_path.chmod(0o644)

            config_module.write_config(
                {"paperless_token": "api-secret", "delivery_mode": "api"},
                str(config_path),
            )

            saved = json.loads(config_path.read_text(encoding="utf-8"))
            saved_mode = config_path.stat().st_mode & 0o777
            leftovers = list(Path(temp_dir).glob(".config.*.tmp"))

        self.assertEqual(saved["paperless_token"], "api-secret")
        self.assertEqual(saved_mode, 0o600)
        self.assertEqual(leftovers, [])

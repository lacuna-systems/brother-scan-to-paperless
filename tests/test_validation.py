import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import brother_scan_to_paperless.config as config_module


class ConfigValidationTests(unittest.TestCase):
    def test_validate_config_accepts_complete_config(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = {
                "driver_version": "5",
                "printer_ip": "192.0.2.10",
                "host_ip": "192.0.2.20",
                "consume_dir": consume_dir,
            }

            self.assertEqual(config_module.validate_config(config), [])

    def test_validate_config_detects_host_ip(self):
        with (
            tempfile.TemporaryDirectory() as consume_dir,
            mock.patch.object(
                config_module, "detect_host_ip", return_value="192.0.2.20"
            ),
        ):
            config = {
                "driver_version": "4",
                "printer_ip": "192.0.2.10",
                "host_ip": "",
                "consume_dir": consume_dir,
            }

            errors = config_module.validate_config(config)

        self.assertEqual(errors, [])
        self.assertEqual(config["host_ip"], "192.0.2.20")

    def test_validate_config_reports_required_and_invalid_values(self):
        with mock.patch.object(config_module, "detect_host_ip", return_value=None):
            errors = config_module.validate_config(
                {
                    "driver_version": "6",
                    "printer_ip": "",
                    "host_ip": "",
                    "consume_dir": "",
                }
            )

        self.assertEqual(
            errors,
            [
                "driver_version must be 4 or 5",
                "printer_ip is required",
                "host_ip is required (auto-detection failed)",
                "consume_dir is required",
            ],
        )

    def test_validate_config_rejects_missing_consume_directory(self):
        errors = config_module.validate_config(
            {
                "driver_version": "4",
                "printer_ip": "192.0.2.10",
                "host_ip": "192.0.2.20",
                "consume_dir": "/does-not-exist",
            }
        )

        self.assertEqual(errors, ["consume_dir does not exist: /does-not-exist"])

    def test_validate_config_rejects_unwritable_consume_directory(self):
        with (
            tempfile.TemporaryDirectory() as consume_dir,
            mock.patch.object(config_module.os, "access", return_value=False),
        ):
            errors = config_module.validate_config(
                {
                    "driver_version": "4",
                    "printer_ip": "192.0.2.10",
                    "host_ip": "192.0.2.20",
                    "consume_dir": consume_dir,
                }
            )

        self.assertEqual(
            errors,
            [f"consume_dir is not writable: {consume_dir}"],
        )

    def test_validate_config_accepts_api_mode_without_consume_directory(self):
        with tempfile.TemporaryDirectory() as staging_dir:
            config = {
                "driver_version": "5",
                "delivery_mode": "api",
                "printer_ip": "192.0.2.10",
                "host_ip": "192.0.2.20",
                "staging_dir": staging_dir,
                "paperless_url": "https://paperless.example/paperless",
                "paperless_token": "secret",
                "paperless_api_timeout": 30,
            }

            self.assertEqual(config_module.validate_config(config), [])

    def test_validate_config_accepts_and_rejects_paperless_tags(self):
        base = {
            "driver_version": "5",
            "delivery_mode": "api",
            "printer_ip": "192.0.2.10",
            "host_ip": "192.0.2.20",
            "paperless_url": "https://paperless.example/paperless",
            "paperless_token": "secret",
            "paperless_api_timeout": 30,
        }
        cases = {
            "valid": ([3, 7], []),
            "zero": ([0], ["paperless_tags must be a list of positive tag IDs"]),
            "name": (["inbox"], ["paperless_tags must be a list of positive tag IDs"]),
            "boolean": ([True], ["paperless_tags must be a list of positive tag IDs"]),
            "not a list": ("3", ["paperless_tags must be a list of positive tag IDs"]),
        }

        with tempfile.TemporaryDirectory() as staging_dir:
            for name, (tags, expected) in cases.items():
                with self.subTest(name=name):
                    config = dict(
                        base, staging_dir=staging_dir, paperless_tags=tags
                    )
                    self.assertEqual(
                        config_module.validate_config(config), expected
                    )

    def test_validate_config_rejects_paperless_tags_in_consume_mode(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            errors = config_module.validate_config(
                {
                    "driver_version": "4",
                    "printer_ip": "192.0.2.10",
                    "host_ip": "192.0.2.20",
                    "consume_dir": consume_dir,
                    "paperless_tags": [3],
                }
            )

        self.assertEqual(errors, ["paperless_tags requires api delivery mode"])

    def test_validate_config_reports_api_requirements(self):
        errors = config_module.validate_config(
            {
                "driver_version": "5",
                "delivery_mode": "api",
                "printer_ip": "192.0.2.10",
                "host_ip": "192.0.2.20",
                "staging_dir": "",
                "paperless_url": "",
                "paperless_token": "",
                "paperless_api_timeout": 0,
            }
        )

        self.assertEqual(
            errors,
            [
                "paperless_url is required in api mode",
                "paperless_token is required in api mode",
                "paperless_api_timeout must be greater than zero",
                "staging_dir is required in api mode",
            ],
        )

    def test_validate_config_rejects_invalid_mode_url_and_token(self):
        with tempfile.TemporaryDirectory() as staging_dir:
            invalid_mode = {
                "driver_version": "5",
                "delivery_mode": "other",
                "printer_ip": "192.0.2.10",
                "host_ip": "192.0.2.20",
                "consume_dir": staging_dir,
            }
            invalid_api = {
                "driver_version": "5",
                "delivery_mode": "api",
                "printer_ip": "192.0.2.10",
                "host_ip": "192.0.2.20",
                "staging_dir": staging_dir,
                "paperless_url": "ftp://paperless.example",
                "paperless_token": "secret\nheader",
                "paperless_api_timeout": 30,
            }

            mode_errors = config_module.validate_config(invalid_mode)
            api_errors = config_module.validate_config(invalid_api)

        self.assertIn("delivery_mode must be consume or api", mode_errors)
        self.assertIn(
            "paperless_url must be an http or https URL",
            api_errors,
        )
        self.assertIn("paperless_token must not contain newlines", api_errors)

    def test_validate_config_checks_api_staging_directory_writability(self):
        with (
            tempfile.TemporaryDirectory() as staging_dir,
            mock.patch.object(
                config_module.os,
                "access",
                return_value=False,
            ),
        ):
            errors = config_module.validate_config(
                {
                    "driver_version": "5",
                    "delivery_mode": "api",
                    "printer_ip": "192.0.2.10",
                    "host_ip": "192.0.2.20",
                    "staging_dir": staging_dir,
                    "paperless_url": "https://paperless.example",
                    "paperless_token": "secret",
                    "paperless_api_timeout": 30,
                }
            )

        self.assertEqual(
            errors,
            [f"staging_dir is not writable: {staging_dir}"],
        )

    def test_validate_config_requires_opt_in_for_plaintext_http(self):
        with tempfile.TemporaryDirectory() as staging_dir:
            config = {
                "driver_version": "5",
                "delivery_mode": "api",
                "printer_ip": "192.0.2.10",
                "host_ip": "192.0.2.20",
                "staging_dir": staging_dir,
                "paperless_url": "http://paperless.example",
                "paperless_token": "secret",
                "paperless_api_timeout": 30,
            }

            errors = config_module.validate_config(config)
            config["paperless_allow_insecure_http"] = True
            allowed_errors = config_module.validate_config(config)

        self.assertEqual(
            errors,
            [
                "paperless_url must use https unless "
                "paperless_allow_insecure_http is true"
            ],
        )
        self.assertEqual(allowed_errors, [])

    def test_validate_config_rejects_non_boolean_http_setting(self):
        with tempfile.TemporaryDirectory() as staging_dir:
            errors = config_module.validate_config(
                {
                    "driver_version": "5",
                    "delivery_mode": "api",
                    "printer_ip": "192.0.2.10",
                    "host_ip": "192.0.2.20",
                    "staging_dir": staging_dir,
                    "paperless_url": "https://paperless.example",
                    "paperless_token": "secret",
                    "paperless_api_timeout": 30,
                    "paperless_allow_insecure_http": "false",
                }
            )

        self.assertEqual(
            errors,
            ["paperless_allow_insecure_http must be true or false"],
        )

    def test_validate_config_requires_private_owned_staging_directory(self):
        with tempfile.TemporaryDirectory() as staging_dir:
            config = {
                "driver_version": "5",
                "delivery_mode": "api",
                "printer_ip": "192.0.2.10",
                "host_ip": "192.0.2.20",
                "staging_dir": staging_dir,
                "paperless_url": "https://paperless.example",
                "paperless_token": "secret",
                "paperless_api_timeout": 30,
            }
            os.chmod(staging_dir, 0o755)
            permission_errors = config_module.validate_config(config)
            os.chmod(staging_dir, 0o700)
            owner = os.stat(staging_dir).st_uid
            with mock.patch.object(
                config_module.os,
                "geteuid",
                return_value=owner + 1,
            ):
                owner_errors = config_module.validate_config(config)

        self.assertEqual(
            permission_errors,
            [f"staging_dir must not grant group or other access: {staging_dir}"],
        )
        self.assertEqual(
            owner_errors,
            [f"staging_dir must be owned by the daemon user: {staging_dir}"],
        )

    def test_validate_config_rejects_staging_directory_symlink(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_dir = Path(temp_dir) / "staging"
            staging_dir.mkdir(mode=0o700)
            staging_link = Path(temp_dir) / "staging-link"
            staging_link.symlink_to(staging_dir, target_is_directory=True)

            errors = config_module.validate_config(
                {
                    "driver_version": "5",
                    "delivery_mode": "api",
                    "printer_ip": "192.0.2.10",
                    "host_ip": "192.0.2.20",
                    "staging_dir": str(staging_link),
                    "paperless_url": "https://paperless.example",
                    "paperless_token": "secret",
                    "paperless_api_timeout": 30,
                }
            )

        self.assertEqual(
            errors,
            ["staging_dir must not be a symbolic link"],
        )

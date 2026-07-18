import tempfile
import unittest
from pathlib import Path
from unittest import mock

import brother_scan_to_paperless.logger as logger_module


class LoggingTests(unittest.TestCase):
    def test_log_prints_and_appends_to_file(self):
        fake_datetime = mock.Mock()
        fake_datetime.now.return_value.strftime.return_value = "2026-01-02 03:04:05"

        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "logger_module.log"
            with (
                mock.patch.object(logger_module, "datetime", fake_datetime),
                mock.patch("builtins.print") as output,
            ):
                logger_module.log("ready", str(log_path))

            self.assertEqual(
                log_path.read_text(encoding="utf-8"),
                "[2026-01-02 03:04:05] ready\n",
            )
            output.assert_called_once_with("[2026-01-02 03:04:05] ready", flush=True)

    def test_log_failures_do_not_interrupt_delivery(self):
        with (
            mock.patch(
                "builtins.print",
                side_effect=BrokenPipeError("stdout closed"),
            ),
            mock.patch(
                "builtins.open",
                side_effect=PermissionError("log is read-only"),
            ),
        ):
            logger_module.log("Paperless accepted scan", "/var/log/scan.log")

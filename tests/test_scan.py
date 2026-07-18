import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests import support

import brother_scan_to_paperless.scanner as scanner_module


SCAN_TIMESTAMP = support.SCAN_TIMESTAMP
SCAN_IDENTIFIER = support.SCAN_IDENTIFIER
scan_config = support.scan_config
expected_scan_path = support.expected_scan_path
expected_partial_path = support.expected_partial_path
write_api_scan = support.write_api_scan


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.datetime = mock.Mock()
        self.datetime.now.return_value.strftime.return_value = SCAN_TIMESTAMP
        self.uuid = SimpleNamespace(hex=f"{SCAN_IDENTIFIER}cafebabe")

    def test_consume_mode_leaves_successful_scan_in_consume_directory(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)
            outfile = expected_scan_path(consume_dir)
            scanfile = expected_partial_path(consume_dir)

            def run_scan(command, **kwargs):
                scanfile.write_bytes(b"scan data")
                return SimpleNamespace(returncode=0, stderr="")

            with (
                mock.patch.object(scanner_module, "datetime", self.datetime),
                mock.patch.object(scanner_module.uuid, "uuid4", return_value=self.uuid),
                mock.patch.object(
                    scanner_module.subprocess, "run", side_effect=run_scan
                ) as run,
                mock.patch.object(scanner_module, "upload_to_paperless") as upload,
                mock.patch.object(scanner_module, "log"),
            ):
                success = scanner_module.do_scan(config)

            self.assertTrue(success)
            self.assertEqual(outfile.read_bytes(), b"scan data")
            upload.assert_not_called()
            run.assert_called_once_with(
                [
                    "scanimage",
                    "--device-name=brother5:net1;dev0",
                    "--resolution=300",
                    "--format=tiff",
                    f"--output-file={scanfile}",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )

    def test_failed_scan_removes_partial_output(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)
            outfile = expected_scan_path(consume_dir)
            scanfile = expected_partial_path(consume_dir)

            def run_scan(command, **kwargs):
                scanfile.write_bytes(b"partial")
                return SimpleNamespace(returncode=1, stderr="device error")

            with (
                mock.patch.object(scanner_module, "datetime", self.datetime),
                mock.patch.object(scanner_module.uuid, "uuid4", return_value=self.uuid),
                mock.patch.object(
                    scanner_module.subprocess, "run", side_effect=run_scan
                ),
                mock.patch.object(scanner_module, "log"),
            ):
                success = scanner_module.do_scan(config)

            self.assertFalse(success)
            self.assertFalse(outfile.exists())
            self.assertFalse(scanfile.exists())

    def test_timed_out_scan_removes_partial_output(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)
            outfile = expected_scan_path(consume_dir)
            scanfile = expected_partial_path(consume_dir)

            def run_scan(command, **kwargs):
                scanfile.write_bytes(b"partial")
                raise subprocess.TimeoutExpired(command, 120)

            with (
                mock.patch.object(scanner_module, "datetime", self.datetime),
                mock.patch.object(scanner_module.uuid, "uuid4", return_value=self.uuid),
                mock.patch.object(
                    scanner_module.subprocess, "run", side_effect=run_scan
                ),
                mock.patch.object(scanner_module, "log"),
            ):
                success = scanner_module.do_scan(config)

            self.assertFalse(success)
            self.assertFalse(outfile.exists())
            self.assertFalse(scanfile.exists())

    def test_api_mode_removes_staged_file_after_successful_upload(self):
        with tempfile.TemporaryDirectory() as staging_dir:
            config = scan_config(staging_dir, delivery_mode="api")
            outfile = expected_scan_path(staging_dir)

            def run_scan(command, **kwargs):
                write_api_scan(command, kwargs, b"scan data")
                return SimpleNamespace(returncode=0, stderr="")

            with (
                mock.patch.object(scanner_module, "datetime", self.datetime),
                mock.patch.object(scanner_module.uuid, "uuid4", return_value=self.uuid),
                mock.patch.object(
                    scanner_module.subprocess, "run", side_effect=run_scan
                ),
                mock.patch.object(
                    scanner_module, "upload_to_paperless", return_value=True
                ) as upload,
                mock.patch.object(scanner_module, "log"),
            ):
                success = scanner_module.do_scan(config)

            self.assertTrue(success)
            upload.assert_called_once_with(config, str(outfile))
            self.assertFalse(outfile.exists())

    def test_api_mode_marks_local_copy_when_accepted_file_cannot_be_removed(self):
        with tempfile.TemporaryDirectory() as staging_dir:
            config = scan_config(staging_dir, delivery_mode="api")
            outfile = expected_scan_path(staging_dir)
            accepted_file = Path(f"{outfile}.accepted")

            def run_scan(command, **kwargs):
                write_api_scan(command, kwargs, b"scan data")
                return SimpleNamespace(returncode=0, stderr="")

            with (
                mock.patch.object(scanner_module, "datetime", self.datetime),
                mock.patch.object(scanner_module.uuid, "uuid4", return_value=self.uuid),
                mock.patch.object(
                    scanner_module.subprocess, "run", side_effect=run_scan
                ),
                mock.patch.object(
                    scanner_module, "upload_to_paperless", return_value=True
                ),
                mock.patch.object(
                    scanner_module.os, "remove", side_effect=OSError("read-only")
                ),
                mock.patch.object(scanner_module, "log") as log,
            ):
                success = scanner_module.do_scan(config)

            self.assertTrue(success)
            self.assertFalse(outfile.exists())
            self.assertEqual(accepted_file.read_bytes(), b"scan data")
            log.assert_any_call(
                "Paperless accepted the scan; retained local copy marked at "
                f"{accepted_file}: read-only",
                None,
            )

    def test_api_mode_retains_staged_file_after_failed_upload(self):
        with tempfile.TemporaryDirectory() as staging_dir:
            config = scan_config(staging_dir, delivery_mode="api")
            outfile = expected_scan_path(staging_dir)

            def run_scan(command, **kwargs):
                write_api_scan(command, kwargs, b"scan data")
                return SimpleNamespace(returncode=0, stderr="")

            with (
                mock.patch.object(scanner_module, "datetime", self.datetime),
                mock.patch.object(scanner_module.uuid, "uuid4", return_value=self.uuid),
                mock.patch.object(
                    scanner_module.subprocess, "run", side_effect=run_scan
                ),
                mock.patch.object(
                    scanner_module, "upload_to_paperless", return_value=False
                ) as upload,
                mock.patch.object(scanner_module, "log") as log,
            ):
                success = scanner_module.do_scan(config)

            self.assertFalse(success)
            upload.assert_called_once_with(config, str(outfile))
            self.assertEqual(outfile.read_bytes(), b"scan data")
            self.assertEqual(outfile.stat().st_mode & 0o777, 0o600)
            log.assert_any_call(
                f"Upload failed; retained scan at {outfile}",
                None,
            )

    def test_api_mode_removes_partial_scan_without_attempting_upload(self):
        with tempfile.TemporaryDirectory() as staging_dir:
            config = scan_config(staging_dir, delivery_mode="api")
            outfile = expected_scan_path(staging_dir)
            scanfile = expected_partial_path(staging_dir)

            def run_scan(command, **kwargs):
                write_api_scan(command, kwargs, b"partial")
                return SimpleNamespace(returncode=1, stderr="device error")

            with (
                mock.patch.object(scanner_module, "datetime", self.datetime),
                mock.patch.object(scanner_module.uuid, "uuid4", return_value=self.uuid),
                mock.patch.object(
                    scanner_module.subprocess, "run", side_effect=run_scan
                ),
                mock.patch.object(scanner_module, "upload_to_paperless") as upload,
                mock.patch.object(scanner_module, "log"),
            ):
                success = scanner_module.do_scan(config)

            self.assertFalse(success)
            upload.assert_not_called()
            self.assertFalse(outfile.exists())
            self.assertFalse(scanfile.exists())

    def test_scan_execution_error_is_reported(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            outfile = expected_scan_path(consume_dir)
            scanfile = expected_partial_path(consume_dir)

            def fail_scan(command, **kwargs):
                scanfile.write_bytes(b"partial")
                raise OSError("scanimage failed")

            with (
                mock.patch.object(scanner_module, "datetime", self.datetime),
                mock.patch.object(scanner_module.uuid, "uuid4", return_value=self.uuid),
                mock.patch.object(
                    scanner_module.subprocess, "run", side_effect=fail_scan
                ),
                mock.patch.object(scanner_module, "log") as log,
            ):
                success = scanner_module.do_scan(scan_config(consume_dir))

        self.assertFalse(success)
        self.assertFalse(outfile.exists())
        self.assertFalse(scanfile.exists())
        self.assertEqual(log.call_args.args[0], "Scan error: scanimage failed")

    def test_interrupted_scan_retains_only_partial_filename(self):
        with tempfile.TemporaryDirectory() as staging_dir:
            config = scan_config(staging_dir, delivery_mode="api")
            outfile = expected_scan_path(staging_dir)
            scanfile = expected_partial_path(staging_dir)

            def interrupt_scan(command, **kwargs):
                write_api_scan(command, kwargs, b"incomplete")
                raise KeyboardInterrupt

            with (
                mock.patch.object(scanner_module, "datetime", self.datetime),
                mock.patch.object(scanner_module.uuid, "uuid4", return_value=self.uuid),
                mock.patch.object(
                    scanner_module.subprocess, "run", side_effect=interrupt_scan
                ),
                mock.patch.object(scanner_module, "log"),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    scanner_module.do_scan(config)

            self.assertFalse(outfile.exists())
            self.assertEqual(scanfile.read_bytes(), b"incomplete")
            self.assertEqual(scanfile.stat().st_mode & 0o777, 0o600)

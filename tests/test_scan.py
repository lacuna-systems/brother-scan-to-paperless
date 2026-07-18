import os
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


def batch_directory(output_dir, extension="tiff"):
    outfile = Path(output_dir) / (
        f"scan_{SCAN_TIMESTAMP}_{SCAN_IDENTIFIER}.{extension}"
    )
    return Path(f"{outfile}.batch")


def write_batch_pages(command, options, *payloads, page_numbers=None):
    argument = next(
        item for item in command if item.startswith("--batch=")
    )
    pattern = argument.split("=", 1)[1]
    numbers = page_numbers or range(1, len(payloads) + 1)
    pages = []
    for page_number, payload in zip(numbers, payloads, strict=True):
        page = Path(options["cwd"]) / (pattern % page_number)
        page.write_bytes(payload)
        pages.append(page)
    return pages


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.datetime = mock.Mock()
        self.datetime.now.return_value.strftime.return_value = SCAN_TIMESTAMP
        self.uuid = SimpleNamespace(hex=f"{SCAN_IDENTIFIER}cafebabe")

    def scan_patches(self):
        return (
            mock.patch.object(scanner_module, "datetime", self.datetime),
            mock.patch.object(
                scanner_module.uuid,
                "uuid4",
                return_value=self.uuid,
            ),
            mock.patch.object(scanner_module, "log"),
        )

    def test_consume_mode_uses_batch_and_delivers_single_page(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)
            outfile = expected_scan_path(consume_dir)
            scanfile = expected_partial_path(consume_dir)
            batch_dir = batch_directory(consume_dir)

            def run_scan(command, **options):
                write_batch_pages(command, options, b"scan data")
                return SimpleNamespace(returncode=0, stderr="")

            datetime_patch, uuid_patch, log_patch = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                log_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    side_effect=run_scan,
                ) as run,
                mock.patch.object(
                    scanner_module,
                    "upload_to_paperless",
                ) as upload,
            ):
                success = scanner_module.do_scan(config)

            self.assertTrue(success)
            self.assertEqual(outfile.read_bytes(), b"scan data")
            self.assertFalse(scanfile.exists())
            self.assertFalse(batch_dir.exists())
            upload.assert_not_called()
            run.assert_called_once_with(
                [
                    "scanimage",
                    "--device-name=brother5:net1;dev0",
                    "--resolution=300",
                    "--format=tiff",
                    "--batch=page-%04d.tiff.partial",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(batch_dir),
            )
            self.assertFalse(
                any(
                    argument.startswith("--output-file")
                    for argument in run.call_args.args[0]
                )
            )

    def test_two_page_batch_is_assembled_in_scanner_order(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)
            outfile = expected_scan_path(consume_dir)
            scanfile = expected_partial_path(consume_dir)
            batch_dir = batch_directory(consume_dir)
            page_paths = [
                batch_dir / "page-0001.tiff.partial",
                batch_dir / "page-0002.tiff.partial",
            ]

            def run_command(command, **options):
                if command[0] == "scanimage":
                    write_batch_pages(
                        command,
                        options,
                        b"first page",
                        b"second page",
                    )
                elif command[0] == "tiffcp":
                    assembled = b"|".join(
                        Path(page).read_bytes() for page in command[1:-1]
                    )
                    Path(command[-1]).write_bytes(assembled)
                else:
                    self.fail(f"unexpected command: {command}")
                return SimpleNamespace(returncode=0, stderr="")

            datetime_patch, uuid_patch, log_patch = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                log_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    side_effect=run_command,
                ) as run,
            ):
                success = scanner_module.do_scan(config)

            self.assertTrue(success)
            self.assertEqual(
                outfile.read_bytes(),
                b"first page|second page",
            )
            self.assertFalse(batch_dir.exists())
            self.assertEqual(
                run.call_args_list,
                [
                    mock.call(
                        [
                            "scanimage",
                            "--device-name=brother5:net1;dev0",
                            "--resolution=300",
                            "--format=tiff",
                            "--batch=page-%04d.tiff.partial",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=120,
                        cwd=str(batch_dir),
                    ),
                    mock.call(
                        ["tiffcp", *map(str, page_paths), str(scanfile)],
                        capture_output=True,
                        text=True,
                        timeout=120,
                    ),
                ],
            )

    def test_batch_page_discovery_sorts_page_numbers_numerically(self):
        with tempfile.TemporaryDirectory() as batch_dir:
            directory = Path(batch_dir)
            (directory / "page-0010.tiff.partial").write_bytes(b"ten")
            (directory / "page-0002.tiff.partial").write_bytes(b"two")
            (directory / "page-invalid.tiff.partial").write_bytes(b"bad")
            (directory / "page-0001.tiff.partial.part").write_bytes(b"part")

            pages = scanner_module._batch_pages(batch_dir, "tiff")

        self.assertEqual(
            [Path(page).name for page in pages],
            ["page-0002.tiff.partial", "page-0010.tiff.partial"],
        )

    def test_failed_scan_retains_completed_pages(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)
            outfile = expected_scan_path(consume_dir)
            batch_dir = batch_directory(consume_dir)

            def run_scan(command, **options):
                pages = write_batch_pages(command, options, b"complete page")
                page_two = pages[0].with_name(
                    "page-0002.tiff.partial.part"
                )
                page_two.write_bytes(b"partial page")
                return SimpleNamespace(returncode=1, stderr="device error")

            datetime_patch, uuid_patch, log_patch = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                log_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    side_effect=run_scan,
                ),
            ):
                success = scanner_module.do_scan(config)

            self.assertFalse(success)
            self.assertFalse(outfile.exists())
            self.assertTrue(batch_dir.exists())
            artifacts = list(batch_dir.iterdir())
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0].read_bytes(), b"complete page")

    def test_page_limit_is_forwarded_to_scanimage(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)

            def run_scan(command, **options):
                write_batch_pages(command, options, b"scan data")
                return SimpleNamespace(returncode=0, stderr="")

            datetime_patch, uuid_patch, log_patch = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                log_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    side_effect=run_scan,
                ) as run,
            ):
                success = scanner_module.do_scan(config, page_limit=1)

            self.assertTrue(success)
            self.assertIn("--batch-count=1", run.call_args.args[0])

    def test_success_without_pages_is_a_failed_scan(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)
            outfile = expected_scan_path(consume_dir)
            batch_dir = batch_directory(consume_dir)

            datetime_patch, uuid_patch, log_patch = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                log_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=0, stderr=""),
                ),
            ):
                success = scanner_module.do_scan(config)

            self.assertFalse(success)
            self.assertFalse(outfile.exists())
            self.assertFalse(batch_dir.exists())

    def test_timed_out_scan_removes_in_progress_page(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)
            outfile = expected_scan_path(consume_dir)
            batch_dir = batch_directory(consume_dir)

            def run_scan(command, **options):
                pattern = next(
                    argument.split("=", 1)[1]
                    for argument in command
                    if argument.startswith("--batch=")
                )
                partial = Path(options["cwd"]) / f"{pattern % 1}.part"
                partial.write_bytes(b"incomplete")
                raise subprocess.TimeoutExpired(command, 120)

            datetime_patch, uuid_patch, log_patch = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                log_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    side_effect=run_scan,
                ),
            ):
                success = scanner_module.do_scan(config)

            self.assertFalse(success)
            self.assertFalse(outfile.exists())
            self.assertFalse(batch_dir.exists())

    def test_timeout_retains_pages_completed_before_interruption(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)
            batch_dir = batch_directory(consume_dir)

            def run_scan(command, **options):
                pages = write_batch_pages(command, options, b"complete page")
                page_two = pages[0].with_name(
                    "page-0002.tiff.partial.part"
                )
                page_two.write_bytes(b"partial page")
                raise subprocess.TimeoutExpired(command, 120)

            datetime_patch, uuid_patch, log_patch = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                log_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    side_effect=run_scan,
                ),
            ):
                success = scanner_module.do_scan(config)

            self.assertFalse(success)
            artifacts = list(batch_dir.iterdir())
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0].read_bytes(), b"complete page")

    def test_scan_execution_error_cleans_incomplete_page_and_is_reported(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)
            outfile = expected_scan_path(consume_dir)
            batch_dir = batch_directory(consume_dir)

            def fail_scan(command, **options):
                pattern = next(
                    argument.split("=", 1)[1]
                    for argument in command
                    if argument.startswith("--batch=")
                )
                partial = Path(options["cwd"]) / f"{pattern % 1}.part"
                partial.write_bytes(b"partial")
                raise OSError("scanimage failed")

            datetime_patch, uuid_patch, _ = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    side_effect=fail_scan,
                ),
                mock.patch.object(scanner_module, "log") as log,
            ):
                success = scanner_module.do_scan(config)

            self.assertFalse(success)
            self.assertFalse(outfile.exists())
            self.assertFalse(batch_dir.exists())
            log.assert_any_call("Scan error: scanimage failed", None)

    def test_two_page_pdf_is_merged_converted_and_delivered(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)
            config["format"] = "pdf"
            outfile = Path(consume_dir) / (
                f"scan_{SCAN_TIMESTAMP}_{SCAN_IDENTIFIER}.pdf"
            )
            scanfile = Path(f"{outfile}.partial")
            batch_dir = batch_directory(consume_dir, "pdf")
            pages = [
                batch_dir / "page-0001.tiff.partial",
                batch_dir / "page-0002.tiff.partial",
            ]
            merged_source = Path(f"{scanfile}.source.tiff")

            def run_command(command, **options):
                if command[0] == "scanimage":
                    write_batch_pages(command, options, b"one", b"two")
                elif command[0] == "tiffcp":
                    Path(command[-1]).write_bytes(b"merged tiff")
                elif command[0] == "tiff2pdf":
                    self.assertEqual(
                        Path(command[-1]).read_bytes(),
                        b"merged tiff",
                    )
                    Path(command[2]).write_bytes(b"%PDF-converted")
                else:
                    self.fail(f"unexpected command: {command}")
                return SimpleNamespace(returncode=0, stderr="")

            datetime_patch, uuid_patch, log_patch = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                log_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    side_effect=run_command,
                ) as run,
            ):
                success = scanner_module.do_scan(config)

            self.assertTrue(success)
            self.assertEqual(outfile.read_bytes(), b"%PDF-converted")
            self.assertFalse(batch_dir.exists())
            self.assertFalse(merged_source.exists())
            self.assertEqual(
                run.call_args_list[1:],
                [
                    mock.call(
                        [
                            "tiffcp",
                            *map(str, pages),
                            str(merged_source),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=120,
                    ),
                    mock.call(
                        [
                            "tiff2pdf",
                            "-o",
                            str(scanfile),
                            str(merged_source),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=120,
                    ),
                ],
            )

    def test_assembly_failure_retains_captured_pages(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)
            outfile = expected_scan_path(consume_dir)
            scanfile = expected_partial_path(consume_dir)
            batch_dir = batch_directory(consume_dir)

            def run_command(command, **options):
                if command[0] == "scanimage":
                    write_batch_pages(command, options, b"one", b"two")
                    return SimpleNamespace(returncode=0, stderr="")
                Path(command[-1]).write_bytes(b"incomplete assembly")
                return SimpleNamespace(returncode=1, stderr="tiffcp failed")

            datetime_patch, uuid_patch, _ = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    side_effect=run_command,
                ),
                mock.patch.object(scanner_module, "log") as log,
            ):
                success = scanner_module.do_scan(config)

            self.assertFalse(success)
            self.assertFalse(outfile.exists())
            self.assertFalse(scanfile.exists())
            self.assertTrue(batch_dir.exists())
            self.assertEqual(
                [page.read_bytes() for page in sorted(batch_dir.iterdir())],
                [b"one", b"two"],
            )
            log.assert_any_call(
                f"Captured pages retained in {batch_dir}",
                None,
            )

    def test_multiple_pages_require_a_document_format(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)
            config["format"] = "png"
            outfile = Path(consume_dir) / (
                f"scan_{SCAN_TIMESTAMP}_{SCAN_IDENTIFIER}.png"
            )
            batch_dir = batch_directory(consume_dir, "png")

            def run_scan(command, **options):
                write_batch_pages(command, options, b"one", b"two")
                return SimpleNamespace(returncode=0, stderr="")

            datetime_patch, uuid_patch, _ = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    side_effect=run_scan,
                ),
                mock.patch.object(scanner_module, "log") as log,
            ):
                success = scanner_module.do_scan(config)

            self.assertFalse(success)
            self.assertFalse(outfile.exists())
            self.assertTrue(batch_dir.exists())
            log.assert_any_call(
                "Cannot assemble 2 pages as png; use FORMAT=tiff or FORMAT=pdf",
                None,
            )

    def test_single_png_page_is_delivered_without_conversion(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)
            config["format"] = "png"
            outfile = Path(consume_dir) / (
                f"scan_{SCAN_TIMESTAMP}_{SCAN_IDENTIFIER}.png"
            )

            def run_scan(command, **options):
                write_batch_pages(command, options, b"png data")
                return SimpleNamespace(returncode=0, stderr="")

            datetime_patch, uuid_patch, log_patch = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                log_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    side_effect=run_scan,
                ),
            ):
                success = scanner_module.do_scan(config)

            self.assertTrue(success)
            self.assertEqual(outfile.read_bytes(), b"png data")

    def test_api_mode_removes_staged_file_after_successful_upload(self):
        with tempfile.TemporaryDirectory() as staging_dir:
            config = scan_config(staging_dir, delivery_mode="api")
            outfile = expected_scan_path(staging_dir)

            def run_scan(command, **options):
                write_batch_pages(command, options, b"scan data")
                return SimpleNamespace(returncode=0, stderr="")

            datetime_patch, uuid_patch, log_patch = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                log_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    side_effect=run_scan,
                ),
                mock.patch.object(
                    scanner_module,
                    "upload_to_paperless",
                    return_value=True,
                ) as upload,
            ):
                success = scanner_module.do_scan(config)

            self.assertTrue(success)
            upload.assert_called_once_with(config, str(outfile))
            self.assertFalse(outfile.exists())

    def test_api_mode_marks_copy_when_accepted_file_cannot_be_removed(self):
        with tempfile.TemporaryDirectory() as staging_dir:
            config = scan_config(staging_dir, delivery_mode="api")
            outfile = expected_scan_path(staging_dir)
            accepted_file = Path(f"{outfile}.accepted")
            real_remove = os.remove

            def run_scan(command, **options):
                write_batch_pages(command, options, b"scan data")
                return SimpleNamespace(returncode=0, stderr="")

            def remove_file(path):
                if os.fspath(path) == str(outfile):
                    raise OSError("read-only")
                return real_remove(path)

            datetime_patch, uuid_patch, _ = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    side_effect=run_scan,
                ),
                mock.patch.object(
                    scanner_module,
                    "upload_to_paperless",
                    return_value=True,
                ),
                mock.patch.object(
                    scanner_module.os,
                    "remove",
                    side_effect=remove_file,
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

    def test_api_mode_retains_secure_document_after_failed_upload(self):
        with tempfile.TemporaryDirectory() as staging_dir:
            config = scan_config(staging_dir, delivery_mode="api")
            outfile = expected_scan_path(staging_dir)

            def run_scan(command, **options):
                write_batch_pages(command, options, b"scan data")
                return SimpleNamespace(returncode=0, stderr="")

            datetime_patch, uuid_patch, _ = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    side_effect=run_scan,
                ),
                mock.patch.object(
                    scanner_module,
                    "upload_to_paperless",
                    return_value=False,
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

    def test_api_capture_failure_does_not_attempt_upload(self):
        with tempfile.TemporaryDirectory() as staging_dir:
            config = scan_config(staging_dir, delivery_mode="api")
            outfile = expected_scan_path(staging_dir)

            datetime_patch, uuid_patch, log_patch = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                log_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    return_value=SimpleNamespace(
                        returncode=1,
                        stderr="device error",
                    ),
                ),
                mock.patch.object(
                    scanner_module,
                    "upload_to_paperless",
                ) as upload,
            ):
                success = scanner_module.do_scan(config)

            self.assertFalse(success)
            upload.assert_not_called()
            self.assertFalse(outfile.exists())

    def test_interrupted_scan_retains_private_batch_artifact(self):
        with tempfile.TemporaryDirectory() as staging_dir:
            config = scan_config(staging_dir, delivery_mode="api")
            outfile = expected_scan_path(staging_dir)
            batch_dir = batch_directory(staging_dir)

            def interrupt_scan(command, **options):
                pattern = next(
                    argument.split("=", 1)[1]
                    for argument in command
                    if argument.startswith("--batch=")
                )
                partial = Path(options["cwd"]) / f"{pattern % 1}.part"
                partial.write_bytes(b"incomplete")
                raise KeyboardInterrupt

            datetime_patch, uuid_patch, log_patch = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                log_patch,
                mock.patch.object(
                    scanner_module.subprocess,
                    "run",
                    side_effect=interrupt_scan,
                ),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    scanner_module.do_scan(config)

            self.assertFalse(outfile.exists())
            self.assertTrue(batch_dir.exists())
            self.assertEqual(batch_dir.stat().st_mode & 0o777, 0o700)
            artifacts = list(batch_dir.iterdir())
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0].read_bytes(), b"incomplete")

    def test_batch_directory_creation_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as consume_dir:
            config = scan_config(consume_dir)

            datetime_patch, uuid_patch, _ = self.scan_patches()
            with (
                datetime_patch,
                uuid_patch,
                mock.patch.object(
                    scanner_module.os,
                    "mkdir",
                    side_effect=OSError("read-only"),
                ),
                mock.patch.object(scanner_module.subprocess, "run") as run,
                mock.patch.object(scanner_module, "log") as log,
            ):
                success = scanner_module.do_scan(config)

            self.assertFalse(success)
            run.assert_not_called()
            log.assert_any_call(
                "Could not create secure scan batch directory: read-only",
                None,
            )

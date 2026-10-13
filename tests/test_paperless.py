import http.server
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import brother_scan_to_paperless.paperless as paperless_module


class PaperlessUploadTests(unittest.TestCase):
    def upload_config(self, url="https://paperless.example/paperless/"):
        return {
            "paperless_url": url,
            "paperless_token": "api-secret",
            "paperless_api_timeout": 17,
            "paperless_allow_insecure_http": url.startswith("http://"),
            "log_file": None,
        }

    def test_upload_target_preserves_reverse_proxy_path(self):
        parsed, path = paperless_module.paperless_upload_target(
            "https://paperless.example:8443/paperless/"
        )

        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.hostname, "paperless.example")
        self.assertEqual(parsed.port, 8443)
        self.assertEqual(
            path,
            "/paperless/api/documents/post_document/",
        )

    def test_upload_target_rejects_unsafe_or_malformed_urls(self):
        cases = {
            "ftp://paperless.example": "http or https",
            "https://user:pass@paperless.example": "credentials",
            "https://paperless.example?next=elsewhere": "query or fragment",
            "https://paperless.example/#fragment": "query or fragment",
            "https://paperless.example:invalid": "invalid port",
        }

        for url, message in cases.items():
            with (
                self.subTest(url=url),
                self.assertRaisesRegex(
                    ValueError,
                    message,
                ),
            ):
                paperless_module.paperless_upload_target(url)

    def test_upload_streams_multipart_with_auth_and_logs_task(self):
        payload = b"a" * (64 * 1024) + b"tail"
        task_id = "7de8f6b1-5ee8-4dec-a0a4-bac26d462f9d"
        response = mock.Mock(status=200)
        response.read.return_value = json.dumps(task_id).encode("ascii")
        response.getheader.return_value = "application/json; charset=utf-8"
        connection = mock.Mock()
        connection.getresponse.return_value = response
        upload_uuid = SimpleNamespace(hex="0123456789abcdef")

        with tempfile.TemporaryDirectory() as temp_dir:
            document = Path(temp_dir) / "scan.tiff"
            document.write_bytes(payload)
            with (
                mock.patch.object(
                    paperless_module.http.client,
                    "HTTPSConnection",
                    return_value=connection,
                ) as connection_class,
                mock.patch.object(
                    paperless_module.uuid,
                    "uuid4",
                    return_value=upload_uuid,
                ),
                mock.patch.object(
                    paperless_module,
                    "log",
                ) as log,
            ):
                success = paperless_module.upload_to_paperless(
                    self.upload_config("https://paperless.example:8443/paperless/"),
                    str(document),
                )

        self.assertTrue(success)
        connection_class.assert_called_once_with(
            "paperless.example",
            port=8443,
            timeout=17,
        )
        connection.putrequest.assert_called_once_with(
            "POST",
            "/paperless/api/documents/post_document/",
        )
        headers = {
            call.args[0]: call.args[1] for call in connection.putheader.call_args_list
        }
        boundary = "brother-scan-0123456789abcdef"
        self.assertEqual(headers["Authorization"], "Token api-secret")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(
            headers["Content-Type"],
            f"multipart/form-data; boundary={boundary}",
        )
        sent_chunks = [call.args[0] for call in connection.send.call_args_list]
        self.assertEqual(sent_chunks[1], payload[: 64 * 1024])
        self.assertEqual(sent_chunks[2], b"tail")
        multipart_body = b"".join(sent_chunks)
        self.assertEqual(headers["Content-Length"], str(len(multipart_body)))
        self.assertIn(b'name="document"; filename="scan.tiff"', multipart_body)
        self.assertIn(payload, multipart_body)
        self.assertTrue(
            multipart_body.endswith(f"\r\n--{boundary}--\r\n".encode("ascii"))
        )
        response.read.assert_called_once_with(64 * 1024)
        log.assert_called_once_with(
            f"Paperless accepted scan for consumption (task {task_id})",
            None,
        )
        connection.close.assert_called_once_with()

    def test_upload_sends_one_tags_field_per_tag_id(self):
        response = mock.Mock(status=200)
        response.read.return_value = b'"OK"'
        response.getheader.return_value = "application/json"
        connection = mock.Mock()
        connection.getresponse.return_value = response
        upload_uuid = SimpleNamespace(hex="0123456789abcdef")
        config = dict(self.upload_config(), paperless_tags=[3, 14])

        with tempfile.TemporaryDirectory() as temp_dir:
            document = Path(temp_dir) / "scan.pdf"
            document.write_bytes(b"scan")
            with (
                mock.patch.object(
                    paperless_module.http.client,
                    "HTTPSConnection",
                    return_value=connection,
                ),
                mock.patch.object(
                    paperless_module.uuid,
                    "uuid4",
                    return_value=upload_uuid,
                ),
                mock.patch.object(paperless_module, "log"),
            ):
                success = paperless_module.upload_to_paperless(
                    config,
                    str(document),
                )

        self.assertTrue(success)
        headers = {
            call.args[0]: call.args[1] for call in connection.putheader.call_args_list
        }
        multipart_body = b"".join(
            call.args[0] for call in connection.send.call_args_list
        )
        boundary = b"brother-scan-0123456789abcdef"
        self.assertEqual(headers["Content-Length"], str(len(multipart_body)))
        self.assertTrue(
            multipart_body.startswith(
                b"--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="tags"\r\n\r\n3\r\n'
                b"--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="tags"\r\n\r\n14\r\n'
                b"--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="document"'
            )
        )

    def test_upload_reports_http_error_and_closes_connection(self):
        response = mock.Mock(status=401)
        response.read.return_value = b'  {"detail":  "bad token api-secret"}\n'
        connection = mock.Mock()
        connection.getresponse.return_value = response

        with tempfile.TemporaryDirectory() as temp_dir:
            document = Path(temp_dir) / "scan.tiff"
            document.write_bytes(b"scan")
            with (
                mock.patch.object(
                    paperless_module.http.client,
                    "HTTPConnection",
                    return_value=connection,
                ),
                mock.patch.object(paperless_module, "log") as log,
            ):
                success = paperless_module.upload_to_paperless(
                    self.upload_config("http://paperless.example:8000"),
                    str(document),
                )

        self.assertFalse(success)
        log.assert_called_once_with(
            'Paperless upload failed: HTTP 401: {"detail": "bad token [redacted]"}',
            None,
        )
        connection.close.assert_called_once_with()

    def test_upload_accepts_legacy_ok_response_without_task_id(self):
        response = mock.Mock(status=200)
        response.read.return_value = b'"OK"'
        response.getheader.return_value = "application/json"
        connection = mock.Mock()
        connection.getresponse.return_value = response

        with tempfile.TemporaryDirectory() as temp_dir:
            document = Path(temp_dir) / "scan.tiff"
            document.write_bytes(b"scan")
            with (
                mock.patch.object(
                    paperless_module.http.client,
                    "HTTPConnection",
                    return_value=connection,
                ),
                mock.patch.object(paperless_module, "log") as log,
            ):
                success = paperless_module.upload_to_paperless(
                    self.upload_config("http://paperless.example"),
                    str(document),
                )

        self.assertTrue(success)
        log.assert_called_once_with(
            "Paperless accepted scan for consumption",
            None,
        )
        connection.close.assert_called_once_with()

    def test_upload_rejects_malformed_success_responses(self):
        cases = (
            (b"<html>proxy error</html>", "text/html", "content type"),
            (b"not json", "application/json", "success response"),
            (b"{}", "application/json", "success response"),
            (b'"not-a-task-id"', "application/json", "success response"),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            document = Path(temp_dir) / "scan.tiff"
            document.write_bytes(b"scan")

            for body, content_type, message in cases:
                response = mock.Mock(status=200)
                response.read.return_value = body
                response.getheader.return_value = content_type
                connection = mock.Mock()
                connection.getresponse.return_value = response

                with (
                    self.subTest(body=body),
                    mock.patch.object(
                        paperless_module.http.client,
                        "HTTPSConnection",
                        return_value=connection,
                    ),
                    mock.patch.object(paperless_module, "log") as log,
                ):
                    success = paperless_module.upload_to_paperless(
                        self.upload_config(),
                        str(document),
                    )

                self.assertFalse(success)
                self.assertIn(message, log.call_args.args[0])
                connection.close.assert_called_once_with()

    def test_upload_rejects_plaintext_http_without_opt_in(self):
        config = self.upload_config("http://paperless.example")
        config["paperless_allow_insecure_http"] = False

        with tempfile.TemporaryDirectory() as temp_dir:
            document = Path(temp_dir) / "scan.tiff"
            document.write_bytes(b"scan")
            with (
                mock.patch.object(
                    paperless_module.http.client,
                    "HTTPConnection",
                ) as connection,
                mock.patch.object(paperless_module, "log") as log,
            ):
                success = paperless_module.upload_to_paperless(config, str(document))

        self.assertFalse(success)
        connection.assert_not_called()
        log.assert_called_once_with(
            "Paperless upload error: paperless_url must use https unless "
            "paperless_allow_insecure_http is true",
            None,
        )

    def test_upload_against_local_http_endpoint(self):
        received = {}
        task_id = "6c5f3859-7a47-4bad-a103-f3f19f7696a8"

        class UploadHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                received["path"] = self.path
                received["authorization"] = self.headers["Authorization"]
                received["content_type"] = self.headers["Content-Type"]
                received["body"] = self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(task_id).encode("ascii"))

            def log_message(self, format, *args):
                pass

        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            UploadHandler,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                document = Path(temp_dir) / "local scan.tiff"
                document.write_bytes(b"local endpoint payload")
                config = self.upload_config(
                    f"http://127.0.0.1:{server.server_port}/paperless"
                )

                with mock.patch.object(paperless_module, "log"):
                    success = paperless_module.upload_to_paperless(
                        config,
                        str(document),
                    )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertTrue(success)
        self.assertEqual(
            received["path"],
            "/paperless/api/documents/post_document/",
        )
        self.assertEqual(received["authorization"], "Token api-secret")
        self.assertIn("multipart/form-data", received["content_type"])
        self.assertIn(b'filename="local scan.tiff"', received["body"])
        self.assertIn(b"local endpoint payload", received["body"])

    def test_upload_reports_network_error_and_closes_connection(self):
        connection = mock.Mock()
        connection.putrequest.side_effect = OSError("connection reset for api-secret")

        with tempfile.TemporaryDirectory() as temp_dir:
            document = Path(temp_dir) / "scan.tiff"
            document.write_bytes(b"scan")
            with (
                mock.patch.object(
                    paperless_module.http.client,
                    "HTTPConnection",
                    return_value=connection,
                ),
                mock.patch.object(paperless_module, "log") as log,
            ):
                success = paperless_module.upload_to_paperless(
                    self.upload_config("http://paperless.example"),
                    str(document),
                )

        self.assertFalse(success)
        log.assert_called_once_with(
            "Paperless upload error: connection reset for [redacted]",
            None,
        )
        connection.close.assert_called_once_with()

    def test_upload_rejects_document_symlink(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            document = Path(temp_dir) / "scan.tiff"
            document.write_bytes(b"scan")
            document_link = Path(temp_dir) / "scan-link.tiff"
            document_link.symlink_to(document)

            with (
                mock.patch.object(
                    paperless_module.http.client,
                    "HTTPSConnection",
                ) as connection,
                mock.patch.object(paperless_module, "log"),
            ):
                success = paperless_module.upload_to_paperless(
                    self.upload_config(),
                    str(document_link),
                )

        self.assertFalse(success)
        connection.assert_not_called()

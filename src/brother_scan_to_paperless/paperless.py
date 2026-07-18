"""Paperless-ngx API delivery."""

import http.client
import json
import mimetypes
import os
import stat
import uuid
from urllib.parse import urlsplit

from . import __version__
from .logger import log


def paperless_upload_target(base_url: str):
    """Return the parsed Paperless base URL and document upload path."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("paperless_url must be an http or https URL")
    if parsed.username or parsed.password:
        raise ValueError("paperless_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("paperless_url must not contain a query or fragment")

    try:
        # Accessing port validates an explicitly supplied port number.
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("paperless_url has an invalid port") from exc
    base_path = parsed.path.rstrip("/")
    return parsed, f"{base_path}/api/documents/post_document/"


def redact_token(config: dict, message: str) -> str:
    """Remove the configured API token from an error message."""
    token = config.get("paperless_token", "")
    return message.replace(token, "[redacted]") if token else message


def upload_to_paperless(config: dict, document_path: str) -> bool:
    """Stream a scanned document to Paperless's asynchronous upload API."""
    log_file = config.get("log_file")
    connection = None
    document_fd = None

    try:
        parsed, request_path = paperless_upload_target(config["paperless_url"])
        if (
            parsed.scheme == "http"
            and config.get("paperless_allow_insecure_http") is not True
        ):
            raise ValueError(
                "paperless_url must use https unless "
                "paperless_allow_insecure_http is true"
            )
        document_fd = os.open(
            document_path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        document_stat = os.fstat(document_fd)
        if not stat.S_ISREG(document_stat.st_mode):
            raise ValueError("the staged document must be a regular file")

        timeout = config["paperless_api_timeout"]
        boundary = f"brother-scan-{uuid.uuid4().hex}"
        filename = os.path.basename(document_path)
        safe_filename = (
            filename.replace("\\", "_")
            .replace('"', "_")
            .replace("\r", "")
            .replace("\n", "")
        )
        content_type = (
            mimetypes.guess_type(filename)[0] or "application/octet-stream"
        )
        preamble = (
            f"--{boundary}\r\n"
            "Content-Disposition: form-data; name=\"document\"; "
            f"filename=\"{safe_filename}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
        closing = f"\r\n--{boundary}--\r\n".encode("ascii")
        content_length = len(preamble) + document_stat.st_size + len(closing)

        connection_class = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_class(
            parsed.hostname,
            port=parsed.port,
            timeout=timeout,
        )
        connection.putrequest("POST", request_path)
        connection.putheader(
            "Authorization", f"Token {config['paperless_token']}"
        )
        connection.putheader("Accept", "application/json")
        connection.putheader(
            "Content-Type", f"multipart/form-data; boundary={boundary}"
        )
        connection.putheader("Content-Length", str(content_length))
        connection.putheader(
            "User-Agent", f"brother-scan-to-paperless/{__version__}"
        )
        connection.endheaders()
        connection.send(preamble)
        document = os.fdopen(document_fd, "rb")
        document_fd = None
        with document:
            while chunk := document.read(64 * 1024):
                connection.send(chunk)
        connection.send(closing)

        response = connection.getresponse()
        response_body = response.read(64 * 1024)
        if response.status != 200:
            detail = " ".join(
                response_body.decode("utf-8", errors="replace").split()
            )
            detail = redact_token(config, detail)[:200]
            suffix = f": {detail}" if detail else ""
            log(
                f"Paperless upload failed: HTTP {response.status}{suffix}",
                log_file,
            )
            return False

        response_content_type = response.getheader("Content-Type", "") or ""
        if (
            response_content_type.split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            log(
                "Paperless upload failed: invalid success content type",
                log_file,
            )
            return False

        try:
            response_data = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            log("Paperless upload failed: invalid success response", log_file)
            return False

        if response_data == "OK":
            log("Paperless accepted scan for consumption", log_file)
            return True

        try:
            task_id = str(uuid.UUID(response_data))
        except (AttributeError, TypeError, ValueError):
            log("Paperless upload failed: invalid success response", log_file)
            return False

        if task_id == response_data.lower():
            log(
                "Paperless accepted scan for consumption "
                f"(task {task_id})",
                log_file,
            )
            return True

        log("Paperless upload failed: invalid success response", log_file)
        return False

    except (OSError, http.client.HTTPException, ValueError) as exc:
        detail = redact_token(config, str(exc))
        log(f"Paperless upload error: {detail}", log_file)
        return False
    finally:
        if document_fd is not None:
            os.close(document_fd)
        if connection is not None:
            connection.close()

"""Scan capture and delivery lifecycle."""

import os
import subprocess
import uuid
from datetime import datetime

from .logger import log
from .paperless import upload_to_paperless


MULTIPAGE_FORMATS = {"pdf", "tiff"}


def _document_paths(config: dict) -> tuple[str, str]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    unique_id = uuid.uuid4().hex[:8]
    output_dir = (
        config["staging_dir"]
        if config.get("delivery_mode") == "api"
        else config["consume_dir"]
    )
    outfile = os.path.join(
        output_dir,
        f"scan_{timestamp}_{unique_id}.{config['format']}",
    )
    return outfile, f"{outfile}.partial"


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _cleanup_batch(batch_dir: str, log_file: str | None) -> None:
    """Remove a private batch directory and any scan artifacts in it."""
    try:
        entries = list(os.scandir(batch_dir))
    except FileNotFoundError:
        return
    except OSError as exc:
        log(f"Could not inspect scan batch for cleanup: {exc}", log_file)
        return

    for entry in entries:
        try:
            os.remove(entry.path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log(
                f"Could not remove scan batch artifact {entry.path}: {exc}",
                log_file,
            )

    try:
        os.rmdir(batch_dir)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log(
            f"Could not remove scan batch directory {batch_dir}: {exc}",
            log_file,
        )


def _batch_pages(batch_dir: str, output_format: str) -> list[str]:
    """Return completed, non-empty batch pages in scanner order."""
    prefix = "page-"
    suffix = f".{output_format}.partial"
    pages = []

    for entry in os.scandir(batch_dir):
        if not entry.is_file(follow_symlinks=False):
            continue
        if not entry.name.startswith(prefix) or not entry.name.endswith(suffix):
            continue
        page_number = entry.name[len(prefix) : -len(suffix)]
        if not page_number.isdecimal():
            continue
        if entry.stat(follow_symlinks=False).st_size <= 0:
            continue
        pages.append((int(page_number), entry.path))

    pages.sort(key=lambda page: page[0])
    return [path for _, path in pages]


def _retain_completed_batch(
    batch_dir: str,
    pages: list[str],
    log_file: str | None,
) -> None:
    """Keep completed pages but discard incomplete batch artifacts."""
    if not pages:
        _cleanup_batch(batch_dir, log_file)
        return

    retained_names = {os.path.basename(path) for path in pages}
    try:
        entries = list(os.scandir(batch_dir))
    except OSError as exc:
        log(f"Could not inspect incomplete scan batch: {exc}", log_file)
        return

    for entry in entries:
        if entry.name in retained_names:
            continue
        try:
            os.remove(entry.path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log(
                f"Could not remove incomplete scan artifact "
                f"{entry.path}: {exc}",
                log_file,
            )

    log(f"Captured pages retained in {batch_dir}", log_file)


def _handle_incomplete_batch(
    batch_dir: str,
    output_format: str,
    log_file: str | None,
    pages: list[str] | None = None,
) -> None:
    if pages is None:
        try:
            pages = _batch_pages(batch_dir, output_format)
        except OSError:
            pages = []
    _retain_completed_batch(batch_dir, pages, log_file)


def _capture_pages(
    config: dict,
    batch_dir: str,
    output_format: str,
    page_limit: int | None = None,
) -> list[str]:
    """Capture every page exposed by one scanner-side SANE job."""
    log_file = config.get("log_file")
    page_pattern = f"page-%04d.{output_format}.partial"
    command = [
        "scanimage",
        f"--device-name={config['scanner_device']}",
        f"--resolution={config['resolution']}",
        f"--format={output_format}",
        f"--batch={page_pattern}",
    ]
    if page_limit is not None:
        command.append(f"--batch-count={page_limit}")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=config["scan_timeout"],
            cwd=batch_dir,
        )
        pages = _batch_pages(batch_dir, output_format)
        artifacts = list(os.scandir(batch_dir))

        if result.returncode == 0 and pages and len(pages) == len(artifacts):
            log(
                f"Scan batch captured {len(pages)} "
                f"page{'s' if len(pages) != 1 else ''}",
                log_file,
            )
            return pages

        detail = " ".join((result.stderr or "").split())
        log(
            f"Scan failed: exit={result.returncode} stderr={detail}",
            log_file,
        )
        _handle_incomplete_batch(
            batch_dir,
            output_format,
            log_file,
            pages,
        )
        return []
    except subprocess.TimeoutExpired:
        log(f"Scan timed out after {config['scan_timeout']}s", log_file)
        _handle_incomplete_batch(batch_dir, output_format, log_file)
        return []
    except Exception as exc:
        log(f"Scan error: {exc}", log_file)
        _handle_incomplete_batch(batch_dir, output_format, log_file)
        return []


def _mark_accepted_scan(config: dict, outfile: str) -> None:
    log_file = config.get("log_file")
    try:
        os.remove(outfile)
    except OSError as exc:
        accepted_file = f"{outfile}.accepted"
        try:
            os.replace(outfile, accepted_file)
        except OSError as mark_exc:
            log(
                "Paperless accepted the scan, but the staged file could not "
                f"be removed or marked as accepted: {exc}; {mark_exc}",
                log_file,
            )
        else:
            log(
                "Paperless accepted the scan; retained local copy marked at "
                f"{accepted_file}: {exc}",
                log_file,
            )


def _deliver_scan(config: dict, scanfile: str, outfile: str) -> bool:
    """Publish a completed partial file to the configured destination."""
    log_file = config.get("log_file")
    try:
        size = os.path.getsize(scanfile)
        os.replace(scanfile, outfile)
        log(f"Scan captured: {outfile} ({size:,} bytes)", log_file)

        if config.get("delivery_mode") == "api":
            if not upload_to_paperless(config, outfile):
                log(f"Upload failed; retained scan at {outfile}", log_file)
                return False
            _mark_accepted_scan(config, outfile)
            return True

        log(f"Scan complete: {outfile} ({size:,} bytes)", log_file)
        return True
    except Exception as exc:
        log(f"Scan error: {exc}", log_file)
        return False


def _run_document_tool(
    config: dict,
    command: list[str],
    output_path: str,
) -> bool:
    log_file = config.get("log_file")
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=config["scan_timeout"],
        )
    except subprocess.TimeoutExpired:
        log(
            f"Document assembly timed out after {config['scan_timeout']}s",
            log_file,
        )
        _remove_file(output_path)
        return False
    except OSError as exc:
        log(f"Document assembly error: {exc}", log_file)
        _remove_file(output_path)
        return False

    if (
        result.returncode == 0
        and os.path.exists(output_path)
        and os.path.getsize(output_path) > 0
    ):
        return True

    detail = " ".join((result.stderr or "").split())
    suffix = f": {detail[:200]}" if detail else ""
    log(
        f"Document assembly failed: exit={result.returncode}{suffix}",
        log_file,
    )
    _remove_file(output_path)
    return False


def _assemble_tiff(config: dict, pages: list[str], scanfile: str) -> bool:
    if len(pages) == 1:
        try:
            os.replace(pages[0], scanfile)
        except OSError as exc:
            log(
                f"Document assembly error: {exc}",
                config.get("log_file"),
            )
            return False
        return True

    return _run_document_tool(
        config,
        ["tiffcp", *pages, scanfile],
        scanfile,
    )


def _assemble_pdf(config: dict, pages: list[str], scanfile: str) -> bool:
    source_path = pages[0]
    merged_source = f"{scanfile}.source.tiff"
    if len(pages) > 1:
        if not _run_document_tool(
            config,
            ["tiffcp", *pages, merged_source],
            merged_source,
        ):
            return False
        source_path = merged_source

    try:
        return _run_document_tool(
            config,
            ["tiff2pdf", "-o", scanfile, source_path],
            scanfile,
        )
    finally:
        _remove_file(merged_source)


def _assemble_pages(
    config: dict,
    pages: list[str],
    scanfile: str,
    output_format: str,
) -> bool:
    if output_format == "tiff":
        return _assemble_tiff(config, pages, scanfile)
    if output_format == "pdf":
        return _assemble_pdf(config, pages, scanfile)
    if len(pages) == 1:
        try:
            os.replace(pages[0], scanfile)
        except OSError as exc:
            log(
                f"Document assembly error: {exc}",
                config.get("log_file"),
            )
            return False
        return True

    log(
        f"Cannot assemble {len(pages)} pages as {output_format}; "
        "use FORMAT=tiff or FORMAT=pdf",
        config.get("log_file"),
    )
    return False


def do_scan(config: dict, *, page_limit: int | None = None) -> bool:
    """Capture and deliver one complete scanner-side document."""
    outfile, scanfile = _document_paths(config)
    log_file = config.get("log_file")
    output_format = config["format"].lower()
    capture_format = (
        "tiff" if output_format in MULTIPAGE_FORMATS else output_format
    )
    batch_dir = f"{outfile}.batch"

    log(f"Starting scan -> {outfile}", log_file)
    try:
        os.mkdir(batch_dir, mode=0o700)
    except OSError as exc:
        log(f"Could not create secure scan batch directory: {exc}", log_file)
        return False

    pages = _capture_pages(
        config,
        batch_dir,
        capture_format,
        page_limit,
    )
    if not pages:
        return False

    if not _assemble_pages(config, pages, scanfile, output_format):
        log(f"Captured pages retained in {batch_dir}", log_file)
        return False

    if config.get("delivery_mode") == "api":
        try:
            os.chmod(scanfile, 0o600)
        except OSError as exc:
            log(f"Document assembly error: {exc}", log_file)
            return False

    _cleanup_batch(batch_dir, log_file)
    log(f"Assembled {len(pages)}-page scan -> {outfile}", log_file)
    return _deliver_scan(config, scanfile, outfile)

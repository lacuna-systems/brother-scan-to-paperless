"""Scan capture and delivery lifecycle."""

import os
import subprocess
import uuid
from datetime import datetime

from .logger import log
from .paperless import upload_to_paperless


def do_scan(config: dict) -> bool:
    """Run scanimage and deliver the completed document."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    unique_id = uuid.uuid4().hex[:8]
    ext = config["format"]
    output_dir = (
        config["staging_dir"]
        if config.get("delivery_mode") == "api"
        else config["consume_dir"]
    )
    outfile = os.path.join(
        output_dir, f"scan_{timestamp}_{unique_id}.{ext}"
    )
    scanfile = f"{outfile}.partial"
    log_file = config.get("log_file")
    scan_complete = False
    scan_descriptor = None

    if config.get("delivery_mode") == "api":
        try:
            scan_descriptor = os.open(
                scanfile,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except OSError as exc:
            log(f"Could not create secure staging file: {exc}", log_file)
            return False

    log(f"Starting scan -> {outfile}", log_file)

    try:
        cmd = [
            "scanimage",
            f"--device-name={config['scanner_device']}",
            f"--resolution={config['resolution']}",
            f"--format={config['format']}",
        ]

        if config.get("delivery_mode") == "api":
            try:
                result = subprocess.run(
                    cmd,
                    stdout=scan_descriptor,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=config["scan_timeout"],
                )
            finally:
                descriptor = scan_descriptor
                scan_descriptor = None
                os.close(descriptor)
        else:
            cmd.append(f"--output-file={scanfile}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=config["scan_timeout"],
            )

        if (
            result.returncode == 0
            and os.path.exists(scanfile)
            and os.path.getsize(scanfile) > 0
        ):
            size = os.path.getsize(scanfile)
            os.replace(scanfile, outfile)
            scan_complete = True
            log(f"Scan captured: {outfile} ({size:,} bytes)", log_file)
            if config.get("delivery_mode") == "api":
                if not upload_to_paperless(config, outfile):
                    log(
                        f"Upload failed; retained scan at {outfile}",
                        log_file,
                    )
                    return False
                try:
                    os.remove(outfile)
                except OSError as exc:
                    accepted_file = f"{outfile}.accepted"
                    try:
                        os.replace(outfile, accepted_file)
                    except OSError as mark_exc:
                        log(
                            "Paperless accepted the scan, but the staged "
                            "file could not be removed or marked as accepted: "
                            f"{exc}; {mark_exc}",
                            log_file,
                        )
                    else:
                        log(
                            "Paperless accepted the scan; retained local "
                            f"copy marked at {accepted_file}: {exc}",
                            log_file,
                        )
                return True

            log(f"Scan complete: {outfile} ({size:,} bytes)", log_file)
            return True

        log(
            f"Scan failed: exit={result.returncode} "
            f"stderr={result.stderr.strip()}",
            log_file,
        )
        if os.path.exists(scanfile):
            os.remove(scanfile)
        return False

    except subprocess.TimeoutExpired:
        log(f"Scan timed out after {config['scan_timeout']}s", log_file)
        if os.path.exists(scanfile):
            os.remove(scanfile)
        return False
    except Exception as exc:
        log(f"Scan error: {exc}", log_file)
        if not scan_complete and os.path.exists(scanfile):
            os.remove(scanfile)
        return False
    finally:
        if scan_descriptor is not None:
            os.close(scan_descriptor)

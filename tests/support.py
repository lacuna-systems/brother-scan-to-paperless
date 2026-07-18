"""Shared test helpers and source-path bootstrap."""

import os
import sys
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


SCAN_TIMESTAMP = "20260102_030405_123456"
SCAN_IDENTIFIER = "deadbeef"


def scan_config(output_dir, delivery_mode="consume"):
    config = {
        "delivery_mode": delivery_mode,
        "format": "tiff",
        "log_file": None,
        "resolution": 300,
        "scanner_device": "brother5:net1;dev0",
        "scan_timeout": 120,
    }
    if delivery_mode == "api":
        config.update(
            {
                "staging_dir": output_dir,
                "paperless_url": "https://paperless.example",
                "paperless_token": "test-token",
                "paperless_api_timeout": 60,
                "paperless_allow_insecure_http": False,
            }
        )
    else:
        config["consume_dir"] = output_dir
    return config


def expected_scan_path(output_dir):
    return Path(output_dir) / (f"scan_{SCAN_TIMESTAMP}_{SCAN_IDENTIFIER}.tiff")


def expected_partial_path(output_dir):
    return Path(f"{expected_scan_path(output_dir)}.partial")


def write_api_scan(command, options, payload):
    if any(argument.startswith("--output-file=") for argument in command):
        raise AssertionError("API scans must stream to the secure descriptor")
    os.write(options["stdout"], payload)

"""Brother driver discovery, registration, and scan event handling."""

import os
import shutil
import socket
import subprocess

from .logger import log


def detect_host_ip() -> str | None:
    """Detect the host IP by opening a UDP socket to a public DNS."""
    try:
        route_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        route_socket.connect(("8.8.8.8", 80))
        ip = route_socket.getsockname()[0]
        route_socket.close()
        return ip
    except Exception:
        return None


def default_driver_version() -> str:
    """Use an explicit driver selection or detect an installed backend."""
    configured = os.environ.get("BRSCAN_VERSION")
    if configured:
        return configured
    for version in ("4", "5"):
        if shutil.which(f"brsaneconfig{version}"):
            return version
    return "4"


def detect_scanner_device(driver_version: str = "4") -> str | None:
    """Try to detect the scanner device name using Brother's config tool."""
    try:
        result = subprocess.run(
            [f"brsaneconfig{driver_version}", "-q"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("*") and "brother" in line.lower():
                return f"brother{driver_version}:net1;dev0"
        return None
    except Exception:
        return None


def register_profiles(
    printer_ip: str,
    host_ip: str,
    listen_port: int,
    display_name: str,
    log_file: str | None,
) -> bool:
    """Register scan profiles on the printer via SNMP."""
    oid = ".1.3.6.1.4.1.2435.2.3.9.2.11.1.1.0"
    community = "internal"

    functions = [
        ("IMAGE", 1),
        ("EMAIL", 2),
        ("OCR", 3),
        ("FILE", 5),
    ]

    cmd = ["snmpset", "-v1", "-c", community, printer_ip]
    for func, appnum in functions:
        profile = (
            f'TYPE=BR;BUTTON=SCAN;USER="{display_name}";'
            f"FUNC={func};HOST={host_ip}:{listen_port};"
            f"APPNUM={appnum};DURATION=360;BRID=;"
        )
        cmd.extend([oid, "s", profile])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            log("Registered scan profiles on printer", log_file)
            return True
        log(f"SNMP registration failed: {result.stderr.strip()}", log_file)
        return False
    except Exception as exc:
        log(f"SNMP registration error: {exc}", log_file)
        return False


def is_scan_event(message: str, sender_ip: str, printer_ip: str) -> bool:
    """Accept scan events only from the configured printer."""
    return sender_ip == printer_ip and "BUTTON=SCAN" in message

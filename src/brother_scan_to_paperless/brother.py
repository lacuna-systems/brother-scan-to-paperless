"""Brother driver discovery, registration, and scan event handling."""

import json
import os
import shutil
import socket
import ssl
import subprocess
from urllib.request import (
    HTTPDigestAuthHandler,
    HTTPPasswordMgrWithDefaultRealm,
    HTTPSHandler,
    Request,
    build_opener,
)

from .logger import log


class ScannerConfigurationError(RuntimeError):
    """Raised when a SANE scanner backend cannot be configured."""


PROFILE_OID = "1.3.6.1.4.1.2435.2.3.9.2.11.1.1.0"
PHOENIX_CAPABILITY_OID = ".1.3.6.1.4.1.2435.2.4.3.2435.5.39.6.0"
SCAN_PROFILES = (
    ("IMAGE", 1),
    ("EMAIL", 2),
    ("OCR", 3),
    ("FILE", 5),
)
PHOENIX_SCAN_PROFILES = (
    ("IMAGE", 1),
    ("OCR", 3),
    ("EMAIL", 2),
    ("FILE", 5),
)


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


def configure_sane_scanner(
    driver_version: str,
    scanner_name: str,
    scanner_model: str,
    printer_ip: str,
) -> bool:
    """Replace and verify a Brother network scanner registration."""
    config_command = f"brsaneconfig{driver_version}"
    if shutil.which(config_command) is None:
        raise ScannerConfigurationError(
            f"Missing Brother configuration tool: {config_command}"
        )

    try:
        subprocess.run(
            [config_command, "-r", scanner_name],
            stdout=subprocess.DEVNULL,
            check=True,
        )
        subprocess.run(
            [
                config_command,
                "-a",
                f"name={scanner_name}",
                f"model={scanner_model}",
                f"ip={printer_ip}",
            ],
            check=True,
        )
        query = subprocess.run(
            [config_command, "-q"],
            stdout=subprocess.PIPE,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ScannerConfigurationError(
            f"Could not configure scanner with {config_command}: {exc}"
        ) from exc

    debug = query.stdout.rstrip("\n")
    print(f"Debug: {debug}, scanner: {scanner_name}")
    registration_line = next(
        (line for line in debug.splitlines() if scanner_name in line),
        "",
    )
    print(f"Reg line: {registration_line}")
    if not registration_line or registration_line.startswith("* *"):
        print(
            f"Warning: {scanner_model} registration could not be verified "
            f"with brscan{driver_version}"
        )
        return False

    print(
        f"Registered {scanner_model} at {printer_ip} with {config_command}"
    )
    return True


def supports_phoenix_registration(printer_ip: str) -> bool:
    """Check Brother's capability table for the Phoenix registration API."""
    try:
        result = subprocess.run(
            [
                "snmpget",
                "-v1",
                "-c",
                "public",
                "-Oqv",
                "-t",
                "2",
                "-r",
                "0",
                printer_ip,
                PHOENIX_CAPABILITY_OID,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return False

    if result.returncode != 0:
        return False

    try:
        capabilities = bytes.fromhex(result.stdout.strip().strip('"'))
    except ValueError:
        return False

    phoenix_supported = False
    offset = 0
    while offset < len(capabilities):
        if capabilities[offset] == 0xFF:
            return phoenix_supported
        record = capabilities[offset : offset + 3]
        if len(record) != 3:
            return False
        if record[0] == 0x0B and record[2] == 0x01:
            phoenix_supported = True
        offset += 3

    return False


def _profile_value(
    function: str,
    app_number: int,
    host_ip: str,
    listen_port: int,
    display_name: str,
    *,
    quote_user: bool,
) -> str:
    user = f'"{display_name}"' if quote_user else display_name
    return (
        f"TYPE=BR;BUTTON=SCAN;USER={user};"
        f"FUNC={function};HOST={host_ip}:{listen_port};"
        f"APPNUM={app_number};DURATION=360;BRID=;"
    )


def _register_profiles_snmp(
    printer_ip: str,
    host_ip: str,
    listen_port: int,
    display_name: str,
    log_file: str | None,
) -> bool:
    """Register scan profiles using Brother's legacy SNMP transport."""
    command = ["snmpset", "-v1", "-c", "internal", printer_ip]
    for function, app_number in SCAN_PROFILES:
        command.extend(
            [
                f".{PROFILE_OID}",
                "s",
                _profile_value(
                    function,
                    app_number,
                    host_ip,
                    listen_port,
                    display_name,
                    quote_user=True,
                ),
            ]
        )

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            log("Registered scan profiles on printer using SNMP", log_file)
            return True
        log(f"SNMP registration failed: {result.stderr.strip()}", log_file)
        return False
    except Exception as exc:
        log(f"SNMP registration error: {exc}", log_file)
        return False


def _register_profiles_phoenix(
    printer_ip: str,
    host_ip: str,
    listen_port: int,
    display_name: str,
    log_file: str | None,
) -> bool:
    """Register scan profiles using Brother's Phoenix HTTPS transport."""
    url = f"https://{printer_ip}/phoenix/mib"
    payload = {
        "request": [
            {
                "key": PROFILE_OID,
                "string_value": _profile_value(
                    function,
                    app_number,
                    host_ip,
                    listen_port,
                    display_name,
                    quote_user=False,
                ),
            }
            for function, app_number in PHOENIX_SCAN_PROFILES
        ]
    }

    try:
        passwords = HTTPPasswordMgrWithDefaultRealm()
        passwords.add_password(None, url, "Public", "0000")

        # Brother's utility accepts the printer's device-generated certificate.
        tls_context = ssl.create_default_context()
        tls_context.check_hostname = False
        tls_context.verify_mode = ssl.CERT_NONE
        opener = build_opener(
            HTTPSHandler(context=tls_context),
            HTTPDigestAuthHandler(passwords),
        )
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(request, timeout=10) as response:
            response_payload = json.loads(response.read())

        results = (
            response_payload.get("response")
            if isinstance(response_payload, dict)
            else None
        )
        if (
            not isinstance(results, list)
            or len(results) != len(PHOENIX_SCAN_PROFILES)
            or any(
                not isinstance(result, dict)
                or result.get("key") != PROFILE_OID
                or result.get("status") != 0
                for result in results
            )
        ):
            raise ValueError("printer returned unsuccessful profile statuses")

        log("Registered scan profiles on printer using Phoenix HTTPS", log_file)
        return True
    except Exception as exc:
        log(f"Phoenix registration error: {exc}", log_file)
        return False


def register_profiles(
    printer_ip: str,
    host_ip: str,
    listen_port: int,
    display_name: str,
    log_file: str | None,
) -> bool:
    """Register scan profiles using the transport advertised by the printer."""
    if supports_phoenix_registration(printer_ip):
        return _register_profiles_phoenix(
            printer_ip,
            host_ip,
            listen_port,
            display_name,
            log_file,
        )
    return _register_profiles_snmp(
        printer_ip,
        host_ip,
        listen_port,
        display_name,
        log_file,
    )


def _scan_event_fields(message: str) -> dict[str, str]:
    """Parse the semicolon-delimited fields in a Brother button packet."""
    fields = {}
    for item in message.split(";"):
        key, separator, value = item.partition("=")
        if separator:
            fields[key] = value
    return fields


def is_scan_event(message: str, sender_ip: str, printer_ip: str) -> bool:
    """Accept scan events only from the configured printer."""
    return (
        sender_ip == printer_ip
        and _scan_event_fields(message).get("BUTTON") == "SCAN"
    )


def scan_event_identity(
    message: str,
    sender_ip: str,
    sender_port: int,
) -> tuple[str, ...]:
    """Return an identity shared by retransmissions of one button press."""
    fields = _scan_event_fields(message)
    registration_id = fields.get("REGID")
    sequence = fields.get("SEQ")
    if registration_id and sequence:
        return ("sequence", sender_ip, registration_id, sequence)

    # Older packets may lack sequence metadata. Retransmissions from one press
    # still share their source socket and exact payload.
    return ("packet", sender_ip, str(sender_port), message)

"""UDP scan-button daemon loop."""

import errno
import signal
import socket
import sys
import time

from .brother import is_scan_event, register_profiles, scan_event_identity
from .logger import log
from .scanner import do_scan


class ScanListenerError(RuntimeError):
    """Raised when the scan-button listener cannot start."""


MIN_EVENT_DEDUPE_SECONDS = 300
PACKET_EVENT_DEDUPE_SECONDS = 5


def _event_dedupe_window(config: dict) -> int:
    """Keep event identities longer than the maximum blocking scan work."""
    processing_timeout = config.get("scan_timeout", 120)
    if config.get("delivery_mode") == "api":
        processing_timeout += config.get("paperless_api_timeout", 60)
    return max(MIN_EVENT_DEDUPE_SECONDS, processing_timeout + 30)


def run_daemon(config: dict) -> None:
    """Run the scan-button listener until it is stopped."""
    log_file = config.get("log_file")

    log("brother-scan-to-paperless daemon starting", log_file)
    log(f"  Printer:    {config['printer_ip']}", log_file)
    log(f"  Host:       {config['host_ip']}", log_file)
    log(f"  Delivery:   {config.get('delivery_mode', 'consume')}", log_file)
    if config.get("delivery_mode") == "api":
        log(f"  Paperless:  {config['paperless_url']}", log_file)
        log(f"  Staging:    {config['staging_dir']}", log_file)
    else:
        log(f"  Consume:    {config['consume_dir']}", log_file)
    log(f"  Device:     {config['scanner_device']}", log_file)
    log(f"  Resolution: {config['resolution']} dpi", log_file)
    log(f"  Format:     {config['format']}", log_file)

    try:
        printer_event_ip = socket.gethostbyname(config["printer_ip"])
    except socket.gaierror as exc:
        raise ValueError(
            f"cannot resolve printer_ip: {config['printer_ip']}"
        ) from exc

    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        listener.bind(("0.0.0.0", config["listen_port"]))
    except OSError as exc:
        listener.close()
        print(exc)
        if exc.errno == errno.EADDRINUSE:
            raise ScanListenerError(
                f"UDP port {config['listen_port']} is already in use; "
                "stop brscan-skey and any other scanner backend"
            ) from exc
        raise
    listener.settimeout(30)

    def shutdown(signum, frame):
        log("Shutting down", log_file)
        listener.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    register_profiles(
        config["printer_ip"],
        config["host_ip"],
        config["listen_port"],
        config["display_name"],
        log_file,
    )
    last_register = time.time()
    recent_scan_events: dict[tuple[str, ...], float] = {}
    event_dedupe_window = _event_dedupe_window(config)

    log(f"Listening on UDP port {config['listen_port']}", log_file)

    while True:
        if time.time() - last_register > config["register_interval"]:
            register_profiles(
                config["printer_ip"],
                config["host_ip"],
                config["listen_port"],
                config["display_name"],
                log_file,
            )
            last_register = time.time()

        try:
            data, addr = listener.recvfrom(1024)
            msg = data.decode("utf-8", errors="replace")

            if is_scan_event(msg, addr[0], printer_event_ip):
                received_at = time.monotonic()
                recent_scan_events = {
                    identity: seen_at
                    for identity, seen_at in recent_scan_events.items()
                    if received_at - seen_at
                    <= (
                        PACKET_EVENT_DEDUPE_SECONDS
                        if identity[0] == "packet"
                        else event_dedupe_window
                    )
                }
                identity = scan_event_identity(msg, addr[0], addr[1])
                if identity in recent_scan_events:
                    log(f"Ignored repeated scan event from {addr}", log_file)
                    continue
                recent_scan_events[identity] = received_at
                try:
                    log(f"Received from {addr}: {msg[:120]}", log_file)
                    do_scan(config)
                finally:
                    # Anchor deduplication to completed scan work so queued
                    # retransmissions cannot outlive a long document job.
                    recent_scan_events[identity] = time.monotonic()

        except socket.timeout:
            continue
        except Exception as exc:
            log(f"Error: {exc}", log_file)
            time.sleep(1)

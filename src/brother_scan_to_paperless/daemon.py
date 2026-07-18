"""UDP scan-button daemon loop."""

import signal
import socket
import sys
import time

from .brother import is_scan_event, register_profiles
from .logger import log
from .scanner import do_scan


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
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", config["listen_port"]))
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
                log(f"Received from {addr}: {msg[:120]}", log_file)
                do_scan(config)

        except socket.timeout:
            continue
        except Exception as exc:
            log(f"Error: {exc}", log_file)
            time.sleep(1)

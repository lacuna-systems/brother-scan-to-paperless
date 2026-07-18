"""Command-line interface."""

import argparse
import getpass
import os
import sys
from urllib.parse import urlsplit

from . import __version__
from .brother import (
    ScannerConfigurationError,
    configure_sane_scanner,
    default_driver_version,
    detect_host_ip,
)
from .config import (
    DEFAULT_CONFIG,
    check_dependencies,
    load_config,
    validate_config,
    write_config,
)
from .daemon import ScanListenerError, run_daemon
from .scanner import do_scan


def configure_container_scanner(config: dict) -> None:
    """Register the container's Brother SANE scanner before scanning."""
    if os.environ.get("AUTO_REGISTER_SCANNER") != "true":
        return
    if os.environ.get("SKIP_SCANNER_REGISTRATION", "false") == "true":
        return

    scanner_model = os.environ.get("SCANNER_MODEL")
    if not scanner_model:
        raise ScannerConfigurationError("SCANNER_MODEL is required")
    scanner_name = os.environ.get("SCANNER_NAME") or "BROTHER"
    verified = configure_sane_scanner(
        config["driver_version"],
        scanner_name,
        scanner_model,
        config["printer_ip"],
    )
    if not verified:
        print("Continuing without a verified Brother SANE registration")


def cmd_setup(args: argparse.Namespace) -> None:
    """Run the interactive setup wizard."""
    print("brother-scan-to-paperless setup\n")

    config = DEFAULT_CONFIG.copy()

    default_driver = default_driver_version()
    driver_version = input(
        f"Brother scanner driver (4 or 5) [{default_driver}]: "
    ).strip()
    config["driver_version"] = driver_version or default_driver
    config["scanner_device"] = (
        f"brother{config['driver_version']}:net1;dev0"
    )

    detected_ip = detect_host_ip()

    config["printer_ip"] = input("Printer IP address: ").strip()

    default_host = detected_ip or ""
    host = input(f"This machine's IP address [{default_host}]: ").strip()
    config["host_ip"] = host or default_host

    delivery_mode = input(
        f"Delivery mode (consume or api) [{config['delivery_mode']}]: "
    ).strip()
    config["delivery_mode"] = (
        delivery_mode or config["delivery_mode"]
    ).lower()

    if config["delivery_mode"] == "api":
        staging_dir = input(
            f"Local staging directory [{config['staging_dir']}]: "
        ).strip()
        config["staging_dir"] = staging_dir or config["staging_dir"]
        config["paperless_url"] = input(
            "Paperless-ngx base URL: "
        ).strip()
        if urlsplit(config["paperless_url"]).scheme.lower() == "http":
            allow_http = input(
                "Allow insecure HTTP (token and scans will be unencrypted) "
                "[no]: "
            ).strip().lower()
            config["paperless_allow_insecure_http"] = allow_http in (
                "y",
                "yes",
            )
        config["paperless_token"] = getpass.getpass(
            "Paperless-ngx API token: "
        ).strip()
    else:
        config["consume_dir"] = input(
            "Paperless-ngx consume directory path: "
        ).strip()

    resolution = input(
        f"Scan resolution in DPI [{config['resolution']}]: "
    ).strip()
    if resolution:
        config["resolution"] = int(resolution)

    name = input(
        f"Display name on printer [{config['display_name']}]: "
    ).strip()
    if name:
        config["display_name"] = name

    errors = validate_config(config)
    if errors:
        print("\nConfiguration errors:")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)

    config_dir = "/etc/brother-scan-to-paperless"
    os.makedirs(config_dir, mode=0o700, exist_ok=True)
    os.chmod(config_dir, 0o700)
    config_path = os.path.join(config_dir, "config.json")

    write_config(config, config_path)

    print(f"\nConfig saved to {config_path}")
    print(f"\nRegister scanner with brscan{config['driver_version']}:")
    print(
        f"  brsaneconfig{config['driver_version']} -a "
        f"name=BROTHER model=YOUR_MODEL ip={config['printer_ip']}"
    )
    print("\nStart the daemon:")
    print("  systemctl enable --now brother-scan-to-paperless")


def cmd_test(args: argparse.Namespace) -> None:
    """Test a scan without starting the daemon."""
    config = load_config(args.config)
    if args.printer_ip:
        config["printer_ip"] = args.printer_ip
    if args.consume_dir:
        config["consume_dir"] = args.consume_dir

    errors = validate_config(config)
    if errors:
        print("Configuration errors:")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)

    configure_container_scanner(config)

    print("Testing scan...")
    success = do_scan(config, page_limit=1)
    if success:
        print("Test scan succeeded!")
    else:
        print("Test scan failed. Check logs.")
        sys.exit(1)


def cmd_run(args: argparse.Namespace) -> None:
    """Run the daemon."""
    config = load_config(args.config)

    if args.printer_ip:
        config["printer_ip"] = args.printer_ip
    if args.host_ip:
        config["host_ip"] = args.host_ip
    if args.consume_dir:
        config["consume_dir"] = args.consume_dir
    if args.resolution:
        config["resolution"] = args.resolution
    if args.display_name:
        config["display_name"] = args.display_name

    missing = check_dependencies(config)
    if missing:
        print(f"Missing dependencies: {', '.join(missing)}")
        print("Install with: apt install sane-utils snmp libtiff-tools")
        driver_command = f"brsaneconfig{config['driver_version']}"
        if driver_command in missing:
            print(
                f"Install brscan{config['driver_version']} from: "
                "https://support.brother.com/g/b/downloadlist.aspx"
                "?os=128"
            )
        sys.exit(1)

    errors = validate_config(config)
    if errors:
        print("Configuration errors:")
        for error in errors:
            print(f"  - {error}")
        print("\nRun 'brother-scan-to-paperless setup' to configure.")
        sys.exit(1)

    configure_container_scanner(config)

    run_daemon(config)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="brother-scan-to-paperless",
        description=(
            "Lightweight daemon that listens for Brother scanner button "
            "presses and delivers scans to Paperless-ngx."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run the scan daemon")
    run_parser.add_argument("-c", "--config", help="Path to config file")
    run_parser.add_argument("--printer-ip", help="Printer IP address")
    run_parser.add_argument("--host-ip", help="This machine's IP address")
    run_parser.add_argument("--consume-dir", help="Paperless consume directory")
    run_parser.add_argument("--resolution", type=int, help="Scan resolution (DPI)")
    run_parser.add_argument("--display-name", help="Name shown on printer display")

    subparsers.add_parser("setup", help="Interactive setup wizard")

    test_parser = subparsers.add_parser("test", help="Test a single scan")
    test_parser.add_argument("-c", "--config", help="Path to config file")
    test_parser.add_argument("--printer-ip", help="Printer IP address")
    test_parser.add_argument("--consume-dir", help="Paperless consume directory")

    args = parser.parse_args()

    try:
        if args.command == "run":
            cmd_run(args)
        elif args.command == "setup":
            cmd_setup(args)
        elif args.command == "test":
            cmd_test(args)
        else:
            parser.print_help()
            sys.exit(1)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(2)
    except (ScannerConfigurationError, ScanListenerError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

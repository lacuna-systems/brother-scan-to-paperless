"""Configuration loading, validation, and persistence."""

import json
import os
import shutil
import stat
import tempfile

from .brother import detect_host_ip
from .paperless import paperless_upload_target


DEFAULT_CONFIG_PATHS = [
    "/etc/brother-scan-to-paperless/config.json",
    os.path.expanduser("~/.config/brother-scan-to-paperless/config.json"),
]

DEFAULT_CONFIG = {
    "driver_version": "4",
    "delivery_mode": "consume",
    "printer_ip": "",
    "host_ip": "",
    "listen_port": 54925,
    "consume_dir": "",
    "staging_dir": "/var/lib/brother-scan-to-paperless",
    "paperless_url": "",
    "paperless_token": "",
    "paperless_api_timeout": 60,
    "paperless_allow_insecure_http": False,
    "scanner_device": "",
    "resolution": 300,
    "source": "FB",
    "size": "A4",
    "format": "tiff",
    "register_interval": 300,
    "scan_timeout": 120,
    "display_name": "Paperless",
    "log_file": "/var/log/brother-scan-to-paperless.log",
}

ENV_CONFIG = [
    ("DELIVERY_MODE", "delivery_mode"),
    ("SCANNER_IP", "printer_ip"),
    ("PRINTER_IP", "printer_ip"),
    ("HOST_IP", "host_ip"),
    ("LISTEN_PORT", "listen_port"),
    ("CONSUME_FOLDER", "consume_dir"),
    ("CONSUME_DIR", "consume_dir"),
    ("STAGING_DIR", "staging_dir"),
    ("PAPERLESS_URL", "paperless_url"),
    ("PAPERLESS_API_TOKEN", "paperless_token"),
    ("PAPERLESS_API_TIMEOUT", "paperless_api_timeout"),
    ("PAPERLESS_ALLOW_INSECURE_HTTP", "paperless_allow_insecure_http"),
    ("DEVICE_NAME", "scanner_device"),
    ("SCANNER_DEVICE", "scanner_device"),
    ("RESOLUTION", "resolution"),
    ("SOURCE", "source"),
    ("PAPER_SIZE", "size"),
    ("FORMAT", "format"),
    ("REGISTRATION_INTERVAL", "register_interval"),
    ("REGISTER_INTERVAL", "register_interval"),
    ("SCAN_TIMEOUT", "scan_timeout"),
    ("DISPLAY_NAME", "display_name"),
    ("LOG_FILE", "log_file"),
    ("BRSCAN_VERSION", "driver_version"),
]

INTEGER_CONFIG_FIELDS = {
    "listen_port",
    "resolution",
    "register_interval",
    "scan_timeout",
    "paperless_api_timeout",
}

BOOLEAN_CONFIG_FIELDS = {"paperless_allow_insecure_http"}


def load_config(config_path: str | None = None) -> dict:
    """Load config from a file and apply environment overrides."""
    config = DEFAULT_CONFIG.copy()

    paths_to_try = [config_path] if config_path else DEFAULT_CONFIG_PATHS
    for path in paths_to_try:
        if path and os.path.exists(path):
            with open(path) as f:
                user_config = json.load(f)
            config.update(user_config)
            break

    for env_name, config_name in ENV_CONFIG:
        value = os.environ.get(env_name)
        if value is None:
            continue
        if config_name in INTEGER_CONFIG_FIELDS:
            try:
                value = int(value)
            except ValueError as exc:
                raise ValueError(f"{env_name} must be an integer") from exc
        elif config_name in BOOLEAN_CONFIG_FIELDS:
            normalized = value.strip().lower()
            if normalized not in ("true", "false"):
                raise ValueError(f"{env_name} must be true or false")
            value = normalized == "true"
        config[config_name] = value

    token_file = os.environ.get("PAPERLESS_API_TOKEN_FILE")
    if token_file:
        try:
            with open(token_file) as f:
                config["paperless_token"] = f.read().strip()
        except OSError as exc:
            raise ValueError(
                f"PAPERLESS_API_TOKEN_FILE cannot be read: {token_file}"
            ) from exc
        if not config["paperless_token"]:
            raise ValueError("PAPERLESS_API_TOKEN_FILE is empty")

    config["driver_version"] = str(config.get("driver_version", "4"))
    config["delivery_mode"] = str(
        config.get("delivery_mode", "consume")
    ).strip().lower()
    for field in ("paperless_url", "paperless_token"):
        if isinstance(config.get(field), str):
            config[field] = config[field].strip()
    if not config.get("scanner_device"):
        config["scanner_device"] = (
            f"brother{config['driver_version']}:net1;dev0"
        )

    return config


def validate_config(config: dict) -> list[str]:
    """Validate config and return list of error messages."""
    errors = []
    delivery_mode = config.get("delivery_mode", "consume")
    if config.get("driver_version") not in ("4", "5"):
        errors.append("driver_version must be 4 or 5")
    if delivery_mode not in ("consume", "api"):
        errors.append("delivery_mode must be consume or api")
    if not config.get("printer_ip"):
        errors.append("printer_ip is required")
    if not config.get("host_ip"):
        detected = detect_host_ip()
        if detected:
            config["host_ip"] = detected
        else:
            errors.append("host_ip is required (auto-detection failed)")
    if delivery_mode == "api":
        paperless_url = config.get("paperless_url", "")
        parsed_url = None
        if not paperless_url:
            errors.append("paperless_url is required in api mode")
        else:
            try:
                parsed_url, _ = paperless_upload_target(paperless_url)
            except ValueError as exc:
                errors.append(str(exc))

        allow_insecure_http = config.get(
            "paperless_allow_insecure_http", False
        )
        if type(allow_insecure_http) is not bool:
            errors.append("paperless_allow_insecure_http must be true or false")
        elif (
            parsed_url is not None
            and parsed_url.scheme == "http"
            and not allow_insecure_http
        ):
            errors.append(
                "paperless_url must use https unless "
                "paperless_allow_insecure_http is true"
            )

        paperless_token = config.get("paperless_token", "")
        if not paperless_token:
            errors.append("paperless_token is required in api mode")
        elif "\r" in paperless_token or "\n" in paperless_token:
            errors.append("paperless_token must not contain newlines")

        timeout = config.get("paperless_api_timeout", 60)
        if type(timeout) is not int or timeout <= 0:
            errors.append("paperless_api_timeout must be greater than zero")

        staging_dir = config.get("staging_dir", "")
        if not staging_dir:
            errors.append("staging_dir is required in api mode")
        elif os.path.islink(staging_dir):
            errors.append("staging_dir must not be a symbolic link")
        elif not os.path.isdir(staging_dir):
            errors.append(f"staging_dir does not exist: {staging_dir}")
        else:
            staging_stat = os.stat(staging_dir)
            if staging_stat.st_uid != os.geteuid():
                errors.append(
                    "staging_dir must be owned by the daemon user: "
                    f"{staging_dir}"
                )
            if stat.S_IMODE(staging_stat.st_mode) & 0o077:
                errors.append(
                    "staging_dir must not grant group or other access: "
                    f"{staging_dir}"
                )
            if not os.access(staging_dir, os.W_OK | os.X_OK):
                errors.append(f"staging_dir is not writable: {staging_dir}")
    else:
        consume_dir = config.get("consume_dir", "")
        if not consume_dir:
            errors.append("consume_dir is required")
        elif not os.path.isdir(consume_dir):
            errors.append(f"consume_dir does not exist: {consume_dir}")
        elif not os.access(consume_dir, os.W_OK | os.X_OK):
            errors.append(f"consume_dir is not writable: {consume_dir}")

    return errors


def check_dependencies(config: dict) -> list[str]:
    """Check that required commands are available."""
    missing = []
    driver_version = config.get("driver_version", "4")
    commands = [
        "scanimage",
        "snmpget",
        "snmpset",
        f"brsaneconfig{driver_version}",
    ]
    output_format = config.get("format", "tiff").lower()
    if output_format in ("pdf", "tiff"):
        commands.append("tiffcp")
    if output_format == "pdf":
        commands.append("tiff2pdf")
    for cmd in commands:
        if shutil.which(cmd) is None:
            missing.append(cmd)
    return missing


def write_config(config: dict, config_path: str) -> None:
    """Atomically replace a configuration file with mode 0600."""
    config_dir = os.path.dirname(config_path)
    descriptor = None
    temp_path = None

    try:
        descriptor, temp_path = tempfile.mkstemp(
            prefix=".config.",
            suffix=".tmp",
            dir=config_dir,
        )
        os.fchmod(descriptor, 0o600)
        config_file = os.fdopen(descriptor, "w")
        descriptor = None
        with config_file:
            json.dump(config, config_file, indent=2)
            config_file.write("\n")
            config_file.flush()
            os.fsync(config_file.fileno())
        os.replace(temp_path, config_path)
        temp_path = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temp_path is not None:
            try:
                os.remove(temp_path)
            except FileNotFoundError:
                pass

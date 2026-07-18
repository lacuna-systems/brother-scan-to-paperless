#!/bin/sh
set -eu

case "${1:-}" in
    run|test)
        if [ "${SKIP_SCANNER_REGISTRATION:-false}" != "true" ]; then
            : "${PRINTER_IP:?PRINTER_IP is required}"
            : "${SCANNER_MODEL:?SCANNER_MODEL is required}"
            : "${BRSCAN_VERSION:?BRSCAN_VERSION is not set by the image}"

            config_command="brsaneconfig${BRSCAN_VERSION}"
            scanner_name="${SCANNER_NAME:-BROTHER}"

            if ! command -v "${config_command}" >/dev/null 2>&1; then
                echo "Missing Brother configuration tool: ${config_command}" >&2
                exit 1
            fi

            "${config_command}" -r "${scanner_name}" >/dev/null
            "${config_command}" -a \
                "name=${scanner_name}" \
                "model=${SCANNER_MODEL}" \
                "ip=${PRINTER_IP}"

            registration_line=$(
                "${config_command}" -q \
                    | grep -F -- "${scanner_name}" \
                    | head -n 1 \
                    || true
            )
            if [ -z "${registration_line}" ] \
                || printf '%s\n' "${registration_line}" | grep -q '^\* \*'; then
                echo "${SCANNER_MODEL} is not supported by brscan${BRSCAN_VERSION}" >&2
                exit 1
            fi

            echo "Registered ${SCANNER_MODEL} at ${PRINTER_IP} with ${config_command}"
        fi
        ;;
esac

exec /usr/local/bin/brother-scan-to-paperless "$@"

#!/usr/bin/env bash
set -euo pipefail

# brother-scan-to-paperless installer

INSTALL_DIR="/opt/brother-scan-to-paperless"
BIN_LINK="/usr/local/bin/brother-scan-to-paperless"
SERVICE_FILE="/etc/systemd/system/brother-scan-to-paperless.service"
CONFIG_DIR="/etc/brother-scan-to-paperless"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# Check root
[[ $EUID -ne 0 ]] && error "This script must be run as root"

# Check Python 3.10+
if ! command -v python3 &>/dev/null; then
    error "Python 3 is required. Install with: apt install python3"
fi

PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYMAJOR=$(echo "$PYVER" | cut -d. -f1)
PYMINOR=$(echo "$PYVER" | cut -d. -f2)
if [[ "$PYMAJOR" -lt 3 ]] || { [[ "$PYMAJOR" -eq 3 ]] && [[ "$PYMINOR" -lt 10 ]]; }; then
    error "Python 3.10+ required (found $PYVER)"
fi

info "Installing dependencies..."
apt-get update -qq
apt-get install -y -qq ca-certificates sane-utils snmp > /dev/null 2>&1

# Check for a supported Brother scanner driver
DRIVER_VERSION="${BRSCAN_VERSION:-}"
if [[ -n "$DRIVER_VERSION" && "$DRIVER_VERSION" != "4" && "$DRIVER_VERSION" != "5" ]]; then
    error "BRSCAN_VERSION must be 4 or 5"
fi

if [[ -z "$DRIVER_VERSION" ]]; then
    for version in 4 5; do
        if command -v "brsaneconfig${version}" &>/dev/null; then
            DRIVER_VERSION="$version"
            break
        fi
    done
fi

if [[ -z "$DRIVER_VERSION" ]] || ! command -v "brsaneconfig${DRIVER_VERSION}" &>/dev/null; then
    warn "brscan4/brscan5 driver not found."
    echo ""
    echo "  Download the scanner driver (.deb) for your model from:"
    echo "  https://support.brother.com"
    echo ""
    echo "  Then install with:"
    echo "    dpkg -i brscan4-*.amd64.deb"
    echo "  or:"
    echo "    dpkg -i brscan5-*.amd64.deb"
    echo ""
    echo "  After installing, re-run this installer."
    exit 1
fi
info "Found brscan${DRIVER_VERSION}"

# Create sane config if missing (common in LXC containers)
mkdir -p /etc/sane.d
touch /etc/sane.d/dll.conf

# Ensure the selected backend is in dll.conf
if ! grep -qx "brother${DRIVER_VERSION}" /etc/sane.d/dll.conf 2>/dev/null; then
    echo "brother${DRIVER_VERSION}" >> /etc/sane.d/dll.conf
    info "Added brother${DRIVER_VERSION} to /etc/sane.d/dll.conf"
fi

# Install the daemon
info "Installing daemon to ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR"
PACKAGE_DIR="$INSTALL_DIR/brother_scan_to_paperless"
PACKAGE_STAGE="$INSTALL_DIR/.brother_scan_to_paperless.new"
rm -rf "$PACKAGE_STAGE"
cp -R src/brother_scan_to_paperless "$PACKAGE_STAGE"
rm -rf "$PACKAGE_DIR"
mv "$PACKAGE_STAGE" "$PACKAGE_DIR"
install -m 0755 \
    src/brother_scan_daemon.py \
    "$INSTALL_DIR/brother_scan_daemon.py"

# Create symlink
ln -sf "$INSTALL_DIR/brother_scan_daemon.py" "$BIN_LINK"
info "Created symlink: $BIN_LINK"

# Install systemd service
info "Installing systemd service..."
cat > "$SERVICE_FILE" << 'EOF'
[Unit]
Description=Brother Scan to Paperless-ngx Daemon
Documentation=https://github.com/zshel/brother-scan-to-paperless
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/brother-scan-to-paperless run
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
info "Systemd service installed"

# Create private config and staging directories
install -d -m 0700 "$CONFIG_DIR"
install -d -m 0700 /var/lib/brother-scan-to-paperless

echo ""
info "Installation complete!"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Register your scanner:"
echo "     brsaneconfig${DRIVER_VERSION} -a name=BROTHER model=YOUR_MODEL ip=PRINTER_IP"
echo ""
echo "  2. Run the setup wizard:"
echo "     brother-scan-to-paperless setup"
echo ""
echo "  3. Test a scan:"
echo "     brother-scan-to-paperless test"
echo ""
echo "  4. Enable and start the daemon:"
echo "     systemctl enable --now brother-scan-to-paperless"
echo ""

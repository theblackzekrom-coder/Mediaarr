#!/bin/bash

set -e

APP_NAME="mediaarr"
INSTALL_DIR="/opt/mediaarr"
SERVICE_FILE="/etc/systemd/system/mediaarr.service"
PORT=8787

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo "  Mediaarr Installer"
echo "  =================="
echo ""

# Root check
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root (sudo ./install.sh)${NC}"
    exit 1
fi

# Python check
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Python 3 not found. Install it with: sudo apt install python3 python3-pip${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PYTHON_VERSION" -lt 11 ]; then
    echo -e "${RED}Python 3.11+ required. Found: $(python3 --version)${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Python $(python3 --version) found${NC}"

# Copy files
echo "Installing to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp -r . "$INSTALL_DIR/"
chown -R root:root "$INSTALL_DIR"

# Install dependencies
echo "Installing dependencies..."
pip3 install --ignore-installed --break-system-packages -r "$INSTALL_DIR/requirements.txt" -q
echo -e "${GREEN}✓ Dependencies installed${NC}"

# Create systemd service
echo "Creating systemd service..."
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Mediaarr Media Manager
After=network.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Enable and start
systemctl daemon-reload
systemctl enable "$APP_NAME"
systemctl start "$APP_NAME"

echo ""
echo -e "${GREEN}  Mediaarr installed and running!${NC}"
echo ""
echo "  URL:     http://localhost:$PORT"
echo "  Logs:    journalctl -u mediaarr -f"
echo "  Stop:    sudo systemctl stop mediaarr"
echo "  Start:   sudo systemctl start mediaarr"
echo "  Restart: sudo systemctl restart mediaarr"
echo ""

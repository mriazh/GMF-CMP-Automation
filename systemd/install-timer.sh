#!/bin/bash
# Install script for Telkomsel CMP systemd timer
# Run with sudo: sudo bash systemd/install-timer.sh

set -euo pipefail

SERVICE_FILE="/etc/systemd/system/cmp-automation-daily.service"
TIMER_FILE="/etc/systemd/system/cmp-automation-daily.timer"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Installing Telkomsel CMP systemd service and timer..."
echo "Source directory: $SOURCE_DIR"

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root (use sudo)"
   exit 1
fi

# Copy service and timer files
cp "$SOURCE_DIR/systemd/cmp-automation-daily.service" "$SERVICE_FILE"
cp "$SOURCE_DIR/systemd/cmp-automation-daily.timer" "$TIMER_FILE"

echo "Copied service file to $SERVICE_FILE"
echo "Copied timer file to $TIMER_FILE"

# Reload systemd daemon
systemctl daemon-reload

# Enable and start timer
systemctl enable cmp-automation-daily.timer
systemctl start cmp-automation-daily.timer

echo "Timer enabled and started."
echo ""
echo "Check status with:"
echo "  systemctl status cmp-automation-daily.timer"
echo "  systemctl list-timers --all | grep cmp-automation"
echo ""
echo "View logs with:"
echo "  journalctl -u cmp-automation-daily.service -f"
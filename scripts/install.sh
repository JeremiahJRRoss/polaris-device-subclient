#!/usr/bin/env bash
# Polaris Device Subclient — Idempotent Installer
# Creates user, directories, venv, installs the app, and sets up systemd.
# Safe to re-run: won't overwrite existing config or credentials.
set -euo pipefail

INSTALL_DIR="/opt/polaris-device-subclient"
CONFIG_DIR="/etc/polaris"
DATA_DIR="/var/lib/polaris/data"
LOG_DIR="/var/log/polaris-device-subclient"
SERVICE_USER="polaris"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Polaris Device Subclient Installer ==="

# --- service user ---
if ! id -u "$SERVICE_USER" &>/dev/null; then
    echo "Creating service user: $SERVICE_USER"
    useradd --system --shell /usr/sbin/nologin --home-dir "$INSTALL_DIR" "$SERVICE_USER"
else
    echo "Service user '$SERVICE_USER' already exists"
fi

# --- directories ---
for dir in "$INSTALL_DIR" "$CONFIG_DIR" "$DATA_DIR" "$LOG_DIR"; do
    mkdir -p "$dir"
done

chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR" "$LOG_DIR"
chmod 750 "$DATA_DIR" "$LOG_DIR"

# --- Python venv ---
if [ ! -d "$INSTALL_DIR/venv" ]; then
    echo "Creating Python virtual environment"
    python3 -m venv "$INSTALL_DIR/venv"
fi

echo "Installing polaris-device-subclient"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet "$SCRIPT_DIR"

# --- config template (don't overwrite existing) ---
if [ ! -f "$CONFIG_DIR/config.json" ]; then
    echo "Installing default config"
    cp "$SCRIPT_DIR/config/config.json" "$CONFIG_DIR/config.json"
    chmod 644 "$CONFIG_DIR/config.json"
else
    echo "Config file already exists — skipping"
fi

# --- env file template (don't overwrite existing) ---
if [ ! -f "$CONFIG_DIR/polaris-device-subclient.env" ]; then
    echo "Installing environment file template"
    cp "$SCRIPT_DIR/systemd/polaris-device-subclient.env" "$CONFIG_DIR/polaris-device-subclient.env"
    chmod 600 "$CONFIG_DIR/polaris-device-subclient.env"
else
    echo "Environment file already exists — skipping"
fi

# --- systemd unit ---
echo "Installing systemd unit file"
cp "$SCRIPT_DIR/systemd/polaris-device-subclient-file.service" /etc/systemd/system/

if [ ! -f /etc/systemd/system/polaris-device-subclient.service ]; then
    echo "Setting default service"
    cp "$SCRIPT_DIR/systemd/polaris-device-subclient-file.service" \
       /etc/systemd/system/polaris-device-subclient.service
fi

systemctl daemon-reload

# --- logrotate ---
if [ -d /etc/logrotate.d ]; then
    cp "$SCRIPT_DIR/systemd/polaris-device-subclient.logrotate" /etc/logrotate.d/polaris-device-subclient
    echo "Installed logrotate config"
fi

echo ""
echo "=== Installation complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit credentials:  sudo nano $CONFIG_DIR/polaris-device-subclient.env"
echo "  2. Start the service: sudo systemctl enable --now polaris-device-subclient"
echo "  3. Check status:      sudo systemctl status polaris-device-subclient"

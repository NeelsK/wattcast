#!/bin/bash
# =============================================================================
# WattCast Pi Install Script
# =============================================================================
# Usage:
#   git clone https://github.com/NeelsK/wattcast.git
#   cd wattcast
#   sudo bash setup/install.sh
#
# What this does:
#   1. Installs system packages (MariaDB, Python3, git)
#   2. Creates wattcast DB user and schema
#   3. Installs Python dependencies system-wide
#   4. Copies and enables systemd services (mqtt_bridge, weather_webhook, solar-rule-engine)
#   5. Prompts for passwords and writes them to the installed service files
#
# After running:
#   - Edit /home/wattcast/wattcast/solar-rule-engine/config.yaml with site values
#   - Set dry_run: false when ready to go live
# =============================================================================

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WATTCAST_USER="wattcast"
WATTCAST_HOME="/home/${WATTCAST_USER}"
INSTALL_DIR="${WATTCAST_HOME}/wattcast"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Must run as root
[[ $EUID -ne 0 ]] && error "Run as root: sudo bash setup/install.sh"

info "WattCast installer starting..."
info "Repo: ${REPO_DIR}"

# =============================================================================
# 1. System packages
# =============================================================================
info "Installing system packages..."
apt-get update -qq
apt-get install -y \
    git \
    python3 \
    python3-pip \
    mariadb-server \
    mariadb-client \
    libmariadb-dev \
    mosquitto \
    mosquitto-clients \
    > /dev/null

info "Packages installed."

# =============================================================================
# 2. Create wattcast system user (if not exists)
# =============================================================================
if ! id "${WATTCAST_USER}" &>/dev/null; then
    info "Creating user ${WATTCAST_USER}..."
    adduser --disabled-password --gecos "" "${WATTCAST_USER}"
fi

# =============================================================================
# 3. Copy repo to install dir (if running from a different location)
# =============================================================================
if [[ "${REPO_DIR}" != "${INSTALL_DIR}" ]]; then
    info "Copying repo to ${INSTALL_DIR}..."
    cp -r "${REPO_DIR}" "${INSTALL_DIR}"
    chown -R "${WATTCAST_USER}:${WATTCAST_USER}" "${INSTALL_DIR}"
fi

# =============================================================================
# 4. Python dependencies
# =============================================================================
info "Installing Python dependencies..."
pip3 install --break-system-packages flask pymysql paho-mqtt requests PyYAML \
    > /dev/null 2>&1
info "Python dependencies installed."

# =============================================================================
# 5. MariaDB setup
# =============================================================================
info "Setting up MariaDB..."
systemctl enable mariadb --now

# Prompt for DB password
echo ""
read -rsp "Enter password for MariaDB wattcast user: " DB_PASS
echo ""
read -rsp "Confirm password: " DB_PASS2
echo ""
[[ "${DB_PASS}" != "${DB_PASS2}" ]] && error "Passwords do not match."

# Create DB and user
mariadb -e "
CREATE DATABASE IF NOT EXISTS wattcast CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'wattcast'@'localhost' IDENTIFIED BY '${DB_PASS}';
GRANT ALL PRIVILEGES ON wattcast.* TO 'wattcast'@'localhost';
FLUSH PRIVILEGES;
"

# Run schema (replace CHANGE_ME placeholder first)
sed "s/CHANGE_ME/${DB_PASS}/g" "${REPO_DIR}/setup/schema.sql" | mariadb
info "Database created and schema applied."

# =============================================================================
# 6. Prompt for remaining passwords
# =============================================================================
echo ""
read -rsp "Enter Shelly geyser password: " SHELLY_PASS
echo ""
read -rsp "Enter MQTT bridge DB password (same as above if using local DB): " MQTT_DB_PASS
echo ""

# =============================================================================
# 7. Install and configure systemd services
# =============================================================================
info "Installing systemd services..."

install_service() {
    local name="$1"
    local src="$2"
    local dst="/etc/systemd/system/${name}.service"
    cp "${src}" "${dst}"
    # Replace CHANGE_ME with actual passwords
    sed -i "s|DB_PASS=CHANGE_ME|DB_PASS=${MQTT_DB_PASS}|g" "${dst}"
    sed -i "s|DB_HOST=10.69.69.4|DB_HOST=localhost|g" "${dst}"
    sed -i "s|DB_NAME=weather_data|DB_NAME=wattcast|g" "${dst}"
    systemctl daemon-reload
    systemctl enable "${name}"
    info "Service ${name} installed and enabled."
}

install_service "mqtt_bridge" "${INSTALL_DIR}/mqtt_bridge/mqtt_bridge.service"
install_service "weather_webhook" "${INSTALL_DIR}/weather_webhook/weather_webhook.service"

# weather_webhook also needs its DB_PASS set
sed -i "s|DB_PASS=CHANGE_ME|DB_PASS=${DB_PASS}|g" /etc/systemd/system/weather_webhook.service

# solar-rule-engine: copy config.yaml.example → config.yaml if not already present
CONFIG="${INSTALL_DIR}/solar-rule-engine/config.yaml"
if [[ ! -f "${CONFIG}" ]]; then
    cp "${INSTALL_DIR}/solar-rule-engine/config.yaml.example" "${CONFIG}"
    chown "${WATTCAST_USER}:${WATTCAST_USER}" "${CONFIG}"
    warn "Config not yet filled in — edit ${CONFIG} before starting solar-rule-engine."
else
    info "config.yaml already exists — not overwriting."
fi

install_service "solar-rule-engine" "${INSTALL_DIR}/solar-rule-engine/solar-rule-engine.service"

# Update solar-rule-engine service to add mariadb password
sed -i "/\[Service\]/a Environment=\"MARIADB_PASS=${DB_PASS}\"" \
    /etc/systemd/system/solar-rule-engine.service
systemctl daemon-reload

# =============================================================================
# 8. Mosquitto bridge to Solar Assistant
# =============================================================================
SA_IP=""
echo ""
read -rp "Enter Solar Assistant Pi IP address (e.g. 10.69.69.31): " SA_IP

if [[ -n "${SA_IP}" ]]; then
    cat > /etc/mosquitto/conf.d/sa-bridge.conf <<EOF
connection solar-assistant
address ${SA_IP}:1883
topic solar_assistant/# in 0
bridge_attempt_unsubscribe false
start_type automatic
EOF
    systemctl enable mosquitto --now
    systemctl restart mosquitto
    info "Mosquitto bridge configured → ${SA_IP}"
fi

# =============================================================================
# 9. Start services
# =============================================================================
info "Starting services..."
systemctl start mqtt_bridge
systemctl start weather_webhook
# Note: solar-rule-engine is NOT started automatically — fill in config.yaml first
warn "solar-rule-engine NOT started. Edit config.yaml first, then:"
warn "  sudo systemctl start solar-rule-engine"

# =============================================================================
# Done
# =============================================================================
echo ""
info "======================================================"
info "WattCast installation complete!"
info "======================================================"
echo ""
echo "Next steps:"
echo "  1. Edit ${CONFIG}"
echo "     - Set your location, panel spec, battery, Shelly IP/password"
echo "     - Set mariadb.password to the DB password you just entered"
echo "     - Leave dry_run: true until you've verified behaviour"
echo ""
echo "  2. Point your Ecowitt device to this Pi:"
echo "     Server: $(hostname -I | awk '{print $1}')"
echo "     Path:   /weather"
echo "     Port:   8080"
echo "     Protocol: Ecowitt or WeatherUnderground"
echo ""
echo "  3. Start the rule engine:"
echo "     sudo systemctl start solar-rule-engine"
echo "     journalctl -u solar-rule-engine -f"
echo ""
echo "  Service logs:"
echo "    journalctl -u mqtt_bridge -f"
echo "    journalctl -u weather_webhook -f"
echo "    journalctl -u solar-rule-engine -f"

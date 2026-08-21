#!/usr/bin/env bash
# EC2 setup script for the Supply Chain Bot.
# Run as root (or with sudo) on a fresh Ubuntu 22.04+ instance (t3.small recommended).
#
# Usage:
#   chmod +x setup_ec2.sh
#   sudo ./setup_ec2.sh

set -euo pipefail

BOT_DIR="/home/ubuntu/G4_automation"

echo "=== 1/6  System packages ==="
apt-get update -y
apt-get install -y python3 python3-pip python3-venv unzip curl wget gnupg

echo "=== 2/6  Google Chrome (stable, pinned version) ==="
# Pin Chrome version to avoid auto-update breaking ChromeDriver
wget -qO - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
  > /etc/apt/sources.list.d/google-chrome.list
apt-get update -y
apt-get install -y google-chrome-stable

# Disable Chrome auto-updates to prevent version mismatch with ChromeDriver
if [ -f /etc/apt/apt.conf.d/20auto-upgrades ]; then
    echo 'APT::Periodic::Unattended-Upgrade "0";' > /etc/apt/apt.conf.d/99disable-unattended-upgrades
fi

echo "=== 3/6  Python virtual environment ==="
cd "$BOT_DIR"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip wheel

echo "=== 4/6  Python dependencies ==="
pip install -r requirements.txt

# Pre-download ChromeDriver so first bot start doesn't need internet
echo "=== 5/6  Pre-cache ChromeDriver ==="
python3 -c "from webdriver_manager.chrome import ChromeDriverManager; ChromeDriverManager().install()"

echo "=== 6/6  Install systemd service ==="
cp supplychain-bot.service /etc/systemd/system/supplychain-bot.service
systemctl daemon-reload
systemctl enable supplychain-bot.service

# Set correct ownership
chown -R ubuntu:ubuntu "$BOT_DIR"

echo ""
echo "============================================"
echo "  Bot installed successfully!"
echo "============================================"
echo ""
echo "To start the bot:"
echo "  sudo systemctl start supplychain-bot"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status supplychain-bot     # check status"
echo "  sudo journalctl -u supplychain-bot -f     # tail logs"
echo "  sudo systemctl restart supplychain-bot    # restart"
echo "  sudo systemctl stop supplychain-bot       # stop"
echo ""
echo "Instance recommendation: t3.small (2GB RAM)"
echo ""

#!/usr/bin/env bash
# Run as root on Ubuntu 24.04 after copying this project into /opt/hd2bot.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y python3-venv fontconfig fonts-noto-cjk fonts-noto-core \
  fonts-noto-color-emoji ca-certificates openssh-client git
id hd2bot >/dev/null 2>&1 || useradd --system --create-home \
  --home-dir /var/lib/hd2bot --shell /usr/sbin/nologin hd2bot
install -d -m 750 -o root -g hd2bot /etc/hd2bot
install -d -m 750 -o hd2bot -g hd2bot /var/lib/hd2bot/state /var/cache/hd2bot /var/log/hd2bot
cd /opt/hd2bot
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/python -m playwright install-deps chromium
runuser -u hd2bot -- env PLAYWRIGHT_BROWSERS_PATH=/var/cache/hd2bot/ms-playwright \
  .venv/bin/python -m playwright install chromium
fc-cache -f
install -m 644 deploy/hd2bot.service /etc/systemd/system/hd2bot.service
systemctl daemon-reload
# Real configuration/state must be copied separately before enabling the service.
printf '%s\n' 'Dependencies installed. Configure /etc/hd2bot/bot.env and runtime state before starting.'

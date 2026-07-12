#!/usr/bin/env bash
# Pork Phone hotline — Pi install. Run from repo root as a sudoer.
set -euo pipefail

sudo useradd --system --home /var/lib/hotline --create-home --shell /usr/sbin/nologin hotline 2>/dev/null || true
sudo mkdir -p /opt/hotline /etc/hotline /var/lib/hotline
sudo apt-get install -y python3-venv rsync
sudo rsync -a --delete --exclude tests --exclude .pytest_cache --exclude __pycache__ --exclude requirements-dev.txt --exclude pytest.ini hotline/server/ /opt/hotline/server/
sudo python3 -m venv /opt/hotline/venv
sudo /opt/hotline/venv/bin/pip install -r /opt/hotline/server/requirements.txt

if [ ! -f /etc/hotline/hotline.env ]; then
  sudo tee /etc/hotline/hotline.env >/dev/null <<'EOF'
HOTLINE_ENV=prod
HOTLINE_DATA_DIR=/var/lib/hotline
HOTLINE_ADMIN_TOKEN=CHANGE-ME
HOTLINE_ARI_PASSWORD=CHANGE-ME
HOTLINE_DELAY_N=4
EOF
  sudo chmod 600 /etc/hotline/hotline.env
  echo ">>> edit /etc/hotline/hotline.env (tokens) before starting"
fi
sudo chown -R hotline:hotline /var/lib/hotline
sudo cp hotline/server/deploy/hotline.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable hotline
echo "install done — start with: sudo systemctl start hotline"

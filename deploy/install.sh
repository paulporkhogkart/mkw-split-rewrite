#!/usr/bin/env bash
# One-time (idempotent) bootstrap of the MKW services on the Pi. Run with sudo, AFTER the
# repo is cloned, `npm --prefix pi install` has run, /etc/mkw/mkw.env is filled in, and the
# data has been migrated (see docs/pi-deploy.md). Starting the bot last keeps its announce
# watermark seeded over the already-imported PBs.
set -euo pipefail

REPO="${MKW_REPO:-/home/pi/mkw}"
DATA=/home/pi/mkw-data
ENVDIR=/etc/mkw

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }

install -d -o pi -g pi "$DATA"
install -d "$ENVDIR"
if [ ! -f "$ENVDIR/mkw.env" ]; then
  install -m 0640 -o root -g pi "$REPO/deploy/mkw.env.example" "$ENVDIR/mkw.env"
  echo "seeded $ENVDIR/mkw.env from the example - fill in DISCORD_* before the bot will start"
fi

# sudoers drop-in (validate, then install read-only).
visudo -cf "$REPO/deploy/sudoers.d/mkw-updater"
install -m 0440 -o root -g root "$REPO/deploy/sudoers.d/mkw-updater" /etc/sudoers.d/mkw-updater

# systemd units.
install -m 0644 \
  "$REPO/deploy/systemd/mkw-server.service" \
  "$REPO/deploy/systemd/mkw-bot.service" \
  "$REPO/deploy/systemd/mkw-web.service" \
  "$REPO/deploy/systemd/mkw-updater.service" \
  "$REPO/deploy/systemd/mkw-updater.timer" \
  /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now mkw-server.service mkw-bot.service mkw-web.service mkw-updater.timer
echo "installed + started."
echo "check: systemctl status mkw-server mkw-bot; systemctl list-timers mkw-updater.timer"

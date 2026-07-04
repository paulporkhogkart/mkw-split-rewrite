#!/usr/bin/env bash
# Pull-deploy: if GitHub has a newer release tag than we've deployed, check it out,
# install deps, and restart the services. Outbound-only (CGNAT-safe). Idempotent +
# fail-safe: on any error it aborts before writing the marker, so the running version
# stays up and the next timer tick retries.
set -euo pipefail

REPO="${MKW_REPO:-/home/pi/mkw}"
DATA="${MKW_DATA:-/home/pi/mkw-data}"
MARKER="$DATA/.deployed-tag"
KEY="${MKW_DEPLOY_KEY:-/home/pi/.ssh/mkw_deploy}"

mkdir -p "$DATA"
# Serialize: if a previous run is still going, skip this tick.
exec 9>"$DATA/.update.lock"
flock -n 9 || { echo "another update is in progress; skipping"; exit 0; }

cd "$REPO"
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

# Keep the checkout lean. The Pi only uses pi/ (server+bot), web/ (site build - which imports the
# desktop src/), src/, server/ (schema.sql + season0 data read at boot) and deploy/ (this script +
# the units). Everything else - screenshots/ (~840MB), captures/ (~350MB), images/, mkw_tracker/,
# src-tauri/, docs/, tests/, ... - is desktop-only. `sparse-checkout set` is idempotent and
# self-healing: on a legacy full clone it drops those dirs from the working tree on the next tick
# (~1.2GB reclaimed) and they stay gone across every future --force checkout; on an already-sparse
# clone it's a near no-op. It canNOT shrink an existing .git (those blobs are already downloaded) -
# reclaim that ~800MB once with a --filter=blob:none re-clone (see docs/pi-deploy.md section 13).
git sparse-checkout set pi web src server deploy \
  || echo "warn: sparse-checkout not applied (git too old?); continuing with a full checkout"

git fetch --tags --prune --quiet origin

latest="$(git tag -l 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | head -n1)"
if [ -z "$latest" ]; then echo "no release tags yet; nothing to deploy"; exit 0; fi

current="$(cat "$MARKER" 2>/dev/null || true)"
if [ "$latest" = "$current" ]; then echo "already up to date ($current)"; exit 0; fi

echo "deploying $latest (was ${current:-none})"
git checkout -q --force "tags/$latest"
npm --prefix "$REPO/pi" install --no-audit --no-fund
npm --prefix "$REPO/web" install --no-audit --no-fund
npm --prefix "$REPO/web" run build
sudo systemctl restart mkw-server mkw-bot mkw-web
echo "$latest" > "$MARKER"
echo "deployed $latest"

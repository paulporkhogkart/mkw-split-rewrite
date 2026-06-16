# Website (thekartoff.com) deploy runbook

The public live-cards site (`web/`), served by the `mkw-web` systemd service on the Pi
alongside `mkw-server`/`mkw-bot`, reachable at `https://thekartoff.com` (and `www.`) through
the existing Cloudflare tunnel. Steady-state it auto-updates with the others: you `git tag` +
push, the Pi rebuilds + restarts within ~2 minutes.

## 0. Prerequisites
- The Pi server + bot + tunnel are already up (see `docs/pi-deploy.md`).
- `thekartoff.com` is an active Cloudflare zone (it is — registered through Cloudflare).
- Node 24 on the Pi (already installed for the server).

## 1. Push the repo + a release tag (dev box)
```bash
git push origin main
git tag v0.4.0           # pick your next version
git push origin v0.4.0
```

## 2. Build the site + (re)install the services (Pi)
```bash
ssh pi@192.168.1.21
cd /home/pi/mkw
export GIT_SSH_COMMAND="ssh -i ~/.ssh/mkw_deploy -o IdentitiesOnly=yes"
git fetch --tags origin && git checkout v0.4.0
npm --prefix web install --no-audit --no-fund
npm --prefix web run build                           # -> web/dist
sudo MKW_REPO=/home/pi/mkw bash deploy/install.sh    # now also installs + enables mkw-web
systemctl status mkw-web --no-pager
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8788    # 200
```

## 3. Cloudflare tunnel route (Pi)
Route the apex + www through the existing tunnel:
```bash
cloudflared tunnel route dns <TUNNEL> thekartoff.com
cloudflared tunnel route dns <TUNNEL> www.thekartoff.com
nano ~/.cloudflared/config.yml
```
Add, **above** the catch-all `- service: http_status:404`:
```yaml
  - hostname: thekartoff.com
    service: http://localhost:8788
  - hostname: www.thekartoff.com
    service: http://localhost:8788
```
(If `route dns` errors about the management cert, use the dashboard proxied-CNAME method from
`docs/pi-deploy.md` §7 — point `thekartoff.com` + `www` at `<TUNNEL-UUID>.cfargotunnel.com`.)
```bash
cloudflared tunnel ingress validate
sudo systemctl restart cloudflared
curl -s -o /dev/null -w "%{http_code}\n" https://thekartoff.com   # 200
```

## 4. Verify
- `https://thekartoff.com` loads the five cards (offline until someone opens the app).
- Open the desktop app + start a race -> that card goes live within ~1s (timer + bar tick).
- `https://www.thekartoff.com` loads the same.
- `sudo reboot`; after it returns, `systemctl is-active mkw-web` -> `active`.

## 5. Steady-state updating
Same as the server: `git tag vX.Y.Z && git push origin vX.Y.Z`. Within ~2 min the Pi checks
out the tag, runs `npm --prefix web install` + `npm --prefix web run build`, and restarts
`mkw-web` (see `deploy/update.sh`). Watch: `journalctl -u mkw-updater -f`.

## 6. Troubleshooting
- **502 on thekartoff.com** — `mkw-web` is down (`systemctl status mkw-web`) or `web/dist` is
  missing (rebuild). `journalctl -u mkw-web -n 50`.
- **Blank page / module MIME errors** — the build didn't run or `web/dist` is stale; rebuild.
- **Cards never go live** — the browser can't reach `wss://api.thekartoff.com/v1/presence`;
  confirm the `api` hostname is still routed (it's separate from the web host).

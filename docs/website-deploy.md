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

## 3. Point thekartoff.com + www at the tunnel (Pi)

`api.thekartoff.com` already works, so the tunnel, its credentials, and the running
`cloudflared` service are all in place — you're just adding two more hostnames to the
**same** tunnel. Two parts per hostname: **(A)** a DNS record so Cloudflare's edge sends the
traffic into the tunnel, and **(B)** an ingress rule so the tunnel forwards it to the local
site on `:8788`.

Find the tunnel name/UUID `api` already uses:
```bash
cloudflared tunnel list
grep -E 'tunnel:|credentials' ~/.cloudflared/config.yml   # the UUID is the `tunnel:` value
```

### A. DNS records (apex + www -> the tunnel)
Use whichever method you used for `api`:

**CLI** (needs the account cert `~/.cloudflared/cert.pem`):
```bash
cloudflared tunnel route dns <TUNNEL> thekartoff.com
cloudflared tunnel route dns <TUNNEL> www.thekartoff.com
```
If it errors *"Cannot determine default origin certificate path"*, run `cloudflared tunnel
login` once (prints a URL — open it, pick `thekartoff.com`; it writes `cert.pem` and does
**not** disturb the running tunnel), then re-run the two commands.

**Dashboard** (no cert): Cloudflare -> `thekartoff.com` -> **DNS -> Add record**, twice, each
**Proxied** (orange cloud). `<TUNNEL-UUID>` is the `tunnel:` value from above:
```
Type=CNAME  Name=@     Target=<TUNNEL-UUID>.cfargotunnel.com
Type=CNAME  Name=www   Target=<TUNNEL-UUID>.cfargotunnel.com
```
(`@` is the apex/root; Cloudflare flattens the apex CNAME automatically.)

### B. Ingress rule (tunnel -> local site on :8788)
Add the two hostnames **above** the catch-all `404`, alongside the existing `api` rule:
```bash
nano ~/.cloudflared/config.yml
```
```yaml
ingress:
  - hostname: api.thekartoff.com
    service: http://localhost:8787        # already there
  - hostname: thekartoff.com
    service: http://localhost:8788
  - hostname: www.thekartoff.com
    service: http://localhost:8788
  - service: http_status:404              # catch-all MUST stay last
```
First match wins, so order matters; the bare `http_status:404` line stays at the bottom.
(Dashboard-managed tunnel with no `config.yml`? Add the two **Public Hostnames** under the
tunnel in the Zero Trust dashboard instead, both -> `http://localhost:8788`.)

Validate + reload (restart re-reads the config; `api` blips for ~a second, then both are up):
```bash
cloudflared tunnel ingress validate
sudo systemctl restart cloudflared
curl -s -o /dev/null -w "%{http_code}\n" https://thekartoff.com       # 200
curl -s -o /dev/null -w "%{http_code}\n" https://www.thekartoff.com   # 200
```
First request after a fresh DNS record can take a minute. **502** = `mkw-web` isn't up or the
ingress port is wrong; **404 from Cloudflare** = the hostname isn't matching an ingress rule.

*Optional — one canonical host:* serving both is fine (the SPA is origin-agnostic). To force
e.g. `www` -> apex, add a Cloudflare **Redirect Rule** (Rules -> Redirect Rules) rather than
routing both. Not required.

## 4. Verify
- `https://thekartoff.com` loads the five cards (offline until someone opens the app).
- Open the desktop app + start a race -> that card goes live within ~1s (timer + bar tick).
- `https://www.thekartoff.com` loads the same.
- `sudo reboot`; after it returns, `systemctl is-active mkw-web` -> `active`.

**If a card shows the old name (or a blank portrait) instead of `Gub`:** the display name
lives in the **server** DB — the app and the site both read it from the presence stream, and
`mkw-server` caches the roster in memory at startup. Run this one-time rename on the Pi and
restart so it re-seeds:
```bash
sqlite3 /home/pi/mkw-data/mkw.db "UPDATE players SET display_name='Gub' WHERE display_name='Adymer' COLLATE NOCASE;"
sudo systemctl restart mkw-server mkw-bot
```

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

# Pi deployment + migration runbook

The new `pi/` server + Discord bot, running on `pi@192.168.1.21` as auto-updating systemd
services, reachable off-network at `https://api.thekartoff.com` through the existing Cloudflare
tunnel. Steady-state updates: you `git tag` + push; the Pi self-updates within ~2 minutes.

Layout it creates:

| Path | What |
|---|---|
| `/home/pi/mkw` | the git clone (checked out at a release tag) |
| `/home/pi/mkw-data` | `mkw.db`, `bot-state.json`, `.deployed-tag` (outside the clone) |
| `/etc/mkw/mkw.env` | env + secrets (outside the clone) |
| `/home/pi/.ssh/mkw_deploy` | read-only GitHub deploy key |

## 0. Prerequisites

- A **64-bit** Pi OS: `uname -m` → `aarch64`. (32-bit `armv7l` has no Node 24 build.)
- Cloudflare account with the existing tunnel (`~/.cloudflared/config.yml` + credentials).
- A Discord **bot token** + the target channel id (developer portal → your application → Bot).
- `thekartoff.com` registered (nameservers not yet pointed at Cloudflare).

## 1. Push the repo + a release tag to GitHub (on the dev box)

The updater tracks GitHub tags, so the code must be on `origin` first.

```bash
git push origin main
git tag v0.3.0          # pick your next version
git push origin v0.3.0
```

## 2. Install Node 24 on the Pi

```bash
ssh pi@192.168.1.21
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt-get install -y nodejs git python3 sqlite3
# (sqlite3 is just the CLI for the .tables sanity check in Step 4; the import + server use
#  Python's built-in sqlite3 module + Node's node:sqlite, so it isn't load-bearing.)
node -v                                   # v24.x
node -e "require('node:sqlite')" && echo "node:sqlite OK"   # must print OK, no flag needed
```

## 3. Read-only deploy key + clone

```bash
ssh-keygen -t ed25519 -f ~/.ssh/mkw_deploy -N "" -C "mkw-pi-deploy"
cat ~/.ssh/mkw_deploy.pub
```

On GitHub: repo → **Settings → Deploy keys → Add deploy key** → paste the `.pub`, leave
**Allow write access** unchecked.

```bash
GIT_SSH_COMMAND="ssh -i ~/.ssh/mkw_deploy -o IdentitiesOnly=yes" \
  git clone git@github.com:<you>/<repo>.git /home/pi/mkw
cd /home/pi/mkw
GIT_SSH_COMMAND="ssh -i ~/.ssh/mkw_deploy -o IdentitiesOnly=yes" git checkout v0.3.0
npm --prefix pi install --no-audit --no-fund
```

## 4. Configure env + secrets

```bash
sudo install -d /etc/mkw
sudo install -m 0640 -o root -g pi deploy/mkw.env.example /etc/mkw/mkw.env
sudo nano /etc/mkw/mkw.env      # fill DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, (DISCORD_GUILD_ID)
```

Confirm `STATS_PORKER_DB=/home/pi/porker-data/databases/botdata.db` and that the body tables
exist:

```bash
sqlite3 /home/pi/porker-data/databases/botdata.db ".tables"   # expect Measurements, AddymerMeasurements, ...
```

## 5. Migrate the legacy data (BEFORE first bot start)

The bot seeds its announce watermark to the newest run on first launch, so the historical PBs
must already be imported — otherwise the bot would announce hundreds of them. Stop the old bot
first so it stops writing + double-posting.

```bash
sudo systemctl stop mkpb && sudo systemctl disable mkpb

# Import PBs/WRs from a snapshot of the legacy DB into the server DB.
mkdir -p /home/pi/mkw-data
cp ~/mkwpb2/kart-off/data/hogkart.db /home/pi/hogkart-snapshot.db
cd /home/pi/mkw
python3 -m server.importer \
  --legacy-db /home/pi/hogkart-snapshot.db \
  --out /home/pi/mkw-data/mkw.db
# prints: players / courses / S0 runs / world_records / carryover seeds
```

(Body stats need no import — the server reads `botdata.db` in place, read-only, alongside the
pork bot's writer.)

## 6. Install + start the services

```bash
sudo MKW_REPO=/home/pi/mkw bash deploy/install.sh
systemctl status mkw-server mkw-bot --no-pager
curl -s http://localhost:8787/health        # {"status":"ok"}
```

## 7. Cloudflare domain + tunnel route

> **thekartoff.com is registered through Cloudflare Registrar**, so it's already an active zone on
> Cloudflare's nameservers — skip "add a site", the nameserver change, and the propagation wait;
> go straight to the tunnel route. (Bought elsewhere? You'd first add it as a site in the
> dashboard, point the registrar's nameservers at Cloudflare, and wait for **Active**.) The domain
> and the `cloudflared` tunnel must be in the same Cloudflare account (they are).

On the Pi, route the subdomain through the existing tunnel and add the ingress rule.

> `tunnel list` / `route dns` need the **account management cert** `~/.cloudflared/cert.pem`, which
> is separate from the per-tunnel credentials JSON that *runs* your tunnel — so a Pi that only has
> the credentials file errors with *"Cannot determine default origin certificate path"*. Two fixes:
> - **Get the cert** (then the commands below work): `cloudflared tunnel login` — it prints a URL;
>   open it in any browser, pick `thekartoff.com`, and it writes `cert.pem` to the Pi. Headless-ok,
>   and it doesn't disturb the running tunnel.
> - **Or skip the CLI route**: add a **Proxied CNAME** `api` → `<TUNNEL-UUID>.cfargotunnel.com` in
>   the dashboard (the UUID is the `tunnel:` value in `~/.cloudflared/config.yml`).

```bash
cloudflared tunnel list                                  # note the tunnel name/UUID
cloudflared tunnel route dns <TUNNEL> api.thekartoff.com
nano ~/.cloudflared/config.yml
```

Add, **above** the catch-all `- service: http_status:404`:

```yaml
ingress:
  - hostname: api.thekartoff.com
    service: http://localhost:8787
  # ...existing rules...
  - service: http_status:404
```

```bash
cloudflared tunnel ingress validate
sudo systemctl restart cloudflared
curl -s https://api.thekartoff.com/health    # {"status":"ok"} through the tunnel
```

## 8. Mint tokens + repoint the desktop apps

Reads + writes need a token. Mint one per player (players exist after the import):

```bash
cd /home/pi/mkw/pi
MKW_DB=/home/pi/mkw-data/mkw.db npm run mint-token -- Paul     # prints the token once
# repeat for Gub / Alex / Aliias / Luke
```

In each person's desktop app **Settings → Server**: set the server URL to
`https://api.thekartoff.com` and paste that person's token. The personal `/explorer` page opens
at `https://api.thekartoff.com/explorer?token=YOUR_TOKEN`.

## 9. Verification

- `curl -s https://api.thekartoff.com/health` → ok.
- `curl -s -o /dev/null -w "%{http_code}" https://api.thekartoff.com/v1/seasons` → `401`.
- `curl -s "https://api.thekartoff.com/v1/seasons?token=<paul>"` → JSON.
- Set a quick PB on the dev box → it announces in Discord within ~1s, and the old bot is silent.
- `sudo systemctl start mkw-updater.service && journalctl -u mkw-updater -n 5 --no-pager`
  → "already up to date".
- `sudo reboot`; after it comes back, `systemctl is-active mkw-server mkw-bot cloudflared` →
  `active`, and `systemctl list-timers mkw-updater.timer` shows the next run.

## 10. Steady-state updating

```bash
# on the dev box, after merging work to main:
git push origin main
git tag v0.3.1 && git push origin v0.3.1
```

Within ~2 minutes the Pi fetches the tag, checks it out, `npm install`s, and restarts. Watch:

```bash
journalctl -u mkw-updater -f
```

## 11. Rollback

The updater only ever moves to the highest tag. To roll back, delete the bad tag on GitHub and
push a higher tag containing the fix (or, on the Pi, `git checkout <good-tag>` + `npm --prefix
pi install` + `sudo systemctl restart mkw-server mkw-bot` and write that tag into
`/home/pi/mkw-data/.deployed-tag`).

## 12. Troubleshooting

- **Bot won't start** — `journalctl -u mkw-bot -n 50`. Usually a missing `DISCORD_BOT_TOKEN` /
  `DISCORD_CHANNEL_ID` in `/etc/mkw/mkw.env` (then `sudo systemctl restart mkw-bot`).
- **Updater not deploying** — `journalctl -u mkw-updater -n 30`. Check the deploy key works:
  `GIT_SSH_COMMAND="ssh -i ~/.ssh/mkw_deploy -o IdentitiesOnly=yes" git -C /home/pi/mkw ls-remote --tags origin`.
- **502 through the tunnel** — the server isn't up (`systemctl status mkw-server`) or the
  ingress hostname/port is wrong (`cloudflared tunnel ingress validate`).
- **Monitor shows no friends' trails after switching to the tunnel** — the desktop token is
  missing/wrong in Settings → Server (reads now require it).
- **`node:sqlite` error on boot** — Node is too old; reinstall Node 24 (Step 2).
- **Live presence cards freeze after a quiet spell through the tunnel** — Cloudflare drops idle
  WebSockets after ~100s; the desktop reconnects on its own (the presence client carries an idle
  heartbeat, see `pi/src/presence/hub.ts`). If a friend's cards don't recover, that's a client
  heartbeat gap to chase, not a server one — `/v1/presence` is unauthenticated and unchanged here.

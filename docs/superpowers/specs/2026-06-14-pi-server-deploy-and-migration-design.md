# Pi server/bot auto-deploy + tunnel + migration — design

**Date:** 2026-06-14
**Status:** Approved (design); pending spec review → implementation plan

## Goal

Stand up the new `pi/` stack (Hono server + Discord bot) on the Raspberry Pi
(`pi@192.168.1.21`) as a first-class deployment: reachable by off-network friends through a
Cloudflare tunnel on a new domain, auto-updating from GitHub releases, surviving reboots and
crashes, with the legacy data (PBs/WRs + body stats) migrated in. Replaces the legacy `mkpb`
Python bot.

## Context (current state)

- **`pi/` is a no-build Node app.** `tsx` runs TypeScript directly; storage is the built-in
  `node:sqlite` (`DatabaseSync`). Deploying = sync source + `npm install` (pulls the ARM
  `esbuild`/native binaries on-device) + restart. No compiled artifact.
- **Server** (`pi/src/server.ts`, port `8787`): Hono HTTP + WS, writes `mkw.db`, reads
  `porker.db` read-only (ATTACH) for body stats, runs the WR scraper in-process. Schema is
  applied + additively migrated on every boot by `applySchema` (`pi/src/db/connect.ts`).
- **Bot** (`pi/src/bot/index.ts`): needs `.env`, connects to the server's `/v1/events` WS,
  reads `mkw.db` directly (file, not HTTP), owns a `bot-state.json` announce watermark.
- **Migration precedent exists**: `server/importer.py` idempotently imports a legacy
  `hogkart.db` (PBs → Season 0 + carryover Season 1, WRs → `world_records`). Pure stdlib
  (`sqlite3`), no pip deps.
- **Body stats** are read by the stats engine via `STATS_PORKER_DB` (default `porker.db`),
  read-only, coexisting with the pork bot's writer. `PORKER_MAP` already targets the
  `Measurements`/`AddymerMeasurements`/… tables that live in `botdata.db`.
- **Auth today**: writes (`/v1/runs*`, `/v1/screen-intervals`, `/v1/me/*`) require a bearer
  token; reads/stats/`/explorer`/`/v1/events` are open.
- The desktop app already holds a configurable `server_url` + `token`
  (`src-tauri/src/sync.rs::sync_set_config`).

## Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Deploy trigger | **Pull-based**: Pi polls GitHub release tags on a systemd timer (CGNAT-proof, mirrors the Tauri updater) |
| Trigger granularity | **Tag-based** (`v[0-9]*.[0-9]*.[0-9]*`), matching `release.yml` + the desktop updater. Tracking `origin/main` HEAD instead is a one-line `update.sh` change. |
| Exposure | **Token for read *and* write** (header `Authorization: Bearer` OR `?token=`). More private than Cloudflare Access, no SSO friction. `/health` + the two WS streams (`/v1/events`, `/v1/presence`) stay open (see Component 6). |
| Domain | **`api.thekartoff.com`** → server; bare `thekartoff.com` reserved for the future website. |
| Pi state | **Greenfield**: new stack never ran here; deploy fresh and retire the legacy `mkpb` bot. |

## Architecture

Three system services + a deploy timer, a one-time data migration, a tunnel ingress rule, and
a server-side auth change. Data and secrets live **outside** the git clone so updates never
touch them.

### On-disk layout (Pi)

| Path | Owner | Purpose |
|---|---|---|
| `/home/pi/mkw/` | `pi` | git clone (the repo; checked out at a release tag) |
| `/home/pi/mkw-data/` | `pi` | `mkw.db` (+ `-wal`/`-shm`), `bot-state.json`, `.deployed-tag` marker |
| `/home/pi/porker-data/databases/botdata.db` | (existing) | body stats, read in place (read-only) |
| `/etc/mkw/mkw.env` | `root:pi` 0640 | env (incl. the Discord bot token) — out of the repo tree |
| `/home/pi/.ssh/mkw_deploy` | `pi` | read-only GitHub deploy key (private half) |
| `/etc/systemd/system/mkw-*.{service,timer}` | `root` | units |
| `/etc/sudoers.d/mkw-updater` | `root` | narrow `systemctl` grant for the updater |

`WorkingDirectory=/home/pi/mkw/pi` for both services; all DB/state paths in `mkw.env` are
**absolute** so CWD is irrelevant.

### Component 1 — Pull updater (`deploy/update.sh` + timer)

`mkw-updater.timer` fires `mkw-updater.service` (oneshot, `User=pi`) every ~2 min
(`OnBootSec=2min`, `OnUnitActiveSec=2min`). `update.sh` (illustrative):

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO=/home/pi/mkw
MARKER=/home/pi/mkw-data/.deployed-tag
exec 9>/home/pi/mkw-data/.update.lock && flock -n 9 || exit 0   # no overlap
cd "$REPO"
export GIT_SSH_COMMAND="ssh -i /home/pi/.ssh/mkw_deploy -o IdentitiesOnly=yes"
git fetch --tags --prune --quiet origin
latest=$(git tag -l 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | head -n1)
[ -z "$latest" ] && { echo "no release tags yet"; exit 0; }
[ "$latest" = "$(cat "$MARKER" 2>/dev/null || true)" ] && exit 0
echo "deploying $latest"
git checkout -q --force "tags/$latest"
npm --prefix "$REPO/pi" install --no-audit --no-fund
sudo systemctl restart mkw-server mkw-bot
echo "$latest" > "$MARKER"
```

Properties: outbound-only over SSH (deploy key works for private *or* public repos); fail-safe
(`set -e` aborts before the marker write, so a bad `npm install` leaves the running version up
and retries next tick); idempotent; no GitHub secrets on the Pi. Schema auto-migrates because
the restart re-runs `applySchema`. **Rollback** = delete the bad tag on GitHub (updater only
ever moves to the highest tag) or push a higher fixed tag.

### Component 2 — systemd units (`deploy/systemd/`)

- **`mkw-server.service`** — `Type=simple`, `User=pi`, `WorkingDirectory=/home/pi/mkw/pi`,
  `EnvironmentFile=/etc/mkw/mkw.env`, `ExecStart=/usr/bin/node --no-warnings --import tsx src/server.ts`,
  `Restart=always`, `After=network-online.target`, `WantedBy=multi-user.target`.
- **`mkw-bot.service`** — same shape, `ExecStart=… src/bot/index.ts`, `After=mkw-server.service`.
  (Under systemd we use `EnvironmentFile`, not the `npm run bot` `--env-file=.env`.)
- **`mkw-updater.service`** (oneshot, `ExecStart=/home/pi/mkw/deploy/update.sh`) +
  **`mkw-updater.timer`** (`WantedBy=timers.target`).

All `WantedBy=multi-user.target`/`timers.target` so they start on boot and restart on crash.
`cloudflared` remains its own existing unit.

### Component 3 — Least-privilege updater grant (`deploy/sudoers.d/mkw-updater`)

```
pi ALL=(root) NOPASSWD: /usr/bin/systemctl restart mkw-server mkw-bot, /usr/bin/systemctl start mkw-server mkw-bot
```

Installed via `visudo -cf` validation. Lets the `pi`-owned updater restart exactly the two
units, nothing else. git + npm run as `pi` (owns the repo + `node_modules`).

### Component 4 — `deploy/install.sh` (one-time bootstrap, idempotent)

Copies units to `/etc/systemd/system`, creates `/etc/mkw/` + seeds `mkw.env` from
`deploy/mkw.env.example` if absent, installs + validates the sudoers drop-in,
`systemctl daemon-reload`, `enable --now` server/bot + `mkw-updater.timer`. Safe to re-run.

### Component 5 — `deploy/mkw.env.example`

```
PORT=8787
MKW_DB=/home/pi/mkw-data/mkw.db
BOT_STATE=/home/pi/mkw-data/bot-state.json
STATS_PORKER_DB=/home/pi/porker-data/databases/botdata.db
DISCORD_BOT_TOKEN=
DISCORD_CHANNEL_ID=
DISCORD_GUILD_ID=
# Optional: BOT_WS_URL, MKWRS_URL, MKWRS_MIN_INTERVAL_SEC, MKWRS_MAX_INTERVAL_SEC
```

### Component 6 — Token auth for reads + writes (server change)

Generalize auth and apply it everywhere except `/health`.

- **New middleware** `requireTokenAny(db)` (extends `pi/src/api/auth.ts`): accept the token from
  `Authorization: Bearer <t>` **or** `?token=<t>` (browsers/WS can't set headers), set
  `playerId`/`playerName`, else 401.
- **Apply to**: one `app.use('*', …)` in `createApp` (after `/health`) gates every HTTP route
  it mounts — `readsRoutes`, the stats app, `screenRoutes`, the run-writes, and `/explorer`.
  An `OPEN` set exempts `/health` (tunnel + updater healthcheck) **and the two WS streams**:
  `/v1/events` stays open because the on-Pi **bot** subscribes to it over localhost with no
  token, and it only carries PB/WR events already announced publicly to Discord; `/v1/presence`
  keeps its existing optional-token (receive-only) model. (Gating the WS streams is a clean
  later follow-up if desired; it isn't the body-fat privacy concern.) `readsRoutes` is left
  unchanged — its existing optional-bearer `is_me` logic still works for the header tokens the
  desktop sends; the gate only adds the requirement.
- **Desktop read calls that must add the token** (audited — gating reads breaks these
  otherwise): in `src-tauri/src/sync.rs::fetch_course_reads`, add `.bearer_auth(&cfg.token)` to
  `/v1/friends-pbs` and `/v1/players/{id}/trails` (today only `/v1/me/pb-splits` carries it).
  `/v1/roster`, `/v1/me/pbs`, `/v1/me/pb-splits`, and the write paths already send it.
- **Browser reads**: `pi/stat-explorer.html` reads its own `?token=` and attaches it to its
  `/v1/stats` fetches; the guide opens `https://api.thekartoff.com/explorer?token=<t>`.
- **Unaffected**: the Discord bot (reads the DB file, not HTTP) and the WR scraper (in-process).
- **Tests**: a new `requireTokenAny` unit test (header / `?token=` / none→401 / bad→401); read
  routes (`reads.test.ts`, the stats reads + `/explorer` in `screen.test.ts`, a case in
  `app.test.ts`) updated to 401 without a token and 200 with a header or `?token=`. The
  `/v1/events` WS test (`ws.test.ts`) and the direct-`createStatsApp` tests (`stats.test.ts`)
  stay unchanged (both bypass the gate).

> Future: when the public website (sub-project C) is built, it chooses its own read tier (a
> dedicated server/service token, or a public read carve-out). Out of scope here.

### Component 7 — Cloudflare tunnel + domain

Runbook (manual, user-driven — outward-facing):
1. Add `thekartoff.com` to Cloudflare; change nameservers at the registrar; wait for **Active**.
2. `cloudflared tunnel route dns <tunnel> api.thekartoff.com` (creates the proxied CNAME).
3. Add an ingress rule to `~/.cloudflared/config.yml` **above** the catch-all:
   ```yaml
   ingress:
     - hostname: api.thekartoff.com
       service: http://localhost:8787
     # …existing rules…
     - service: http_status:404
   ```
4. `cloudflared tunnel ingress validate` → `sudo systemctl restart cloudflared`.

Cloudflare terminates TLS at the edge; the tunnel encrypts edge→Pi, so `http://localhost:8787`
origin is fine. WebSockets proxy through Cloudflare; the design note: confirm an app-level
keepalive/ping against Cloudflare's ~100s idle WS timeout (the `PresenceHub` already sweeps
stale sockets every 5s — verify it pings, add one if not). Friends set their desktop
`server_url = https://api.thekartoff.com`.

### Component 8 — Data migration (one-time)

Order matters (the bot watermark self-heals to `MAX(runs.id)` on first launch, so the import
must precede the bot's first start, else hundreds of historical PBs get announced):

1. `sudo systemctl stop mkpb && sudo systemctl disable mkpb` (stop the legacy bot writing/posting).
2. Snapshot + import PBs/WRs:
   ```bash
   cp ~/mkwpb2/kart-off/data/hogkart.db /home/pi/hogkart-snapshot.db
   cd /home/pi/mkw
   python3 -m server.importer --legacy-db /home/pi/hogkart-snapshot.db --out /home/pi/mkw-data/mkw.db
   ```
   (Idempotent/re-runnable; pure stdlib, no pip.)
3. Body stats: point `STATS_PORKER_DB=/home/pi/porker-data/databases/botdata.db` (already in
   `mkw.env`); verify `sqlite3 botdata.db ".tables"` shows the `…Measurements` tables. Read in
   place so stats stay live as the pork bot keeps writing.
4. *Then* start the services (`install.sh`), so the bot seeds its watermark over the imported
   rows.

### Component 9 — Runbook doc (`docs/pi-deploy.md`)

A single ordered guide covering everything above, in first-run order, plus a "what each
subsequent `git tag && push` does" section and troubleshooting. Sections:
prerequisites → Node install → deploy key + clone → env + secrets → data migration → tunnel
→ `install.sh` → retire `mkpb` → friends repoint → verification → updating (the steady state)
→ rollback → troubleshooting.

## Prerequisites & sequencing

0. **Push to GitHub.** `main` is ~61 commits ahead of `origin`, unpushed. Push `main` + at
   least one `v*.*.*` tag — the updater tracks GitHub tags. (Origin is the existing GitHub
   remote.)
1. **64-bit Pi OS + Node ≥ dev box** (≥22.5 for `node:sqlite`; the scripts don't pass
   `--experimental-sqlite`, so use a version where it's unflagged — pin **Node 24** to match
   CI). Verify `uname -m` = `aarch64` and `node -e "require('node:sqlite')"` exits 0. (32-bit
   `armv7l` has no Node 24 build → 64-bit OS required.)
2. **Read-only deploy key** added to the GitHub repo; clone origin is the SSH URL.
3. First-run order: clone → `npm --prefix pi install` → write `/etc/mkw/mkw.env` →
   import `hogkart.db` → `deploy/install.sh` (starts everything) → retire `mkpb` → tunnel
   route → friends repoint.

## Verification

- `curl https://api.thekartoff.com/health` → `{"status":"ok"}` (tunnel up).
- Token'd read returns data; the same read **without** a token → 401.
- A test PB lands in the Discord channel within ~1s (new bot live, old bot silent).
- `systemctl start mkw-updater.service` with no new tag logs "already up to date" and is a
  no-op; pushing a higher tag flips the deployed marker within ~2 min and restarts cleanly.
- `sudo reboot` → all three units (server, bot, updater.timer) come back; `cloudflared` too.
- Desktop monitor (Paul's box) still reads friends-PBs/trails after the token change.

## Out of scope

- The public website (sub-project C) and its read tier.
- Cloudflare Access / Zero Trust (token auth chosen instead).
- Cross-compiling/building an artifact for the Pi (on-device `npm install` instead).
- Any change to the Windows desktop release pipeline (`release.yml`) beyond it being the tag
  source; the Pi deploy is independent of the Windows build's success.

## Risks & mitigations

- **Gating reads breaks an un-audited consumer.** Mitigation: the enumerated `sync.rs` +
  `stat-explorer.html` changes; tests asserting 401/200; verify the desktop monitor post-change.
- **Bot announce-spam on import.** Mitigation: import strictly before first bot start (ordering
  baked into the guide + `install.sh` run last).
- **WS dropped by Cloudflare's idle timeout.** Mitigation: confirm/add app-level ping.
- **Node-version drift** (`node:sqlite` flag/behavior). Mitigation: pinned Node 24 + the
  `require('node:sqlite')` smoke check in the guide.
- **Deploy mid-restart races a live race upload.** Low impact: the desktop outbox retries; the
  restart is sub-second. No mitigation needed beyond the outbox that already exists.
- **`git checkout --force` clobbering local edits.** Mitigation: data + env live outside the
  repo; the clone is treated as read-only/disposable.

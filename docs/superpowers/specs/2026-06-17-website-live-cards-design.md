# thekartoff.com — live cards site (v1) — design

**Date:** 2026-06-17
**Status:** approved, pre-implementation
**Topic:** A public website at `thekartoff.com` that renders the pbenguin live player cards, connected to the season server, auto-updating + Pi-hosted alongside the existing services.

## Goal

Stand up `thekartoff.com` showing the **live player-card wall** — the same timing-tower cards from the pbenguin desktop app (`PlayerCard`), live and connected to the season server, behaving exactly as they do in-app. This is the whole of v1; the broader site (leaderboards, course pages, etc.) comes later as separate increments. The cards page **is** the landing page for now.

## Decisions (locked during brainstorming)

| Decision | Choice |
|---|---|
| v1 scope | The live card wall is the site. Landing page = the wall. |
| Access | **Public.** No login. (The `/v1/presence` stream is already token-less / receive-only.) |
| Card size | Native ~**189px** wide (= the desktop panel's `946/5`). Do **not** scale up. |
| Wall layout | One centered row of all 5; shrink slightly to hold the row, then wrap-and-center; one column on a phone. |
| Serving | **Separate `mkw-web` service** (not folded into `mkw-server`) — matches the existing service split. |
| Server changes | **None** for v1. The presence broadcast already serves token-less viewers. |

## Architecture

### New app: `web/`

A plain **Vite + Svelte SPA**, sibling to the existing Tauri frontend, with its own `package.json` / `vite.config.js` / `index.html`. It is build-only (no Tauri, no Rust).

```
web/
├── package.json          svelte + vite (+ vitest) only
├── vite.config.js        svelte plugin; build outDir = web/dist
├── index.html            #app mount + <title>thekartoff</title>
├── serve.mjs             dependency-free static server (prod)
└── src/
    ├── main.js           mounts App, imports ../../src/theme.css, starts presence client
    ├── App.svelte        header (wordmark + live indicator) + the card-wall layout
    └── presenceClient.js read-only presence WebSocket → shared stores
```

### Reused from the desktop app (imported from `../src`, unchanged)

Verified Tauri-free (a repo-wide grep shows only `sync.js`, `discord.js`, `ipc.js` import Tauri — none are in the card render path):

- Components: `PlayerCard.svelte`, `Fire.svelte` (PlayerCard's transitive deps). NOT `PlayerPanel.svelte` — its grid stretches cards to fill width (desktop behavior); the web uses its own `web/src/CardWall.svelte` (the approved wall layout) which renders `PlayerCard`.
- Logic/lib: `playerCard.js` (view-model), `playerFigures.js` (portrait URLs via `import.meta.glob`), `raceTimerBuffer.js`, `fireState.js`, `cardSettings.js`, `discordFormat.js`
- State: `stores.js` (reuses the `presence`, `serverConnection`, `myPlayerId` writables)
- Connection helper: `handlePresenceMessage` from `presence.js` (pure read-path: parses `presence_snapshot` / `presence_update`, updates the `presence` store, feeds `raceTimerBuffer`, marks the link connected)- `theme.css` (design tokens)

Reusing the real modules means the site tracks any future card tuning automatically — no fork, no copy.

### Live data flow

```
api.thekartoff.com  /v1/presence  (token-less, receive-only)
        │  presence_snapshot + presence_update frames
        ▼
web/src/presenceClient.js  (WebSocket, auto-reconnect w/ backoff)
        │  handlePresenceMessage(raw)
        ▼
shared stores: presence{}, serverConnection, raceTimerBuffer
        ▼
CardWall.svelte → PlayerCard.svelte  (one shared clock; fast while racing)
```

- `presenceClient.js` opens `wss://api.thekartoff.com/v1/presence` with **no token** (receive-only), and on each message calls the shared `handlePresenceMessage`. On close it marks `serverConnection` disconnected and reconnects with capped exponential backoff. It only receives — it never sends frames, so there is no local-self echo and no outbound throttle (those are desktop-only concerns).
- At boot, `main.js` reads the API origin (`VITE_API_BASE`, default `https://api.thekartoff.com`) and starts the read-only presence client against it.
- **Never-empty wall:** the server's `PresenceHub` seeds every roster player offline at startup, so the snapshot always contains all 5 players. Offline players render as grey "last seen" cards. No empty-state needed for the roster (the existing `emptyState` only shows if the snapshot is genuinely empty, e.g. before the first frame).
- When the link drops, all cards render `stale` (offline styling) until the snapshot returns — same as the desktop app with `_localSelf` absent.

### Layout spec (approved)

Page chrome — a slim sticky top bar:
- Left: `thekartoff` wordmark (`the` in `--tx`, `kartoff` in `--accent`).
- Right: live indicator — pulsing `--ok` dot + `N online · M racing` (derived from the presence map).

The wall:
- `display:flex; flex-wrap:wrap; justify-content:center; gap:8px;` capped at `max-width:1200px`, centered.
- Cards: `flex:0 1 189px; min-width:170px;` — natural width 189px, shrink to 170px to hold the row, then wrap; wrapped/leftover rows stay centered.
- Phone (`max-width:430px`): `flex-basis:100%` → one full-width column.
- Card internals are the verbatim `PlayerCard` markup/CSS (3px spine, 56px portrait strip, data column with name / selection-or-career-stats / resets / PB+delta / timer / progress bar). Height tracks the card's natural content (~172px in the row).

No server, REST, or auth code is involved in v1.

## Serving + auto-update on the Pi

Mirrors the existing pull-deploy machinery (`docs/pi-deploy.md`).

- **`web/serve.mjs`** — a zero-dependency Node static server (`node:http` + `node:fs`) serving `web/dist/` on port **8788**, with SPA fallback to `index.html` and basic content-type mapping. No framework, no deps to install for the runtime.
- **`deploy/systemd/mkw-web.service`** — `Type=simple`, `User=pi`, `WorkingDirectory=/home/pi/mkw/web`, `ExecStart=/usr/bin/node serve.mjs`, `Restart=always`. Mirrors `mkw-server.service`.
- **`deploy/update.sh`** — after `git checkout` of the new tag, additionally:
  - `npm --prefix "$REPO/web" install --no-audit --no-fund`
  - `npm --prefix "$REPO/web" run build`  (→ `web/dist/`)
  - `sudo systemctl restart mkw-server mkw-bot mkw-web`
  So the existing `git tag` push deploys the site too, no extra steps.
- **`deploy/install.sh`** — install `mkw-web.service` and `systemctl enable --now mkw-web.service` alongside the others.
- **Cloudflare tunnel** — add ingress rules (above the catch-all) for `thekartoff.com` and `www.thekartoff.com` → `http://localhost:8788`; `api.thekartoff.com` stays → `8787`. Add the DNS routes for both apex + www. Both hostnames serve the site directly (no redirect) — simplest, and the SPA is origin-agnostic.

Build-on-Pi (not CI artifacts) is chosen for consistency with the existing flow; Vite/esbuild have aarch64 builds and the app is small.

## Setup guide

New **`docs/website-deploy.md`**, modeled on `pi-deploy.md`:
1. Prereqs (assumes the Pi server + tunnel from `pi-deploy.md` are already up).
2. First build + `install.sh` (now also installs `mkw-web`).
3. Cloudflare tunnel routes for `thekartoff.com` + `www`.
4. Verify: `curl -s http://localhost:8788` returns the SPA shell; `https://thekartoff.com` loads the wall; cards go live when someone opens the app.
5. Note: steady-state it auto-updates via the existing tag flow (`git tag` → Pi self-updates → rebuilds + restarts `mkw-web`).

## Testing

- **`web/src/presenceClient.js`** — unit test the message-handling + reconnect logic (snapshot/update applied to stores, backoff on close, no outbound). Injectable `WebSocket`/timers as the existing `presence.test.js` does.
- **Build smoke** — `npm --prefix web run build` succeeds and emits `web/dist/index.html` + assets.
- Card components/view-model are already covered by the existing `src` vitest suite (reused, not duplicated).

## Out of scope (future increments)

- Leaderboards / WR / course / player pages (would use the token'd REST reads → needs a browser auth story + CORS).
- Any write path or accounts.
- SSR / SEO. v1 is a client-rendered SPA.

## File change summary

**New**
- `web/package.json`, `web/vite.config.js`, `web/index.html`, `web/serve.mjs`
- `web/src/main.js`, `web/src/App.svelte`, `web/src/presenceClient.js`, `web/src/presenceClient.test.js`
- `deploy/systemd/mkw-web.service`
- `docs/website-deploy.md`

**Edited**
- `deploy/update.sh` (build web + restart `mkw-web`)
- `deploy/install.sh` (install + enable `mkw-web`)
- `~/.cloudflared/config.yml` on the Pi (ingress; done during setup, not in-repo)

**Unchanged**
- `pi/` server (no server work in v1)
- `src/` desktop app (imported from, not modified)

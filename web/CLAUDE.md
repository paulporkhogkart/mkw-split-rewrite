# web/ — thekartoff.com (public website)

Vite + Svelte SPA, **served by the Pi** (not part of the desktop app). See the root `CLAUDE.md`
"Repo Surfaces" for the big picture. Talks to the Pi server over the token-free `PUBLIC_READS`
endpoints + receive-only WebSockets.

## Running / commands (from `web/`)

```bash
npm run dev          # vite — bound to 127.0.0.1:1430 (strictPort). Open http://127.0.0.1:1430
                     #   NOT localhost (→ IPv6 ::1 stalls, looks like a regression)
npm run build        # vite build → web/dist/   (__SITE_VERSION__ baked from ROOT package.json)
npm run serve        # node serve.mjs — dependency-free static server for web/dist, PORT 8788
npm test             # vitest run (colocated *.test.js)
npm run check        # svelte-check
```

Prod: the `mkw-web` systemd unit runs `node serve.mjs` on the Pi (PORT 8788); `deploy/update.sh`
runs `npm --prefix web run build` on each tagged deploy.

## Layout (`web/src/`)

- `main.js` → mounts `App.svelte` + `startPresence(API_BASE)`.
- `App.svelte` — shell + navbar, **History-API routing** (no hash) via `lib/view.js`:
  `/` → Live (`CardWall`), `/turf` → `WorldMap`, `/players` → `PlayersIndex` (roster grid) /
  `/players/:slug` → `PlayerProfile`, plus URL-only `/heat` (`HeatGraph`) and `/version`
  (`VersionPage`). `view.js` also exports `playerSlugFromPath` (the `:slug` for the profile).
- Components: `CardWall`, `WorldMap`, `MapFireLayer`, `TurfLeaderboard`, `HeatGraph`,
  `TimelineScrubber`, `ActivityLog`, `CoursePopup`, `VersionPage`, `PlayersIndex`,
  `PlayerProfile`, `StrategyPanel` (GOLF/TURF/TIME toggle on a player profile).
- `lib/` — `api.js` (API base + URL builders), `turf.js` / `territory*.js`, `timeline.js`,
  `heat.js`, `map.js`, `fireModel.js`/`onFire.js`, `strategy.js` (GOLF/TURF/TIME sorts, reuses
  `fireModel.fireBarPct`), `playerSlug.js` (mirrors the Pi `slugify`), `chips.js`,
  `Wordmark*.svelte`. WS clients: `presenceClient.js`, `activityClient.js`.
- **Shares desktop code:** imports from `../../src/` (root `src/lib/` — `stores.js`, `presence.js`,
  `theme.css`, `PlayerCard`, `playerFigures`, `playerKey`). `vite.config.js` sets
  `server.fs.allow:['..']` so dev can read one dir up. Editing those root files affects BOTH the
  desktop app and the website.

## Talking to the Pi (`lib/api.js`)

- `API_BASE` = `VITE_API_BASE`, else `http://127.0.0.1:8787` in dev, else `https://api.thekartoff.com`.
- Only hits token-free `PUBLIC_READS` (`/v1/territory`, `/v1/territory/timeline`, `/v1/version`,
  `/v1/activity`, `/v1/roster`, and `/v1/players/:slug` — the public player summary, opened by a
  single-segment regex exception in `pi/src/api/app.ts`, NOT the token-gated `/v1/players/:id/pbs`)
  + receive-only WS (`/v1/presence`, `/v1/activity/stream`).

## Visual work — read `../CLAUDE.md` conventions + note

The user has hard visual standards (smooth AA via hi-res→downscale, terrain-derived fills, change
one thing / never regress). **Verify map/canvas visuals in a REAL browser (headless Edge + CDP),
never OpenCV** — OpenCV premultiplies differently and lies about compositing. Pi-served media
(`web/public/**`, e.g. player GIFs) must be **ordinary git binaries, never Git LFS** (the Pi build
has no `git lfs pull`, so LFS files ship as pointer stubs).

**Chip sprite-sheet pack:** built by `tools/asset_matte/build_site_pack.py`, delivered as GitHub
Release assets pinned by `web/chips.lock`, served at `/chips/anim/` — see
`docs/superpowers/specs/2026-07-18-chip-site-pack-design.md`.

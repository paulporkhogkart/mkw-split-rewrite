# `/version` diagnostic page — design

**Date:** 2026-06-24
**Status:** approved (pending spec review)

## Goal

An unlisted page on thekartoff.com at `#/version` (reachable by URL only, exactly like
`#/heat`) that reports, for the four deployable pieces of the project:

1. The **latest available** version of the bot, server, site, and pbenguin app.
2. The **deployed** version of the bot, server, and site.
3. The **last-ran** pbenguin-app version installed by each player.

The page is a drift detector: is prod running the newest code, and which players are behind?

## Background — how versioning works today

- **One shared version.** `scripts/set_version.py` is the single source of truth: it stamps
  the root `package.json` version into `pyproject.toml`, `src-tauri/Cargo.toml`,
  `src-tauri/tauri.conf.json`, then the release is cut as git tag `v{x.y.z}`. It never touches
  `pi/package.json` or `web/package.json` — server, bot, and site have no version field of
  their own and inherit the repo version.
- **Server, bot, site co-deploy from one tag.** The Pi clones the whole repo at a release tag
  (recorded in `/home/pi/mkw-data/.deployed-tag`); `mkw-server`, `mkw-bot`, and the `web/` site
  (built + served on the Pi) all come from that same checkout. In steady state they are always
  equal; drift appears only mid-deploy or if a service fails to restart. The updater "only ever
  moves to the highest tag."
- **The app releases on a subset of tags.** Per the user: desktop releases are no longer cut on
  every tag (the release workflow skips rebuilding the app when only server/bot/site changed).
  So the app's "latest available" is the **last actual GitHub Release**, which may lag the latest
  tag.
- **Players update on their own schedule** — the real, ongoing drift.

Consequences for this design:

- "Latest available" for **server / bot / site** = the **highest git tag**.
- "Latest available" for the **pbenguin app** = the **last release** (the updater manifest
  `latest.json` `.version` — exactly what the in-app auto-updater compares against).
- The updater endpoint in `src-tauri/tauri.conf.json` is
  `https://github.com/paulporkhogkart/mkw-split-rewrite/releases/latest/download/latest.json`,
  which yields both the manifest URL and the repo slug `paulporkhogkart/mkw-split-rewrite`.

## What the page shows

Two tables on a plain, utilitarian dark page.

**Components**

| Component | Latest available | Deployed | Status |
|---|---|---|---|
| pbenguin app | last release | (per-player — see Players) | summary "N/M on latest" |
| server | latest tag | running server version | ✓ / ⚠ behind / ? |
| bot | latest tag | bot's running version | ✓ / ⚠ behind / ? |
| site | latest tag | loaded web-bundle version | ✓ / ⚠ behind / ? |

**Players** — one row per active-season roster player:

| Player | Installed app (last-ran) | Last seen | Status |
|---|---|---|---|
| Paul | 2.1.0 | online | ✓ on latest release |
| Gub | 2.0.0 | 3h ago | ⚠ behind |
| Aliias | — | 12d ago | ? never reported |

## Data sources

| Component | "Latest available" source | "Deployed / installed" source |
|---|---|---|
| pbenguin app | `latest.json` `.version` (fetched server-side, cached) | per-player `players.app_version`, persisted from presence |
| server | GitHub tags API, highest semver (cached) | `repoVersion()` — root `package.json`, read in-process |
| bot | GitHub tags API, highest semver (cached) | self-reported `service_status` row written on bot boot |
| site | GitHub tags API, highest semver (cached) | `__SITE_VERSION__` baked into the web bundle at build |

## Architecture & data flow

### 1. Per-player app version (through presence)

The desktop app already reads its version via Tauri's `getVersion()` in `src/App.svelte`.

- `src/lib/stores.js` — add `export const appVersion = writable("")`.
- `src/App.svelte` — set `appVersion` from the `getVersion()` it already awaits.
- `src/lib/presence.js` — `frame()` includes `app_version: get(appVersion) || null`. (The
  website's receive-only presence client never sends frames, so it never reports a version —
  correct.)
- `pi/src/presence/hub.ts`:
  - Add `app_version?: string | null` to `PresenceFrame`.
  - In `update()`, persist to `players.app_version` **only when it changes**, guarded by an
    in-memory `Map<playerId, string>` of last-written values (the same minimal-write discipline
    as `writeLastSeen`). Helper: `writeAppVersion(playerId, version)`.
  - The presence **broadcast** type (`PresenceEntry`) is left unchanged — `/v1/version` reads
    `players.app_version` from the DB, so offline players still show their last-ran version and
    it survives a server restart.

### 2. Bot deployed version (`service_status`)

The bot is a separate process that treats the server DB as read-only. On boot it makes one
small, well-scoped exception:

- `pi/src/bot/index.ts` — defensively `CREATE TABLE IF NOT EXISTS service_status (...)` (the bot
  doesn't run `applySchema`), then upsert its row:
  `INSERT INTO service_status(service,version,booted_at) VALUES('bot',?,?) ON CONFLICT(service)
  DO UPDATE SET version=excluded.version, booted_at=excluded.booted_at`, with
  `version = repoVersion()`. `booted_at` lets the page spot a bot that failed to restart after a
  deploy (stale boot time + old version).

### 3. Server deployed version (in-process)

- `pi/src/version/repoVersion.ts` — tiny module: reads the root `package.json` version
  relative to its own location (`../../../package.json`). Imported by both the version route and
  the bot, so neither hard-codes a path.
- The version route reports `repoVersion()` for the server, plus a module-level `BOOTED_AT`
  (set when the version module loads ≈ server start). The server is authoritative about its own
  version because it is the process answering the request; no DB round-trip needed. `server.ts`
  needs no change.

### 4. Site deployed version (bundle define)

- `web/vite.config.js` — read the root `package.json` version at config-eval time and
  `define: { __SITE_VERSION__: JSON.stringify(version) }`. The web build runs on the Pi from the
  same clone, so this is the deployed tag's version.
- `web/src/lib/version.js` — `export const SITE_VERSION = typeof __SITE_VERSION__ !== "undefined"
  ? __SITE_VERSION__ : "dev"` (the `typeof` guard keeps vitest, where the define is absent,
  from throwing). The page renders the site's deployed version client-side — the page *is* the
  served site, so the loaded bundle's version is authoritative; the server cannot know it and
  does not report it.

### 5. Latest-available fetcher (`pi/src/version/latest.ts`)

- **Resolve at boot** from `src-tauri/tauri.conf.json` `plugins.updater.endpoints[0]`
  (`../../../src-tauri/tauri.conf.json` relative to the module):
  - manifest URL = that endpoint (the `latest.json` URL).
  - repo slug = the `{owner}/{repo}` parsed from the `github.com/...` path.
  - Env overrides: `MKW_UPDATER_MANIFEST` (full URL), `MKW_RELEASE_REPO` (`owner/repo`).
- **`latestApp()`** — GET the manifest URL, return `.version` (the last release).
- **`latestTag()`** — GET `https://api.github.com/repos/{slug}/tags?per_page=100` with headers
  `Accept: application/vnd.github+json` and `User-Agent: mkw-version` (GitHub requires a UA);
  `Authorization: Bearer ${GITHUB_TOKEN}` if that env is set. Map `.name`, strip a leading `v`,
  return the highest by `compareSemver` (GitHub's tag order is not guaranteed, so we sort
  ourselves). `pickLatestTag(names)` is a pure, tested helper. Assumes < 100 tags (current
  reality); add pagination only if that's ever exceeded.
- **Cache** both behind one in-memory cache with TTL `MKW_VERSION_CACHE_MS` (default 600000 =
  10 min). Each fetch uses a ~5s `AbortController` timeout so a hung GitHub never blocks the
  request. On failure, serve the last-good cached value and record a string in `errors`
  (e.g. `"tags: HTTP 503"`). The fetcher never throws.

## The `/v1/version` endpoint

New `pi/src/api/version.ts` exporting `versionRoutes(db)`, self-contained (owns the cached
latest-fetcher and reads `repoVersion()` itself). Mounted in `createApp` via
`app.route('/', versionRoutes(db))`, and `'/v1/version'` is added to the `PUBLIC_READS` array in
`pi/src/api/app.ts` (which grants permissive GET CORS and skips the token gate — same treatment
as `/v1/territory/timeline`, which `#/heat` uses).

`GET /v1/version` → always HTTP 200:

```json
{
  "latest": { "tag": "2.1.5", "app": "2.1.0", "fetched_at": 1750000000000, "errors": [] },
  "deployed": {
    "server": { "version": "2.1.5", "booted_at": 1750000000000 },
    "bot":    { "version": "2.1.5", "booted_at": 1749990000000 }
  },
  "players": [
    { "player_id": 1, "name": "Paul", "color": "#a78bfa", "app_version": "2.1.0", "last_seen_at": 1750000000000 }
  ]
}
```

- `latest.tag` / `latest.app`: strings without a leading `v`, or `null` if that fetch failed.
- `deployed.bot`: `null` if no `service_status` bot row exists (fresh DB / bot never started).
- `players`: the active-season roster (same membership as `/v1/roster`), each with
  `app_version` (nullable) and `last_seen_at` (nullable). Site version is intentionally absent
  (client renders it from the bundle).

## The `#/version` page

- `web/src/lib/view.js` — `if (h === "version") return "version";` (no navbar tab — unlisted).
- `web/src/App.svelte` — import `VersionPage`, add `{:else if view === "version"}<VersionPage />`.
- `web/src/lib/api.js` — `export const versionUrl = () => `${API_BASE}/v1/version`;`.
- `web/src/VersionPage.svelte` — on mount `fetch(versionUrl())`, render the two tables;
  read `SITE_VERSION` from `version.js` for the site's deployed cell. Pure presentation.
- `web/src/lib/version.js` — pure, tested helpers:
  - `compareSemver(a, b)` → -1/0/1, or `null` if unparseable (strip `v`, missing parts → 0).
  - `status(deployed, latest)` → `"current" | "behind" | "ahead" | "unknown"`
    (`unknown` if either side is null/unparseable; `ahead` = dev box on an untagged build).
  - `formatLastSeen(lastSeenAt, now)` → `"online"` if within 60s, else `"3m ago"`/`"5h ago"`/
    `"12d ago"`, `"never"` if null.
  - `componentRows(payload, siteVersion)` and `playerRows(payload, now)` — build the table
    rows + statuses so the `.svelte` is logic-free (same split as `heat.js`/`HeatGraph.svelte`).

## Status & semver rules

- server/bot/site: `status(deployed, latest.tag)`.
- each player: `status(player.app_version, latest.app)`.
- app component summary: count roster players whose `app_version === latest.app` over the count
  with any reported version → "N/M on latest".

## Caching, config & error handling

- Latest lookups cached ~10 min in server memory. Optional `?fresh=1` query param bypasses the
  cache for a manual recheck (cheap to include; the page itself does not send it).
- GitHub unreachable → `latest` fields null, components show `?`, all local data still renders.
- Bot row absent → bot deployed `unknown`.
- Player never reported → installed `—` / `unknown`.
- `deployed > latest` → shown as `dev`/`ahead`, not an error.
- The endpoint catches all errors and returns 200 with partial data; the page tolerates nulls.

## Security / exposure

Public-but-unlisted, identical to `/heat`: no navbar link, and `/v1/version` serves the same
class of already-public data the live cards site shows (player names, colours, presence). App
version strings are not sensitive. If private access is ever wanted, move `/v1/version` out of
`PUBLIC_READS` and token-gate it — out of scope here.

## Testing

- **pi** `pi/src/version/latest.test.ts`: `pickLatestTag(["v2.1.0","v2.10.0","v2.2.0"]) ===
  "2.10.0"`; `compareSemver` cases; repo-slug parse from a sample endpoint URL; cache returns the
  cached value within TTL and re-fetches after (inject fake `fetch` + clock).
- **pi** `pi/src/api/version.test.ts`: in-memory DB with a mix of players (some with
  `app_version`/`last_seen_at`, some without) + a `service_status` bot row; assert payload shape,
  roster membership, `deployed.bot`, `deployed.server`, and `latest` from an injected fetcher.
- **pi** presence: a frame with `app_version` persists `players.app_version`; an identical
  follow-up frame does not re-write (guard); a changed version re-writes.
- **web** `web/src/lib/version.test.js`: `compareSemver`, `status`, `formatLastSeen`,
  `componentRows`, `playerRows`.

## Files

**New**
- `pi/src/version/repoVersion.ts`
- `pi/src/version/latest.ts`
- `pi/src/api/version.ts`
- `pi/src/version/latest.test.ts`
- `pi/src/api/version.test.ts`
- `web/src/VersionPage.svelte`
- `web/src/lib/version.js`
- `web/src/lib/version.test.js`

**Modified**
- `server/schema.sql` — `players.app_version TEXT`; new `service_status` table.
- `pi/src/db/connect.ts` — additive `ALTER TABLE players ADD COLUMN app_version TEXT`;
  `CREATE TABLE IF NOT EXISTS service_status (service TEXT PRIMARY KEY, version TEXT,
  booted_at INTEGER)`.
- `pi/src/presence/hub.ts` — `PresenceFrame.app_version`; persist-on-change in `update()`.
- `pi/src/bot/index.ts` — defensive table create + `service_status` upsert on boot.
- `pi/src/api/app.ts` — add `/v1/version` to `PUBLIC_READS`; mount `versionRoutes(db)`.
- `web/vite.config.js` — `define __SITE_VERSION__` from root `package.json`.
- `web/src/lib/view.js` — route `#/version`.
- `web/src/App.svelte` — dispatch `VersionPage` (no navbar tab).
- `web/src/lib/api.js` — `versionUrl()`.
- `src/lib/stores.js` — `appVersion` store.
- `src/App.svelte` — set `appVersion` from `getVersion()`.
- `src/lib/presence.js` — include `app_version` in `frame()`.

`pi/src/server.ts` needs no change (the version route is self-contained; `BOOTED_AT` is a
module-level timestamp).

## Non-goals

- No per-component independent versioning (the repo keeps one shared version + tag).
- No live "online" truth from the presence hub — "online" is derived from `last_seen_at`
  recency (≤60s). Wiring the hub in for exact status is a later, easy follow-up.
- No new app release cadence or release-workflow changes.
- No live `app_version` on the monitor cards (it is not added to the presence broadcast); easy
  to add later if wanted.

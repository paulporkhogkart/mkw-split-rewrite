# pi/ — Pi server (canonical source of truth)

Node/TS server, runs on a Raspberry Pi. **No build step** — TypeScript is executed directly via
`tsx`. Hono for HTTP, `@hono/node-ws` for WebSockets, `node:sqlite` (`DatabaseSync`) for the DB,
discord.js for the bot. See the root `CLAUDE.md` "Repo Surfaces" for how this fits the whole system.

## Running / commands (from `pi/`)

```bash
npm run dev          # node --import tsx src/server.ts  → listens on http://127.0.0.1:8787 (PORT env)
npm run bot          # the Discord bot (separate process, same package, shared DB)
npm test             # vitest run  (colocated *.test.ts — dense; add tests beside new modules)
npm run typecheck    # tsc --noEmit — NON-GATING (not in CI); keep source AND tests tsc-clean
# ops CLIs (via tsx): mint-token, set-color, scrape-wr, scrape-wr-history, wr-flags,
#                     build-course-model, wipe-runs, recompute-pbs, migrate-trails, diff-trails
```

## Layout (`pi/src/`)

- `server.ts` — entry: open DB → `applySchema` → one-time recovery/rename migrations → wire
  EventHub/PresenceHub/ActivityHub/SessionTracker → start HTTP+WS → start WR scrapers on intervals.
- `api/` — Hono routes. `app.ts` (composition + token gate + WS attach), `runs.ts` (writes),
  `reads.ts`, `stats.ts` (`/v1/stats/*`), `activity.ts`, `presence.ts`, `version.ts`, `auth.ts`.
- `db/` — data access over `node:sqlite`. `connect.ts` (`applySchema`), `ingest.ts`, `pb.ts`,
  `reads.ts`, `seasons.ts`, `players.ts`, `slug.ts`, `season0Recovery.ts`, `ghostImport.ts`,
  `courseModels.ts`, `types.ts`, …
- `wr/` — mkwrs.com WR scraper (current + full history) and reconciliation.
- `bot/` — Discord bot (announcements + slash commands); has its own `README.md`.
- `stats/`, `activity/`, `presence/`, `progress/`, `turf/`, `version/`, `scripts/`.

## DB

- Schema DDL is **`server/schema.sql` at repo ROOT** (not in `pi/`); `db/connect.ts` reads it via a
  relative URL and `db.exec`s it, then applies idempotent additive `ALTER`s. See `docs/database-schema.md`.
- Runtime DB path = `MKW_DB` env (default `mkw.db`). On the Pi it lives OUTSIDE the clone
  (`~/mkw-data/mkw.db`) so deploys never clobber it. WAL + `foreign_keys=ON`. Migrate-on-boot is
  idempotent (safe to re-run).

## API gating (`api/app.ts`, `auth.ts`)

- **Every route requires a token by default.** Writes (`POST /v1/runs`, `/v1/runs/start`) take a
  **Bearer header only** (never a URL token). Reads/WS accept header OR `?token=`.
- **Intentionally OPEN (no token) — do NOT "fix":** `/health`, WS `/v1/events` + `/v1/presence` +
  `/v1/activity/stream` (the on-Pi bot / public site subscribe token-less over localhost), and the
  `PUBLIC_READS` GET allowlist (`/v1/leaderboard`, `/v1/world-records`, `/v1/roster`,
  `/v1/territory`, `/v1/territory/timeline`, `/v1/version`, `/v1/activity`, `/v1/wr-jobs`, `/v1/wr-trails`) which also gets
  permissive CORS for the cross-origin website. `PUBLIC_READS` matches the exact `c.req.path`.
- `/v1/presence` sockets are receive-only; `?token=` only attributes inbound frames to a player.

## Gotchas

- Slugs strip apostrophes (incl. curly U+2019); `POST /v1/runs` 400s on an unknown course slug.
- `wipe-runs` is provenance-blind `DELETE FROM runs` — follow with the importer + `recompute-pbs`,
  or it also nukes legacy/carryover PB seeds.
- discord.js v14: `'clientReady'` + `MessageFlags.Ephemeral`; one gateway per token.

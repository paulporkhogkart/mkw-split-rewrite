# pi/ — MKW server (sub-project B, Phase 1)

TypeScript/Node (Hono) HTTP API + WS event hub over the sub-project A SQLite store.
Spec: `docs/superpowers/specs/2026-06-04-server-api-and-sync-design.md`.

## Dev
    npm install
    npm test                 # vitest
    npm run dev              # serve on :8787 (MKW_DB=path PORT=n to override)
    npm run mint-token Paul  # issue a player token (printed once)
    npm run scrape-wr        # one-shot mkwrs WR scrape (MKW_DB=path)

Uses Node's built-in `node:sqlite` (run scripts pass `--no-warnings`). The DB is
created/seeded by A's importer (`python -m server.importer`); `MKW_DB` points the
server at it.

## WR scraper
Mirrors the mkwrs.com Mario Kart World WR table into `world_records` (`src/wr/`). Runs
in-process (started by `server.ts`) and re-polls at a random interval in
`[MKWRS_MIN_INTERVAL_SEC, MKWRS_MAX_INTERVAL_SEC]` (defaults 900–1800s; re-rolled each
cycle to avoid a fixed cadence; set `MKWRS_MAX_INTERVAL_SEC=0` to disable). `MKWRS_URL`
overrides the page. One-shot: `npm run scrape-wr`.

## Out of scope here
Client write path (engine `run_finalized` + `src-tauri/src/sync.rs`) — separate plan.
The web/overlays (C). Live in-progress telemetry.

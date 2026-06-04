# pi/ — MKW server (sub-project B, Phase 1)

TypeScript/Node (Hono) HTTP API + WS event hub over the sub-project A SQLite store.
Spec: `docs/superpowers/specs/2026-06-04-server-api-and-sync-design.md`.

## Dev
    npm install
    npm test                 # vitest
    npm run dev              # serve on :8787 (MKW_DB=path PORT=n to override)
    npm run mint-token Paul  # issue a player token (printed once)

Uses Node's built-in `node:sqlite` (run scripts pass `--no-warnings`). The DB is
created/seeded by A's importer (`python -m server.importer`); `MKW_DB` points the
server at it.

## Out of scope here
Client write path (engine `run_finalized` + `src-tauri/src/sync.rs`) — separate plan.
The web/overlays (C). Live in-progress telemetry. WR scraper.

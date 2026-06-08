# Screen-Time Telemetry (Increment #4) — Design

**Date:** 2026-06-08
**Status:** Approved for planning (standing approval; capture approach confirmed by the user).
**Builds on:** Increments #1–#3 (the `/v1/stats` engine).

## 1. Goal

Broadcast stats like "time spent in menus" — total wall-clock time each player spends in each game screen (TITLE, MAIN_MENU, RACE_MENU, RACING, results, …), sliceable by player / screen / period.

**Confirmed capture approach:** the **engine does not change** — it already emits `screen_change` events on stdout, which the Rust/Tauri app already sees in `sync.rs::on_line`. The app records intervals from those events and forwards them to the server. No new engine responsibility (the detector stays pure). **No AFK/idle cap** (per earlier decision — raw wall-clock is fine).

## 2. Data flow

```
engine screen_change (stdout)  →  app sync.rs (interval tracking + outbox)
   →  drain loop POST /v1/screen-intervals (authed)  →  server screen_intervals table
   →  /v1/stats screen_time metric
```

Timestamps are **epoch milliseconds** (the app stamps `screen_change` on receipt via the system clock) — this side-steps the mixed-string-timestamp issue entirely for this table; durations are integer subtraction.

## 3. Server

### 3.1 Schema (`server/schema.sql`, picked up by `applySchema`'s re-run since it's `IF NOT EXISTS`)
```sql
CREATE TABLE IF NOT EXISTS screen_intervals (
    id          INTEGER PRIMARY KEY,
    season_id   INTEGER NOT NULL REFERENCES seasons(id),
    player_id   INTEGER NOT NULL REFERENCES players(id),
    screen      TEXT NOT NULL,
    started_ms  INTEGER NOT NULL,
    ended_ms    INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(player_id, started_ms)
);
CREATE INDEX IF NOT EXISTS idx_screen_intervals ON screen_intervals(season_id, player_id, screen, started_ms);
```
`UNIQUE(player_id, started_ms)` + `INSERT OR IGNORE` makes re-sent batches idempotent (a player can't start two screens at the same instant).

### 3.2 Ingest (`pi/src/api/screen.ts`, mounted in `app.ts`)
`POST /v1/screen-intervals` (authed via `requireToken`). Body `{ intervals: [{ screen, started_ms, ended_ms }] }`. For each: `INSERT OR IGNORE` with `player_id` from the token + active `season_id`. Ignores zero/negative-length intervals. Returns `{ inserted: n }`.

### 3.3 Stats (`pi/src/stats/screen.ts`)
- `insertScreenIntervals(db, seasonId, playerId, intervals)` — the write helper (used by the route + tests).
- `resolveScreen(db, { metric, filters, groupBy, period, seasonId })` — `screen_time` = `SUM(ended_ms - started_ms)` over intervals filtered by `player`, optional `screen`, and the period window (on `started_ms`, epoch-ms bounds from `toEpochSeconds(period.*Utc) * 1000`). `group_by` `screen` (per-screen) or `player`. Returns the standard `StatResult`.

New metric kind `screen`: `{ id: 'screen_time', kind: 'screen' }`. A new dimension **`screen`** is added to the `Dimension` union (+ `DIMS`); `allowsDimension('screen_time', …)` → `player | screen`. The race `groupExpr`/`filterClause` switches get a `default: throw` (unreachable — `guard` blocks `screen`/non-race dims on race metrics). Route dispatch: `kind === 'screen'` → `resolveScreen`.

## 4. App (Rust, `src-tauri/src/sync.rs`)

- Track `(current_screen, entered_ms)` in a `static Mutex`. On each `screen_change` line: if a screen was open, record the closed interval `(prev_screen, entered_ms, now_ms)` into a new local `screen_outbox` table; set `current = to`, `entered_ms = now`.
- `parse_screen_change(line) -> Option<(from, to)>` and an interval-tracking function are **pure + unit-tested** (mirroring the existing `is_racing_entry` tests).
- Drain loop (extend the existing thread in `init`): batch-POST ready `screen_outbox` rows to `/v1/screen-intervals`; delete on 2xx. Same `CONFIG`/auth as the run uploader.
- Hook `on_line`: call the interval tracker for `screen_change` lines (alongside the existing course/racing handling).

## 5. Testing

- **Server/stats (vitest):** `insertScreenIntervals` idempotency (dup `started_ms` ignored); `resolveScreen` total over a window; `group_by screen`; `screen` filter; zero-length ignored. Route: `POST /v1/screen-intervals` authed insert + `GET /v1/stats/value?metric=screen_time&screen=MAIN_MENU` and `…/breakdown?group_by=screen`; `/v1/stats/metrics` lists `screen_time` with `dimensions:['player','screen']`.
- **App (cargo):** `parse_screen_change` extracts from/to; the interval tracker emits the previous interval with correct bounds on transition and nothing on the first screen.

## 6. Caveats

- Wall-clock (no AFK cap) — "time in menus" counts a player who walks away. Accepted.
- Intervals accrue only while the app runs; an app crash loses the open interval (acceptable — coarse stat).
- Screen names are whatever the engine emits (`to`/`from` strings); the stat groups by them verbatim.

## 7. Roadmap position

Increment #4 of 5. Last: #5 race×body analytics (correlation), reusing the #1 alignment primitive.

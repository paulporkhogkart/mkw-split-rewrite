# Canonical Data Model + Migration — Design

**Date:** 2026-06-04
**Status:** Approved (design); pending implementation plan
**Component:** New server-side canonical store (SQLite) + a repeatable importer for the legacy `kart-off` / `hogkart.db` data. Schema-first. Server transport, client auth/upload, the WR scraper, and broadcast/OBS consumption are separate sub-projects (B/C) and out of scope here.

## 1. Goal

Define the single canonical data model that the home server (a Raspberry Pi; run manually on the dev machine for now) will hold for the Mario Kart World time-trial competition, plus a repeatable migration that imports the legacy app's historical data into it without loss.

The model must capture the rich, auto-detected telemetry the `pbenguin` client produces — every attempt, variable-length lap splits, coins-per-lap, mushrooms-per-lap, character/kart/costume, full minimap replay trail — and absorb the legacy total-time-only PB/WR history as the competition's "before".

This is sub-project **A** of the larger client→server shift, and the keystone: B (server + identity + sync) and C (broadcast/OBS) build on this schema.

## 2. Non-goals (YAGNI / deferred)

- **Auth / identity mechanism** — how a client proves it is player X (token, etc.). The model carries stable player identity; the proof is sub-project B.
- **Client→server upload protocol** and client capture wiring (bucketing coins/mushrooms per lap, detecting cc, emitting resets as rows) — sub-project B.
- **WR scraper** — server-side ingestion of external world records. The WR table is append-ready; the monitor is built later (the server's job, never the app/engine).
- **Live transport** — WebSocket/SSE fan-out of live runs to viewers/clients — sub-project B/C.
- **Website / OBS overlay consumption** — sub-project C.
- **Reconciling the client's existing local SQLite** (`mkw_tracker.db`: `replays` / `replay_points` / `replay_splits`) with this schema — sub-project B.
- **Season 2 logic** — a fresh roster / its carry-over policy. Only the S0→S1 carry-over is built now.
- **Postgres** — not now; schema kept portable so it stays a cheap future option.

## 3. The legacy data (the integration surface)

Measured from the copied `legacy/mkwpb2/kart-off/data/hogkart.db` (peewee / SQLite). Small and clean:

| Legacy table | Rows | Notes |
|---|---|---|
| `players` | 5 | Paul, Adymer, Luke, Alex, Aliias |
| `tracks` | 30 | MKW course names |
| `personal_bests` | 205 | append-only history; current PB = latest `achieved_at_utc`. **Only** `record` / `record_ms` / `achieved_at_utc` populated — character, vehicle, and every lap/coins/shroom column are empty |
| `world_records` | 473 | external WR progression; `holder`, `character`, `vehicle`, `video_url` all populated; 62 distinct holders incl. non-ASCII names; lap columns empty |

- **PB distribution:** 150 current PBs (5 players × 30 courses) + 55 historical-improvement rows = 205.
- **Course-name reconciliation:** 29/30 map by slug-normalization; the only true mismatch is legacy `Wario Shipyard` → canonical `Wario's Galleon`. One normalization rule + one alias resolves all 30.
- **Encoding:** holder names contain non-ASCII → the store + importer must be UTF-8 (SQLite default; ensure the importer reads/writes UTF-8).

## 4. Locked decisions

(From the brainstorming dialogue.)

1. **Canonical store:** server-side **SQLite** (WAL). A single server process owns the file; the website and other consumers read via that server's API — never a second writer, never a network-shared file. Schema written portably (a later move to Postgres is a standard one-time ETL).
2. **Grain — full firehose:** every attempt is a `runs` row carrying a status (`reset` / `dnf` / `finished`). Finished runs carry full telemetry. The replay trail is stored for **every** attempt.
3. **Record key:** `(season, player, course, cc)`. Leaderboards are per `(season, course, cc)` plus an overall aggregate.
4. **Splits:** variable-length (normalized `run_laps`), with coins and mushrooms per lap.
5. **Identity:** global `players` (stable id + display name), a server-seeded fixed roster. External WR holders are plain name strings, **not** accounts.
6. **Seasons are the competition frame.** **Season 0** = the legacy history. **Season 1** = active, no end date. Each player's final S0 PB seeds their S1 starting time (carry-over). "One continuous competition" = simply never starting S2.
7. **World records are global** (no season), append-only; migrate all 473.

## 5. Schema

SQLite, WAL. Times are stored as both integer milliseconds (for comparison/sorting) and a formatted `m:ss.mmm` string (for display), matching the legacy and client conventions. Timestamps are ISO-8601 UTC text.

```sql
seasons(
  id             INTEGER PRIMARY KEY,
  name           TEXT NOT NULL,            -- 'Season 0', 'Season 1'
  started_at     TEXT,
  ended_at       TEXT,                     -- NULL = open (S1)
  is_active      INTEGER NOT NULL DEFAULT 0
);

players(
  id             INTEGER PRIMARY KEY,
  display_name   TEXT NOT NULL UNIQUE,
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
  -- auth columns (token/secret, claimed_at, ...) added in sub-project B
);

season_rosters(
  season_id      INTEGER NOT NULL REFERENCES seasons(id),
  player_id      INTEGER NOT NULL REFERENCES players(id),
  PRIMARY KEY (season_id, player_id)
);

courses(
  id             INTEGER PRIMARY KEY,
  slug           TEXT NOT NULL UNIQUE,     -- canonical, matches images/courses/<lang>/<slug>.png
  display_name   TEXT NOT NULL,            -- e.g. "Wario's Galleon"
  default_laps   INTEGER                   -- optional, for split validation; nullable
);

runs(
  id             INTEGER PRIMARY KEY,
  season_id      INTEGER NOT NULL REFERENCES seasons(id),
  player_id      INTEGER NOT NULL REFERENCES players(id),
  course_id      INTEGER NOT NULL REFERENCES courses(id),
  cc             INTEGER NOT NULL,                          -- engine class, e.g. 150
  status         TEXT NOT NULL CHECK (status IN ('reset','dnf','finished')),
  provenance     TEXT NOT NULL CHECK (provenance IN ('live','legacy_import','carryover')),
  started_at     TEXT,
  ended_at       TEXT,
  total_time_ms  INTEGER,                                   -- set for finished / carryover
  total_time_str TEXT,
  character      TEXT,
  kart           TEXT,
  costume        TEXT,
  is_pb          INTEGER NOT NULL DEFAULT 0,                -- current best for (season,player,course,cc)
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

run_laps(
  run_id         INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  lap_index      INTEGER NOT NULL,         -- 1-based
  lap_time_ms    INTEGER NOT NULL,
  lap_time_str   TEXT,
  coins          INTEGER,                  -- nullable (legacy / pre-capture runs)
  shrooms        INTEGER,                  -- nullable
  PRIMARY KEY (run_id, lap_index)
);

run_points(
  run_id         INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  t_ms           INTEGER NOT NULL,
  cx             REAL NOT NULL,
  cy             REAL NOT NULL,
  score          REAL NOT NULL DEFAULT 1.0
);

world_records(
  id             INTEGER PRIMARY KEY,
  course_id      INTEGER NOT NULL REFERENCES courses(id),
  cc             INTEGER NOT NULL,
  holder_name    TEXT,                     -- free-text external runner
  record_ms      INTEGER NOT NULL,
  record_str     TEXT NOT NULL,
  achieved_at    TEXT,
  video_url      TEXT,
  character      TEXT,
  vehicle        TEXT,
  provenance     TEXT NOT NULL DEFAULT 'legacy_import',
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
  -- GLOBAL: no season_id, by design
);
```

Indexes:
- `runs(season_id, course_id, cc, is_pb)` — leaderboard lookups
- `runs(season_id, player_id, course_id, cc)` — a player's timeline / PB on a course
- `run_laps(run_id)`, `run_points(run_id)` — child fetch
- `world_records(course_id, cc, achieved_at)` — current / historical WR per course

Notes:
- **`cc` is a column, not a table** (no cc metadata is needed).
- The legacy WR per-lap columns are dropped (always empty). If WR splits are ever scraped, add a `wr_laps` table at that point.

## 6. Derived data (not stored)

Leaderboards, "current PB", and reigns are computed by query from the per-season finished/carry-over timeline — the same approach the legacy app used, cleaner here. `is_pb` is the single denormalized flag.

- **Current PB** for `(season, player, course, cc)` = the row with min `total_time_ms` among that player's `finished` + `carryover` runs in scope. Maintained as `is_pb=1` on insert and recomputed by the importer.
- **Course leaderboard** `(season, course, cc)` = each rostered player's `is_pb=1` run, ordered by `total_time_ms`.
- **Overall leaderboard** = aggregate of per-course PBs per player, ordered by total time, with summed position-points as the tiebreaker (per the legacy total-leaderboard logic).
- **Reigns** = walk the per-scope PB timeline (ordered by `ended_at` / `created_at`) to find the current leader's reign-start — exactly as legacy `calculate_reign_info` did. Can be materialized later if the website needs the speed.

## 7. Seasons & carry-over

- `seasons`: **Season 0** (historical; `started_at` = earliest legacy `achieved_at`, `ended_at` = cutover timestamp, `is_active=0`) and **Season 1** (`started_at` = cutover, `ended_at` NULL, `is_active=1`).
- `season_rosters`: both seasons seeded with the 5 players (S1 roster extensible later).
- **Carry-over:** for each rostered S1 player × course, create one `runs` row with `provenance='carryover'`, `status='finished'`, `is_pb=1`, `cc=150`, and `total_time*` = that player's final S0 PB. S1's leaderboard is therefore populated from day one, and a real S1 attempt simply beats the seed. Carry-over rows are timestamped at S1's `started_at` (cutover) and have no laps/points (inherited baseline).
- Carry-over is a per-boundary **policy**; S2 (new roster) carry-over is decided when S2 is real (not built now).

## 8. Migration — repeatable importer

A standalone importer (e.g. `scripts/import_legacy.py`, or a module under the new `server/`) reads a **copy** of the legacy `hogkart.db` and is **safe to run any number of times**: practiced now on today's snapshot, then run once more authoritatively on the final dump at cutover. (The legacy server keeps scraping WRs until then, so the final run captures everything with no misses.)

Steps (single transaction):
1. **Idempotency wipe:** delete `runs` where `provenance IN ('legacy_import','carryover')`; delete `world_records` where `provenance='legacy_import'`. Live data (`provenance='live'`) is untouched. (Assumes the authoritative cutover import lands before live S1 play begins; practice imports wipe freely — see §10.)
2. Ensure `seasons` (S0, S1) and `season_rosters`.
3. Seed `courses` (the canonical 30: slug + display_name).
4. Map legacy `players` → `players` by case-insensitive name (create if missing); add to rosters.
5. Map legacy `tracks` → `courses` by **`slugify`** (lowercase, **strip apostrophes**, then collapse runs of non-alphanumeric → `_`, trim) **+ the explicit alias `Wario Shipyard → warios_galleon`**. **Fail loudly** if any legacy track does not map. (Stripping apostrophes is what makes `Bowser's Castle → bowsers_castle` and `Toad's Factory → toads_factory` line up with the image-asset slugs — a plain non-alphanumeric rule would wrongly yield `bowser_s_castle`.)
6. Import `personal_bests` → `runs` (S0, `legacy_import`, `finished`, cc=150, `total_time*`, original `achieved_at` → `ended_at`/`created_at`; character/kart/costume/laps/points left null).
7. Import `world_records` → `world_records` (global, `legacy_import`, cc=150; preserve holder/character/vehicle/video_url/achieved_at; UTF-8).
8. Recompute S0 `is_pb` (current best per player×course×cc).
9. Build the S1 carry-over seeds from final S0 PBs (§7).

`slugify` produces the existing `images/courses/<lang>/<slug>.png` stems for all 30 courses. It strips apostrophes so the legacy display names (which keep `'` / `.` / `?`) map onto the same slugs the client's already-apostrophe-free course names do.

## 9. Validation / acceptance

The importer prints a report and asserts, against today's snapshot:

- 5 players (Paul, Adymer, Luke, Alex, Aliias)
- 30 courses, **0 unmapped** legacy tracks (incl. Wario Shipyard → Wario's Galleon)
- 205 S0 `runs` (`legacy_import`, finished)
- 473 `world_records`
- 150 S1 carry-over seeds (one per player×course, `is_pb=1`)
- UTF-8 holder names intact (incl. non-ASCII)
- **Idempotent:** a second consecutive run yields identical counts (no dupes)
- Spot-check: a chosen player's current S0 PB on a chosen course equals the legacy latest `record` for that pair

## 10. Risks / notes

- **Cutover ordering:** the idempotency wipe (§8.1) assumes no live S1 runs exist at authoritative-import time. If live play could ever precede cutover, switch the wipe to a key-based upsert (e.g. a `legacy_src_id` column on imported rows). Flagged for the implementation plan.
- **cc capture:** new runs need a cc value; how the client determines it is a sub-project B concern. Legacy data is all 150.
- **Course canonical strings:** `slug` / `display_name` must match the app's canonical set (course strings also appear in `database/migrations.py` and `detection/selection.py`); the importer's course seed is the single source of truth, and those call sites should align to it in B.

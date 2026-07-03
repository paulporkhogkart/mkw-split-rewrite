# Database Schema

There are **two** SQLite databases in this project (see the root `CLAUDE.md` "Repo Surfaces"):

1. **`mkw_tracker.db`** — the desktop app. Now holds **only config + minimap detection tuning**;
   all race/replay data moved to the Pi server.
2. **The Pi server DB** (`mkw.db` at runtime, `~/mkw-data/mkw.db` on the Pi) — the canonical race
   data. DDL is `server/schema.sql`, loaded verbatim at boot by `pi/src/db/connect.ts`.

---

## 1. Desktop `mkw_tracker.db` (SQLite, WAL)

Schema defined in `mkw_tracker/database/migrations.py` (`_SCHEMA_V1` + migrations). Current
`schema_version` = **5**. A fresh DB creates only **5 tables**.

### `schema_version`
`version INTEGER NOT NULL` (single row). Drives `apply_migrations()`.

### `config`
`key TEXT PK`, `value TEXT NOT NULL` (JSON-encoded), `updated_at TEXT`. Every tunable constant;
seeded from `config/defaults.py`. Tauri writes; Python hot-reloads via `settings.reload(keys)`.
(Screen-detection "tell" overrides are also stored here as `tell_tree_<SCREEN>` JSON blobs — there
is no dedicated `tells` table.)

### `minimap_seeds`
`course TEXT PK`, `cx INTEGER`, `cy INTEGER`, `radius INTEGER DEFAULT 0`, `conf REAL`, `updated_at`.
Per-course minimap start positions (full 1080p px).

### `minimap_rois`
`course TEXT PK`, `x,y,w,h INTEGER`, `updated_at`. Per-course custom minimap scan ROI.

### `minimap_thresholds`
PK `(course, character, costume)`, `threshold REAL`, `updated_at`. Auto-calibrated per-combo
identity thresholds. **On the badge-NCC score scale** since v5.

### Migration chain
- **v2** — seeds `minimap_seeds` + `minimap_rois` for 30 courses.
- **v3** — migrates legacy tell overrides to the boolean-tree format (`tell_repo`).
- **v4** — **no-op** (only bumps version; `replay_splits` was removed in Phase 2).
- **v5** — `DELETE FROM minimap_thresholds` (badge-NCC score rescale).

### ⚠️ Removed tables — `replays`, `replay_points`, `replay_splits`
These were dropped in Phase 2 (race data moved to the Pi). **Nothing in the codebase creates,
reads, or writes them.** Fresh DBs never have them. DBs created *before* Phase 2 still carry the
tables as stale dead weight (~10 MB) because no destructive DROP migration exists — harmless, but
don't build on them.

---

## 2. Pi server DB — `server/schema.sql`

`connect.ts` runs `schema.sql` (all `CREATE TABLE IF NOT EXISTS`) then applies idempotent additive
`ALTER`s. WAL + `foreign_keys=ON`. Key tables (see `server/schema.sql` for full DDL):

- **`seasons`** — `id`, `name UNIQUE`, `started_at`, `ended_at`, `is_active`.
- **`players`** — `id`, `display_name UNIQUE`, `auth_token_hash UNIQUE`, `color`,
  `last_seen_at` (epoch ms), `app_version`, `created_at`.
- **`season_rosters`** — PK `(season_id, player_id)`.
- **`courses`** — `id`, `slug UNIQUE`, `display_name`.
- **`runs`** — the core attempt row: `id`, `attempt_id UNIQUE`, `season_id`, `player_id`,
  `course_id`, `cc`, `status ∈ {reset,dnf,finished}`, `provenance ∈ {live,legacy_import,carryover}`,
  `started_at`, `ended_at`, `total_time_ms`, `total_time_str`, `character`, `kart`, `costume`,
  `coins_gained`, `coins_lost`, `mushrooms_used`, `is_pb`, `source`, `created_at`, **`was_pb`**.
  Note `was_pb` is added by a `connect.ts` ALTER, **not** declared in `schema.sql` (a doc generated
  from schema.sql alone would miss it). `total_laps` and `invalid_reason` are transported in the
  upload payload but **not persisted**.
- **`run_laps`** — PK `(run_id, lap_index)`, `run_id` FK ON DELETE CASCADE, `lap_time_ms`,
  `lap_time_str`, `coins`, `shrooms`.
- **`run_points`** (trail) — `run_id` FK CASCADE, `t_ms`, `cx REAL`, `cy REAL`,
  `score REAL DEFAULT 1.0`, **`lap INTEGER`** (nullable, 1-based HUD lap). No PK; indexed by
  `idx_run_points_run`. **No decimation** — one point per detection frame (~25.6 Hz, ~50 B/pt).
  The only volume guard: if `max(t_ms) > OVER_LIMIT_MS` (11 min) the run+laps are kept but the
  **whole trail is dropped** (`ingest.ts`). See `docs/replay-format.md`.
- **`world_records`** — full WR mirror + history (`record_ms`, `holder_name`, `is_current`,
  `removed_at` [soft-delete — consumers must filter `removed_at IS NULL`], `provenance`,
  `character_slug`/`kart_slug`/`costume_slug`, `lap_splits_ms`, …). Partial unique index
  `idx_wr_current WHERE is_current=1` created in `connect.ts`.
- **`course_models`** — PK `(course_id, cc)`, `model_json`, `lap_length_px`,
  `status ∈ {graph,centerline}`, `source_run_count`, `version`, `built_at`.
- **`ghost_imports`**, **`wr_name_flags`**, **`wr_meta`**, **`service_status`**,
  **`player_alignment`**, **`screen_intervals`**, **`activity_events`** — audit / scraper /
  presence / activity support tables.

**Schema evolution note:** `connect.ts` still runs additive `ALTER`s for columns that `schema.sql`
has since caught up to (redundant but harmless — they exist for already-migrated DBs). The one
genuine divergence to remember is `runs.was_pb` (ALTER-only). The old "`ingest.ts` writes a `lap`
column not in schema.sql" note is **resolved** — `schema.sql` now declares `run_points.lap`.

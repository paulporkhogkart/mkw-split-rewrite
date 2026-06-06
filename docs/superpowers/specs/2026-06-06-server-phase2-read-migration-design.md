# Sub-project B Phase 2 — Read-migration + engine race-store teardown — Design

- **Date:** 2026-06-06
- **Status:** approved (brainstorm), pending implementation plans
- **Relates to:** [[client-server-shift]] sub-project B. Phase 1 (server + write path + run-review) is complete and merged. This is the deferred Phase 2 from `docs/superpowers/specs/2026-06-04-server-api-and-sync-design.md` §2/§6/§16.

## Goal

Make the **server the only source of race data for the monitor too**: repoint the monitor's reads (PB splits, replay trails) off the engine's local race store onto the server (via a Rust read-back + cache), then **delete the engine's race-data tier** so the engine is a pure detector. Also make **friends' PB + trail data available** to the frontend (no new monitor UI this round — the user wires the panel/rendering later).

## End-state architecture

- **Engine (Python) = pure detector.** Still records minimap points live (for the current-run overlay dot and the `run_finalized.points` upload) and still calibrates minimap thresholds, but owns **no local race store**: `save_run` and every race-data read are removed. Keeps only detection config — minimap **seeds / ROIs / thresholds**, tells, the `config` table — and `get_minimap_sample` (seed-derived).
- **Tauri app (Rust) = client edge.** The existing upload outbox **plus** a new read-back + rusqlite cache for per-course reads, served to the webview through Tauri commands. Holds the token; all network stays here.
- **Server (TS) = source of truth.** Two new read queries/routes; everything else already exists.
- **Frontend (Svelte).** Course reads come from Rust, not the engine. `minimap_sample` still comes from the engine.

**Invariant preserved:** the engine does no network and is the app's child over stdio only; the webview talking to Rust (not the network) keeps that intact.

## Build phases (sequential; teardown LAST, only once reads are proven server-side)

### 2a — Server read endpoints (`pi/`)

Two new public/me routes (queries over already-stored `run_laps` / `run_points`):

| Method | Path | Auth | Returns |
|---|---|---|---|
| `GET` | `/v1/me/pb-splits?course&cc` | token | The caller's PB on the course: `{ course, cc, total_ms, splits: { "1": lap1_ms, "2": lap2_ms, … } }` (the cumulative split per lap, mirroring the engine's old `emit_pb_splits` shape). `{ course, cc, total_ms: null, splits: {} }` when the caller has no live PB or the PB is a legacy total-time-only run (no laps). |
| `GET` | `/v1/trails?course&cc` | optional token | Every roster player's PB **trail** for the course: `[ { player_id, player, total_ms, is_me, points: [[t_ms,cx,cy,score], …] } ]`. `is_me` is `true` for the entry whose player matches the supplied token (false/absent without a token). Only live PB runs have points; legacy PBs are omitted (no trail). |

- `cc` defaults to 150; `course` is slugified server-side (same `slugify`); unknown course → 400 (matches the other read routes).
- `/v1/friends-pbs?course&cc` already exists and is reused as-is for friends' PB *times*.
- Implemented in `pi/src/db/reads.ts` (`myPbSplits`, `courseTrails`) + `pi/src/api/reads.ts` (routes). `is_me` resolution: the trails route runs an **optional** token check (look up the player if a token is present; never reject when absent).

### 2b — Rust read-back + cache (`src-tauri/src/sync.rs`)

- New rusqlite table `course_cache(course_slug TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at INTEGER)` next to the outbox.
- New Tauri command `sync_course_reads(course: String) -> String` (JSON): fetches `/v1/me/pb-splits`, `/v1/trails`, `/v1/friends-pbs` for the course (with the token), combines into one payload `{ pb_splits, trails, friends_pbs }`, writes it to `course_cache`, and returns it. **On any network error, returns the last cached payload** for that course (or an empty payload if none). All three sub-fetches use `slugify(course)` for the query param.
- After a successful **own upload** of a run whose course matches a cached course, clear that course's cache row so the next read re-fetches (keeps the just-set PB/trail fresh). (Done in the drain loop where it already deletes the outbox row.)
- Registered in `lib.rs` `invoke_handler` alongside the existing sync commands.

### 2c — Frontend repoint (`src/`)

- On RACING entry (`App.svelte` `screen_change` handler), replace the engine sends `get_pb_splits` and `get_replay_paths` with a single `invoke("sync_course_reads", { course: selCourse })`. From its result:
  - `pb_splits` → set `pbSplits` / `pbTotalMs` stores (these already feed the Discord in-race delta and any split UI — this **fixes the known gap** where Discord's live delta still read the engine's local store).
  - `trails` → split into **own** (`is_me`) and **friends**. Own trail feeds the existing `replays` store the minimap overlay (`FeedOverlay`) already draws — **mapping each server point `[t_ms,cx,cy,score]` → `[cx,cy]`** to match the overlay's current `{id, points:[[x,y],…]}` shape. Friends' trails go into a **new `friendsTrails` store** in their raw server shape (data available; **not drawn** this round, so no mapping needed).
  - `friends_pbs` → a **new `friendsPbs` store** (data available; **no Rail panel** this round).
- `get_minimap_sample` is unchanged (still engine IPC).
- The engine's `pb_splits` / `replay_paths` tracker-event handlers can stay as dead no-ops until 2d removes the emits (or be removed in 2c — either order is safe since the frontend stops relying on them here).
- New stores added to `src/lib/stores.js`: `friendsTrails` (`[]`), `friendsPbs` (`[]`).

### 2d — Engine teardown (`mkw_tracker/`)

Remove the race-data tier now that nothing reads it:

- **`database/replay_repo.py`:** delete `save_run`, `_maybe_update_pb`, `_prune_history`, `get_pb`, `get_pb_splits`, `get_history`, `get_friends_pbs`, `_load_replay`, `export_mkwreplay`, `save_friend_pb`, `replay_paths`. **Keep** `get_minimap_seed/set_minimap_seed`, `get_minimap_roi/set_minimap_roi`, `get_minimap_threshold/set_minimap_threshold`, and `_to_ms` (still used by the lifecycle laps payload). Remove the `replays` / `replay_points` / `replay_splits` table creation from `database/migrations.py` and their JSON import from `import_json_files` (keep the minimap-seeds/rois/thresholds import).
- **`lifecycle/race.py`:** remove the `self._mm_rec.save(...)` call in `_finalize_recording`. Recording (`_mm_rec.start/points`), the `points` payload, and `calibrate_from_race` + `set_minimap_threshold` stay.
- **`main.py` `_handle_ipc_command`:** remove the `get_pb_splits`, `get_replay_paths`, and `export_pb` command branches. **Keep** `get_minimap_sample`.
- **`ipc/protocol.py`:** remove `emit_pb_splits`, `emit_replay_paths`, `emit_pb_export` (and `ExportPbCmd`). **Keep** `emit_minimap_sample`, `emit_minimap_update`, `minimap_update_payload`.
- **CLI:** remove the `--history` flag and the `MinimapPlayer` "history" load path (the live resume-from-`MinimapPlayer` path during a race stays). `--history` is engine-CLI-only; the Tauri app never passes it, so zero frontend impact.
- Delete now-dead tests (`tests/test_replay_paths.py`, `tests/test_pb_splits.py`) or repoint them; add a teardown guard test asserting the removed functions are gone and the suite is green without the race store.

## Stays vs goes

| Stays (detection / live) | Goes (race data → server) |
|---|---|
| minimap seeds / ROIs / thresholds (+ their getters/setters, JSON import) | `replays` / `replay_points` / `replay_splits` tables (+ JSON import) |
| `get_minimap_sample`, `calibrate_from_race`, `set_minimap_threshold` | `save_run`, `get_pb`, `get_pb_splits`, `get_history`, `get_friends_pbs`, `replay_paths`, `export_mkwreplay`, `save_friend_pb` |
| MinimapRecorder (live overlay points + `run_finalized.points`) | `--history` mode + `MinimapPlayer` history load |
| `run_finalized` emit (unchanged) | engine `emit_pb_splits` / `emit_replay_paths` / `emit_pb_export` |

## Friends = data-only (this round)

`friends_pbs` and `friends` trails are fetched, cached, and placed in Svelte stores so they're available — but **no new monitor UI** is built: no Rail PB-times panel, and friends' trails are **not drawn** on the minimap. The user will wire that rendering later. Own trails + own splits *do* keep rendering (repointed source), since they render today.

## Consequences to flag

- **Live-only trails/splits.** Only live (post-cutover) PB runs carry laps/points. Legacy/carryover PBs were total-time-only, so a course whose PB is still a legacy run shows no trail and empty splits until a live PB is set.
- **Own trails = PB ghost only.** The local store showed *every* past run's trail on the course; the server `trails` endpoint returns one PB trail per player, so the monitor now shows your own PB ghost (plus friends' once drawn) rather than all historical attempts. Intended (a PB ghost to race against), but a deliberate behavior change.
- **Local pre-cutover trails dropped.** Deleting the engine's `replays` tier discards the user's locally-stored historical trails. Consistent with the server being authoritative and the user's "fine to lose existing data" stance. (No migration of local replays into the server is in scope.)
- **Offline.** `sync_course_reads` serves the last cached payload per course when the network is down; an uncached course offline shows nothing (graceful empty).

## Testing

- **TS (`pi/`):** `db/reads.ts` query tests for `myPbSplits` (own PB laps; empty for none/legacy) and `courseTrails` (roster trails, `is_me` flag, legacy omitted) over a temp SQLite; `api/reads.ts` route tests (auth-optional trails, token me/pb-splits, unknown-course 400).
- **Rust (`sync.rs`):** `course_cache` upsert/read; `sync_course_reads` combines the three fetches against a mock server; offline falls back to cache; cache cleared after a matching own upload. Pure helpers unit-tested with in-memory conns (mirrors the existing sync tests).
- **Engine (Python):** teardown leaves the suite green; a guard test asserts the removed `replay_repo` functions/tables are gone and `_finalize_recording` no longer calls `save`; minimap seed/roi/threshold paths still work.
- **Frontend:** `svelte-check` 0/0 + build; own splits/trails still populate their stores from `sync_course_reads`; new `friendsTrails`/`friendsPbs` stores exist.

## Execution staging

Four implementation plans under this spec, built and ff-merged in order:
1. **2a** server read endpoints (standalone-testable with curl + TS tests).
2. **2b** Rust read-back + cache + command.
3. **2c** frontend repoint + new friends stores.
4. **2d** engine race-store teardown.

2a–2c leave the engine's local store in place (harmless); only 2d removes it, after the new read path is proven end to end.

## Decided judgment calls

- Read-back lives in **Rust + rusqlite cache** (offline-resilient; keeps network in the client edge; consistent with `sync.rs`/`pb_cache`). Not frontend-direct.
- **`--history` dropped** from the engine (CLI-only; never used by the app).
- **Friends = data available only**; no new monitor UI this round.
- Minimap seeds/ROIs/thresholds + `get_minimap_sample` + recorder + calibration are **detection config and stay**.
- Trails/splits are **live-run-only**; legacy PBs have none until a live PB exists.

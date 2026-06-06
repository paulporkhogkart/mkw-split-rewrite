# Configurable trail playback + friends' rendering — Design

- **Date:** 2026-06-06
- **Status:** approved (brainstorm + Visual Companion mockup), pending implementation plans
- **Relates to:** [[client-server-shift]] sub-project B Phase 2 (read-migration). Extends the read side and pulls friends' monitor **rendering** forward from sub-project C (the user asked for it now). Mockup: `.superpowers/brainstorm/3679-1780744490/content/trails-settings.html`.

## Goal

Let the user choose, **per roster player**, which of that player's past-run ghost trails replay on the minimap during a race (none / PBs / best N / last N / all), in a **per-player colour**, with multi-run sets **faded by rank** so the standout run stands out. This exposes the trail data the server already stores for every run and renders friends' ghosts alongside the user's own.

## Data situation (already true)

The engine records minimap points for **every** attempt and uploads them; the server stores `run_points` for every uploaded run (finished auto-upload; resets via the review popup on Submit; only discarded runs never land). So **every run's trail is on the server, PB and non-PB alike.** The only gap is the read side: Phase 2's `/v1/trails` returns only PB trails. This feature generalises the read + adds the config + rendering. **No change to recording or upload.**

## Config model — per player

Keyed by `player_id`. Each roster player (the caller included) has:

- **mode**: `none` | `pbs` | `best` | `last` | `all`
- **n**: integer (used by `best`/`last`; e.g. 10 or 100)
- **color**: hex string (from a preset palette or custom)

Plus one **global** `fadeByRank: boolean` (default on).

Stored in app settings (**localStorage**, like the Discord/Sync settings). Players with no stored entry use the **default**: `pbs` (every player), fade on — light and predictable; the user tunes from there. The roster list comes from the server (below); a player added to the roster later inherits the default until configured.

### Modes
- **none** — draw nothing for this player.
- **pbs** — the player's PB run only (single trail, full opacity).
- **best N** — the player's N fastest *finished* runs, ranked fastest→slowest.
- **last N** — the player's N most recent runs, ranked newest→oldest.
- **all** — every stored run for the player (ranked newest→oldest).

### Opacity ramp ("Fade by rank")
When `fadeByRank` is on (default), multi-run sets fade by rank so the standout run reads clearly:
- **best** — fastest run fully opaque; each slower run dimmer.
- **last** / **all** — newest run fully opaque; each older run dimmer.
- Opacity = linear from `1.0` at rank 0 down to a floor of **0.2** across the set (clamped; sets of 1 are full opacity). `pbs` is always full opacity. Off → all runs full opacity.
The server returns each player's runs **already ordered by rank** (rank 0 first), so the client assigns opacity by array index.

## Server (`pi/`)

- `GET /v1/roster?season` (optional token) → `[{ player_id, display_name, is_me }]` for the active season's roster, so settings can list everyone (no roster-list endpoint exists yet). `is_me` set when a valid token is supplied.
- `GET /v1/players/:id/trails?course&cc&mode&n` (public) → that player's selected runs' trails, **ordered by rank**:
  - `pbs` → the `is_pb=1` run (≤1).
  - `best` → finished runs `ORDER BY total_time_ms ASC LIMIT n`.
  - `last` → runs `ORDER BY COALESCE(ended_at, started_at, '') DESC, id DESC LIMIT n`.
  - `all` → runs `ORDER BY … DESC` (newest first), no limit.
  - Response: `[{ run_id, total_ms, status, points: [[t_ms,cx,cy,score], …] }]`; runs with no points (legacy total-only) omitted. (Per-player windowing via `ROW_NUMBER()` is not needed here since each call is one player — a plain `ORDER BY … LIMIT n` suffices.)
- `db/reads.ts`: `roster(db, seasonId)`, `playerTrails(db, seasonId, playerId, courseId, cc, mode, n)`. The Phase-2a `/v1/me/pb-splits` + `/v1/friends-pbs` stay; the Phase-2a `/v1/trails` (all-PB) stays (harmless; may serve sub-project C later).

## Rust (`src-tauri/src/sync.rs`)

`sync_course_reads(course, config)` gains a `config` arg — a JSON array `[{player_id, mode, n}]` of the players with mode≠none (colour is a frontend concern, not sent). It fetches each listed player's trails via `/v1/players/:id/trails` (plus the caller's `/v1/me/pb-splits` and `/v1/friends-pbs` as before), tags each trail with its `player_id`, combines into `{ pb_splits, trails:[{player_id, run_id, total_ms, points}], friends_pbs }`, caches per course, and serves the last cache offline. (The per-player fetches replace the single all-PB `/v1/trails` call from Phase 2c.)

## Frontend (`src/`)

- **New "Trails" settings tab** (in `SettingsModal`, alongside Discord/Sync), matching the approved mockup: a roster list (from `GET /v1/roster`, cached), each row = **Show** dropdown (None/PBs only/Best N/Last N/All) + **Count** field (enabled only for Best/Last) + **Colour** chip opening a picker (preset palette + custom hex). A top **"Fade by rank"** toggle. All persisted to localStorage (`src/lib/trailSettings.js`, mirroring `syncSettings.js`).
- **Preset palette** (8, minimap-legible on dark): `#3d7cc2 #d98a3e #5aa86a #cf5b4e #9b6bd0 #46b0c8 #d56aa8 #c9b03e`.
- On RACING entry, `App.svelte` passes the active config (players with mode≠none + mode/n) to `sync_course_reads`; the result's `trails` (grouped by `player_id`) feed a `trails` store carrying `{player_id, color, runs:[{points, opacity}]}` (colour + opacity computed client-side from the per-player colour + the rank fade). `friends_pbs` store stays (data available).
- **Overlay** (`FeedOverlay.svelte` / `overlay.js`): draw every player's runs as moving ghost dots in the player's colour at the run's computed opacity (extending the existing single-trail interpolation to multiple coloured groups), plus a small **name→colour legend**. Replaces the current own-only `replays`-store rendering.

## Defaults / decisions
- Default config: **all players `pbs`**, fade **on**. Adjustable per player.
- Per-player config (not 2-group), keyed by `player_id`; colour configurable from presets or custom hex (user decision).
- Fade floor 0.2; PB/single-run = full opacity.
- `best`/`last` `n` is free integer (10, 100, …).

## Consequences / notes
- Trails are **live-run-only** (legacy total-time PBs have no points) — unchanged from Phase 2.
- **Performance:** large sets (e.g. everyone's `last 100`) put many simultaneous ghosts on the minimap; the fade keeps the standout readable, and the light default avoids surprise. Rendering should stay efficient (the dots are already interpolated each frame).
- Roster/colour config persists locally; the roster *list* needs the server once (cached) to enumerate players.

## Testing
- **TS (`pi/`):** `roster` (season members, `is_me` flag) and `playerTrails` (each mode's ordering + limit; legacy point-less runs omitted; unknown course 400) over temp SQLite; route tests (optional-token roster, public player-trails).
- **Rust:** `sync_course_reads` builds per-player fetches from the config + combines (pure helpers unit-tested with in-memory conns; the multi-fetch orchestration follows the existing untested-HTTP pattern); cache still serves offline + clears after a matching own upload.
- **Frontend:** `svelte-check` 0/0 + build; `trailSettings.js` persistence; opacity-ramp + per-player-colour mapping as a pure helper unit-tested (vitest, like `runReview.js`).

## Build phases (inline, ff-merged in order)
1. **TP-a — server:** `/v1/roster` + `/v1/players/:id/trails` (+ `reads.ts` queries).
2. **TP-b — Rust:** per-player fetch in `sync_course_reads` (config arg) + cache.
3. **TP-c — frontend settings:** Trails tab + `trailSettings.js` + roster fetch + the config passed on RACING entry.
4. **TP-d — overlay:** per-player coloured trails + rank-fade opacity + legend (the visible payoff).

TP-a/b leave rendering unchanged; TP-c wires the config; TP-d lights it up. Each is independently testable and ff-merged like Phase 2.

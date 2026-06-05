# Run review + gated upload — design

- **Date:** 2026-06-06
- **Status:** approved (brainstorm), pending implementation plan
- **Relates to:** sub-project B (client→server sync). Builds on the existing engine→app→server pipe and the Sync settings tab.

## Goal

CV detection is fallible. For a competition we must never (a) silently drop a real run or (b) upload a garbage/incomplete one. So: a finished/reset run reaches the upload outbox only once it is **complete enough**; otherwise it is held in a **review queue**, surfaced with a sound + a popup, and released to the outbox only after the user fills the missing fields (or discards it). Filling the popup for a just-finished run also corrects the engine's live selection state.

This also replaces the engine's **broken PB detection** (it reads the engine's local `replays` table, which has none of the migrated history or carryover seeds) with a **server-authoritative PB cache**, fixing Discord's PB signal as a side effect.

## Completeness rules (when a run is held)

| Field | Finished | Finished + PB | Reset |
|---|---|---|---|
| course | required | required | required |
| character | required | required | required |
| kart | required | required | required |
| total_time | required | required | — n/a |
| per-lap time + coins + mushrooms (all laps) | not required | **required** | — n/a |
| costume | optional | optional | optional |

- A run missing any *required* field is held; otherwise it goes straight to the outbox as today.
- "PB" is decided **before** upload from the local PB cache (see below).
- Resets are held on identity only (they are incomplete by definition). Even a 2-second reset nags for course/character/kart; the popup's **Discard** covers throwaways.
- **Per-lap values:** `coins` = change in coin count since the previous lap line (lap 1 vs race-start 0), **may be negative**. `shrooms` = mushrooms *used* during the lap (count of item-count decrements; e.g. 2 used lap 1, 0 lap 2, 1 lap 3 → `2, 0, 1`). **`0` and negatives are valid** — a per-lap field is "missing" only when null/unentered, never when `0`.

## PB detection — server-authoritative cache (approach A)

The server `runs` table is the source of truth for PB (per season/player/course/cc, incl. carryover). The app keeps a **local cache** of the authenticated player's current bests so PB can be judged pre-upload and offline.

- **Server:** new token-keyed `GET /v1/me/pbs` → for the active season + caller's player, returns `[{ course_slug, cc, total_time_ms }]` where `is_pb=1`. (Auth via the existing `requireToken`; resolves player from the token, so no player id needed client-side.)
- **Rust cache:** a `pb_cache(course_slug, cc, best_ms)` table next to the outbox. Seeded from `/v1/me/pbs` on launch and after each successful upload (reconcile from the server response / a refresh). **Optimistic update:** when a finished run beats the cached best, treat it as a PB *and* immediately lower the cached best — so back-to-back PBs while offline are both detected.
- **Keying:** the cache is keyed by course **slug**. `on_line` slugifies the run's course display-name with the *same* rule as the server (apostrophe-stripping `slugify`; `Wario Shipyard`→`warios_galleon` alias) before lookup — the same slug-consistency concern that otherwise causes `/v1/runs` 400s.
- **is_pb (for gating)** = run is finished AND (`total_ms < cached_best` for course+cc, or no cache entry).
- **Discord:** PB-achieved is now driven by this app-side determination; the engine's local-store PB emit (`pending_pb_event` → `emit_pb_achieved`) is retired.

## Data flow

```
engine (detector)            Rust app (lib.rs / sync.rs)                 server (pi/)
─────────────────            ───────────────────────────                 ────────────
run_finalized  ──stdout──▶   on_line:
  + detected fields            parse → is_pb (pb_cache) → completeness
  + total_laps                 ├─ complete   → outbox status='ready' ──▶ POST /v1/runs
  + laps[].lap_time_str        └─ incomplete → outbox status='pending_review'
                                              + emit "run_needs_review" {attempt_id, run, isPb, total_laps, missing[]}
                             drain loop: uploads status='ready' only
                             pb_cache: seed from GET /v1/me/pbs (launch + post-upload)

frontend                     review queue (store) ← run_needs_review
  RunReviewModal  ──submit──▶ invoke sync_resolve_pending(attempt_id, filled)
                               → merge into outbox body, flip status='ready'
                             ──discard─▶ invoke sync_discard_pending(attempt_id) → delete row
                             on launch: invoke sync_list_pending() → seed queue (resurfaced)
                             on submit of a *just-finished* run: also send set_selection to engine
```

## Components by layer

### Engine (`mkw_tracker/`) — stays a pure detector
- `run_finalized` payload gains: `total_laps` (from `LapTracker`; `courses.default_laps` is the fallback when per-race lap-count detection failed), and each lap in `laps[]` gains `lap_time_str` (the `"M:SS.mmm"` string, alongside `time_ms`), `coins` (delta vs the previous lap line, may be negative — the engine snapshots the coin count at each lap crossing and diffs), and `shrooms` (mushrooms *used* during the lap — the engine counts mushroom-count decrements within the lap window; acquiring items doesn't count). Per-lap capture hangs off the same lap-crossing hook that records the split; an uncaptured value is emitted as `null` (distinct from `0`).
- New inbound command `set_selection {course?, character?, kart?, costume?}` → sets `SelectionTracker.state` fields (dispatch added to the `if/elif` chain in `main.py`).
- Retire local-store PB emission (`pending_pb_event`/`emit_pb_achieved`) — PB is now app-side. (Local `replays` store remains until Phase 2's broader retirement; only its PB *signalling* is dropped.)
- New `option_lists` emit (courses/characters/karts/costumes for the active language, which the engine already loads from `images/<cat>/<lang>`) so the popup dropdowns have canonical options. Emitted on `ready` (or on request).

### Server (`pi/`)
- `GET /v1/me/pbs` (token-gated) — active season, caller's player, `is_pb=1` rows as `{course_slug, cc, total_time_ms}`.
- `ingest.ts`: store `lap_time_str` in `run_laps` (column already exists; the INSERT currently omits it).

### Rust (`src-tauri/src/sync.rs`)
- `outbox` gains a `status` column (`'ready'` | `'pending_review'`, default `'ready'`); drain query filters `status='ready'`.
- `pb_cache(course, cc, best_ms)` table + sync from `/v1/me/pbs` + optimistic update + reconcile from upload responses.
- `on_line`: compute is_pb, run the completeness check, route to `ready` vs `pending_review`, emit `run_needs_review` for held runs.
- Commands: `sync_resolve_pending(attempt_id, filled)` (merge + flip to ready), `sync_discard_pending(attempt_id)` (delete), `sync_list_pending()` (for launch resurface).
- Discord PB driven from the app-side is_pb determination.

### Frontend (`src/`)
- `RunReviewModal.svelte` — **built + approved** (props `run`/`isPb`/`options`/`queueIndex`/`queueCount`, events `submit`/`discard`; missing-field flags, two-step "Discard run" confirm far-left, sound on appear, queue counter). **Update for this scope change:** a PB's lap rows now carry three compact inputs — time + coins (allows negative) + mushrooms (≥0) — each required; `0` is a valid entry, empty = missing. Densifies the lap rows, so worth a quick re-preview when built.
- `App.svelte` wiring: a review-queue store; `run_needs_review` handler enqueues; render the modal for the head with the engine's option lists; on submit → `sync_resolve_pending` (+ `set_selection` **only for the just-finished run**, not resurfaced ones); on discard → `sync_discard_pending`; on launch → `sync_list_pending` to seed the queue.
- Sound asset already at `src/assets/run-review.wav`.

### Data
- Populate `courses.default_laps` in `seed_courses` (MKW is 3 laps for standard courses; confirm any oddballs). Used as the split-completeness fallback + server-side validation.
- `run_laps.coins`/`shrooms` already exist and `ingest` already stores them — no schema change. `coins` is a signed delta (INTEGER handles negatives); `shrooms` a non-negative use-count.

## Edge cases / decisions
- **Queue:** several pending runs → one popup at a time, "n / N" counter.
- **Resurface on launch:** `pending_review` rows from a previous session are surfaced; live-state is **not** set for them (you're not about to race that course).
- **Offline:** cache enables PB detection; runs queue and upload on reconnect; cache reconciles from upload responses.
- **Idempotency:** resolve rewrites the outbox body keyed by `attempt_id`; the server upsert is already idempotent by `attempt_id`.
- **Discard safety:** two-step confirm; Cancel lands where "Discard run" was.

## Phasing
- **Phase A — PB cache + Discord fix** (independently shippable; fixes the false-PB bug): `/v1/me/pbs`, Rust `pb_cache`, Discord switch.
- **Phase B — gating + review popup:** outbox `status`, `on_line` gating + `run_needs_review`, resolve/discard/list commands, engine `total_laps` + `lap_time_str` + **per-lap coins/mushrooms capture**, `option_lists` emit, `ingest` lap_time_str, `default_laps` populate, popup PB lap-row update (time+coins+mushrooms), App.svelte queue + popup wiring.
- **Phase C — live-state:** `set_selection` command + submit→set_selection for just-finished runs.

## Out of scope
- Phase 2 retirement of the engine's local race store.

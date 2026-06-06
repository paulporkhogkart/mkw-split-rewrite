# Phase 2c — Frontend repoint Implementation Plan

> Executed inline. Spec: `docs/superpowers/specs/2026-06-06-server-phase2-read-migration-design.md` §2c.

**Goal:** Source the monitor's course reads (PB splits + own/friends trails + friends PBs) from the Rust `sync_course_reads` cache instead of the engine; make friends' data available (no new UI).

**Done:**
- `src/lib/stores.js`: add `friendsTrails` (`[]`) and `friendsPbs` (`[]`) stores.
- `src/App.svelte`:
  - import the two new stores.
  - `loadCourseReads(course)`: `invoke("sync_course_reads", {course})` → `pbSplits`/`pbTotalMs` from `pb_splits`; own trail (`is_me`) → existing `replays` store mapped `[t,cx,cy,score]`→`[cx,cy]`; friends' trails → `friendsTrails`; `friends_pbs` → `friendsPbs`. try/catch keeps stores on offline.
  - RACING-entry handler: replace `get_pb_splits` + `get_replay_paths` sends with `loadCourseReads(selCourse)`; keep `get_minimap_sample` (engine, seed-derived).
- The dead `pb_splits` / `replay_paths` tracker-event handlers are left as no-ops (the engine no longer receives the requests); removed in 2d with the engine emits.

**Verify:** `npm run check` 0/0, `npm run build` clean. Discord in-race delta now reads server-sourced `pbSplits`/`pbTotalMs` (fixes the known local-store gap).

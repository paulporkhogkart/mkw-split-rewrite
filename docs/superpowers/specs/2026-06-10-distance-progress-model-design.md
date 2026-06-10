# Distance-Based Per-Lap Progress Model (v2) — Design

**Date:** 2026-06-10
**Status:** Approved (brainstorm); pending implementation plan
**Supersedes:** the completion model of `2026-06-10-course-progress-model-design.md` and Plan 1 (`2026-06-10-progress-centerline-slice.md`, shipped) — specifically the single collapsed centerline and the `(lap−1 + u)/totLap` lap-count scaling. **Subsumes** the original spec's deferred Plan-2 work (full graph / branches / engine lap stamp / rebuild-on-ingest / retirement).

---

## 1. Problem & Goals

The shipped live-completion bar (Plan 1) computes `completion = (lap − 1 + withinLapFraction) / totalLaps`, which **assumes every lap covers 1/N of the race**, and builds the course model by **collapsing all laps into one centerline** (only valid if every lap is the same loop). Mario Kart World breaks both assumptions: lap counts vary (3, 5, …), and laps are not consistent — some courses repeat a circuit, others have distinct sections per lap, and a different first lap / run-in is common. On those courses the bar's lap boundaries land at the wrong place (e.g. forcing the end of a long first lap to 33% when it's really ~50% of the race by distance).

Raw distance-travelled (movement ÷ a reference total) was rejected: as players learn shortcuts they travel *less* distance, so the denominator drifts and runs would never reach 100%.

### Goals
- **Completion is distance-along-route, not lap count:** `% = distance reached along the course / total course distance`. Always reaches 100% at the finish regardless of shortcuts (it measures route *position*, not wheels turned).
- **Per-lap geometry:** each lap index has its own route; laps may differ in shape and length. Lap counts vary per course.
- **Lap dividers are the model's known per-lap boundaries** (`laps[k].startOffsetPx / totalLengthPx`), shown from the start of the race, so they land at the true per-lap proportions instead of assumed 1/N. (Superseded the original "placed live at each `cur_lap` tick" idea: live-only dividers leave the bar an undivided mass until the first lap completes — wrong, since the model already knows where the boundaries are.)
- **Branch- and self-crossing-aware** within each lap (the graph work deferred from Plan 1).
- Fixes the residual **~5% lap-1 start offset** as a side effect (lap 1 gets its own grid-inclusive geometry).

### Non-goals
- De-duplicating identical lap routes (storage/data-pooling optimisation) — explicitly deferred (YAGNI); the explicit per-lap model is correct without it.
- Displaying which branch a player took (only the % must be correct).

---

## 2. Architecture

A course model is an **ordered list of N lap-routes**. Each lap-route is a branch-aware graph carrying arc-length, built by pooling that lap *index* across the recent reference runs. Two decoupled phases, as in Plan 1:

- **Offline builder** (per course, event-driven): group runs' points by lap index → per-lap branch-aware route → measure per-lap arc-length → CourseModel v2, stored & versioned.
- **Live projector** (per player, per frame, presence hub): the HUD lap selects the lap-route; project the position onto it (forward-window matcher), accumulate cumulative arc-length → distance-based completion; emit lap dividers live.

The Plan-1 module `pi/src/progress/` is reworked into v2; the forward-window projector core (`project.ts`) largely survives — it now runs against one lap-route's graph and the completion arithmetic changes.

---

## 3. Data model

`course_models.model_json` holds CourseModel **version 2** (the `course_models`/`player_alignment` tables from Plan 1 are unchanged; `version` column carries the format version).

```jsonc
CourseModel {
  version: 2;
  totalLengthPx: number;        // Σ lap-route lengthPx
  laps: LapRoute[];             // length = N (the course's lap count), in order
  status: 'graph' | 'centerline';
}
LapRoute {
  index: number;                // 1-based lap index
  lengthPx: number;             // arc-length of this lap's route (common frame)
  startOffsetPx: number;        // Σ lengthPx of prior laps (lap 1 = 0)
  graph: Graph;                 // branch-aware geometry for THIS lap
}
Graph {                         // one lap's route (the Plan-1-spec CourseGraph, scoped to a lap)
  nodes: { id, x, y, sWithinLap }[];   // sWithinLap in [0,1] along this lap
  edges: { id, a, b, poly:[x,y][], arcLen, sLo, sHi, kind:'main'|'branch', passThrough:number|null }[];
  startNode: number;            // progress 0 of this lap (grid for lap 1; the line for laps >= 2)
}
```

`completion(lap k, within-lap fraction u) = (laps[k-1].startOffsetPx + u · laps[k-1].lengthPx) / totalLengthPx`, clamped to `[0,1]`.

---

## 4. Builder (per lap index)

Pure TypeScript in `pi/` (no Python/opencv dependency; Zhang–Suen thinning hand-rolled, as in the original spec §5.5). Input: `course_id`, `cc`, the recent W finished runs.

1. **Group by lap index.** Split each run's `run_points` by the per-point **`lap` stamp** (§7); fall back to time-derived lap (cumulative `run_laps.lap_time_ms`) for legacy rows. Pool all runs' lap-k points together for each index k = 1..N. N is the lap count of the densest finished run (`run_laps` row count).
2. **Per lap index k:** align the runs' lap-k trails to a common frame (translation via f-binned-centroid correspondence, within the lap) → score-weighted density raster → **threshold + Zhang–Suen skeleton → graph extraction → junction classification** (branch vs pass-through crossing) → **anchor** `startNode` at the lap's start position (the centroid of where lap k begins across runs: lap 1 = the runs' first points / grid; lap k≥2 = the lap-(k−1)-end line crossing) → measure `lengthPx` (total route arc-length).
3. **Assemble:** `startOffsetPx` cumulative; `totalLengthPx = Σ`. Emit `LapRoute[]`.
4. **Fallbacks:** a degenerate skeleton for a lap → that lap's f-ordered centerline (`status='centerline'` if any lap falls back). < 1 usable run → no model (no bar).

This is the original spec's graph builder (§5) applied **once per lap index** instead of once over collapsed laps.

---

## 5. Live projector

Replaces Plan 1's `makeLiveCompletion`/`projectStep` arithmetic; keeps the forward-window matching core.

**Per-player state:** `{ lap, edge, u, x, y, t }`.

**Per frame** (aligned position, HUD `cur_lap` k, `tot_lap`, `track_state`, `t`):
1. **Stale** (`track_state` not fresh) → hold last completion.
2. **Run reset** (course changed, or `k` < stored lap) → clear state (bootstrap).
3. **Lap advance** (`k` > stored lap, in-race) → reset within-lap matcher onto `laps[k-1]`.
4. **Project** the position onto `laps[k-1].graph` with the forward-window matcher (monotonic within lap, branch-agnostic, no snap — the Plan-1 projector, scoped to this lap-route) → within-lap fraction `u`.
5. **Completion** = `(laps[k-1].startOffsetPx + u·laps[k-1].lengthPx) / totalLengthPx`, clamped `[0,1]`, monotonic.
6. **Post-finish** (`k` > N) → hold 100%.

Returns `{ completion, dividers }`. `dividers` are the **model's interior lap boundaries** — `laps[k].startOffsetPx / totalLengthPx` for k = 1..N−1 (lap 1 starts at 0, the finish at 1.0 — neither is drawn) — a constant per course, available the moment the model loads, so the bar shows its lap segments from the gun. No per-frame state.

---

## 6. Frontend

The card's lap bar changes from **N equal segments** to **one continuous fill** at `completion`, plus **divider ticks** at the model-derived `dividers[]` boundaries (drawn from the start, not as laps complete). `src/lib/playerCard.js`: `lapSegments(...)` → `{ fill: completion, dividers }`; `PlayerCard.svelte` renders one bar + ticks. Uneven, non-1/N gaps fall out automatically. The presence frame already carries `completion`; it gains `dividers`. The temp yellow debug `%` is removed once validated.

---

## 7. Engine — per-point lap stamp

`mkw_tracker/minimap/recorder.py` stamps each recorded point with the current HUD lap (from the lap tracker) → emitted in the `run_finalized` `points` payload as a 5-element array `[t, cx, cy, score, lap]` → `run_points.lap` (column added in Plan 1). The **Rust uploader (`src-tauri/src/sync.rs`) forwards the run_finalized JSON opaquely** (it only strips `type` / nulls a partial `laps` set — it never restructures `points`), so the lap rides through with no Rust change. `pi/src/db/ingest.ts` reads the optional 5th element (legacy 4-tuples → `lap = null`) and stores it. Makes the builder's lap grouping exact (no countdown/boundary ambiguity). Old data still works via the time-derived fallback. The only piece that needs the user's hardware to verify is the `main.py` wiring of `lap_state.current_lap` into the recorder during live play; the recorder/serialisation/ingest are all unit-tested.

---

## 8. Consumers & retirement

- **Live hub** → the v2 projector.
- **`avg_completion_before_reset`** (`resolveCompletion`) → replays each reset's trail through the v2 projector against the CourseModel (distance-based; numbers shift, more correct).
- **Retire** the single-run `pi/src/stats/progress.ts` (`prepareReference`/`step`/`clipTeleports`) and `courseReference`'s single-run path once the stat is migrated.

---

## 9. Recompute cadence

Event-driven + windowed (unchanged from the original spec §8): a finished run for a course marks its model stale → debounced (~30 s) rebuild from the last W runs → write `course_models` → hub drops its cache. CLI `build-course-model --course <slug>` for backfill. Config: `PROGRESS_MODEL_WINDOW=40`, `PROGRESS_MODEL_DEBOUNCE_MS=30000`.

---

## 10. Config keys

Reuse Plan-1 keys (`PROGRESS_MATCH_DIST_PX`, `PROGRESS_EPS_BACK`, `PROGRESS_REACH_K`, density/window/min-runs). Add `PROGRESS_SKELETON_*` (thinning threshold, resample spacing) per the original graph spec. Documented in `docs/config-reference.md`.

---

## 11. Testing strategy

- **Builder (synthetic):** a **uniform** N-lap course (all laps the same loop) → boundaries land at 1/N, `totalLengthPx = N·lapLen`; an **uneven** course (lap 1 longer) → boundaries at real proportions; a **branch** that rejoins → two edges, one progress interval; a **self-crossing** → pass-through pair. Per-lap arc-lengths + `startOffsetPx` correct.
- **Projector:** distance completion across laps; a **shortcut** (position jumps ahead on the route) still reaches 100%; HUD-lap selects the right lap-route; **live dividers** pushed at ticks; monotonic / stale-hold / post-finish-hold / run-reset.
- **Real-data probe:** rebuild `bowsers_castle` (uniform 3-lap) → boundaries ≈ 33/66/100, **start ≈ 0%**, finish = 100%; if an uneven course exists in `mkw.db`, confirm non-1/N boundaries.
- Existing pi/frontend suites stay green; card interface change is covered by `playerCard` tests.

---

## 12. Rollout / migration (phased plan)

The implementation plan will phase this so each lands testable:
1. **Engine lap stamp** (`recorder.py` → Rust payload → ingest) + exact lap grouping.
2. **Per-lap builder** (the graph CV per lap index) + CourseModel v2 + CLI/backfill.
3. **Distance projector** swap (live hub) + the dividers.
4. **Frontend** bar/dividers; remove temp debug %.
5. **Reset-stat migration** + retire the single-run projector.

All local + unpushed, per the established workflow.

---

## 13. Risks & open questions

- **Per-lap data volume:** ~W samples per lap index (one lap per run). For W≈40 that's ample for a clean route; very-low-run courses degrade to the centerline fallback.
- **Skeletonisation in Node** remains the largest implementation unknown (hand-rolled Zhang–Suen vs a vetted dep) — same as the original spec.
- **Lap-1 run-in distance:** lap 1's route includes the grid→line run-in, so lap 1's share is slightly inflated by pre-timer driving. Accepted as honest distance; could trim to the timer line later if undesired.
- **Lap-count mismatch:** if a live race reports more laps than the model has lap-routes (rare), clamp to the last lap-route and hold; rebuild picks up the correct N.
- **De-dup deferred:** identical repeated laps are stored separately; a tiny seam between independently-built identical laps is possible — add shape de-dup if it shows.

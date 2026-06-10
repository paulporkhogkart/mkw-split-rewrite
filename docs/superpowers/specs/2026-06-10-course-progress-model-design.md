# Course Structure & Live Progress Model — Design

**Date:** 2026-06-10
**Status:** Approved (brainstorm); pending implementation plan
**Supersedes:** the single-run reference projector introduced 2026-06-09 (`pi/src/stats/progress.ts` `prepareReference`/`step`, and `courseReference` in `pi/src/stats/completion.ts`).

---

## 1. Problem & Goals

The player-card **live race-completion %** is produced by projecting a player's 2D minimap position onto a 1D progress value. The current approach builds a reference path from **one** past finished run and projects the live position onto it with nearest-point + lap-window gating. It fails in two structural ways, both confirmed live on `bowsers_castle`:

1. **Self-crossing snap.** Where the lap passes near an earlier section, the % jumps backward then recovers (observed `33% → 17% → 33%`).
2. **Start/finish seam aliasing.** At the lap line, the % drops as if the lap restarted (observed at the end of lap 2 reading as the start of lap 1).

Root causes: (a) a single noisy run is a fragile reference (a recent bug — `clipTeleports` anchor-freeze — truncated the reference to 38 s of a 180 s race and collapsed the lap boundaries to `[1,1,1]`); (b) a single arc-length path fundamentally cannot represent a self-crossing or an alternate route; (c) lap boundaries derived by matching point timestamps to lap times are brittle.

### Goals
- A **monotonic, broadcast-grade** live % that never snaps backward on self-crossings or at the start/finish seam.
- Correct % on tracks with **alternate/branching routes**, regardless of which route a player takes.
- **Smooth out** per-run minimap noise by aggregating many runs from all players.
- **Improve automatically** as more runs accumulate; usable from the very first recorded run.

### Non-goals
- Detecting or displaying *which* route a player took (only the % must be correct).
- Pixel-perfect %. "Monotonic and roughly right" is the bar.
- Any change to the frontend card interface (it continues to consume `completion` as `0..1`).

---

## 2. Chosen approach

Model each course as a **CourseGraph** (a skeleton of the track): **nodes** at junctions/endpoints, **edges** = path segments, each edge carrying a **progress range** `[p_lo, p_hi] ⊂ [0,1]`. The graph represents **one lap**; the race drives it `total_laps` times.

- A **branch** (alternate route) is two edges that share the same progress interval between a *split* node and a *merge* node.
- A **self-crossing** is a *pass-through* node where two edges with **different** progress ranges cross without connecting.
- Within an edge, progress interpolates linearly by arc length between the endpoint nodes' progress values.

Live **completion** = `(lap − 1 + within-lap-progress) / total_laps`, with `lap` taken from the HUD (authoritative), not inferred from geometry.

Two **decoupled phases** share only the stored model:

- **Offline builder** (per course, re-run as data arrives): aggregate the recent window of finished runs → a CourseGraph, stored & versioned.
- **Live projector** (per player, per frame, in the presence hub): align the incoming position → match it onto the graph within a forward window → emit `completion`.

This was chosen over (A) a single averaged centerline — which cannot represent branches — and (B) ordered checkpoint gates — which is "the graph with the structure discarded," requiring the same branch detection but yielding a coarser %.

---

## 3. Architecture

```
OFFLINE  (per course, event-driven)
  recent finished runs ─▶ align each ─▶ merge→density ─▶ skeletonise→graph ─▶ anchor progress (time) ─▶ COURSE MODEL
                                                                                                            │ (mkw.db, versioned)
LIVE     (per player, per frame, presence hub)                                                              ▼
  live pos + HUD lap ─▶ align to frame ─▶ match on graph ◀── reads model ──┘ ─▶ (lap−1+u)/laps ─▶ completion 0..1 ─▶ card
```

The builder may be slow and clever; the live path stays cheap and stateless-per-frame (beyond a tiny per-player state). The card interface is unchanged.

---

## 4. Data model (mkw.db)

### 4.1 `run_points` — changed
Add a `lap` column: `run_points(run_id, t_ms, cx, cy, score, lap)`. The engine stamps the current HUD lap (1-based) on each recorded point. Existing rows have `lap = NULL`; the builder falls back to deriving lap from `t_ms` vs `run_laps` cumulative times when `lap` is null. Migration adds the column (nullable).

### 4.2 `course_models` — new
One row per `(course_id, cc)`:

| column | meaning |
|---|---|
| `id` | PK |
| `course_id`, `cc` | course + engine class (live = 150) |
| `model_json` | the CourseGraph (see §6) |
| `lap_length_px` | total lap arc length (common frame) |
| `status` | `graph` \| `centerline` (degenerate fallback) |
| `source_run_count` | runs that went into this build |
| `version` | model-format version integer |
| `built_at` | UTC timestamp |

`UNIQUE(course_id, cc)`; a rebuild **replaces** the row.

### 4.3 `player_alignment` — new
One row per `player_id` (drift is a property of the capture setup, which uses the fixed minimap ROI, so it is course-independent):

| column | meaning |
|---|---|
| `player_id` | PK |
| `dx`, `dy`, `scale` | similarity transform into the common frame |
| `updated_at` | UTC timestamp |
| `sample_count` | runs used to estimate it |

Missing row → identity transform (acceptable under the "minor drift" assumption).

---

## 5. Course-model builder

A pure-ish pipeline in `pi/`, invoked per course. Input: `course_id`, `cc`, and the most recent `W` finished runs (with points) for that course.

### 5.1 Windowing
Select the last `W` (default **40**) finished runs for the course at `cc`, newest first, that have `run_points`. The window makes the model track current racing lines as they drift.

### 5.2 Lap-collapse + in-lap fraction `f`
For each run, split its points into laps (using the new `lap` column, or `run_laps` cumulative times as fallback) and compute each point's **in-lap time fraction** `f ∈ [0,1)`. **Fold every lap of every run into one lap**: the result is one lap's worth of geometry with `~total_laps × W` samples, each carrying `(cx, cy, f, score)`. This is the primary noise-reduction step and the source of `f`, the progress prior.

### 5.3 Per-run alignment (using `f` for correspondence)
Minor per-capture drift is removed by fitting a similarity transform (translation + uniform scale; rotation optional, expected ≈0) per run to a common frame. `f` provides free cross-run correspondence: the centroid of points at `f ≈ k` in one run maps to the centroid at `f ≈ k` in the reference aggregate. Fit the transform by minimizing the distance between `f`-binned centroids (no ICP). The first/densest run defines the initial common frame; subsequent runs align to the growing aggregate. Per-player transforms are aggregated from their runs' transforms and written to `player_alignment` for live use.

### 5.4 Density merge
Rasterize all aligned points into a 2D grid over the minimap ROI (or a tight bbox), accumulating a small Gaussian splat per point **weighted by `score`** (interpolated points, `score = 0`, contribute nothing). The result is a density field bright along the real track and along any well-travelled alternate route.

### 5.5 Skeletonisation
Threshold the density into a binary track mask (morphological close to bridge small gaps), then skeletonise to a 1-px centerline network via morphological thinning (**Zhang–Suen**). Node has no standard skeletonisation library, so this is hand-rolled (≈100 lines, well-specified and unit-testable) rather than taking an image-processing dependency; the plan may instead vet a small library. The builder stays in **TypeScript (`pi/`)** so the model can be (re)built on the server host — including a Raspberry Pi — with no Python/opencv dependency. Branches appear as forks; self-crossings as X's.

### 5.6 Graph extraction
From the skeleton: **nodes** = pixels with neighbour-degree ≠ 2 (endpoints deg 1, junctions deg ≥ 3); **edges** = pixel chains between nodes, simplified to polylines (Douglas–Peucker) with arc lengths.

### 5.7 Time-anchored progress + junction classification
Map the sampled points (with their `f`) onto the nearest edge; each edge gets a distribution of `f` and a dominant traversal direction.
- **Node progress** = median `f` of nearby points. Normalize around the lap cycle so the **start node** (where `f` wraps `1 → 0`, i.e. the start/finish line) is `progress = 0` and progress increases along travel.
- **Junction classification** at a degree-4 node:
  - **Crossing (pass-through):** runs traverse straight through (enter edge A, exit the opposite edge) and the two through-edges have **different** `f` ranges. Pair them; not a routing choice.
  - **Branch:** one incoming edge, two outgoing edges with the **same** `f` range that later reconverge at a merge node. Tag both edges as branch-parallel between split and merge, sharing the progress interval.
- **Edge progress range** = `[progress(startNode), progress(endNode)]` along the traversal direction.

### 5.8 Fallback (degenerate skeleton)
If skeleton extraction yields a degenerate/disconnected graph (sparse or noisy data), fall back to a **single `f`-ordered centerline**: order all points by `f`, build one cyclic edge with arc-length progress. This is still a valid CourseGraph (one loop edge, no branches) the live projector consumes identically. `status = 'centerline'`. A graph is **attempted from the first run**; sparse early models simply have no branches yet and improve as runs accumulate.

---

## 6. CourseGraph format (`model_json`)

```jsonc
{
  "version": 1,
  "start_node": 0,
  "lap_length_px": 1460.0,
  "nodes": [ { "id": 0, "x": 31.0, "y": 110.0, "progress": 0.0 }, ... ],
  "edges": [
    {
      "id": 0, "a": 0, "b": 1,
      "poly": [[31,110],[34,98], ...],   // common-frame polyline
      "arc_len": 512.0,
      "p_lo": 0.0, "p_hi": 0.34,
      "kind": "main" | "branch",
      "pass_through": 7 | null            // paired edge id at a crossing, else null
    }, ...
  ]
}
```

`progress` is defined at nodes and interpolated by arc length within an edge. Branch-parallel edges share `p_lo`/`p_hi`.

---

## 7. Live projector

Replaces `makeLiveCompletion`. Lives in `pi/src/presence/` (with the graph-matching core in a pure module). Same signature/semantics as today (returns `completion` `0..1` or `null`, per-player state, resets on a new run).

### 7.1 Per-player state
`{ edge, u, progress, lap, t }` — current edge id, position-along-edge `u ∈ [0,1]`, last within-lap progress, last HUD lap, last update time.

### 7.2 Per-frame algorithm
Inputs: live `(cx, cy)` in the player's frame, HUD `lap`, `track_state`, `t`, `player_id`, course.
1. **Stale gate** — `track_state` not in `{tracking, ring_only}` → hold last progress; return it.
2. **Align** — apply the player's `player_alignment` transform → position in the common frame.
3. **Run reset** — HUD `lap` decreased or course changed → clear state (bootstrap).
4. **Candidate edges** — edges whose progress range is reachable forward of `state.progress` (≥ `progress − EPS_BACK`, ≤ `progress + reach`, where `reach` scales with pixels moved) and whose nearest point is within `MATCH_DIST` px.
5. **Pick** the lowest-cost candidate by point distance + heading agreement with the edge tangent.
6. **Progress** — `u` from the nearest point → `progress = p_lo + u·(p_hi − p_lo)`; monotonic-clamp within the lap (`max(progress, state.progress − EPS_BACK)`).
7. **Completion** — `(lap − 1 + progress) / total_laps`, clamped to `[0,1]`.
8. **Bootstrap** (fresh/dropout/lost) — global nearest edge consistent with the current lap's plausible progress, using heading to disambiguate; then continue.

### 7.3 Why the prior bugs cannot recur
- **Self-crossing snap:** the earlier and later strands are *different edges with different progress*. The forward window only considers progress ≥ current, so the earlier edge is unreachable.
- **Seam aliasing:** within-lap `u` wraps `0→1`, but the multiplier is the **HUD lap**, so `%` steps continuously across the line (e.g. 66% → 67%). No geometric lap ambiguity exists.
- **Branch:** both branch edges share the progress interval, so picking the "wrong" route still yields the correct `%`.

### 7.4 Knobs (4, down from ~7)
`MATCH_DIST` (reject/hold if nearest edge too far), `EPS_BACK` (backward noise tolerance), forward-`reach` factor (displacement-scaled), and the stale-hold `track_state` set. All govern matching *tolerance*, not structural ambiguity. All are config keys.

---

## 8. Recompute cadence

**Event-driven + windowed.** When a finished run is ingested for a course, mark that `(course_id, cc)` model **stale**; a debounced (~30 s) async task in the pi server rebuilds it from the last `W` runs and writes `course_models`, after which the hub drops its in-memory cache for that course. Builds are seconds. A CLI `build-course-model --course <slug> [--cc 150]` supports backfill and dev. Config: `PROGRESS_MODEL_WINDOW=40`, `PROGRESS_MODEL_DEBOUNCE_MS=30000`.

---

## 9. Consumers & retirement

- **Live hub** — `makeLiveCompletion` → the graph projector reading `course_models` + `player_alignment`.
- **`avg_completion_before_reset` stat** (`resolveCompletion`) — replays each reset's trail through the **same** graph projector against the course model (consistent with live numbers).
- **Retire** the single-run reference projector: `prepareReference`/`step`/`clipTeleports` and `courseReference`'s single-run path. (The 2026-06-09 `clipTeleports` fix becomes moot.)

The frontend card is unchanged. The temporary debug `%` readout on `PlayerCard.svelte` stays until the new projector is validated live, then is removed.

---

## 10. Engine recording change

`mkw_tracker/minimap/recorder.py` stamps each recorded point with the current HUD lap (from the race lap tracker) → `run_points.lap`. The app/server ingest (`pi/src/db/ingest.ts`, `AttemptPayload.points`) carries the lap per point. Minor, additive; old data still works via the time-derived fallback.

---

## 11. Config keys (new)
`PROGRESS_MODEL_WINDOW=40`, `PROGRESS_MODEL_MIN_RUNS=1`, `PROGRESS_MODEL_DEBOUNCE_MS=30000`, `PROGRESS_DENSITY_GRID_PX` (raster resolution), `PROGRESS_MATCH_DIST_PX`, `PROGRESS_EPS_BACK`, `PROGRESS_REACH_K`. Defaults live with the other config defaults; documented in `docs/config-reference.md`.

---

## 12. Testing strategy

- **Builder (synthetic clouds):** clean loop, figure-8 (crossing → pass-through pair, not a branch), and a branch that diverges & rejoins → assert extracted topology, monotonic node progress, correct branch vs crossing tags, and graceful centerline fallback on degenerate input.
- **Alignment:** a drifted run → recovered transform within tolerance; merged density stays sharp.
- **Projector (synthetic model + position streams):** no snap-back through a crossing; branch-agnostic % (both routes equal); seam continuity across a HUD lap increment; monotonic clamp; stale-hold; run reset.
- **Real-data probe:** rebuild `bowsers_castle` from `pi/mkw.db` and assert sane progress along a known run (no `[1,1,1]`-style degeneracy; smooth 0→1).
- **Integration:** ingest finished run → debounced rebuild → live projector reads new model.
- Existing pi/frontend suites stay green; the card interface is unchanged.

---

## 13. Rollout / migration

1. Schema migration: `run_points.lap`, `course_models`, `player_alignment`.
2. Engine: stamp `lap` on recorded points.
3. Builder module + CLI; backfill models for existing courses from history.
4. Swap live hub + reset stat to the graph projector; retire the old projector code.
5. Validate live (temp debug %), then remove the debug readout.

All local + unpushed, consistent with current workflow.

---

## 14. Risks & open questions

- **Skeleton/junction quality** is the main risk — heavy synthetic + real-data test coverage; the centerline fallback bounds the downside.
- **Alignment** under more-than-minor drift could smear the density; mitigated by `f`-correspondence fitting and per-player transforms. If drift turns out larger than assumed, alignment can escalate to a full rigid/affine ICP later.
- **Branch detection** needs ≥2 runs that take *different* routes; until then a branch reads as a single edge (still correct %, just no parallel route modelled).
- **Time≠progress** exactly (pace varies); aggregated medians are expected to give a clean monotonic ordering, but a pathological course could need arc-length re-parameterisation as a refinement.
- **Skeletonisation in Node** is the largest implementation unknown — no standard library, so the plan hand-rolls Zhang–Suen thinning (testable in isolation) or vets a small dependency. Keeping the builder in TypeScript avoids a Python/opencv dependency on the server host; the alternative (a Python+opencv builder reusing `mkw_tracker`'s CV stack, run as a job that writes `course_models`) trades deployment simplicity for trivial CV, and is the fallback if hand-rolled thinning proves troublesome.

# Race-Progress Projector — Design

**Date:** 2026-06-09
**Status:** Approved for planning.
**Supersedes the projection used by:** `2026-06-08-track-progress-reconstruction-design.md` (Increment #3). That design's reference-path idea stands; its **memoryless nearest-vertex projection** is replaced by the stateful projector below.

## 1. Problem

Completion is a 2D→1D map: project a player's minimap `(x, y)` onto a route fraction `s ∈ [0,1]`. The current projection (`pi/src/stats/completion.ts:completionFraction`) is **memoryless** (each frame is an independent global nearest-vertex search) and only **lower-bounded** (`lowerS` from the lap counter). On real courses this snaps badly:

- **Lap-to-lap:** identical-lap courses retrace the same pixels N×, so a live point sits near-equidistant from each lap's copy. With no upper bound, jitter flips `s` between this lap's copy and a later lap's copy.
- **Within-lap (self-crossing courses):** the two branches of a crossover share an `(x,y)` but have very different `s`; nearest-vertex grabs whichever branch is *momentarily* closest, not the one being driven.

A 2D→1D projection is genuinely multivalued without a z-axis. It is only *ambiguous* if you discard two things we already have: (a) the racer moves **continuously** along the route, and (b) we know the **lap number** from the HUD. Respecting both removes nearly all of the ambiguity.

**Required quality (confirmed):** the value must be **accurate** (trustworthy as a number / dot position) *and* **smooth/monotonic** (no backward snapping). This rules out a coarse sector model; we keep the dense reference trail and upgrade the projection.

## 2. Scope

**In:**
- One reusable, pure projector module (`pi/src/stats/progress.ts`): reference preparation + a stateful `step()`.
- Live wiring: `pi/src/presence/completion.ts` + `hub.ts` become per-player stateful.
- Reset-stat wiring: `resolveCompletion` replays each reset's full trail through the projector.
- A `track_state`/`stale` field added to the presence frame (client + server).

**Out:** rendering changes on the card (it already consumes `completion`), per-run exposure of completion beyond the reset average, multi-hypothesis/particle filtering (YAGNI for a single TT car on a known route — revisit only if window+heading proves insufficient), schema changes (uses existing `run_points` + `run_laps`).

## 3. Data contract (how position arrives)

Grounding facts that shape the filter:

- The engine emits `minimap_update` **only while locked** (`mkw_tracker/ipc/protocol.py:248` — returns `None` when `not state.tracking` or `cx/cy is None`). On freeze/suspend it emits nothing, so the frontend `minimap` store **holds its last value**.
- `presence.js:frame()` (`src/lib/presence.js:13`) sends `pos = mm ? [mm.cx, mm.cy] : null` — i.e. a **stale** position during a dropout, `null` only once the store is cleared (screen change / race end).
- Frames are throttled to **~4 Hz** with a **5 s heartbeat** (`THROTTLE_MS=250`, `HEARTBEAT_MS=5000`). So steps are ~250 ms, and idle heartbeats repeat an identical pos. → the projector uses a **time-scaled** forward window, not a fixed per-frame step.
- The `minimap_update` payload already carries **`track_state`** (`live`/`freeze`/`suspend`) and `radius` (`protocol.py:257`), but `frame()` drops `track_state`. We forward it so the projector can hold/​re-bootstrap explicitly rather than inferring a dropout from a position jump.

## 4. Architecture

```
pi/src/stats/progress.ts        (NEW, pure)
  ├─ prepareReference(points, lapCumMs) -> Reference   // clean + resample + bounds
  └─ step(state, ref, obs) -> { state, s }             // the stateful filter

pi/src/stats/completion.ts      (DB wiring only)
  ├─ courseReference(...)  -> calls prepareReference, cached per course
  └─ resolveCompletion(...) -> replays each reset's trail through step()

pi/src/presence/completion.ts   (live, now stateful)
  └─ makeLiveCompletion(db) -> closure holding Map<playerId, ProjState>

pi/src/presence/hub.ts          (passes playerId + t; resets state per run)
src/lib/presence.js             (forwards track_state in the frame)
```

`buildReference` / `lapBoundaries` move into `progress.ts`. `completionFraction` (memoryless nearest-vertex) is **retired**.

## 5. Reference preparation

`prepareReference(points: {cx,cy,t_ms}[], lapCumMs: number[]) -> Reference`, pure. Built once per course and cached exactly where `courseReference` caches today.

1. **Dedup** consecutive near-duplicate points (sub-pixel steps add nothing).
2. **Clip teleports:** **drop** the endpoint of any segment whose length ≫ the median segment length (`TELEPORT_CLIP_FACTOR`), so the skipped glitch never contributes arc length. These are the *reference run's own* minimap glitches; left in, they poison every downstream projection.
3. **Resample to ~uniform arc-length** spacing (`RESAMPLE_SPACING`). Uniform short segments make the windowed nearest-point-on-segment cheap and well-behaved.
4. **Normalize** cumulative arc length → `s ∈ [0,1]`.
5. **Lap bounds** `S_k` via the existing time-nearest method on `lapCumMs`. `bounds[k]` = route fraction at the **end of lap (k+1)** (0-indexed); `bounds.length` = lap count; `bounds[last] ≈ 1`.

```ts
interface RefPt { cx: number; cy: number; s: number; t: number; }
interface Reference { ref: RefPt[]; bounds: number[]; totalLen: number; }  // totalLen = route arc length in px
```

## 6. The projector — `step(state, ref, obs) -> { state, s }`

Pure and deterministic. All tuning via the constants in §7.

```ts
type ProjState = { s: number; t: number; x: number; y: number } | null;
interface Obs { x: number; y: number; lap: number; t: number; stale: boolean; }
```

**Lap window** from `obs.lap` (1-based), against `ref.bounds`:
- `loS = lap >= 2 ? bounds[lap - 2] : 0`   (= S_{lap-1})
- `hiS = (lap - 1) < bounds.length ? bounds[lap - 1] : 1`   (= S_{lap}, clamped to 1 on overshoot)

This window alone kills lap-to-lap snapping.

**Stale / freeze** (`obs.stale === true`): return `state.s` unchanged — hold, do not advance, do not snap. (If `state` is null, return `null`.)

**Tracking** — `state` present, fresh (`obs.t - state.t <= DROPOUT_MS`), and the window search succeeds:
1. **Displacement-scaled reach:** `reach = max(EPS_FWD_MIN, K_REACH * |obs − state.pos|px / totalLen)` — the window scales with how far the dot moved this frame (no pace state; an idle heartbeat repeat ⇒ ~0 reach ⇒ `s` holds).
2. Search nearest-point-**on-segment** over `ref` restricted to `s ∈ [max(loS, state.s - EPS_BACK), min(hiS, state.s + reach)]`.
3. If the best distance > `MAX_JUMP_DIST`, the local window is wrong → fall through to **bootstrap**.
4. **Monotonic clamp:** `s_new = max(candidate_s, state.s - EPS_BACK)`, then clamp to `[loS, hiS]`.

**Bootstrap** — `state` null, or `Δt > DROPOUT_MS`, or tracking fell through:
1. Search nearest-point-on-segment over the **whole lap window** `[loS, hiS]`.
2. If a second candidate's distance is within `BOOTSTRAP_TIE_TOL ×` the best **and** we have a heading (`state` exists → vector `state→obs`), pick the candidate whose local tangent has the larger dot-product with the heading. (No heading on the very first frame → take nearest; the first frame is almost always at the start line, where both branches' `s ≈ loS` anyway.)

**Output:** `s` is the completion fraction directly (already 0..1 over the full route). New `state = { s, t: obs.t, x: obs.x, y: obs.y }`.

## 7. Config knobs (module constants; defaults are starting points, tune live)

| Const | Meaning | Default |
|------|---------|---------|
| `RESAMPLE_SPACING` | target arc-length between reference vertices (px) | ~5 px |
| `TELEPORT_CLIP_FACTOR` | segment length > factor × median ⇒ clip | 8× |
| `EPS_BACK` | backward tolerance in `s` (noise) | ~0.004 |
| `K_REACH` | forward reach multiplier over `pixelsMoved/totalLen` | 2.5 |
| `EPS_FWD_MIN` | minimum forward reach in `s` | 0.01 |
| `DROPOUT_MS` | `Δt` above which we re-bootstrap | 1500 |
| `MAX_JUMP_DIST` | tracking best-distance over which we re-bootstrap (px) | tuned to minimap scale |
| `BOOTSTRAP_TIE_TOL` | distance ratio that counts as a tie ⇒ heading breaks it | 1.25× |

## 8. Live wiring

- `makeLiveCompletion(db)` keeps a `Map<playerId, ProjState>` and the per-course reference cache. Signature becomes `(playerId, course, curLap, pos, t, stale) -> number | null`.
- `hub.update(playerId, frame)` already runs `this.now()` and has `playerId`; it passes both plus `frame.track_state` (mapped to `stale = track_state !== 'live'`).
- **State reset** for a player when a new run starts: `course` changed, or `cur_lap` decreased, or the player went offline (`setOffline`). Clears the `Map` entry so the next frame bootstraps.
- `null` handling: `pos == null` (store cleared) → return `null` *and* clear state. `stale` (frozen) → hold last `s`. Net card effect: the bar advances smoothly and **no longer collapses to 0 on a tracking blip**.
- `src/lib/presence.js:frame()` adds `track_state: mm ? mm.track_state : null`. `PresenceFrame` (server) gains `track_state?: string | null`.

## 9. Reset-stat wiring

`resolveCompletion` (`completion.ts`): for each in-scope reset, instead of snapping the single last point:
1. Load **all** `run_points` ordered by `t_ms`.
2. Derive each point's `lap` from the reset's own `run_laps` cumulative times.
3. Replay through `step()` against the course reference; final `s` = the reset's completion.
4. Unevaluable cases unchanged (no points, or no reference for the course).

Continuity carries `s` correctly across each lap boundary, so the result is trajectory-consistent rather than a lone-point snap. **Historical `avg_completion_before_reset` values will shift** (more correct) — documented in §11.

## 10. Testing

**Pure (`pi/src/stats/progress.test.ts`):**
- `prepareReference`: dedup + teleport-clip + uniform resample on a synthetic noisy/glitchy trail; bounds correct (equal laps ≈ even; a skewed lap follows the *time*, not an even split).
- **Figure-8** reference: a trajectory around one loop then the other ⇒ `s` strictly forward, and at the crossing `s` does **not** jump to the far branch.
- **Identical-3-lap** reference: a lap-2 trajectory stays within `[S_1, S_2]`; never snaps to the lap-1 or lap-3 copy.
- **Heading bootstrap:** two equidistant candidates with opposite tangents ⇒ heading picks the forward one.
- **Dropout:** `Δt > DROPOUT_MS` re-bootstraps rather than force-continuing through an implausible jump.
- **Monotonic clamp:** a backward-noisy observation does not move `s` back beyond `EPS_BACK`.
- **Stale:** `stale=true` holds `s`.

**Hub (`pi/src/presence/*.test.ts`):** per-player state isolation; hold-on-stale; state reset on course change / lap decrease / offline.

**Reset stat (`pi/src/stats/completion.test.ts`):** a reference run + a reset that completed 1 lap and stopped mid-lap-2 ⇒ trajectory-consistent completion; existing single-point snap test updated; no-trail and no-reference still `unevaluable`.

## 11. Behavior deltas (sign-off)

1. Reference path is **cleaned + resampled** — slightly different vertices than the raw trail.
2. Card **holds** completion through a tracking blip instead of dropping to 0.
3. `PresenceFrame` / `presence.js:frame()` gain a `track_state` field (client + server types).
4. **`avg_completion_before_reset` historical numbers shift** (more correct) — it now replays the full trail instead of snapping the last point.

## 12. Caveats

- Needs at least one finished run **with a trail** per course for a reference (unchanged from Increment #3); pre-cutover history has no points.
- The window+heading filter assumes a **single car** with a **reliable lap counter** — true for the TT competition. A wrong `cur_lap` would mis-gate the window; continuity still bounds the error to nearby laps.
- A genuinely ambiguous crossover (two branches close **and** near-parallel) relies on continuity (which branch you came from); only a hard re-bootstrap *at* such a point could mis-lock, and re-bootstrap there is rare (bootstraps happen at the start line / after dropouts).

# Race-Progress Projector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the memoryless nearest-vertex completion projection with a per-player stateful filter (lap-window gating + local s-window + heading bootstrap), shared by the live presence hub and the `avg_completion_before_reset` stat, so race-progress stops snapping on self-crossing and identical-lap courses.

**Architecture:** A new pure module `pi/src/stats/progress.ts` owns reference preparation (`prepareReference`: clean + resample + lap bounds) and the stateful `step(state, ref, obs)` filter. `completion.ts` (reset stat) and `presence/completion.ts` (live) both feed an ordered `(pos, lap, t)` stream through `step()`. The forward search window scales with **pixels-moved-this-frame ÷ route-length** (no pace state) — a planning refinement over the spec's pace-EMA; the spec's §6/§7 are reconciled to match.

**Tech Stack:** TypeScript (Node 22 `node:sqlite`), vitest 4. Frontend: Svelte stores + vitest. Spec: `docs/superpowers/specs/2026-06-09-progress-projector-design.md`.

**Conventions:** All commits end with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer (omitted below for brevity). pi tests run from `pi/`; frontend tests from the repo root.

---

## File Structure

- **Create** `pi/src/stats/progress.ts` — pure: `RefPt`/`Reference`/`ProjState`/`Obs` types, `buildReference`, `lapBoundaries`, `prepareReference`, `nearestOnPath`, `step`, tuning constants.
- **Create** `pi/src/stats/progress.test.ts` — unit tests for all of the above.
- **Modify** `pi/src/stats/completion.ts` — `courseReference` → `prepareReference`; `resolveCompletion` → trail replay via `step`; remove `buildReference`/`lapBoundaries`/`completionFraction`/`RefPt`/`RefEntry`.
- **Modify** `pi/src/stats/completion.test.ts` — drop the moved primitive tests + the `completionFraction` test; keep/adjust `resolveCompletion`.
- **Modify** `pi/src/presence/completion.ts` — `makeLiveCompletion` becomes stateful (per-player `Map`), new optional params `(…, playerId, t, stale)`.
- **Modify** `pi/src/presence/completion.test.ts` — keep existing asserts (still pass); add stateful cases.
- **Modify** `pi/src/presence/hub.ts` — `PresenceFrame.track_state`; `update()` passes `playerId`/`now`/`stale`.
- **Modify** `pi/src/presence/hub.test.ts` — add a `stale`-propagation case.
- **Modify** `src/lib/presence.js` — forward `track_state: mm?.trackState`.
- **Modify** `src/lib/presence.test.js` — update the `frame()` expectation.

---

## Task 1: Extract reference primitives into `progress.ts`

Pure refactor — move `RefPt`, `buildReference`, `lapBoundaries` out of `completion.ts` into the new module, no behaviour change.

**Files:**
- Create: `pi/src/stats/progress.ts`
- Create: `pi/src/stats/progress.test.ts`
- Modify: `pi/src/stats/completion.ts:1-30` (remove the moved defs, import them)
- Modify: `pi/src/stats/completion.test.ts:1-27` (move the two describe blocks out)

- [ ] **Step 1: Create `progress.ts` with the moved primitives**

```ts
// pi/src/stats/progress.ts
export interface RefPt { cx: number; cy: number; s: number; t: number; }

const dist = (ax: number, ay: number, bx: number, by: number) => Math.hypot(ax - bx, ay - by);

/** Arc-length-normalised reference path from time-ordered trail points (s in [0,1]). */
export function buildReference(points: { cx: number; cy: number; t_ms: number }[]): RefPt[] {
  if (points.length === 0) return [];
  const out: RefPt[] = [{ cx: points[0].cx, cy: points[0].cy, s: 0, t: points[0].t_ms }];
  let acc = 0;
  for (let i = 1; i < points.length; i++) {
    acc += dist(points[i - 1].cx, points[i - 1].cy, points[i].cx, points[i].cy);
    out.push({ cx: points[i].cx, cy: points[i].cy, s: acc, t: points[i].t_ms });
  }
  const total = acc || 1;
  for (const p of out) p.s /= total;
  return out;
}

/** Route fraction at the end of each lap (S_k), from cumulative lap end-times. Length = laps. */
export function lapBoundaries(ref: RefPt[], cumulativeLapMs: number[]): number[] {
  return cumulativeLapMs.map((t) => {
    let best = Infinity, bestS = 0;
    for (const p of ref) { const d = Math.abs(p.t - t); if (d < best) { best = d; bestS = p.s; } }
    return bestS;
  });
}
```

- [ ] **Step 2: Move the two describe blocks into `progress.test.ts`**

```ts
// pi/src/stats/progress.test.ts
import { describe, it, expect } from 'vitest';
import { buildReference, lapBoundaries } from './progress';

const LOOP = [
  { cx: 0, cy: 0, t_ms: 0 }, { cx: 10, cy: 0, t_ms: 50 }, { cx: 0, cy: 0, t_ms: 100 },
  { cx: 10, cy: 0, t_ms: 150 }, { cx: 0, cy: 0, t_ms: 200 },
];

describe('buildReference', () => {
  it('normalises arc length to s in [0,1]', () => {
    const ref = buildReference([{ cx: 0, cy: 0, t_ms: 0 }, { cx: 5, cy: 0, t_ms: 50 }, { cx: 10, cy: 0, t_ms: 100 }]);
    expect(ref.map((p) => p.s)).toEqual([0, 0.5, 1]);
  });
});

describe('lapBoundaries', () => {
  it('places the lap-1 boundary at the route fraction matching its end-time', () => {
    expect(lapBoundaries(buildReference(LOOP), [100, 200])).toEqual([0.5, 1]);
  });
});
```

- [ ] **Step 3: Point `completion.ts` at the moved primitives**

In `pi/src/stats/completion.ts`, delete the `RefPt` interface, `dist`, `buildReference`, and `lapBoundaries` definitions (lines ~5-30), and add at the top (after the existing imports):

```ts
import { buildReference, lapBoundaries, type RefPt } from './progress';
```

(Leave `completionFraction`, `courseReference`, `resolveCompletion`, `RefEntry` as-is for now.)

- [ ] **Step 4: Trim the moved blocks out of `completion.test.ts`**

In `pi/src/stats/completion.test.ts`: change the import line to `import { completionFraction, resolveCompletion } from './completion';` and **delete** the `describe('buildReference'…)` and `describe('lapBoundaries'…)` blocks (they now live in `progress.test.ts`). Keep `describe('completionFraction'…)` and `describe('resolveCompletion'…)`. The `LOOP` const stays (still used).

- [ ] **Step 5: Run the pi suite — all green**

Run: `cd pi && npm test`
Expected: PASS — `progress.test.ts` (2 tests) + the unchanged `completion.test.ts`/`completion`/`presence` tests.

- [ ] **Step 6: Commit**

```bash
git add pi/src/stats/progress.ts pi/src/stats/progress.test.ts pi/src/stats/completion.ts pi/src/stats/completion.test.ts
git commit -m "refactor(progress): extract reference primitives into progress.ts"
```

---

## Task 2: `prepareReference` — clean + resample + route length

Add reference preparation: dedup sub-pixel points, clip teleport spikes, resample to ~uniform arc-length, and return lap bounds + total route length (px) for the displacement-scaled window.

**Files:**
- Modify: `pi/src/stats/progress.ts`
- Modify: `pi/src/stats/progress.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `pi/src/stats/progress.test.ts`:

```ts
import { prepareReference } from './progress';

describe('prepareReference', () => {
  it('drops a teleport spike and a sub-pixel dup, then resamples ~uniformly', () => {
    const pts = [
      { cx: 0, cy: 0, t_ms: 0 }, { cx: 10, cy: 0, t_ms: 10 }, { cx: 10, cy: 0, t_ms: 11 }, // dup
      { cx: 1000, cy: 1000, t_ms: 12 },                                                     // teleport
      { cx: 40, cy: 0, t_ms: 40 }, { cx: 100, cy: 0, t_ms: 100 },
    ];
    const ref = prepareReference(pts, [100]);
    expect(ref.ref.every((p) => p.cx >= 0 && p.cx <= 100 && Math.abs(p.cy) < 1e-6)).toBe(true); // spike gone
    expect(ref.totalLen).toBeGreaterThan(90);
    expect(ref.totalLen).toBeLessThan(110);                                                   // ~100, not ~2828
    const gaps = ref.ref.slice(1).map((p, i) => p.s - ref.ref[i].s);
    expect(Math.max(...gaps)).toBeLessThan(0.1);                                              // resampled ~5px/100
    expect(ref.bounds).toEqual([1]);
  });

  it('preserves a recurring position at two distinct fractions (LOOP)', () => {
    const ref = prepareReference(LOOP, [100, 200]);
    expect(ref.bounds).toEqual([0.5, 1]);
    const at10 = ref.ref.filter((p) => Math.abs(p.cx - 10) < 1e-6).map((p) => p.s);
    expect(at10.some((s) => Math.abs(s - 0.25) < 0.02)).toBe(true);
    expect(at10.some((s) => Math.abs(s - 0.75) < 0.02)).toBe(true);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/stats/progress.test.ts`
Expected: FAIL — `prepareReference` is not exported.

- [ ] **Step 3: Implement `prepareReference` + helpers**

Append to `pi/src/stats/progress.ts`:

```ts
export interface Reference { ref: RefPt[]; bounds: number[]; totalLen: number; }

const RESAMPLE_SPACING = 5;     // px between resampled reference vertices
const TELEPORT_CLIP_FACTOR = 8; // segment > factor x median => drop the endpoint

function dedup(p: { cx: number; cy: number; t_ms: number }[]) {
  if (p.length === 0) return p;
  const out = [p[0]];
  for (let i = 1; i < p.length; i++) if (dist(out[out.length - 1].cx, out[out.length - 1].cy, p[i].cx, p[i].cy) > 1e-6) out.push(p[i]);
  return out;
}

function clipTeleports(p: { cx: number; cy: number; t_ms: number }[]) {
  if (p.length < 3) return p;
  const seg: number[] = [];
  for (let i = 1; i < p.length; i++) seg.push(dist(p[i - 1].cx, p[i - 1].cy, p[i].cx, p[i].cy));
  const med = [...seg].sort((a, b) => a - b)[Math.floor(seg.length / 2)] || 0;
  if (med <= 0) return p;
  const out = [p[0]];
  for (let i = 1; i < p.length; i++) {
    const last = out[out.length - 1];
    if (dist(last.cx, last.cy, p[i].cx, p[i].cy) <= TELEPORT_CLIP_FACTOR * med) out.push(p[i]);
  }
  return out;
}

function resample(raw: RefPt[], spacingPx: number, totalLen: number): RefPt[] {
  if (raw.length < 2 || totalLen <= 0) return raw.slice();
  const stepS = spacingPx / totalLen;
  const out: RefPt[] = [raw[0]];
  let nextS = stepS;
  for (let i = 1; i < raw.length; i++) {
    const a = raw[i - 1], b = raw[i];
    while (nextS < b.s) {
      const f = b.s !== a.s ? (nextS - a.s) / (b.s - a.s) : 0;
      out.push({ cx: a.cx + f * (b.cx - a.cx), cy: a.cy + f * (b.cy - a.cy), s: nextS, t: a.t + f * (b.t - a.t) });
      nextS += stepS;
    }
  }
  out.push(raw[raw.length - 1]);
  return out;
}

/** Clean + arc-length-normalise + resample a trail; bounds computed on the raw (timed) path. */
export function prepareReference(points: { cx: number; cy: number; t_ms: number }[], lapCumMs: number[]): Reference {
  const cleaned = clipTeleports(dedup(points));
  const raw = buildReference(cleaned);
  if (raw.length === 0) return { ref: [], bounds: [], totalLen: 0 };
  let totalLen = 0;
  for (let i = 1; i < cleaned.length; i++) totalLen += dist(cleaned[i - 1].cx, cleaned[i - 1].cy, cleaned[i].cx, cleaned[i].cy);
  const bounds = lapBoundaries(raw, lapCumMs);
  const ref = totalLen > 0 ? resample(raw, RESAMPLE_SPACING, totalLen) : raw;
  return { ref, bounds, totalLen: totalLen || 1 };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/stats/progress.test.ts`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add pi/src/stats/progress.ts pi/src/stats/progress.test.ts
git commit -m "feat(progress): prepareReference with dedup, teleport-clip and resample"
```

---

## Task 3: `nearestOnPath` + `step()` bootstrap

`step()` with no prior state: window-gated nearest-point-**on-segment** over the current lap. This is the stateless projection that replaces `completionFraction`.

**Files:**
- Modify: `pi/src/stats/progress.ts`
- Modify: `pi/src/stats/progress.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `pi/src/stats/progress.test.ts`:

```ts
import { step, type ProjState } from './progress';

describe('step (bootstrap)', () => {
  it('lap-gates a recurring position to the current lap (3 identical laps)', () => {
    // 3 laps of (0,0)->(10,0)->(0,0): lap ends at t=2,4,6
    const THREE = [
      { cx: 0, cy: 0, t_ms: 0 }, { cx: 10, cy: 0, t_ms: 1 }, { cx: 0, cy: 0, t_ms: 2 },
      { cx: 10, cy: 0, t_ms: 3 }, { cx: 0, cy: 0, t_ms: 4 },
      { cx: 10, cy: 0, t_ms: 5 }, { cx: 0, cy: 0, t_ms: 6 },
    ];
    const ref = prepareReference(THREE, [2, 4, 6]);
    const onLap2 = step(null, ref, { x: 10, y: 0, lap: 2, t: 0, stale: false });
    expect(onLap2.s).toBeCloseTo(0.5, 2);   // lap-2 copy of (10,0), not 0.167 or 0.833
  });

  it('returns null s for an empty reference', () => {
    expect(step(null, { ref: [], bounds: [], totalLen: 0 }, { x: 0, y: 0, lap: 1, t: 0, stale: false }).s).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/stats/progress.test.ts`
Expected: FAIL — `step` is not exported.

- [ ] **Step 3: Implement `nearestOnPath` + `step` (bootstrap path only)**

Append to `pi/src/stats/progress.ts`:

```ts
export type ProjState = { s: number; t: number; x: number; y: number } | null;
export interface Obs { x: number; y: number; lap: number; t: number; stale: boolean; }

const HEADING_BONUS = 6;        // px-equivalent bonus for a heading-aligned branch at bootstrap

/** Nearest point on the polyline, restricted to s in [loS,hiS] (projections clamped into-window).
 *  When useH, a heading-aligned segment tangent discounts the cost (bootstrap tie-break). */
function nearestOnPath(ref: RefPt[], loS: number, hiS: number, px: number, py: number, hx = 0, hy = 0, useH = false) {
  let best = Infinity, bestS = loS;
  for (let i = 1; i < ref.length; i++) {
    const a = ref[i - 1], b = ref[i];
    if (Math.max(a.s, b.s) < loS || Math.min(a.s, b.s) > hiS) continue;
    const dx = b.cx - a.cx, dy = b.cy - a.cy, L2 = dx * dx + dy * dy;
    let t = L2 > 0 ? ((px - a.cx) * dx + (py - a.cy) * dy) / L2 : 0;
    t = Math.max(0, Math.min(1, t));
    let s = a.s + t * (b.s - a.s);
    const sC = Math.max(loS, Math.min(hiS, s));
    if (sC !== s) { t = b.s !== a.s ? Math.max(0, Math.min(1, (sC - a.s) / (b.s - a.s))) : 0; s = sC; }
    const x = a.cx + t * dx, y = a.cy + t * dy;
    let cost = dist(px, py, x, y);
    if (useH) { const tl = Math.hypot(dx, dy) || 1; cost -= HEADING_BONUS * Math.max(0, (dx / tl) * hx + (dy / tl) * hy); }
    if (cost < best) { best = cost; bestS = s; }
  }
  return { s: bestS, dist: best };
}

/** Project one observation onto the route, carrying per-player state. */
export function step(state: ProjState, ref: Reference, obs: Obs): { state: ProjState; s: number | null } {
  if (ref.ref.length === 0) return { state, s: state ? state.s : null };
  const b = ref.bounds;
  const loS = obs.lap >= 2 ? (b[obs.lap - 2] ?? 0) : 0;
  const hiS = (obs.lap - 1) < b.length ? b[obs.lap - 1] : 1;

  // bootstrap: no heading on a truly fresh state
  const r = nearestOnPath(ref.ref, loS, hiS, obs.x, obs.y);
  const s = Math.min(hiS, Math.max(loS, r.s));
  return { state: { s, t: obs.t, x: obs.x, y: obs.y }, s };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/stats/progress.test.ts`
Expected: PASS — 6 tests.

- [ ] **Step 5: Commit**

```bash
git add pi/src/stats/progress.ts pi/src/stats/progress.test.ts
git commit -m "feat(progress): step() bootstrap — lap-gated nearest-point-on-segment"
```

---

## Task 4: `step()` tracking — displacement window + monotonic clamp

Add the stateful tracking branch: a forward window scaled by pixels-moved-this-frame, a small backward tolerance, and a fall-through to bootstrap when the best match is implausibly far.

**Files:**
- Modify: `pi/src/stats/progress.ts`
- Modify: `pi/src/stats/progress.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `pi/src/stats/progress.test.ts`:

```ts
// Self-crossing "X": (0,0)->(20,20)->(20,0)->(0,20); diagonals cross at (10,10) (s~0.185 & ~0.815)
const XPATH = [
  { cx: 0, cy: 0, t_ms: 0 }, { cx: 20, cy: 20, t_ms: 100 }, { cx: 20, cy: 0, t_ms: 200 }, { cx: 0, cy: 20, t_ms: 300 },
];

describe('step (tracking)', () => {
  it('stays on the entered branch through a self-crossing', () => {
    const ref = prepareReference(XPATH, [300]);
    let st: ProjState = null;
    const run = (x: number, y: number, t: number) => { const r = step(st, ref, { x, y, lap: 1, t, stale: false }); st = r.state; return r.s!; };
    run(0, 0, 0);
    run(5, 5, 100);
    const atCrossing1 = run(10, 10, 200);
    expect(atCrossing1).toBeLessThan(0.4);     // branch 1, not the s~0.815 branch
    run(20, 0, 300);
    const atCrossing2 = run(10, 10, 400);
    expect(atCrossing2).toBeGreaterThan(0.7);  // now legitimately on branch 3
  });

  it('clamps a small backward-noisy observation (no reversal beyond EPS_BACK)', () => {
    const LINE = [{ cx: 0, cy: 0, t_ms: 0 }, { cx: 100, cy: 0, t_ms: 100 }];
    const ref = prepareReference(LINE, [100]);
    const r = step({ s: 0.5, t: 0, x: 50, y: 0 }, ref, { x: 48, y: 0, lap: 1, t: 50, stale: false });
    expect(r.s).toBeCloseTo(0.496, 3);         // clamped to 0.5 - EPS_BACK, not 0.48
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/stats/progress.test.ts`
Expected: FAIL — `step` currently always bootstraps; the backward-clamp test returns ~0.48, and `atCrossing2` re-bootstraps to ~0.185.

- [ ] **Step 3: Add the tracking branch to `step`**

In `pi/src/stats/progress.ts`, add the constants near `HEADING_BONUS`:

```ts
const EPS_BACK = 0.004;      // backward tolerance in s (noise)
const K_REACH = 2.5;         // forward window = K * pixelsMoved / totalLen
const EPS_FWD_MIN = 0.01;    // minimum forward reach in s
const DROPOUT_MS = 1500;     // dt above this => re-bootstrap
const MAX_JUMP_DIST = 60;    // tracking best-distance over this (px) => re-bootstrap
```

Replace the body of `step` after the `loS`/`hiS` lines (keep those) with:

```ts
  if (obs.stale) return { state, s: state ? state.s : null };

  if (state && (obs.t - state.t) <= DROPOUT_MS) {
    const move = dist(state.x, state.y, obs.x, obs.y);
    const reach = Math.max(EPS_FWD_MIN, K_REACH * move / ref.totalLen);
    const lo = Math.max(loS, state.s - EPS_BACK);
    const hi = Math.min(hiS, state.s + reach);
    const r = nearestOnPath(ref.ref, lo, hi, obs.x, obs.y);
    if (r.dist <= MAX_JUMP_DIST) {
      const s = Math.min(hiS, Math.max(loS, Math.max(r.s, state.s - EPS_BACK)));
      return { state: { s, t: obs.t, x: obs.x, y: obs.y }, s };
    }
  }

  // bootstrap (fresh state, dropout, or implausible tracking match)
  let hx = 0, hy = 0, useH = false;
  if (state) { const mx = obs.x - state.x, my = obs.y - state.y, ml = Math.hypot(mx, my); if (ml > 1e-6) { hx = mx / ml; hy = my / ml; useH = true; } }
  const r = nearestOnPath(ref.ref, loS, hiS, obs.x, obs.y, hx, hy, useH);
  const s = Math.min(hiS, Math.max(loS, r.s));
  return { state: { s, t: obs.t, x: obs.x, y: obs.y }, s };
```

(Remove the old bootstrap-only `const r = nearestOnPath(...)`/`const s = ...`/`return …` lines that were the entire previous body — the block above is the complete replacement.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/stats/progress.test.ts`
Expected: PASS — 8 tests (the bootstrap test still passes: a `null` state skips the tracking branch).

- [ ] **Step 5: Commit**

```bash
git add pi/src/stats/progress.ts pi/src/stats/progress.test.ts
git commit -m "feat(progress): step() tracking — displacement window + monotonic clamp"
```

---

## Task 5: `step()` heading bootstrap, dropout, and stale-hold

Cover the remaining branches with tests: heading disambiguates a crossing at bootstrap, a large `dt` re-bootstraps (allowing a legitimate backward correction), and `stale` holds.

**Files:**
- Modify: `pi/src/stats/progress.test.ts` (logic already implemented in Task 4)

- [ ] **Step 1: Write the failing tests**

Append to `pi/src/stats/progress.test.ts`:

```ts
describe('step (bootstrap edges)', () => {
  it('uses heading to pick the right branch at a crossing', () => {
    const ref = prepareReference(XPATH, [300]);
    // dt > DROPOUT_MS forces bootstrap; state supplies only the heading
    const upRight = step({ s: 0.18, t: 0, x: 5, y: 5 }, ref, { x: 10, y: 10, lap: 1, t: 5000, stale: false });
    expect(upRight.s).toBeLessThan(0.4);       // heading (1,1) -> branch 1
    const upLeft = step({ s: 0.8, t: 0, x: 15, y: 5 }, ref, { x: 10, y: 10, lap: 1, t: 5000, stale: false });
    expect(upLeft.s).toBeGreaterThan(0.7);     // heading (-1,1) -> branch 3
  });

  it('re-bootstraps after a dropout (a backward correction is allowed)', () => {
    const LINE = [{ cx: 0, cy: 0, t_ms: 0 }, { cx: 100, cy: 0, t_ms: 100 }];
    const ref = prepareReference(LINE, [100]);
    const dropout = step({ s: 0.2, t: 0, x: 20, y: 0 }, ref, { x: 10, y: 0, lap: 1, t: 5000, stale: false });
    expect(dropout.s).toBeCloseTo(0.1, 2);     // free re-bootstrap, not clamped to ~0.196
    const tracked = step({ s: 0.2, t: 0, x: 20, y: 0 }, ref, { x: 10, y: 0, lap: 1, t: 100, stale: false });
    expect(tracked.s).toBeGreaterThanOrEqual(0.196); // in-window tracking clamps the reversal
  });

  it('holds s while the fix is stale', () => {
    const ref = prepareReference([{ cx: 0, cy: 0, t_ms: 0 }, { cx: 100, cy: 0, t_ms: 100 }], [100]);
    const r = step({ s: 0.42, t: 0, x: 5, y: 5 }, ref, { x: 99, y: 0, lap: 1, t: 100, stale: true });
    expect(r.s).toBe(0.42);
  });
});
```

- [ ] **Step 2: Run to verify it passes immediately**

Run: `cd pi && npx vitest run src/stats/progress.test.ts`
Expected: PASS — 11 tests (these exercise branches already written in Task 4; if any fails, the bug is in Task 4's `step`, fix it there).

- [ ] **Step 3: Commit**

```bash
git add pi/src/stats/progress.test.ts
git commit -m "test(progress): cover heading bootstrap, dropout and stale-hold"
```

---

## Task 6: Reset stat replays the full trail through `step`

`courseReference` builds a prepared `Reference`; `resolveCompletion` replays each reset's whole trail instead of snapping its last point. `completionFraction` is left defined (removed in Task 8).

**Files:**
- Modify: `pi/src/stats/completion.ts`
- Modify: `pi/src/stats/completion.test.ts`

- [ ] **Step 1: Update the `resolveCompletion` test for replay**

In `pi/src/stats/completion.test.ts`, the existing "estimates a reset that stopped mid lap-2" test already feeds two points `(5,0)@140` then `(10,0)@150` and expects `0.75` — replay preserves that. Add a self-crossing assertion after it inside the same `describe('resolveCompletion', …)`:

```ts
  it('replays a self-crossing reset without snapping to the far branch', () => {
    const d = base();
    // reference X: crossing at (10,10); single lap
    const X = [{ cx: 0, cy: 0, t_ms: 0 }, { cx: 20, cy: 20, t_ms: 100 }, { cx: 20, cy: 0, t_ms: 200 }, { cx: 0, cy: 20, t_ms: 300 }];
    addRun(d, 1, 'finished'); addPoints(d, 1, X); addLaps(d, 1, [300]);
    addRun(d, 2, 'reset'); addLaps(d, 2, []);                       // 0 completed laps
    addPoints(d, 2, [{ cx: 0, cy: 0, t_ms: 0 }, { cx: 5, cy: 5, t_ms: 50 }, { cx: 10, cy: 10, t_ms: 100 }]); // along branch 1
    const r = resolveCompletion(d, { metric: 'avg_completion_before_reset', period: allTime(), filters: { course: 'bc' }, seasonId: 1 });
    expect(r.total).toBeLessThan(0.4);                              // branch 1 (~0.185), not ~0.815
  });
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/stats/completion.test.ts`
Expected: FAIL — the current last-point snap of `(10,10)` picks the globally nearest vertex (can be the far branch); `r.total` is not reliably `< 0.4`.

- [ ] **Step 3: Rewrite `courseReference` + `resolveCompletion`**

In `pi/src/stats/completion.ts`:

Update the import added in Task 1 to also bring in the projector:

```ts
import { buildReference, lapBoundaries, prepareReference, step, type RefPt, type Reference, type ProjState } from './progress';
```

Replace `RefEntry` + `courseReference`:

```ts
/** Per-course completion reference: the densest finished run's prepared trail + lap bounds. */
export function courseReference(db: DatabaseSync, seasonId: number, courseId: number, cc: number): Reference | null {
  const refRun = db.prepare(
    `SELECT r.id, COUNT(p.run_id) AS n FROM runs r JOIN run_points p ON p.run_id=r.id
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.status='finished'
     GROUP BY r.id ORDER BY n DESC LIMIT 1`).get(seasonId, courseId, cc) as { id: number; n: number } | undefined;
  if (!refRun) return null;
  const pts = db.prepare('SELECT cx, cy, t_ms FROM run_points WHERE run_id=? ORDER BY t_ms').all(refRun.id) as { cx: number; cy: number; t_ms: number }[];
  const laps = db.prepare('SELECT lap_time_ms FROM run_laps WHERE run_id=? ORDER BY lap_index').all(refRun.id) as { lap_time_ms: number }[];
  let cum = 0; const cumMs = laps.map((l) => (cum += l.lap_time_ms));
  return prepareReference(pts, cumMs);
}
```

In `resolveCompletion`, change the per-course cache type and the per-reset loop. Replace the `refCache`/`getRef` declarations' `RefEntry` with `Reference`, replace the `lastPt`/`lapCount` statements, and replace the `for (const reset of resets)` body:

```ts
  const refCache = new Map<number, Reference | null>();
  const getRef = (courseIdv: number): Reference | null => {
    if (refCache.has(courseIdv)) return refCache.get(courseIdv)!;
    const entry = courseReference(db, q.seasonId, courseIdv, cc);
    refCache.set(courseIdv, entry);
    return entry;
  };

  const ptsStmt = db.prepare('SELECT cx, cy, t_ms FROM run_points WHERE run_id=? ORDER BY t_ms');
  const lapsStmt = db.prepare('SELECT lap_time_ms FROM run_laps WHERE run_id=? ORDER BY lap_index');

  const byKey = new Map<string, { sum: number; n: number }>();
  let overallSum = 0, overallN = 0, unevaluable = 0;
  const keyOf = (r: { player_id: number; course_id: number }) =>
    q.groupBy === 'player' ? nameOf(db, 'players', r.player_id)
      : q.groupBy === 'course' ? nameOf(db, 'courses', r.course_id)
        : q.metric;

  for (const reset of resets) {
    const entry = getRef(reset.course_id);
    const pts = ptsStmt.all(reset.id) as { cx: number; cy: number; t_ms: number }[];
    if (!entry || entry.ref.length === 0 || pts.length === 0) { unevaluable++; continue; }
    const laps = lapsStmt.all(reset.id) as { lap_time_ms: number }[];
    let c = 0; const cum = laps.map((l) => (c += l.lap_time_ms));
    const lapOf = (t: number) => { let L = 1; for (const bnd of cum) { if (t >= bnd) L++; else break; } return L; };
    let st: ProjState = null, frac = 0;
    for (const p of pts) { const r = step(st, entry, { x: p.cx, y: p.cy, lap: lapOf(p.t_ms), t: p.t_ms, stale: false }); st = r.state; if (r.s != null) frac = r.s; }
    overallSum += frac; overallN += 1;
    const k = keyOf(reset);
    const cur = byKey.get(k) ?? { sum: 0, n: 0 };
    cur.sum += frac; cur.n += 1; byKey.set(k, cur);
  }
```

(Leave `completionFraction` defined for now — Task 8 removes it. Delete the now-unused `RefEntry` interface.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/stats/completion.test.ts`
Expected: PASS — the `0.75` test still holds (replay), the new self-crossing test passes, and the two `unevaluable` tests are unchanged.

- [ ] **Step 5: Commit**

```bash
git add pi/src/stats/completion.ts pi/src/stats/completion.test.ts
git commit -m "feat(completion): reset stat replays the full trail through the projector"
```

---

## Task 7: Live hub — stateful per-player projection

`makeLiveCompletion` keeps per-player projector state and resets it on a new run; the hub passes `playerId`/`now`/`stale` and forwards `track_state`.

**Files:**
- Modify: `pi/src/presence/completion.ts`
- Modify: `pi/src/presence/completion.test.ts`
- Modify: `pi/src/presence/hub.ts`
- Modify: `pi/src/presence/hub.test.ts`

- [ ] **Step 1: Write the failing tests**

Append a stateful case to `pi/src/presence/completion.test.ts`:

```ts
  it('keeps per-player state and resets it on a new run', () => {
    const live = makeLiveCompletion(db());
    // Player 1 advances on lap 2; a self-recurring (10,0) tracks forward, never snapping back to lap 1
    expect(live('Bowsers Castle', 2, [10, 0], 1, 1000, false)).toBeCloseTo(0.75, 2);
    expect(live('Bowsers Castle', 2, [0, 0], 1, 1100, false)).toBeGreaterThanOrEqual(0.75 - 0.01); // (0,0)=lap2 end ~1.0, forward
    // A new run for player 1 (lap drops to 1) resets state -> back near the start
    expect(live('Bowsers Castle', 1, [0, 0], 1, 5000, false)).toBeLessThan(0.2);
  });

  it('holds completion while the fix is stale', () => {
    const live = makeLiveCompletion(db());
    live('Bowsers Castle', 2, [10, 0], 1, 1000, false);              // s ~ 0.75
    expect(live('Bowsers Castle', 2, [0, 0], 1, 1100, true)).toBeCloseTo(0.75, 2); // stale -> hold
  });
```

Add a `stale`-propagation case to `pi/src/presence/hub.test.ts`:

```ts
  it('maps a non-fresh track_state to a held (stale) completion', () => {
    const seen: boolean[] = [];
    const hub = new PresenceHub(db(), (_c, _l, _p, _pid, _t, stale) => { seen.push(!!stale); return stale ? 0.9 : 0.1; }, () => 3000);
    hub.addSink(() => {});
    hub.update(1, { screen: 'RACING', course: 'bc', cur_lap: 2, pos: [1, 2], track_state: 'reacquire' });
    expect(seen).toEqual([true]);
  });
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd pi && npx vitest run src/presence/`
Expected: FAIL — `makeLiveCompletion` ignores `playerId`/`stale`; `PresenceFrame` has no `track_state`; the hub passes only 3 args.

- [ ] **Step 3: Rewrite `makeLiveCompletion`**

Replace the body of `pi/src/presence/completion.ts`:

```ts
import type { DatabaseSync } from 'node:sqlite';
import { courseReference } from '../stats/completion';
import { step, type Reference, type ProjState } from '../stats/progress';
import { activeSeasonId, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';

export type LiveCompletion = (
  course: string | null | undefined, curLap: number | null | undefined, pos: [number, number] | null | undefined,
  playerId?: number, t?: number, stale?: boolean,
) => number | null;

/** A stateful live-completion function for the presence hub: projects each player's minimap
 *  position onto the course route (reference cached per course) with per-player continuity.
 *  Resets a player's state on a new run (course change or lap decrease) or when pos clears.
 *  Returns 0..1, or null with no position / no reference. cc fixed at 150 (live runs are 150). */
export function makeLiveCompletion(db: DatabaseSync, cc = 150): LiveCompletion {
  const refCache = new Map<number, Reference | null>();
  const pstate = new Map<number, { st: ProjState; course: number; lap: number }>();
  return (course, curLap, pos, playerId, t = Date.now(), stale = false) => {
    if (!course || !pos) { if (playerId != null) pstate.delete(playerId); return null; }
    const courseId = courseIdBySlug(db, slugify(course));
    if (courseId == null) return null;
    let entry = refCache.get(courseId);
    if (entry === undefined) { entry = courseReference(db, activeSeasonId(db), courseId, cc); refCache.set(courseId, entry); }
    if (!entry || entry.ref.length === 0) return null;
    const lap = curLap ?? 1;
    let ps = playerId != null ? pstate.get(playerId) : undefined;
    if (ps && (ps.course !== courseId || lap < ps.lap)) ps = undefined;   // new run -> reset
    const r = step(ps?.st ?? null, entry, { x: pos[0], y: pos[1], lap, t, stale });
    if (playerId != null) pstate.set(playerId, { st: r.state, course: courseId, lap });
    return r.s;
  };
}
```

- [ ] **Step 4: Wire the hub**

In `pi/src/presence/hub.ts`, add `track_state` to `PresenceFrame` (after the `pos`/`final_time` line):

```ts
  pos?: [number, number] | null; final_time?: string | null;
  track_state?: string | null;
```

In `update()`, replace the `const entry: PresenceEntry = { … }` construction's `completion` line and capture `now` once. Change the opening of the method body to:

```ts
    const cur = this.map.get(playerId);
    if (!cur) return;
    const now = this.now();
    const FRESH = new Set(['tracking', 'ring_only']);
    const stale = frame.track_state != null && !FRESH.has(frame.track_state);
    const entry: PresenceEntry = {
```

and within that object set:

```ts
      completion: this.completion(frame.course, frame.cur_lap, frame.pos, playerId, now, stale),
      pb_ms: this.pbForCourse(playerId, frame.course),
      final_time: frame.final_time ?? null, updated_at: now,
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd pi && npm test`
Expected: PASS — new presence/hub cases pass; existing hub tests still pass (their `(_c,_l,pos)=>…` doubles read `pos` as the 3rd arg, unchanged).

- [ ] **Step 6: Commit**

```bash
git add pi/src/presence/completion.ts pi/src/presence/completion.test.ts pi/src/presence/hub.ts pi/src/presence/hub.test.ts
git commit -m "feat(presence): stateful per-player live completion + track_state gating"
```

---

## Task 8: Remove the dead `completionFraction`

**Files:**
- Modify: `pi/src/stats/completion.ts`
- Modify: `pi/src/stats/completion.test.ts`

- [ ] **Step 1: Delete the function and its test**

In `pi/src/stats/completion.ts`, delete the `completionFraction` export (the `/** Nearest-vertex … */` function). In `pi/src/stats/completion.test.ts`, delete the `describe('completionFraction', …)` block and drop `completionFraction` from the import (leaving `import { resolveCompletion } from './completion';`).

- [ ] **Step 2: Run the whole pi suite**

Run: `cd pi && npm test`
Expected: PASS — no references to `completionFraction` remain.

- [ ] **Step 3: Commit**

```bash
git add pi/src/stats/completion.ts pi/src/stats/completion.test.ts
git commit -m "refactor(completion): drop the retired nearest-vertex completionFraction"
```

---

## Task 9: Frontend forwards `track_state`

**Files:**
- Modify: `src/lib/presence.js:13-21`
- Modify: `src/lib/presence.test.js:14-18`

- [ ] **Step 1: Update the `frame()` expectation**

In `src/lib/presence.test.js`, the first test sets `minimap.set({ …, trackState: 1, … })`. Change `trackState: 1` to `trackState: "tracking"`, and add `track_state: "tracking"` to the expected object:

```js
    minimap.set({ cx: 12, cy: 34, radius: 5, trackState: "tracking", roi: [0, 0, 1, 1] });
    resets.set(4);
    expect(frame()).toEqual({
      screen: "RACING", course: "Bowsers Castle", character: "Mario", kart: "Std", costume: "Base",
      cur_lap: 2, tot_lap: 3, coins: 7, mushrooms: 1, pos: [12, 34], final_time: null, resets: 4,
      track_state: "tracking",
    });
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/lib/presence.test.js`
Expected: FAIL — `frame()` does not include `track_state`.

- [ ] **Step 3: Forward `track_state` in `frame()`**

In `src/lib/presence.js`, change the returned object's last line:

```js
    pos: mm ? [mm.cx, mm.cy] : null, final_time: r.finishTime, resets: get(resets),
    track_state: mm ? mm.trackState : null,
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/lib/presence.test.js`
Expected: PASS — both `frame()` tests and the `wsUrl()` tests pass (the "pos is null" test still passes; `track_state` is `null` there).

- [ ] **Step 5: Commit**

```bash
git add src/lib/presence.js src/lib/presence.test.js
git commit -m "feat(presence): forward minimap track_state in the frame"
```

---

## Final verification

- [ ] **Full pi suite:** `cd pi && npm test` — all green.
- [ ] **Full frontend suite:** `npx vitest run` (repo root) — all green.
- [ ] **Type check:** `npx svelte-check` (repo root) — 0 errors / 0 warnings.

---

## Self-Review (author checklist — completed)

**Spec coverage** (`2026-06-09-progress-projector-design.md`):
- §4 module shape → Tasks 1-5 (`progress.ts`), 6-8 (consumers). ✓
- §5 reference prep (dedup / teleport-clip / resample / bounds) → Task 2. ✓
- §6 `step` (lap window, stale-hold, tracking window, monotonic clamp, bootstrap, heading) → Tasks 3-5. ✓
- §7 constants → Tasks 2-4 (with the reach mechanism refined to displacement-scaled — see below). ✓
- §8 live wiring (stateful map, signature, run-reset, hold-on-stale, `track_state`) → Task 7. ✓
- §9 reset-stat trail replay → Task 6. ✓
- §10 tests (figure-8, identical-laps, heading, dropout, monotonic, stale, hub isolation, replay) → Tasks 2-7. ✓
- §11 deltas: reference cleaned/resampled (T2), hold-not-zero (T7), `track_state` field (T7/T9), reset numbers shift (T6). ✓

**Spec reconciliation:** §6/§7's "pace = EMA of Δs/Δt" is implemented as a **displacement-scaled** reach (`K_REACH * pixelsMoved / totalLen`, floored by `EPS_FWD_MIN`). Simpler, no pace state, holds on heartbeat repeats. The spec file is updated to match in the same series.

**Placeholder scan:** none — every code/command step is concrete.

**Type consistency:** `Reference { ref; bounds; totalLen }`, `ProjState { s; t; x; y } | null`, `Obs { x; y; lap; t; stale }`, and `step(state, ref, obs) -> { state; s }` are used identically across `progress.ts`, `completion.ts`, and `presence/completion.ts`. `LiveCompletion` gains optional trailing `(playerId?, t?, stale?)` — append-only, so existing hub test doubles keep working.

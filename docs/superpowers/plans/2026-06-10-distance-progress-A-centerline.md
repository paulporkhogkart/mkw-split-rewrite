# Distance Progress v2 — Plan A: Per-Lap Centerline + Distance Completion

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make live completion **distance-based and per-lap** — `% = (Σ completed-lap lengths + arc into current lap) / total course length` — so uneven-lap / variable-lap-count courses read correctly, using per-lap **centerlines** (no branch CV yet).

**Architecture:** The builder stops collapsing laps; it groups each run's points by lap *index*, pools across runs, and builds one centerline per lap index → a `CourseModel` (ordered `LapRoute[]` with per-lap arc-length + cumulative offsets). The projector selects the current lap's route via the HUD lap, matches with the existing forward-window matcher, and converts to cumulative distance. Reworks the Plan-1 `pi/src/progress/` module.

**Tech Stack:** TypeScript, Node 22 `node:sqlite`, vitest 4. Spec: `docs/superpowers/specs/2026-06-10-distance-progress-model-design.md`.

**Out of scope (follow-on plans):** engine per-point lap stamp (Plan B); branch-aware graph CV per lap (Plan C); frontend continuous-fill + live dividers + remove temp debug % (Plan D); reset-stat migration + retire single-run projector (Plan E). This plan keeps the time-derived lap fallback, `status='centerline'`, and the existing card rendering (the temp debug % shows the corrected value).

**Conventions:** pi tests from `pi/` (`cd pi && npx vitest run <file>`); commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` (omitted below).

---

## File Structure

- **Modify** `pi/src/progress/types.ts` — add `LapRoute`, `CourseModel`; keep `CourseGraph`/`GraphEdge`/`GraphNode`/`Transform`/`RunInput`/`ProjState`/`Obs`.
- **Modify** `pi/src/progress/build.ts` — add `groupByLap`; `buildCourseModel` returns `{ model: CourseModel; alignments }` (per-lap centerlines). `foldRun` retained (used by `groupByLap`).
- **Modify** `pi/src/progress/build.test.ts`.
- **Modify** `pi/src/progress/project.ts` — add `prepareModel`; `projectStep(state, model, prepared, obs)` returns distance completion.
- **Modify** `pi/src/progress/project.test.ts`.
- **Modify** `pi/src/db/courseModels.ts` — `loadCourseModel`/`saveCourseModel` typed to `CourseModel` (JSON blob; `lap_length_px` column stores `totalLengthPx`).
- **Modify** `pi/src/db/courseModels.test.ts`.
- **Modify** `pi/src/scripts/buildCourseModel.ts` — adapt to the new return shape + log.
- **Modify** `pi/src/presence/completion.ts` — `makeLiveCompletion` uses `CourseModel` + the distance projector.
- **Modify** `pi/src/presence/completion.test.ts`.

---

## Task 1: v2 model types

**Files:** Modify `pi/src/progress/types.ts`

- [ ] **Step 1: Add the types** (append after the existing `CourseGraph` interface; keep everything else):

```ts
/** One lap's route (a CourseGraph scoped to a single lap) plus its place in the race. */
export interface LapRoute {
  index: number;          // 1-based lap index
  lengthPx: number;       // arc-length of this lap's route
  startOffsetPx: number;  // Σ lengthPx of prior laps (lap 1 = 0)
  graph: CourseGraph;     // this lap's geometry; graph.lapLengthPx === lengthPx
}

/** A course as an ordered list of per-lap routes; completion is cumulative distance. */
export interface CourseModel {
  version: number;        // 2
  totalLengthPx: number;  // Σ laps[].lengthPx
  laps: LapRoute[];
  status: 'graph' | 'centerline';
}
```

- [ ] **Step 2: Commit**

```bash
git add pi/src/progress/types.ts
git commit -m "feat(progress): CourseModel v2 (per-lap LapRoute) types"
```

---

## Task 2: Builder — `groupByLap`

Split a run's points into per-lap point lists, each carrying that lap's within-lap fraction `f`.

**Files:** Modify `pi/src/progress/build.ts`, `pi/src/progress/build.test.ts`

- [ ] **Step 1: Write the failing test** — append to `build.test.ts`:

```ts
import { groupByLap } from './build';

describe('groupByLap', () => {
  it('splits points by lap index with per-lap fraction f', () => {
    const run: RunInput = {
      playerId: 1, lapCumMs: [100, 200],
      points: [
        { t_ms: 0,   cx: 0, cy: 0, score: 1, lap: 1 },
        { t_ms: 50,  cx: 5, cy: 0, score: 1, lap: 1 },   // lap1 f=0.5
        { t_ms: 150, cx: 9, cy: 0, score: 1, lap: 2 },   // lap2 f=0.5
      ],
    };
    const g = groupByLap(run);
    expect([...g.keys()].sort()).toEqual([1, 2]);
    expect(g.get(1)!.map((p) => Number(p.f.toFixed(2)))).toEqual([0, 0.5]);
    expect(g.get(2)!.map((p) => Number(p.f.toFixed(2)))).toEqual([0.5]);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/progress/build.test.ts`
Expected: FAIL — `groupByLap` not exported.

- [ ] **Step 3: Implement** — append to `build.ts` (reuse the existing `foldRun` per-point `f` math by factoring it; simplest is a direct implementation):

```ts
/** Split a run into per-lap-index point lists, each tagged with that lap's within-lap fraction f. */
export function groupByLap(run: RunInput): Map<number, FoldPt[]> {
  const cum = run.lapCumMs;
  const lapOfT = (t: number) => { let L = 1; for (const b of cum) { if (t >= b) L++; else break; } return L; };
  const out = new Map<number, FoldPt[]>();
  for (const p of run.points) {
    const lap = p.lap ?? lapOfT(p.t_ms);
    const lo = lap >= 2 ? (cum[lap - 2] ?? 0) : 0;
    const hi = cum[lap - 1] ?? (cum[cum.length - 1] ?? lo + 1);
    const span = hi - lo;
    const f = span > 0 ? Math.min(0.999999, Math.max(0, (p.t_ms - lo) / span)) : 0;
    if (!out.has(lap)) out.set(lap, []);
    out.get(lap)!.push({ x: p.cx, y: p.cy, f, score: p.score });
  }
  return out;
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/progress/build.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/progress/build.ts pi/src/progress/build.test.ts
git commit -m "feat(progress): groupByLap — per-lap-index point lists"
```

---

## Task 3: Builder — `buildCourseModel` returns a per-lap `CourseModel`

Pool each lap index across runs, build one centerline per lap, assemble `LapRoute[]` with arc-length + offsets. Per-lap centerlines are **not** force-closed and need **no anchor** — each lap's `f=0` is its own start (lap 1 = grid, laps ≥2 = the line), which is what fixes the start offset.

**Files:** Modify `pi/src/progress/build.ts`, `pi/src/progress/build.test.ts`

- [ ] **Step 1: Write the failing test** — append to `build.test.ts` (reuses `loopRun` from the existing tests; add an uneven-lap fixture):

```ts
import { buildCourseModel } from './build';

// lap 1 is a big circle (r=50), lap 2 a small one (r=20): unequal lengths.
function unevenRun(playerId: number): RunInput {
  const pts: RunInput['points'] = [];
  const add = (lap: number, r: number, base: number) => {
    for (let i = 0; i < 48; i++) { const a = (i / 48) * 2 * Math.PI;
      pts.push({ t_ms: base + (i / 48) * 1000, cx: r * Math.cos(a), cy: r * Math.sin(a), score: 1, lap }); }
  };
  add(1, 50, 0); add(2, 20, 1000);
  return { playerId, lapCumMs: [1000, 2000], points: pts };
}

describe('buildCourseModel (v2 per-lap)', () => {
  it('builds one LapRoute per lap, with cumulative offsets and a distance total', () => {
    const res = buildCourseModel([unevenRun(1), unevenRun(2)], { bins: 64 });
    expect(res).not.toBeNull();
    const m = res!.model;
    expect(m.version).toBe(2);
    expect(m.laps).toHaveLength(2);
    expect(m.laps[0].startOffsetPx).toBe(0);
    expect(m.laps[1].startOffsetPx).toBeCloseTo(m.laps[0].lengthPx, 5);   // offset = prior length
    expect(m.totalLengthPx).toBeCloseTo(m.laps[0].lengthPx + m.laps[1].lengthPx, 5);
    expect(m.laps[0].lengthPx).toBeGreaterThan(m.laps[1].lengthPx * 2);    // r50 lap >> r20 lap
  });

  it('returns null with no usable points', () => {
    expect(buildCourseModel([], {})).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/progress/build.test.ts`
Expected: FAIL — `buildCourseModel`'s old return shape (`{ graph }`) has no `.model`.

- [ ] **Step 3: Rewrite `centerline` (open, not force-closed) and `buildCourseModel`** in `build.ts`.

Replace the existing `centerline` body so it does NOT push `c[0]` (per-lap routes are open):

```ts
/** Ordered centerline (open) from points: score-weighted centroid per f-bin. */
export function centerline(pts: FoldPt[], bins: number): [number, number][] {
  return fBinCentroids(pts, bins).filter((p): p is [number, number] => p != null);
}
```

Replace `BuildResult` + `buildCourseModel` (keep `arcLen`, `DEF_BINS`, and the `anchorAtLine` export — `anchorAtLine` is now unused by the builder but harmless; remove it if your lint flags unused exports):

```ts
import type { CourseModel, LapRoute, GraphEdge, GraphNode } from './types';

export interface BuildResult {
  model: CourseModel;
  alignments: { playerId: number; transform: Transform }[];
}

function lapGraph(poly: [number, number][]): { graph: CourseGraph; lengthPx: number } {
  const lengthPx = arcLen(poly);
  const node: GraphNode = { id: 0, x: poly[0][0], y: poly[0][1], progress: 0 };
  const edge: GraphEdge = { id: 0, a: 0, b: 0, poly, arcLen: lengthPx, pLo: 0, pHi: 1, kind: 'main', passThrough: null };
  const graph: CourseGraph = { version: 1, startNode: 0, lapLengthPx: lengthPx, nodes: [node], edges: [edge], status: 'centerline' };
  return { graph, lengthPx };
}

export function buildCourseModel(runs: RunInput[], opts: { bins?: number } = {}): BuildResult | null {
  const bins = opts.bins ?? DEF_BINS;
  const grouped = runs.map(groupByLap);
  const lapIndices = [...new Set(grouped.flatMap((g) => [...g.keys()]))].sort((a, b) => a - b);
  if (lapIndices.length === 0) return null;

  // Per-player alignment is estimated once on lap 1 (cheap; the live frame is one capture).
  const perRunTransform: Transform[] = grouped.map(() => ({ dx: 0, dy: 0, scale: 1 }));
  {
    const lap1 = grouped.map((g) => g.get(lapIndices[0]) ?? []);
    let refIdx = 0;
    for (let i = 1; i < lap1.length; i++) if (lap1[i].length > lap1[refIdx].length) refIdx = i;
    const refC = fBinCentroids(lap1[refIdx], Math.min(bins, 32));
    lap1.forEach((f, i) => { if (i !== refIdx && f.length) perRunTransform[i] = fitTranslation(refC, fBinCentroids(f, Math.min(bins, 32))); });
  }

  const laps: LapRoute[] = [];
  let offset = 0;
  for (const k of lapIndices) {
    const merged: FoldPt[] = [];
    grouped.forEach((g, i) => { for (const p of g.get(k) ?? []) merged.push(applyTransform(p, perRunTransform[i])); });
    const poly = centerline(merged, bins);
    if (poly.length < 3) return null;
    const { graph, lengthPx } = lapGraph(poly);
    laps.push({ index: k, lengthPx, startOffsetPx: offset, graph });
    offset += lengthPx;
  }

  const model: CourseModel = { version: 2, totalLengthPx: offset, laps, status: 'centerline' };

  const byPlayer = new Map<number, { dx: number; dy: number; n: number }>();
  runs.forEach((r, i) => {
    const cur = byPlayer.get(r.playerId) ?? { dx: 0, dy: 0, n: 0 };
    cur.dx += perRunTransform[i].dx; cur.dy += perRunTransform[i].dy; cur.n++; byPlayer.set(r.playerId, cur);
  });
  const alignments = [...byPlayer.entries()].map(([playerId, v]) =>
    ({ playerId, transform: { dx: v.dx / v.n, dy: v.dy / v.n, scale: 1 } as Transform }));

  return { model, alignments };
}
```

(Delete the now-replaced old `buildCourseModel`/`BuildResult` and the old `centerline` that pushed `c[0]`. The earlier `buildCourseModel` test from Plan 1 — the `loopRun` "centerline graph" test — is replaced by the v2 test above; delete that old `describe('buildCourseModel'…)` block.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/progress/build.test.ts`
Expected: PASS — `groupByLap`, alignment, foldRun, and the v2 `buildCourseModel` tests green.

- [ ] **Step 5: Commit**

```bash
git add pi/src/progress/build.ts pi/src/progress/build.test.ts
git commit -m "feat(progress): buildCourseModel emits per-lap CourseModel v2"
```

---

## Task 4: Projector — distance completion over the model

**Files:** Modify `pi/src/progress/project.ts`, `pi/src/progress/project.test.ts`

- [ ] **Step 1: Write the failing tests** — replace the `G`/`obs` setup at the top of `project.test.ts` and the tests with model-based ones:

```ts
import { projectStep, prepareModel } from './project';
import type { CourseModel, ProjState } from './types';

const SQUARE: [number, number][] = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
const lap = (index: number, startOffsetPx: number, lengthPx = 40): import('./types').LapRoute => ({
  index, lengthPx, startOffsetPx,
  graph: { version: 1, startNode: 0, lapLengthPx: lengthPx, status: 'centerline',
    nodes: [{ id: 0, x: 0, y: 0, progress: 0 }],
    edges: [{ id: 0, a: 0, b: 0, poly: SQUARE, arcLen: lengthPx, pLo: 0, pHi: 1, kind: 'main', passThrough: null }] },
});
// Two equal laps -> total 80; lap boundary at 50%.
const M: CourseModel = { version: 2, totalLengthPx: 80, status: 'centerline', laps: [lap(1, 0), lap(2, 40)] };
const obs = (x: number, y: number, lp = 1, t = 0, stale = false) => ({ x, y, lap: lp, totLap: 2, t, stale });

describe('projectStep (distance, per-lap)', () => {
  it('accumulates distance across laps', () => {
    const pe = prepareModel(M);
    let st: ProjState = null;
    let r = projectStep(st, M, pe, obs(10, 0, 1, 0)); st = r.state;     // lap1 quarter -> 10/80
    expect(r.completion).toBeCloseTo(10 / 80, 2);
    r = projectStep(st, M, pe, obs(10, 0, 2, 100));                     // lap2 quarter -> (40+10)/80
    expect(r.completion).toBeCloseTo(50 / 80, 2);
  });

  it('uneven laps put the lap-1 boundary at its real proportion, not 1/N', () => {
    const UM: CourseModel = { version: 2, totalLengthPx: 100, status: 'centerline', laps: [lap(1, 0, 80), lap(2, 80, 20)] };
    const pe = prepareModel(UM);
    const r = projectStep({ edge: 0, progress: 1, x: 0, y: 0, t: 0 }, UM, pe, { x: 0, y: 0, lap: 2, totLap: 2, t: 1, stale: false });
    expect(r.completion).toBeGreaterThanOrEqual(80 / 100 - 0.01);       // end lap1 ≈ 80%, not 50%
  });

  it('clamps to [0,1] past the final lap, and holds while stale', () => {
    const pe = prepareModel(M);
    expect(projectStep({ edge: 0, progress: 1, x: 0, y: 0, t: 0 }, M, pe, obs(0, 0, 3, 9)).completion).toBe(1);
    expect(projectStep({ edge: 0, progress: 0.5, x: 5, y: 0, t: 0 }, M, pe, obs(9, 9, 1, 9, true)).completion).toBeCloseTo((0 + 0.5 * 40) / 80, 4);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/progress/project.test.ts`
Expected: FAIL — `prepareModel` not exported; `projectStep` signature mismatch.

- [ ] **Step 3: Rewrite `project.ts`** — keep `nearestOnEdge` and the constants; change `prepareEdges`→`prepareModel` and `projectStep` to select a lap and return distance completion:

```ts
import type { CourseModel, GraphEdge, ProjState, Obs } from './types';

const EPS_BACK = 0.004, REACH_K = 2.5, EPS_FWD_MIN = 0.02, MATCH_DIST = 60;

interface PreparedEdge { edge: GraphEdge; cumFrac: number[]; }
export interface PreparedLap { edges: PreparedEdge[]; }
export interface Prepared { laps: PreparedLap[]; }

export function prepareModel(m: CourseModel): Prepared {
  return { laps: m.laps.map((lap) => ({ edges: lap.graph.edges.map((edge) => {
    const cum = [0];
    for (let i = 1; i < edge.poly.length; i++)
      cum.push(cum[i - 1] + Math.hypot(edge.poly[i][0] - edge.poly[i - 1][0], edge.poly[i][1] - edge.poly[i - 1][1]));
    const total = cum[cum.length - 1] || 1;
    return { edge, cumFrac: cum.map((c) => c / total) };
  }) })) };
}

// nearestOnEdge: UNCHANGED from the current project.ts — keep it verbatim.

/** Project onto the current lap's route; completion is cumulative distance over the whole course. */
export function projectStep(state: ProjState, m: CourseModel, pe: Prepared, obs: Obs):
    { state: ProjState; completion: number | null } {
  const N = m.laps.length || 1;
  const k = Math.min(Math.max(obs.lap, 1), N);                  // clamp lap into range
  const lapRoute = m.laps[k - 1];
  const plap = pe.laps[k - 1];
  const toPct = (u: number) =>
    Math.max(0, Math.min(1, (lapRoute.startOffsetPx + u * lapRoute.lengthPx) / (m.totalLengthPx || 1)));
  const finished = obs.lap > N;
  if (finished) return { state, completion: 1 };
  if (obs.stale) return { state, completion: state ? toPct(state.progress) : null };
  if (!plap || plap.edges.length === 0) return { state, completion: state ? toPct(state.progress) : null };

  const tracking = state != null;
  const loP = tracking ? Math.max(0, state!.progress - EPS_BACK) : 0;
  const moved = tracking ? Math.hypot(obs.x - state!.x, obs.y - state!.y) : 0;
  const reach = Math.max(EPS_FWD_MIN, REACH_K * moved / (lapRoute.lengthPx || 1));
  const hiP = tracking ? Math.min(1, state!.progress + reach) : 1;

  let best = Infinity, bestU = tracking ? state!.progress : 0;
  for (const e of plap.edges) {
    const r = nearestOnEdge(e, loP, hiP, obs.x, obs.y);
    if (r.dist < best) { best = r.dist; bestU = r.progress; }
  }
  if (best > MATCH_DIST && tracking) return { state, completion: toPct(state!.progress) };

  const u = Math.max(tracking ? state!.progress - EPS_BACK : 0, Math.min(1, bestU));
  return { state: { edge: 0, progress: u, x: obs.x, y: obs.y, t: obs.t }, completion: toPct(u) };
}
```

(`nearestOnEdge` and its helper stay exactly as they are in the current file — only the prepare/step wrappers change. `ProjState.progress` is now the within-lap fraction `u`.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/progress/project.test.ts`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add pi/src/progress/project.ts pi/src/progress/project.test.ts
git commit -m "feat(progress): distance-based per-lap projector"
```

---

## Task 5: Storage + live wiring

**Files:** Modify `pi/src/db/courseModels.ts` (+test), `pi/src/scripts/buildCourseModel.ts`, `pi/src/presence/completion.ts` (+test)

- [ ] **Step 1: Retype the repo** — in `pi/src/db/courseModels.ts`, change the model type from `CourseGraph` to `CourseModel` (the SQL is unchanged — it's a JSON blob; `lap_length_px` now stores `totalLengthPx`):

```ts
import type { CourseModel, Transform } from '../progress/types';
export function saveCourseModel(db: DatabaseSync, courseId: number, cc: number, m: CourseModel, sourceRuns: number): void {
  db.prepare(`INSERT INTO course_models(course_id, cc, model_json, lap_length_px, status, source_run_count, version, built_at)
     VALUES (?,?,?,?,?,?,?, datetime('now'))
     ON CONFLICT(course_id, cc) DO UPDATE SET model_json=excluded.model_json, lap_length_px=excluded.lap_length_px,
       status=excluded.status, source_run_count=excluded.source_run_count, version=excluded.version, built_at=excluded.built_at`)
    .run(courseId, cc, JSON.stringify(m), m.totalLengthPx, m.status, sourceRuns, m.version);
}
export function loadCourseModel(db: DatabaseSync, courseId: number, cc: number): CourseModel | null {
  const r = db.prepare('SELECT model_json FROM course_models WHERE course_id=? AND cc=?').get(courseId, cc) as { model_json: string } | undefined;
  return r ? (JSON.parse(r.model_json) as CourseModel) : null;
}
// savePlayerAlignment / loadPlayerAlignment UNCHANGED.
```

Update `courseModels.test.ts`: change the test `G` to a v2 `CourseModel` (`{ version: 2, totalLengthPx: 100, status: 'centerline', laps: [{ index:1, lengthPx:100, startOffsetPx:0, graph: <the old SQUARE graph> }] }`) and assert `loadCourseModel(...)!.totalLengthPx === 100` / upsert to 222 via `{ ...m, totalLengthPx: 222 }`.

- [ ] **Step 2: Fix the CLI** — in `pi/src/scripts/buildCourseModel.ts`, change the result handling to the new shape:

```ts
  const res = buildCourseModel(inputs);
  if (!res) { console.error(`no usable runs for ${slug}`); process.exitCode = 1; return; }
  saveCourseModel(db, course.id, cc, res.model, inputs.length);
  for (const a of res.alignments) savePlayerAlignment(db, a.playerId, a.transform, 1);
  console.log(`[course-model] ${slug} cc${cc}: ${res.model.status}, ${res.model.laps.length} laps, total=${res.model.totalLengthPx.toFixed(0)}px, ${inputs.length} runs`);
```

- [ ] **Step 3: Rewrite `makeLiveCompletion`** (`pi/src/presence/completion.ts`) to use the model + distance projector:

```ts
import { prepareModel, projectStep, type Prepared } from '../progress/project';
import type { CourseModel, ProjState } from '../progress/types';
// ...loadCourseModel/loadPlayerAlignment/courseIdBySlug/slugify imports unchanged...

export function makeLiveCompletion(db: DatabaseSync, cc = 150): LiveCompletion {
  const modelCache = new Map<number, { m: CourseModel; pe: Prepared } | null>();
  const pstate = new Map<number, { st: ProjState; course: number; lap: number }>();
  return (course, curLap, pos, playerId, t = Date.now(), stale = false, totLap) => {
    if (!course || !pos) { if (playerId != null) pstate.delete(playerId); return null; }
    const courseId = courseIdBySlug(db, slugify(course));
    if (courseId == null) return null;
    let entry = modelCache.get(courseId);
    if (entry === undefined) { const m = loadCourseModel(db, courseId, cc); entry = m ? { m, pe: prepareModel(m) } : null; modelCache.set(courseId, entry); }
    if (!entry) return null;
    const lap = curLap ?? 1;
    const N = entry.m.laps.length;
    const al = playerId != null ? loadPlayerAlignment(db, playerId) : { dx: 0, dy: 0, scale: 1 };
    const x = pos[0] * al.scale + al.dx, y = pos[1] * al.scale + al.dy;
    let ps = playerId != null ? pstate.get(playerId) : undefined;
    if (ps && (ps.course !== courseId || (lap !== ps.lap && lap <= N))) ps = undefined;   // reset on new run or in-race lap change
    const r = projectStep(ps?.st ?? null, entry.m, entry.pe, { x, y, lap, totLap: totLap ?? N, t, stale });
    if (playerId != null) pstate.set(playerId, { st: r.state, course: courseId, lap });
    return r.completion;
  };
}
```

Update `completion.test.ts`: `seedModel` saves a v2 `CourseModel` (one SQUARE lap, total 40) — `live('Bowsers Castle', 1, [10,0], 1, t, false, 1)` → `toBeCloseTo(0.25, 2)` (single lap, quarter = 25%). Adjust the seam/stale/post-finish cases to the v2 numbers (e.g. a 2-lap model: lap2 quarter → `(40+10)/80`). Keep the "no model → null" and "pos clears → null" cases.

- [ ] **Step 4: Run the presence + progress suites**

Run: `cd pi && npx vitest run src/progress/ src/db/courseModels.test.ts src/presence/`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/db/courseModels.ts pi/src/db/courseModels.test.ts pi/src/scripts/buildCourseModel.ts pi/src/presence/completion.ts pi/src/presence/completion.test.ts
git commit -m "feat(progress): wire v2 CourseModel through storage, CLI, live completion"
```

---

## Final verification

- [ ] **Full pi suite:** `cd pi && npm test` — all green. (`avg_completion_before_reset` still uses the old single-run `stats/` path — untouched here, retired in Plan E.)
- [ ] **Type check:** `npx svelte-check` (repo root) — 0/0 (frontend untouched this plan).
- [ ] **Real-data smoke:** `cd pi && npm run build-course-model -- --course bowsers_castle` → prints `2 laps`? No — bowsers_castle is 3 laps, so `3 laps, total=<~3·lap>px`. Restart the pi server; the yellow debug % should rise 0→100% across the race with the **lap-1 start near 0%** and lap boundaries at each lap's true distance proportion (≈33/66 on this uniform course).

---

## Notes for follow-on plans

- **Plan B:** engine per-point `lap` stamp (`recorder.py` → Rust `AttemptPayload.points` → `ingest.ts`) for exact lap grouping (removes the time-derived/countdown ambiguity in `groupByLap`).
- **Plan C:** replace per-lap `centerline()` with density-raster → Zhang–Suen skeleton → branch-aware graph (`status='graph'`); `prepareModel`/`projectStep` already consume a multi-edge per-lap graph unchanged.
- **Plan D:** presence emits live `dividers[]` (push completion at each lap tick); frontend `playerCard.js`/`PlayerCard.svelte` render one continuous fill + divider ticks; remove the temp debug %.
- **Plan E:** migrate `avg_completion_before_reset` (`resolveCompletion`) to the v2 projector; delete the retired single-run `stats/progress.ts` + `courseReference`.

---

## Self-Review (author checklist — completed)

**Spec coverage:** §3 data model → Task 1 (LapRoute/CourseModel). §4 builder (group by lap, per-lap centerline, arc-length, offsets, total) → Tasks 2-3 (centerline path; branch CV is Plan C per the agreed phasing). §5 projector (HUD-lap selects route, distance completion, monotonic/stale/post-finish) → Task 4. §6 frontend / live dividers → deferred to Plan D (explicit). §7 engine stamp → Plan B (time-derived fallback meanwhile, Task 2). §8 retirement → Plan E. §11 tests → each task (uneven-lap fixture covers the core requirement). Deferrals explicit in "Notes for follow-on plans".

**Placeholder scan:** none — every code/command step is concrete (the test-file *edits* in Task 5 describe exact value changes against the existing files, which the implementer reads first).

**Type consistency:** `CourseModel`/`LapRoute` (Task 1) are produced by `buildCourseModel` (Task 3), persisted by `saveCourseModel` (Task 5), prepared by `prepareModel` and consumed by `projectStep` (Task 4), and read by `makeLiveCompletion` (Task 5) — same shape throughout. `projectStep(state, model, prepared, obs)` and `prepareModel(model)` signatures match across Tasks 4 and 5. `ProjState.progress` is reused as the within-lap fraction `u`.

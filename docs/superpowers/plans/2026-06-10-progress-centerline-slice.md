# Live Progress — Centerline Vertical Slice (Plan 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the live race-completion projector with an aggregated-trail **centerline** model + a forward-window graph projector, end-to-end, fixing the start/finish seam and self-crossing snap on non-branching courses.

**Architecture:** An offline builder folds all laps of the recent finished runs into one lap (by in-lap time fraction `f`), aligns runs by `f`-binned centroids, and emits a `CourseGraph` whose single cyclic edge is the score-weighted centerline. A pure projector matches a live minimap position onto that graph within a forward progress window and returns `(lap−1 + u)/laps`. Branch detection (full skeleton graph) is a later plan; the projector already consumes a general graph, so the centerline is just a one-edge graph.

**Tech Stack:** TypeScript, Node 22 `node:sqlite`, vitest 4. The `CourseGraph` JSON is the language-neutral artifact. Spec: `docs/superpowers/specs/2026-06-10-course-progress-model-design.md`.

**Conventions:** pi tests run from `pi/` (`cd pi && npx vitest run <file>`). All commits end with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer (omitted below for brevity).

---

## File Structure

- **Create** `pi/src/progress/types.ts` — `CourseGraph`, `GraphNode`, `GraphEdge`, `Transform`, `RunInput`, `ProjState`, `Obs`.
- **Create** `pi/src/progress/build.ts` — pure builder: `foldRun`, `alignRuns`, `centerline`, `buildCourseModel`.
- **Create** `pi/src/progress/build.test.ts`.
- **Create** `pi/src/progress/project.ts` — pure projector: `prepareEdges`, `projectStep`.
- **Create** `pi/src/progress/project.test.ts`.
- **Create** `pi/src/db/courseModels.ts` — `saveCourseModel`, `loadCourseModel`, `savePlayerAlignment`, `loadPlayerAlignment`.
- **Create** `pi/src/db/courseModels.test.ts`.
- **Modify** `server/schema.sql` — add `course_models`, `player_alignment` tables.
- **Modify** `pi/src/db/connect.ts:33-41` — additive `ALTER TABLE run_points ADD COLUMN lap`.
- **Create** `pi/src/scripts/buildCourseModel.ts` — CLI: build a course's model from its recent runs.
- **Modify** `pi/src/presence/completion.ts` — `makeLiveCompletion` uses the new model + projector.
- **Modify** `pi/src/presence/completion.test.ts` — adjust live-completion expectations.

---

## Task 1: CourseGraph types

**Files:**
- Create: `pi/src/progress/types.ts`

- [ ] **Step 1: Write the types**

```ts
// pi/src/progress/types.ts
export interface GraphNode { id: number; x: number; y: number; progress: number; }

export interface GraphEdge {
  id: number; a: number; b: number;        // endpoint node ids (a===b for a cyclic centerline)
  poly: [number, number][];                // common-frame polyline
  arcLen: number;                          // total px length of poly
  pLo: number; pHi: number;                // progress range covered by this edge
  kind: 'main' | 'branch';
  passThrough: number | null;              // paired edge id at a crossing (always null in Plan 1)
}

export interface CourseGraph {
  version: number; startNode: number; lapLengthPx: number;
  nodes: GraphNode[]; edges: GraphEdge[];
  status: 'graph' | 'centerline';
}

export interface Transform { dx: number; dy: number; scale: number; }

/** One run's recorded trail + its lap structure, for the builder. */
export interface RunInput {
  playerId: number;
  points: { t_ms: number; cx: number; cy: number; score: number; lap: number | null }[];
  lapCumMs: number[];                      // cumulative lap end-times (run_laps), ascending
}

export type ProjState = { edge: number; progress: number; x: number; y: number; t: number } | null;
export interface Obs { x: number; y: number; lap: number; totLap: number; t: number; stale: boolean; }
```

- [ ] **Step 2: Commit**

```bash
git add pi/src/progress/types.ts
git commit -m "feat(progress): CourseGraph + projector types"
```

---

## Task 2: Schema — new tables + `run_points.lap`

**Files:**
- Modify: `server/schema.sql` (after the `run_points` table, ~line 69)
- Modify: `pi/src/db/connect.ts:33-41` (additive migrations block)
- Test: `pi/src/db/courseModels.test.ts` (schema smoke — created in Task 6; here we just add the DDL)

- [ ] **Step 1: Add the two tables to `server/schema.sql`**

Insert after the `run_points` table definition (before `world_records`):

```sql
CREATE TABLE IF NOT EXISTS course_models (
    course_id        INTEGER NOT NULL REFERENCES courses(id),
    cc               INTEGER NOT NULL,
    model_json       TEXT NOT NULL,
    lap_length_px    REAL NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('graph','centerline')),
    source_run_count INTEGER NOT NULL,
    version          INTEGER NOT NULL,
    built_at         TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (course_id, cc)
);

CREATE TABLE IF NOT EXISTS player_alignment (
    player_id     INTEGER PRIMARY KEY REFERENCES players(id),
    dx            REAL NOT NULL,
    dy            REAL NOT NULL,
    scale         REAL NOT NULL DEFAULT 1.0,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    sample_count  INTEGER NOT NULL
);
```

- [ ] **Step 2: Add the `run_points.lap` migration to `connect.ts`**

In `pi/src/db/connect.ts`, inside `applySchema`, after the `mushrooms_used` migrations (line ~37) and before the `idx_wr_current` line:

```ts
  // Additive: per-point HUD lap (1-based). Null for legacy rows; builder falls back to time.
  try { db.exec('ALTER TABLE run_points ADD COLUMN lap INTEGER'); } catch { /* present */ }
```

- [ ] **Step 3: Verify schema applies (existing suite)**

Run: `cd pi && npx vitest run src/db/connect.test.ts`
Expected: PASS (schema still loads; new tables are additive).

- [ ] **Step 4: Commit**

```bash
git add server/schema.sql pi/src/db/connect.ts
git commit -m "feat(progress): course_models + player_alignment tables, run_points.lap"
```

---

## Task 3: Builder — fold a run into `(x, y, f, score)` by in-lap fraction

**Files:**
- Create: `pi/src/progress/build.ts`
- Create: `pi/src/progress/build.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// pi/src/progress/build.test.ts
import { describe, it, expect } from 'vitest';
import { foldRun } from './build';
import type { RunInput } from './types';

describe('foldRun', () => {
  it('computes in-lap fraction f from the lap stamp, folding all laps into one', () => {
    // 2 laps; lap 1 ends at t=100, lap 2 at t=200. Points stamped with lap.
    const run: RunInput = {
      playerId: 1, lapCumMs: [100, 200],
      points: [
        { t_ms: 0,   cx: 0, cy: 0, score: 1, lap: 1 },
        { t_ms: 50,  cx: 5, cy: 0, score: 1, lap: 1 },   // f=0.5
        { t_ms: 100, cx: 0, cy: 0, score: 1, lap: 2 },   // lap 2 start, f=0
        { t_ms: 150, cx: 5, cy: 0, score: 1, lap: 2 },   // f=0.5
      ],
    };
    const out = foldRun(run);
    expect(out.map((p) => Number(p.f.toFixed(3)))).toEqual([0, 0.5, 0, 0.5]);
    expect(out).toHaveLength(4);              // all laps folded together
  });

  it('falls back to time-derived lap when lap is null', () => {
    const run: RunInput = {
      playerId: 1, lapCumMs: [100, 200],
      points: [{ t_ms: 150, cx: 9, cy: 0, score: 1, lap: null }],  // 150 is in lap 2 -> f=0.5
    };
    expect(foldRun(run)[0].f).toBeCloseTo(0.5, 3);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/progress/build.test.ts`
Expected: FAIL — `foldRun` not exported.

- [ ] **Step 3: Implement `foldRun`**

```ts
// pi/src/progress/build.ts
import type { RunInput, Transform, CourseGraph } from './types';

export interface FoldPt { x: number; y: number; f: number; score: number; }

/** Lap (1-based) of a timestamp from cumulative lap end-times. */
function lapOf(t: number, cum: number[]): number {
  let L = 1;
  for (const b of cum) { if (t >= b) L++; else break; }
  return L;
}

/** Fold every lap of a run into one lap: each point -> (x, y, in-lap fraction f, score). */
export function foldRun(run: RunInput): FoldPt[] {
  const cum = run.lapCumMs;
  const out: FoldPt[] = [];
  for (const p of run.points) {
    const lap = p.lap ?? lapOf(p.t_ms, cum);
    const lo = lap >= 2 ? (cum[lap - 2] ?? 0) : 0;
    const hi = cum[lap - 1] ?? (cum[cum.length - 1] ?? lo + 1);
    const span = hi - lo;
    const f = span > 0 ? Math.min(0.999999, Math.max(0, (p.t_ms - lo) / span)) : 0;
    out.push({ x: p.cx, y: p.cy, f, score: p.score });
  }
  return out;
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/progress/build.test.ts`
Expected: PASS — 2 tests.

- [ ] **Step 5: Commit**

```bash
git add pi/src/progress/build.ts pi/src/progress/build.test.ts
git commit -m "feat(progress): foldRun — collapse laps via in-lap fraction f"
```

---

## Task 4: Builder — align runs by `f`-binned centroids

**Files:**
- Modify: `pi/src/progress/build.ts`
- Modify: `pi/src/progress/build.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// append to pi/src/progress/build.test.ts
import { fBinCentroids, fitTranslation } from './build';

describe('alignment', () => {
  const ring = (n: number, ox = 0, oy = 0): FoldPt[] =>
    Array.from({ length: n }, (_, i) => ({ x: ox + Math.cos((i / n) * 2 * Math.PI),
      y: oy + Math.sin((i / n) * 2 * Math.PI), f: i / n, score: 1 }));

  it('recovers a pure translation between two runs of the same shape', () => {
    const ref = ring(64);
    const drifted = ring(64, 7, -3);                       // same shape, +7,-3 offset
    const t = fitTranslation(fBinCentroids(ref, 16), fBinCentroids(drifted, 16));
    expect(t.dx).toBeCloseTo(-7, 1);                       // maps drifted -> ref
    expect(t.dy).toBeCloseTo(3, 1);
  });
});
```

(Add `import type { FoldPt } ...`? `FoldPt` is exported from `build.ts`; import it: change the existing import to `import { foldRun, fBinCentroids, fitTranslation, type FoldPt } from './build';`.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/progress/build.test.ts`
Expected: FAIL — `fBinCentroids`/`fitTranslation` not exported.

- [ ] **Step 3: Implement binning + translation fit**

```ts
// append to pi/src/progress/build.ts

/** Score-weighted centroid per f-bin; null for empty bins. Length = bins. */
export function fBinCentroids(pts: FoldPt[], bins: number): ([number, number] | null)[] {
  const sx = new Array(bins).fill(0), sy = new Array(bins).fill(0), sw = new Array(bins).fill(0);
  for (const p of pts) {
    const b = Math.min(bins - 1, Math.floor(p.f * bins));
    sx[b] += p.x * p.score; sy[b] += p.y * p.score; sw[b] += p.score;
  }
  return sx.map((_, b) => (sw[b] > 0 ? [sx[b] / sw[b], sy[b] / sw[b]] : null));
}

/** Least-squares translation mapping `from` centroids onto `ref` centroids (shared bins only). */
export function fitTranslation(ref: ([number, number] | null)[], from: ([number, number] | null)[]): Transform {
  let dx = 0, dy = 0, n = 0;
  for (let b = 0; b < Math.min(ref.length, from.length); b++) {
    const r = ref[b], g = from[b];
    if (r && g) { dx += r[0] - g[0]; dy += r[1] - g[1]; n++; }
  }
  return { dx: n ? dx / n : 0, dy: n ? dy / n : 0, scale: 1 };
}

export function applyTransform(p: FoldPt, t: Transform): FoldPt {
  return { x: p.x * t.scale + t.dx, y: p.y * t.scale + t.dy, f: p.f, score: p.score };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/progress/build.test.ts`
Expected: PASS — 3 describes green.

- [ ] **Step 5: Commit**

```bash
git add pi/src/progress/build.ts pi/src/progress/build.test.ts
git commit -m "feat(progress): f-binned centroid alignment (translation)"
```

---

## Task 5: Builder — centerline + `buildCourseModel`

**Files:**
- Modify: `pi/src/progress/build.ts`
- Modify: `pi/src/progress/build.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// append to pi/src/progress/build.test.ts
import { buildCourseModel } from './build';
import type { RunInput } from './types';

function loopRun(playerId: number, ox = 0, oy = 0): RunInput {
  // 2 laps around a unit circle, 1 lap = 36 pts, lap ends 1000/2000ms.
  const pts = [];
  for (let lap = 1; lap <= 2; lap++) for (let i = 0; i < 36; i++) {
    const a = (i / 36) * 2 * Math.PI;
    pts.push({ t_ms: (lap - 1) * 1000 + (i / 36) * 1000, cx: ox + 50 * Math.cos(a),
      cy: oy + 50 * Math.sin(a), score: 1, lap });
  }
  return { playerId, lapCumMs: [1000, 2000], points: pts };
}

describe('buildCourseModel', () => {
  it('builds a centerline graph that closes the loop with monotonic progress', () => {
    const res = buildCourseModel([loopRun(1), loopRun(2, 6, 0)], { bins: 72 });
    expect(res).not.toBeNull();
    const g = res!.graph;
    expect(g.status).toBe('centerline');
    expect(g.edges).toHaveLength(1);
    expect(g.edges[0].pLo).toBe(0);
    expect(g.edges[0].pHi).toBe(1);
    expect(g.edges[0].poly.length).toBeGreaterThan(40);
    expect(g.lapLengthPx).toBeGreaterThan(250);            // ~2*pi*50 ≈ 314
    // player 2 was offset +6 -> its alignment maps roughly -6 back
    const a2 = res!.alignments.find((a) => a.playerId === 2)!;
    expect(a2.transform.dx).toBeCloseTo(-6, 0);
  });

  it('returns null with no usable points', () => {
    expect(buildCourseModel([], {})).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/progress/build.test.ts`
Expected: FAIL — `buildCourseModel` not exported.

- [ ] **Step 3: Implement `centerline` + `buildCourseModel`**

```ts
// append to pi/src/progress/build.ts
import type { GraphEdge, GraphNode } from './types';

const DEF_BINS = 180;

function arcLen(poly: [number, number][]): number {
  let s = 0;
  for (let i = 1; i < poly.length; i++) s += Math.hypot(poly[i][0] - poly[i - 1][0], poly[i][1] - poly[i - 1][1]);
  return s;
}

/** Ordered cyclic centerline from merged points: score-weighted centroid per f-bin. */
export function centerline(pts: FoldPt[], bins: number): [number, number][] {
  const c = fBinCentroids(pts, bins).filter((p): p is [number, number] => p != null);
  if (c.length >= 2) c.push(c[0]);          // close the loop
  return c;
}

export interface BuildResult {
  graph: CourseGraph;
  alignments: { playerId: number; transform: Transform }[];
}

/** Build a centerline CourseGraph from a set of runs + per-player alignment transforms. */
export function buildCourseModel(runs: RunInput[], opts: { bins?: number } = {}): BuildResult | null {
  const bins = opts.bins ?? DEF_BINS;
  const folded = runs.map(foldRun);
  if (folded.length === 0 || folded.every((f) => f.length === 0)) return null;

  // Reference = densest run; align the rest to it by f-binned centroids.
  let refIdx = 0;
  for (let i = 1; i < folded.length; i++) if (folded[i].length > folded[refIdx].length) refIdx = i;
  const refC = fBinCentroids(folded[refIdx], Math.min(bins, 32));

  const perRun: Transform[] = folded.map((f, i) =>
    i === refIdx ? { dx: 0, dy: 0, scale: 1 } : fitTranslation(refC, fBinCentroids(f, Math.min(bins, 32))));

  const merged: FoldPt[] = [];
  folded.forEach((f, i) => { for (const p of f) merged.push(applyTransform(p, perRun[i])); });

  const poly = centerline(merged, bins);
  if (poly.length < 3) return null;

  const lapLen = arcLen(poly);
  const node: GraphNode = { id: 0, x: poly[0][0], y: poly[0][1], progress: 0 };
  const edge: GraphEdge = { id: 0, a: 0, b: 0, poly, arcLen: lapLen, pLo: 0, pHi: 1, kind: 'main', passThrough: null };
  const graph: CourseGraph = { version: 1, startNode: 0, lapLengthPx: lapLen, nodes: [node], edges: [edge], status: 'centerline' };

  // Per-player transform = mean of that player's runs' transforms.
  const byPlayer = new Map<number, { dx: number; dy: number; n: number }>();
  runs.forEach((r, i) => {
    const cur = byPlayer.get(r.playerId) ?? { dx: 0, dy: 0, n: 0 };
    cur.dx += perRun[i].dx; cur.dy += perRun[i].dy; cur.n++; byPlayer.set(r.playerId, cur);
  });
  const alignments = [...byPlayer.entries()].map(([playerId, v]) =>
    ({ playerId, transform: { dx: v.dx / v.n, dy: v.dy / v.n, scale: 1 } as Transform }));

  return { graph, alignments };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/progress/build.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/progress/build.ts pi/src/progress/build.test.ts
git commit -m "feat(progress): centerline builder + per-player alignment"
```

---

## Task 6: Storage — course_models + player_alignment repo

**Files:**
- Create: `pi/src/db/courseModels.ts`
- Create: `pi/src/db/courseModels.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// pi/src/db/courseModels.test.ts
import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from './connect';
import { saveCourseModel, loadCourseModel, savePlayerAlignment, loadPlayerAlignment } from './courseModels';
import type { CourseGraph } from '../progress/types';

function db() {
  const d = new DatabaseSync(':memory:'); applySchema(d);
  d.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'s',1)");
  d.exec("INSERT INTO players(id,display_name) VALUES (1,'P')");
  d.exec("INSERT INTO courses(id,slug,display_name) VALUES (5,'bc','Bowsers Castle')");
  return d;
}
const G: CourseGraph = { version: 1, startNode: 0, lapLengthPx: 100, status: 'centerline',
  nodes: [{ id: 0, x: 0, y: 0, progress: 0 }],
  edges: [{ id: 0, a: 0, b: 0, poly: [[0, 0], [10, 0], [0, 0]], arcLen: 20, pLo: 0, pHi: 1, kind: 'main', passThrough: null }] };

describe('courseModels repo', () => {
  it('round-trips a model and upserts on rebuild', () => {
    const d = db();
    saveCourseModel(d, 5, 150, G, 3);
    expect(loadCourseModel(d, 5, 150)!.lapLengthPx).toBe(100);
    saveCourseModel(d, 5, 150, { ...G, lapLengthPx: 222 }, 4);   // replace
    expect(loadCourseModel(d, 5, 150)!.lapLengthPx).toBe(222);
    expect(loadCourseModel(d, 99, 150)).toBeNull();
  });

  it('round-trips alignment; missing -> identity', () => {
    const d = db();
    expect(loadPlayerAlignment(d, 1)).toEqual({ dx: 0, dy: 0, scale: 1 });   // identity default
    savePlayerAlignment(d, 1, { dx: -6, dy: 2, scale: 1 }, 3);
    expect(loadPlayerAlignment(d, 1)).toEqual({ dx: -6, dy: 2, scale: 1 });
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/db/courseModels.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the repo**

```ts
// pi/src/db/courseModels.ts
import type { DatabaseSync } from 'node:sqlite';
import type { CourseGraph, Transform } from '../progress/types';

export function saveCourseModel(db: DatabaseSync, courseId: number, cc: number, g: CourseGraph, sourceRuns: number): void {
  db.prepare(
    `INSERT INTO course_models(course_id, cc, model_json, lap_length_px, status, source_run_count, version, built_at)
     VALUES (?,?,?,?,?,?,?, datetime('now'))
     ON CONFLICT(course_id, cc) DO UPDATE SET
       model_json=excluded.model_json, lap_length_px=excluded.lap_length_px, status=excluded.status,
       source_run_count=excluded.source_run_count, version=excluded.version, built_at=excluded.built_at`
  ).run(courseId, cc, JSON.stringify(g), g.lapLengthPx, g.status, sourceRuns, g.version);
}

export function loadCourseModel(db: DatabaseSync, courseId: number, cc: number): CourseGraph | null {
  const r = db.prepare('SELECT model_json FROM course_models WHERE course_id=? AND cc=?').get(courseId, cc) as { model_json: string } | undefined;
  return r ? (JSON.parse(r.model_json) as CourseGraph) : null;
}

export function savePlayerAlignment(db: DatabaseSync, playerId: number, t: Transform, sampleCount: number): void {
  db.prepare(
    `INSERT INTO player_alignment(player_id, dx, dy, scale, updated_at, sample_count)
     VALUES (?,?,?,?, datetime('now'), ?)
     ON CONFLICT(player_id) DO UPDATE SET dx=excluded.dx, dy=excluded.dy, scale=excluded.scale,
       updated_at=excluded.updated_at, sample_count=excluded.sample_count`
  ).run(playerId, t.dx, t.dy, t.scale, sampleCount);
}

export function loadPlayerAlignment(db: DatabaseSync, playerId: number): Transform {
  const r = db.prepare('SELECT dx, dy, scale FROM player_alignment WHERE player_id=?').get(playerId) as Transform | undefined;
  return r ?? { dx: 0, dy: 0, scale: 1 };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/db/courseModels.test.ts`
Expected: PASS — 2 tests.

- [ ] **Step 5: Commit**

```bash
git add pi/src/db/courseModels.ts pi/src/db/courseModels.test.ts
git commit -m "feat(progress): course_models + player_alignment repo"
```

---

## Task 7: Projector — `prepareEdges` + `projectStep`

**Files:**
- Create: `pi/src/progress/project.ts`
- Create: `pi/src/progress/project.test.ts`

- [ ] **Step 1: Write the failing tests**

```ts
// pi/src/progress/project.test.ts
import { describe, it, expect } from 'vitest';
import { projectStep, prepareEdges } from './project';
import type { CourseGraph, ProjState } from './types';

// Unit square loop as a single cyclic centerline edge, progress = arc fraction.
const SQUARE: [number, number][] = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
const G: CourseGraph = { version: 1, startNode: 0, lapLengthPx: 40, status: 'centerline',
  nodes: [{ id: 0, x: 0, y: 0, progress: 0 }],
  edges: [{ id: 0, a: 0, b: 0, poly: SQUARE, arcLen: 40, pLo: 0, pHi: 1, kind: 'main', passThrough: null }] };

const obs = (x: number, y: number, lap = 1, t = 0, stale = false) => ({ x, y, lap, totLap: 3, t, stale });

describe('projectStep', () => {
  it('bootstraps to the nearest progress and advances forward', () => {
    const pe = prepareEdges(G);
    let st: ProjState = null;
    let r = projectStep(st, G, pe, obs(0, 0, 1, 0)); st = r.state;
    expect(r.completion).toBeCloseTo(0, 2);                         // start, lap1/3 -> 0
    r = projectStep(st, G, pe, obs(10, 0, 1, 100)); st = r.state;   // quarter way
    expect(r.completion).toBeCloseTo(0.25 / 3, 2);
  });

  it('does not snap back when the path nears an earlier point (forward window)', () => {
    const pe = prepareEdges(G);
    let st: ProjState = null;
    st = projectStep(st, G, pe, obs(10, 0, 1, 0)).state;           // progress .25
    st = projectStep(st, G, pe, obs(10, 10, 1, 100)).state;        // progress .5
    const r = projectStep(st, G, pe, obs(1, 0, 1, 200));           // near start (.0/1.0) but we're at .5
    expect(r.completion).toBeGreaterThan(0.5 / 3 - 0.02);          // stayed forward, no snap to ~0
  });

  it('seam: lap from HUD makes completion continuous across the line', () => {
    const pe = prepareEdges(G);
    let st: ProjState = { edge: 0, progress: 0.98, x: 0, y: 9, t: 0 };
    const r = projectStep(st, G, pe, obs(0, 0, 2, 100));           // crossed line, HUD lap=2
    expect(r.completion).toBeGreaterThanOrEqual(1 / 3 - 0.02);     // ~ (2-1+0)/3, not dropping below lap1
  });

  it('holds while stale', () => {
    const pe = prepareEdges(G);
    const st: ProjState = { edge: 0, progress: 0.4, x: 4, y: 0, t: 0 };
    expect(projectStep(st, G, pe, obs(9, 9, 1, 50, true)).completion).toBeCloseTo(0.4 / 3, 4);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/progress/project.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the projector**

```ts
// pi/src/progress/project.ts
import type { CourseGraph, GraphEdge, ProjState, Obs } from './types';

const EPS_BACK = 0.004;       // backward tolerance in within-lap progress
const REACH_K = 2.5;          // forward window = K * pixelsMoved / lapLengthPx
const EPS_FWD_MIN = 0.02;     // minimum forward reach in progress
const MATCH_DIST = 60;        // px; nearest edge beyond this -> hold/bootstrap
// staleness is decided by the caller (hub) and passed in as obs.stale.

export interface PreparedEdge { edge: GraphEdge; cumFrac: number[]; }     // arc fraction at each poly vertex
export interface Prepared { edges: PreparedEdge[]; }

export function prepareEdges(g: CourseGraph): Prepared {
  return { edges: g.edges.map((edge) => {
    const cum = [0];
    for (let i = 1; i < edge.poly.length; i++)
      cum.push(cum[i - 1] + Math.hypot(edge.poly[i][0] - edge.poly[i - 1][0], edge.poly[i][1] - edge.poly[i - 1][1]));
    const total = cum[cum.length - 1] || 1;
    return { edge, cumFrac: cum.map((c) => c / total) };
  }) };
}

/** Nearest point on one edge's poly, restricted to progress in [loP, hiP]. */
function nearestOnEdge(pe: PreparedEdge, loP: number, hiP: number, px: number, py: number) {
  const { edge, cumFrac } = pe;
  const span = edge.pHi - edge.pLo || 1;
  let best = Infinity, bestProg = edge.pLo;
  for (let i = 1; i < edge.poly.length; i++) {
    const a = edge.poly[i - 1], b = edge.poly[i];
    const segLoP = edge.pLo + cumFrac[i - 1] * span, segHiP = edge.pLo + cumFrac[i] * span;
    if (Math.max(segLoP, segHiP) < loP || Math.min(segLoP, segHiP) > hiP) continue;
    const dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy;
    let t = L2 > 0 ? ((px - a[0]) * dx + (py - a[1]) * dy) / L2 : 0;
    t = Math.max(0, Math.min(1, t));
    let prog = segLoP + t * (segHiP - segLoP);
    const pc = Math.max(loP, Math.min(hiP, prog));
    if (pc !== prog) { t = segHiP !== segLoP ? Math.max(0, Math.min(1, (pc - segLoP) / (segHiP - segLoP))) : 0; prog = pc; }
    const x = a[0] + t * dx, y = a[1] + t * dy, d = Math.hypot(px - x, py - y);
    if (d < best) { best = d; bestProg = prog; }
  }
  return { dist: best, progress: bestProg };
}

export function projectStep(state: ProjState, g: CourseGraph, pe: Prepared, obs: Obs):
    { state: ProjState; completion: number | null } {
  const laps = obs.totLap > 0 ? obs.totLap : 3;
  const done = (progress: number) => (obs.lap - 1 + progress) / laps;
  if (obs.stale) return { state, completion: state ? done(state.progress) : null };
  if (pe.edges.length === 0) return { state, completion: state ? done(state.progress) : null };

  const tracking = state != null;
  const loP = tracking ? Math.max(0, state!.progress - EPS_BACK) : 0;
  const moved = tracking ? Math.hypot(obs.x - state!.x, obs.y - state!.y) : 0;
  const reach = Math.max(EPS_FWD_MIN, REACH_K * moved / (g.lapLengthPx || 1));
  const hiP = tracking ? Math.min(1, state!.progress + reach) : 1;

  let best = Infinity, bestProg = tracking ? state!.progress : 0;
  for (const e of pe.edges) {
    const r = nearestOnEdge(e, loP, hiP, obs.x, obs.y);
    if (r.dist < best) { best = r.dist; bestProg = r.progress; }
  }
  if (best > MATCH_DIST && tracking) return { state, completion: done(state!.progress) };   // implausible -> hold

  const progress = Math.max(tracking ? state!.progress - EPS_BACK : 0, Math.min(1, bestProg));
  return { state: { edge: 0, progress, x: obs.x, y: obs.y, t: obs.t }, completion: done(progress) };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/progress/project.test.ts`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add pi/src/progress/project.ts pi/src/progress/project.test.ts
git commit -m "feat(progress): forward-window graph projector"
```

---

## Task 8: CLI — build a course model from recent runs

**Files:**
- Create: `pi/src/scripts/buildCourseModel.ts`

- [ ] **Step 1: Implement the CLI** (pattern: `pi/src/scripts/scrapeWr.ts`)

```ts
// pi/src/scripts/buildCourseModel.ts
import { openDb, applySchema } from '../db/connect';
import { activeSeasonId } from '../db/seasons';
import { buildCourseModel } from '../progress/build';
import { saveCourseModel, savePlayerAlignment } from '../db/courseModels';
import type { RunInput } from '../progress/types';

const arg = (k: string, d?: string) => {
  const i = process.argv.indexOf(k); return i >= 0 ? process.argv[i + 1] : d;
};

function main() {
  const db = openDb(process.env.MKW_DB ?? 'mkw.db');
  applySchema(db);
  const slug = arg('--course');
  const cc = Number(arg('--cc', '150'));
  const window = Number(arg('--window', '40'));
  if (!slug) { console.error('usage: build-course-model --course <slug> [--cc 150] [--window 40]'); process.exitCode = 1; return; }

  const course = db.prepare('SELECT id FROM courses WHERE slug=?').get(slug) as { id: number } | undefined;
  if (!course) { console.error(`unknown course: ${slug}`); process.exitCode = 1; return; }
  const season = activeSeasonId(db);

  const runs = db.prepare(
    `SELECT r.id, r.player_id FROM runs r
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.status='finished'
       AND EXISTS (SELECT 1 FROM run_points p WHERE p.run_id=r.id)
     ORDER BY r.id DESC LIMIT ?`).all(season, course.id, cc, window) as { id: number; player_id: number }[];

  const ptsStmt = db.prepare('SELECT t_ms, cx, cy, score, lap FROM run_points WHERE run_id=? ORDER BY t_ms');
  const lapStmt = db.prepare('SELECT lap_time_ms FROM run_laps WHERE run_id=? ORDER BY lap_index');
  const inputs: RunInput[] = runs.map((r) => {
    let c = 0; const cum = (lapStmt.all(r.id) as { lap_time_ms: number }[]).map((l) => (c += l.lap_time_ms));
    return { playerId: r.player_id, lapCumMs: cum,
      points: ptsStmt.all(r.id) as RunInput['points'] };
  });

  const res = buildCourseModel(inputs);
  if (!res) { console.error(`no usable runs for ${slug}`); process.exitCode = 1; return; }
  saveCourseModel(db, course.id, cc, res.graph, inputs.length);
  for (const a of res.alignments) savePlayerAlignment(db, a.playerId, a.transform, 1);
  console.log(`[course-model] ${slug} cc${cc}: ${res.graph.status}, ${res.graph.edges[0].poly.length} pts, ${inputs.length} runs, lapLen=${res.graph.lapLengthPx.toFixed(0)}px`);
}
main();
```

- [ ] **Step 2: Add an npm script**

In `pi/package.json` `scripts`, add: `"build-course-model": "node --experimental-strip-types src/scripts/buildCourseModel.ts"` (match the runner form used by the existing `scrape-wr` script — copy its exact `node`/`tsx` invocation).

- [ ] **Step 3: Smoke-run against the real DB** (manual; not a unit test)

Run: `cd pi && MKW_DB=mkw.db npm run build-course-model -- --course bowsers_castle`
Expected: prints `centerline, <N> pts, <M> runs, lapLen=<...>px`; a `course_models` row exists for bowsers_castle.

- [ ] **Step 4: Commit**

```bash
git add pi/src/scripts/buildCourseModel.ts pi/package.json
git commit -m "feat(progress): build-course-model CLI"
```

---

## Task 9: Live swap — `makeLiveCompletion` uses the model

**Files:**
- Modify: `pi/src/presence/completion.ts`
- Modify: `pi/src/presence/completion.test.ts`

- [ ] **Step 1: Update the live-completion tests**

Replace the body of `pi/src/presence/completion.test.ts` setup so a course model exists, then assert projection. Key cases (keep the existing `db()` helper that runs `applySchema`; add a model + a run):

```ts
import { saveCourseModel } from '../db/courseModels';
import type { CourseGraph } from '../progress/types';

const SQUARE: [number, number][] = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
const MODEL: CourseGraph = { version: 1, startNode: 0, lapLengthPx: 40, status: 'centerline',
  nodes: [{ id: 0, x: 0, y: 0, progress: 0 }],
  edges: [{ id: 0, a: 0, b: 0, poly: SQUARE, arcLen: 40, pLo: 0, pHi: 1, kind: 'main', passThrough: null }] };

// in the test that builds `db`: resolve the course id used by makeLiveCompletion's slug lookup,
// then: saveCourseModel(d, <courseId>, 150, MODEL, 1);

it('projects live position onto the course model, monotonic per lap', () => {
  const live = makeLiveCompletion(db());
  // course 'Bowsers Castle' resolves via slug; lap 1 of 3; quarter way -> 0.25/3
  expect(live('Bowsers Castle', 1, [10, 0], 1, 1000, false)).toBeCloseTo(0.25 / 3, 2);
  expect(live('Bowsers Castle', 1, [10, 10], 1, 1100, false)).toBeCloseTo(0.5 / 3, 2);
});

it('holds completion while stale', () => {
  const live = makeLiveCompletion(db());
  live('Bowsers Castle', 1, [10, 0], 1, 1000, false);
  expect(live('Bowsers Castle', 1, [0, 0], 1, 1100, true)).toBeCloseTo(0.25 / 3, 2);  // held
});
```

(Match the existing helper's course/slug seeding — the prior tests used `Bowsers Castle`; reuse `courseIdBySlug(db, slugify('Bowsers Castle'))` to get the id for `saveCourseModel`.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd pi && npx vitest run src/presence/completion.test.ts`
Expected: FAIL — old `makeLiveCompletion` ignores the model / uses the retired projector.

- [ ] **Step 3: Rewrite `makeLiveCompletion`**

```ts
// pi/src/presence/completion.ts
import type { DatabaseSync } from 'node:sqlite';
import { loadCourseModel, loadPlayerAlignment } from '../db/courseModels';
import { prepareEdges, projectStep, type Prepared } from '../progress/project';
import type { CourseGraph, ProjState } from '../progress/types';
import { courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';

export type LiveCompletion = (
  course: string | null | undefined, curLap: number | null | undefined, pos: [number, number] | null | undefined,
  playerId?: number, t?: number, stale?: boolean, totLap?: number,
) => number | null;

export function makeLiveCompletion(db: DatabaseSync, cc = 150): LiveCompletion {
  const modelCache = new Map<number, { g: CourseGraph; pe: Prepared } | null>();
  const pstate = new Map<number, { st: ProjState; course: number; lap: number }>();
  return (course, curLap, pos, playerId, t = Date.now(), stale = false, totLap = 3) => {
    if (!course || !pos) { if (playerId != null) pstate.delete(playerId); return null; }
    const courseId = courseIdBySlug(db, slugify(course));
    if (courseId == null) return null;
    let entry = modelCache.get(courseId);
    if (entry === undefined) {
      const g = loadCourseModel(db, courseId, cc);
      entry = g ? { g, pe: prepareEdges(g) } : null;
      modelCache.set(courseId, entry);
    }
    if (!entry) return null;
    const lap = curLap ?? 1;
    const al = playerId != null ? loadPlayerAlignment(db, playerId) : { dx: 0, dy: 0, scale: 1 };
    const x = pos[0] * al.scale + al.dx, y = pos[1] * al.scale + al.dy;
    let ps = playerId != null ? pstate.get(playerId) : undefined;
    if (ps && (ps.course !== courseId || lap < ps.lap)) ps = undefined;          // new run -> reset
    const r = projectStep(ps?.st ?? null, entry.g, entry.pe, { x, y, lap, totLap, t, stale });
    if (playerId != null) pstate.set(playerId, { st: r.state, course: courseId, lap });
    return r.completion;
  };
}
```

- [ ] **Step 4: Check the hub passes `totLap`**

In `pi/src/presence/hub.ts`, the `completion(...)` call (added in the earlier presence work) passes `(frame.course, frame.cur_lap, frame.pos, playerId, now, stale)`. Append `frame.tot_lap` as the 7th arg:

```ts
completion: this.completion(frame.course, frame.cur_lap, frame.pos, playerId, now, stale, frame.tot_lap),
```

Confirm `PresenceFrame` has `tot_lap` (it does, from `src/lib/presence.js` `frame()`); if the type lacks it, add `tot_lap?: number | null` next to `cur_lap`.

- [ ] **Step 5: Run to verify they pass**

Run: `cd pi && npx vitest run src/presence/`
Expected: PASS — new completion cases + existing hub tests (the hub test doubles pass `pos` as the 3rd arg, unaffected).

- [ ] **Step 6: Commit**

```bash
git add pi/src/presence/completion.ts pi/src/presence/completion.test.ts pi/src/presence/hub.ts
git commit -m "feat(presence): live completion projects onto the course model"
```

---

## Final verification

- [ ] **Full pi suite:** `cd pi && npm test` — all green. (The retired single-run `progress.ts`/`completion.ts` `courseReference` path is now unused by the live hub; the `avg_completion_before_reset` stat still uses it and stays green — it migrates to the model in a later plan.)
- [ ] **Type check (frontend unaffected):** `npx svelte-check` (repo root) — 0/0.
- [ ] **Real-data smoke:** rebuild `bowsers_castle` (Task 8 Step 3), restart the pi server, and confirm the live yellow debug % rises monotonically 0→~33% across lap 1 with no snap at the self-crossing or the line.

---

## Notes for the next plan (Plan 2 — full graph)

- Replace `centerline()` with density-raster → Zhang–Suen thinning → graph extraction → junction classification (branch vs pass-through), emitting `status='graph'` with multiple edges. `projectStep`/`prepareEdges` already consume a multi-edge graph unchanged.
- Add the debounced rebuild-on-ingest trigger (`PROGRESS_MODEL_DEBOUNCE_MS`) in the server ingest path.
- Migrate `avg_completion_before_reset` (`resolveCompletion`) to replay through `projectStep`; then delete the retired `prepareReference`/`step`/`clipTeleports` and the single-run `courseReference` path.
- Add the engine per-point `lap` stamp (`mkw_tracker/minimap/recorder.py` + Rust payload + `pi/src/db/ingest.ts`) so new data anchors exactly.
- Promote constants in `project.ts`/`build.ts` to config keys (`PROGRESS_*`) per spec §11.

---

## Self-Review (author checklist — completed)

**Spec coverage:** §4 schema (run_points.lap, course_models, player_alignment) → Task 2, 6. §5 builder (fold/f, align, density→centerline fallback) → Tasks 3-5 (centerline path; full graph deferred to Plan 2 per the agreed phasing). §6 CourseGraph format → Task 1. §7 projector (forward window, stale-hold, monotonic, seam via HUD lap) → Task 7, 9. §8 cadence → CLI now (Task 8); debounced trigger deferred to Plan 2. §9 consumers: live hub → Task 9; reset stat + retirement deferred to Plan 2 (noted). §10 engine lap stamp → deferred to Plan 2 (builder uses time fallback meanwhile, Task 3). §12 tests → each task. This plan is the agreed Phase-1 slice; deferrals are explicit in "Notes for the next plan".

**Placeholder scan:** none — every code/command step is concrete.

**Type consistency:** `CourseGraph`/`GraphEdge`/`Transform`/`ProjState`/`Obs` (Task 1) are used identically in `build.ts`, `project.ts`, `courseModels.ts`, `completion.ts`. `projectStep(state, g, pe, obs)` and `prepareEdges(g)` signatures match across Tasks 7 and 9. `makeLiveCompletion` gains an append-only trailing `totLap?` arg, so existing hub test doubles keep working.

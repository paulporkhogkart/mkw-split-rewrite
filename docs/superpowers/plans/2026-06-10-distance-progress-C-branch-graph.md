# Distance Progress v2 — Plan C: Branch-Aware Per-Lap Graph (CV)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development per module. Executed inline (the pipeline is tightly coupled + needs empirical iteration against synthetic shapes, so subagent task-isolation doesn't fit). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace each lap's single averaged centerline with a branch-aware **graph** built from the pooled trail, so split paths (shortcut vs non-shortcut runs, genuine forks) become distinct edges instead of one wrong middle line. The forward-window projector already consumes multi-edge graphs unchanged — this is purely a *builder* change with a centerline fallback.

**Architecture:** Per lap index, pool the runs' lap-k points (already aligned + f-stamped), then: score-weighted **density raster** → **threshold** → **Zhang–Suen thinning** (1px skeleton) → **graph extraction** (endpoints/junctions = nodes, traced pixel chains = edges) → **progress + classification** from the per-point f-stamp → `CourseGraph` (`status='graph'`). Degenerate skeleton → fall back to the existing f-bin centerline (`status='centerline'`).

**Tech Stack:** TypeScript (pi), pure (no opencv). Spec: `docs/superpowers/specs/2026-06-10-distance-progress-model-design.md` §4.

**Conventions:** pi tests from `pi/`. Commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## Why f is the spine

`f` (a point's within-lap time fraction, from `groupByLap`) only ever increases as you drive a lap, so the **f-ordered sequence of positions is the true route order**. The whole graph's progress comes from f: each edge's `[pLo, pHi]` is the f-range of the trail under it. That makes the labels (`kind`/`passThrough`) mostly cosmetic — correctness lives in the f-ranges, which the projector turns into a monotonic %:

- **Branch** (fork that rejoins): two edges cover the **same** f-interval → both get `[a, c]` → projector picks the nearest → same % on either path. `kind='branch'`.
- **Self-crossing** (track crosses itself): two strands sit on the same pixel but at **different** f (e.g. 20% and 70%) → `[a,b]` and `[c,d]` disjoint → the forward window never jumps between them. Paired via `passThrough`.

So the build must get f-ranges right; the classification is a thin overlap rule on top.

---

## File Structure
- **Create** `pi/src/progress/raster.ts` — `rasterize(pts, opts) -> Raster` (score-weighted splat grid + per-cell mean-f) + `cellXY`.
- **Create** `pi/src/progress/skeleton.ts` — `threshold(r, frac) -> Uint8Array` + `zhangSuen(bin, w, h) -> Uint8Array`.
- **Create** `pi/src/progress/graphExtract.ts` — `extractGraph(skel, w, h) -> { nodes, edges }` (raw pixel graph: nodes at degree≠2, edges = traced chains).
- **Create** `pi/src/progress/lapGraphCV.ts` — `buildLapGraphCV(pts, opts) -> CourseGraph | null` (ties raster→skeleton→extract→progress/classify into a `CourseGraph`; null on degenerate).
- **Modify** `pi/src/progress/build.ts` — per lap: try `buildLapGraphCV`; fall back to the existing `centerline`→`lapGraph`. Model `status='graph'` only if every lap built a graph.
- **Test** one `*.test.ts` beside each new module + extend `build.test.ts`.

---

## Task 1: Density raster

**Files:** `pi/src/progress/raster.ts`, `pi/src/progress/raster.test.ts`

`Raster` = `{ w, h, ox, oy, cell, wGrid: Float32Array, fGrid: Float32Array }`. `wGrid[j*w+i]` = Σ score splatted into cell (i,j); `fGrid` = Σ f·score (so mean-f = fGrid/wGrid). Course→cell: `i=(x-ox)/cell`, `j=(y-oy)/cell`. `cellXY(r,i,j)=[ox+(i+0.5)·cell, oy+(j+0.5)·cell]`.

- [ ] **Step 1: Test** — points on a known bbox land in expected cells; a splat radius makes a single point fill a small disk; mean-f recovered.

```ts
import { describe, it, expect } from 'vitest';
import { rasterize, cellXY } from './raster';
import type { FoldPt } from './build';

const P = (x:number,y:number,f:number):FoldPt => ({ x, y, f, score: 1 });

describe('rasterize', () => {
  it('maps points into cells and recovers mean-f, round-tripping cell centres', () => {
    const pts = [P(0,0,0), P(10,0,0.5), P(10,10,1)];
    const r = rasterize(pts, { grid: 16, splatR: 0 })!;
    expect(r.w).toBeGreaterThan(0); expect(r.h).toBeGreaterThan(0);
    // the cell nearest (10,0) carries f≈0.5
    const ci = Math.round((10 - r.ox)/r.cell - 0.5), cj = Math.round((0 - r.oy)/r.cell - 0.5);
    const idx = cj*r.w + ci;
    expect(r.wGrid[idx]).toBeGreaterThan(0);
    expect(r.fGrid[idx]/r.wGrid[idx]).toBeCloseTo(0.5, 5);
    const [bx,by] = cellXY(r, ci, cj);
    expect(Math.hypot(bx-10, by-0)).toBeLessThan(r.cell);   // round-trips within a cell
  });

  it('splat radius fills neighbouring cells (connectivity)', () => {
    const r = rasterize([P(5,5,0.5)], { grid: 16, splatR: 1 })!;
    const n = r.wGrid.reduce((a,v)=>a+(v>0?1:0),0);
    expect(n).toBeGreaterThanOrEqual(5);   // centre + 4-neighbourhood at least
  });

  it('returns null with <2 distinct points', () => { expect(rasterize([P(1,1,0)], {})).toBeNull(); });
});
```

- [ ] **Step 2** run (fail) → **Step 3** implement → **Step 4** run (pass) → **Step 5** commit `feat(progress): score-weighted density raster`.

Implementation notes: bbox over pts (+1 cell margin); `cell = max(maxDim/grid, 1e-6)`; `grid` default 96, `splatR` default 1. Splat each point into the disk `|di|,|dj|<=splatR` adding `score` to `wGrid` and `f·score` to `fGrid`. Null if bbox degenerate (w·h<4 or <2 distinct points).

---

## Task 2: Threshold + Zhang–Suen thinning

**Files:** `pi/src/progress/skeleton.ts`, `pi/src/progress/skeleton.test.ts`

- [ ] **Step 1: Test** — a filled rectangle thins to a 1px medial line (interior cleared, connected); a plus/cross keeps its centre as a junction; thresholding drops sub-fraction cells.

```ts
import { describe, it, expect } from 'vitest';
import { threshold, zhangSuen } from './skeleton';

function grid(rows: string[]) {                  // '#'=1
  const h = rows.length, w = rows[0].length, b = new Uint8Array(w*h);
  rows.forEach((r,j)=>[...r].forEach((c,i)=>{ if (c==='#') b[j*w+i]=1; }));
  return { b, w, h };
}
const count = (a: Uint8Array) => a.reduce((s,v)=>s+v,0);

describe('zhangSuen', () => {
  it('thins a filled block toward a 1px skeleton (fewer set pixels, still connected)', () => {
    const { b, w, h } = grid(['........','.######.','.######.','.######.','.######.','........']);
    const sk = zhangSuen(b, w, h);
    expect(count(sk)).toBeLessThan(count(b));
    expect(count(sk)).toBeGreaterThan(0);
    // no 2x2 block remains in a proper skeleton
    let block = false;
    for (let j=0;j<h-1;j++) for (let i=0;i<w-1;i++)
      if (sk[j*w+i]&&sk[j*w+i+1]&&sk[(j+1)*w+i]&&sk[(j+1)*w+i+1]) block = true;
    expect(block).toBe(false);
  });
});

describe('threshold', () => {
  it('keeps cells above frac*max', () => {
    const r:any = { w:3, h:1, wGrid: Float32Array.from([10, 1, 0]) };
    const bin = threshold(r, 0.2);
    expect([...bin]).toEqual([1, 0, 0]);
  });
});
```

- [ ] **Step 2** run (fail) → **Step 3** implement (standard Zhang–Suen: repeat two sub-iterations marking deletable border pixels by the B(P)/A(P) conditions until stable) → **Step 4** run (pass) → **Step 5** commit `feat(progress): threshold + Zhang–Suen thinning`.

---

## Task 3: Graph extraction

**Files:** `pi/src/progress/graphExtract.ts`, `pi/src/progress/graphExtract.test.ts`

`extractGraph(skel,w,h) -> { nodes: {id,ci,cj}[], edges: {id,a,b,cells:[i,j][]}[] }`. Node pixels = 8-neighbour degree ≠ 2 (endpoints=1, junctions≥3); a pure loop with no such pixel seeds one node. Trace each edge from a node through degree-2 pixels to the next node; `cells` is the ordered pixel chain (inclusive of both node pixels). Merge nodes within 2 cells.

- [ ] **Step 1: Test** — a straight line → 1 edge + 2 endpoint nodes; a Y → 1 junction (deg 3) + 3 edges; a plain ring → 1 seed node + 1 cyclic edge.

```ts
import { describe, it, expect } from 'vitest';
import { extractGraph } from './graphExtract';

function grid(rows: string[]) {
  const h=rows.length, w=rows[0].length, b=new Uint8Array(w*h);
  rows.forEach((r,j)=>[...r].forEach((c,i)=>{ if(c==='#') b[j*w+i]=1; }));
  return { b, w, h };
}

describe('extractGraph', () => {
  it('a straight line -> one edge, two endpoints', () => {
    const { b,w,h } = grid(['.....','#####','.....']);
    const g = extractGraph(b,w,h);
    expect(g.nodes.length).toBe(2);
    expect(g.edges.length).toBe(1);
    expect(g.edges[0].cells.length).toBe(5);
  });

  it('a Y -> a degree-3 junction and three edges', () => {
    const { b,w,h } = grid([
      '#...#',
      '.#.#.',
      '..#..',
      '..#..']);
    const g = extractGraph(b,w,h);
    const deg = new Map<number,number>();
    for (const e of g.edges) { deg.set(e.a,(deg.get(e.a)??0)+1); deg.set(e.b,(deg.get(e.b)??0)+1); }
    expect(Math.max(...deg.values())).toBe(3);
    expect(g.edges.length).toBe(3);
  });
});
```

- [ ] **Step 2** run (fail) → **Step 3** implement → **Step 4** run (pass) → **Step 5** commit `feat(progress): skeleton graph extraction`.

---

## Task 4: Lap graph (progress + classification)

**Files:** `pi/src/progress/lapGraphCV.ts`, `pi/src/progress/lapGraphCV.test.ts`

`buildLapGraphCV(pts: FoldPt[], opts?) -> CourseGraph | null`. Pipeline: `rasterize` → `threshold` → `zhangSuen` → `extractGraph`. Then build the `CourseGraph`:

- Each raw edge → course-coord polyline via `cellXY`; `arcLen` from the polyline; decimate to ≤ ~40 verts. **f-range:** mean-f of each chain cell (`fGrid/wGrid`); orient low→high; `pLo=min`, `pHi=max` (clamped [0,1]).
- Nodes: `cellXY`; `progress` = mean of incident edge endpoints' f at that node. `startNode` = node with min progress (lap start).
- **kind:** `'branch'` if an edge's f-range overlaps another edge's by ≥ `BRANCH_OVERLAP` (default 0.5 of the shorter range); else `'main'`.
- **passThrough:** for an edge sharing a node with another whose f-range is **disjoint** (gap > `CROSS_GAP`, default 0.1), pair their ids (nearest such); else `null`.
- `lapLengthPx`: arc length of the f-ordered **main** spine (chain the `'main'` edges by ascending f covering [0,1]); `graph.lapLengthPx` = that. Return `null` if the skeleton is degenerate (no edges, or the union of edge f-ranges leaves a gap > `COVER_GAP`=0.25 in [0,1]) so the caller falls back to the centerline.

- [ ] **Step 1: Test** — three synthetic cases, asserting f-range semantics (the part that drives correctness):

```ts
import { describe, it, expect } from 'vitest';
import { buildLapGraphCV } from './lapGraphCV';
import type { FoldPt } from './build';

// helper: a dense poly of FoldPts with f rising 0->1 along the given course points
function trail(xy: [number,number][], f0=0, f1=1, score=1): FoldPt[] {
  return xy.map((p,k)=>({ x:p[0], y:p[1], f: f0 + (f1-f0)*k/(xy.length-1), score }));
}

describe('buildLapGraphCV', () => {
  it('a simple loop builds a single-spine graph covering [0,1]', () => {
    const pts: FoldPt[] = [];
    for (let k=0;k<400;k++){ const a=2*Math.PI*k/400; pts.push({ x:50+40*Math.cos(a), y:50+40*Math.sin(a), f:k/400, score:1 }); }
    const g = buildLapGraphCV(pts, { grid: 64 });
    expect(g).not.toBeNull();
    const lo = Math.min(...g!.edges.map(e=>e.pLo)), hi = Math.max(...g!.edges.map(e=>e.pHi));
    expect(lo).toBeLessThan(0.1); expect(hi).toBeGreaterThan(0.9);
  });

  it('a fork that rejoins yields two edges sharing a progress interval (branch)', () => {
    // common start 0->0.4, then two arcs 0.4->0.7 (upper & lower), rejoined 0.7->1
    const start = trail([[0,0],[10,0],[20,0],[30,0]], 0, 0.4);
    const upper = trail([[30,0],[40,-8],[50,-8],[60,0]], 0.4, 0.7);
    const lower = trail([[30,0],[40,8],[50,8],[60,0]], 0.4, 0.7);
    const end   = trail([[60,0],[70,0],[80,0],[90,0]], 0.7, 1);
    const dense = (t:FoldPt[]) => t.flatMap((p,i,a)=> i? interp(a[i-1],p,6):[p]);   // densify for raster
    const pts = [start,upper,lower,end].flatMap(dense);
    const g = buildLapGraphCV(pts, { grid: 96, splatR: 1 });
    expect(g).not.toBeNull();
    const branchy = g!.edges.filter(e => e.kind === 'branch');
    expect(branchy.length).toBeGreaterThanOrEqual(2);
    // the two branch edges overlap in progress (both ~[0.4,0.7])
    const [b1,b2] = branchy;
    expect(Math.min(b1.pHi,b2.pHi) - Math.max(b1.pLo,b2.pLo)).toBeGreaterThan(0.1);
  });

  it('falls back (null) on a degenerate trail', () => {
    expect(buildLapGraphCV([{x:0,y:0,f:0,score:1},{x:0.1,y:0,f:1,score:1}], {})).toBeNull();
  });
});

function interp(a:FoldPt,b:FoldPt,n:number):FoldPt[] {
  return Array.from({length:n},(_,k)=>{ const t=(k+1)/n; return { x:a.x+(b.x-a.x)*t, y:a.y+(b.y-a.y)*t, f:a.f+(b.f-a.f)*t, score:1 }; });
}
```

- [ ] **Step 2** run (fail) → **Step 3** implement → **Step 4** run (pass) → **Step 5** commit `feat(progress): per-lap CV graph with f-progress + branch/crossing tags`.

(If the branch test proves finicky on raster resolution, tune `grid`/`splatR` in the test — the goal is the f-range *semantics*, not exact pixel counts.)

---

## Task 5: Integrate into the builder + real-data smoke

**Files:** `pi/src/progress/build.ts`, `pi/src/progress/build.test.ts`

- [ ] **Step 1: Test** — `buildCourseModel` still builds the uneven 2-lap fixture (now possibly via CV) with correct cumulative offsets; a `status` field present; existing tests stay green.

Add to `build.test.ts`:
```ts
it('produces a graph-or-centerline status and keeps cumulative offsets', () => {
  const res = buildCourseModel([unevenRun(1), unevenRun(2)], { bins: 64, grid: 64 });
  expect(res).not.toBeNull();
  const m = res!.model;
  expect(['graph','centerline']).toContain(m.status);
  expect(m.laps[1].startOffsetPx).toBeCloseTo(m.laps[0].lengthPx, 5);
  expect(m.totalLengthPx).toBeCloseTo(m.laps[0].lengthPx + m.laps[1].lengthPx, 5);
});
```

- [ ] **Step 2: Implement.** In `buildCourseModel`, per lap k: build `merged` (as now), then `const g = buildLapGraphCV(merged, { grid: opts.grid, splatR: opts.splatR })`. If `g` → `lengthPx = g.lapLengthPx`, `graph = g`. Else → existing `centerline`→`lapGraph` (set a `usedCenterline` flag). After the loop, `model.status = usedCenterline ? 'centerline' : 'graph'`. Keep `startOffsetPx`/`totalLengthPx` accumulation identical.

- [ ] **Step 3** run (fail→pass).

- [ ] **Step 4: Real-data smoke** — rebuild bowsers_castle through the new path and confirm it still produces a sane 3-lap model:

Run: `cd pi && npx tsx src/scripts/buildCourseModel.ts --course bowsers_castle` (or the existing `npm run build-course-model -- --course bowsers_castle`).
Expected: logs `graph|centerline, 3 laps, total≈3477px` (graph or centerline; total within ~25% of the centerline build — a structural sanity check, not exact). If it errors or laps≠3, STOP and diagnose before merge.

- [ ] **Step 5: Commit** `feat(progress): builder uses the per-lap CV graph, centerline fallback`.

---

## Final verification
- [ ] **pi suite:** `cd pi && npm test` — green.
- [ ] **Type check:** `npx svelte-check` (root) — 0/0.
- [ ] **Projector unaffected:** existing `project.test.ts` green (it already iterates multi-edge graphs; confirm a branched graph projects to the same % on either branch — add a small case if not covered).

---

## Notes / honest limits
- **Real branch validation needs the user's live test** on a split-path course; synthetic Y/X + the bowsers_castle smoke are the automated coverage. Mark this in the merge summary.
- **Classification is pragmatic** (overlap/gap rules on f-ranges). The f-ranges — which actually drive the projector — are the rigorous part; `kind`/`passThrough` are metadata the projector doesn't yet branch on.
- **YAGNI honoured:** no de-dup of identical laps; no per-branch length reconciliation beyond picking the main spine for `lapLengthPx`.

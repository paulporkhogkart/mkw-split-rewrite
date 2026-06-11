# Progress Reset + Projector v2.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wipe all test runs/models, make models auto-rebuild on upload (with live cache invalidation), and give the projector a self-paced reach + bounded glide, tuned against clip-derived trails.

**Architecture:** Per the spec `docs/superpowers/specs/2026-06-11-progress-reset-projector-v21-design.md`. All server changes in `pi/src/` (node:sqlite + hono + vitest); the trail generator is an engine-side temp script reusing production components.

**Tech Stack:** TypeScript (pi), Python (trail generation), vitest, pytest.

**Repo rules:** branch `progress-reset-v21`; stage files explicitly; ff-merge at the end. pi tests: `cd pi; npx vitest run`.

---

### Task 1: Branch + docs

- [ ] ```bash
git checkout -b progress-reset-v21
git add docs/superpowers/specs/2026-06-11-progress-reset-projector-v21-design.md docs/superpowers/plans/2026-06-11-progress-reset-projector-v21.md
git commit -m "docs(progress): reset + projector v2.1 spec/plan

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Wipe script (+ execute)

**Files:** Create `pi/src/scripts/wipeRuns.ts`; modify `pi/package.json` (script entry).

- [ ] **Step 1: Script**

```ts
// pi/src/scripts/wipeRuns.ts
// Deletes ALL recorded runs (run_laps/run_points cascade), course models and
// player alignments. Players/seasons/rosters/courses/world_records survive.
// Usage: npm run wipe-runs -- --confirm
import { openDb, applySchema } from '../db/connect';

function count(db: ReturnType<typeof openDb>, table: string): number {
  return (db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get() as { n: number }).n;
}

function main() {
  if (!process.argv.includes('--confirm')) {
    console.error('refusing without --confirm (this deletes ALL runs, models and alignments)');
    process.exitCode = 1;
    return;
  }
  const db = openDb(process.env.MKW_DB ?? 'mkw.db');
  applySchema(db);
  const tables = ['runs', 'run_laps', 'run_points', 'course_models', 'player_alignment'];
  const before = Object.fromEntries(tables.map((t) => [t, count(db, t)]));
  db.exec('BEGIN');
  try {
    db.exec('DELETE FROM runs');             // run_laps + run_points cascade
    db.exec('DELETE FROM course_models');
    db.exec('DELETE FROM player_alignment');
    db.exec('COMMIT');
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }
  for (const t of tables) console.log(`${t}: ${before[t]} -> ${count(db, t)}`);
}
main();
```

package.json scripts: `"wipe-runs": "node --no-warnings --import tsx src/scripts/wipeRuns.ts"`.

- [ ] **Step 2: Execute on the dev DB** (user-authorized destruction)

Run: `cd pi; npm run wipe-runs -- --confirm`
Expected: every listed table -> 0; players/world_records untouched (spot-check
`node -e` count or sqlite3). Record the before-counts for the report.

- [ ] **Step 3: Commit**

```bash
git add pi/src/scripts/wipeRuns.ts pi/package.json
git commit -m "feat(server): wipe-runs script; test corpus deleted for clean rebuild

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Auto-rebuild on upload + cache invalidation (TDD)

**Files:** Modify `pi/src/db/courseModels.ts`, `pi/src/scripts/buildCourseModel.ts`, `pi/src/presence/completion.ts`, `pi/src/api/runs.ts`, `pi/src/server.ts`; tests in `pi/src/db/courseModels.test.ts` + `pi/src/api/runs.test.ts` (or a new `runs.rebuild.test.ts` if none exists).

- [ ] **Step 1: Failing test - rebuildCourseModel builds from db runs**

```ts
// append to pi/src/db/courseModels.test.ts (reuse its in-memory db helper)
import { rebuildCourseModel } from './courseModels';

it('rebuildCourseModel builds + saves from finished runs with points', () => {
  const d = mem();                       // existing helper in this file
  seedSeasonCoursePlayer(d);             // existing/most similar helper; else inline INSERTs
  d.exec(`INSERT INTO runs(id,attempt_id,season_id,player_id,course_id,cc,status,provenance,total_time_ms)
          VALUES (1,'a',1,1,1,150,'finished','live',60000)`);
  d.exec(`INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES (1,1,30000),(1,2,30000)`);
  const pts = d.prepare('INSERT INTO run_points(run_id,t_ms,cx,cy,score,lap) VALUES (1,?,?,?,1.0,?)');
  for (let i = 0; i < 600; i++) {        // two 30s laps around a circle
    const t = i * 100, lap = t < 30000 ? 1 : 2, f = (t % 30000) / 30000;
    pts.run(t, 200 + 100 * Math.cos(2 * Math.PI * f), 200 + 100 * Math.sin(2 * Math.PI * f), lap);
  }
  const res = rebuildCourseModel(d, 1, 150);
  expect(res).not.toBeNull();
  expect(loadCourseModel(d, 1, 150)).not.toBeNull();
});
```

- [ ] **Step 2: Implement** - move the query+build+save block from
`scripts/buildCourseModel.ts` into:

```ts
// pi/src/db/courseModels.ts
import { buildCourseModel } from '../progress/build';
import type { RunInput } from '../progress/types';

/** Rebuild + persist the (course, cc) model from the latest <=window finished
 *  runs that carry points. Returns the saved model summary or null. */
export function rebuildCourseModel(db: DatabaseSync, courseId: number, cc: number,
                                   window = 40): { status: string; laps: number; runs: number } | null {
  const season = activeSeasonId(db);
  const runs = db.prepare(
    `SELECT r.id, r.player_id FROM runs r
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.status='finished'
       AND EXISTS (SELECT 1 FROM run_points p WHERE p.run_id=r.id)
     ORDER BY r.id DESC LIMIT ?`).all(season, courseId, cc, window) as { id: number; player_id: number }[];
  if (runs.length === 0) return null;
  const ptsStmt = db.prepare('SELECT t_ms, cx, cy, score, lap FROM run_points WHERE run_id=? ORDER BY t_ms');
  const lapStmt = db.prepare('SELECT lap_time_ms FROM run_laps WHERE run_id=? ORDER BY lap_index');
  const inputs: RunInput[] = runs.map((r) => {
    let c = 0;
    const cum = (lapStmt.all(r.id) as { lap_time_ms: number }[]).map((l) => (c += l.lap_time_ms));
    return { playerId: r.player_id, lapCumMs: cum, points: ptsStmt.all(r.id) as RunInput['points'] };
  });
  const res = buildCourseModel(inputs);
  if (!res) return null;
  saveCourseModel(db, courseId, cc, res.model, inputs.length);
  for (const a of res.alignments) savePlayerAlignment(db, a.playerId, a.transform, 1);
  return { status: res.model.status, laps: res.model.laps.length, runs: inputs.length };
}
```

(`activeSeasonId` import from `./seasons`.) `scripts/buildCourseModel.ts`
shrinks to arg-parsing + `rebuildCourseModel` + logging.

- [ ] **Step 3: Failing test - cache invalidation**

```ts
// append to pi/src/presence/completion.test.ts (reuse its setup that saves MODEL)
it('invalidate() makes the next call reload the stored model', () => {
  const live = makeLiveCompletion(d);
  expect(live('Test Course', 1, [10, 10], 1).completion).not.toBeNull();
  // replace the stored model with one twice as long, then invalidate
  saveCourseModel(d, id, 150, { ...MODEL, totalLengthPx: MODEL.totalLengthPx * 2 }, 2);
  live.invalidate(id);
  // a fresh projection now uses the new model (completion halves for the same point)
  const after = live('Test Course', 1, [10, 10], 2);   // different player: clean state
  expect(after.completion).not.toBeNull();
  expect(after.completion!).toBeLessThan(0.99);        // and differs from the cached-model value
});
```

- [ ] **Step 4: Implement invalidate**

In `presence/completion.ts`: `export type LiveCompletion = ((...) => LiveResult) & { invalidate(courseId: number): void };`
build the closure as today, then:

```ts
  const fn = ((course, curLap, pos, playerId, t = Date.now(), stale = false, totLap) => {
    /* existing body unchanged */
  }) as LiveCompletion;
  fn.invalidate = (courseId: number) => { modelCache.delete(courseId); };
  return fn;
```

- [ ] **Step 5: Wire the upload hook**

`api/runs.ts`: `runsRoutes(db, hub, invalidateModel?: (courseId: number) => void)`;
after `recomputeWasPb(...)` (finished path only):

```ts
    if ((p.points?.length ?? 0) > 0) {
      const built = rebuildCourseModel(db, courseId, cc);
      if (built) invalidateModel?.(courseId);
    }
```

`server.ts`: `const live = makeLiveCompletion(db); const presence = new PresenceHub(db, live);`
and pass `live.invalidate` where `runsRoutes(db, hub)` is mounted.

- [ ] **Step 6: pi suite**

Run: `cd pi; npx vitest run` -> green.

- [ ] **Step 7: Commit**

```bash
git add pi/src/db/courseModels.ts pi/src/db/courseModels.test.ts pi/src/scripts/buildCourseModel.ts pi/src/presence/completion.ts pi/src/presence/completion.test.ts pi/src/api/runs.ts pi/src/server.ts
git commit -m "feat(progress): models auto-rebuild on finished uploads; live cache invalidation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Projector v2.1 (TDD)

**Files:** Modify `pi/src/progress/project.ts`, `pi/src/progress/types.ts`; tests append to `pi/src/progress/project.test.ts`.

- [ ] **Step 1: Failing tests** (use the straight-line model helper already in
project.test.ts; positions in px along a known-length line make expected
progress exact)

```ts
describe('v2.1 pace + glide', () => {
  it('learns a rate and widens reach by time', () => {
    // step 1%/100ms three times -> rate ~1e-4/ms; then a 500ms gap with a
    // position 4% ahead (beyond EPS_FWD_MIN + movement reach for a tiny move)
    // must still be reached via the time term.
  });
  it('glides on stale at the learned rate, capped at GLIDE_MAX_MS', () => {
    // establish rate, then stale frames at +500ms/+1000ms/+5000ms:
    // completion advances ~rate*dt for the first two, clamps at rate*2000
    // for the third; state.progress (anchor) unchanged -> a fresh confident
    // obs near the anchor re-projects without a jump.
  });
  it('freezes on stale when no rate is established (legacy behaviour)', () => {});
});
```

(Write them as real assertions against `projectStep` with hand-built obs
sequences; the existing tests in this file show the model/obs scaffolding to
copy. Each test asserts exact `completion` values within 1e-3.)

- [ ] **Step 2: Implement**

`types.ts`: `ProjState = { edge: number; progress: number; x: number; y: number; t: number; rate: number | null } | null;`

`project.ts`:

```ts
const RATE_ALPHA = 0.2;       // EMA on within-course progress per ms
const K_T = 2.5;              // forward reach = K_T * rate * dt
const GLIDE_MAX_MS = 2000;    // bounded dead-reckoning on hold paths

function glided(state: NonNullable<ProjState>, t: number, toPct: (u: number) => number): number {
  if (state.rate == null) return toPct(state.progress);
  const dt = Math.max(0, t - state.t);
  return toPct(state.progress + state.rate * Math.min(dt, GLIDE_MAX_MS));
}
```

- hold paths (`obs.stale`, empty lap route, `best > MATCH_DIST`):
  return `completion: state ? glided(state, obs.t, toPct) : null` instead of
  the frozen `toPct(state.progress)`.
- reach:

```ts
  const dt = tracking ? Math.max(0, obs.t - state!.t) : 0;
  const rateReach = tracking && state!.rate != null ? K_T * state!.rate * dt * (m.totalLengthPx / (lapRoute.lengthPx || 1)) : 0;
  const reach = Math.max(EPS_FWD_MIN, REACH_K * moved / (lapRoute.lengthPx || 1), rateReach);
```

  (`rate` is in within-course progress/ms; converting to within-lap progress
  multiplies by totalLength/lapLength.)
- on a confirmed step, update the EMA in within-course units before storing:

```ts
  const du = toPct(u) - (tracking ? toPct(state!.progress) : toPct(u));
  const obsRate = dt > 0 ? Math.max(0, du / dt) : null;
  const rate = !tracking ? null
    : obsRate == null ? state!.rate
    : state!.rate == null ? obsRate
    : state!.rate + RATE_ALPHA * (obsRate - state!.rate);
  return { state: { edge: 0, progress: u, x: obs.x, y: obs.y, t: obs.t, rate }, completion: toPct(u) };
```

  Lap-crossing seeds in `presence/completion.ts` construct ProjState directly -
  carry the previous rate: `seed = { edge: 0, progress: 0, x, y, t, rate: ps.st?.rate ?? null }`
  (and the cold `null` seeds stay null).

- [ ] **Step 3: Run** `cd pi; npx vitest run` -> green (existing projector tests
must pass unmodified except ProjState literals gaining `rate: null`).

- [ ] **Step 4: Commit**

```bash
git add pi/src/progress/project.ts pi/src/progress/types.ts pi/src/progress/project.test.ts pi/src/presence/completion.ts
git commit -m "feat(progress): self-paced reach + bounded glide in the projector (v2.1)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Clip trails + tuning sweep

**Files:** Create `temp/trail_lab.py`, `pi/src/scripts/projectorLab.ts`; modify `pi/package.json`.

- [ ] **Step 1: trail_lab.py** - replay bootest/koops from GO through
production components (badge `MinimapTracker` seeded from DB values,
`MinimapRecorder` fed `race_ms` from `RaceTimer`, `LapTracker` for per-point
lap + `lap_inc` boundary times, `FinishLatch` for the final boundary), then
write `temp/trails/<clip>.json` as `{ playerId, lapCumMs, points: [{t_ms,
cx, cy, score, lap}] }` with `lapCumMs` from the race-clock times of each
`lap_inc` plus the latched final. (Reuse the seeding/segment constants from
`temp/mm_lab.py`'s CLIPS; KTB seed (1799,792); GO frames: bootest 41.74s,
koops 15.98s.)

- [ ] **Step 2: projectorLab.ts** - read both JSONs; for each (model_run,
replay_run) pair in {(bootest,koops),(koops,bootest)}: `buildCourseModel`
on one, `prepareModel`, then step the other's points at 15Hz through
`projectStep` (lap from the point stamps); per config report:
`mono_violations` (completion decreasing > 1e-6), `d_p50/d_p99` (per-step
delta), `held_pct` (steps where completion equals previous because of
hold), `final` (last completion). Sweep MATCH_DIST {60,40,30} x K_T
{1.5,2.5,4.0} via exported-for-test setters or env vars; print a table.
npm script: `"projector-lab": "node --no-warnings --import tsx src/scripts/projectorLab.ts"`.

- [ ] **Step 3: Run + lock constants** - pick the config with zero
monotonicity violations, lowest held%, sane d_p99; if none beats the
defaults meaningfully, keep defaults. Apply the chosen constants in
`project.ts` and re-run the sweep once to confirm. Record the table for the
report/commit message.

- [ ] **Step 4: Commit**

```bash
git add pi/src/scripts/projectorLab.ts pi/package.json pi/src/progress/project.ts
git commit -m "tune(progress): projector constants from cross-validated clip trails

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Suites + merge

- [ ] `cd pi; npx vitest run` and `python -m pytest tests/ -q` -> green.
- [ ] ```bash
git checkout main
git merge progress-reset-v21
git branch -d progress-reset-v21
```

## Self-review notes

- Spec coverage: A->Task 2, B->Task 3, C->Task 4, D->Task 5.
- Signature consistency: `rebuildCourseModel(db, courseId, cc, window=40)`;
  `LiveCompletion` callable + `.invalidate(courseId)`; `ProjState.rate:
  number | null`; `runsRoutes(db, hub, invalidateModel?)`.
- Risk: existing tests constructing `ProjState` literals must add `rate` -
  Task 4 step 3 calls this out.

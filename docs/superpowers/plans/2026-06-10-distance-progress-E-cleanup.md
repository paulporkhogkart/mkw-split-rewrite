# Distance Progress v2 — Plan E: Migrate Reset-Stat to v2 + Retire Old Projector

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the `avg_completion_before_reset` stat distance-based (consistent with the live bar) by replaying each reset's trail through the v2 projector against the stored `CourseModel`, then delete the now-unused single-run projector (`pi/src/stats/progress.ts`) and `courseReference`.

**Architecture:** `resolveCompletion` (`pi/src/stats/completion.ts`) currently builds a per-call single-run reference (`courseReference` → `prepareReference`) and replays resets through the old `step`. Switch it to `loadCourseModel` (the stored v2 `CourseModel`) + `prepareModel`/`projectStep`, with the same per-frame replay (resetting state on in-race lap change, as the live hub does). Then remove the dead old projector.

**Tech Stack:** TypeScript, vitest 4. Spec: `docs/superpowers/specs/2026-06-10-distance-progress-model-design.md` §8.

**Conventions:** pi tests from `pi/`. Commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure
- **Modify** `pi/src/stats/completion.ts` — `resolveCompletion` uses the v2 model + projector; delete `courseReference`; drop the `./progress` import.
- **Modify** `pi/src/stats/completion.test.ts` — seed a v2 `CourseModel`; assert distance-based reset fractions.
- **Delete** `pi/src/stats/progress.ts` and `pi/src/stats/progress.test.ts` — the retired single-run projector.

---

## Task 1: `resolveCompletion` replays through the v2 model

**Files:** `pi/src/stats/completion.ts`, `pi/src/stats/completion.test.ts`

The function's query + filtering + `byKey`/result assembly stay; only the reference source and the per-reset replay change. Read the current file first.

- [ ] **Step 1: Rewrite the imports + `courseReference` + the per-reset loop.**

Replace the top import line `import { prepareReference, step, type Reference, type ProjState } from './progress';` with:
```ts
import { loadCourseModel } from '../db/courseModels';
import { prepareModel, projectStep, type Prepared } from '../progress/project';
import type { CourseModel, ProjState } from '../progress/types';
```

**Delete** the entire `courseReference` function (lines ~26-37).

Inside `resolveCompletion`, replace the `refCache`/`getRef` block (the `Map<number, Reference | null>` + `getRef` closure that calls `courseReference`) with a model cache:
```ts
  const modelCache = new Map<number, { m: CourseModel; pe: Prepared } | null>();
  const getModel = (courseIdv: number): { m: CourseModel; pe: Prepared } | null => {
    if (modelCache.has(courseIdv)) return modelCache.get(courseIdv)!;
    const m = loadCourseModel(db, courseIdv, cc);
    const entry = m ? { m, pe: prepareModel(m) } : null;
    modelCache.set(courseIdv, entry);
    return entry;
  };
```

Change the points statement to also select `lap`:
```ts
  const ptsStmt = db.prepare('SELECT cx, cy, t_ms, lap FROM run_points WHERE run_id=? ORDER BY t_ms');
```
(keep the existing `lapsStmt`.)

Replace the `for (const reset of resets) { … }` body with the v2 replay:
```ts
  for (const reset of resets) {
    const entry = getModel(reset.course_id);
    const pts = ptsStmt.all(reset.id) as { cx: number; cy: number; t_ms: number; lap: number | null }[];
    if (!entry || pts.length === 0) { unevaluable++; continue; }
    const laps = lapsStmt.all(reset.id) as { lap_time_ms: number }[];
    let c = 0; const cum = laps.map((l) => (c += l.lap_time_ms));
    const N = entry.m.laps.length;
    const lapOf = (t: number) => { let L = 1; for (const b of cum) { if (t >= b) L++; else break; } return L; };
    let st: ProjState = null, prevLap = 0, frac = 0;
    for (const p of pts) {
      const lap = p.lap ?? lapOf(p.t_ms);
      if (st && lap !== prevLap && lap <= N) st = null;          // reset on in-race lap change (as the live hub)
      const r = projectStep(st, entry.m, entry.pe, { x: p.cx, y: p.cy, lap, totLap: N, t: p.t_ms, stale: false });
      st = r.state; prevLap = lap; if (r.completion != null) frac = r.completion;
    }
    overallSum += frac; overallN += 1;
    const k = keyOf(reset);
    const cur = byKey.get(k) ?? { sum: 0, n: 0 };
    cur.sum += frac; cur.n += 1; byKey.set(k, cur);
  }
```

(Leave the `byKey`/`overallSum`/`overallN`/`unevaluable` declarations, `keyOf`, and the `total`/`rows`/`return` assembly exactly as they are.)

- [ ] **Step 2: Update `completion.test.ts` to seed a v2 model.**

The existing tests build a finished run (the old `courseReference` source) + resets, then assert reset fractions. For v2, the model must be **stored** via `saveCourseModel`. Read the current test file; then, for each test that expects a non-unevaluable result, after seeding the runs add a saved model and convert expectations to **distance** (the reset's last-point completion = `(startOffset + u·lapLen)/total`).

Concretely: add `import { saveCourseModel } from '../db/courseModels';` and `import type { CourseModel } from '../progress/types';`. Define a single-lap straight-line model the existing fixtures' points lie on, e.g. a LINE from (0,0) to (10,0), length 10, one lap (total 10):
```ts
const LINE: [number, number][] = [[0, 0], [10, 0]];
const lineModel = (): CourseModel => ({ version: 2, totalLengthPx: 10, status: 'centerline',
  laps: [{ index: 1, lengthPx: 10, startOffsetPx: 0,
    graph: { version: 1, startNode: 0, lapLengthPx: 10, status: 'centerline',
      nodes: [{ id: 0, x: 0, y: 0, progress: 0 }],
      edges: [{ id: 0, a: 0, b: 0, poly: LINE, arcLen: 10, pLo: 0, pHi: 1, kind: 'main', passThrough: null }] }}] });
```
For the existing "estimates a reset that stopped mid-lap-2 … expects 0.75" test (or whichever exact reset case it has): rework it to a single-lap LINE model where a reset whose last point is at x=7.5 → completion `0.75` (7.5/10). Save the model with `saveCourseModel(d, <courseId>, 150, lineModel(), 1)` for the reset's course before calling `resolveCompletion`. Keep the two `unevaluable` cases (no model / no points) — with no saved model, `getModel` returns null → unevaluable, which the test should now assert by NOT seeding a model.

Adapt each existing assertion's expected number to the distance fraction the LINE model produces for that test's points. (The point is to keep the test's *intent* — mid-run resets score partial, point-less/model-less resets are unevaluable — with v2 numbers.)

- [ ] **Step 3: Run**

Run: `cd pi && npx vitest run src/stats/completion.test.ts`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add pi/src/stats/completion.ts pi/src/stats/completion.test.ts
git commit -m "feat(stats): avg_completion_before_reset replays through the v2 model"
```

---

## Task 2: Delete the retired single-run projector

**Files:** Delete `pi/src/stats/progress.ts`, `pi/src/stats/progress.test.ts`

- [ ] **Step 1: Confirm no remaining consumers**

Run: `grep -rn "stats/progress\|from './progress'" pi/src/stats`
Expected: nothing references `stats/progress` anymore (Task 1 removed the `completion.ts` import). If anything else imports it, STOP and report.

- [ ] **Step 2: Delete the files**

```bash
git rm pi/src/stats/progress.ts pi/src/stats/progress.test.ts
```

- [ ] **Step 3: Verify the whole suite**

Run: `cd pi && npm test`
Expected: green (the retired projector's tests are gone; nothing else depended on it).

- [ ] **Step 4: Commit**

```bash
git commit -m "chore(progress): delete the retired single-run projector (stats/progress.ts)"
```

---

## Final verification

- [ ] **Full pi suite:** `cd pi && npm test` — green.
- [ ] **Type check (frontend unaffected):** `npx svelte-check` (root) — 0/0.

---

## Self-Review (author checklist — completed)

**Spec coverage:** §8 retirement — `avg_completion_before_reset` → v2 projector (Task 1); delete single-run `progress.ts` + `courseReference` (Tasks 1-2). **Placeholder scan:** Task 2 is concrete; Task 1 Step 2's test edits are guided against the existing fixtures (implementer reads the file) — the model + the distance-conversion rule (`last-point u·lapLen/total`) are explicit. **Type consistency:** `CourseModel`/`Prepared` (from `../progress/types`/`../progress/project`) flow through `getModel` → `projectStep`, matching Plans A/D. `ProjState` reused. `loadCourseModel`/`prepareModel`/`projectStep` signatures are those shipped in Plan A.

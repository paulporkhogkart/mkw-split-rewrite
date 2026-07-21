# WR Trail Job Status Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A hidden read-only page at `/wr-jobs` on thekartoff.com showing the status of every WR trail-recording job, backed by a new token-free `GET /v1/wr-jobs` on the Pi.

**Architecture:** One SQL query in `pi/src/db/wrJobs.ts` derives each WR's status (`done` / `in_progress` / `parked` / `unprocessable` / `not_queued` / `cooldown` / `queued`) with the **same predicates `claimJob` uses**, exposed via a new GET route in the existing `wrJobsRoutes` group and added to `PUBLIC_READS`. The site adds a URL-only route (like `/version`) rendering a `VersionPage`-styled table that polls every 30 s; all page logic lives in a pure-function `lib/wrJobs.js` module.

**Tech Stack:** Pi: Node/TS via tsx, Hono, `node:sqlite` (`DatabaseSync`), vitest. Web: Vite + Svelte SPA, vitest.

**Spec:** `docs/superpowers/specs/2026-07-21-wr-jobs-status-page-design.md`

## Global Constraints

- The status CASE must mirror `claimJob`'s predicates exactly: cooldown gates on `attempts >= FREE_ATTEMPTS` (=5) with windows off `updated_at` of 1 hour, 6 hours at `attempts >= 8`, 24 hours at `attempts >= 12`; `last_error LIKE 'time_mismatch%'` is terminal for claiming.
- The endpoint is token-free: path added to `PUBLIC_READS` in `pi/src/api/app.ts` (exact-path match + GET CORS). All existing `/v1/wr-jobs/...` POST worker routes stay token-gated — do not touch them.
- Removed WRs (`removed_at IS NOT NULL`) never appear. Row inclusion: every current WR, plus non-current WRs that have a job row or a trail.
- Web page is URL-only (no navbar link), styled like `VersionPage.svelte` (plain dark ops table — NOT the KART-OFF print language).
- Pi tests are colocated (`*.test.ts` beside the module); keep source and tests `npm run typecheck`-clean (non-gating but required by repo convention). Web tests colocated `*.test.js`.
- Commit after each task; commit messages end with the `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` trailer (repo convention, shown in examples below).

---

### Task 1: Pi — `wrJobsStatus()` query in `pi/src/db/wrJobs.ts`

**Files:**
- Modify: `pi/src/db/wrJobs.ts` (append after `reviveStaleEngineJobs`, ~line 266)
- Test: `pi/src/db/wrJobs.test.ts` (append a new `describe` block; reuse the file's existing `setup()` / `addWr()` helpers at lines 10–21)

**Interfaces:**
- Consumes: existing `FREE_ATTEMPTS` const in the same file; schema tables `world_records`, `courses`, `wr_jobs`, `wr_trails` (all already exist — no schema change).
- Produces: `export type WrJobStatus`, `export type WrJobStatusRow`, `export function wrJobsStatus(db: DatabaseSync): WrJobStatusRow[]` — Task 2's route calls `wrJobsStatus(db)` and returns `{ jobs: rows }`.

- [ ] **Step 1: Write the failing tests**

Append to `pi/src/db/wrJobs.test.ts`. Add `wrJobsStatus` to the existing import from `./wrJobs` (line 3–5). The helpers `setup()` (creates `:memory:` db + course id 1 "Mario Circuit") and `addWr(db, id, {current?, video?})` are already in this file — reuse them, do not redefine them.

```ts
describe('wrJobsStatus', () => {
  const rowOf = (db: any, wrId: number) =>
    wrJobsStatus(db).find((r) => r.wr_id === wrId);

  it('reports a freshly seeded job as queued', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    expect(rowOf(db, 10)).toMatchObject({
      wr_id: 10, course: 'Mario Circuit', course_slug: 'mario_circuit', cc: 150,
      holder_name: 'JaK', record_str: '1:02.934', is_current: 1,
      status: 'queued', attempts: 0, last_error: null, lease_owner: null,
      next_eligible_at: null, trail_points: null,
    });
  });

  it('reports a live lease as in_progress with the worker id', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    claimJob(db, 'w1');
    expect(rowOf(db, 10)).toMatchObject({ status: 'in_progress', lease_owner: 'w1', attempts: 1 });
  });

  it('reports a lapsed lease as queued again, without leaking the stale owner', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    claimJob(db, 'w1');
    db.prepare("UPDATE wr_jobs SET lease_until = datetime('now','-1 minute') WHERE wr_id=10").run();
    expect(rowOf(db, 10)).toMatchObject({ status: 'queued', lease_owner: null, attempts: 1 });
  });

  it('reports a completed job as done with the trail point count', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    claimJob(db, 'w1');
    completeJob(db, 10, 'w1', [[0, 1, 2, 0.9, 1], [100, 3, 4, 0.9, 1]]);
    expect(rowOf(db, 10)).toMatchObject({ status: 'done', trail_points: 2, lease_owner: null });
  });

  it('keeps a failed-but-under-cap job queued, with the error visible', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    claimJob(db, 'w1');
    failJob(db, 10, 'w1', 'download_failed: 403');
    expect(rowOf(db, 10)).toMatchObject({ status: 'queued', attempts: 1, last_error: 'download_failed: 403' });
  });

  // Cooldown tiers mirror claimJob: 1h at FREE_ATTEMPTS, 6h at 8, 24h at 12 — off updated_at.
  it.each([
    [FREE_ATTEMPTS, '+1 hour'],
    [8, '+6 hours'],
    [12, '+24 hours'],
  ])('reports attempts=%i inside its window as cooldown with next_eligible_at %s', (attempts, win) => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    db.prepare("UPDATE wr_jobs SET attempts=?, updated_at=datetime('now'), last_error='engine_failed x' WHERE wr_id=10")
      .run(attempts);
    const row = rowOf(db, 10)!;
    expect(row.status).toBe('cooldown');
    const expected = db.prepare(
      'SELECT datetime(updated_at, ?) AS t FROM wr_jobs WHERE wr_id=10').get(win) as any;
    expect(row.next_eligible_at).toBe(expected.t);
  });

  it('reports a cooled-down job as queued once the window has elapsed (matches claimJob)', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    db.prepare("UPDATE wr_jobs SET attempts=?, updated_at=datetime('now','-61 minutes') WHERE wr_id=10")
      .run(FREE_ATTEMPTS);
    expect(rowOf(db, 10)).toMatchObject({ status: 'queued', next_eligible_at: null });
    expect(claimJob(db, 'w1')).not.toBeNull();   // the page and the queue agree
  });

  it('reports time_mismatch as parked, even past the attempt cap', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    db.prepare("UPDATE wr_jobs SET attempts=6, updated_at=datetime('now'), last_error='time_mismatch got 1:03.001' WHERE wr_id=10").run();
    expect(rowOf(db, 10)).toMatchObject({ status: 'parked' });
  });

  it('reports a current WR with no video as unprocessable (no job row exists)', () => {
    const db = setup(); addWr(db, 10, { video: null });
    seedWrJobs(db);   // seeds nothing — no video
    expect(rowOf(db, 10)).toMatchObject({ status: 'unprocessable', attempts: 0 });
  });

  it('reports an unresolved character_slug as unprocessable even with a job row', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    db.prepare('UPDATE world_records SET character_slug=NULL WHERE id=10').run();
    expect(rowOf(db, 10)).toMatchObject({ status: 'unprocessable' });
  });

  it('reports a current videoed WR with no job row yet as not_queued (pre-seed transient)', () => {
    const db = setup(); addWr(db, 10);   // no seedWrJobs call
    expect(rowOf(db, 10)).toMatchObject({ status: 'not_queued', attempts: 0 });
  });

  it('includes a superseded WR that has a job row', () => {
    const db = setup(); addWr(db, 11, { current: 0 });
    enqueueJob(db, 11);
    expect(rowOf(db, 11)).toMatchObject({ is_current: 0, status: 'queued' });
  });

  it('excludes a superseded WR with neither job nor trail', () => {
    const db = setup(); addWr(db, 11, { current: 0 });
    expect(rowOf(db, 11)).toBeUndefined();
  });

  it('excludes soft-removed WRs entirely', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    db.prepare("UPDATE world_records SET removed_at=datetime('now') WHERE id=10").run();
    expect(rowOf(db, 10)).toBeUndefined();
  });

  it('orders current WRs before superseded, by course name within each', () => {
    const db = setup();
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (2,'acorn_heights','Acorn Heights')");
    addWr(db, 10);                                 // current, Mario Circuit
    db.prepare(`INSERT INTO world_records(id, course_id, cc, holder_name, record_ms, record_str,
                  achieved_at, video_url, character_slug, is_current)
                VALUES (11,2,150,'JaK',60000,'1:00.000','2026-04-06T00:00:00.000Z',
                        'https://youtu.be/y','toadette',1)`).run();   // current, Acorn Heights
    addWr(db, 12, { current: 0 }); enqueueJob(db, 12);               // superseded, Mario Circuit
    seedWrJobs(db);
    expect(wrJobsStatus(db).map((r) => r.wr_id)).toEqual([11, 10, 12]);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `pi/`): `npx vitest run src/db/wrJobs.test.ts`
Expected: FAIL — `wrJobsStatus` is not exported (`SyntaxError` / undefined import). Existing tests in the file still pass.

- [ ] **Step 3: Implement `wrJobsStatus`**

Append to `pi/src/db/wrJobs.ts` (after `reviveStaleEngineJobs`):

```ts
export type WrJobStatus =
  'done' | 'in_progress' | 'parked' | 'unprocessable' | 'not_queued' | 'cooldown' | 'queued';

export type WrJobStatusRow = {
  wr_id: number; course: string; course_slug: string; cc: number;
  holder_name: string | null; record_str: string; is_current: number;
  status: WrJobStatus;
  attempts: number; last_error: string | null; updated_at: string | null;
  lease_owner: string | null;        // only while in_progress — a lapsed lease's owner is noise
  next_eligible_at: string | null;   // only while in cooldown
  trail_points: number | null;       // only when done
};

/** Read-only status of every WR trail job, for the hidden /wr-jobs site page. One row per
 *  current non-removed WR, plus non-current WRs that have a job row or a trail (processed
 *  history stays visible; untouched history stays out).
 *
 *  The status CASE mirrors claimJob's WHERE clause term for term (live lease, terminal
 *  time_mismatch, FREE_ATTEMPTS + the 1h/6h/24h cooldown tiers off updated_at) so this page
 *  can never disagree with the queue — if you change one, change both. `not_queued` is the
 *  transient gap between a WR being scraped and its job being enqueued/boot-seeded. */
export function wrJobsStatus(db: DatabaseSync): WrJobStatusRow[] {
  return db.prepare(
    `SELECT s.wr_id, s.course, s.course_slug, s.cc, s.holder_name, s.record_str, s.is_current,
            s.status, s.attempts, s.last_error, s.updated_at,
            CASE WHEN s.status = 'in_progress' THEN s.lease_owner END AS lease_owner,
            CASE WHEN s.status = 'cooldown'
                 THEN datetime(s.updated_at, CASE WHEN s.attempts >= 12 THEN '+24 hours'
                                                  WHEN s.attempts >= 8  THEN '+6 hours'
                                                  ELSE '+1 hour' END)
            END AS next_eligible_at,
            s.trail_points
     FROM (
       SELECT w.id AS wr_id, c.display_name AS course, c.slug AS course_slug, w.cc,
              w.holder_name, w.record_str, w.is_current,
              COALESCE(j.attempts, 0) AS attempts, j.last_error, j.updated_at,
              j.lease_owner, t.n AS trail_points,
              CASE
                WHEN t.wr_id IS NOT NULL THEN 'done'
                WHEN j.lease_until IS NOT NULL AND j.lease_until >= datetime('now') THEN 'in_progress'
                WHEN j.last_error LIKE 'time_mismatch%' THEN 'parked'
                WHEN w.video_url IS NULL OR w.character_slug IS NULL THEN 'unprocessable'
                WHEN j.wr_id IS NULL THEN 'not_queued'
                WHEN j.attempts >= ?
                     AND j.updated_at > datetime('now', CASE WHEN j.attempts >= 12 THEN '-24 hours'
                                                             WHEN j.attempts >= 8  THEN '-6 hours'
                                                             ELSE '-1 hour' END)
                  THEN 'cooldown'
                ELSE 'queued'
              END AS status
       FROM world_records w
       JOIN courses c ON c.id = w.course_id
       LEFT JOIN wr_jobs j ON j.wr_id = w.id
       LEFT JOIN wr_trails t ON t.wr_id = w.id
       WHERE w.removed_at IS NULL
         AND (w.is_current = 1 OR j.wr_id IS NOT NULL OR t.wr_id IS NOT NULL)
     ) s
     ORDER BY s.is_current DESC, s.course ASC, s.cc ASC`
  ).all(FREE_ATTEMPTS) as WrJobStatusRow[];
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run (from `pi/`): `npx vitest run src/db/wrJobs.test.ts`
Expected: PASS — all new `wrJobsStatus` tests green, all pre-existing tests in the file still green.

- [ ] **Step 5: Typecheck and full pi suite**

Run (from `pi/`): `npm run typecheck` then `npm test`
Expected: tsc clean; full suite green (~610+ tests).

- [ ] **Step 6: Commit**

```bash
git add pi/src/db/wrJobs.ts pi/src/db/wrJobs.test.ts
git commit -m "feat(pi): wrJobsStatus() — per-WR trail job status derived with claimJob's predicates

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Pi — `GET /v1/wr-jobs` route, token-free

**Files:**
- Modify: `pi/src/api/wrJobs.ts` (add GET route inside `wrJobsRoutes`, before the POST routes; extend the import from `../db/wrJobs` at line 8)
- Modify: `pi/src/api/app.ts:39` (append `'/v1/wr-jobs'` to the `PUBLIC_READS` array)
- Test: `pi/src/api/wrJobs.test.ts` (append a `describe` block; reuse the file's `setup()` at lines 9–28)

**Interfaces:**
- Consumes: `wrJobsStatus(db): WrJobStatusRow[]` from Task 1.
- Produces: `GET /v1/wr-jobs` → 200 `{ jobs: WrJobStatusRow[] }`, no token, GET CORS. This is the wire contract Task 4's page fetches.

- [ ] **Step 1: Write the failing tests**

Append to `pi/src/api/wrJobs.test.ts` (its `setup()` already inserts WR id 10 on Mario Circuit and runs `seedWrJobs` — one queued job exists):

```ts
describe('GET /v1/wr-jobs (public status read)', () => {
  it('200s with no token and returns the status rows', async () => {
    const { app } = setup();
    const res = await app.request('/v1/wr-jobs');
    expect(res.status).toBe(200);
    const body = await res.json() as any;
    expect(body.jobs).toHaveLength(1);
    expect(body.jobs[0]).toMatchObject({
      wr_id: 10, course: 'Mario Circuit', cc: 150, status: 'queued',
      attempts: 0, is_current: 1,
    });
  });

  it('reflects a live claim as in_progress', async () => {
    const { app, w1 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    const body = await (await app.request('/v1/wr-jobs')).json() as any;
    expect(body.jobs[0]).toMatchObject({ status: 'in_progress', lease_owner: 'machine-a', attempts: 1 });
  });

  it('serves permissive CORS for the cross-origin website', async () => {
    const { app } = setup();
    const res = await app.request('/v1/wr-jobs', { headers: { Origin: 'https://thekartoff.com' } });
    expect(res.headers.get('access-control-allow-origin')).toBe('*');
  });

  it('leaves the worker POST routes token-gated', async () => {
    const { app } = setup();
    const res = await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: { 'X-Worker-Id': 'machine-a' } });
    expect(res.status).toBe(401);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `pi/`): `npx vitest run src/api/wrJobs.test.ts`
Expected: the first three new tests FAIL with 401 (path not yet in `PUBLIC_READS`); the fourth passes already.

- [ ] **Step 3: Implement the route and open the path**

In `pi/src/api/wrJobs.ts`, extend the db import (line 8):

```ts
import { claimJob, heartbeatJob, releaseJob, completeJob, failJob, stuckJobs, markJobAlerted, DEFAULT_LEASE_SEC, wrJobsStatus } from '../db/wrJobs';
```

Inside `wrJobsRoutes`, immediately after `const r = new Hono<Env>();`:

```ts
  // Public read-only status board for the hidden site page (/wr-jobs). Token-free via
  // PUBLIC_READS (exact path — the worker POST routes below live on subpaths and stay gated).
  r.get('/v1/wr-jobs', (c) => c.json({ jobs: wrJobsStatus(db) }));
```

In `pi/src/api/app.ts` line 39, append `'/v1/wr-jobs'` to `PUBLIC_READS`:

```ts
  const PUBLIC_READS = ['/v1/leaderboard', '/v1/world-records', '/v1/wr-trails', '/v1/roster', '/v1/territory', '/v1/territory/timeline', '/v1/version', '/v1/activity', '/v1/wr-jobs'];
```

- [ ] **Step 4: Run the tests to verify they pass**

Run (from `pi/`): `npx vitest run src/api/wrJobs.test.ts`
Expected: PASS — all new and pre-existing tests green.

- [ ] **Step 5: Typecheck and full pi suite**

Run (from `pi/`): `npm run typecheck` then `npm test`
Expected: tsc clean; full suite green.

- [ ] **Step 6: Commit**

```bash
git add pi/src/api/wrJobs.ts pi/src/api/app.ts pi/src/api/wrJobs.test.ts
git commit -m "feat(pi): token-free GET /v1/wr-jobs status read for the hidden site page

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Web — `/wr-jobs` route + pure page-logic module

**Files:**
- Modify: `web/src/lib/view.js` (add the route; update the doc comment's URL-only list)
- Modify: `web/src/lib/view.test.js` (add one case beside the existing `/version` case)
- Modify: `web/src/lib/api.js` (add `wrJobsUrl`)
- Create: `web/src/lib/wrJobs.js`
- Test: `web/src/lib/wrJobs.test.js`

**Interfaces:**
- Consumes: the Task 2 wire shape — `{ jobs: [{ wr_id, course, course_slug, cc, holder_name, record_str, is_current, status, attempts, last_error, updated_at, lease_owner, next_eligible_at, trail_points }] }`.
- Produces (for Task 4's Svelte page):
  - `viewFromPath("/wr-jobs") === "wr-jobs"`
  - `wrJobsUrl(): string`
  - `STATUS_META: Record<status, {label, color, rank}>`
  - `splitRows(jobs) → { current, superseded }` (problem-states-first sort within each)
  - `summary(jobs) → { done, queued, stuck, coverage }` (coverage = `"X/Y"` over current WRs)
  - `detailOf(job, now?) → string` (per-status detail cell text)
  - `relTime(sqliteUtcString, now?) → string`, `parseUtc(sqliteUtcString) → Date|null`

- [ ] **Step 1: Write the failing tests**

In `web/src/lib/view.test.js`, add beside the existing `/version` assertion:

```js
  it("maps /wr-jobs to the wr-jobs view (URL-only, like /version)", () => {
    expect(viewFromPath("/wr-jobs")).toBe("wr-jobs");
    expect(viewFromPath("/wr-jobs/")).toBe("wr-jobs");
  });
```

Create `web/src/lib/wrJobs.test.js`:

```js
import { describe, it, expect } from "vitest";
import { STATUS_META, splitRows, summary, detailOf, relTime, parseUtc } from "./wrJobs.js";

const job = (over = {}) => ({
  wr_id: 1, course: "Mario Circuit", course_slug: "mario_circuit", cc: 150,
  holder_name: "JaK", record_str: "1:02.934", is_current: 1,
  status: "queued", attempts: 0, last_error: null, updated_at: null,
  lease_owner: null, next_eligible_at: null, trail_points: null, ...over,
});

describe("STATUS_META", () => {
  it("covers every server status", () => {
    for (const s of ["done", "in_progress", "parked", "unprocessable", "not_queued", "cooldown", "queued"])
      expect(STATUS_META[s], s).toBeTruthy();
  });
});

describe("splitRows", () => {
  it("splits current from superseded and sorts problem states to the top", () => {
    const rows = [
      job({ wr_id: 1, status: "done" }),
      job({ wr_id: 2, status: "queued" }),
      job({ wr_id: 3, status: "parked" }),
      job({ wr_id: 4, status: "in_progress" }),
      job({ wr_id: 5, status: "cooldown" }),
      job({ wr_id: 6, is_current: 0, status: "done" }),
    ];
    const { current, superseded } = splitRows(rows);
    expect(superseded.map((j) => j.wr_id)).toEqual([6]);
    // problems (parked, cooldown) first, then in_progress, queued, done
    expect(current.map((j) => j.wr_id)).toEqual([3, 5, 4, 2, 1]);
  });

  it("keeps the server's course order within a status band (stable sort)", () => {
    const rows = [
      job({ wr_id: 1, course: "Acorn Heights", status: "queued" }),
      job({ wr_id: 2, course: "Mario Circuit", status: "queued" }),
    ];
    expect(splitRows(rows).current.map((j) => j.wr_id)).toEqual([1, 2]);
  });
});

describe("summary", () => {
  it("counts done/queued/stuck and current-WR trail coverage", () => {
    const rows = [
      job({ wr_id: 1, status: "done" }),
      job({ wr_id: 2, status: "queued" }),
      job({ wr_id: 3, status: "in_progress" }),
      job({ wr_id: 4, status: "cooldown" }),
      job({ wr_id: 5, status: "parked" }),
      job({ wr_id: 6, status: "unprocessable" }),
      job({ wr_id: 7, is_current: 0, status: "done" }),
    ];
    expect(summary(rows)).toEqual({ done: 2, queued: 2, stuck: 3, coverage: "1/6" });
  });
});

describe("relTime / parseUtc", () => {
  const now = Date.parse("2026-07-21T12:00:00Z");
  it("parses SQLite UTC datetimes", () => {
    expect(parseUtc("2026-07-21 11:57:00").toISOString()).toBe("2026-07-21T11:57:00.000Z");
    expect(parseUtc(null)).toBeNull();
  });
  it("renders past and future times relative to now", () => {
    expect(relTime("2026-07-21 11:57:00", now)).toBe("3 m ago");
    expect(relTime("2026-07-21 12:42:00", now)).toBe("in 42 m");
    expect(relTime("2026-07-20 12:00:00", now)).toBe("24 h ago");
    expect(relTime(null, now)).toBe("—");
  });
});

describe("detailOf", () => {
  const now = Date.parse("2026-07-21T12:00:00Z");
  it("shows point count when done", () => {
    expect(detailOf(job({ status: "done", trail_points: 1732 }), now)).toBe("1732 pts");
  });
  it("shows worker and attempt when in progress", () => {
    expect(detailOf(job({ status: "in_progress", lease_owner: "machine-a", attempts: 2 }), now))
      .toBe("machine-a · attempt 2");
  });
  it("shows next retry and error when cooling down", () => {
    expect(detailOf(job({ status: "cooldown", next_eligible_at: "2026-07-21 12:42:00",
      last_error: "engine_failed boom" }), now)).toBe("retry in 42 m — engine_failed boom");
  });
  it("explains unprocessable", () => {
    expect(detailOf(job({ status: "unprocessable" }), now)).toBe("no video or unresolved character");
  });
  it("shows the last error on a queued row, truncated to 80 chars", () => {
    const long = "x".repeat(100);
    const d = detailOf(job({ status: "queued", last_error: long }), now);
    expect(d).toHaveLength(80);
    expect(d.endsWith("…")).toBe(true);
  });
  it("is empty for a clean queued row", () => {
    expect(detailOf(job(), now)).toBe("");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `web/`): `npx vitest run src/lib/wrJobs.test.js src/lib/view.test.js`
Expected: `wrJobs.test.js` FAILS (module not found); the new view case FAILS (`"live"` !== `"wr-jobs"`); existing view cases pass.

- [ ] **Step 3: Implement**

`web/src/lib/view.js` — add to `viewFromPath` after the `version` line, and mention it in the file's doc comment alongside `heat`/`version` as URL-only:

```js
  if (p === "wr-jobs") return "wr-jobs";
```

`web/src/lib/api.js` — add after `versionUrl`:

```js
export const wrJobsUrl = () => `${API_BASE}/v1/wr-jobs`;
```

Create `web/src/lib/wrJobs.js`:

```js
// Pure logic for the hidden /wr-jobs status page (WR trail recording jobs). Mirrors the
// version.js pattern: everything testable lives here, WrJobsPage.svelte stays thin.
// Statuses come from the Pi's wrJobsStatus() — see pi/src/db/wrJobs.ts.

// rank: sort order within a table — problem states first, then working, waiting, done.
export const STATUS_META = {
  parked:        { label: "parked",        color: "#f87171", rank: 0 },
  cooldown:      { label: "cooldown",      color: "#fbbf24", rank: 0 },
  unprocessable: { label: "unprocessable", color: "#b91c1c", rank: 0 },
  in_progress:   { label: "in progress",   color: "#60a5fa", rank: 1 },
  queued:        { label: "queued",        color: "#9aa1ab", rank: 2 },
  not_queued:    { label: "not queued",    color: "#6f7782", rank: 3 },
  done:          { label: "done",          color: "#4ade80", rank: 4 },
};

const rankOf = (j) => STATUS_META[j.status]?.rank ?? 5;

/** Current-WR rows (problem states first; server course order kept within a band — the sort is
 *  stable) and superseded-WR rows (server order as-is). */
export function splitRows(jobs) {
  const current = jobs.filter((j) => j.is_current);
  const superseded = jobs.filter((j) => !j.is_current);
  current.sort((a, b) => rankOf(a) - rankOf(b));
  return { current, superseded };
}

/** Header-line counts. "stuck" = needs eyes (cooldown, parked, unprocessable) — a superset of
 *  the Pi's stuckJobs() in that it also counts unprocessable WRs, which can never be claimed.
 *  coverage = trailed current WRs / all current WRs (what `wr-flags` prints). */
export function summary(jobs) {
  const n = (pred) => jobs.filter(pred).length;
  const cur = jobs.filter((j) => j.is_current);
  return {
    done: n((j) => j.status === "done"),
    queued: n((j) => j.status === "queued" || j.status === "in_progress" || j.status === "not_queued"),
    stuck: n((j) => j.status === "cooldown" || j.status === "parked" || j.status === "unprocessable"),
    coverage: `${cur.filter((j) => j.status === "done").length}/${cur.length}`,
  };
}

/** SQLite `datetime('now')` strings are "YYYY-MM-DD HH:MM:SS" in UTC with no zone marker. */
export function parseUtc(s) {
  return s ? new Date(s.replace(" ", "T") + "Z") : null;
}

export function relTime(s, now = Date.now()) {
  const d = parseUtc(s);
  if (!d) return "—";
  const ms = d.getTime() - now;
  const abs = Math.abs(ms);
  const [v, u] = abs >= 3600e3 ? [Math.round(abs / 3600e3), "h"]
               : abs >= 60e3   ? [Math.round(abs / 60e3), "m"]
               :                 [Math.round(abs / 1e3), "s"];
  return ms >= 0 ? `in ${v} ${u}` : `${v} ${u} ago`;
}

const trunc = (s, n = 80) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

/** The per-row detail cell: whatever is most useful for the row's status. */
export function detailOf(j, now = Date.now()) {
  if (j.status === "done") return j.trail_points != null ? `${j.trail_points} pts` : "";
  if (j.status === "in_progress") return `${j.lease_owner ?? "?"} · attempt ${j.attempts}`;
  if (j.status === "cooldown")
    return `retry ${relTime(j.next_eligible_at, now)}${j.last_error ? ` — ${trunc(j.last_error)}` : ""}`;
  if (j.status === "unprocessable") return "no video or unresolved character";
  return j.last_error ? trunc(j.last_error) : "";
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run (from `web/`): `npx vitest run src/lib/wrJobs.test.js src/lib/view.test.js`
Expected: PASS.

Note: the cooldown-detail test string is `"retry in 42 m — engine_failed boom"` — 80-char truncation applies to the error alone, so the test asserting `toHaveLength(80)` covers `trunc` via the queued branch.

- [ ] **Step 5: Full web suite**

Run (from `web/`): `npm test`
Expected: all green (existing suites untouched).

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/view.js web/src/lib/view.test.js web/src/lib/api.js web/src/lib/wrJobs.js web/src/lib/wrJobs.test.js
git commit -m "feat(web): /wr-jobs route + page logic for the WR trail job status board

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Web — `WrJobsPage.svelte` + shell wiring

**Files:**
- Create: `web/src/WrJobsPage.svelte`
- Modify: `web/src/App.svelte` (import at ~line 9 beside `VersionPage`; view branch at ~line 90)
- Modify: `web/CLAUDE.md` (add `/wr-jobs` to the URL-only routes in the `App.svelte` bullet)

**Interfaces:**
- Consumes: `wrJobsUrl`, `STATUS_META`, `splitRows`, `summary`, `detailOf`, `relTime`, `parseUtc` from Task 3; the `"wr-jobs"` view key.
- Produces: the user-visible page. No exports.

- [ ] **Step 1: Create the page component**

`web/src/WrJobsPage.svelte` — same visual system as `VersionPage.svelte` (Inter, 13 px table, uppercase 11 px headers, `#0…#2` dark greys, mono numerics):

```svelte
<script>
  import { onMount, onDestroy } from "svelte";
  import { wrJobsUrl } from "./lib/api.js";
  import { STATUS_META, splitRows, summary, detailOf, relTime, parseUtc } from "./lib/wrJobs.js";

  let loaded = false, error = false;
  let current = [], superseded = [], sum = null;

  async function refresh() {
    try {
      const res = await fetch(wrJobsUrl(), { cache: "no-store" });
      if (!res.ok) throw new Error(`wr-jobs ${res.status}`);
      const payload = await res.json();
      const jobs = payload.jobs ?? [];
      ({ current, superseded } = splitRows(jobs));
      sum = summary(jobs);
      loaded = true;
      error = false;
    } catch (e) {
      console.error("wr-jobs load failed", e);
      if (!loaded) error = true;   // a failed poll keeps the last good table
    }
  }

  let timer;
  onMount(() => { refresh(); timer = setInterval(refresh, 30_000); });
  onDestroy(() => clearInterval(timer));

  const absTitle = (s) => parseUtc(s)?.toLocaleString() ?? "";
</script>

<section class="jobs">
  <h2>wr trail jobs</h2>
  {#if error}
    <p class="msg">Couldn't load job data.</p>
  {:else if !loaded}
    <p class="msg">Loading…</p>
  {:else}
    <p class="sum">
      <b class="ok">{sum.done}</b> done ·
      <b>{sum.queued}</b> queued ·
      <b class="warn" class:zero={sum.stuck === 0}>{sum.stuck}</b> stuck ·
      {sum.coverage} current WRs trailed
    </p>

    <table>
      <thead><tr>
        <th>Course</th><th>cc</th><th>Holder</th><th>Record</th>
        <th>Status</th><th class="num">Att</th><th>Detail</th><th>Updated</th>
      </tr></thead>
      <tbody>
        {#each current as j (j.wr_id)}
          <tr>
            <td>{j.course}</td>
            <td class="mono">{j.cc}</td>
            <td>{j.holder_name ?? "—"}</td>
            <td class="mono">{j.record_str}</td>
            <td><span class="dot" style="background:{STATUS_META[j.status]?.color ?? '#666'}"></span>{STATUS_META[j.status]?.label ?? j.status}</td>
            <td class="mono num">{j.attempts}</td>
            <td class="detail">{detailOf(j)}</td>
            <td class="seen" title={absTitle(j.updated_at)}>{relTime(j.updated_at)}</td>
          </tr>
        {/each}
      </tbody>
    </table>

    {#if superseded.length}
      <h3>superseded — processed history</h3>
      <table>
        <thead><tr>
          <th>Course</th><th>cc</th><th>Holder</th><th>Record</th>
          <th>Status</th><th class="num">Att</th><th>Detail</th><th>Updated</th>
        </tr></thead>
        <tbody>
          {#each superseded as j (j.wr_id)}
            <tr>
              <td>{j.course}</td>
              <td class="mono">{j.cc}</td>
              <td>{j.holder_name ?? "—"}</td>
              <td class="mono">{j.record_str}</td>
              <td><span class="dot" style="background:{STATUS_META[j.status]?.color ?? '#666'}"></span>{STATUS_META[j.status]?.label ?? j.status}</td>
              <td class="mono num">{j.attempts}</td>
              <td class="detail">{detailOf(j)}</td>
              <td class="seen" title={absTitle(j.updated_at)}>{relTime(j.updated_at)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {/if}
</section>

<style>
  .jobs { max-width: 980px; margin: 0 auto; padding: 22px 24px; color: #c7ccd2;
          font-family: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif; }
  h2 { color: #e8eaed; font-size: 18px; margin: 0 0 12px; }
  h3 { color: #cfd3d8; font-size: 13px; margin: 22px 0 6px; font-weight: 600; }
  .msg { color: #8a8f98; font-size: 13px; padding: 24px 0; }
  .sum { font-size: 13px; color: #8a8f98; margin: 0 0 12px; }
  .sum b { color: #e8eaed; font-weight: 600; }
  .sum b.ok { color: #4ade80; }
  .sum b.warn { color: #fbbf24; }
  .sum b.warn.zero { color: #8a8f98; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: #7a818b; font-weight: 600; font-size: 11px; text-transform: uppercase;
       letter-spacing: .08em; padding: 4px 10px; border-bottom: 1px solid #23262b; }
  td { padding: 6px 10px; border-bottom: 1px solid #181b1f; }
  .mono { font-family: ui-monospace, Menlo, monospace; font-variant-numeric: tabular-nums; color: #e8eaed; }
  .num { text-align: right; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 7px; vertical-align: middle; }
  .detail { color: #8a8f98; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .seen { color: #8a8f98; white-space: nowrap; }
</style>
```

- [ ] **Step 2: Wire it into the shell**

`web/src/App.svelte` — add the import beside `VersionPage` (line ~9):

```js
  import WrJobsPage from "./WrJobsPage.svelte";
```

and the view branch after the `version` branch (line ~90):

```svelte
  {:else if view === "wr-jobs"}<WrJobsPage />
```

`web/CLAUDE.md` — in the `App.svelte` layout bullet, change the URL-only list "plus URL-only `/heat` (`HeatGraph`) and `/version` (`VersionPage`)" to also name `/wr-jobs` (`WrJobsPage`, WR trail job statuses).

- [ ] **Step 3: Svelte check + full web suite**

Run (from `web/`): `npm run check` then `npm test`
Expected: svelte-check clean (no new errors); vitest green.

- [ ] **Step 4: Manual smoke against a local Pi**

Terminal 1 (from `pi/`): `npm run dev` (listens on `http://127.0.0.1:8787`; uses the local `mkw.db` — set `MKW_DB` to a scratch copy if preferred).
Terminal 2 (from `web/`): `npm run dev`, then open `http://127.0.0.1:1430/wr-jobs` (NOT `localhost` — IPv6 stall, per `web/CLAUDE.md`).
Expected: the table renders rows with coloured status dots and the summary line; `curl http://127.0.0.1:8787/v1/wr-jobs` returns `{ "jobs": [...] }` with no token. Navigating to `/` still shows the card wall; `/wr-jobs` appears in no navbar.

- [ ] **Step 5: Commit**

```bash
git add web/src/WrJobsPage.svelte web/src/App.svelte web/CLAUDE.md
git commit -m "feat(web): hidden /wr-jobs page — WR trail job status board, 30s poll

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Verification checklist (whole feature)

- `cd pi && npm test && npm run typecheck` — green, tsc clean.
- `cd web && npm test && npm run check` — green.
- `curl http://127.0.0.1:8787/v1/wr-jobs` (local Pi dev) — 200, no token, `{jobs:[...]}`.
- Browser at `http://127.0.0.1:1430/wr-jobs` — table renders; no navbar link anywhere.
- Deploy note: ships with the normal tag-based Pi deploy (`docs/pi-deploy.md`); no schema change, no migration, no new dependency.

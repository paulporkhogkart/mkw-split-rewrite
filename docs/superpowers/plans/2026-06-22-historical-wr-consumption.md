# Historical-WR Territory Consumption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the territory map's course popups and the "on fire" / heat calc use the World Record **as of the scrubbed timeline date** instead of the frozen current WR.

**Architecture:** Ship the full per-course WR progression once in the `/v1/territory/timeline` payload (`wrHistory`), and resolve "WR as of `t`" on the client with a new pure `wrAsOf` — symmetric to how `leaderboardAt` already reconstructs standings as-of-`t`. The single `courseRowAt` chokepoint (fire + heat) and the popup path swap their static WR lookup for `wrAsOf`. At the LIVE frame (`t = Infinity`) this is identical to today.

**Tech Stack:** TypeScript (`pi/` server, node:sqlite + vitest), JavaScript + Svelte (`web/` SPA, vitest).

## Global Constraints

- All consumers exclude DQ'd WRs: `removed_at IS NULL` (the `0333c1b` rule). New code keeps this.
- `wrAsOf` must be **order-independent** for correctness (robust to a stray out-of-order/legacy row) — never rely on the server's ascending sort for the answer.
- LIVE frame is `t = Infinity`; `wrAsOf(..., Infinity)` MUST equal the current WR (newest entry).
- Pure lib modules (`timeline.js`, `heat.js`, `onFire.js`) stay pure: no DOM, no fetch.
- `web` tests: `npm --prefix web test`. `web` type check: `npm --prefix web run check`. `web` build: `npm --prefix web run build`. `pi` tests: `npm --prefix pi test`.
- Spec: `docs/superpowers/specs/2026-06-22-historical-wr-consumption-design.md`.

---

## File Structure

- `web/src/lib/timeline.js` — **add** pure `wrAsOf(wrHistory, slug, t)` beside `leaderboardAt`.
- `web/src/lib/timeline.test.js` — **add** a `wrAsOf` describe block.
- `pi/src/db/reads.ts` — **modify** `territoryTimeline`: return `wrHistory` instead of `wrs`.
- `pi/src/db/reads.test.ts` — **modify** the existing `territoryTimeline` test; **add** an as-of/exclusion test.
- `web/src/lib/heat.js` — **modify** `courseRowAt` / `heatRows`: param `wrs` → `wrHistory`, resolve via `wrAsOf`.
- `web/src/lib/onFire.js` — **modify** `fireListAt`: param `wrs` → `wrHistory`, pass through.
- `web/src/lib/heat.test.js`, `web/src/lib/onFire.test.js` — **modify** fixtures `wrs` → `wrHistory`.
- `web/src/WorldMap.svelte` — **modify** state rename + popup/fire wiring + `wrAsOf` import.
- `web/src/HeatGraph.svelte` — **modify** payload destructure.

---

### Task 1: `wrAsOf` resolver (client core)

**Files:**
- Modify: `web/src/lib/timeline.js`
- Test: `web/src/lib/timeline.test.js`

**Interfaces:**
- Consumes: nothing (pure, leaf function).
- Produces: `wrAsOf(wrHistory, slug, t) -> number | null` where `wrHistory` is `{ [slug]: [achievedMs, recordMs][] }`. Returns the minimum `recordMs` among entries with `achievedMs <= t`, or `null` when none (unknown slug, or no entry achieved by `t`).

- [ ] **Step 1: Write the failing test**

Append to `web/src/lib/timeline.test.js`:

```js
describe("wrAsOf", () => {
  // achievedMs intentionally NOT ascending, to prove order-independence (min over <= t).
  const hist = { rr: [[100, 95000], [300, 99000], [200, 91000]] };

  it("returns the min record achieved by t (latest effective WR for clean data)", () => {
    expect(wrAsOf(hist, "rr", 250)).toBe(91000); // 95000@100 and 91000@200 are <= 250
  });
  it("ignores a stray slower-but-later row (min over entries <= t)", () => {
    expect(wrAsOf(hist, "rr", 350)).toBe(91000); // all three <= 350; 99000@300 ignored
  });
  it("returns null before the first achieved entry", () => {
    expect(wrAsOf(hist, "rr", 50)).toBe(null);
  });
  it("includes an entry achieved exactly at t", () => {
    expect(wrAsOf(hist, "rr", 100)).toBe(95000);
  });
  it("equals the current (best-ever) WR at t = Infinity", () => {
    expect(wrAsOf(hist, "rr", Infinity)).toBe(91000);
  });
  it("returns null for an unknown slug", () => {
    expect(wrAsOf(hist, "nope", Infinity)).toBe(null);
  });
});
```

Also extend the import on line 2:

```js
import { buildSnapshots, flippedCourses, leaderboardAt, wrAsOf } from "./timeline.js";
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix web test -- src/lib/timeline.test.js -t wrAsOf`
Expected: FAIL — `wrAsOf is not a function` / `not exported`.

- [ ] **Step 3: Write minimal implementation**

Append to `web/src/lib/timeline.js`:

```js
// The WR in effect for `slug` at time `t`: the minimum record_ms among that course's history
// entries achieved by `t` (achievedMs <= t). null when none exist yet. Entries arrive pre-sorted
// ascending by achievedMs, but we scan all and take the running min (no early break) so a stray
// out-of-order/legacy row can never report a slower record than one already achieved. At
// t = Infinity this is the best-ever = the current WR, so the LIVE frame is unchanged. Pure.
export function wrAsOf(wrHistory, slug, t) {
  const entries = wrHistory[slug];
  if (!entries) return null;
  let best = null;
  for (const [achievedMs, recordMs] of entries) {
    if (achievedMs > t) continue;              // not yet achieved at this frame
    if (best == null || recordMs < best) best = recordMs;
  }
  return best;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix web test -- src/lib/timeline.test.js`
Expected: PASS (all `wrAsOf` cases + the existing `buildSnapshots`/`flippedCourses`/`leaderboardAt` cases).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/timeline.js web/src/lib/timeline.test.js
git commit -m "feat(territory): wrAsOf resolver — WR in effect at a timeline frame"
```

---

### Task 2: server `territoryTimeline` → `wrHistory`

**Files:**
- Modify: `pi/src/db/reads.ts` (the `territoryTimeline` function)
- Test: `pi/src/db/reads.test.ts` (the `territoryTimeline` describe block)

**Interfaces:**
- Consumes: nothing new.
- Produces: `territoryTimeline(db, cc)` now returns `{ events, colors, wrHistory }` where `wrHistory: Record<string, [number, number][]>` maps each course slug to its `[achievedMs, recordMs]` pairs, ascending by `achievedMs`, excluding `removed_at` rows and null/unparseable `achieved_at`, filtered to `cc`. The `wrs` key is removed.

- [ ] **Step 1: Update the existing test + add the as-of/exclusion test (failing)**

In `pi/src/db/reads.test.ts`, the current `territoryTimeline` test inserts a WR with no `achieved_at` and asserts `r.wrs`. Replace the WR insert and the `r.wrs` assertion in that test:

Replace this line:

```ts
    db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current) VALUES (1,150,'WR',60000,'1:00.000',1)");
```

with (give it a real `achieved_at`):

```ts
    db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current,achieved_at) VALUES (1,150,'WR',60000,'1:00.000',1,'2025-06-04T00:00:00.000Z')");
```

Replace this assertion:

```ts
    expect(r.wrs).toEqual({ mario_circuit: 60000 });
```

with:

```ts
    expect(r.wrHistory).toEqual({ mario_circuit: [[Date.parse('2025-06-04T00:00:00.000Z'), 60000]] });
```

Then add a second test inside the `describe('territoryTimeline', ...)` block:

```ts
  it('wrHistory carries the full progression, ascending, excluding removed + undated rows', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
    db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Gub','#38bdf8')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'mario_circuit','Mario Circuit')");
    // two real WRs (out of insert order), one DQ'd (removed_at), one undated (achieved_at NULL)
    db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current,achieved_at) VALUES (1,150,'B',61000,'1:01.000',0,'2025-08-01T00:00:00.000Z')");
    db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current,achieved_at) VALUES (1,150,'A',62000,'1:02.000',0,'2025-07-01T00:00:00.000Z')");
    db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current,achieved_at,removed_at) VALUES (1,150,'DQ',59000,'0:59.000',0,'2025-09-01T00:00:00.000Z','2025-09-02T00:00:00.000Z')");
    db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current,achieved_at) VALUES (1,150,'Undated',58000,'0:58.000',1,NULL)");
    // a different cc must not leak in
    db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current,achieved_at) VALUES (1,200,'Other',50000,'0:50.000',1,'2025-07-15T00:00:00.000Z')");
    const r = territoryTimeline(db, 150);
    expect(r.wrHistory).toEqual({
      mario_circuit: [
        [Date.parse('2025-07-01T00:00:00.000Z'), 62000],
        [Date.parse('2025-08-01T00:00:00.000Z'), 61000],
      ],
    });
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix pi test -- src/db/reads.test.ts -t territoryTimeline`
Expected: FAIL — `r.wrHistory` is `undefined` (function still returns `wrs`).

- [ ] **Step 3: Implement the query + return-shape change**

In `pi/src/db/reads.ts`, change the `territoryTimeline` return-type annotation:

```ts
export function territoryTimeline(db: DatabaseSync, cc: number):
    { events: TimelineEvent[]; colors: Record<string, string>; wrHistory: Record<string, [number, number][]> } {
```

Replace the `wrs` block (the `const wrs ...` loop and its query) with:

```ts
  const wrHistory: Record<string, [number, number][]> = {};
  for (const w of db.prepare(
    `SELECT c.slug AS slug, w.record_ms AS ms, w.achieved_at AS achieved_at
     FROM world_records w JOIN courses c ON c.id = w.course_id
     WHERE w.cc = ? AND w.removed_at IS NULL AND w.achieved_at IS NOT NULL`
  ).all(cc) as { slug: string; ms: number; achieved_at: string }[]) {
    const at = Date.parse(w.achieved_at);
    if (!Number.isFinite(at)) continue;                 // undated/unparseable -> not placeable on the timeline
    (wrHistory[w.slug] ||= []).push([at, w.ms]);
  }
  for (const slug in wrHistory) wrHistory[slug].sort((a, b) => a[0] - b[0]);
  return { events, colors, wrHistory };
```

(Delete the old `return { events, colors, wrs };`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm --prefix pi test -- src/db/reads.test.ts`
Expected: PASS (both `territoryTimeline` cases + all other `reads` cases).

- [ ] **Step 5: Verify nothing else referenced `.wrs` server-side**

Run: `npm --prefix pi test`
Expected: PASS (full pi suite — confirms no other server code/test consumed the old `wrs` key; the API smoke in `app.test.ts` only checks status).

- [ ] **Step 6: Commit**

```bash
git add pi/src/db/reads.ts pi/src/db/reads.test.ts
git commit -m "feat(territory): serve full per-course wrHistory in timeline payload"
```

---

### Task 3: thread `wrHistory` through fire + heat (`heat.js`, `onFire.js`)

**Files:**
- Modify: `web/src/lib/heat.js` (`courseRowAt`, `heatRows`)
- Modify: `web/src/lib/onFire.js` (`fireListAt`)
- Test: `web/src/lib/heat.test.js`, `web/src/lib/onFire.test.js`

**Interfaces:**
- Consumes: `wrAsOf` (Task 1) from `./timeline.js`.
- Produces: `courseRowAt({ course, events, wrHistory, colors, t })`, `heatRows({ courses, events, wrHistory, colors, t })`, `fireListAt({ courses, events, wrHistory, colors, t })` — all now take `wrHistory` (was `wrs`) and resolve the per-frame WR internally. `onFireCourses(entries)` is UNCHANGED (it operates on entries that already carry a resolved `wr`).

- [ ] **Step 1: Update the test fixtures (failing)**

In `web/src/lib/heat.test.js`, replace the `wrs` fixture:

```js
const wrs = { mc: 107414, pb: 100139, rr: 233693 }; // nw absent on purpose
```

with:

```js
// single-entry histories (achieved at t=0) so each resolves at every frame; nw absent on purpose
const wrHistory = { mc: [[0, 107414]], pb: [[0, 100139]], rr: [[0, 233693]] };
```

Then replace every `wrs` argument in this file with `wrHistory` (every `heatRows({...})` and `fireListAt({...})` call — there are no other uses of the name). Each becomes e.g.:

```js
    const rows = heatRows({ courses, events, wrHistory, colors, t: Infinity });
```
```js
    expect(heatRows({ courses, events, wrHistory, colors, t: 999 })).toEqual([]);
```
```js
    const lit = heatRows({ courses, events, wrHistory, colors, t: Infinity }).filter((r) => r.fire).map((r) => r.slug).sort();
    const mapLit = fireListAt({ courses, events, wrHistory, colors, t: Infinity }).map((e) => e.slug).sort();
```

In `web/src/lib/onFire.test.js`, replace the `fireListAt` fixture:

```js
  const wrs = { mc: 107414, rr: 80000 };
```

with:

```js
  const wrHistory = { mc: [[0, 107414]], rr: [[0, 80000]] };
```

and replace the two `fireListAt({ courses, events, wrs, colors, t })` calls (lines ~38, ~44) with `wrHistory`:

```js
    const out = fireListAt({ courses, events, wrHistory, colors, t: Infinity });
```
```js
    const out = fireListAt({ courses, events, wrHistory, colors, t: 999 }); // before any event
```

(The `onFireCourses` describe block is untouched — its entries already carry `wr`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm --prefix web test -- src/lib/heat.test.js src/lib/onFire.test.js`
Expected: FAIL — `courseRowAt` reads `wrs[course.slug]` (now `undefined`), so rows are dropped (`!wr`) and the lit sets are empty / mismatched.

- [ ] **Step 3: Implement the param + resolution change**

In `web/src/lib/heat.js`, update the import:

```js
import { leaderboardAt, wrAsOf } from "./timeline.js";
```

Replace `courseRowAt`'s signature and WR lookup:

```js
export function courseRowAt({ course, events, wrHistory, colors, t }) {
  const board = leaderboardAt(events, course.slug, t);
  const wr = wrAsOf(wrHistory, course.slug, t);
  if (board.length < 2 || !wr) return null;
```

(everything below the guard is unchanged). Update `heatRows`:

```js
export function heatRows({ courses, events, wrHistory, colors, t }) {
  const rows = [];
  for (const c of courses) {
    const row = courseRowAt({ course: c, events, wrHistory, colors, t });
    if (row) rows.push(row);
  }
  return rows;
}
```

In `web/src/lib/onFire.js`, update `fireListAt`:

```js
export function fireListAt({ courses, events, wrHistory, colors, t }) {
  const out = [];
  for (const c of courses) {
    const row = courseRowAt({ course: c, events, wrHistory, colors, t });
    if (row && row.fire) out.push({ slug: row.slug, hit: c.hit, color: row.color, t1: row.t1, t2: row.t2, wr: row.wr });
  }
  return out;
}
```

Also update the doc comment on `fireListAt` (the `wrs` mention) to say `wrHistory`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm --prefix web test -- src/lib/heat.test.js src/lib/onFire.test.js`
Expected: PASS (including the heat↔map parity test — still true by construction).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/heat.js web/src/lib/onFire.js web/src/lib/heat.test.js web/src/lib/onFire.test.js
git commit -m "feat(territory): fire/heat use as-of-frame WR via wrHistory"
```

---

### Task 4: wire the Svelte consumers (`WorldMap.svelte`, `HeatGraph.svelte`)

**Files:**
- Modify: `web/src/WorldMap.svelte`
- Modify: `web/src/HeatGraph.svelte`

**Interfaces:**
- Consumes: the `wrHistory` payload key (Task 2); `wrAsOf` (Task 1); `fireListAt`/`heatRows` with the `wrHistory` param (Task 3).
- Produces: nothing downstream (UI leaves). Verified by `svelte-check` + `vite build` (no unit tests for `.svelte` here).

- [ ] **Step 1: Update `WorldMap.svelte`**

Add `wrAsOf` to the timeline import:

```js
  import { buildSnapshots, flippedCourses, leaderboardAt, wrAsOf } from "./lib/timeline.js";
```

Rename the retained-state declaration:

```js
  let tlEvents = [], tlColors = {}, tlWrHistory = {};   // retained run stream + colour + WR-history maps
```

Destructure the new payload key (the `await res.json()` line):

```js
      const { events, colors, wrHistory } = await res.json();
```

Assign it (the `tlEvents = events; ...` line):

```js
      tlEvents = events; tlColors = colors; tlWrHistory = wrHistory || {};   // kept for the unified board + fire
```

Update the fire-list reactive call (pass `wrHistory`):

```js
    ? fireListAt({ courses: manifest.courses, events: tlEvents, wrHistory: tlWrHistory, colors: tlColors, t: frameTime })
```

Update the popup builder call (resolve the as-of WR; the comment above it too):

```js
    // One path for every frame: the board AS OF the shown moment, reconstructed from the
    // in-memory event stream (matches the territory colours). WR is the as-of-frame WR.
    // A course with no runs by then has no board -> no popup opens.
    const standings = leaderboardAt(tlEvents, course.slug, frameTime);
    const v = standings.length
      ? buildCourseView({ standings, colorByName: tlColors, courseName: course.name, wr: wrAsOf(tlWrHistory, course.slug, frameTime) })
      : null;
```

- [ ] **Step 2: Update `HeatGraph.svelte`**

Replace the destructure + `heatRows` call (the two adjacent lines):

```js
      const { events, colors, wrHistory } = tl;
      rows = heatRows({ courses: mf.courses, events: events || [], wrHistory: wrHistory || {}, colors: colors || {}, t: Infinity });
```

- [ ] **Step 3: Type-check**

Run: `npm --prefix web run check`
Expected: `svelte-check` 0 errors / 0 warnings (no remaining `tlWrs`/`wrs` references).

- [ ] **Step 4: Build**

Run: `npm --prefix web run build`
Expected: `vite build` completes clean.

- [ ] **Step 5: Full web test sweep**

Run: `npm --prefix web test`
Expected: PASS (entire web vitest suite green — confirms no fixture/usage drift).

- [ ] **Step 6: Commit**

```bash
git add web/src/WorldMap.svelte web/src/HeatGraph.svelte
git commit -m "feat(territory): WorldMap + HeatGraph consume wrHistory (as-of-frame WR)"
```

---

## Verification (after all tasks)

- `npm --prefix pi test` — full server suite green.
- `npm --prefix web test` — full web suite green (incl. heat↔map parity).
- `npm --prefix web run check` — 0/0.
- `npm --prefix web run build` — clean.
- Manual (user, live): scrub the territory timeline back — a course popup's WR number rolls to the older record; flames re-evaluate against the as-of WR; LIVE frame matches today; `#/heat` lit dots match `#/territory` flames.

## Notes for the implementer

- No new server route; same `GET /v1/territory/timeline?cc=150`.
- `onFireCourses` and `CoursePopup.svelte`/`buildCourseView` are intentionally **not** changed — the popup still receives a resolved `record_ms` number.
- The `/v1/territory` fallback path (`renderTerritory`) never reads WRs, so it's untouched.
- If `npm --prefix web test -- <file>` runs the whole suite on your vitest version, narrow with `-t <name>` or just run the full `npm --prefix web test`; the suite is fast.

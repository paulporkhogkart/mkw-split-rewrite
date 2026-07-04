# Tracks Data Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a complete, tested `/tracks` section of thekartoff.com — an index that is a wall of per-track leaderboard cards (plus an Overall-time card and a name search), and a per-track hub page showing the leaderboard, world record, on-fire target time, lap splits, and history. The track map/canvas is deferred to Plan 2.

**Architecture:** Mirror the players page. One new **public** Pi endpoint `GET /v1/courses/:slug` bundles the hub's tables (WR, leaderboard, splits, history), assembled from existing DB reads. The web index reuses the turf map's data path (`/v1/territory` + `/v1/territory/timeline` → `courseData.js`/`timeline.js`) so all 30 boards render from two fetches with no new endpoint. Pure client math (`fireTarget.js`, `courseSplits.js`, `overallBoard.js`) is unit-tested; Svelte components are browser-verified.

**Tech Stack:** Pi = Node/TS + Hono + `node:sqlite`, run via `tsx`, tested with vitest. Web = Vite + Svelte SPA, tested with vitest, History-API routing. No build step on the Pi.

## Global Constraints

- **Public label "Tracks", internal "course".** Only the URL path (`/tracks`) and visible UI text read "Tracks"; every identifier (components `CoursesIndex`/`CourseProfile`, view key `"courses"`, `courseSlugFromPath`, `courseSummaryUrl`, API `/v1/courses/...`, DB `courses`) stays "course".
- **`cc=150` everywhere** (the only class in v1). Every course query takes `cc`, defaulting to 150.
- **Public endpoint gating is load-bearing** (`pi/src/api/app.ts`): a new public read needs a CORS `app.use` + an `isOpen` regex exception; single path segment only — never open a two-segment route.
- **Course slugs are canonical** (`courses.slug`); resolve via `courseIdBySlug(db, slug)` → `null` → 404. No client slug-mirror.
- **Tests colocated**, vitest both sides: `npm --prefix pi test`, `npm --prefix web test`. Keep pi source + tests tsc-clean (`npm --prefix pi run typecheck`).
- **Web dev server:** `npm --prefix web run dev` → open `http://127.0.0.1:1430` (NOT localhost). Verify components in a **real browser**, never OpenCV.
- **Commit after each task.** Frequent commits; TDD (failing test first).

---

### Task 1: Per-track lap splits (`courseSplits`)

**Files:**
- Create: `pi/src/db/courseSummary.ts` (the `courseSplits` function; the assembler is added in Task 3)
- Test: `pi/src/db/courseSummary.test.ts`

**Interfaces:**
- Produces: `courseSplits(db, seasonId, courseId, cc): CourseSplits` where
  `CourseSplits = { laps: number; perPlayer: { player_id: number; display_name: string; color: string | null; best: (number | null)[] }[]; fieldIdeal: (number | null)[] }`.
  `best[i]` is the player's fastest **lap-duration** for lap `i+1` (or null); `fieldIdeal[i]` is the min across players.

- [ ] **Step 1: Write the failing test**

```ts
// pi/src/db/courseSummary.test.ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { courseSplits } from './courseSummary';

function seededLaps() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Paul','#f00'),(2,'Luke','#0f0')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
  // Two finished runs by Paul (ids 10,11) and one by Luke (id 12), 3 laps each.
  db.exec(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES
    (10,1,1,1,150,'finished','live',110000,0),
    (11,1,1,1,150,'finished','live',108000,1),
    (12,1,2,1,150,'finished','live',109000,1)`);
  db.exec(`INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES
    (10,1,40000),(10,2,35000),(10,3,35000),
    (11,1,39000),(11,2,34000),(11,3,35000),
    (12,1,38000),(12,2,36000),(12,3,35000)`);
  return db;
}

describe('courseSplits', () => {
  it('takes each player\'s fastest lap per index and the per-lap field ideal', () => {
    const s = courseSplits(seededLaps(), 1, 1, 150);
    expect(s.laps).toBe(3);
    const paul = s.perPlayer.find(p => p.display_name === 'Paul')!;
    expect(paul.color).toBe('#f00');
    expect(paul.best).toEqual([39000, 34000, 35000]); // min across runs 10 & 11
    const luke = s.perPlayer.find(p => p.display_name === 'Luke')!;
    expect(luke.best).toEqual([38000, 36000, 35000]);
    expect(s.fieldIdeal).toEqual([38000, 34000, 35000]); // min across players per lap
  });

  it('returns empty structure when the course has no lap data', () => {
    const db = openDb(':memory:');
    applySchema(db);
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
    expect(courseSplits(db, 1, 1, 150)).toEqual({ laps: 0, perPlayer: [], fieldIdeal: [] });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix pi test -- courseSummary`
Expected: FAIL — `courseSplits` is not exported / module missing.

- [ ] **Step 3: Write minimal implementation**

```ts
// pi/src/db/courseSummary.ts
import type { DatabaseSync } from 'node:sqlite';

export interface CourseSplits {
  laps: number;
  perPlayer: { player_id: number; display_name: string; color: string | null; best: (number | null)[] }[];
  fieldIdeal: (number | null)[];
}

/** Each player's fastest lap-duration per lap index over finished runs, plus the per-lap
 *  field ideal (min across players). `best`/`fieldIdeal` are length `laps` (max lap seen). */
export function courseSplits(db: DatabaseSync, seasonId: number, courseId: number, cc: number): CourseSplits {
  const rows = db.prepare(
    `SELECT r.player_id AS pid, p.display_name AS name, p.color AS color,
            rl.lap_index AS lap, MIN(rl.lap_time_ms) AS best
     FROM run_laps rl
     JOIN runs r ON r.id = rl.run_id
     JOIN players p ON p.id = r.player_id
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.status='finished' AND rl.lap_time_ms IS NOT NULL
     GROUP BY r.player_id, rl.lap_index`
  ).all(seasonId, courseId, cc) as { pid: number; name: string; color: string | null; lap: number; best: number }[];

  const laps = rows.reduce((m, r) => Math.max(m, r.lap), 0);
  const byPlayer = new Map<number, { name: string; color: string | null; best: (number | null)[] }>();
  for (const r of rows) {
    let e = byPlayer.get(r.pid);
    if (!e) { e = { name: r.name, color: r.color, best: new Array(laps).fill(null) }; byPlayer.set(r.pid, e); }
    e.best[r.lap - 1] = r.best;
  }
  const perPlayer = [...byPlayer.entries()]
    .map(([player_id, e]) => ({ player_id, display_name: e.name, color: e.color, best: e.best }))
    .sort((a, b) => (a.display_name < b.display_name ? -1 : a.display_name > b.display_name ? 1 : 0));
  const fieldIdeal: (number | null)[] = new Array(laps).fill(null);
  for (let l = 0; l < laps; l++)
    for (const p of perPlayer) { const v = p.best[l]; if (v != null && (fieldIdeal[l] == null || v < fieldIdeal[l]!)) fieldIdeal[l] = v; }
  return { laps, perPlayer, fieldIdeal };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix pi test -- courseSummary`
Expected: PASS (both cases).

- [ ] **Step 5: Commit**

```bash
git add pi/src/db/courseSummary.ts pi/src/db/courseSummary.test.ts
git commit -m "feat(pi): per-track lap splits aggregation (best lap + field ideal)"
```

---

### Task 2: Per-track history (`courseHistory`)

**Files:**
- Create: `pi/src/db/courseHistory.ts`
- Test: `pi/src/db/courseHistory.test.ts`

**Interfaces:**
- Produces:
  - `recordProgression(db, seasonId, courseId, cc): { t: number; player: string; ms: number }[]` — each time the local record dropped.
  - `courseReigns(db, seasonId, courseId, cc): { player: string; from: number; to: number | null; ms: number | null }[]` — #1-ownership spans; last has `to=null, ms=null` (ongoing).
  - `wrHistoryRows(db, courseId, cc): { t: number | null; holder_name: string | null; record_ms: number; video_url: string | null }[]` — WR history ascending.

- [ ] **Step 1: Write the failing test**

```ts
// pi/src/db/courseHistory.test.ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { recordProgression, courseReigns, wrHistoryRows } from './courseHistory';

function seeded() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
  // Timeline: Paul 110s (owns), Luke 109s (takes #1), Paul 108s (retakes #1).
  db.exec(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb,ended_at) VALUES
    (1,1,1,1,150,'finished','live',110000,0,'2026-01-01T00:00:00Z'),
    (2,1,2,1,150,'finished','live',109000,0,'2026-01-02T00:00:00Z'),
    (3,1,1,1,150,'finished','live',108000,1,'2026-01-03T00:00:00Z')`);
  db.exec(`INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,video_url,is_current,removed_at,achieved_at) VALUES
    (1,150,'WRHolderA',105000,'1:45','http://v/a',0,NULL,'2025-12-01T00:00:00Z'),
    (1,150,'WRHolderB',104000,'1:44','http://v/b',1,NULL,'2025-12-20T00:00:00Z')`);
  return db;
}

describe('recordProgression', () => {
  it('emits an entry each time the local record falls', () => {
    const p = recordProgression(seeded(), 1, 1, 150);
    expect(p.map(e => [e.player, e.ms])).toEqual([['Paul', 110000], ['Luke', 109000], ['Paul', 108000]]);
    expect(p[0].t).toBe(Date.parse('2026-01-01T00:00:00Z'));
  });
});

describe('courseReigns', () => {
  it('spans #1 ownership, last reign ongoing', () => {
    const r = courseReigns(seeded(), 1, 1, 150);
    expect(r.map(x => x.player)).toEqual(['Paul', 'Luke', 'Paul']);
    expect(r[0].to).toBe(Date.parse('2026-01-02T00:00:00Z'));
    expect(r[0].ms).toBe(Date.parse('2026-01-02T00:00:00Z') - Date.parse('2026-01-01T00:00:00Z'));
    expect(r[2].to).toBeNull();
    expect(r[2].ms).toBeNull();
  });
});

describe('wrHistoryRows', () => {
  it('returns WR history ascending with videos', () => {
    const w = wrHistoryRows(seeded(), 1, 150);
    expect(w.map(x => x.holder_name)).toEqual(['WRHolderA', 'WRHolderB']);
    expect(w[1].record_ms).toBe(104000);
    expect(w[1].video_url).toBe('http://v/b');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix pi test -- courseHistory`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```ts
// pi/src/db/courseHistory.ts
import type { DatabaseSync } from 'node:sqlite';

type FinRow = { name: string; ms: number; ended_at: string };
function finishedRuns(db: DatabaseSync, seasonId: number, courseId: number, cc: number): FinRow[] {
  return db.prepare(
    `SELECT p.display_name AS name, r.total_time_ms AS ms, r.ended_at AS ended_at
     FROM runs r JOIN players p ON p.id = r.player_id
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.status='finished'
       AND r.total_time_ms IS NOT NULL AND r.ended_at IS NOT NULL
     ORDER BY r.ended_at ASC, r.id ASC`
  ).all(seasonId, courseId, cc) as FinRow[];
}

export interface ProgressionPoint { t: number; player: string; ms: number; }
export function recordProgression(db: DatabaseSync, seasonId: number, courseId: number, cc: number): ProgressionPoint[] {
  const out: ProgressionPoint[] = [];
  let best = Infinity;
  for (const r of finishedRuns(db, seasonId, courseId, cc)) {
    const t = Date.parse(r.ended_at);
    if (r.ms < best && Number.isFinite(t)) { best = r.ms; out.push({ t, player: r.name, ms: r.ms }); }
  }
  return out;
}

export interface Reign { player: string; from: number; to: number | null; ms: number | null; }
export function courseReigns(db: DatabaseSync, seasonId: number, courseId: number, cc: number): Reign[] {
  const best = new Map<string, number>();
  let leader: string | null = null, reignStart = 0;
  const reigns: Reign[] = [];
  for (const r of finishedRuns(db, seasonId, courseId, cc)) {
    const t = Date.parse(r.ended_at);
    if (!Number.isFinite(t)) continue;
    const cur = best.get(r.name);
    if (cur === undefined || r.ms < cur) best.set(r.name, r.ms);
    let lname: string | null = null, lmin = Infinity;
    for (const [n, m] of best) if (m < lmin) { lmin = m; lname = n; }
    if (lname !== leader) {
      if (leader !== null) reigns.push({ player: leader, from: reignStart, to: t, ms: t - reignStart });
      leader = lname; reignStart = t;
    }
  }
  if (leader !== null) reigns.push({ player: leader, from: reignStart, to: null, ms: null });
  return reigns;
}

export interface WrRow { t: number | null; holder_name: string | null; record_ms: number; video_url: string | null; }
export function wrHistoryRows(db: DatabaseSync, courseId: number, cc: number): WrRow[] {
  const rows = db.prepare(
    `SELECT holder_name, record_ms, video_url, achieved_at FROM world_records
     WHERE course_id=? AND cc=? AND removed_at IS NULL ORDER BY achieved_at ASC, id ASC`
  ).all(courseId, cc) as { holder_name: string | null; record_ms: number; video_url: string | null; achieved_at: string | null }[];
  return rows.map((r) => {
    const t = r.achieved_at ? Date.parse(r.achieved_at) : NaN;
    return { t: Number.isFinite(t) ? t : null, holder_name: r.holder_name, record_ms: r.record_ms, video_url: r.video_url };
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix pi test -- courseHistory`
Expected: PASS (all three describes).

- [ ] **Step 5: Commit**

```bash
git add pi/src/db/courseHistory.ts pi/src/db/courseHistory.test.ts
git commit -m "feat(pi): per-track history (record progression, reigns, WR history)"
```

---

### Task 3: Course summary assembler + public endpoint + gating

**Files:**
- Modify: `pi/src/db/courseSummary.ts` (add `courseSummary` below `courseSplits`)
- Modify: `pi/src/db/courseSummary.test.ts` (add assembler tests)
- Modify: `pi/src/api/reads.ts` (add the route)
- Modify: `pi/src/api/app.ts` (CORS + `isOpen` regex)
- Test: `pi/src/api/courses.test.ts` (HTTP + gate/CORS)

**Interfaces:**
- Consumes: `courseSplits` (Task 1); `recordProgression`/`courseReigns`/`wrHistoryRows` (Task 2); `courseIdBySlug` (`db/seasons.ts`), `courseLeaderboard`/`currentWr` (`db/reads.ts`); route helpers `season(c)` + `num(...)` already in `reads.ts`.
- Produces: `courseSummary(db, seasonId, cc, slug): CourseSummary | null` and `GET /v1/courses/:slug?cc=` (public).

- [ ] **Step 1: Write the failing test (assembler)**

Append to `pi/src/db/courseSummary.test.ts`:

```ts
import { courseSummary } from './courseSummary';
import { currentWr } from './reads';

describe('courseSummary', () => {
  function seeded() {
    const db = openDb(':memory:');
    applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
    db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Paul','#f00'),(2,'Luke','#0f0')");
    db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
    db.exec(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb,ended_at) VALUES
      (1,1,2,1,150,'finished','live',108000,1,'2026-01-02T00:00:00Z'),
      (2,1,1,1,150,'finished','live',110000,1,'2026-01-03T00:00:00Z')`);
    db.exec("INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES (1,1,40000),(1,2,34000),(2,1,41000),(2,2,35000)");
    db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,video_url,is_current,achieved_at) VALUES (1,150,'WR',100000,'1:40','http://v',1,'2025-12-01T00:00:00Z')");
    return db;
  }

  it('assembles profile, wr, coloured leaderboard, splits, and history', () => {
    const s = courseSummary(seeded(), 1, 150, 'rr')!;
    expect(s.profile.display_name).toBe('Rainbow Road');
    expect(s.wr!.record_ms).toBe(100000);
    expect(s.wr!.video_url).toBe('http://v');
    expect(s.leaderboard.map(r => [r.display_name, r.rank, r.color])).toEqual([['Luke', 1, '#0f0'], ['Paul', 2, '#f00']]);
    expect(s.splits.laps).toBe(2);
    expect(s.history.recordProgression.length).toBeGreaterThan(0);
    expect(s.history.wrHistory[0].holder_name).toBe('WR');
  });

  it('returns null for an unknown slug', () => {
    expect(courseSummary(seeded(), 1, 150, 'nope')).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix pi test -- courseSummary`
Expected: FAIL — `courseSummary` not exported.

- [ ] **Step 3: Implement the assembler**

Add these three imports at the **top** of `pi/src/db/courseSummary.ts` (beside the existing `DatabaseSync` import), then append the interfaces + `courseSummary` after `courseSplits`:

```ts
import { courseIdBySlug } from './seasons';
import { courseLeaderboard, currentWr } from './reads';
import { recordProgression, courseReigns, wrHistoryRows, ProgressionPoint, Reign, WrRow } from './courseHistory';

export interface CourseLeaderRow {
  player_id: number; display_name: string; color: string | null;
  total_time_ms: number; total_time_str: string | null; rank: number;
}
export interface CourseSummary {
  profile: { slug: string; display_name: string };
  wr: { holder_name: string | null; record_ms: number; record_str: string | null; video_url: string | null; character: string | null; vehicle: string | null } | null;
  leaderboard: CourseLeaderRow[];
  splits: CourseSplits;
  history: { recordProgression: ProgressionPoint[]; reigns: Reign[]; wrHistory: WrRow[] };
}

/** The per-track hub payload, resolving :slug via courseIdBySlug. Null on unknown slug. */
export function courseSummary(db: DatabaseSync, seasonId: number, cc: number, slug: string): CourseSummary | null {
  const courseId = courseIdBySlug(db, slug);
  if (courseId == null) return null;
  const course = db.prepare('SELECT slug, display_name FROM courses WHERE id=?').get(courseId) as { slug: string; display_name: string };
  const colors = new Map<number, string | null>();
  for (const p of db.prepare('SELECT id, color FROM players').all() as { id: number; color: string | null }[]) colors.set(p.id, p.color);
  const leaderboard: CourseLeaderRow[] = courseLeaderboard(db, seasonId, courseId, cc)
    .map((r) => ({ player_id: r.player_id, display_name: r.display_name, color: colors.get(r.player_id) ?? null, total_time_ms: r.total_time_ms, total_time_str: r.total_time_str, rank: r.rank }));
  const wr = currentWr(db, courseId, cc);
  return {
    profile: { slug: course.slug, display_name: course.display_name },
    wr: wr ? { holder_name: wr.holder_name, record_ms: wr.record_ms, record_str: wr.record_str, video_url: wr.video_url, character: wr.character, vehicle: wr.vehicle } : null,
    leaderboard,
    splits: courseSplits(db, seasonId, courseId, cc),
    history: {
      recordProgression: recordProgression(db, seasonId, courseId, cc),
      reigns: courseReigns(db, seasonId, courseId, cc),
      wrHistory: wrHistoryRows(db, courseId, cc),
    },
  };
}
```

- [ ] **Step 4: Run assembler test to verify it passes**

Run: `npm --prefix pi test -- courseSummary`
Expected: PASS.

- [ ] **Step 5: Add the route + gating, and the HTTP test**

Create `pi/src/api/courses.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { createApp } from './app';

function appWithData() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Paul','#f00')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
  db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb,ended_at) VALUES (1,1,1,1,150,'finished','live',110000,1,'2026-01-01T00:00:00Z')");
  return createApp(db, new EventHub());
}

describe('GET /v1/courses/:slug', () => {
  it('serves a summary with no token and CORS headers', async () => {
    const res = await appWithData().request('/v1/courses/rr');
    expect(res.status).toBe(200);
    expect(res.headers.get('access-control-allow-origin')).toBe('*');
    const body = await res.json();
    expect(body.profile.display_name).toBe('Rainbow Road');
    expect(body.leaderboard[0].display_name).toBe('Paul');
  });

  it('404s an unknown slug', async () => {
    const res = await appWithData().request('/v1/courses/nope');
    expect(res.status).toBe(404);
  });
});
```

In `pi/src/api/reads.ts`, add the import near the other db imports:

```ts
import { courseSummary } from '../db/courseSummary';
```

and add this route inside `readsRoutes(db)`, next to the `/v1/players/:slug` route:

```ts
r.get('/v1/courses/:slug', (c) => {
  const s = courseSummary(db, season(c), num(c.req.query('cc'), 150), c.req.param('slug'));
  return s ? c.json(s) : c.json({ error: 'unknown course' }, 404);
});
```

In `pi/src/api/app.ts`, after the `app.use('/v1/players/:slug', readCors);` line add:

```ts
app.use('/v1/courses/:slug', readCors);   // single-segment course summary is public
```

and after the `const PLAYER_SUMMARY = ...;` line add:

```ts
const COURSE_SUMMARY = /^\/v1\/courses\/[^/]+$/;   // Plan 2 extends this for /model|/trails|/heatmap
```

and extend `isOpen` to include it:

```ts
const isOpen = (path: string) => OPEN.has(path) || PLAYER_SUMMARY.test(path) || COURSE_SUMMARY.test(path);
```

- [ ] **Step 6: Run the HTTP test + typecheck**

Run: `npm --prefix pi test -- courses` then `npm --prefix pi run typecheck`
Expected: PASS (200 + CORS, 404); typecheck clean.

- [ ] **Step 7: Commit**

```bash
git add pi/src/db/courseSummary.ts pi/src/db/courseSummary.test.ts pi/src/api/reads.ts pi/src/api/app.ts pi/src/api/courses.test.ts
git commit -m "feat(pi): public GET /v1/courses/:slug hub summary (wr, leaderboard, splits, history)"
```

---

### Task 4: Web routing + API url builder

**Files:**
- Modify: `web/src/lib/view.js`
- Modify: `web/src/lib/api.js`
- Modify: `web/src/lib/view.test.js`

**Interfaces:**
- Produces: `viewFromPath("/tracks") === "courses"`, `courseSlugFromPath("/tracks/rr") === "rr"`, `courseSummaryUrl(slug, cc=150)`.

- [ ] **Step 1: Write the failing test**

Append to `web/src/lib/view.test.js`:

```js
import { courseSlugFromPath } from "./view.js";

describe("tracks routing", () => {
  it("routes /tracks and /tracks/:slug to the courses view", () => {
    expect(viewFromPath("/tracks")).toBe("courses");
    expect(viewFromPath("/tracks/rainbow_road")).toBe("courses");
  });
  it("extracts the course slug (null on the index)", () => {
    expect(courseSlugFromPath("/tracks")).toBeNull();
    expect(courseSlugFromPath("/tracks/rainbow_road")).toBe("rainbow_road");
    expect(courseSlugFromPath("/players/paul")).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix web test -- view`
Expected: FAIL — `courseSlugFromPath` undefined / "courses" not returned.

- [ ] **Step 3: Implement**

In `web/src/lib/view.js`, add the route line inside `viewFromPath` (before `return "live"`):

```js
  if (p === "tracks" || p.startsWith("tracks/")) return "courses";
```

and add the exported helper:

```js
/** The course slug from /tracks/:slug, or null on /tracks (index) and non-track paths. */
export function courseSlugFromPath(pathname) {
  const p = (pathname || "/").replace(/^\/+/, "").replace(/\/+$/, "");
  const m = /^tracks\/([^/]+)/.exec(p);
  return m ? m[1] : null;
}
```

In `web/src/lib/api.js`, add:

```js
export const courseSummaryUrl = (slug, cc = 150) => `${API_BASE}/v1/courses/${encodeURIComponent(slug)}?cc=${cc}`;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix web test -- view`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/view.js web/src/lib/api.js web/src/lib/view.test.js
git commit -m "feat(web): /tracks routing + courseSummaryUrl"
```

---

### Task 5: On-fire target math (`fireTarget.js`)

**Files:**
- Create: `web/src/lib/fireTarget.js`
- Test: `web/src/lib/fireTarget.test.js`

**Interfaces:**
- Consumes: `fireBarPct` from `web/src/lib/fireModel.js`.
- Produces: `fireTargetMs({ leaderMs, wr }): { ms: number | null, reason: "ok" | "no-wr" | "wr-tight" }`.

- [ ] **Step 1: Write the failing test**

```js
// web/src/lib/fireTarget.test.js
import { describe, it, expect } from "vitest";
import { fireTargetMs } from "./fireTarget.js";
import { fireBarPct } from "./fireModel.js";

describe("fireTargetMs", () => {
  it("returns a target strictly between WR and the current leader, on the fire bar", () => {
    const wr = 100000, leaderMs = 110000;
    const { ms, reason } = fireTargetMs({ leaderMs, wr });
    expect(reason).toBe("ok");
    expect(ms).toBeGreaterThan(wr);
    expect(ms).toBeLessThan(leaderMs);
    // at the target, lead% ~= bar% (the crossing)
    const leadPct = ((leaderMs - ms) / wr) * 100;
    const barPct = fireBarPct(((ms - wr) / wr) * 100);
    expect(leadPct).toBeCloseTo(barPct, 2);
  });

  it("is null when there is no WR", () => {
    expect(fireTargetMs({ leaderMs: 110000, wr: null }).ms).toBeNull();
    expect(fireTargetMs({ leaderMs: null, wr: 100000 }).ms).toBeNull();
  });

  it("is wr-tight when even WR pace cannot clear the bar", () => {
    const r = fireTargetMs({ leaderMs: 100100, wr: 100000 }); // leader only 0.1% off WR (< E0=0.2)
    expect(r.ms).toBeNull();
    expect(r.reason).toBe("wr-tight");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix web test -- fireTarget`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```js
// web/src/lib/fireTarget.js
import { fireBarPct } from "./fireModel.js";

/** The PB time that seizes #1 AND lights the track: lead the current leader by the fire bar,
 *  evaluated at your own % off WR. Returns { ms, reason }; ms is null when impossible. */
export function fireTargetMs({ leaderMs, wr }) {
  if (wr == null || leaderMs == null) return { ms: null, reason: "no-wr" };
  const lit = (T) => ((leaderMs - T) / wr) * 100 >= fireBarPct(((T - wr) / wr) * 100);
  if (!lit(wr)) return { ms: null, reason: "wr-tight" }; // leader too close to WR to ever be out-lit
  // Largest T in [wr, leaderMs] that is still lit — bisect the crossing.
  let lo = wr, hi = leaderMs;
  for (let i = 0; i < 60; i++) { const mid = (lo + hi) / 2; if (lit(mid)) lo = mid; else hi = mid; }
  return { ms: lo, reason: "ok" };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix web test -- fireTarget`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/fireTarget.js web/src/lib/fireTarget.test.js
git commit -m "feat(web): on-fire target-time math (fireTargetMs)"
```

---

### Task 6: Splits + overall-board client math

**Files:**
- Create: `web/src/lib/courseSplits.js`, `web/src/lib/overallBoard.js`
- Test: `web/src/lib/courseSplits.test.js`, `web/src/lib/overallBoard.test.js`

**Interfaces:**
- Produces:
  - `withTheoretical(splits): { laps, perPlayer: [{...p, theoretical: number|null}], fieldIdeal, fieldIdealTotal: number|null }`.
  - `overallBoard(boards): [{ player, total_ms, tracks }]` fastest-first, where `boards = [{ standings: [{player, ms}] }]`.

- [ ] **Step 1: Write the failing tests**

```js
// web/src/lib/courseSplits.test.js
import { describe, it, expect } from "vitest";
import { withTheoretical } from "./courseSplits.js";

describe("withTheoretical", () => {
  it("sums each player's best laps; null when any lap is missing", () => {
    const splits = {
      laps: 3,
      perPlayer: [
        { player_id: 1, display_name: "Paul", color: "#f00", best: [39000, 34000, 35000] },
        { player_id: 2, display_name: "Luke", color: "#0f0", best: [38000, null, 35000] },
      ],
      fieldIdeal: [38000, 34000, 35000],
    };
    const r = withTheoretical(splits);
    expect(r.perPlayer[0].theoretical).toBe(108000);
    expect(r.perPlayer[1].theoretical).toBeNull();
    expect(r.fieldIdealTotal).toBe(107000);
  });

  it("field ideal total is null when a lap is unset", () => {
    expect(withTheoretical({ laps: 2, perPlayer: [], fieldIdeal: [40000, null] }).fieldIdealTotal).toBeNull();
  });
});
```

```js
// web/src/lib/overallBoard.test.js
import { describe, it, expect } from "vitest";
import { overallBoard } from "./overallBoard.js";

describe("overallBoard", () => {
  it("sums each player's per-track bests, fastest total first, with a track count", () => {
    const boards = [
      { standings: [{ player: "Paul", ms: 110000 }, { player: "Luke", ms: 108000 }] },
      { standings: [{ player: "Paul", ms: 90000 }] },
    ];
    const r = overallBoard(boards);
    expect(r).toEqual([
      { player: "Luke", total_ms: 108000, tracks: 1 },
      { player: "Paul", total_ms: 200000, tracks: 2 },
    ]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm --prefix web test -- courseSplits overallBoard`
Expected: FAIL — modules missing.

- [ ] **Step 3: Implement**

```js
// web/src/lib/courseSplits.js
/** Sum an array of lap times; null if it is empty or any entry is null. */
function sumLaps(arr) {
  if (!arr.length) return null;
  let s = 0;
  for (const v of arr) { if (v == null) return null; s += v; }
  return s;
}

/** Augment a courseSummary.splits with each player's theoretical best (sum of their best laps)
 *  and the field-ideal total (sum of the per-lap field ideal). */
export function withTheoretical(splits) {
  return {
    laps: splits.laps,
    perPlayer: splits.perPlayer.map((p) => ({ ...p, theoretical: sumLaps(p.best) })),
    fieldIdeal: splits.fieldIdeal,
    fieldIdealTotal: sumLaps(splits.fieldIdeal),
  };
}
```

```js
// web/src/lib/overallBoard.js
/** Overall standings from per-course boards: sum each player's per-track best time.
 *  boards: [{ standings: [{ player, ms }] }] -> [{ player, total_ms, tracks }] fastest total first. */
export function overallBoard(boards) {
  const sum = {}, cnt = {};
  for (const b of boards)
    for (const s of b.standings) { sum[s.player] = (sum[s.player] || 0) + s.ms; cnt[s.player] = (cnt[s.player] || 0) + 1; }
  return Object.keys(sum)
    .map((player) => ({ player, total_ms: sum[player], tracks: cnt[player] }))
    .sort((a, b) => a.total_ms - b.total_ms);
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm --prefix web test -- courseSplits overallBoard`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/courseSplits.js web/src/lib/courseSplits.test.js web/src/lib/overallBoard.js web/src/lib/overallBoard.test.js
git commit -m "feat(web): theoretical-best + overall-board client math"
```

---

### Task 7: Tracks index (`CoursesIndex.svelte`) + nav wiring

**Files:**
- Create: `web/src/CoursesIndex.svelte`
- Create: `web/src/CourseProfile.svelte` (minimal stub; filled in Task 8)
- Modify: `web/src/App.svelte` (import, nav tab, `courseSlug` wiring, render branch)

**Interfaces:**
- Consumes: `territoryUrl`/`territoryTimelineUrl` (`lib/api.js`), `leaderboardAt`/`wrAsOf` (`lib/timeline.js`), `buildCourseView`/`preloadPlayerGifs`/`freshGifUrl` (`lib/courseData.js`), `overallBoard` (Task 6), `CoursePopup.svelte`, `viewFromPath`/`courseSlugFromPath` (Task 4).
- Produces: navigable `/tracks` index; App renders `CoursesIndex` (no slug) / `CourseProfile` (slug).

- [ ] **Step 1: Create the CourseProfile stub**

```svelte
<!-- web/src/CourseProfile.svelte -->
<script>
  export let slug;
</script>

<section class="wrap"><h1>{slug}</h1><p>Loading…</p></section>

<style>
  .wrap { padding: 16px; color: #e8eaed; }
</style>
```

- [ ] **Step 2: Create CoursesIndex.svelte**

```svelte
<!-- web/src/CoursesIndex.svelte -->
<script>
  import { onMount } from "svelte";
  import { territoryUrl, territoryTimelineUrl, API_BASE } from "./lib/api.js";
  import { leaderboardAt, wrAsOf } from "./lib/timeline.js";
  import { buildCourseView, preloadPlayerGifs, freshGifUrl } from "./lib/courseData.js";
  import { overallBoard } from "./lib/overallBoard.js";
  import CoursePopup from "./CoursePopup.svelte";

  let cards = [];        // [{ slug, name, view, figUrl }]
  let overall = [];      // [{ player, total_ms, tracks, rank }]
  let query = "";
  let error = null;

  const fmt = (ms) => { if (ms == null) return "—"; const s = ms/1000, m = Math.floor(s/60); return `${m}:${(s-m*60<10?"0":"")}${(s-m*60).toFixed(3)}`; };

  onMount(async () => {
    try {
      const [courses, tl] = await Promise.all([
        fetch(territoryUrl()).then((r) => r.json()),
        fetch(territoryTimelineUrl()).then((r) => r.json()),
      ]);
      const { events, colors, wrHistory } = tl;
      const ordered = [...courses].sort((a, b) => a.course_id - b.course_id);
      const boards = ordered.map((c) => ({ slug: c.slug, name: c.display_name, standings: leaderboardAt(events, c.slug, Infinity) }));
      cards = boards.map((b) => ({
        slug: b.slug, name: b.name,
        view: buildCourseView({ standings: b.standings, colorByName: colors, courseName: b.name, wr: wrAsOf(wrHistory, b.slug, Infinity) }),
        figUrl: "",
      }));
      overall = overallBoard(boards).map((o, i) => ({ ...o, rank: i + 1, color: colors[o.player] || "#888" }));
      await preloadPlayerGifs(API_BASE);
      cards = cards.map((c) => ({ ...c, figUrl: c.view.gifUrl ? freshGifUrl(c.view.gifUrl) : "" }));
    } catch (e) { error = String(e); }
  });

  $: filtered = cards.filter((c) => c.name.toLowerCase().includes(query.trim().toLowerCase()));
</script>

<section class="page">
  {#if error}<p class="err">Couldn't load tracks: {error}</p>{/if}

  <div class="overall">
    <div class="head">Overall — Total Time</div>
    {#each overall as o (o.player)}
      <div class="orow"><span class="bar" style="background:{o.color}"></span><span class="rk">{o.rank}</span><span class="nm">{o.player}</span><span class="tm">{fmt(o.total_ms)}</span><span class="tk">{o.tracks} tracks</span></div>
    {/each}
  </div>

  <input class="search" placeholder="Search tracks…" bind:value={query} />

  <div class="grid">
    {#each filtered as c (c.slug)}
      <a class="card" href={`/tracks/${c.slug}`}><CoursePopup view={c.view} figUrl={c.figUrl} /></a>
    {/each}
  </div>
</section>

<style>
  .page { padding: 16px; color: #e8eaed; }
  .err { color: #f77; }
  .overall { max-width: 420px; margin: 0 0 16px; background:#121419; border:1px solid #2a2d33; border-radius:6px; padding:10px 12px; }
  .overall .head { font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:#5f656e; margin-bottom:6px; }
  .orow { display:flex; align-items:center; gap:10px; padding:2px 0; border-top:1px solid #1c1f24; }
  .orow .bar { flex:0 0 3px; width:3px; height:14px; border-radius:2px; }
  .orow .rk { flex:0 0 16px; text-align:right; color:#6f7782; font-variant-numeric:tabular-nums; }
  .orow .nm { flex:1 1 auto; }
  .orow .tm, .orow .tk { font-variant-numeric:tabular-nums; color:#9aa3ad; }
  .orow .tk { flex:0 0 auto; font-size:11px; }
  .search { width:100%; max-width:420px; margin-bottom:16px; padding:8px 10px; background:#0e1014; border:1px solid #2a2d33; border-radius:6px; color:#e8eaed; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(344px,1fr)); gap:14px; }
  .card { display:block; text-decoration:none; }
</style>
```

- [ ] **Step 3: Wire App.svelte**

Add the two component imports, and **modify the existing** `./lib/view.js` import to also pull in `courseSlugFromPath`:

```js
import CoursesIndex from "./CoursesIndex.svelte";
import CourseProfile from "./CourseProfile.svelte";
// change the existing line `import { viewFromPath, playerSlugFromPath } from "./lib/view.js";` to:
import { viewFromPath, playerSlugFromPath, courseSlugFromPath } from "./lib/view.js";
```

Add reactive state beside `playerSlug`:

```js
let courseSlug = courseSlugFromPath(typeof location !== "undefined" ? location.pathname : "/");
```

In `navigate(e)` and in the `popstate` `sync` handler, add alongside the existing `playerSlug = ...` line:

```js
  courseSlug = courseSlugFromPath(location.pathname);
```

Add the nav tab beside the others:

```svelte
<a class="tab" class:on={view === "courses"} href="/tracks" on:click={navigate}>Tracks</a>
```

Add the render branch in `<main>`:

```svelte
  {:else if view === "courses"}
    {#if courseSlug}<CourseProfile slug={courseSlug} />{:else}<CoursesIndex />{/if}
```

- [ ] **Step 4: Verify in a real browser**

Run: `npm --prefix web run dev`, open `http://127.0.0.1:1430/tracks`.
Expected: an Overall total-time card, a search box that filters the grid as you type, and a wall of track cards each showing that track's full leaderboard + WR; clicking a card navigates to `/tracks/<slug>` (stub page showing the slug). Back/forward works. (Requires the Pi dev server running with data: `npm --prefix pi run dev`.)

- [ ] **Step 5: Commit**

```bash
git add web/src/CoursesIndex.svelte web/src/CourseProfile.svelte web/src/App.svelte
git commit -m "feat(web): /tracks index — leaderboard-card wall + Overall card + search"
```

---

### Task 8: Track hub (`CourseProfile.svelte`)

**Files:**
- Modify: `web/src/CourseProfile.svelte` (replace the stub with the full hub)

**Interfaces:**
- Consumes: `courseSummaryUrl` (Task 4), `fireTargetMs` (Task 5), `withTheoretical` (Task 6), `chipUrl`/`slugify` (`lib/chips.js`), `isOnFire` (`lib/fireModel.js`), `fmtTime` (`lib/activityFormat.js`).

- [ ] **Step 1: Replace the stub with the full hub**

```svelte
<!-- web/src/CourseProfile.svelte -->
<script>
  import { courseSummaryUrl } from "./lib/api.js";
  import { fmtTime } from "./lib/activityFormat.js";
  import { fireTargetMs } from "./lib/fireTarget.js";
  import { withTheoretical } from "./lib/courseSplits.js";
  import { isOnFire } from "./lib/fireModel.js";
  import { chipUrl, slugify } from "./lib/chips.js";

  export let slug;

  let s = null;      // the summary
  let error = null;

  $: load(slug);
  async function load(sl) {
    s = null; error = null;
    try {
      const res = await fetch(courseSummaryUrl(sl));
      if (res.status === 404) { error = "No such track."; return; }
      if (!res.ok) { error = "Couldn't load this track."; return; }
      s = await res.json();
    } catch (e) { error = String(e); }
  }

  const date = (t) => (t == null ? "—" : new Date(t).toISOString().slice(0, 10));
  const days = (ms) => (ms == null ? "current" : `${Math.max(1, Math.round(ms / 86400000))}d`);
  const pctOffWr = (ms, wr) => (wr == null || ms == null ? "—" : `${(((ms - wr) / wr) * 100).toFixed(2)}%`);

  $: wrMs = s?.wr?.record_ms ?? null;
  $: leaderMs = s?.leaderboard?.[0]?.total_time_ms ?? null;
  $: lit = s ? isOnFire({ t1: leaderMs, t2: s.leaderboard?.[1]?.total_time_ms ?? null, wr: wrMs }) : false;
  $: fire = s ? fireTargetMs({ leaderMs, wr: wrMs }) : { ms: null, reason: "no-wr" };
  $: splits = s ? withTheoretical(s.splits) : null;
</script>

{#if error}
  <section class="wrap"><p class="err">{error}</p></section>
{:else if !s}
  <section class="wrap"><p>Loading…</p></section>
{:else}
<section class="wrap">
  <header class="head">
    <h1>{s.profile.display_name}</h1>
    {#if s.wr}
      <div class="wr">
        <span class="lbl">WR</span>
        <span class="tm">{fmtTime(s.wr.record_ms)}</span>
        <span class="holder">{s.wr.holder_name ?? "—"}</span>
        {#if s.wr.character}<img class="chip" src={chipUrl("combos", `${slugify(s.wr.character)}__base`)} alt="" on:error={(e) => (e.target.style.display = "none")} /><span class="lo">{s.wr.character}</span>{/if}
        {#if s.wr.vehicle}<img class="chip" src={chipUrl("karts", slugify(s.wr.vehicle))} alt="" on:error={(e) => (e.target.style.display = "none")} /><span class="lo">{s.wr.vehicle}</span>{/if}
        {#if s.wr.video_url}<a class="vid" href={s.wr.video_url} target="_blank" rel="noopener">video ↗</a>{/if}
      </div>
    {/if}
  </header>

  <!-- On-fire target -->
  <div class="fireline">
    {#if lit}🔥 This track is on fire — {s.leaderboard[0].display_name} leads by enough to burn.
    {:else if fire.ms != null}🔥 Run <b>{fmtTime(fire.ms)}</b> or faster to seize #1 and light this track.
    {:else if fire.reason === "wr-tight"}The leader is too close to the WR to be out-lit.
    {:else}Needs a WR and a second time before a track can catch fire.{/if}
  </div>

  <!-- Leaderboard -->
  <table class="board">
    <thead><tr><th>#</th><th>Player</th><th>Time</th><th>Gap</th><th>Δ WR</th></tr></thead>
    <tbody>
      {#each s.leaderboard as r (r.player_id)}
        <tr>
          <td class="num">{r.rank}</td>
          <td><span class="dot" style="background:{r.color || '#888'}"></span>{r.display_name}</td>
          <td class="num">{fmtTime(r.total_time_ms)}</td>
          <td class="num">{r.rank === 1 ? "—" : "+" + ((r.total_time_ms - leaderMs) / 1000).toFixed(3)}</td>
          <td class="num">{pctOffWr(r.total_time_ms, wrMs)}</td>
        </tr>
      {/each}
    </tbody>
  </table>

  <!-- Lap splits -->
  {#if splits && splits.laps > 0}
    <h2>Lap splits</h2>
    <table class="splits">
      <thead><tr><th>Player</th>{#each Array(splits.laps) as _, i}<th>Lap {i + 1}</th>{/each}<th>Theoretical</th></tr></thead>
      <tbody>
        {#each splits.perPlayer as p (p.player_id)}
          <tr>
            <td><span class="dot" style="background:{p.color || '#888'}"></span>{p.display_name}</td>
            {#each p.best as b}<td class="num">{b == null ? "—" : fmtTime(b)}</td>{/each}
            <td class="num strong">{p.theoretical == null ? "—" : fmtTime(p.theoretical)}</td>
          </tr>
        {/each}
        <tr class="ideal">
          <td>Field ideal</td>
          {#each splits.fieldIdeal as b}<td class="num">{b == null ? "—" : fmtTime(b)}</td>{/each}
          <td class="num strong">{splits.fieldIdealTotal == null ? "—" : fmtTime(splits.fieldIdealTotal)}</td>
        </tr>
      </tbody>
    </table>
  {/if}

  <!-- History -->
  <div class="hist">
    <div class="col">
      <h2>Record progression</h2>
      {#each s.history.recordProgression as e}<div class="hrow"><span>{date(e.t)}</span><span>{e.player}</span><span class="num">{fmtTime(e.ms)}</span></div>{/each}
    </div>
    <div class="col">
      <h2>#1 reigns</h2>
      {#each s.history.reigns as r}<div class="hrow"><span>{r.player}</span><span>{date(r.from)} → {r.to == null ? "now" : date(r.to)}</span><span>{days(r.ms)}</span></div>{/each}
    </div>
    <div class="col">
      <h2>World record history</h2>
      {#each s.history.wrHistory as w}<div class="hrow"><span>{date(w.t)}</span><span>{w.holder_name ?? "—"}</span><span class="num">{fmtTime(w.record_ms)}</span>{#if w.video_url}<a href={w.video_url} target="_blank" rel="noopener">↗</a>{/if}</div>{/each}
    </div>
  </div>
</section>
{/if}

<style>
  .wrap { padding: 16px; color: #e8eaed; max-width: 960px; }
  .err { color: #f77; }
  .head h1 { margin: 0 0 6px; }
  .wr { display: flex; align-items: center; gap: 8px; color: #9aa3ad; font-variant-numeric: tabular-nums; flex-wrap: wrap; }
  .wr .lbl { font-size: 10px; letter-spacing: .08em; color: #5f656e; }
  .wr .tm { color: #e8eaed; } .wr .chip { height: 20px; width: auto; } .wr .lo { font-size: 12px; }
  .wr .vid { color: #5f9bd6; text-decoration: none; }
  .fireline { margin: 12px 0; padding: 8px 10px; background: #17140e; border: 1px solid #3a2f18; border-radius: 6px; }
  table { border-collapse: collapse; width: 100%; margin: 8px 0 20px; font-variant-numeric: tabular-nums; }
  th, td { text-align: left; padding: 4px 10px; border-bottom: 1px solid #1c1f24; font-size: 13px; }
  th { color: #5f656e; font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: .06em; }
  .num { text-align: right; font-family: ui-monospace, Menlo, monospace; }
  .strong { color: #fff; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 7px; vertical-align: middle; }
  .ideal td { color: #cdd3da; border-top: 1px solid #2c313a; }
  .hist { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 18px; }
  .hist h2 { font-size: 13px; color: #cdd3da; }
  .hrow { display: flex; gap: 10px; justify-content: space-between; padding: 3px 0; border-bottom: 1px solid #1c1f24; font-size: 12.5px; }
</style>
```

- [ ] **Step 2: Verify in a real browser**

Run: `npm --prefix web run dev` (+ Pi dev server), open `http://127.0.0.1:1430/tracks/rainbow_road`.
Expected: track name + WR (time, holder, chips hidden if assets absent, video link), an on-fire target line, the full leaderboard (rank/player/time/gap/Δ WR), a lap-splits table with a Theoretical column and a Field-ideal row, and three history columns. A bad slug shows "No such track."

- [ ] **Step 3: Commit**

```bash
git add web/src/CourseProfile.svelte
git commit -m "feat(web): track hub page (WR+chips, on-fire target, leaderboard, splits, history)"
```

---

## Self-Review

**Spec coverage** (checked against `2026-07-04-tracks-page-design.md`):
- Routing `/tracks` + `/tracks/:slug`, nav, view key `courses` → Task 4 + Task 7. ✅
- Index = leaderboard-card wall (CoursePopup inline) → Task 7. ✅
- Overall-time pinned card (non-linking) → Task 6 (math) + Task 7 (render). ✅
- Track search → Task 7 (`filtered`). ✅
- Header + WR + chips + video → Task 8. ✅
- Full leaderboard → Task 8. ✅
- On-fire target → Task 5 + Task 8. ✅
- Lap splits (best per lap, theoretical, field ideal) → Task 1 + Task 6 + Task 8. ✅
- History (record progression, reigns, WR history) → Task 2 + Task 8. ✅
- Public `GET /v1/courses/:slug` + gating (single-segment regex) → Task 3. ✅
- Deferred to Plan 2: the track map + model/trails/heatmap endpoints (multi-segment regex extension noted in Task 3's `COURSE_SUMMARY` comment). ✅

**Placeholder scan:** none — every step has real code + exact commands.

**Type consistency:** `courseSummary(db, seasonId, cc, slug)` order matches the route call in Task 3. `splits` shape (`{laps, perPlayer:[{player_id, display_name, color, best}], fieldIdeal}`) is produced identically in Task 1 and consumed in Task 6/8. `fireTargetMs({leaderMs, wr})` return `{ms, reason}` matches Task 8 usage. `overallBoard(boards)` input `[{standings:[{player,ms}]}]` matches Task 7's `boards`. `CoursePopup` props `view`/`figUrl` match `courseData.js buildCourseView` output + `freshGifUrl`.

## Notes for Plan 2 (Track map — next cycle)

Adds: public `GET /v1/courses/:slug/model|trails|heatmap` (extend the `COURSE_SUMMARY` regex in `app.ts` to `/^\/v1\/courses\/[^/]+(\/(model|trails|heatmap))?$/` and the CORS `app.use` to `/v1/courses/*`), a `CourseMap.svelte` reusing `src/lib/overlay.js` with the four modes (outline / PB lines / replay / heatmap), and heatmap rasterization reusing `pi/src/progress/lapGraphCV`. Written after this plan executes so the endpoints exist to draw from.

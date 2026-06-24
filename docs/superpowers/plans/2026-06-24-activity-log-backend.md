# Activity Log — Backend (pi) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the server-side half of the `/live` activity log: a persisted `activity_events` stream computed from runs/WRs/screen-intervals, served as history (`GET /v1/activity`) and live (`/v1/activity` WS).

**Architecture:** Events are computed once at ingestion and stored in a new `activity_events` table (the history never changes, so reads never recompute). A run finishing triggers a **cascade** — PB → RANK ladder → TURF transitions — built by pure functions over the before/after course leaderboards. A new WR triggers a TURF recompute. Screen intervals emit per-screen "off-track" rows. A one-time backfill replays history. Delivery is **additive** — a separate `ActivityHub` + `/v1/activity` endpoints, leaving the bot's `/v1/events` untouched.

**Tech Stack:** TypeScript, `node:sqlite` `DatabaseSync` (synchronous), Hono, vitest.

> **Decision (deviation from spec C.3):** the spec proposed a standing per-course leaderboard cache. This plan instead computes the cascade from before/after `courseLeaderboard()` snapshots taken in the run handler (the query is a single indexed read over a handful of rows per course, and the cascade only runs per *finished* run). A standing cache adds a sync-with-DB bug surface across every write path for negligible gain at this scale — YAGNI. Revisit only if profiling shows it matters.

> **Scope:** This is the backend only. The `web/` `ActivityLog.svelte` + store, and the `PlayerCard` activity-line change with its presence fields (`session_attempts`, `screen_since_ms`), are **Plan 2**.

## Global Constraints

- DB is **synchronous** `node:sqlite` `DatabaseSync`; all queries are `db.prepare(...).get/all/run()` or `db.exec(...)`. No async DB.
- Schema lives in repo-root **`server/schema.sql`**; new tables are added there as `CREATE TABLE IF NOT EXISTS` and applied at boot by `applySchema()` (`pi/src/db/connect.ts:16`). Column-only changes use a try/catch `ALTER TABLE` block in `applySchema()`.
- Tests: vitest, files `pi/src/**/*.test.ts`, run with **`npm --prefix pi test`**. DB tests use `openDb(':memory:')` + `applySchema(db)` then seed with `db.exec(...)`.
- Fire model constants are **`E0 = 0.2`, `K = 4`** — copied verbatim from `web/src/lib/fireModel.js`; must stay identical.
- `activity_events` ordering key is **`id` (autoincrement = insertion order = chronological)**; feeds read newest-first `ORDER BY id DESC`. Cascade rows are inserted oldest-first so the burst reads correctly when reversed.
- Delivery is **additive**: do not modify the `ServerEvent` union or `/v1/events`. Activity uses its own `ActivityHub` + `/v1/activity`.

## File structure

| File | Responsibility |
|---|---|
| `server/schema.sql` (modify) | add `activity_events` table |
| `pi/src/activity/types.ts` (create) | `ActivityType`, `ActivityInput`, `ActivityEvent` |
| `pi/src/db/activity.ts` (create) | persist (`insertActivityEvents`) + read/resolve (`recentActivity`, `resolveActivity`) |
| `pi/src/activity/hub.ts` (create) | `ActivityHub` pub/sub (mirrors `EventHub`) |
| `pi/src/turf/fireModel.ts` (create) | port of `isOnFire` / `fireBarPct` |
| `pi/src/turf/transitions.ts` (create) | pure `turfTransitions(before, after)` → claim/fire/waver |
| `pi/src/turf/rank.ts` (create) | pure `rankGains(before, after, moverId)` |
| `pi/src/activity/cascade.ts` (create) | pure `buildRunCascade(args)` → `ActivityInput[]` |
| `pi/src/activity/publish.ts` (create) | `commitActivity(db, hub, inputs)` — insert + resolve + publish |
| `pi/src/api/runs.ts` (modify) | call the cascade at the existing publish hook |
| `pi/src/api/activity.ts` (create) | `GET /v1/activity` + `/v1/activity` WS |
| `pi/src/api/app.ts` (modify) | mount activity routes + WS; add to `PUBLIC_READS`/`OPEN` |
| `pi/src/wr/reconcile.ts` (modify) | WR → TURF recompute at the WR-change point |
| `pi/src/activity/screens.ts` (create) | screen→label map + `screenActivityInputs()` |
| `pi/src/stats/screen.ts` (modify) | emit off-track events for inserted intervals |
| `pi/src/activity/grind.ts` (create) | per-player grind tracker → closed attempts segments |
| `pi/src/activity/backfill.ts` (create) | one-time history replay |

---

## PHASE 1 — Core log (PB → RANK → TURF, history + live)

### Task 1: `activity_events` table

**Files:**
- Modify: `server/schema.sql` (append a table)
- Test: `pi/src/db/activity.test.ts`

**Interfaces:**
- Produces: an `activity_events` table with columns `id, ts, type, season_id, player_id, course_id, cc, payload, created_at`.

- [ ] **Step 1: Write the failing test**

```typescript
// pi/src/db/activity.test.ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';

describe('activity_events schema', () => {
  it('exists with the expected columns after applySchema', () => {
    const db = openDb(':memory:');
    applySchema(db);
    const cols = (db.prepare("PRAGMA table_info(activity_events)").all() as { name: string }[]).map(c => c.name);
    expect(cols).toEqual(expect.arrayContaining(['id', 'ts', 'type', 'season_id', 'player_id', 'course_id', 'cc', 'payload']));
  });
});
```

- [ ] **Step 2: Run it, expect FAIL** — `npm --prefix pi test -- activity` → fails (`no such table: activity_events`).

- [ ] **Step 3: Add the table** to `server/schema.sql` (append near the other `CREATE TABLE IF NOT EXISTS` blocks, e.g. after `screen_intervals`):

```sql
CREATE TABLE IF NOT EXISTS activity_events (
    id         INTEGER PRIMARY KEY,
    ts         INTEGER NOT NULL,
    type       TEXT NOT NULL,
    season_id  INTEGER NOT NULL REFERENCES seasons(id),
    player_id  INTEGER REFERENCES players(id),
    course_id  INTEGER REFERENCES courses(id),
    cc         INTEGER,
    payload    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_activity_season_id ON activity_events(season_id, id DESC);
```

- [ ] **Step 4: Run it, expect PASS.**

- [ ] **Step 5: Commit** — `git add server/schema.sql pi/src/db/activity.test.ts && git commit -m "feat(pi): activity_events table"`

---

### Task 2: Activity types + persist/read layer

**Files:**
- Create: `pi/src/activity/types.ts`, `pi/src/db/activity.ts`
- Test: `pi/src/db/activity.test.ts` (extend)

**Interfaces:**
- Produces:
  - `ActivityType = 'pb'|'rank'|'turf_claim'|'turf_fire'|'turf_waver'|'wr'|'attempts'|'screen'`
  - `ActivityInput { ts:number; type:ActivityType; season_id:number; player_id:number|null; course_id:number|null; cc:number|null; payload:Record<string,unknown> }`
  - `ActivityEvent { id:number; ts:number; type:ActivityType; course:{slug:string;name:string}|null; player:{id:number;name:string;color:string|null}|null; payload:Record<string,unknown> }`
  - `insertActivityEvents(db, inputs:ActivityInput[]): number[]`
  - `recentActivity(db, opts:{ seasonId:number; before?:number; limit?:number }): ActivityEvent[]`
  - `resolveActivity(db, row): ActivityEvent` (resolves `player_id`, `course_id`, and any `payload.rival_id` to names/colours)

- [ ] **Step 1: Write the failing test** (append to `activity.test.ts`):

```typescript
import { insertActivityEvents, recentActivity } from './activity';

function seed() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Gub','#38bdf8'),(2,'Paul','#a78bfa')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'crown_city','Crown City')");
  return db;
}

describe('activity persist/read', () => {
  it('inserts then reads newest-first, resolving names/colours + rival', () => {
    const db = seed();
    insertActivityEvents(db, [
      { ts: 1000, type: 'pb', season_id: 1, player_id: 1, course_id: 1, cc: 150, payload: { time_ms: 107980, time_str: '1:47.980', delta_ms: -430 } },
      { ts: 1000, type: 'turf_claim', season_id: 1, player_id: 1, course_id: 1, cc: 150, payload: { rival_id: 2 } },
    ]);
    const out = recentActivity(db, { seasonId: 1, limit: 10 });
    expect(out.map(e => e.type)).toEqual(['turf_claim', 'pb']); // newest (highest id) first
    expect(out[0].player).toEqual({ id: 1, name: 'Gub', color: '#38bdf8' });
    expect(out[0].course).toEqual({ slug: 'crown_city', name: 'Crown City' });
    expect((out[0].payload as any).rival).toEqual({ id: 2, name: 'Paul', color: '#a78bfa' });
    expect(out[1].payload).toMatchObject({ time_str: '1:47.980', delta_ms: -430 });
  });

  it('paginates with `before`', () => {
    const db = seed();
    const ids = insertActivityEvents(db, [
      { ts: 1, type: 'pb', season_id: 1, player_id: 1, course_id: 1, cc: 150, payload: {} },
      { ts: 2, type: 'pb', season_id: 1, player_id: 1, course_id: 1, cc: 150, payload: {} },
    ]);
    const page = recentActivity(db, { seasonId: 1, before: ids[1], limit: 10 });
    expect(page.map(e => e.id)).toEqual([ids[0]]);
  });
});
```

- [ ] **Step 2: Run, expect FAIL** (module not found).

- [ ] **Step 3: Create `pi/src/activity/types.ts`:**

```typescript
export type ActivityType =
  | 'pb' | 'rank' | 'turf_claim' | 'turf_fire' | 'turf_waver' | 'wr' | 'attempts' | 'screen';

export interface ActivityInput {
  ts: number;
  type: ActivityType;
  season_id: number;
  player_id: number | null;
  course_id: number | null;
  cc: number | null;
  payload: Record<string, unknown>;
}

export interface ActivityEvent {
  id: number;
  ts: number;
  type: ActivityType;
  course: { slug: string; name: string } | null;
  player: { id: number; name: string; color: string | null } | null;
  payload: Record<string, unknown>;
}
```

- [ ] **Step 4: Create `pi/src/db/activity.ts`:**

```typescript
import type { DatabaseSync } from 'node:sqlite';
import type { ActivityInput, ActivityEvent } from '../activity/types';

export function insertActivityEvents(db: DatabaseSync, inputs: ActivityInput[]): number[] {
  const stmt = db.prepare(
    `INSERT INTO activity_events(ts, type, season_id, player_id, course_id, cc, payload)
     VALUES (?,?,?,?,?,?,?)`);
  const ids: number[] = [];
  for (const e of inputs)
    ids.push(Number(stmt.run(e.ts, e.type, e.season_id, e.player_id, e.course_id, e.cc, JSON.stringify(e.payload)).lastInsertRowid));
  return ids;
}

type Row = { id: number; ts: number; type: string; player_id: number | null;
  course_id: number | null; payload: string };

function player(db: DatabaseSync, id: number | null) {
  if (id == null) return null;
  const p = db.prepare('SELECT id, display_name, color FROM players WHERE id=?').get(id) as
    { id: number; display_name: string; color: string | null } | undefined;
  return p ? { id: p.id, name: p.display_name, color: p.color } : null;
}

function course(db: DatabaseSync, id: number | null) {
  if (id == null) return null;
  const c = db.prepare('SELECT slug, display_name FROM courses WHERE id=?').get(id) as
    { slug: string; display_name: string } | undefined;
  return c ? { slug: c.slug, name: c.display_name } : null;
}

export function resolveActivity(db: DatabaseSync, row: Row): ActivityEvent {
  const payload = JSON.parse(row.payload) as Record<string, unknown>;
  if (typeof payload.rival_id === 'number') payload.rival = player(db, payload.rival_id as number);
  return {
    id: row.id, ts: row.ts, type: row.type as ActivityEvent['type'],
    player: player(db, row.player_id), course: course(db, row.course_id), payload,
  };
}

export function recentActivity(
  db: DatabaseSync, opts: { seasonId: number; before?: number; limit?: number }): ActivityEvent[] {
  const limit = Math.min(opts.limit ?? 100, 500);
  const rows = (opts.before
    ? db.prepare('SELECT * FROM activity_events WHERE season_id=? AND id<? ORDER BY id DESC LIMIT ?')
        .all(opts.seasonId, opts.before, limit)
    : db.prepare('SELECT * FROM activity_events WHERE season_id=? ORDER BY id DESC LIMIT ?')
        .all(opts.seasonId, limit)) as Row[];
  return rows.map(r => resolveActivity(db, r));
}
```

- [ ] **Step 5: Run, expect PASS. Commit** — `git add pi/src/activity/types.ts pi/src/db/activity.ts pi/src/db/activity.test.ts && git commit -m "feat(pi): activity persist + resolve layer"`

---

### Task 3: Port the fire model to `pi/`

**Files:**
- Create: `pi/src/turf/fireModel.ts`, `pi/src/turf/fireModel.test.ts`

**Interfaces:**
- Produces: `E0`, `K`, `fireBarPct(offPct:number):number`, `isOnFire(t1:number|null, t2:number|null, wr:number|null):boolean`.

- [ ] **Step 1: Write the failing test** (mirror `web/src/lib/fireModel` behaviour):

```typescript
// pi/src/turf/fireModel.test.ts
import { describe, it, expect } from 'vitest';
import { isOnFire, fireBarPct, E0, K } from './fireModel';

describe('fireModel', () => {
  it('keeps the web constants', () => { expect(E0).toBe(0.2); expect(K).toBe(4); });
  it('bar rises exponentially with off-WR %', () => {
    expect(fireBarPct(0)).toBeCloseTo(0.2, 6);
    expect(fireBarPct(4)).toBeCloseTo(0.2 * Math.E, 6);
  });
  it('on fire when the lead over #2 clears the bar', () => {
    // wr=100000; leader=100100 (0.1% off), #2=100400 -> lead 0.3% >= bar ~0.2005%
    expect(isOnFire(100100, 100400, 100000)).toBe(true);
  });
  it('not on fire without wr / #2 / when #2 faster', () => {
    expect(isOnFire(100100, 100400, null)).toBe(false);
    expect(isOnFire(100100, null, 100000)).toBe(false);
    expect(isOnFire(100100, 100050, 100000)).toBe(false);
  });
});
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Create `pi/src/turf/fireModel.ts`** (verbatim logic from `web/src/lib/fireModel.js`):

```typescript
export const E0 = 0.2;
export const K = 4;

export function fireBarPct(offPct: number): number {
  return E0 * Math.exp(offPct / K);
}

export function isOnFire(t1: number | null, t2: number | null, wr: number | null): boolean {
  if (!wr || t1 == null || t2 == null || t2 < t1) return false;
  const leadPct = ((t2 - t1) / wr) * 100;
  const offPct = ((t1 - wr) / wr) * 100;
  return leadPct >= fireBarPct(offPct);
}
```

- [ ] **Step 4: Run, expect PASS. Commit** — `git commit -am "feat(pi): port fireModel (isOnFire)"`

---

### Task 4: Turf transitions (pure)

**Files:**
- Create: `pi/src/turf/transitions.ts`, `pi/src/turf/transitions.test.ts`

**Interfaces:**
- Consumes: `LeaderRow` from `pi/src/db/reads.ts` (`{ player_id; display_name; total_time_ms; total_time_str; rank }`), `isOnFire`.
- Produces: `TurfTransition = {kind:'claim';leaderId;rivalId} | {kind:'fire';leaderId} | {kind:'waver';leaderId}` and `turfTransitions(before:{board:LeaderRow[];wr:number|null}, after:{board:LeaderRow[];wr:number|null}): TurfTransition[]` — claim (if #1 changed) first, then a single fire/waver for the after-leader.

- [ ] **Step 1: Write the failing test:**

```typescript
// pi/src/turf/transitions.test.ts
import { describe, it, expect } from 'vitest';
import { turfTransitions } from './transitions';
import type { LeaderRow } from '../db/reads';

const row = (id: number, ms: number): LeaderRow =>
  ({ player_id: id, display_name: `P${id}`, total_time_ms: ms, total_time_str: null, rank: 0 });

describe('turfTransitions', () => {
  it('emits a claim when #1 changes', () => {
    const before = { board: [row(2, 100200), row(1, 100400)], wr: 100000 };
    const after = { board: [row(1, 100100), row(2, 100200)], wr: 100000 };
    const t = turfTransitions(before, after);
    expect(t).toContainEqual({ kind: 'claim', leaderId: 1, rivalId: 2 });
  });
  it('emits fire when the new leader is on fire (claim then fire order)', () => {
    const before = { board: [row(2, 100500), row(1, 100600)], wr: 100000 };
    const after = { board: [row(1, 100100), row(2, 100500)], wr: 100000 }; // big lead, near WR
    const t = turfTransitions(before, after);
    expect(t[0]).toEqual({ kind: 'claim', leaderId: 1, rivalId: 2 });
    expect(t[1]).toEqual({ kind: 'fire', leaderId: 1 });
  });
  it('emits waver when the same leader loses fire (e.g. WR raised)', () => {
    const board = [row(1, 100100), row(2, 100400)];
    const before = { board, wr: 100050 }; // lead ~0.30% vs bar ~0.20% -> on fire
    const after = { board, wr: 98000 };    // faster WR -> bar ~0.34% > lead -> snuffed
    expect(turfTransitions(before, after)).toEqual([{ kind: 'waver', leaderId: 1 }]);
  });
  it('no transition when nothing changes', () => {
    const board = [row(1, 100100), row(2, 100400)];
    expect(turfTransitions({ board, wr: 100000 }, { board, wr: 100000 })).toEqual([]);
  });
});
```

(These ms are chosen so `isOnFire` flips exactly as asserted with `E0=0.2, K=4`.)

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Create `pi/src/turf/transitions.ts`:**

```typescript
import type { LeaderRow } from '../db/reads';
import { isOnFire } from './fireModel';

export type TurfTransition =
  | { kind: 'claim'; leaderId: number; rivalId: number }
  | { kind: 'fire'; leaderId: number }
  | { kind: 'waver'; leaderId: number };

interface Standing { board: LeaderRow[]; wr: number | null }

export function turfTransitions(before: Standing, after: Standing): TurfTransition[] {
  const out: TurfTransition[] = [];
  const a0 = after.board[0];
  if (!a0) return out;
  const b0 = before.board[0] ?? null;
  const claimed = !!b0 && b0.player_id !== a0.player_id;
  if (claimed) out.push({ kind: 'claim', leaderId: a0.player_id, rivalId: b0!.player_id });

  const fireAfter = isOnFire(a0.total_time_ms, after.board[1]?.total_time_ms ?? null, after.wr);
  const fireBefore = b0 ? isOnFire(b0.total_time_ms, before.board[1]?.total_time_ms ?? null, before.wr) : false;

  if (fireAfter && (claimed || !fireBefore)) out.push({ kind: 'fire', leaderId: a0.player_id });
  else if (!fireAfter && !claimed && fireBefore) out.push({ kind: 'waver', leaderId: a0.player_id });
  return out;
}
```

- [ ] **Step 4: Run, expect PASS. Commit** — `git commit -am "feat(pi): turf transition detector"`

---

### Task 5: Rank gains (pure)

**Files:**
- Create: `pi/src/turf/rank.ts`, `pi/src/turf/rank.test.ts`

**Interfaces:**
- Consumes: `LeaderRow`.
- Produces: `RankGain { place:number; rivalId:number; rivalName:string; rivalTimeMs:number }` and `rankGains(before:LeaderRow[], after:LeaderRow[], moverId:number): RankGain[]` — one per place gained, ordered **first-gained first** (highest place number down to the new place); the rival at each step is who held that place in `before`.

- [ ] **Step 1: Write the failing test:**

```typescript
// pi/src/turf/rank.test.ts
import { describe, it, expect } from 'vitest';
import { rankGains } from './rank';
import type { LeaderRow } from '../db/reads';

const row = (id: number, rank: number): LeaderRow =>
  ({ player_id: id, display_name: `P${id}`, total_time_ms: 100000 + rank, total_time_str: null, rank });

describe('rankGains', () => {
  it('lists each place gained, first-gained first, rival = prior holder', () => {
    // before: 1st P9, 2nd P2(Aliias), 3rd P3(Luke), 4th P4(Alex), 5th P5(mover)
    const before = [row(9, 1), row(2, 2), row(3, 3), row(4, 4), row(5, 5)];
    // after: mover P5 -> 2nd; others shift down
    const after = [row(9, 1), row(5, 2), row(2, 3), row(3, 4), row(4, 5)];
    const g = rankGains(before, after, 5);
    expect(g.map(x => [x.place, x.rivalId])).toEqual([[4, 4], [3, 3], [2, 2]]);
  });
  it('returns [] when the mover did not climb', () => {
    const before = [row(1, 1), row(2, 2)];
    expect(rankGains(before, before, 2)).toEqual([]);
  });
});
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Create `pi/src/turf/rank.ts`:**

```typescript
import type { LeaderRow } from '../db/reads';

export interface RankGain { place: number; rivalId: number; rivalName: string; rivalTimeMs: number }

export function rankGains(before: LeaderRow[], after: LeaderRow[], moverId: number): RankGain[] {
  const oldPlace = before.find(r => r.player_id === moverId)?.rank ?? before.length + 1;
  const newPlace = after.find(r => r.player_id === moverId)?.rank;
  if (newPlace == null || newPlace >= oldPlace) return [];
  const gains: RankGain[] = [];
  for (let place = oldPlace - 1; place >= newPlace; place--) {
    const rival = before[place - 1]; // 0-indexed: who held `place` before
    if (rival) gains.push({ place, rivalId: rival.player_id, rivalName: rival.display_name, rivalTimeMs: rival.total_time_ms });
  }
  return gains;
}
```

- [ ] **Step 4: Run, expect PASS. Commit** — `git commit -am "feat(pi): rank-gain ladder"`

---

### Task 6: The run cascade (pure)

**Files:**
- Create: `pi/src/activity/cascade.ts`, `pi/src/activity/cascade.test.ts`

**Interfaces:**
- Consumes: `LeaderRow`, `rankGains`, `turfTransitions`, `ActivityInput`.
- Produces: `RunCascadeArgs` and `buildRunCascade(a:RunCascadeArgs): ActivityInput[]`, inserted **oldest-first**: optional `attempts` → `pb` → rank ladder (`rank`) → turf (`turf_claim`/`turf_fire`/`turf_waver`). Reversed at read time this yields turf→rank→pb→attempts top-to-bottom.

- [ ] **Step 1: Write the failing test:**

```typescript
// pi/src/activity/cascade.test.ts
import { describe, it, expect } from 'vitest';
import { buildRunCascade } from './cascade';
import type { LeaderRow } from '../db/reads';

const row = (id: number, ms: number, rank: number): LeaderRow =>
  ({ player_id: id, display_name: `P${id}`, total_time_ms: ms, total_time_str: null, rank });

describe('buildRunCascade', () => {
  it('PB takes #1: order is attempts, pb, rank(1st), turf_claim', () => {
    const before = [row(2, 108221, 1), row(1, 108600, 2)]; // P2 leads, mover P1 2nd
    const after = [row(1, 107980, 1), row(2, 108221, 2)];  // mover P1 -> 1st
    const out = buildRunCascade({
      ts: 1000, seasonId: 1, cc: 150, courseId: 1, moverId: 1, moverName: 'P1',
      before, after, beforeWr: null, afterWr: null, prevPbMs: 108410,
      attempts: { count: 12, durationMs: 240000 },
    });
    expect(out.map(e => e.type)).toEqual(['attempts', 'pb', 'rank', 'turf_claim']);
    expect(out[1].payload).toMatchObject({ time_ms: 107980, delta_ms: 107980 - 108410 });
    expect(out[2].payload).toMatchObject({ place: 1, rival_id: 2, gap_ms: 108221 - 107980 });
    expect(out[3]).toMatchObject({ type: 'turf_claim', player_id: 1, payload: { rival_id: 2 } });
  });

  it('PB that only climbs mid-board: pb + rank rows, no turf', () => {
    const before = [row(9, 100000, 1), row(2, 100500, 2), row(1, 100900, 3)];
    const after = [row(9, 100000, 1), row(1, 100400, 2), row(2, 100500, 3)];
    const out = buildRunCascade({
      ts: 1, seasonId: 1, cc: 150, courseId: 1, moverId: 1, moverName: 'P1',
      before, after, beforeWr: null, afterWr: null, prevPbMs: 100900, attempts: null,
    });
    expect(out.map(e => e.type)).toEqual(['pb', 'rank']);
    expect(out[1].payload).toMatchObject({ place: 2, rival_id: 2 });
  });
});
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Create `pi/src/activity/cascade.ts`:**

```typescript
import type { LeaderRow } from '../db/reads';
import { rankGains } from '../turf/rank';
import { turfTransitions } from '../turf/transitions';
import type { ActivityInput } from './types';

export interface RunCascadeArgs {
  ts: number; seasonId: number; cc: number; courseId: number;
  moverId: number; moverName: string;
  before: LeaderRow[]; after: LeaderRow[];
  beforeWr: number | null; afterWr: number | null;
  prevPbMs: number | null;
  attempts: { count: number; durationMs: number } | null;
}

export function buildRunCascade(a: RunCascadeArgs): ActivityInput[] {
  const out: ActivityInput[] = [];
  const mine = a.after.find(r => r.player_id === a.moverId);
  if (!mine) return out;
  const base = { ts: a.ts, season_id: a.seasonId, cc: a.cc, course_id: a.courseId };

  if (a.attempts && a.attempts.count > 0)
    out.push({ ...base, type: 'attempts', player_id: a.moverId,
      payload: { count: a.attempts.count, duration_ms: a.attempts.durationMs } });

  out.push({ ...base, type: 'pb', player_id: a.moverId,
    payload: { time_ms: mine.total_time_ms, time_str: mine.total_time_str,
               delta_ms: a.prevPbMs != null ? mine.total_time_ms - a.prevPbMs : null } });

  for (const g of rankGains(a.before, a.after, a.moverId))
    out.push({ ...base, type: 'rank', player_id: a.moverId,
      payload: { place: g.place, rival_id: g.rivalId, rival_name: g.rivalName,
                 rival_time_ms: g.rivalTimeMs, gap_ms: g.rivalTimeMs - mine.total_time_ms } });

  for (const t of turfTransitions({ board: a.before, wr: a.beforeWr }, { board: a.after, wr: a.afterWr })) {
    if (t.kind === 'claim') out.push({ ...base, type: 'turf_claim', player_id: t.leaderId, payload: { rival_id: t.rivalId } });
    else if (t.kind === 'fire') out.push({ ...base, type: 'turf_fire', player_id: t.leaderId, payload: {} });
    else out.push({ ...base, type: 'turf_waver', player_id: t.leaderId, payload: {} });
  }
  return out;
}
```

- [ ] **Step 4: Run, expect PASS. Commit** — `git commit -am "feat(pi): run cascade builder"`

---

### Task 7: ActivityHub + commit helper, wired into `POST /v1/runs`

**Files:**
- Create: `pi/src/activity/hub.ts`, `pi/src/activity/publish.ts`
- Modify: `pi/src/api/runs.ts` (signature + the publish hook ~line 88), `pi/src/api/app.ts` (construct + pass the hub)
- Test: `pi/src/api/runs-activity.test.ts`

**Interfaces:**
- Produces: `ActivityHub` (`subscribe(sink:(e:ActivityEvent)=>void):()=>void`, `publish(e)`, `size`); `commitActivity(db, hub:ActivityHub, inputs:ActivityInput[]): ActivityEvent[]` (insert → resolve → publish in id order, returns the resolved events).
- Consumes: `insertActivityEvents`, `resolveActivity`, `buildRunCascade`, `courseLeaderboard`, `currentWr`.

- [ ] **Step 1: Write the failing test** (mirror `runs.test.ts` `ctx()` but capture activity):

```typescript
// pi/src/api/runs-activity.test.ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { mintToken } from '../db/players';
import { EventHub } from './events';
import { ActivityHub } from '../activity/hub';
import { createApp } from './app';
import type { ActivityEvent } from '../activity/types';

function ctx() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Gub','#38bdf8'),(2,'Paul','#a78bfa')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'crown_city','Crown City')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
  const hub = new EventHub(); const act = new ActivityHub();
  const seen: ActivityEvent[] = []; act.subscribe(e => seen.push(e));
  return { db, app: createApp(db, hub, undefined, { activity: act }), act, seen,
           gub: mintToken(db, 'Gub'), paul: mintToken(db, 'Paul') };
}
const post = (app: any, token: string, body: unknown) => app.request('/v1/runs', {
  method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
  body: JSON.stringify(body) });

describe('run -> activity cascade', () => {
  it('a PB that takes #1 writes pb + rank + turf_claim, newest-first', async () => {
    const { app, db, seen, gub, paul } = ctx();
    await post(app, paul, { attempt_id: 'p1', course: 'Crown City', status: 'finished', total_time: '1:48.221' });
    seen.length = 0;
    await post(app, gub, { attempt_id: 'g1', course: 'Crown City', status: 'finished', total_time: '1:47.980' });
    expect(seen.map(e => e.type)).toEqual(['turf_claim', 'rank', 'pb']);
    expect(seen[0].player!.name).toBe('Gub');
    expect((seen[0].payload as any).rival.name).toBe('Paul');
    expect((seen[1].payload as any).place).toBe(1);
    expect((db.prepare('SELECT COUNT(*) c FROM activity_events').get() as any).c).toBe(3);
  });
});
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Create `pi/src/activity/hub.ts`:**

```typescript
import type { ActivityEvent } from './types';
type Sink = (e: ActivityEvent) => void;
export class ActivityHub {
  private sinks = new Set<Sink>();
  subscribe(sink: Sink): () => void { this.sinks.add(sink); return () => this.sinks.delete(sink); }
  publish(e: ActivityEvent): void { for (const s of [...this.sinks]) { try { s(e); } catch {} } }
  get size(): number { return this.sinks.size; }
}
```

- [ ] **Step 4: Create `pi/src/activity/publish.ts`:**

```typescript
import type { DatabaseSync } from 'node:sqlite';
import type { ActivityInput, ActivityEvent } from './types';
import { insertActivityEvents, resolveActivity } from '../db/activity';
import type { ActivityHub } from './hub';

export function commitActivity(db: DatabaseSync, hub: ActivityHub, inputs: ActivityInput[]): ActivityEvent[] {
  if (!inputs.length) return [];
  const ids = insertActivityEvents(db, inputs);
  const events = ids.map(id => resolveActivity(db, db.prepare('SELECT * FROM activity_events WHERE id=?').get(id) as any));
  for (const e of events) hub.publish(e); // ascending id; client prepends so newest ends on top
  return events;
}
```

- [ ] **Step 5: Thread the `ActivityHub` through `createApp` and `runsRoutes`.** In `pi/src/api/app.ts`, extend the `opts` param of `createApp` with `activity?: ActivityHub`, default-construct one if absent, and pass it to `runsRoutes`:

```typescript
// app.ts — imports
import { ActivityHub } from '../activity/hub';
// inside createApp(db, hub, invalidateModel?, opts?) — opts now: { latest?: LatestFn; activity?: ActivityHub }
const activity = opts?.activity ?? new ActivityHub();
// change: app.route('/', runsRoutes(db, hub, invalidateModel));
app.route('/', runsRoutes(db, hub, activity, invalidateModel));
```

- [ ] **Step 6: Add the cascade at the publish hook in `pi/src/api/runs.ts`.** Change the `runsRoutes` signature to accept `activity: ActivityHub`, capture the **full** before-board, and after computing `result`/`lb`/`wr`, build + commit the cascade:

```typescript
// runs.ts — new import
import { buildRunCascade } from '../activity/cascade';
import { commitActivity } from '../activity/publish';
import type { ActivityHub } from '../activity/hub';
// (use Date.now() directly in the handler — it's runtime code; only workflow scripts forbid it)

// signature: export function runsRoutes(db, hub: EventHub, activity: ActivityHub, invalidateModel?)
// near the existing prevLeader capture, also capture the full board:
const beforeBoard = courseLeaderboard(db, seasonId, courseId, cc);
// ...after recomputeIsPb/recomputeWasPb and `const lb = courseLeaderboard(...)`:
if (isPb) {
  const wrMs = wr ? wr.record_ms : null;
  const inputs = buildRunCascade({
    ts: Date.now(), seasonId, cc, courseId, moverId: playerId, moverName: playerName,
    before: beforeBoard, after: lb, beforeWr: wrMs, afterWr: wrMs,
    prevPbMs: prevMineMs, attempts: null, // attempts wired in Task 11
  });
  commitActivity(db, activity, inputs);
}
// leave the existing hub.publish(...) lines for the bot untouched (additive).
```

(`prevMineMs` and `lb` already exist in the handler; `beforeBoard` is the new capture. `Date.now()` is fine at runtime — only workflow scripts forbid it.)

- [ ] **Step 7: Run the new test + the existing `runs.test.ts`, expect PASS** — `npm --prefix pi test -- runs`

- [ ] **Step 8: Commit** — `git commit -am "feat(pi): emit activity cascade on PB"`

---

### Task 8: `GET /v1/activity` + `/v1/activity` live WS

**Files:**
- Create: `pi/src/api/activity.ts`
- Modify: `pi/src/api/app.ts` (mount REST route + WS, add to `OPEN`/`PUBLIC_READS`)
- Test: `pi/src/api/activity-route.test.ts`

**Interfaces:**
- Consumes: `recentActivity`, `activeSeasonId`, `ActivityHub`.
- Produces: `GET /v1/activity?limit=&before=` → `ActivityEvent[]` newest-first; `GET /v1/activity` WS (upgrade) streaming live `ActivityEvent`s.

- [ ] **Step 1: Write the failing test:**

```typescript
// pi/src/api/activity-route.test.ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { ActivityHub } from '../activity/hub';
import { insertActivityEvents } from '../db/activity';
import { createApp } from './app';

function ctx() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Gub','#38bdf8')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'crown_city','Crown City')");
  return { db, app: createApp(db, new EventHub(), undefined, { activity: new ActivityHub() }) };
}

describe('GET /v1/activity', () => {
  it('returns newest-first, no auth required', async () => {
    const { db, app } = ctx();
    insertActivityEvents(db, [
      { ts: 1, type: 'pb', season_id: 1, player_id: 1, course_id: 1, cc: 150, payload: { time_str: '1:48.000' } },
      { ts: 2, type: 'pb', season_id: 1, player_id: 1, course_id: 1, cc: 150, payload: { time_str: '1:47.000' } },
    ]);
    const res = await app.request('/v1/activity?limit=10');
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.map((e: any) => e.payload.time_str)).toEqual(['1:47.000', '1:48.000']);
  });
});
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Create `pi/src/api/activity.ts`:**

```typescript
import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import { recentActivity } from '../db/activity';
import { activeSeasonId } from '../db/seasons';

const num = (v: string | undefined, d: number) => { const n = Number(v); return Number.isFinite(n) ? n : d; };

export function activityRoutes(db: DatabaseSync): Hono<Env> {
  const r = new Hono<Env>();
  r.get('/v1/activity', (c) => {
    const seasonId = num(c.req.query('season'), activeSeasonId(db));
    const before = c.req.query('before') ? num(c.req.query('before'), 0) : undefined;
    return c.json(recentActivity(db, { seasonId, before, limit: num(c.req.query('limit'), 100) }));
  });
  return r;
}
```

- [ ] **Step 4: Mount it + the WS in `app.ts`.** Add `/v1/activity` to `PUBLIC_READS` (and `OPEN`), `app.route('/', activityRoutes(db));`, and in `makeWs(...)` add a live channel (note: HTTP GET and WS share the path; Hono routes the upgrade):

```typescript
// app.ts createApp: import { activityRoutes } from './activity';
app.route('/', activityRoutes(db));   // REST; add '/v1/activity' to PUBLIC_READS set

// app.ts makeWs(app, hub, presence, db, activity): add the activity param, then:
app.get('/v1/activity/stream', upgradeWebSocket(() => {
  let unsub = () => {};
  return {
    onOpen(_e, ws) { unsub = activity.subscribe(ev => ws.send(JSON.stringify(ev))); },
    onClose() { unsub(); },
  };
}));
```

Use path `/v1/activity/stream` for the WS to avoid colliding with the REST `GET /v1/activity`; add it to `OPEN`. Thread the same `ActivityHub` instance from `createApp` into `makeWs` (update the server bootstrap in `pi/src/server.ts` to construct one `ActivityHub` and pass it to both).

- [ ] **Step 5: Run, expect PASS. Commit** — `git commit -am "feat(pi): /v1/activity history + live stream"`

> **End of Phase 1** — PB/RANK/TURF now persist, serve as history, and stream live. The remaining phases add WR-driven turf, off-track rows, attempts segments, and backfill.

---

## PHASE 2 — WR → TURF

### Task 9: WR change recomputes turf

**Files:**
- Modify: `pi/src/wr/reconcile.ts` (`reconcileOne`, the Case-2 WR-change block ~line 60–93)
- Test: `pi/src/wr/reconcile-activity.test.ts`

**Interfaces:**
- Consumes: `courseLeaderboard`, `turfTransitions`, `commitActivity`, `ActivityHub`.
- Produces: on a WR change, a `wr` activity event + any `turf_fire`/`turf_waver` for that course's leader.

> `reconcileOne` currently takes `(db, hub, s, courseId, cc, report)`. Thread an `ActivityHub` to it (through `scrapeOnce`/`startWrScraper`/`reconcile`). When `cur` exists and the current WR changes, after the transaction:

- [ ] **Step 1: Write the failing test** — seed a course with a leader on fire under the old WR, apply a faster WR, assert a `wr` event and a `turf_waver` are committed. (Construct `ScrapedWr`/`reconcileOne` inputs per the existing `reconcile.test.ts` pattern; capture via an `ActivityHub` subscription.)

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement** — in the `if (cur) { hub.publish({type:'wr_update', ...}) }` block of `reconcileOne`, additionally:

```typescript
import { courseLeaderboard } from '../db/reads';
import { turfTransitions } from '../turf/transitions';
import { commitActivity } from '../activity/publish';
// ...inside the `if (cur)` block, with `activity: ActivityHub`, `seasonId = activeSeasonId(db)`:
const board = courseLeaderboard(db, seasonId, courseId, cc);
const ts = Date.now();
const inputs = [{ ts, type: 'wr' as const, season_id: seasonId, player_id: null, course_id: courseId, cc,
  payload: { time_ms: s.recordMs, time_str: s.recordStr, holder: s.holder, delta_ms: s.recordMs - cur.record_ms } }];
for (const t of turfTransitions({ board, wr: cur.record_ms }, { board, wr: s.recordMs })) {
  if (t.kind === 'fire') inputs.push({ ts, type: 'turf_fire', season_id: seasonId, player_id: t.leaderId, course_id: courseId, cc, payload: {} });
  else if (t.kind === 'waver') inputs.push({ ts, type: 'turf_waver', season_id: seasonId, player_id: t.leaderId, course_id: courseId, cc, payload: {} });
}
commitActivity(db, activity, inputs);
```

- [ ] **Step 4: Run, expect PASS. Commit** — `git commit -am "feat(pi): WR change recomputes turf activity"`

---

## PHASE 3 — Off-track (per-screen) rows

### Task 10: Screen intervals emit per-screen events

**Files:**
- Create: `pi/src/activity/screens.ts`, `pi/src/activity/screens.test.ts`
- Modify: `pi/src/stats/screen.ts` (`insertScreenIntervals` → also return the inserted rows so the caller can emit), `pi/src/api/screen.ts` (commit activity for inserted intervals)

**Interfaces:**
- Produces: `SCREEN_LABELS: Record<string,string>` mapping engine screen names → log labels (`CHARACTER_SELECT`→`character select`, `KART_SELECT`→`kart select`, `COURSE_SELECT`→`track select`, `GHOST`/`START_REPLAY`/`REPLAY_MENU`→`watching a ghost`, default→`menus`); `screenActivityInputs(seasonId, playerId, intervals): ActivityInput[]` (one `screen` event per interval — **no duration floor**, label via the map, `payload:{ screen:label, dwell_ms: ended-started }`, `course_id:null`).

- [ ] **Step 1: Write the failing test** for `screenActivityInputs` (every interval → one input; label mapping; dwell computed; no floor — a 200 ms blip still emits).

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Create `pi/src/activity/screens.ts`:**

```typescript
import type { ActivityInput } from './types';
import type { ScreenInterval } from '../stats/screen';

export const SCREEN_LABELS: Record<string, string> = {
  CHARACTER_SELECT: 'character select', KART_SELECT: 'kart select', COURSE_SELECT: 'track select',
  GHOST: 'watching a ghost', START_REPLAY: 'watching a ghost', REPLAY_MENU: 'watching a ghost',
};
export const labelFor = (screen: string) => SCREEN_LABELS[screen] ?? 'menus';

export function screenActivityInputs(seasonId: number, playerId: number, intervals: ScreenInterval[]): ActivityInput[] {
  return intervals
    .filter(iv => iv.screen && iv.ended_ms > iv.started_ms)
    .map(iv => ({ ts: iv.started_ms, type: 'screen', season_id: seasonId, player_id: playerId,
      course_id: null, cc: null, payload: { screen: labelFor(iv.screen), dwell_ms: iv.ended_ms - iv.started_ms } }));
}
```

- [ ] **Step 4: Wire into the screen route.** Make `insertScreenIntervals` return the intervals it actually inserted (currently returns a count). In `pi/src/api/screen.ts`, after inserting, `commitActivity(db, activity, screenActivityInputs(seasonId, playerId, insertedIntervals))`. Thread `ActivityHub` into `screenRoutes(db, activity)` and its registration in `app.ts`.

- [ ] **Step 5: Run, expect PASS. Commit** — `git commit -am "feat(pi): per-screen off-track activity rows"`

---

## PHASE 4 — Attempts (grind) segments

### Task 11: Grind tracker → closed attempts segments

**Files:**
- Create: `pi/src/activity/grind.ts`, `pi/src/activity/grind.test.ts`
- Modify: `pi/src/api/runs.ts` (update the tracker on every run; pass the closed segment into the cascade)

**Interfaces:**
- Produces: `GrindTracker` with `note(playerId, courseId, ts): { count:number; durationMs:number } | null` — increments the open segment on a same-course run, and **returns + closes** the prior segment when (a) the run is the player's PB (the run handler calls `close(playerId)` after a PB) or (b) the course changed. `close(playerId, ts)` returns the open segment (or null) and resets it.

> Model: per-player in-memory `{ courseId, count, startedTs }`. `note()` is called on **every** finished/reset run *before* the cascade: if same course, `count++`; if different course, capture the old segment to return as "closed", then start fresh. On a **PB**, the run handler calls `close()` to hand the just-finished grind to `buildRunCascade({ attempts })` and start the next segment.

- [ ] **Step 1: Write the failing test:**

```typescript
// pi/src/activity/grind.test.ts
import { describe, it, expect } from 'vitest';
import { GrindTracker } from './grind';

describe('GrindTracker', () => {
  it('counts same-course attempts and closes on PB', () => {
    const g = new GrindTracker();
    g.note(1, 5, 1000); g.note(1, 5, 2000); g.note(1, 5, 3000); // 3 attempts
    expect(g.close(1, 3000)).toEqual({ count: 3, durationMs: 2000 });
    expect(g.close(1, 4000)).toBeNull(); // nothing open after close
  });
  it('closes the prior segment when the course changes', () => {
    const g = new GrindTracker();
    g.note(1, 5, 1000); g.note(1, 5, 2000);
    const closed = g.note(1, 6, 5000); // moved to course 6
    expect(closed).toEqual({ count: 2, durationMs: 1000 });
  });
});
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Create `pi/src/activity/grind.ts`:**

```typescript
interface Seg { courseId: number; count: number; startedTs: number; lastTs: number }
export interface ClosedSeg { count: number; durationMs: number }

export class GrindTracker {
  private open = new Map<number, Seg>();
  /** Record a run; returns a closed segment if the course changed. */
  note(playerId: number, courseId: number, ts: number): ClosedSeg | null {
    const cur = this.open.get(playerId);
    if (cur && cur.courseId === courseId) { cur.count++; cur.lastTs = ts; return null; }
    const closed = cur ? { count: cur.count, durationMs: cur.lastTs - cur.startedTs } : null;
    this.open.set(playerId, { courseId, count: 1, startedTs: ts, lastTs: ts });
    return closed && closed.count > 0 ? closed : null;
  }
  /** Close + return the open segment (e.g. on a PB), resetting it. */
  close(playerId: number, ts: number): ClosedSeg | null {
    const cur = this.open.get(playerId);
    this.open.delete(playerId);
    if (!cur) return null;
    const seg = { count: cur.count, durationMs: (ts > cur.lastTs ? ts : cur.lastTs) - cur.startedTs };
    return seg.count > 0 ? seg : null;
  }
}
```

- [ ] **Step 4: Wire into `runs.ts`.** Construct one module-level `GrindTracker` in `runsRoutes`. On every run (any status), call `tracker.note(playerId, courseId, Date.now())` and `commitActivity` any returned course-change segment as a standalone `attempts` event. On a PB, pass `attempts: tracker.close(playerId, Date.now())` into `buildRunCascade`.

```typescript
// runs.ts (inside runsRoutes closure)
const tracker = new GrindTracker();
// after resolving courseId, before/independent of the PB branch:
const closedByMove = tracker.note(playerId, courseId, Date.now());
if (closedByMove) commitActivity(db, activity, [{ ts: Date.now(), type: 'attempts', season_id: seasonId,
  player_id: playerId, course_id: /* the PRIOR course */ priorCourseId, cc, payload: { count: closedByMove.count, duration_ms: closedByMove.durationMs } }]);
// (capture priorCourseId from tracker before note(), or have note() return it)
// in the PB branch: attempts: tracker.close(playerId, Date.now())
```

> Note for the implementer: `note()` must also surface the *prior* segment's `courseId` so the standalone close event is attributed to the right course — extend `ClosedSeg` with `courseId` (update the Task-11 test accordingly).

- [ ] **Step 5: Run, expect PASS. Commit** — `git commit -am "feat(pi): attempts-segment grind tracking"`

---

## PHASE 5 — Backfill

### Task 12: One-time history replay

**Files:**
- Create: `pi/src/activity/backfill.ts`, `pi/src/activity/backfill.test.ts`
- Modify: `pi/src/db/connect.ts` (`applySchema` runs the backfill once if `activity_events` is empty)

**Interfaces:**
- Produces: `backfillActivity(db): number` — replays all finished runs in `ended_at` order, maintaining a per-course board, inserting `attempts`/`pb`/`rank`/`turf_*` with `ts = run ended_at`. Deterministic; idempotent (guarded by an empty-table check). Returns the count inserted. WR history is replayed by interleaving `world_records.achieved_at` to drive `turf_*`. (Screen + attempts backfill MAY be limited to keep it tractable — at minimum backfill `pb`/`rank`/`turf`.)

- [ ] **Step 1: Write the failing test** — seed two players' finished runs across two timestamps on one course; `backfillActivity(db)`; assert the `activity_events` contain the expected `pb` + `turf_claim` in chronological `id` order, and that a second call inserts nothing (idempotent).

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement `backfillActivity`** — reuse `territoryTimeline(db, cc)`-style ordering: pull finished runs `ORDER BY ended_at, id`, replay a `Map<courseId, LeaderRow[]>`, and for each run compute `before`/`after` boards + `buildRunCascade` (with `ts = Date.parse(ended_at)`, `prevPbMs` from the player's prior board entry, `attempts: null` for v1), inserting via `insertActivityEvents`. Interleave WR `achieved_at` rows to emit `wr` + turf transitions. Guard: `if ((SELECT COUNT(*) FROM activity_events) > 0) return 0;`

- [ ] **Step 4: Invoke once from `applySchema`** (after the table exists):

```typescript
// connect.ts applySchema(), at the end:
try {
  const empty = (db.prepare('SELECT COUNT(*) c FROM activity_events').get() as any).c === 0;
  if (empty) backfillActivity(db);
} catch { /* table not present in an older partial schema */ }
```

- [ ] **Step 5: Run, expect PASS. Commit** — `git commit -am "feat(pi): one-time activity backfill"`

---

## Final verification

- [ ] Run the whole pi suite: `npm --prefix pi test` — all green.
- [ ] Run the type check: `npm --prefix pi run typecheck` — clean (keep new pi source tsc-clean).
- [ ] Manual smoke: boot the server against a copy of `pi/mkw.db`, `GET /v1/activity?limit=20`, confirm a sensible backfilled feed; POST a finished run via the existing flow and confirm a live event on `/v1/activity/stream`.

## Self-review notes (author)

- **Spec coverage:** activity_events ✓(T1), persisted-once ✓(T2/T7), cascade PB→RANK→TURF ✓(T6), fire model server-side ✓(T3/T4), WR→TURF ✓(T9), off-track no-floor ✓(T10), attempts segments ✓(T11), backfill ✓(T12), `/v1/activity` REST+live additive ✓(T8). **Deferred to Plan 2:** presence `session_attempts`/`screen_since_ms` + the card (Part A) and all `web/` rendering (Part B UI). **Deviation:** leaderboard cache replaced by before/after snapshots (documented above).
- **Type consistency:** `LeaderRow` reused from `reads.ts` throughout; `ActivityInput`/`ActivityEvent` from `activity/types.ts`; `ActivityHub` single instance threaded `createApp`→`runsRoutes`/`screenRoutes`/`makeWs` and into `reconcile`.
- **Open implementation detail flagged in-task:** `GrindTracker.note()` must surface the prior segment's `courseId` (T11 Step 4 note).

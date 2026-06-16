# Broadcast Stats Engine — Implementation Plan (Increment #1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the composable stats engine and its `/v1/stats` HTTP surface (race + body domains, tz-aware periods, cross-domain body-condition filter) per `docs/superpowers/specs/2026-06-08-broadcast-stats-engine-design.md`.

**Architecture:** A metric registry + resolver in a new `pi/src/stats/` module, served by the existing Hono app. Periods are resolved tz-correctly in app code (Luxon) into UTC bounds; SQL only range-compares. `was_pb` is a new stored column maintained at finalize. Body facts come from a read-only `porker.db` union; a cross-domain alignment helper filters race metrics by body state "as of" a run. Compute is on-demand (data is tiny).

**Tech Stack:** TypeScript, `node:sqlite` (`DatabaseSync`), Hono, Luxon (new dep — pure JS, no native binaries, so it sidesteps the CI optional-deps lockfile issue), vitest.

**Conventions (read before starting):**
- All commands run from the `pi/` directory. Full suite: `npm test`. Single file: `npx vitest run src/stats/<file>.test.ts`.
- Tests are co-located `*.test.ts`, use `import { describe, it, expect } from 'vitest'`, and build fixtures with an in-memory `new DatabaseSync(':memory:')` + `applySchema`.
- Period UTC bounds are SQLite `datetime()`-comparable strings `'YYYY-MM-DD HH:MM:SS'`. Always compare timestamp columns as `datetime(col)` (the DB mixes `T`/space formats).
- Existing helpers to reuse: `applySchema`/`openDb` (`src/db/connect.ts`), `activeSeasonId`/`courseIdBySlug` (`src/db/seasons.ts`), `slugify` (`src/db/slug.ts`), `recomputeIsPb` (`src/db/pb.ts`).

---

## File Structure

| File | Responsibility |
|---|---|
| `pi/src/stats/types.ts` | Shared types: `Dimension`, `Period`, `StatRow`, `StatResult`, `MetricDef`. |
| `pi/src/stats/period.ts` | `resolvePeriod(key, tz, opts)` → tz-correct UTC `[start,end)` (+ ISO for display) and epoch helpers. |
| `pi/src/stats/metrics.ts` | Declarative metric registry (race + body) + lookup/validation helpers. |
| `pi/src/stats/resolve.ts` | `resolveRace()` — builds value/breakdown SQL from a race metric + filters + period + group_by. |
| `pi/src/stats/body.ts` | Read-only porker access; normalized union (`player`, `ts`, columns); `resolveBody()`. |
| `pi/src/stats/align.ts` | "Measurement as of run R" + `body_condition` parse/evaluate; race-metric filter hook. |
| `pi/src/api/stats.ts` | Hono routes `/v1/stats/{value,breakdown,series,metrics}` + validation. |
| `pi/src/db/pb.ts` (modify) | Add `recomputeWasPb()` + `backfillWasPb()`. |
| `pi/src/db/connect.ts` (modify) | Additive `runs.was_pb` migration + one-time backfill. |
| `pi/src/api/runs.ts` (modify) | Call `recomputeWasPb` after `recomputeIsPb` at finalize. |
| `pi/src/api/app.ts` (modify) | Mount the stats routes. |

**Scope note:** `time_improvement` from the spec catalog is **deferred to increment #2** — it needs an "as-of-time PB" (the same machinery as resets-since-PB). Every other catalog metric is in this plan.

---

## Task 1: Period resolution (Luxon)

**Files:**
- Modify: `pi/package.json` (add `luxon` + `@types/luxon`)
- Create: `pi/src/stats/types.ts`
- Create: `pi/src/stats/period.ts`
- Test: `pi/src/stats/period.test.ts`

- [ ] **Step 1: Add the dependency**

Run (in `pi/`): `npm install luxon@^3.5.0 && npm install -D @types/luxon@^3.4.2`
Expected: `package.json` gains `luxon` (deps) and `@types/luxon` (devDeps); `npm test` still green.

- [ ] **Step 2: Write `types.ts`**

```ts
// pi/src/stats/types.ts
export type Dimension = 'player' | 'course' | 'character' | 'kart' | 'costume' | 'cc';
export type PeriodKey = 'today' | 'this_week' | 'this_month' | 'all_time' | 'range';

export interface Period {
  key: PeriodKey;
  tz: string;
  startUtc: string | null;   // 'YYYY-MM-DD HH:MM:SS' UTC (datetime()-comparable), null = open
  endUtc: string | null;
  startIso: string | null;   // offset ISO for the response, null = open
  endIso: string | null;
}

export interface StatRow { key: string; value: number | null; }

export interface StatResult {
  metric: string;
  period: { key: string; tz: string; start: string | null; end: string | null };
  filters: Record<string, string>;
  group_by?: Dimension;
  rows: StatRow[];
  total: number | null;
  unevaluable?: number;      // runs skipped by a body_condition (no prior weigh-in)
}
```

- [ ] **Step 3: Write the failing test**

```ts
// pi/src/stats/period.test.ts
import { describe, it, expect } from 'vitest';
import { DateTime } from 'luxon';
import { resolvePeriod, toEpochSeconds } from './period';

const MEL = 'Australia/Melbourne';

describe('resolvePeriod', () => {
  it('today uses the tz midnight and is DST-correct (AEDT +11 in January)', () => {
    const now = DateTime.fromISO('2026-01-15T09:00:00', { zone: MEL }); // AEDT (+11)
    const p = resolvePeriod('today', MEL, { now });
    // Midnight Melbourne 2026-01-15 == 2026-01-14 13:00:00 UTC
    expect(p.startUtc).toBe('2026-01-14 13:00:00');
    expect(p.endUtc).toBe('2026-01-15 13:00:00');
  });

  it('today is DST-correct in winter (AEST +10 in July)', () => {
    const now = DateTime.fromISO('2026-07-15T09:00:00', { zone: MEL }); // AEST (+10)
    const p = resolvePeriod('today', MEL, { now });
    expect(p.startUtc).toBe('2026-07-14 14:00:00');
  });

  it('this_week starts Monday', () => {
    const now = DateTime.fromISO('2026-06-10T12:00:00', { zone: MEL }); // a Wednesday
    const p = resolvePeriod('this_week', MEL, { now });
    // Monday 2026-06-08 00:00 Melbourne (AEST +10) == 2026-06-07 14:00 UTC
    expect(p.startUtc).toBe('2026-06-07 14:00:00');
    expect(p.endUtc).toBe('2026-06-14 14:00:00');
  });

  it('all_time has open bounds', () => {
    const p = resolvePeriod('all_time', MEL);
    expect(p.startUtc).toBeNull();
    expect(p.endUtc).toBeNull();
  });

  it('rejects an invalid timezone', () => {
    expect(() => resolvePeriod('today', 'Mars/Olympus')).toThrow(/invalid tz/);
  });
});

describe('toEpochSeconds', () => {
  it('parses a UTC sql string to epoch seconds', () => {
    expect(toEpochSeconds('2026-06-07 14:00:00')).toBe(
      DateTime.fromISO('2026-06-07T14:00:00', { zone: 'utc' }).toSeconds());
  });
});
```

- [ ] **Step 4: Run it, expect failure**

Run: `npx vitest run src/stats/period.test.ts`
Expected: FAIL — `resolvePeriod`/`toEpochSeconds` not found.

- [ ] **Step 5: Implement `period.ts`**

```ts
// pi/src/stats/period.ts
import { DateTime } from 'luxon';
import type { Period, PeriodKey } from './types';

const SQL = 'yyyy-MM-dd HH:mm:ss';

export interface PeriodOpts { from?: string; to?: string; now?: DateTime; }

/** Resolve a period key + IANA tz into UTC [start,end) bounds (+ ISO for display).
 *  Weeks start Monday (Luxon ISO weeks). all_time → open (null) bounds. */
export function resolvePeriod(key: PeriodKey, tz: string, opts: PeriodOpts = {}): Period {
  if (key === 'all_time') return { key, tz, startUtc: null, endUtc: null, startIso: null, endIso: null };

  const now = (opts.now ?? DateTime.now()).setZone(tz);
  if (!now.isValid) throw new Error(`invalid tz: ${tz}`);

  let start: DateTime, end: DateTime;
  if (key === 'today') { start = now.startOf('day'); end = start.plus({ days: 1 }); }
  else if (key === 'this_week') { start = now.startOf('week'); end = start.plus({ weeks: 1 }); }
  else if (key === 'this_month') { start = now.startOf('month'); end = start.plus({ months: 1 }); }
  else if (key === 'range') {
    if (!opts.from || !opts.to) throw new Error('range requires from and to');
    start = DateTime.fromISO(opts.from, { zone: tz });
    end = DateTime.fromISO(opts.to, { zone: tz });
    if (!start.isValid || !end.isValid) throw new Error('invalid range bounds');
  } else throw new Error(`unknown period: ${key}`);

  return {
    key, tz,
    startUtc: start.toUTC().toFormat(SQL),
    endUtc: end.toUTC().toFormat(SQL),
    startIso: start.toISO(),
    endIso: end.toISO(),
  };
}

/** A UTC sql string ('YYYY-MM-DD HH:MM:SS') → epoch seconds (for porker's integer Timestamp). */
export function toEpochSeconds(utcSql: string): number {
  return DateTime.fromFormat(utcSql, SQL, { zone: 'utc' }).toSeconds();
}
```

- [ ] **Step 6: Run it, expect pass**

Run: `npx vitest run src/stats/period.test.ts`
Expected: PASS (6 tests).

- [ ] **Step 7: Commit**

```bash
git add pi/package.json pi/package-lock.json pi/src/stats/types.ts pi/src/stats/period.ts pi/src/stats/period.test.ts
git commit -m "feat(stats): tz-aware period resolution (Luxon)"
```

---

## Task 2: `was_pb` — stored column, maintainer, backfill

**Files:**
- Modify: `pi/src/db/pb.ts`
- Modify: `pi/src/db/connect.ts:15-31` (`applySchema`)
- Modify: `pi/src/api/runs.ts:35`
- Test: `pi/src/db/pb.test.ts` (exists — append)

- [ ] **Step 1: Write the failing test**

```ts
// pi/src/db/pb.test.ts  (append; keep existing imports/tests)
import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from './connect';
import { recomputeWasPb, backfillWasPb } from './pb';

function seedRun(db: DatabaseSync, id: number, ms: number, endedAt: string, status = 'finished') {
  db.prepare(
    `INSERT INTO runs(id, season_id, player_id, course_id, cc, status, provenance, ended_at, total_time_ms)
     VALUES (?, 1, 1, 1, 150, ?, 'live', ?, ?)`
  ).run(id, status, endedAt, status === 'finished' ? ms : null);
}

function baseDb(): DatabaseSync {
  const db = new DatabaseSync(':memory:');
  applySchema(db);
  db.exec(`INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1);
           INSERT INTO players(id,display_name) VALUES (1,'Paul');
           INSERT INTO courses(id,slug,display_name) VALUES (1,'bc','Bowsers Castle');`);
  return db;
}

describe('recomputeWasPb', () => {
  it('flags the record progression: first finish + each strict improvement', () => {
    const db = baseDb();
    seedRun(db, 1, 160000, '2026-06-01T00:00:00+00:00');  // first → PB
    seedRun(db, 2, 165000, '2026-06-02T00:00:00+00:00');  // slower → not
    seedRun(db, 3, 160000, '2026-06-03T00:00:00+00:00');  // ties prev best → not (strict)
    seedRun(db, 4, 158000, '2026-06-04T00:00:00+00:00');  // faster → PB
    seedRun(db, 5, 0,      '2026-06-05T00:00:00+00:00', 'reset'); // reset → never
    recomputeWasPb(db, 1, 1, 1, 150);
    const flags = db.prepare('SELECT id, was_pb FROM runs ORDER BY id').all();
    expect(flags).toEqual([
      { id: 1, was_pb: 1 }, { id: 2, was_pb: 0 }, { id: 3, was_pb: 0 },
      { id: 4, was_pb: 1 }, { id: 5, was_pb: 0 },
    ]);
  });
});

describe('backfillWasPb', () => {
  it('populates every (season,player,course,cc) group', () => {
    const db = baseDb();
    seedRun(db, 1, 160000, '2026-06-01T00:00:00+00:00');
    seedRun(db, 2, 158000, '2026-06-02T00:00:00+00:00');
    db.prepare('UPDATE runs SET was_pb=0').run(); // simulate pre-migration state
    backfillWasPb(db);
    const pbs = db.prepare('SELECT id FROM runs WHERE was_pb=1 ORDER BY id').all();
    expect(pbs).toEqual([{ id: 1 }, { id: 2 }]);
  });
});
```

- [ ] **Step 2: Run it, expect failure**

Run: `npx vitest run src/db/pb.test.ts`
Expected: FAIL — `recomputeWasPb`/`backfillWasPb` not exported; (and `was_pb` column missing until Step 4 migration).

- [ ] **Step 3: Add the migration to `applySchema`**

In `pi/src/db/connect.ts`, inside `applySchema`, after the `world_records` block and before the `CREATE UNIQUE INDEX idx_wr_current` line, add:

```ts
  // Additive: per-run "was a PB when set" flag. Backfilled once, on first add.
  try {
    db.exec('ALTER TABLE runs ADD COLUMN was_pb INTEGER NOT NULL DEFAULT 0');
    backfillWasPb(db);
  } catch { /* already present + backfilled */ }
```

And add the import at the top of `connect.ts`:

```ts
import { backfillWasPb } from './pb';
```

- [ ] **Step 4: Implement the maintainer + backfill in `pb.ts`**

Append to `pi/src/db/pb.ts`:

```ts
/** Re-derive was_pb for one (season,player,course,cc): a finished run was a PB iff it is
 *  strictly faster than every chronologically-prior finished run (first finish counts).
 *  Idempotent; safe under attempt-replacement / out-of-order ingest. */
export function recomputeWasPb(db: DatabaseSync, seasonId: number, playerId: number, courseId: number, cc: number): void {
  const rows = db.prepare(
    `WITH f AS (
       SELECT id, total_time_ms,
         MIN(total_time_ms) OVER (ORDER BY datetime(ended_at), id
              ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_min
       FROM runs
       WHERE season_id=? AND player_id=? AND course_id=? AND cc=? AND status='finished')
     SELECT id, CASE WHEN prior_min IS NULL OR total_time_ms < prior_min THEN 1 ELSE 0 END AS was_pb
     FROM f`
  ).all(seasonId, playerId, courseId, cc) as { id: number; was_pb: number }[];
  const upd = db.prepare('UPDATE runs SET was_pb=? WHERE id=?');
  for (const r of rows) upd.run(r.was_pb, r.id);
  // Non-finished runs in the group are never PBs.
  db.prepare("UPDATE runs SET was_pb=0 WHERE season_id=? AND player_id=? AND course_id=? AND cc=? AND status!='finished'")
    .run(seasonId, playerId, courseId, cc);
}

/** One-time: re-derive was_pb for every group that has finished runs. */
export function backfillWasPb(db: DatabaseSync): void {
  const groups = db.prepare(
    "SELECT DISTINCT season_id, player_id, course_id, cc FROM runs WHERE status='finished'"
  ).all() as { season_id: number; player_id: number; course_id: number; cc: number }[];
  for (const g of groups) recomputeWasPb(db, g.season_id, g.player_id, g.course_id, g.cc);
}
```

- [ ] **Step 5: Run it, expect pass**

Run: `npx vitest run src/db/pb.test.ts`
Expected: PASS.

- [ ] **Step 6: Wire into finalize**

In `pi/src/api/runs.ts`, change the import line 10 and add the call after `recomputeIsPb`:

```ts
import { recomputeIsPb, recomputeWasPb } from '../db/pb';
```
```ts
    recomputeIsPb(db, seasonId, playerId, courseId, cc);
    recomputeWasPb(db, seasonId, playerId, courseId, cc);
```

- [ ] **Step 7: Run the whole suite (guards the migration + existing ingest)**

Run: `npm test`
Expected: PASS (existing tests + new). If an existing test builds a `runs` table by hand without `was_pb`, it still passes (column has a default); the migration adds it on `applySchema`.

- [ ] **Step 8: Commit**

```bash
git add pi/src/db/pb.ts pi/src/db/connect.ts pi/src/api/runs.ts pi/src/db/pb.test.ts
git commit -m "feat(stats): stored was_pb column maintained at finalize + backfill"
```

---

## Task 3: Metric registry

**Files:**
- Create: `pi/src/stats/metrics.ts`
- Test: `pi/src/stats/metrics.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// pi/src/stats/metrics.test.ts
import { describe, it, expect } from 'vitest';
import { getMetric, listMetrics, allowsDimension } from './metrics';

describe('metric registry', () => {
  it('exposes coins as an all-status lap-grain metric', () => {
    const m = getMetric('coins');
    expect(m?.kind).toBe('race');
    if (m?.kind === 'race') {
      expect(m.statuses).toBe('all');
      expect(m.joins).toContain('laps');
    }
  });

  it('pb_count is finished-only and pb-restricted', () => {
    const m = getMetric('pb_count');
    expect(m?.kind === 'race' && m.pbOnly).toBe(true);
  });

  it('body_fat is a body metric not groupable by course', () => {
    expect(allowsDimension('body_fat', 'player')).toBe(true);
    expect(allowsDimension('body_fat', 'course')).toBe(false);
  });

  it('unknown metric returns undefined', () => {
    expect(getMetric('nope')).toBeUndefined();
  });

  it('lists both domains', () => {
    const ids = listMetrics().map((m) => m.id);
    expect(ids).toContain('resets');
    expect(ids).toContain('muscle_mass');
  });
});
```

- [ ] **Step 2: Run it, expect failure**

Run: `npx vitest run src/stats/metrics.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `metrics.ts`**

```ts
// pi/src/stats/metrics.ts
import type { Dimension } from './types';

export const RACE_DIMENSIONS: Dimension[] = ['player', 'course', 'character', 'kart', 'costume', 'cc'];

export type Status = 'finished' | 'reset' | 'dnf';

export interface RaceMetric {
  id: string;
  kind: 'race';
  /** SQL aggregate over the joined run/lap/point rows. */
  value: string;
  /** Which run statuses count; 'all' = no status filter. */
  statuses: Status[] | 'all';
  /** Extra joins the value needs. */
  joins: Array<'laps' | 'points'>;
  /** Restrict to was_pb=1 (PB counters). */
  pbOnly?: boolean;
}

export type BodyAgg = 'current' | 'change' | 'min' | 'max';

export interface BodyMetric {
  id: string;
  kind: 'body';
  /** Normalized column on the porker union (see body.ts). */
  column: string;
  aggs: BodyAgg[];
  defaultAgg: BodyAgg;
}

export type MetricDef = RaceMetric | BodyMetric;

const RACE: RaceMetric[] = [
  { id: 'attempts',        kind: 'race', value: 'COUNT(*)',                                            statuses: 'all',          joins: [] },
  { id: 'resets',          kind: 'race', value: 'COUNT(*)',                                            statuses: ['reset'],      joins: [] },
  { id: 'finishes',        kind: 'race', value: 'COUNT(*)',                                            statuses: ['finished'],   joins: [] },
  { id: 'reset_rate',      kind: 'race', value: "AVG(CASE WHEN r.status='reset' THEN 1.0 ELSE 0.0 END)", statuses: 'all',        joins: [] },
  { id: 'coins',           kind: 'race', value: 'SUM(rl.coins)',                                       statuses: 'all',          joins: ['laps'] },
  { id: 'mushrooms',       kind: 'race', value: 'SUM(rl.shrooms)',                                     statuses: 'all',          joins: ['laps'] },
  { id: 'driving_time',    kind: 'race', value: 'SUM(pt.maxt)',                                        statuses: 'all',          joins: ['points'] },
  { id: 'best_time',       kind: 'race', value: 'MIN(r.total_time_ms)',                                statuses: ['finished'],   joins: [] },
  { id: 'avg_finish_time', kind: 'race', value: 'AVG(r.total_time_ms)',                                statuses: ['finished'],   joins: [] },
  { id: 'pb_count',        kind: 'race', value: 'COUNT(*)',                                            statuses: ['finished'],   joins: [], pbOnly: true },
];

const BODY_COLUMNS: Record<string, string> = {
  weight: 'weight', bmi: 'bmi', body_fat: 'body_fat', fat_free_weight: 'fat_free_weight',
  subcutaneous_fat: 'subcutaneous_fat', visceral_fat: 'visceral_fat', body_water: 'body_water',
  skeletal_muscle: 'skeletal_muscle', muscle_mass: 'muscle_mass', bone_mass: 'bone_mass',
  protein: 'protein', bmr: 'bmr', metabolic_age: 'metabolic_age',
};

const BODY: BodyMetric[] = Object.entries(BODY_COLUMNS).map(([id, column]) => ({
  id, kind: 'body', column, aggs: ['current', 'change', 'min', 'max'], defaultAgg: 'current',
}));

const REGISTRY = new Map<string, MetricDef>([...RACE, ...BODY].map((m) => [m.id, m]));

export function getMetric(id: string): MetricDef | undefined { return REGISTRY.get(id); }
export function listMetrics(): MetricDef[] { return [...REGISTRY.values()]; }

/** Which dimensions a metric may be filtered/grouped by. Body metrics: player only. */
export function allowsDimension(metricId: string, dim: Dimension): boolean {
  const m = REGISTRY.get(metricId);
  if (!m) return false;
  return m.kind === 'race' ? RACE_DIMENSIONS.includes(dim) : dim === 'player';
}
```

- [ ] **Step 4: Run it, expect pass**

Run: `npx vitest run src/stats/metrics.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/stats/metrics.ts pi/src/stats/metrics.test.ts
git commit -m "feat(stats): declarative metric registry (race + body)"
```

---

## Task 4: Race resolver (value + breakdown)

**Files:**
- Create: `pi/src/stats/resolve.ts`
- Test: `pi/src/stats/resolve.test.ts`

The resolver builds one query: `SELECT <groupExpr> AS key, <value> FROM runs r [joins] WHERE <season,cc,status,filters,window> [GROUP BY key]`. Group keys map to display labels (course/player) or the raw column (character/kart/costume/cc).

- [ ] **Step 1: Write the failing test**

```ts
// pi/src/stats/resolve.test.ts
import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { resolveRace } from './resolve';
import { resolvePeriod } from './period';
import { DateTime } from 'luxon';

function db(): DatabaseSync {
  const d = new DatabaseSync(':memory:');
  applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1);
          INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke');
          INSERT INTO courses(id,slug,display_name) VALUES (1,'bc','Bowsers Castle'),(2,'mbc','Mario Bros Circuit');`);
  const run = (id:number,p:number,c:number,st:string,ms:number|null,when:string,ch:string) =>
    d.prepare(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,ended_at,total_time_ms,character)
               VALUES (?,1,?,?,150,?,'live',?,?,?)`).run(id,p,c,st,when,ms,ch);
  // Two finished + one reset for Luke on bc, inside the window; one for Paul.
  run(1,2,1,'finished',160000,'2026-06-10T03:00:00+00:00','Mario');
  run(2,2,1,'reset',    null,  '2026-06-10T04:00:00+00:00','Mario');
  run(3,2,2,'finished',120000,'2026-06-10T05:00:00+00:00','Peach');
  run(4,1,1,'finished',158000,'2026-06-10T06:00:00+00:00','Mario');
  // Add laps for coin sums on runs 1 (finished) and 2 (reset).
  const lap = (rid:number,idx:number,coins:number)=>d.prepare(
    'INSERT INTO run_laps(run_id,lap_index,lap_time_ms,coins,shrooms) VALUES (?,?,1000,?,1)').run(rid,idx,coins);
  lap(1,0,5); lap(1,1,4); lap(2,0,3); // reset still collected 3
  return d;
}
const week = () => resolvePeriod('this_week','Australia/Melbourne',
  { now: DateTime.fromISO('2026-06-10T20:00:00',{zone:'Australia/Melbourne'}) });

describe('resolveRace', () => {
  it('counts resets (status-filtered)', () => {
    const r = resolveRace(db(), { metric: 'resets', period: week(), filters: {}, seasonId: 1 });
    expect(r.total).toBe(1);
  });

  it('sums coins across all statuses incl. the reset', () => {
    const r = resolveRace(db(), { metric: 'coins', period: week(), filters: { player: 'Luke' }, seasonId: 1 });
    expect(r.total).toBe(12); // 5+4 (finished) + 3 (reset)
  });

  it('breaks coins down by course', () => {
    const r = resolveRace(db(), { metric: 'coins', period: week(), filters: {}, groupBy: 'course', seasonId: 1 });
    expect(r.rows).toEqual([{ key: 'Bowsers Castle', value: 12 }]); // only bc has laps
  });

  it('pb_count uses the stored flag', () => {
    const d = db();
    // mark run 1 + 4 as was_pb (first finishes are PBs)
    d.prepare('UPDATE runs SET was_pb=1 WHERE id IN (1,4)').run();
    const r = resolveRace(d, { metric: 'pb_count', period: week(), filters: {}, seasonId: 1 });
    expect(r.total).toBe(2);
  });
});
```

- [ ] **Step 2: Run it, expect failure**

Run: `npx vitest run src/stats/resolve.test.ts`
Expected: FAIL — `resolveRace` not found.

- [ ] **Step 3: Implement `resolve.ts`**

```ts
// pi/src/stats/resolve.ts
import type { DatabaseSync } from 'node:sqlite';
import type { Dimension, Period, StatResult, StatRow } from './types';
import { getMetric, type RaceMetric } from './metrics';

export interface RaceQuery {
  metric: string;
  period: Period;
  filters: Partial<Record<Dimension, string>>;
  groupBy?: Dimension;
  seasonId: number;
  cc?: number;
  /** Optional pre-built body-condition predicate (Task 6). */
  bodyConditionSql?: { join: string; where: string; params: unknown[] };
}

const POINTS_JOIN = 'LEFT JOIN (SELECT run_id, MAX(t_ms) AS maxt FROM run_points GROUP BY run_id) pt ON pt.run_id = r.id';
const LAPS_JOIN = 'JOIN run_laps rl ON rl.run_id = r.id';

/** SQL expression + label join for a group-by dimension. */
function groupExpr(dim: Dimension): { select: string; join: string; needsName: 'course' | 'player' | null } {
  switch (dim) {
    case 'course': return { select: 'c.display_name', join: 'JOIN courses c ON c.id = r.course_id', needsName: 'course' };
    case 'player': return { select: 'p.display_name', join: 'JOIN players p ON p.id = r.player_id', needsName: 'player' };
    case 'character': return { select: 'r.character', join: '', needsName: null };
    case 'kart': return { select: 'r.kart', join: '', needsName: null };
    case 'costume': return { select: 'r.costume', join: '', needsName: null };
    case 'cc': return { select: 'CAST(r.cc AS TEXT)', join: '', needsName: null };
  }
}

/** Filter dimension → (column, value-resolver). */
function filterClause(db: DatabaseSync, dim: Dimension, value: string): { sql: string; param: unknown } | null {
  switch (dim) {
    case 'course': {
      const row = db.prepare('SELECT id FROM courses WHERE slug=? OR display_name=? COLLATE NOCASE').get(value, value) as { id: number } | undefined;
      return { sql: 'r.course_id=?', param: row?.id ?? -1 };
    }
    case 'player': {
      const row = db.prepare('SELECT id FROM players WHERE display_name=? COLLATE NOCASE').get(value) as { id: number } | undefined;
      return { sql: 'r.player_id=?', param: row?.id ?? -1 };
    }
    case 'character': return { sql: 'r.character=?', param: value };
    case 'kart': return { sql: 'r.kart=?', param: value };
    case 'costume': return { sql: 'r.costume=?', param: value };
    case 'cc': return { sql: 'r.cc=?', param: Number(value) };
  }
}

export function resolveRace(db: DatabaseSync, q: RaceQuery): StatResult {
  const m = getMetric(q.metric);
  if (!m || m.kind !== 'race') throw new Error(`not a race metric: ${q.metric}`);
  const rm = m as RaceMetric;

  const joins: string[] = [];
  if (rm.joins.includes('laps')) joins.push(LAPS_JOIN);
  if (rm.joins.includes('points')) joins.push(POINTS_JOIN);

  const where: string[] = ['r.season_id=?'];
  const params: unknown[] = [q.seasonId];
  if (q.cc != null) { where.push('r.cc=?'); params.push(q.cc); }
  if (rm.statuses !== 'all') { where.push(`r.status IN (${rm.statuses.map(() => '?').join(',')})`); params.push(...rm.statuses); }
  if (rm.pbOnly) where.push('r.was_pb=1');
  if (q.period.startUtc) { where.push('datetime(r.ended_at) >= ?'); params.push(q.period.startUtc); }
  if (q.period.endUtc) { where.push('datetime(r.ended_at) < ?'); params.push(q.period.endUtc); }

  for (const [dim, val] of Object.entries(q.filters) as [Dimension, string][]) {
    if (dim === q.groupBy) continue; // grouping dimension isn't also an equality filter
    const fc = filterClause(db, dim, val);
    if (fc) { where.push(fc.sql); params.push(fc.param); }
  }
  if (q.bodyConditionSql) { joins.push(q.bodyConditionSql.join); where.push(q.bodyConditionSql.where); params.push(...q.bodyConditionSql.params); }

  let rows: StatRow[];
  let total: number | null;
  if (q.groupBy) {
    const g = groupExpr(q.groupBy);
    if (g.join) joins.push(g.join);
    const sql = `SELECT ${g.select} AS key, ${rm.value} AS value
                 FROM runs r ${joins.join(' ')} WHERE ${where.join(' AND ')}
                 GROUP BY key HAVING key IS NOT NULL ORDER BY value DESC`;
    rows = db.prepare(sql).all(...params) as StatRow[];
    total = rows.reduce((s, r) => s + (r.value ?? 0), 0);
  } else {
    const sql = `SELECT ${rm.value} AS value FROM runs r ${joins.join(' ')} WHERE ${where.join(' AND ')}`;
    const row = db.prepare(sql).get(...params) as { value: number | null };
    total = row?.value ?? 0;
    rows = [{ key: q.metric, value: total }];
  }

  return {
    metric: q.metric,
    period: { key: q.period.key, tz: q.period.tz, start: q.period.startIso, end: q.period.endIso },
    filters: q.filters as Record<string, string>,
    group_by: q.groupBy,
    rows, total,
  };
}
```

- [ ] **Step 4: Run it, expect pass**

Run: `npx vitest run src/stats/resolve.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/stats/resolve.ts pi/src/stats/resolve.test.ts
git commit -m "feat(stats): race resolver (value + breakdown, status/dim/period filters)"
```

---

## Task 5: Body source + body resolver

**Files:**
- Create: `pi/src/stats/body.ts`
- Test: `pi/src/stats/body.test.ts`

porker.db is per-person tables. We open it read-only and expose a normalized union tagged with the player display_name. Aggregations are `current` (latest ≤ window end), `change` (last − first in window), `min`/`max` (in window). No dedup: the offered aggregations are duplicate-invariant.

- [ ] **Step 1: Write the failing test (builds a fixture porker DB)**

```ts
// pi/src/stats/body.test.ts
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { openPorker, resolveBody } from './body';
import { resolvePeriod } from './period';
import { DateTime } from 'luxon';

let dir: string, path: string;

function mkPorker(p: string) {
  const d = new DatabaseSync(p);
  const cols = `"Timestamp" INTEGER, "Weight" REAL, "BodyMassIndex" REAL, "BodyFat" REAL,
    "FatFreeBodyWeight" REAL, "SubcutaneousFat" REAL, "VisceralFat" REAL, "BodyWater" REAL,
    "SkeletalMuscle" REAL, "MuscleMass" REAL, "BoneMass" REAL, "Protein" REAL,
    "BasalMetabolicRate" REAL, "MetabolicAge" REAL`;
  for (const t of ['Measurements', 'EunoraMeasurements']) d.exec(`CREATE TABLE "${t}" (${cols})`);
  const ins = (t: string, ts: number, fat: number, muscle: number) => d.prepare(
    `INSERT INTO "${t}"("Timestamp","Weight","BodyMassIndex","BodyFat","FatFreeBodyWeight","SubcutaneousFat","VisceralFat","BodyWater","SkeletalMuscle","MuscleMass","BoneMass","Protein","BasalMetabolicRate","MetabolicAge")
     VALUES (?,80,22,?,60,15,5,55,50,?,3,18,1700,25)`).run(ts, fat, muscle);
  const day = (iso: string) => Math.floor(DateTime.fromISO(iso, { zone: 'utc' }).toSeconds());
  // Luke (Eunora): fat 20 → 18 over June; Paul (Measurements): muscle 52 latest
  ins('EunoraMeasurements', day('2026-06-02T00:00:00'), 20, 49);
  ins('EunoraMeasurements', day('2026-06-20T00:00:00'), 18, 50);
  ins('Measurements', day('2026-06-05T00:00:00'), 16, 52);
  d.close();
}

beforeAll(() => { dir = mkdtempSync(join(tmpdir(), 'porker-')); path = join(dir, 'porker.db'); mkPorker(path); });
afterAll(() => rmSync(dir, { recursive: true, force: true }));

const june = () => resolvePeriod('this_month', 'Australia/Melbourne',
  { now: DateTime.fromISO('2026-06-15T12:00:00', { zone: 'Australia/Melbourne' }) });

describe('resolveBody', () => {
  it('change = last − first in window, per player', () => {
    const pk = openPorker(path);
    const r = resolveBody(pk, { metric: 'body_fat', agg: 'change', period: june(), filters: { player: 'Luke' } });
    expect(r.total).toBeCloseTo(-2, 5); // 18 − 20
    pk.close();
  });

  it('current (no player) sums latest across the roster', () => {
    const pk = openPorker(path);
    const r = resolveBody(pk, { metric: 'muscle_mass', agg: 'current', period: june(), filters: {} });
    expect(r.total).toBeCloseTo(102, 5); // Luke 50 + Paul 52
    pk.close();
  });
});
```

- [ ] **Step 2: Run it, expect failure**

Run: `npx vitest run src/stats/body.test.ts`
Expected: FAIL — `openPorker`/`resolveBody` not found.

- [ ] **Step 3: Implement `body.ts`**

```ts
// pi/src/stats/body.ts
import { DatabaseSync } from 'node:sqlite';
import type { Period, StatResult, StatRow } from './types';
import type { BodyAgg } from './metrics';
import { getMetric } from './metrics';
import { toEpochSeconds } from './period';

/** porker table → kart-player display name. Blu/Cbri excluded (non-participants). */
export const PORKER_MAP: { table: string; player: string }[] = [
  { table: 'Measurements', player: 'Paul' },
  { table: 'AddymerMeasurements', player: 'Gub' },
  { table: 'AlexMeasurements', player: 'Alex' },
  { table: 'EunoraMeasurements', player: 'Luke' },
  { table: 'BraydenMeasurements', player: 'Aliias' },
];

const COLS = `"Timestamp" AS ts, "Weight" AS weight, "BodyMassIndex" AS bmi, "BodyFat" AS body_fat,
  "FatFreeBodyWeight" AS fat_free_weight, "SubcutaneousFat" AS subcutaneous_fat,
  "VisceralFat" AS visceral_fat, "BodyWater" AS body_water, "SkeletalMuscle" AS skeletal_muscle,
  "MuscleMass" AS muscle_mass, "BoneMass" AS bone_mass, "Protein" AS protein,
  "BasalMetabolicRate" AS bmr, "MetabolicAge" AS metabolic_age`;

/** Open porker.db read-only (coexists with the pork bot's writer). */
export function openPorker(path: string): DatabaseSync {
  const db = new DatabaseSync(path, { readOnly: true });
  db.exec('PRAGMA busy_timeout=2000');
  return db;
}

/** Which porker tables actually exist (a fixture/partial DB may omit some). */
function presentTables(db: DatabaseSync): { table: string; player: string }[] {
  const names = new Set((db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all() as { name: string }[]).map((r) => r.name));
  return PORKER_MAP.filter((m) => names.has(m.table));
}

export interface BodyQuery {
  metric: string; agg: BodyAgg; period: Period; filters: { player?: string };
}

/** One player's column value under an aggregation, scoped to the period window (epoch bounds). */
function valueFor(db: DatabaseSync, table: string, column: string, agg: BodyAgg, lo: number | null, hi: number | null): number | null {
  const win: string[] = []; const p: number[] = [];
  if (lo != null) { win.push('"Timestamp" >= ?'); p.push(lo); }
  if (hi != null) { win.push('"Timestamp" < ?'); p.push(hi); }
  const w = win.length ? `WHERE ${win.join(' AND ')}` : '';
  if (agg === 'min' || agg === 'max') {
    const row = db.prepare(`SELECT ${agg.toUpperCase()}("${column}") AS v FROM "${table}" ${w}`).get(...p) as { v: number | null };
    return row?.v ?? null;
  }
  if (agg === 'current') {
    // latest on-or-before the window end (hi); ignore lo so "current" is the standing value
    const hw = hi != null ? 'WHERE "Timestamp" < ?' : '';
    const hp = hi != null ? [hi] : [];
    const row = db.prepare(`SELECT "${column}" AS v FROM "${table}" ${hw} ORDER BY "Timestamp" DESC LIMIT 1`).get(...hp) as { v: number | null };
    return row?.v ?? null;
  }
  // change = last − first within the window
  const first = db.prepare(`SELECT "${column}" AS v FROM "${table}" ${w} ORDER BY "Timestamp" ASC LIMIT 1`).get(...p) as { v: number | null };
  const last = db.prepare(`SELECT "${column}" AS v FROM "${table}" ${w} ORDER BY "Timestamp" DESC LIMIT 1`).get(...p) as { v: number | null };
  return first?.v != null && last?.v != null ? last.v - first.v : null;
}

export function resolveBody(db: DatabaseSync, q: BodyQuery): StatResult {
  const m = getMetric(q.metric);
  if (!m || m.kind !== 'body') throw new Error(`not a body metric: ${q.metric}`);
  if (!m.aggs.includes(q.agg)) throw new Error(`agg ${q.agg} not allowed for ${q.metric}`);

  const lo = q.period.startUtc ? toEpochSeconds(q.period.startUtc) : null;
  const hi = q.period.endUtc ? toEpochSeconds(q.period.endUtc) : null;

  const tables = presentTables(db).filter((t) => !q.filters.player || t.player.toLowerCase() === q.filters.player.toLowerCase());
  const rows: StatRow[] = [];
  for (const t of tables) {
    const v = valueFor(db, t.table, m.column, q.agg, lo, hi);
    if (v != null) rows.push({ key: t.player, value: v });
  }
  rows.sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  const total = rows.length ? rows.reduce((s, r) => s + (r.value ?? 0), 0) : null;

  return {
    metric: q.metric,
    period: { key: q.period.key, tz: q.period.tz, start: q.period.startIso, end: q.period.endIso },
    filters: q.filters as Record<string, string>,
    rows, total,
  };
}
```

- [ ] **Step 4: Run it, expect pass**

Run: `npx vitest run src/stats/body.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/stats/body.ts pi/src/stats/body.test.ts
git commit -m "feat(stats): read-only porker body source + resolver (current/change/min/max)"
```

---

## Task 6: Cross-domain alignment + body-condition filter

**Files:**
- Create: `pi/src/stats/align.ts`
- Test: `pi/src/stats/align.test.ts`
- Modify: `pi/src/stats/resolve.ts` (already accepts `bodyConditionSql`)

`body_condition=bmi<22` keeps only runs where the player's most-recent weigh-in **on-or-before** the run's `ended_at` satisfies the predicate. We attach porker read-only via `ATTACH ... AS porker` and build a correlated subquery per porker table (UNION) that finds the latest measurement ≤ the run time.

- [ ] **Step 1: Write the failing test**

```ts
// pi/src/stats/align.test.ts
import { describe, it, expect } from 'vitest';
import { parseBodyCondition } from './align';

describe('parseBodyCondition', () => {
  it('parses column/op/number', () => {
    expect(parseBodyCondition('bmi<22')).toEqual({ column: 'bmi', op: '<', value: 22 });
    expect(parseBodyCondition('body_fat>=20.5')).toEqual({ column: 'body_fat', op: '>=', value: 20.5 });
  });
  it('rejects unknown columns and bad syntax', () => {
    expect(() => parseBodyCondition('course<3')).toThrow(/unknown/);
    expect(() => parseBodyCondition('bmi!!22')).toThrow(/invalid/);
  });
});
```

(Full DB-attached behaviour is covered by the route integration test in Task 7; this task unit-tests the parser and exposes the SQL builder.)

- [ ] **Step 2: Run it, expect failure**

Run: `npx vitest run src/stats/align.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `align.ts`**

```ts
// pi/src/stats/align.ts
import { PORKER_MAP } from './body';

const BODY_FILTER_COLUMNS: Record<string, string> = {
  weight: 'Weight', bmi: 'BodyMassIndex', body_fat: 'BodyFat', fat_free_weight: 'FatFreeBodyWeight',
  subcutaneous_fat: 'SubcutaneousFat', visceral_fat: 'VisceralFat', body_water: 'BodyWater',
  skeletal_muscle: 'SkeletalMuscle', muscle_mass: 'MuscleMass', bone_mass: 'BoneMass',
  protein: 'Protein', bmr: 'BasalMetabolicRate', metabolic_age: 'MetabolicAge',
};
const OPS = new Set(['<', '<=', '>', '>=', '=']);

export interface BodyCondition { column: string; op: string; value: number; }

export function parseBodyCondition(raw: string): BodyCondition {
  const m = /^([a-z_]+)\s*(<=|>=|<|>|=)\s*(-?\d+(\.\d+)?)$/.exec(raw.trim());
  if (!m) throw new Error(`invalid body_condition: ${raw}`);
  const [, column, op, num] = m;
  if (!(column in BODY_FILTER_COLUMNS)) throw new Error(`unknown body column: ${column}`);
  if (!OPS.has(op)) throw new Error(`invalid op: ${op}`);
  return { column, op, value: Number(num) };
}

/** Build a WHERE fragment keeping runs whose player's latest weigh-in (≤ ended_at) matches.
 *  Assumes porker is ATTACHed AS porker. Player identity bridges via display_name. */
export function bodyConditionSql(cond: BodyCondition): { join: string; where: string; params: unknown[] } {
  const col = BODY_FILTER_COLUMNS[cond.column];
  // Per porker table: latest row with Timestamp ≤ strftime('%s', r.ended_at). UNION across players,
  // joined to the run's player via display_name → table.
  const unions = PORKER_MAP.map((m) => `
    SELECT '${m.player}' AS player, "${col}" AS v, "Timestamp" AS ts
    FROM porker."${m.table}"`).join(' UNION ALL ');
  // Correlated: the latest measurement for THIS run's player at/just before the run time.
  const where = `(
    SELECT b.v FROM ( ${unions} ) b
    JOIN players pp ON pp.display_name = b.player COLLATE NOCASE
    WHERE pp.id = r.player_id AND b.ts <= CAST(strftime('%s', datetime(r.ended_at)) AS INTEGER)
    ORDER BY b.ts DESC LIMIT 1
  ) ${cond.op} ?`;
  return { join: '', where, params: [cond.value] };
}
```

- [ ] **Step 4: Run it, expect pass**

Run: `npx vitest run src/stats/align.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/stats/align.ts pi/src/stats/align.test.ts
git commit -m "feat(stats): body-condition parser + as-of-run alignment SQL"
```

---

## Task 7: API routes + series + wiring

**Files:**
- Create: `pi/src/api/stats.ts`
- Modify: `pi/src/api/app.ts:9-15`
- Test: `pi/src/api/stats.test.ts`

Routes resolve query params, validate against the registry, attach porker for body/condition queries, and shape the JSON. `series` loops sub-buckets via `resolvePeriod('range', ...)`. The porker path comes from `STATS_PORKER_DB` (default `porker.db` relative to cwd).

- [ ] **Step 1: Write the failing test**

```ts
// pi/src/api/stats.test.ts
import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { createStatsApp } from './stats';

function db(): DatabaseSync {
  const d = new DatabaseSync(':memory:');
  applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1);
          INSERT INTO players(id,display_name) VALUES (1,'Luke');
          INSERT INTO courses(id,slug,display_name) VALUES (1,'bc','Bowsers Castle');
          INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,ended_at,total_time_ms)
          VALUES (1,1,1,1,150,'reset','live','2026-06-10T03:00:00+00:00',NULL),
                 (2,1,1,1,150,'finished','live','2026-06-10T04:00:00+00:00',160000);`);
  return d;
}

describe('stats routes', () => {
  it('GET /v1/stats/value resets this_week = 1', async () => {
    const app = createStatsApp(db(), { porkerPath: null });
    const res = await app.request('/v1/stats/value?metric=resets&period=this_week&tz=Australia/Melbourne');
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.value).toBe(1);
    expect(body.period.tz).toBe('Australia/Melbourne');
  });

  it('rejects body_fat grouped by course', async () => {
    const app = createStatsApp(db(), { porkerPath: null });
    const res = await app.request('/v1/stats/breakdown?metric=body_fat&group_by=course');
    expect(res.status).toBe(400);
  });

  it('GET /v1/stats/metrics lists the registry', async () => {
    const app = createStatsApp(db(), { porkerPath: null });
    const res = await app.request('/v1/stats/metrics');
    const body = await res.json();
    expect(body.map((m: any) => m.id)).toContain('coins');
  });

  it('body metric without porker → 503', async () => {
    const app = createStatsApp(db(), { porkerPath: null });
    const res = await app.request('/v1/stats/value?metric=body_fat&agg=current');
    expect(res.status).toBe(503);
  });
});
```

- [ ] **Step 2: Run it, expect failure**

Run: `npx vitest run src/api/stats.test.ts`
Expected: FAIL — `createStatsApp` not found.

- [ ] **Step 3: Implement `stats.ts`**

```ts
// pi/src/api/stats.ts
import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import { existsSync } from 'node:fs';
import { resolvePeriod } from '../stats/period';
import type { PeriodKey, Dimension } from '../stats/types';
import { getMetric, listMetrics, allowsDimension, type BodyAgg } from '../stats/metrics';
import { resolveRace } from '../stats/resolve';
import { resolveBody } from '../stats/body';
import { parseBodyCondition, bodyConditionSql } from '../stats/align';
import { activeSeasonId } from '../db/seasons';

const DIMS: Dimension[] = ['player', 'course', 'character', 'kart', 'costume', 'cc'];
const PERIODS: PeriodKey[] = ['today', 'this_week', 'this_month', 'all_time', 'range'];

export interface StatsDeps { porkerPath: string | null; }

export function createStatsApp(db: DatabaseSync, deps: StatsDeps): Hono {
  const app = new Hono();

  const collectFilters = (c: any): Partial<Record<Dimension, string>> => {
    const f: Partial<Record<Dimension, string>> = {};
    for (const d of DIMS) { const v = c.req.query(d); if (v != null) f[d] = v; }
    return f;
  };

  const period = (c: any) => {
    const key = (c.req.query('period') ?? 'all_time') as PeriodKey;
    if (!PERIODS.includes(key)) throw { code: 400, msg: `bad period: ${key}` };
    const tz = c.req.query('tz') ?? 'Australia/Melbourne';
    return resolvePeriod(key, tz, { from: c.req.query('from'), to: c.req.query('to') });
  };

  // Attach porker for the duration of a body / body_condition query, then detach.
  const withPorker = <T>(fn: () => T): T => {
    if (!deps.porkerPath || !existsSync(deps.porkerPath)) throw { code: 503, msg: 'porker.db unavailable' };
    db.exec(`ATTACH DATABASE '${deps.porkerPath.replace(/'/g, "''")}' AS porker`);
    try { return fn(); } finally { try { db.exec('DETACH DATABASE porker'); } catch { /* ignore */ } }
  };

  const handleRace = (c: any, groupBy?: Dimension) => {
    const metric = c.req.query('metric');
    const filters = collectFilters(c);
    const seasonId = c.req.query('season') ? Number(c.req.query('season')) : activeSeasonId(db);
    const cc = c.req.query('cc') ? Number(c.req.query('cc')) : undefined;
    const bcRaw = c.req.query('body_condition');
    const run = () => {
      let bodyConditionSqlFrag;
      if (bcRaw) bodyConditionSqlFrag = bodyConditionSql(parseBodyCondition(bcRaw));
      return resolveRace(db, { metric, period: period(c), filters, groupBy, seasonId, cc, bodyConditionSql: bodyConditionSqlFrag });
    };
    return bcRaw ? withPorker(run) : run();
  };

  const handleBody = (c: any) => {
    const metric = c.req.query('metric');
    const agg = (c.req.query('agg') ?? (getMetric(metric) as any).defaultAgg) as BodyAgg;
    const filters = { player: c.req.query('player') ?? undefined };
    return withPorker(() => resolveBody(db, { metric, agg, period: period(c), filters }));
  };

  const guard = (c: any, groupBy?: Dimension) => {
    const id = c.req.query('metric');
    const m = getMetric(id);
    if (!m) throw { code: 400, msg: `unknown metric: ${id}` };
    if (groupBy) {
      if (!DIMS.includes(groupBy)) throw { code: 400, msg: `bad group_by: ${groupBy}` };
      if (!allowsDimension(id, groupBy)) throw { code: 400, msg: `${id} cannot group by ${groupBy}` };
    }
    for (const d of DIMS) if (c.req.query(d) != null && !allowsDimension(id, d)) throw { code: 400, msg: `${id} cannot filter by ${d}` };
    return m;
  };

  const dispatch = (c: any, groupBy?: Dimension) => {
    const m = guard(c, groupBy);
    return m.kind === 'body' ? handleBody(c) : handleRace(c, groupBy);
  };

  const wrap = (c: any, fn: () => any) => {
    try { return c.json(fn()); }
    catch (e: any) { return c.json({ error: e?.msg ?? String(e?.message ?? e) }, e?.code ?? 400); }
  };

  app.get('/v1/stats/value', (c) => wrap(c, () => dispatch(c)));
  app.get('/v1/stats/breakdown', (c) => wrap(c, () => {
    const gb = c.req.query('group_by') as Dimension | undefined;
    if (!gb) throw { code: 400, msg: 'breakdown requires group_by' };
    return dispatch(c, gb);
  }));
  app.get('/v1/stats/series', (c) => wrap(c, () => {
    const bucket = (c.req.query('bucket') ?? 'day') as 'day' | 'week' | 'month';
    const p = period(c);
    if (!p.startUtc || !p.endUtc) throw { code: 400, msg: 'series requires a bounded period' };
    const tz = c.req.query('tz') ?? 'Australia/Melbourne';
    const buckets = subBuckets(p.startIso!, p.endIso!, bucket, tz).map(([from, to]) => {
      const sub = new URLSearchParams(c.req.query());
      sub.set('period', 'range'); sub.set('from', from); sub.set('to', to);
      const fakeC = { req: { query: (k?: string) => (k ? sub.get(k) ?? undefined : Object.fromEntries(sub)) } };
      const r = guard(fakeC).kind === 'body' ? handleBody(fakeC) : handleRace(fakeC);
      return { start: from, end: to, value: r.total };
    });
    return { metric: c.req.query('metric'), bucket, buckets };
  }));
  app.get('/v1/stats/metrics', (c) => c.json(listMetrics().map((m) => ({
    id: m.id, kind: m.kind,
    dimensions: m.kind === 'race' ? DIMS : ['player'],
    aggs: m.kind === 'body' ? m.aggs : undefined,
  }))));

  return app;
}

import { DateTime } from 'luxon';
/** Split [from,to) ISO bounds into per-bucket [from,to) ISO ranges in tz. */
function subBuckets(fromIso: string, toIso: string, bucket: 'day' | 'week' | 'month', tz: string): [string, string][] {
  const end = DateTime.fromISO(toIso, { zone: tz });
  let cur = DateTime.fromISO(fromIso, { zone: tz });
  const out: [string, string][] = [];
  while (cur < end) {
    const next = cur.plus({ [bucket === 'day' ? 'days' : bucket === 'week' ? 'weeks' : 'months']: 1 });
    out.push([cur.toISO()!, (next < end ? next : end).toISO()!]);
    cur = next;
  }
  return out;
}
```

- [ ] **Step 4: Run it, expect pass**

Run: `npx vitest run src/api/stats.test.ts`
Expected: PASS.

- [ ] **Step 5: Mount in `app.ts`**

In `pi/src/api/app.ts`, add the import and route inside `createApp`:

```ts
import { createStatsApp } from './stats';
```
```ts
  app.route('/', createStatsApp(db, { porkerPath: process.env.STATS_PORKER_DB ?? 'porker.db' }));
```

- [ ] **Step 6: Full suite**

Run: `npm test`
Expected: PASS (all pi tests, including pre-existing).

- [ ] **Step 7: Commit**

```bash
git add pi/src/api/stats.ts pi/src/api/stats.test.ts pi/src/api/app.ts
git commit -m "feat(stats): /v1/stats value|breakdown|series|metrics routes + porker wiring"
```

---

## Task 8: End-to-end body-condition integration test

**Files:**
- Test: `pi/src/api/stats.test.ts` (append)

Proves the cross-domain filter works against a real attached porker fixture (the one thing only an integration test covers).

- [ ] **Step 1: Append the test**

```ts
// pi/src/api/stats.test.ts (append)
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DateTime } from 'luxon';

it('body_condition keeps only runs under the BMI at run time', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pk-'));
  const pkPath = join(dir, 'porker.db');
  const pk = new DatabaseSync(pkPath);
  pk.exec(`CREATE TABLE "EunoraMeasurements" ("Timestamp" INTEGER,"Weight" REAL,"BodyMassIndex" REAL,
    "BodyFat" REAL,"FatFreeBodyWeight" REAL,"SubcutaneousFat" REAL,"VisceralFat" REAL,"BodyWater" REAL,
    "SkeletalMuscle" REAL,"MuscleMass" REAL,"BoneMass" REAL,"Protein" REAL,"BasalMetabolicRate" REAL,"MetabolicAge" REAL)`);
  const ts = Math.floor(DateTime.fromISO('2026-06-09T00:00:00', { zone: 'utc' }).toSeconds());
  pk.prepare(`INSERT INTO "EunoraMeasurements" VALUES (?,80,21,18,60,15,5,55,50,50,3,18,1700,25)`).run(ts);
  pk.close();

  const d = db();                       // Luke has runs 1 (reset) + 2 (finished) on 2026-06-10
  d.prepare('UPDATE players SET display_name=? WHERE id=1').run('Luke');
  const app = createStatsApp(d, { porkerPath: pkPath });
  const res = await app.request('/v1/stats/value?metric=finishes&period=this_week&tz=Australia/Melbourne&body_condition=bmi<22');
  const body = await res.json();
  expect(body.value).toBe(1);           // BMI 21 < 22 → the finished run qualifies

  const res2 = await app.request('/v1/stats/value?metric=finishes&period=this_week&tz=Australia/Melbourne&body_condition=bmi<20');
  expect((await res2.json()).value).toBe(0);
  rmSync(dir, { recursive: true, force: true });
});
```

- [ ] **Step 2: Run it, expect pass**

Run: `npx vitest run src/api/stats.test.ts`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add pi/src/api/stats.test.ts
git commit -m "test(stats): e2e body-condition filter over attached porker"
```

---

## Self-Review

**Spec coverage:**
- Composable engine (registry + resolver) → Tasks 3, 4. ✓
- Two domains race + body → Tasks 4, 5. ✓
- tz periods (Melbourne default, Monday, UTC bounds, datetime()) → Task 1 + resolver window. ✓
- Reset handling per-metric → Task 3 statuses + Task 4 test. ✓
- driving_time = trail max(t_ms) → Task 3 (`SUM(pt.maxt)`, LEFT JOIN). ✓
- was_pb stored/maintained/backfilled → Task 2. ✓
- body-condition + alignment (most-recent on-or-before) → Tasks 6, 8. ✓
- API value/breakdown/series/metrics + validation + 503 → Task 7. ✓
- Identity map, porker read-only → Task 5. ✓
- `time_improvement` → **deferred to increment #2** (documented above). ✓ (intentional gap)

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `StatResult`/`StatRow`/`Period`/`Dimension` defined in Task 1 and used unchanged in 4/5/7. `getMetric`/`allowsDimension`/`RaceMetric`/`BodyAgg` from Task 3 used in 4/5/7. `resolveRace`'s `bodyConditionSql` shape `{join,where,params}` defined in Task 4 and produced by Task 6. `openPorker`/`PORKER_MAP` from Task 5 used in 6/7.

**Risk note for the executor:** `node:sqlite` `ATTACH` with a JS-interpolated path is used in Task 7 (`withPorker`) — the path is single-quote-escaped, and it comes from server config (`STATS_PORKER_DB`), not user input. If `node:sqlite` rejects `ATTACH` while in WAL with an external writer, fall back to a second read-only `DatabaseSync` and evaluate the body-condition in app code (per spec §5.3). Verify `new DatabaseSync(path, { readOnly: true })` is supported by the installed Node; if not, open with the URI form `file:...?mode=ro`.

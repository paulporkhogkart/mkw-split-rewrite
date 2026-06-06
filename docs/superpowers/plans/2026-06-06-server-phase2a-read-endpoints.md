# Phase 2a — Server read endpoints (PB splits + trails) Implementation Plan

> Executed inline with TDD. Steps use `- [ ]` for tracking. Spec: `docs/superpowers/specs/2026-06-06-server-phase2-read-migration-design.md`.

**Goal:** Add two server reads the monitor needs — the caller's PB lap splits, and every roster player's PB trail — over already-stored `run_laps` / `run_points`.

**Tech Stack:** TypeScript, Hono, node:sqlite, vitest. Run tests from `pi/`: `npm test`.

---

### Task 1: `myPbSplits` query

**Files:** Modify `pi/src/db/reads.ts`; test `pi/src/db/reads.test.ts`.

- [ ] **Step 1: failing test** (append to `reads.test.ts`):
```ts
import { myPbSplits, courseTrails } from './reads';

describe('myPbSplits', () => {
  it('returns the caller PB total + per-lap cumulative splits', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
    db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (10,1,1,1,150,'finished','live',108000,1)");
    db.exec("INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES (10,1,36000),(10,2,72000),(10,3,108000)");
    expect(myPbSplits(db, 1, 1, 1, 150)).toEqual({ total_ms: 108000, splits: { 1: 36000, 2: 72000, 3: 108000 } });
  });
  it('returns empty splits when there is no live PB (or a legacy total-only PB)', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
    db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (11,1,1,1,150,'finished','legacy',95000,1)");
    expect(myPbSplits(db, 1, 1, 1, 150)).toEqual({ total_ms: 95000, splits: {} });
    expect(myPbSplits(db, 1, 1, 999, 150)).toEqual({ total_ms: null, splits: {} });
  });
});
```

- [ ] **Step 2: run `npm test` → FAIL** (no `myPbSplits`/`courseTrails` export).

- [ ] **Step 3: implement** (append to `reads.ts`):
```ts
export function myPbSplits(db: DatabaseSync, seasonId: number, playerId: number, courseId: number, cc: number):
    { total_ms: number | null; splits: Record<number, number> } {
  const run = db.prepare(
    `SELECT id, total_time_ms FROM runs
     WHERE season_id=? AND player_id=? AND course_id=? AND cc=? AND is_pb=1`
  ).get(seasonId, playerId, courseId, cc) as { id: number; total_time_ms: number | null } | undefined;
  if (!run) return { total_ms: null, splits: {} };
  const laps = db.prepare(
    `SELECT lap_index, lap_time_ms FROM run_laps WHERE run_id=? ORDER BY lap_index`
  ).all(run.id) as { lap_index: number; lap_time_ms: number | null }[];
  const splits: Record<number, number> = {};
  for (const l of laps) if (l.lap_time_ms != null) splits[l.lap_index] = l.lap_time_ms;
  return { total_ms: run.total_time_ms ?? null, splits };
}
```

- [ ] **Step 4: run `npm test` → myPbSplits tests PASS** (courseTrails tests still fail; fixed in Task 2).

### Task 2: `courseTrails` query

**Files:** Modify `pi/src/db/reads.ts`; test `pi/src/db/reads.test.ts`.

- [ ] **Step 1: failing test** (append to `reads.test.ts`):
```ts
describe('courseTrails', () => {
  function seededTrails() {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke'),(3,'Alex')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
    // Paul: live PB with points; Luke: live PB with points; Alex: legacy PB, no points.
    db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (10,1,1,1,150,'finished','live',108000,1),(20,1,2,1,150,'finished','live',112000,1),(30,1,3,1,150,'finished','legacy',95000,1)");
    db.exec("INSERT INTO run_points(run_id,t_ms,cx,cy,score) VALUES (10,0,100,200,0.9),(10,16,101,201,0.95)");
    db.exec("INSERT INTO run_points(run_id,t_ms,cx,cy,score) VALUES (20,0,300,400,0.8)");
    return db;
  }
  it('returns roster PB trails with points, omitting legacy (point-less) PBs', () => {
    const t = courseTrails(seededTrails(), 1, 1, 150, null);
    expect(t.map(x => x.player)).toEqual(['Paul', 'Luke']);   // Alex (no points) omitted
    expect(t[0]).toEqual({ player_id: 1, player: 'Paul', total_ms: 108000, is_me: false,
      points: [[0,100,200,0.9],[16,101,201,0.95]] });
  });
  it('flags is_me for the matching player', () => {
    const t = courseTrails(seededTrails(), 1, 1, 150, 1);
    expect(t.find(x => x.player_id === 1)?.is_me).toBe(true);
    expect(t.find(x => x.player_id === 2)?.is_me).toBe(false);
  });
});
```

- [ ] **Step 2: run `npm test` → courseTrails tests FAIL.**

- [ ] **Step 3: implement** (append to `reads.ts`):
```ts
export type Trail = { player_id: number; player: string; total_ms: number | null; is_me: boolean; points: number[][] };

export function courseTrails(db: DatabaseSync, seasonId: number, courseId: number, cc: number, meId: number | null): Trail[] {
  const runs = db.prepare(
    `SELECT r.id, r.player_id, p.display_name, r.total_time_ms
     FROM runs r JOIN players p ON p.id = r.player_id
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.is_pb=1
     ORDER BY r.total_time_ms ASC`
  ).all(seasonId, courseId, cc) as { id: number; player_id: number; display_name: string; total_time_ms: number | null }[];
  const out: Trail[] = [];
  const ptStmt = db.prepare(`SELECT t_ms, cx, cy, score FROM run_points WHERE run_id=? ORDER BY t_ms`);
  for (const r of runs) {
    const pts = ptStmt.all(r.id) as { t_ms: number; cx: number; cy: number; score: number }[];
    if (pts.length === 0) continue;   // legacy / point-less PB: no trail
    out.push({
      player_id: r.player_id, player: r.display_name, total_ms: r.total_time_ms ?? null,
      is_me: meId != null && r.player_id === meId,
      points: pts.map((p) => [p.t_ms, p.cx, p.cy, p.score]),
    });
  }
  return out;
}
```

- [ ] **Step 4: run `npm test` → all `reads.test.ts` PASS.**

### Task 3: routes `/v1/me/pb-splits` + `/v1/trails`

**Files:** Modify `pi/src/api/reads.ts`; test `pi/src/api/reads.test.ts`.

- [ ] **Step 1: failing test** — read `pi/src/api/reads.test.ts` first to match its app/seed setup, then add tests:
  - `GET /v1/me/pb-splits?course=rr` with a valid Bearer token → 200, body `{ total_ms, splits }`; no token → 401; unknown course → 400.
  - `GET /v1/trails?course=rr` (no token) → 200 array, each trail `is_me:false`; with the owner's token → that player's trail `is_me:true`; unknown course → 400.
  (Mirror the existing route-test seed: players + course + a live PB run with `run_laps` and `run_points`, and a minted token via `mintToken`.)

- [ ] **Step 2: run `npm test` → new route tests FAIL.**

- [ ] **Step 3: implement** in `pi/src/api/reads.ts`:
  - Add imports: `myPbSplits, courseTrails` (from `../db/reads`), `playerByToken` (from `../db/players`).
  - Inside `readsRoutes`, add:
```ts
  r.get('/v1/me/pb-splits', requireToken(db), (c) => {
    const cid = course(c); if (cid === null) return c.json({ error: 'unknown course' }, 400);
    return c.json(myPbSplits(db, season(c), c.get('playerId'), cid, num(c.req.query('cc'), 150)));
  });
  r.get('/v1/trails', (c) => {
    const cid = course(c); if (cid === null) return c.json({ error: 'unknown course' }, 400);
    const auth = c.req.header('authorization') ?? '';
    const m = /^Bearer (.+)$/.exec(auth);
    const me = m ? playerByToken(db, m[1]) : null;
    return c.json(courseTrails(db, season(c), cid, num(c.req.query('cc'), 150), me ? me.id : null));
  });
```

- [ ] **Step 4: run `npm test` → all PASS.**

### Task 4: commit + finish

- [ ] `npm test` green; commit `pi/src/db/reads.ts`, `pi/src/db/reads.test.ts`, `pi/src/api/reads.ts`, `pi/src/api/reads.test.ts` with `feat(server): /v1/me/pb-splits + /v1/trails read endpoints`.
- [ ] ff-merge `server-phase2a` → `main`.

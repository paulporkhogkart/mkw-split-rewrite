# Players Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-player section to thekartoff.com — a roster index, a profile (card + headline standings + full PB table), and a GOLF/TURF/TIME strategy panel that ranks a player's cheapest opportunities to climb each leaderboard.

**Architecture:** The Pi gains one token-free endpoint `GET /v1/players/:slug` that assembles a player's headline standings (with rank in each of 4 metrics) and a rich per-course PB row list, all from existing DB reads. The web computes the three strategy sort orders **client-side** from those PB rows, reusing the existing on-fire difficulty kernel (`fireBarPct`) so the curve lives in exactly one place. A single `PLAYERS` nav item routes `/players` (roster grid) and `/players/:slug` (profile) via the existing History-API router.

**Tech Stack:** Pi — Node/TS run via `tsx`, Hono, `node:sqlite` (`DatabaseSync`), vitest. Web — Svelte + Vite, vitest, History-API routing.

## Global Constraints

- **No slug column on `players`** — the display name is `UNIQUE`; derive a player's slug with `slugify(display_name)` (Pi: `pi/src/db/slug.ts`; web: new mirror `web/src/lib/playerSlug.js`).
- **Fire kernel constants are locked:** `E0 = 0.2`, `K = 4`, `fireBarPct(off) = E0 * Math.exp(off / K)` — import from `web/src/lib/fireModel.js`; never redefine.
- **cc defaults to `150`** everywhere (`?cc=` query, `num(c.req.query('cc'), 150)`); season defaults to `activeSeasonId(db)`.
- **The token gate matches `c.req.path` exactly** (`OPEN.has(c.req.path)`). A dynamic slug path is not a fixed string, so `/v1/players/:slug` needs an explicit regex exception; the two-segment `/v1/players/:id/pbs` and `/v1/players/:id/trails` stay token-gated.
- **Web dev server is `http://127.0.0.1:1430`** (strictPort) — NOT `localhost` (IPv6 stall).
- **Web imports from `../../src`** are allowed (`vite.config.js` `server.fs.allow:['..']`); `playerKey`/`playerFigures` live there.
- **`playerKey(name)` is first-name-only** (can collide across players) — use it only for figure/GIF asset keys, never for routing. Routing uses the full-name `playerSlug`.
- **Pi: `npm run typecheck` must stay clean** (source AND tests); `npm test` is vitest.
- **Visual design is deferred** — components get functional, minimal-CSS markup now; a later frontend-design pass polishes them. Do not block on aesthetics.

---

### Task 1: Pi — per-course PB rows

**Files:**
- Create: `pi/src/db/playerSummary.ts`
- Test: `pi/src/db/playerSummary.test.ts`

**Interfaces:**
- Consumes: `courseLeaderboard`, `currentWr` from `pi/src/db/reads.ts`.
- Produces: `interface PbRow`, `function playerPbRows(db, seasonId, cc, playerId): PbRow[]`.

```ts
export interface PbRow {
  slug: string; course: string; cc: number;
  your_ms: number; your_rank: number; field_size: number;
  wr_ms: number | null; off_wr_pct: number | null;
  next_rank_ms: number | null; gap_to_next_ms: number | null;
  leader_ms: number; leader_off_wr_pct: number | null; leads: boolean;
}
```

- [ ] **Step 1: Write the failing test**

```ts
// pi/src/db/playerSummary.test.ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { playerPbRows } from './playerSummary';

function seeded() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke'),(3,'Max')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2),(1,3)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road'),(2,'mc','Mario Circuit')");
  // Rainbow Road: Luke 108000 (#1), Paul 110000 (#2), Max 111000 (#3)
  // Mario Circuit: Paul 90000 (#1), Max 92000 (#2)
  db.exec(`INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES
    (1,2,1,150,'finished','live',108000,1),
    (1,1,1,150,'finished','live',110000,1),
    (1,3,1,150,'finished','live',111000,1),
    (1,1,2,150,'finished','live',90000,1),
    (1,3,2,150,'finished','live',92000,1)`);
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,is_current) VALUES (1,150,'WR',100000,1),(2,150,'WR',80000,1)");
  return db;
}

describe('playerPbRows', () => {
  it('builds a row per course with rank, gap-to-next, WR gap, and leader softness', () => {
    const rows = playerPbRows(seeded(), 1, 150, 1); // Paul
    const rr = rows.find(r => r.slug === 'rr')!;
    expect(rr.your_rank).toBe(2);
    expect(rr.field_size).toBe(3);
    expect(rr.leads).toBe(false);
    expect(rr.next_rank_ms).toBe(108000);         // Luke directly above
    expect(rr.gap_to_next_ms).toBe(2000);         // 110000 - 108000
    expect(rr.wr_ms).toBe(100000);
    expect(rr.off_wr_pct).toBeCloseTo(10, 6);     // (110000-100000)/100000
    expect(rr.leader_ms).toBe(108000);
    expect(rr.leader_off_wr_pct).toBeCloseTo(8, 6);

    const mc = rows.find(r => r.slug === 'mc')!;
    expect(mc.your_rank).toBe(1);
    expect(mc.leads).toBe(true);
    expect(mc.next_rank_ms).toBeNull();
    expect(mc.gap_to_next_ms).toBeNull();
    expect(mc.leader_ms).toBe(90000);
  });

  it('sets WR fields null when the course has no current WR', () => {
    const db = seeded();
    db.exec("UPDATE world_records SET is_current=0 WHERE course_id=1");
    const rr = playerPbRows(db, 1, 150, 1).find(r => r.slug === 'rr')!;
    expect(rr.wr_ms).toBeNull();
    expect(rr.off_wr_pct).toBeNull();
    expect(rr.leader_off_wr_pct).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix pi test -- playerSummary`
Expected: FAIL — `playerPbRows` is not exported / module not found.

- [ ] **Step 3: Write minimal implementation**

```ts
// pi/src/db/playerSummary.ts
import type { DatabaseSync } from 'node:sqlite';
import { courseLeaderboard, currentWr } from './reads';

export interface PbRow {
  slug: string; course: string; cc: number;
  your_ms: number; your_rank: number; field_size: number;
  wr_ms: number | null; off_wr_pct: number | null;
  next_rank_ms: number | null; gap_to_next_ms: number | null;
  leader_ms: number; leader_off_wr_pct: number | null; leads: boolean;
}

/** One rich row per course the player has a PB on: their rank in the field, the PB directly
 *  above them (for GOLF), the course leader (for TURF), and WR gaps (for TIME + kernel). */
export function playerPbRows(db: DatabaseSync, seasonId: number, cc: number, playerId: number): PbRow[] {
  const mine = db.prepare(
    `SELECT r.course_id, c.slug, c.display_name, r.total_time_ms
     FROM runs r JOIN courses c ON c.id = r.course_id
     WHERE r.season_id=? AND r.player_id=? AND r.cc=? AND r.is_pb=1 AND r.total_time_ms IS NOT NULL
     ORDER BY c.display_name`
  ).all(seasonId, playerId, cc) as { course_id: number; slug: string; display_name: string; total_time_ms: number }[];

  const rows: PbRow[] = [];
  for (const m of mine) {
    const lb = courseLeaderboard(db, seasonId, m.course_id, cc);
    const meIdx = lb.findIndex((r) => r.player_id === playerId);
    if (meIdx < 0) continue;
    const your_rank = meIdx + 1;
    const leads = your_rank === 1;
    const wr = currentWr(db, m.course_id, cc);
    const wr_ms: number | null = wr ? wr.record_ms : null;
    const off = (ms: number): number | null => (wr_ms != null ? ((ms - wr_ms) / wr_ms) * 100 : null);
    const next_rank_ms = leads ? null : lb[meIdx - 1].total_time_ms;
    const leader_ms = lb[0].total_time_ms;
    rows.push({
      slug: m.slug, course: m.display_name, cc,
      your_ms: m.total_time_ms, your_rank, field_size: lb.length,
      wr_ms, off_wr_pct: off(m.total_time_ms),
      next_rank_ms,
      gap_to_next_ms: next_rank_ms != null ? m.total_time_ms - next_rank_ms : null,
      leader_ms, leader_off_wr_pct: off(leader_ms), leads,
    });
  }
  return rows;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix pi test -- playerSummary`
Expected: PASS (both `playerPbRows` cases).

- [ ] **Step 5: Commit**

```bash
git add pi/src/db/playerSummary.ts pi/src/db/playerSummary.test.ts
git commit -m "feat(pi): playerPbRows — per-course PB rows for the players page"
```

---

### Task 2: Pi — headline standings with ranks

**Files:**
- Modify: `pi/src/db/playerSummary.ts` (add `avgOffWrByPlayer`, `playerHeadline`)
- Test: `pi/src/db/playerSummary.test.ts` (add a describe block)

**Interfaces:**
- Consumes: `overallStandings` from `pi/src/db/leaderboards.ts`; `territoryOwners` from `pi/src/db/reads.ts`.
- Produces: `interface Headline`, `function avgOffWrByPlayer(db, seasonId, cc): Map<number, number>`, `function playerHeadline(db, seasonId, cc, playerId): Headline`.

```ts
export interface Headline {
  turf:  { pct: number; rank: number };
  time:  { total_ms: number; rank: number };
  golf:  { points: number; rank: number };
  offwr: { avg_pct: number | null; rank: number | null };
}
```

- [ ] **Step 1: Write the failing test**

```ts
// append to pi/src/db/playerSummary.test.ts
import { avgOffWrByPlayer, playerHeadline } from './playerSummary';

describe('playerHeadline', () => {
  it('ranks the player in turf %, total time, golf, and % off WR', () => {
    // Reuse the seeded() fixture from the playerPbRows block above.
    // Standings (150cc): Paul total=200000 (rr 110000 + mc 90000, ranks 2+1=3 golf),
    //   Luke total=108000 (rr only, rank 1 => 1 golf), Max total=203000 (rr 111000 + mc 92000, ranks 3+2=5 golf).
    const h = playerHeadline(seeded(), 1, 150, 1); // Paul
    expect(h.time.total_ms).toBe(200000);
    expect(h.time.rank).toBe(2);      // Luke 108000 < Paul 200000 < Max 203000
    expect(h.golf.points).toBe(3);
    expect(h.golf.rank).toBe(2);      // Luke 1 < Paul 3 < Max 5
    // Turf: Paul owns mc (1), Luke owns rr (1), Max owns 0. Tie on owned=1 broken by total_ms asc:
    //   Luke(108000) ahead of Paul(200000). Paul rank 2.
    expect(h.turf.rank).toBe(2);
    expect(h.turf.pct).toBe(50);      // owns 1 of 2 courses
    // % off WR: Paul avg of rr(10%) + mc((90000-80000)/80000=12.5%) = 11.25%.
    expect(h.offwr.avg_pct).toBeCloseTo(11.25, 4);
    expect(typeof h.offwr.rank).toBe('number');
  });

  it('avgOffWrByPlayer averages only WR-covered PBs', () => {
    const m = avgOffWrByPlayer(seeded(), 1, 150);
    expect(m.get(1)).toBeCloseTo(11.25, 4);   // Paul
    expect(m.get(2)).toBeCloseTo(8, 6);        // Luke: rr only, (108000-100000)/100000
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix pi test -- playerSummary`
Expected: FAIL — `avgOffWrByPlayer` / `playerHeadline` not exported.

- [ ] **Step 3: Write minimal implementation**

```ts
// add to pi/src/db/playerSummary.ts
import { overallStandings } from './leaderboards';
import { territoryOwners } from './reads';

export interface Headline {
  turf:  { pct: number; rank: number };
  time:  { total_ms: number; rank: number };
  golf:  { points: number; rank: number };
  offwr: { avg_pct: number | null; rank: number | null };
}

/** Mean % off the current WR across each player's WR-covered PBs (season+cc). */
export function avgOffWrByPlayer(db: DatabaseSync, seasonId: number, cc: number): Map<number, number> {
  const rows = db.prepare(
    `SELECT r.player_id AS pid, AVG((r.total_time_ms - w.record_ms) * 100.0 / w.record_ms) AS avg_pct
     FROM runs r
     JOIN world_records w ON w.course_id = r.course_id AND w.cc = r.cc AND w.is_current = 1 AND w.removed_at IS NULL
     WHERE r.season_id=? AND r.cc=? AND r.is_pb=1 AND r.total_time_ms IS NOT NULL
     GROUP BY r.player_id`
  ).all(seasonId, cc) as { pid: number; avg_pct: number }[];
  const m = new Map<number, number>();
  for (const r of rows) m.set(r.pid, r.avg_pct);
  return m;
}

export function playerHeadline(db: DatabaseSync, seasonId: number, cc: number, playerId: number): Headline {
  const standings = overallStandings(db, seasonId, cc); // {player_id, display_name, total_ms, tracks, points}

  const byTime = [...standings].sort((a, b) => a.total_ms - b.total_ms || a.points - b.points);
  const timeRank = byTime.findIndex((s) => s.player_id === playerId) + 1;
  const byGolf = [...standings].sort((a, b) => a.points - b.points || a.total_ms - b.total_ms);
  const golfRank = byGolf.findIndex((s) => s.player_id === playerId) + 1;
  const me = standings.find((s) => s.player_id === playerId);

  // Turf: owned-course counts, ranked (owned desc, total_ms asc, name asc) — matches web turf.js.
  const owners = territoryOwners(db, seasonId, cc);
  const totalCourses = owners.length;
  const ownCount = new Map<number, number>();
  for (const o of owners) if (o.owner_player_id != null) ownCount.set(o.owner_player_id, (ownCount.get(o.owner_player_id) ?? 0) + 1);
  const turfRows = standings
    .map((s) => ({ id: s.player_id, name: s.display_name, owned: ownCount.get(s.player_id) ?? 0, total: s.total_ms }))
    .sort((a, b) => b.owned - a.owned || a.total - b.total || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  const turfRank = turfRows.findIndex((r) => r.id === playerId) + 1;
  const owned = ownCount.get(playerId) ?? 0;
  const turfPct = totalCourses ? Math.round((owned / totalCourses) * 100) : 0;

  // % off WR: rank all players ascending (lower = sharper).
  const avg = avgOffWrByPlayer(db, seasonId, cc);
  const withAvg = [...avg.entries()].map(([id, v]) => ({ id, v })).sort((a, b) => a.v - b.v);
  const offIdx = withAvg.findIndex((x) => x.id === playerId);
  const offwr = offIdx >= 0 ? { avg_pct: withAvg[offIdx].v, rank: offIdx + 1 } : { avg_pct: null, rank: null };

  return {
    turf: { pct: turfPct, rank: turfRank },
    time: { total_ms: me?.total_ms ?? 0, rank: timeRank },
    golf: { points: me?.points ?? 0, rank: golfRank },
    offwr,
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix pi test -- playerSummary`
Expected: PASS (all `playerHeadline` + `avgOffWrByPlayer` cases).

- [ ] **Step 5: Commit**

```bash
git add pi/src/db/playerSummary.ts pi/src/db/playerSummary.test.ts
git commit -m "feat(pi): playerHeadline — turf/time/golf/off-WR standings with ranks"
```

---

### Task 3: Pi — summary assembler + slug resolution

**Files:**
- Modify: `pi/src/db/playerSummary.ts` (add `playerSummary`)
- Test: `pi/src/db/playerSummary.test.ts` (add a describe block)

**Interfaces:**
- Consumes: `slugify` from `pi/src/db/slug.ts`; `playerPbRows`, `playerHeadline` (Tasks 1–2).
- Produces: `interface PlayerSummary`, `function playerSummary(db, seasonId, cc, slug): PlayerSummary | null`.

```ts
export interface PlayerSummary {
  profile: { slug: string; display_name: string };
  headline: Headline;
  pbs: PbRow[];
}
```

- [ ] **Step 1: Write the failing test**

```ts
// append to pi/src/db/playerSummary.test.ts
import { playerSummary } from './playerSummary';

describe('playerSummary', () => {
  it('resolves a roster player by slugified display name and assembles the summary', () => {
    const s = playerSummary(seeded(), 1, 150, 'paul');
    expect(s).not.toBeNull();
    expect(s!.profile.display_name).toBe('Paul');
    expect(s!.headline.turf.pct).toBe(50);
    expect(s!.pbs.map((r) => r.slug).sort()).toEqual(['mc', 'rr']);
  });

  it('returns null for an unknown slug', () => {
    expect(playerSummary(seeded(), 1, 150, 'nobody')).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix pi test -- playerSummary`
Expected: FAIL — `playerSummary` not exported.

- [ ] **Step 3: Write minimal implementation**

```ts
// add to pi/src/db/playerSummary.ts
import { slugify } from './slug';

export interface PlayerSummary {
  profile: { slug: string; display_name: string };
  headline: Headline;
  pbs: PbRow[];
}

/** Assemble a roster player's public summary, resolving :slug via slugify(display_name).
 *  Returns null when no active-season roster player slugifies to `slug`. */
export function playerSummary(db: DatabaseSync, seasonId: number, cc: number, slug: string): PlayerSummary | null {
  const players = db.prepare(
    `SELECT p.id, p.display_name FROM season_rosters sr JOIN players p ON p.id = sr.player_id WHERE sr.season_id=?`
  ).all(seasonId) as { id: number; display_name: string }[];
  const player = players.find((p) => slugify(p.display_name) === slug);
  if (!player) return null;
  return {
    profile: { slug, display_name: player.display_name },
    headline: playerHeadline(db, seasonId, cc, player.id),
    pbs: playerPbRows(db, seasonId, cc, player.id),
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix pi test -- playerSummary`
Expected: PASS (resolves `paul`, null for `nobody`).

- [ ] **Step 5: Commit**

```bash
git add pi/src/db/playerSummary.ts pi/src/db/playerSummary.test.ts
git commit -m "feat(pi): playerSummary — slug-resolved public player summary"
```

---

### Task 4: Pi — public route + gate/CORS exception

**Files:**
- Modify: `pi/src/api/reads.ts:6` (import), and add the route near the other `/v1/players/...` routes (`:57` area)
- Modify: `pi/src/api/app.ts:38-43` (CORS + open gate for the slug route)
- Test: `pi/src/api/players.test.ts`

**Interfaces:**
- Consumes: `playerSummary` (Task 3).
- Produces: `GET /v1/players/:slug` → `PlayerSummary` (200) or `{ error: 'unknown player' }` (404); public (no token), permissive GET CORS.

- [ ] **Step 1: Write the failing test**

```ts
// pi/src/api/players.test.ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { createApp } from './app';

function appWithData() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
  db.exec(`INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES
    (1,2,1,150,'finished','live',108000,1),(1,1,1,150,'finished','live',110000,1)`);
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,is_current) VALUES (1,150,'WR',100000,1)");
  return createApp(db, new EventHub());
}

describe('GET /v1/players/:slug', () => {
  it('serves a summary with no token and CORS headers', async () => {
    const res = await appWithData().request('/v1/players/paul');
    expect(res.status).toBe(200);
    expect(res.headers.get('access-control-allow-origin')).toBe('*');
    const body = await res.json();
    expect(body.profile.display_name).toBe('Paul');
    expect(body.pbs[0].slug).toBe('rr');
  });

  it('404s an unknown slug', async () => {
    const res = await appWithData().request('/v1/players/nobody');
    expect(res.status).toBe(404);
  });

  it('leaves the token-gated /v1/players/:id/pbs untouched (401 without a token)', async () => {
    const res = await appWithData().request('/v1/players/1/pbs');
    expect(res.status).toBe(401);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix pi test -- players`
Expected: FAIL — `/v1/players/paul` returns 401 (still token-gated) or 404 (route missing).

- [ ] **Step 3: Write minimal implementation**

In `pi/src/api/reads.ts`, extend the import on line 6 to add `playerSummary` from `../db/playerSummary`:

```ts
import { playerSummary } from '../db/playerSummary';
```

Add the route inside `readsRoutes`, just after the `/v1/players/:id/pbs` route (line 29):

```ts
  r.get('/v1/players/:slug', (c) => {
    const s = playerSummary(db, season(c), num(c.req.query('cc'), 150), c.req.param('slug'));
    return s ? c.json(s) : c.json({ error: 'unknown player' }, 404);
  });
```

In `pi/src/api/app.ts`, replace lines 40–43 (the CORS loop + gate) with:

```ts
  for (const p of PUBLIC_READS) app.use(p, readCors);
  app.use('/v1/players/:slug', readCors);   // single-segment player summary is public

  const OPEN = new Set(['/health', '/v1/events', '/v1/presence', '/v1/activity/stream', ...PUBLIC_READS]);
  const PLAYER_SUMMARY = /^\/v1\/players\/[^/]+$/;   // NOT /v1/players/:id/pbs|trails (two segments — stay gated)
  const isOpen = (path: string) => OPEN.has(path) || PLAYER_SUMMARY.test(path);
  app.use('*', (c, next) => (isOpen(c.req.path) ? next() : requireTokenAny(db)(c, next)));
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix pi test -- players` then `npm --prefix pi run typecheck`
Expected: PASS (3 cases); typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add pi/src/api/reads.ts pi/src/api/app.ts pi/src/api/players.test.ts
git commit -m "feat(pi): public GET /v1/players/:slug (gate + CORS exception)"
```

---

### Task 5: Web — pure strategy library

**Files:**
- Create: `web/src/lib/strategy.js`
- Test: `web/src/lib/strategy.test.js`

**Interfaces:**
- Consumes: `fireBarPct` from `web/src/lib/fireModel.js`; `PbRow`-shaped objects from `GET /v1/players/:slug`.
- Produces: `golfList(pbs)`, `turfList(pbs)`, `timeList(pbs)` — each returns a new sorted array (rows spread with derived fields; inputs untouched).

- [ ] **Step 1: Write the failing test**

```js
// web/src/lib/strategy.test.js
import { describe, it, expect } from "vitest";
import { golfList, turfList, timeList } from "./strategy.js";

// Minimal PbRow shapes (only the fields the sorts read).
const row = (o) => ({
  slug: o.slug, leads: !!o.leads, your_ms: o.your_ms, wr_ms: o.wr_ms,
  next_rank_ms: o.next_rank_ms ?? null, leader_ms: o.leader_ms ?? null,
  leader_off_wr_pct: o.leader_off_wr_pct ?? null, off_wr_pct: o.off_wr_pct ?? null,
});

describe("strategy", () => {
  it("golfList ranks a same-ms gap easier when the PB sits further off WR", () => {
    const near = row({ slug: "near", your_ms: 101000, wr_ms: 100000, next_rank_ms: 100800, off_wr_pct: 1 }); // 200ms gap, ~1% off
    const far  = row({ slug: "far",  your_ms: 110000, wr_ms: 100000, next_rank_ms: 109800, off_wr_pct: 10 }); // 200ms gap, ~10% off
    const out = golfList([near, far]);
    expect(out.map((r) => r.slug)).toEqual(["far", "near"]); // far is easier
    expect(out[0].ease).toBeLessThan(out[1].ease);
  });

  it("golfList excludes courses you lead or that lack a rival above / a WR", () => {
    const lead = row({ slug: "lead", leads: true, your_ms: 90000, wr_ms: 80000, off_wr_pct: 12.5 });
    const nowr = row({ slug: "nowr", your_ms: 90000, wr_ms: null, next_rank_ms: 89000 });
    expect(golfList([lead, nowr])).toEqual([]);
  });

  it("turfList prefers a soft leader (further off WR) over a tight one at equal gap", () => {
    const soft  = row({ slug: "soft",  your_ms: 110000, wr_ms: 100000, leader_ms: 109000, leader_off_wr_pct: 9, off_wr_pct: 10 });
    const tight = row({ slug: "tight", your_ms: 110000, wr_ms: 100000, leader_ms: 109000, leader_off_wr_pct: 1, off_wr_pct: 10 });
    expect(turfList([soft, tight]).map((r) => r.slug)).toEqual(["soft", "tight"]);
  });

  it("timeList sorts your PBs by largest % off WR first and drops WR-less rows", () => {
    const a = row({ slug: "a", your_ms: 1, wr_ms: 1, off_wr_pct: 3 });
    const b = row({ slug: "b", your_ms: 1, wr_ms: 1, off_wr_pct: 8 });
    const c = row({ slug: "c", your_ms: 1, wr_ms: null, off_wr_pct: null });
    expect(timeList([a, b, c]).map((r) => r.slug)).toEqual(["b", "a"]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix web test -- strategy`
Expected: FAIL — `strategy.js` not found.

- [ ] **Step 3: Write minimal implementation**

```js
// web/src/lib/strategy.js
// Strategy sorts for the players page. One per-course table (the summary's `pbs`), three sort
// keys, one shared difficulty kernel: the on-fire fireBarPct — the natural spread of time "in
// play" at a given distance off the WR. A fixed ms gap is easy far off WR (fat denominator),
// brutal near it. GOLF = cheapest next place; TURF = softest #1 to steal; TIME = worst PBs.
import { fireBarPct } from "./fireModel.js";

const gapPct = (yourMs, rivalMs, wrMs) => Math.max(0, ((yourMs - rivalMs) / wrMs) * 100);
const offPct = (yourMs, wrMs) => Math.max(0, ((yourMs - wrMs) / wrMs) * 100);

/** Courses you don't lead, ranked by difficulty-adjusted ease of gaining your next single place. */
export function golfList(pbs) {
  return pbs
    .filter((r) => !r.leads && r.next_rank_ms != null && r.wr_ms != null)
    .map((r) => ({ ...r, ease: gapPct(r.your_ms, r.next_rank_ms, r.wr_ms) / fireBarPct(offPct(r.your_ms, r.wr_ms)) }))
    .sort((a, b) => a.ease - b.ease);
}

/** Courses you don't lead, ranked by how snuffable the leader is: ease to the leader, made
 *  easier the further off WR the leader sits (a soft record is more stealable). */
export function turfList(pbs) {
  return pbs
    .filter((r) => !r.leads && r.leader_ms != null && r.wr_ms != null && r.leader_off_wr_pct != null)
    .map((r) => {
      const ease = gapPct(r.your_ms, r.leader_ms, r.wr_ms) / fireBarPct(offPct(r.your_ms, r.wr_ms));
      return { ...r, ease, score: ease / fireBarPct(r.leader_off_wr_pct) };
    })
    .sort((a, b) => a.score - b.score);
}

/** Your PBs, worst % off WR first — where your total time bleeds most. */
export function timeList(pbs) {
  return pbs
    .filter((r) => r.off_wr_pct != null)
    .map((r) => ({ ...r }))
    .sort((a, b) => b.off_wr_pct - a.off_wr_pct);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix web test -- strategy`
Expected: PASS (all four cases).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/strategy.js web/src/lib/strategy.test.js
git commit -m "feat(web): strategy lib — GOLF/TURF/TIME sorts on the shared fire kernel"
```

---

### Task 6: Web — routing, slug helper, and API URL

**Files:**
- Modify: `web/src/lib/view.js`
- Create: `web/src/lib/playerSlug.js`
- Test: `web/src/lib/view.test.js` (add cases), `web/src/lib/playerSlug.test.js`
- Modify: `web/src/lib/api.js` (add `playerSummaryUrl`, `rosterUrl`)
- Modify: `web/src/App.svelte` (nav tab + view routing + slug state)

**Interfaces:**
- Produces: `viewFromPath(path)` now returns `"players"` for `/players` and `/players/:slug`; new `playerSlugFromPath(path): string | null`; `playerSlug(name): string`; `playerSummaryUrl(slug, cc=150)`, `rosterUrl()`.
- Consumes (App.svelte): `PlayersIndex.svelte` (Task 7), `PlayerProfile.svelte` (Task 8) — imported now; created next.

- [ ] **Step 1: Write the failing tests**

```js
// append to web/src/lib/view.test.js
import { viewFromPath, playerSlugFromPath } from "./view.js";
describe("players routing", () => {
  it("routes /players and /players/:slug to the players view", () => {
    expect(viewFromPath("/players")).toBe("players");
    expect(viewFromPath("/players/paul")).toBe("players");
  });
  it("extracts the slug (null on the index)", () => {
    expect(playerSlugFromPath("/players")).toBeNull();
    expect(playerSlugFromPath("/players/paul")).toBe("paul");
    expect(playerSlugFromPath("/turf")).toBeNull();
  });
});
```

```js
// web/src/lib/playerSlug.test.js
import { describe, it, expect } from "vitest";
import { playerSlug } from "./playerSlug.js";
it("mirrors the Pi slugify (lowercase, drop apostrophes, join on non-alnum)", () => {
  expect(playerSlug("Paul")).toBe("paul");
  expect(playerSlug("Paul Pork")).toBe("paul_pork");
  expect(playerSlug("D’Angelo")).toBe("dangelo");
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm --prefix web test -- view playerSlug`
Expected: FAIL — `playerSlugFromPath` / `playerSlug` not exported.

- [ ] **Step 3: Write minimal implementations**

Replace `web/src/lib/view.js` body with (keeps existing views, adds players):

```js
// Views selected by the URL path (History API — no more #/ hash). Unknown paths fall back
// to "live". `heat` and `version` are URL-only. `/players` and `/players/:slug` share the
// "players" view; the slug (via playerSlugFromPath) selects index vs profile.
export function viewFromPath(pathname) {
  const p = (pathname || "/").replace(/^\/+/, "").replace(/\/+$/, "");
  if (p === "turf") return "turf";
  if (p === "heat") return "heat";
  if (p === "version") return "version";
  if (p === "players" || p.startsWith("players/")) return "players";
  return "live";
}

/** The player slug from /players/:slug, or null on /players (index) and non-player paths. */
export function playerSlugFromPath(pathname) {
  const p = (pathname || "/").replace(/^\/+/, "").replace(/\/+$/, "");
  const m = /^players\/([^/]+)/.exec(p);
  return m ? m[1] : null;
}
```

```js
// web/src/lib/playerSlug.js
// Client mirror of the Pi's slugify (pi/src/db/slug.ts): a player's display name -> route slug.
// Players have no slug column, so /players/:slug is resolved by slugified display name on both ends.
export function playerSlug(name) {
  return (name || "")
    .toLowerCase()
    .replace(/[‘’']/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}
```

Add to `web/src/lib/api.js`:

```js
export const rosterUrl = () => `${API_BASE}/v1/roster`;
export const playerSummaryUrl = (slug, cc = 150) => `${API_BASE}/v1/players/${encodeURIComponent(slug)}?cc=${cc}`;
```

In `web/src/App.svelte`:
- Add imports near the other component imports (line 7–8 area):

```js
  import PlayersIndex from "./PlayersIndex.svelte";
  import PlayerProfile from "./PlayerProfile.svelte";
  import { viewFromPath, playerSlugFromPath } from "./lib/view.js";
```
(Replace the existing `import { viewFromPath } from "./lib/view.js";` on line 3 with the combined import above; do not leave a duplicate.)

- Add slug state next to `let view` (line 19):

```js
  let playerSlug = playerSlugFromPath(typeof location !== "undefined" ? location.pathname : "/");
```

- In `navigate(e)` (after `view = viewFromPath(location.pathname);`, line 44) add:

```js
    playerSlug = playerSlugFromPath(location.pathname);
```

- In the popstate `sync` handler (line 48) change it to also set the slug:

```js
    const sync = () => { view = viewFromPath(location.pathname); playerSlug = playerSlugFromPath(location.pathname); };
```

- Add the nav tab after the Turf tab (line 69):

```svelte
    <a class="tab" class:on={view === "players"} href="/players" on:click={navigate}>Players</a>
```

- Add the view branch in the render block (before the final `{:else}`, line 76):

```svelte
  {:else if view === "players"}
    {#if playerSlug}<PlayerProfile slug={playerSlug} />{:else}<PlayersIndex />{/if}
```

- [ ] **Step 4: Run tests + check to verify they pass**

Run: `npm --prefix web test -- view playerSlug`
Expected: PASS. (`npm --prefix web run check` will fail until Task 7–8 create the imported components — that is expected and resolved by Task 8.)

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/view.js web/src/lib/view.test.js web/src/lib/playerSlug.js web/src/lib/playerSlug.test.js web/src/lib/api.js web/src/App.svelte
git commit -m "feat(web): players routing, slug helper, and summary API URL"
```

---

### Task 7: Web — PlayersIndex (roster grid)

**Files:**
- Create: `web/src/PlayersIndex.svelte`

**Interfaces:**
- Consumes: `rosterUrl`, `territoryUrl` from `lib/api.js`; `playerSlug` from `lib/playerSlug.js`; `playerKey` from `../../src/lib/playerKey.js`; player figure GIFs at `/players/<key>.gif` (Pi-served `web/public/players/`).
- Produces: `<PlayersIndex />` — renders roster cards (fixed `/v1/roster` order) linking to `/players/:slug`, each showing the player's turf %.

- [ ] **Step 1: Implement the component**

```svelte
<!-- web/src/PlayersIndex.svelte -->
<script>
  import { onMount } from "svelte";
  import { rosterUrl, territoryUrl } from "./lib/api.js";
  import { playerSlug } from "./lib/playerSlug.js";
  import { playerKey } from "../../src/lib/playerKey.js";

  let players = [];   // [{ display_name, slug, key, turfPct }]
  let error = null;

  onMount(async () => {
    try {
      const [roster, territory] = await Promise.all([
        fetch(rosterUrl()).then((r) => r.json()),
        fetch(territoryUrl()).then((r) => r.json()),
      ]);
      const total = territory.length || 1;
      const owned = {};
      for (const c of territory) if (c.owner_player_id != null) owned[c.owner_player_id] = (owned[c.owner_player_id] || 0) + 1;
      players = roster.map((p) => ({
        display_name: p.display_name,
        slug: playerSlug(p.display_name),
        key: playerKey(p.display_name),
        turfPct: Math.round(((owned[p.player_id] || 0) / total) * 100),
      }));
    } catch (e) { error = String(e); }
  });
</script>

<section class="players-index">
  <h1>Players</h1>
  {#if error}<p class="err">Couldn't load players: {error}</p>{/if}
  <div class="grid">
    {#each players as p (p.slug)}
      <a class="card" href={`/players/${p.slug}`}>
        <img class="figure" src={`/players/${p.key}.gif`} alt={p.display_name} loading="lazy" />
        <span class="name">{p.display_name}</span>
        <span class="turf">{p.turfPct}% turf</span>
      </a>
    {/each}
  </div>
</section>

<style>
  .players-index { padding: 1rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; }
  .card { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 10px;
          text-decoration: none; color: inherit; border: 1px solid var(--line, #333); border-radius: 8px; }
  .figure { width: 100px; height: 100px; object-fit: contain; }
  .name { font-weight: 600; }
  .turf { font-variant-numeric: tabular-nums; opacity: 0.7; font-size: 0.85em; }
  .err { color: #d66; }
</style>
```

Note: the plain `<a href>` links work as full navigations (the static server serves `index.html` for extension-less paths); in-SPA pushState is a later enhancement if wanted. Do not wire `on:click` here — `navigate` lives in `App.svelte`.

- [ ] **Step 2: Verify the build + types**

Run: `npm --prefix web run check` (svelte-check must be clean now that both components exist after Task 8; if running Task 7 before Task 8, expect the missing-`PlayerProfile` error only). Then `npm --prefix web run build`.
Expected: build succeeds.

- [ ] **Step 3: Browser-verify**

Run `npm --prefix web run dev`, open `http://127.0.0.1:1430/players` (NOT localhost). Confirm: the grid renders one card per roster player in `/v1/roster` order, each with a figure, name, and turf %; clicking a card navigates to `/players/<slug>`.

- [ ] **Step 4: Commit**

```bash
git add web/src/PlayersIndex.svelte
git commit -m "feat(web): PlayersIndex — roster grid linking to profiles"
```

---

### Task 8: Web — PlayerProfile (card + headline + PB table)

**Files:**
- Create: `web/src/PlayerProfile.svelte`

**Interfaces:**
- Consumes: `playerSummaryUrl` from `lib/api.js`; `fmtTime` from `lib/activityFormat.js`; `playerKey` from `../../src/lib/playerKey.js`; `StrategyPanel.svelte` (Task 9).
- Produces: `<PlayerProfile slug={string} />` — fetches `/v1/players/:slug`, renders the card, 4 headline tiles (turf → time → golf → off-WR, each value + rank), the full PB table, and `<StrategyPanel {pbs} />`.

- [ ] **Step 1: Implement the component**

```svelte
<!-- web/src/PlayerProfile.svelte -->
<script>
  import { playerSummaryUrl } from "./lib/api.js";
  import { fmtTime } from "./lib/activityFormat.js";
  import { playerKey } from "../../src/lib/playerKey.js";
  import StrategyPanel from "./StrategyPanel.svelte";

  export let slug;

  let summary = null;
  let error = null;

  $: load(slug);
  async function load(s) {
    summary = null; error = null;
    try {
      const res = await fetch(playerSummaryUrl(s));
      if (res.status === 404) { error = "No such player."; return; }
      summary = await res.json();
    } catch (e) { error = String(e); }
  }

  const pct = (v) => (v == null ? "—" : `${v.toFixed(1)}%`);
  const ord = (r) => (r == null || r === 0 ? "—" : `#${r}`);
</script>

{#if error}
  <p class="err">{error}</p>
{:else if summary}
  <section class="profile">
    <header class="head">
      <img class="figure" src={`/players/${playerKey(summary.profile.display_name)}.gif`} alt={summary.profile.display_name} />
      <div class="tiles">
        <div class="tile"><span class="k">Turf</span><span class="v">{summary.headline.turf.pct}%</span><span class="r">{ord(summary.headline.turf.rank)}</span></div>
        <div class="tile"><span class="k">Total time</span><span class="v">{fmtTime(summary.headline.time.total_ms)}</span><span class="r">{ord(summary.headline.time.rank)}</span></div>
        <div class="tile"><span class="k">Golf</span><span class="v">{summary.headline.golf.points}</span><span class="r">{ord(summary.headline.golf.rank)}</span></div>
        <div class="tile"><span class="k">% off WR</span><span class="v">{pct(summary.headline.offwr.avg_pct)}</span><span class="r">{ord(summary.headline.offwr.rank)}</span></div>
      </div>
    </header>

    <h2>{summary.profile.display_name}</h2>

    <table class="pbs">
      <thead><tr><th>Course</th><th>PB</th><th>Rank</th><th>WR</th><th>Δ WR</th><th>Gap ↑</th></tr></thead>
      <tbody>
        {#each summary.pbs as r (r.slug)}
          <tr>
            <td class="course">{r.course}</td>
            <td class="num">{fmtTime(r.your_ms)}</td>
            <td class="num">{r.your_rank}/{r.field_size}</td>
            <td class="num">{r.wr_ms == null ? "—" : fmtTime(r.wr_ms)}</td>
            <td class="num">{pct(r.off_wr_pct)}</td>
            <td class="num">{r.gap_to_next_ms == null ? "—" : `+${(r.gap_to_next_ms / 1000).toFixed(3)}`}</td>
          </tr>
        {/each}
      </tbody>
    </table>

    <StrategyPanel pbs={summary.pbs} />
  </section>
{:else}
  <p class="loading">Loading…</p>
{/if}

<style>
  .profile { padding: 1rem; }
  .head { display: flex; gap: 16px; align-items: center; }
  .figure { width: 120px; height: 120px; object-fit: contain; }
  .tiles { display: grid; grid-template-columns: repeat(2, minmax(120px, 1fr)); gap: 8px; }
  .tile { display: flex; flex-direction: column; padding: 8px 12px; border: 1px solid var(--line, #333); border-radius: 8px; }
  .tile .k { font-size: 0.75em; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.04em; }
  .tile .v { font-size: 1.3em; font-variant-numeric: tabular-nums; }
  .tile .r { font-size: 0.8em; opacity: 0.6; font-variant-numeric: tabular-nums; }
  .pbs { width: 100%; border-collapse: collapse; margin: 1rem 0; }
  .pbs th, .pbs td { padding: 4px 8px; border-bottom: 1px solid var(--line, #2a2a2a); text-align: left; }
  .pbs .num { font-variant-numeric: tabular-nums; text-align: right; }
  .err { color: #d66; padding: 1rem; }
  .loading { padding: 1rem; opacity: 0.6; }
</style>
```

- [ ] **Step 2: Verify types + build**

Run: `npm --prefix web run check` then `npm --prefix web run build`.
Expected: svelte-check clean (all imports — `PlayersIndex`, `PlayerProfile`, `StrategyPanel` — now resolve once Task 9 exists; if running before Task 9, expect only the missing-`StrategyPanel` error). Build succeeds after Task 9.

- [ ] **Step 3: Browser-verify**

`npm --prefix web run dev`, open `http://127.0.0.1:1430/players/<a-real-slug>`. Confirm: figure, four headline tiles (turf/time/golf/off-WR with ranks), and a PB table with one row per course (PB, rank/field, WR, Δ WR %, gap ↑). A bad slug shows "No such player."

- [ ] **Step 4: Commit**

```bash
git add web/src/PlayerProfile.svelte
git commit -m "feat(web): PlayerProfile — card, headline standings, PB table"
```

---

### Task 9: Web — StrategyPanel (GOLF/TURF/TIME toggle)

**Files:**
- Create: `web/src/StrategyPanel.svelte`

**Interfaces:**
- Consumes: `golfList`, `turfList`, `timeList` from `lib/strategy.js`; `fmtTime` from `lib/activityFormat.js`.
- Produces: `<StrategyPanel pbs={PbRow[]} />` — a mode toggle (GOLF | TURF | TIME) rendering the corresponding ranked list.

- [ ] **Step 1: Implement the component**

```svelte
<!-- web/src/StrategyPanel.svelte -->
<script>
  import { golfList, turfList, timeList } from "./lib/strategy.js";

  export let pbs = [];

  const MODES = [
    { key: "golf", label: "GOLF", hint: "cheapest next place to gain" },
    { key: "turf", label: "TURF", hint: "softest #1 to steal" },
    { key: "time", label: "TIME", hint: "your worst PBs vs WR" },
  ];
  let mode = "golf";

  $: rows = mode === "golf" ? golfList(pbs) : mode === "turf" ? turfList(pbs) : timeList(pbs);

  const secs = (ms) => `${(ms / 1000).toFixed(3)}s`;
  function line(r) {
    if (mode === "time") return `${r.off_wr_pct.toFixed(1)}% off WR`;
    if (mode === "turf") return `shave ${secs(r.your_ms - r.leader_ms)} → take #1 (leader ${r.leader_off_wr_pct.toFixed(1)}% off WR)`;
    return `shave ${secs(r.your_ms - r.next_rank_ms)} → ${r.your_rank}${nth(r.your_rank)} → ${r.your_rank - 1}${nth(r.your_rank - 1)}`;
  }
  const nth = (n) => (n % 10 === 1 && n % 100 !== 11 ? "st" : n % 10 === 2 && n % 100 !== 12 ? "nd" : n % 10 === 3 && n % 100 !== 13 ? "rd" : "th");
</script>

<section class="strategy">
  <div class="tabs">
    {#each MODES as m (m.key)}
      <button class:on={mode === m.key} on:click={() => (mode = m.key)} title={m.hint}>{m.label}</button>
    {/each}
  </div>
  <p class="hint">{MODES.find((m) => m.key === mode).hint}</p>
  {#if rows.length === 0}
    <p class="empty">Nothing to show here.</p>
  {:else}
    <ol class="rows">
      {#each rows as r (r.slug)}
        <li><span class="course">{r.course}</span><span class="advice">{line(r)}</span></li>
      {/each}
    </ol>
  {/if}
</section>

<style>
  .strategy { margin-top: 1.5rem; }
  .tabs { display: flex; gap: 4px; }
  .tabs button { padding: 4px 14px; border: 1px solid var(--line, #333); background: transparent; color: inherit;
                 cursor: pointer; letter-spacing: 0.08em; font-weight: 600; border-radius: 6px; }
  .tabs button.on { background: var(--line, #333); }
  .hint { opacity: 0.6; font-size: 0.85em; margin: 6px 0; }
  .rows { list-style: none; padding: 0; margin: 0; }
  .rows li { display: flex; justify-content: space-between; gap: 12px; padding: 5px 0; border-bottom: 1px solid var(--line, #2a2a2a); }
  .course { font-weight: 600; }
  .advice { font-variant-numeric: tabular-nums; opacity: 0.85; }
  .empty { opacity: 0.6; }
</style>
```

- [ ] **Step 2: Verify types + build**

Run: `npm --prefix web run check` then `npm --prefix web run build`.
Expected: svelte-check clean; build succeeds (all components now resolve).

- [ ] **Step 3: Browser-verify**

`npm --prefix web run dev`, open a profile at `http://127.0.0.1:1430/players/<slug>`. Confirm: three tabs GOLF/TURF/TIME switch the list; GOLF rows read "shave 0.05s → 5th → 4th" cheapest-first; TURF rows name the leader's % off WR; TIME rows list worst % off WR first. A player who leads every course shows "Nothing to show here" on GOLF/TURF but a populated TIME list.

- [ ] **Step 4: Full test sweep + commit**

Run: `npm --prefix web test` and `npm --prefix pi test` and `npm --prefix pi run typecheck`.
Expected: all green; typecheck clean.

```bash
git add web/src/StrategyPanel.svelte
git commit -m "feat(web): StrategyPanel — GOLF/TURF/TIME strategy toggle"
```

---

## Self-Review

**Spec coverage:**
- Nav one item → index → profile, strategy on profile → Tasks 6–9. ✓
- Fixed roster order (not rank-sorted) → Task 7 uses `/v1/roster` order verbatim. ✓
- One new public endpoint `GET /v1/players/:slug` + PUBLIC_READS/CORS → Task 4. ✓
- Index reuses `/v1/roster` + `/v1/territory` (no new endpoint) → Task 7. ✓
- Headline: turf/time/golf/off-WR each value + rank, turf primary → Task 2 + Task 8 tile order. ✓
- PB table (course, PB, rank, WR, Δ WR, gap to next) → Task 8. ✓
- Strategy = one table, three sorts, shared `fireBarPct` kernel; GOLF cheapest single place (no leapfrog), TURF inverse-fire softened by leader off-WR, TIME worst % off WR → Task 5 + Task 9. ✓
- Files listed in spec → all created/modified across Tasks 1–9. ✓
- v1 uses only already-public data + one endpoint; no porker/stats surface → confirmed (no `/v1/stats` touched). ✓
- Visual design deferred → components minimal-CSS, noted in Global Constraints. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases" — every step has real code or a concrete command. Edge cases (no WR, player leads, unknown slug, WR-less rows) are covered by explicit tests/filters. ✓

**Type consistency:** `PbRow` field names are identical across the Pi producer (Task 1), the strategy filters/sorts (Task 5 — reads `leads`, `your_ms`, `wr_ms`, `next_rank_ms`, `leader_ms`, `leader_off_wr_pct`, `off_wr_pct`), and the Svelte consumers (Tasks 8–9 — `course`, `your_rank`, `field_size`, `gap_to_next_ms`). `Headline` shape (`turf/time/golf/offwr` each `{value, rank}`) matches between Task 2 and the Task 8 tiles. `playerSlug`/`slugify` algorithms match between web and Pi. ✓

## Notes for the implementer

- **Deferred visual pass:** after Task 9, a separate frontend-design pass will restyle all three components to the site's OBS-plain / functional-colour standards and verify in a real browser. The CSS here is deliberately minimal — correct structure and data binding are what these tasks deliver.
- **v2 (not in this plan):** most-played character/kart, coins, play counts, and trend charts need a new public passthrough over the token-gated `/v1/stats/*` (porker) surface — a separate spec.

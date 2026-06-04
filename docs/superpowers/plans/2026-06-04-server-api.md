# Server API (sub-project B, Phase 1 — server) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Pi server — a TypeScript/Node (Hono) HTTP API + WebSocket event hub over the sub-project A SQLite store — that ingests uploaded attempts (idempotent, token-gated), serves public leaderboard/WR reads, and fans out event notifications. Standalone-testable; the client write path is a separate plan.

**Architecture:** A single new `pi/` Node package (TypeScript, run via `tsx`, tested via `vitest`). A thin **db layer** (`src/db/*`) over Node's built-in `node:sqlite`, applying A's `server/schema.sql`. A **Hono app** (`src/api/*`): bearer-token auth, `POST /v1/runs` ingest with server-side `is_pb` recompute + event derivation, public read routes, and a `WS /v1/events` hub.

**Tech Stack:** Node 25 + npm 11; TypeScript (`tsx` runner, `moduleResolution: Bundler`); `vitest`; `node:sqlite` (built-in, no native deps); `hono` + `@hono/node-server` + `@hono/node-ws`; Node's global `WebSocket` for test clients. Spec: `docs/superpowers/specs/2026-06-04-server-api-and-sync-design.md`.

**Tooling decisions (veto at review):**
- **Single `pi/` package now**, not a pnpm workspace (pnpm isn't installed; and the `packages/*`/`apps/*` split is only needed once C imports shared types — extract then). Folders `src/db`, `src/api`, `src/scripts`.
- **`node:sqlite`** (built-in) instead of `better-sqlite3` — zero native-build friction on Node 25 + ARM Pi; same SQLite engine. (It prints an `ExperimentalWarning`; suppressed at run time with `--no-warnings`.) Swappable later if we want the mature native lib.
- **Wire/event types live in `src/db/types.ts`** for now; extracted to a shared package when C needs them.

**Every commit** ends with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Work on branch `server-sync`. **Run all commands from `pi/`** unless noted.

---

## File Structure

| Path | Responsibility |
|---|---|
| `pi/package.json`, `pi/tsconfig.json` | Node package + TS config (`tsx`, `vitest`). |
| `server/schema.sql` *(modify)* | Add `players.auth_token_hash` (single schema source). |
| `pi/src/db/connect.ts` | `openDb(path)` → `DatabaseSync` (WAL, FK on); `applySchema(db)` (reads `server/schema.sql`). |
| `pi/src/db/types.ts` | Wire payload + event + row TS types. |
| `pi/src/db/slug.ts` | `slugify()` (TS port of A's apostrophe-stripping rule). |
| `pi/src/db/seasons.ts` | `activeSeasonId(db)`, `listSeasons(db)`, `courseIdBySlug(db, slug)`. |
| `pi/src/db/ingest.ts` | `upsertRun(db, payload, playerId, seasonId)` — idempotent by `attempt_id`. |
| `pi/src/db/pb.ts` | `recomputeIsPb(db, seasonId, playerId, courseId, cc)` (scoped). |
| `pi/src/db/reads.ts` | `courseLeaderboard`, `overallLeaderboard`, `friendsPbs`, `playerPbs`, `currentWr`. |
| `pi/src/db/players.ts` | `hashToken`, `mintToken(db, name)`, `playerByToken(db, token)`. |
| `pi/src/api/auth.ts` | Hono bearer middleware → `c.set('playerId', …)`. |
| `pi/src/api/events.ts` | `EventHub` (subscriber set + `publish`) + `eventsWs` route factory. |
| `pi/src/api/runs.ts` | `POST /v1/runs`, `POST /v1/runs/start` (ingest + derive + publish). |
| `pi/src/api/reads.ts` | public GET routes. |
| `pi/src/api/app.ts` | `createApp(db, hub)` → Hono app wiring all routes. |
| `pi/src/server.ts` | entry: `serve` + WS injection (`--no-warnings`). |
| `pi/src/scripts/mintToken.ts` | CLI: `tsx src/scripts/mintToken.ts <player>`. |

**Interfaces locked here:**

```ts
// src/db/types.ts
export type Lap = { lap: number; time_ms: number; coins?: number | null; shrooms?: number | null };
export type Point = [number, number, number, number]; // [t_ms, cx, cy, score]
export type AttemptPayload = {
  attempt_id: string; course: string; cc?: number;
  status: 'finished' | 'reset' | 'dnf';
  character?: string | null; kart?: string | null; costume?: string | null;
  started_at?: string | null; ended_at?: string | null; total_time?: string | null;
  laps?: Lap[]; points?: Point[];
};
export type RunResult = { is_pb: boolean; rank: number | null; gap_to_leader_ms: number | null; gap_to_wr_ms: number | null };
export type ServerEvent =
  | { type: 'run_started'; player: string; course: string; cc: number }
  | { type: 'run_finished'; player: string; course: string; cc: number; total_time: string | null; is_pb: boolean; rank: number | null }
  | { type: 'pb_achieved'; player: string; course: string; cc: number; total_time: string; delta_vs_prev_ms: number | null; rank: number | null }
  | { type: 'lead_change'; course: string; cc: number; new_leader: string; prev_leader: string | null; total_time: string }
  | { type: 'wr_beaten'; player: string; course: string; cc: number; total_time: string; wr_time: string };
```

---

### Task 1: Scaffold `pi/` + node:sqlite smoke test

**Files:** Create `pi/package.json`, `pi/tsconfig.json`, `pi/.gitignore`, `pi/src/db/connect.ts` (smoke), `pi/src/db/connect.test.ts`.

- [ ] **Step 1: Create `pi/package.json`**

```json
{
  "name": "mkw-pi",
  "private": true,
  "type": "module",
  "scripts": {
    "test": "vitest run",
    "dev": "node --no-warnings --import tsx src/server.ts",
    "mint-token": "node --no-warnings --import tsx src/scripts/mintToken.ts"
  },
  "dependencies": {
    "hono": "^4.6.0",
    "@hono/node-server": "^1.13.0",
    "@hono/node-ws": "^1.0.4"
  },
  "devDependencies": {
    "typescript": "^5.7.0",
    "tsx": "^4.19.0",
    "vitest": "^4.1.8",
    "@types/node": "^22.10.0"
  }
}
```

- [ ] **Step 2: Create `pi/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2023",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "types": ["node"]
  },
  "include": ["src"]
}
```

- [ ] **Step 3: Create `pi/.gitignore`**

```
node_modules/
*.db
*.db-*
```

- [ ] **Step 4: Install deps**

Run (from `pi/`): `npm install`
Expected: completes; `node_modules/` populated, no native build (node:sqlite is built-in).

- [ ] **Step 5: Write the failing test** `pi/src/db/connect.test.ts`

```ts
import { describe, it, expect } from 'vitest';
import { openDb } from './connect';

describe('openDb', () => {
  it('opens an in-memory db with foreign keys on', () => {
    const db = openDb(':memory:');
    db.exec('CREATE TABLE t(x INTEGER)');
    db.prepare('INSERT INTO t(x) VALUES (?)').run(7);
    const row = db.prepare('SELECT COUNT(*) c, SUM(x) s FROM t').get() as { c: number; s: number };
    expect(row).toEqual({ c: 1, s: 7 });
    expect((db.prepare('PRAGMA foreign_keys').get() as { foreign_keys: number }).foreign_keys).toBe(1);
  });
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `npx vitest run src/db/connect.test.ts`
Expected: FAIL — cannot find `./connect` / `openDb`.

- [ ] **Step 7: Create `pi/src/db/connect.ts`** (smoke version)

```ts
import { DatabaseSync } from 'node:sqlite';

export function openDb(path: string): DatabaseSync {
  const db = new DatabaseSync(path);
  db.exec('PRAGMA journal_mode = WAL');
  db.exec('PRAGMA foreign_keys = ON');
  return db;
}
```

- [ ] **Step 8: Run it to verify it passes**

Run: `npx vitest run src/db/connect.test.ts`
Expected: PASS (1 test; an `ExperimentalWarning` line is fine).

- [ ] **Step 9: Commit**

```bash
git add pi/package.json pi/tsconfig.json pi/.gitignore pi/src/db/connect.ts pi/src/db/connect.test.ts pi/package-lock.json
git commit -m "feat(pi): scaffold server package + node:sqlite connect" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Schema apply (+ `auth_token_hash`)

**Files:** Modify `server/schema.sql` (repo root); modify `pi/src/db/connect.ts`; test `pi/src/db/schema.test.ts`.

- [ ] **Step 1: Add the auth column to `server/schema.sql`**

In `server/schema.sql`, change the `players` table to include the hash column:

```sql
CREATE TABLE IF NOT EXISTS players (
    id              INTEGER PRIMARY KEY,
    display_name    TEXT NOT NULL UNIQUE,
    auth_token_hash TEXT UNIQUE,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

(Adding a column to the `CREATE` is enough — the authoritative server DB is created fresh at/after cutover, after this work. A's Python tests still pass: the importer never references the column.)

- [ ] **Step 2: Write the failing test** `pi/src/db/schema.test.ts`

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';

const TABLES = ['seasons','players','season_rosters','courses','runs','run_laps','run_points','world_records'];

describe('applySchema', () => {
  it('creates every canonical table + the auth column', () => {
    const db = openDb(':memory:');
    applySchema(db);
    const names = (db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all() as { name: string }[])
      .map(r => r.name);
    for (const t of TABLES) expect(names).toContain(t);
    const cols = (db.prepare('PRAGMA table_info(players)').all() as { name: string }[]).map(c => c.name);
    expect(cols).toContain('auth_token_hash');
  });

  it('is idempotent', () => {
    const db = openDb(':memory:');
    applySchema(db);
    expect(() => applySchema(db)).not.toThrow();
  });
});
```

- [ ] **Step 2b: Run it to verify it fails**

Run: `npx vitest run src/db/schema.test.ts`
Expected: FAIL — `applySchema` is not exported.

- [ ] **Step 3: Add `applySchema` to `pi/src/db/connect.ts`**

Append:

```ts
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// pi/src/db/connect.ts → repo root is four levels up; A's schema is the single source.
const SCHEMA_PATH = fileURLToPath(new URL('../../../server/schema.sql', import.meta.url));

export function applySchema(db: DatabaseSync): void {
  db.exec(readFileSync(SCHEMA_PATH, 'utf8'));
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npx vitest run src/db/schema.test.ts`
Expected: PASS (2 tests).

- [ ] **Step 4b: Confirm A's Python tests still pass**

Run (from repo root): `python -m pytest tests/test_server_db.py tests/test_server_courses.py tests/test_server_importer.py -q`
Expected: PASS (the extra column doesn't affect the importer or its tests).

- [ ] **Step 5: Commit**

```bash
git add server/schema.sql pi/src/db/connect.ts pi/src/db/schema.test.ts
git commit -m "feat(pi): apply A's schema (+ players.auth_token_hash)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Types + slugify + course/season helpers

**Files:** Create `pi/src/db/types.ts`, `pi/src/db/slug.ts`, `pi/src/db/seasons.ts`; tests `pi/src/db/slug.test.ts`, `pi/src/db/seasons.test.ts`.

- [ ] **Step 1: Create `pi/src/db/types.ts`** (the locked interfaces from the File Structure section)

```ts
export type Lap = { lap: number; time_ms: number; coins?: number | null; shrooms?: number | null };
export type Point = [number, number, number, number];
export type AttemptPayload = {
  attempt_id: string; course: string; cc?: number;
  status: 'finished' | 'reset' | 'dnf';
  character?: string | null; kart?: string | null; costume?: string | null;
  started_at?: string | null; ended_at?: string | null; total_time?: string | null;
  laps?: Lap[]; points?: Point[];
};
export type RunResult = { is_pb: boolean; rank: number | null; gap_to_leader_ms: number | null; gap_to_wr_ms: number | null };
export type ServerEvent =
  | { type: 'run_started'; player: string; course: string; cc: number }
  | { type: 'run_finished'; player: string; course: string; cc: number; total_time: string | null; is_pb: boolean; rank: number | null }
  | { type: 'pb_achieved'; player: string; course: string; cc: number; total_time: string; delta_vs_prev_ms: number | null; rank: number | null }
  | { type: 'lead_change'; course: string; cc: number; new_leader: string; prev_leader: string | null; total_time: string }
  | { type: 'wr_beaten'; player: string; course: string; cc: number; total_time: string; wr_time: string };
```

- [ ] **Step 2: Write the failing slug test** `pi/src/db/slug.test.ts`

```ts
import { describe, it, expect } from 'vitest';
import { slugify } from './slug';

describe('slugify', () => {
  it('strips apostrophes and collapses punctuation (matches A)', () => {
    expect(slugify("Bowser's Castle")).toBe('bowsers_castle');
    expect(slugify("Toad's Factory")).toBe('toads_factory');
    expect(slugify("Wario's Galleon")).toBe('warios_galleon');
    expect(slugify('Mario Bros. Circuit')).toBe('mario_bros_circuit');
    expect(slugify('Great ? Block Ruins')).toBe('great_block_ruins');
    expect(slugify('Sky-High Sundae')).toBe('sky_high_sundae');
    expect(slugify('DK Pass')).toBe('dk_pass');
  });
});
```

- [ ] **Step 2b: Run it to verify it fails**

Run: `npx vitest run src/db/slug.test.ts`
Expected: FAIL — `./slug` not found.

- [ ] **Step 3: Create `pi/src/db/slug.ts`**

```ts
export function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/['’]/g, '')      // drop straight + curly apostrophes
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npx vitest run src/db/slug.test.ts`
Expected: PASS.

- [ ] **Step 5: Write the failing seasons test** `pi/src/db/seasons.test.ts`

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { activeSeasonId, listSeasons, courseIdBySlug } from './seasons';

function seeded() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 0',0),(2,'Season 1',1)");
  db.exec("INSERT INTO courses(slug,display_name) VALUES ('rainbow_road','Rainbow Road')");
  return db;
}

describe('seasons helpers', () => {
  it('activeSeasonId returns the active season', () => {
    expect(activeSeasonId(seeded())).toBe(2);
  });
  it('listSeasons returns all', () => {
    expect(listSeasons(seeded()).map(s => s.name)).toEqual(['Season 0', 'Season 1']);
  });
  it('courseIdBySlug resolves a known slug, null otherwise', () => {
    const db = seeded();
    const rr = courseIdBySlug(db, 'rainbow_road');
    expect(typeof rr).toBe('number');
    expect(courseIdBySlug(db, 'nope')).toBeNull();
  });
});
```

- [ ] **Step 5b: Run it to verify it fails**

Run: `npx vitest run src/db/seasons.test.ts`
Expected: FAIL — `./seasons` not found.

- [ ] **Step 6: Create `pi/src/db/seasons.ts`**

```ts
import type { DatabaseSync } from 'node:sqlite';

export function activeSeasonId(db: DatabaseSync): number {
  const row = db.prepare('SELECT id FROM seasons WHERE is_active = 1 ORDER BY id DESC LIMIT 1').get() as { id: number } | undefined;
  if (!row) throw new Error('no active season');
  return row.id;
}

export function listSeasons(db: DatabaseSync): { id: number; name: string; is_active: number }[] {
  return db.prepare('SELECT id, name, is_active FROM seasons ORDER BY id').all() as any;
}

export function courseIdBySlug(db: DatabaseSync, slug: string): number | null {
  const row = db.prepare('SELECT id FROM courses WHERE slug = ?').get(slug) as { id: number } | undefined;
  return row ? row.id : null;
}
```

- [ ] **Step 7: Run it to verify it passes**

Run: `npx vitest run src/db/seasons.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 8: Commit**

```bash
git add pi/src/db/types.ts pi/src/db/slug.ts pi/src/db/seasons.ts pi/src/db/slug.test.ts pi/src/db/seasons.test.ts
git commit -m "feat(pi): wire types + slugify + season/course helpers" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Ingest (idempotent upsert) + is_pb recompute

**Files:** Create `pi/src/db/ingest.ts`, `pi/src/db/pb.ts`; tests `pi/src/db/ingest.test.ts`, `pi/src/db/pb.test.ts`.

- [ ] **Step 1: Write the failing ingest test** `pi/src/db/ingest.test.ts`

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { upsertRun } from './ingest';
import type { AttemptPayload } from './types';

function base() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  return db;
}

const payload: AttemptPayload = {
  attempt_id: 'a1', course: 'Rainbow Road', cc: 150, status: 'finished',
  character: 'Mario', kart: 'Std', costume: 'Base',
  started_at: '2026-06-01T00:00:00Z', ended_at: '2026-06-01T00:02:00Z',
  total_time: '2:00.000',
  laps: [{ lap: 1, time_ms: 40000, coins: 5, shrooms: 1 }, { lap: 2, time_ms: 80000, coins: 3, shrooms: 0 }],
  points: [[0, 1, 2, 0.9], [16, 1.1, 2.1, 0.95]],
};

describe('upsertRun', () => {
  it('inserts a live finished run with laps + points', () => {
    const db = base();
    const runId = upsertRun(db, payload, 1, 1);
    const run = db.prepare('SELECT * FROM runs WHERE id=?').get(runId) as any;
    expect(run.season_id).toBe(1);
    expect(run.player_id).toBe(1);
    expect(run.course_id).toBe(1);
    expect(run.provenance).toBe('live');
    expect(run.total_time_ms).toBe(120000);
    expect((db.prepare('SELECT COUNT(*) c FROM run_laps WHERE run_id=?').get(runId) as any).c).toBe(2);
    expect((db.prepare('SELECT COUNT(*) c FROM run_points WHERE run_id=?').get(runId) as any).c).toBe(2);
  });

  it('is idempotent by attempt_id (re-send replaces, no dup)', () => {
    const db = base();
    upsertRun(db, payload, 1, 1);
    upsertRun(db, payload, 1, 1);
    expect((db.prepare("SELECT COUNT(*) c FROM runs WHERE provenance='live'").get() as any).c).toBe(1);
    expect((db.prepare('SELECT COUNT(*) c FROM run_laps').get() as any).c).toBe(2);
  });

  it('parses total_time null for resets', () => {
    const db = base();
    const id = upsertRun(db, { attempt_id: 'r1', course: 'Rainbow Road', status: 'reset' }, 1, 1);
    const run = db.prepare('SELECT * FROM runs WHERE id=?').get(id) as any;
    expect(run.status).toBe('reset');
    expect(run.total_time_ms).toBeNull();
  });
});
```

- [ ] **Step 1b: Run it to verify it fails**

Run: `npx vitest run src/db/ingest.test.ts`
Expected: FAIL — `./ingest` not found.

- [ ] **Step 2: Create `pi/src/db/ingest.ts`**

```ts
import type { DatabaseSync } from 'node:sqlite';
import type { AttemptPayload } from './types';
import { slugify } from './slug';

export function timeToMs(t?: string | null): number | null {
  if (!t) return null;
  const m = /^(\d+):(\d{2})\.(\d{3})$/.exec(t.trim());
  if (!m) return null;
  return Number(m[1]) * 60000 + Number(m[2]) * 1000 + Number(m[3]);
}

/** Insert (or replace by attempt_id) a live attempt. Returns the run id. Caller resolves course_id externally; here we re-resolve from slug for safety. */
export function upsertRun(db: DatabaseSync, p: AttemptPayload, playerId: number, seasonId: number): number {
  const slug = slugify(p.course);
  const course = db.prepare('SELECT id FROM courses WHERE slug=?').get(slug) as { id: number } | undefined;
  if (!course) throw new Error(`unknown course: ${p.course} (${slug})`);
  const cc = p.cc ?? 150;
  const totalMs = timeToMs(p.total_time);

  db.exec('BEGIN');
  try {
    // Idempotency: clear any prior run for this attempt_id (children cascade via FK).
    const prior = db.prepare(
      "SELECT id FROM runs WHERE provenance='live' AND attempt_id=?"
    ).get(p.attempt_id) as { id: number } | undefined;
    if (prior) db.prepare('DELETE FROM runs WHERE id=?').run(prior.id);

    const info = db.prepare(
      `INSERT INTO runs(attempt_id, season_id, player_id, course_id, cc, status, provenance,
                        started_at, ended_at, total_time_ms, total_time_str, character, kart, costume, is_pb)
       VALUES (?,?,?,?,?,?, 'live', ?,?,?,?,?,?,?, 0)`
    ).run(p.attempt_id, seasonId, playerId, course.id, cc, p.status,
          p.started_at ?? null, p.ended_at ?? null, totalMs, p.total_time ?? null,
          p.character ?? null, p.kart ?? null, p.costume ?? null);
    const runId = Number(info.lastInsertRowid);

    const lapStmt = db.prepare(
      'INSERT INTO run_laps(run_id, lap_index, lap_time_ms, coins, shrooms) VALUES (?,?,?,?,?)'
    );
    for (const lap of p.laps ?? []) lapStmt.run(runId, lap.lap, lap.time_ms, lap.coins ?? null, lap.shrooms ?? null);

    const ptStmt = db.prepare('INSERT INTO run_points(run_id, t_ms, cx, cy, score) VALUES (?,?,?,?,?)');
    for (const [t, cx, cy, sc] of p.points ?? []) ptStmt.run(runId, t, cx, cy, sc);

    db.exec('COMMIT');
    return runId;
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }
}
```

> Requires `runs.attempt_id`. **Add it to `server/schema.sql`** in the `runs` table (after `id`): `attempt_id TEXT UNIQUE,` — the A importer leaves it null for legacy/carryover rows (no conflict; SQLite allows multiple NULLs in a UNIQUE column). Include this edit in Step 2 and re-run A's Python tests (`python -m pytest tests/test_server_importer.py -q`) to confirm still green before committing.

- [ ] **Step 3: Run it to verify it passes**

Run: `npx vitest run src/db/ingest.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 4: Write the failing pb test** `pi/src/db/pb.test.ts`

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { recomputeIsPb } from './pb';

function withRuns() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  const ins = db.prepare("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,ended_at) VALUES (1,1,1,150,'finished','live',?,?)");
  ins.run(110000, '2026-01-01'); ins.run(108000, '2026-02-01'); ins.run(112000, '2026-03-01');
  return db;
}

describe('recomputeIsPb', () => {
  it('flags the single fastest finished run for the scope', () => {
    const db = withRuns();
    recomputeIsPb(db, 1, 1, 1, 150);
    const pbs = db.prepare('SELECT total_time_ms FROM runs WHERE is_pb=1').all() as { total_time_ms: number }[];
    expect(pbs.map(r => r.total_time_ms)).toEqual([108000]);
  });
});
```

- [ ] **Step 4b: Run it to verify it fails**

Run: `npx vitest run src/db/pb.test.ts`
Expected: FAIL — `./pb` not found.

- [ ] **Step 5: Create `pi/src/db/pb.ts`**

```ts
import type { DatabaseSync } from 'node:sqlite';

/** Recompute is_pb for one (season, player, course, cc) scope: fastest finished run wins. */
export function recomputeIsPb(db: DatabaseSync, seasonId: number, playerId: number, courseId: number, cc: number): void {
  db.prepare('UPDATE runs SET is_pb=0 WHERE season_id=? AND player_id=? AND course_id=? AND cc=?')
    .run(seasonId, playerId, courseId, cc);
  const best = db.prepare(
    `SELECT id FROM runs WHERE season_id=? AND player_id=? AND course_id=? AND cc=? AND status='finished'
     ORDER BY total_time_ms ASC, ended_at ASC LIMIT 1`
  ).get(seasonId, playerId, courseId, cc) as { id: number } | undefined;
  if (best) db.prepare('UPDATE runs SET is_pb=1 WHERE id=?').run(best.id);
}
```

- [ ] **Step 6: Run it to verify it passes**

Run: `npx vitest run src/db/pb.test.ts`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add server/schema.sql pi/src/db/ingest.ts pi/src/db/pb.ts pi/src/db/ingest.test.ts pi/src/db/pb.test.ts
git commit -m "feat(pi): idempotent run ingest + is_pb recompute" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Read queries

**Files:** Create `pi/src/db/reads.ts`; test `pi/src/db/reads.test.ts`.

- [ ] **Step 1: Write the failing test** `pi/src/db/reads.test.ts`

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { courseLeaderboard, friendsPbs, currentWr } from './reads';

function seeded() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,is_pb) VALUES (1,1,1,150,'finished','live',108000,'1:48.000',1),(1,2,1,150,'finished','live',112000,'1:52.000',1)");
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str) VALUES (1,150,'SuperFX',100000,'1:40.000')");
  return db;
}

describe('reads', () => {
  it('courseLeaderboard orders by time with names + rank', () => {
    const lb = courseLeaderboard(seeded(), 1, 1, 150);
    expect(lb.map(r => [r.display_name, r.total_time_ms, r.rank])).toEqual([['Paul',108000,1],['Luke',112000,2]]);
  });
  it('friendsPbs returns the roster PBs for the course', () => {
    const pbs = friendsPbs(seeded(), 1, 1, 150);
    expect(pbs.length).toBe(2);
  });
  it('currentWr returns the latest WR', () => {
    expect(currentWr(seeded(), 1, 150)?.record_ms).toBe(100000);
  });
});
```

- [ ] **Step 1b: Run it to verify it fails**

Run: `npx vitest run src/db/reads.test.ts`
Expected: FAIL — `./reads` not found.

- [ ] **Step 2: Create `pi/src/db/reads.ts`**

```ts
import type { DatabaseSync } from 'node:sqlite';

export type LeaderRow = { player_id: number; display_name: string; total_time_ms: number; total_time_str: string | null; rank: number };

export function courseLeaderboard(db: DatabaseSync, seasonId: number, courseId: number, cc: number): LeaderRow[] {
  const rows = db.prepare(
    `SELECT r.player_id, p.display_name, r.total_time_ms, r.total_time_str
     FROM runs r JOIN players p ON p.id = r.player_id
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.is_pb=1
     ORDER BY r.total_time_ms ASC`
  ).all(seasonId, courseId, cc) as Omit<LeaderRow, 'rank'>[];
  return rows.map((r, i) => ({ ...r, rank: i + 1 }));
}

export function friendsPbs(db: DatabaseSync, seasonId: number, courseId: number, cc: number): LeaderRow[] {
  return courseLeaderboard(db, seasonId, courseId, cc);
}

export function playerPbs(db: DatabaseSync, seasonId: number, playerId: number, cc: number) {
  return db.prepare(
    `SELECT r.course_id, c.slug, c.display_name, r.total_time_ms, r.total_time_str
     FROM runs r JOIN courses c ON c.id = r.course_id
     WHERE r.season_id=? AND r.player_id=? AND r.cc=? AND r.is_pb=1
     ORDER BY c.display_name`
  ).all(seasonId, playerId, cc);
}

export function currentWr(db: DatabaseSync, courseId: number, cc: number) {
  return (db.prepare(
    `SELECT holder_name, record_ms, record_str, achieved_at, video_url, character, vehicle
     FROM world_records WHERE course_id=? AND cc=? ORDER BY achieved_at DESC, id DESC LIMIT 1`
  ).get(courseId, cc) as any) ?? null;
}

export function overallLeaderboard(db: DatabaseSync, seasonId: number, cc: number) {
  return db.prepare(
    `SELECT p.id player_id, p.display_name, SUM(r.total_time_ms) total_time_ms, COUNT(*) tracks
     FROM runs r JOIN players p ON p.id = r.player_id
     WHERE r.season_id=? AND r.cc=? AND r.is_pb=1
     GROUP BY p.id ORDER BY total_time_ms ASC`
  ).all(seasonId, cc);
}
```

- [ ] **Step 3: Run it to verify it passes**

Run: `npx vitest run src/db/reads.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 4: Commit**

```bash
git add pi/src/db/reads.ts pi/src/db/reads.test.ts
git commit -m "feat(pi): leaderboard / friends-pbs / WR read queries" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Token auth (mint + lookup)

**Files:** Create `pi/src/db/players.ts`; test `pi/src/db/players.test.ts`.

- [ ] **Step 1: Write the failing test** `pi/src/db/players.test.ts`

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { mintToken, playerByToken, hashToken } from './players';

function seeded() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  return db;
}

describe('token auth', () => {
  it('mintToken stores a hash and returns the plaintext once', () => {
    const db = seeded();
    const token = mintToken(db, 'Paul');
    expect(token).toMatch(/^[a-f0-9]{64}$/);
    const row = db.prepare('SELECT auth_token_hash FROM players WHERE display_name=?').get('Paul') as any;
    expect(row.auth_token_hash).toBe(hashToken(token));
  });
  it('playerByToken resolves the player, null for a bad token', () => {
    const db = seeded();
    const token = mintToken(db, 'Paul');
    expect(playerByToken(db, token)?.display_name).toBe('Paul');
    expect(playerByToken(db, 'deadbeef')).toBeNull();
  });
  it('mintToken throws for an unknown player', () => {
    expect(() => mintToken(seeded(), 'Nobody')).toThrow();
  });
});
```

- [ ] **Step 1b: Run it to verify it fails**

Run: `npx vitest run src/db/players.test.ts`
Expected: FAIL — `./players` not found.

- [ ] **Step 2: Create `pi/src/db/players.ts`**

```ts
import type { DatabaseSync } from 'node:sqlite';
import { randomBytes, createHash } from 'node:crypto';

export function hashToken(token: string): string {
  return createHash('sha256').update(token).digest('hex');
}

/** Generate a token for an existing player, store its hash, return the plaintext (shown once). */
export function mintToken(db: DatabaseSync, displayName: string): string {
  const player = db.prepare('SELECT id FROM players WHERE display_name = ? COLLATE NOCASE').get(displayName) as { id: number } | undefined;
  if (!player) throw new Error(`unknown player: ${displayName}`);
  const token = randomBytes(32).toString('hex');
  db.prepare('UPDATE players SET auth_token_hash=? WHERE id=?').run(hashToken(token), player.id);
  return token;
}

export function playerByToken(db: DatabaseSync, token: string): { id: number; display_name: string } | null {
  const row = db.prepare('SELECT id, display_name FROM players WHERE auth_token_hash=?').get(hashToken(token)) as any;
  return row ?? null;
}
```

- [ ] **Step 3: Run it to verify it passes**

Run: `npx vitest run src/db/players.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 4: Commit**

```bash
git add pi/src/db/players.ts pi/src/db/players.test.ts
git commit -m "feat(pi): per-player token mint + lookup (sha256)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Event hub + Hono app skeleton + /health + auth middleware

**Files:** Create `pi/src/api/events.ts`, `pi/src/api/auth.ts`, `pi/src/api/app.ts`; test `pi/src/api/app.test.ts`.

- [ ] **Step 1: Write the failing test** `pi/src/api/app.test.ts`

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { mintToken } from '../db/players';
import { EventHub } from './events';
import { createApp } from './app';

function appWith() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  const token = mintToken(db, 'Paul');
  return { app: createApp(db, new EventHub()), token };
}

describe('app skeleton', () => {
  it('GET /health is public + ok', async () => {
    const { app } = appWith();
    const res = await app.request('/health');
    expect(res.status).toBe(200);
    expect((await res.json()).status).toBe('ok');
  });
  it('a token-gated write 401s without a token', async () => {
    const { app } = appWith();
    const res = await app.request('/v1/runs', { method: 'POST', body: '{}', headers: { 'content-type': 'application/json' } });
    expect(res.status).toBe(401);
  });
  it('accepts a valid token (not 401)', async () => {
    const { app, token } = appWith();
    const res = await app.request('/v1/runs', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: JSON.stringify({ attempt_id: 'x', course: 'Rainbow Road', status: 'reset' }),
    });
    expect(res.status).not.toBe(401);
  });
});
```

- [ ] **Step 1b: Run it to verify it fails**

Run: `npx vitest run src/api/app.test.ts`
Expected: FAIL — `./events` / `./app` not found.

- [ ] **Step 2: Create `pi/src/api/events.ts`**

```ts
import type { ServerEvent } from '../db/types';

type Sink = (e: ServerEvent) => void;

export class EventHub {
  private sinks = new Set<Sink>();
  subscribe(sink: Sink): () => void { this.sinks.add(sink); return () => this.sinks.delete(sink); }
  publish(e: ServerEvent): void { for (const s of [...this.sinks]) { try { s(e); } catch {} } }
  get size(): number { return this.sinks.size; }
}
```

- [ ] **Step 3: Create `pi/src/api/auth.ts`**

```ts
import type { Context, Next } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import { playerByToken } from '../db/players';

export function requireToken(db: DatabaseSync) {
  return async (c: Context, next: Next) => {
    const auth = c.req.header('authorization') ?? '';
    const m = /^Bearer (.+)$/.exec(auth);
    const player = m ? playerByToken(db, m[1]) : null;
    if (!player) return c.json({ error: 'unauthorized' }, 401);
    c.set('playerId', player.id);
    c.set('playerName', player.display_name);
    await next();
  };
}
```

- [ ] **Step 4: Create `pi/src/api/app.ts`**

```ts
import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import { EventHub } from './events';
import { requireToken } from './auth';

export type Env = { Variables: { playerId: number; playerName: string } };

export function createApp(db: DatabaseSync, hub: EventHub): Hono<Env> {
  const app = new Hono<Env>();
  app.get('/health', (c) => c.json({ status: 'ok' }));
  // Placeholder write route (replaced in Task 8) — just exercises auth.
  app.post('/v1/runs', requireToken(db), (c) => c.json({ ok: true }));
  return app;
}
```

- [ ] **Step 5: Run it to verify it passes**

Run: `npx vitest run src/api/app.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add pi/src/api/events.ts pi/src/api/auth.ts pi/src/api/app.ts pi/src/api/app.test.ts
git commit -m "feat(pi): event hub + Hono app + token auth middleware" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: `POST /v1/runs` ingest + event derivation, and read routes

**Files:** Create `pi/src/api/runs.ts`, `pi/src/api/reads.ts`; modify `pi/src/api/app.ts`; test `pi/src/api/runs.test.ts`, `pi/src/api/reads.test.ts`.

- [ ] **Step 1: Write the failing test** `pi/src/api/runs.test.ts`

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { mintToken } from '../db/players';
import { EventHub } from './events';
import { createApp } from './app';
import type { ServerEvent } from '../db/types';

function ctx() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1)");
  const hub = new EventHub();
  const events: ServerEvent[] = [];
  hub.subscribe(e => events.push(e));
  return { app: createApp(db, hub), token: mintToken(db, 'Paul'), db, events };
}

function post(app: any, token: string, body: unknown) {
  return app.request('/v1/runs', {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
}

describe('POST /v1/runs', () => {
  it('ingests a finished run, marks PB, returns result, emits events', async () => {
    const { app, token, db, events } = ctx();
    const res = await post(app, token, { attempt_id: 'a1', course: 'Rainbow Road', cc: 150, status: 'finished', total_time: '1:50.000' });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toMatchObject({ is_pb: true, rank: 1 });
    expect((db.prepare("SELECT COUNT(*) c FROM runs WHERE provenance='live'").get() as any).c).toBe(1);
    expect(events.some(e => e.type === 'pb_achieved')).toBe(true);
    expect(events.some(e => e.type === 'run_finished')).toBe(true);
  });

  it('400s on an unknown course', async () => {
    const { app, token } = ctx();
    const res = await post(app, token, { attempt_id: 'b1', course: 'Nonexistent Track', status: 'finished', total_time: '1:00.000' });
    expect(res.status).toBe(400);
  });

  it('reset uploads silently (no events, stored)', async () => {
    const { app, token, events, db } = ctx();
    const res = await post(app, token, { attempt_id: 'r1', course: 'Rainbow Road', status: 'reset' });
    expect(res.status).toBe(200);
    expect(events.length).toBe(0);
    expect((db.prepare("SELECT status FROM runs WHERE attempt_id='r1'").get() as any).status).toBe('reset');
  });
});
```

- [ ] **Step 1b: Run it to verify it fails**

Run: `npx vitest run src/api/runs.test.ts`
Expected: FAIL — placeholder route returns `{ok:true}`, not the result/events.

- [ ] **Step 2: Create `pi/src/api/runs.ts`**

```ts
import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import type { EventHub } from './events';
import type { AttemptPayload } from '../db/types';
import { requireToken } from './auth';
import { activeSeasonId, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';
import { upsertRun } from '../db/ingest';
import { recomputeIsPb } from '../db/pb';
import { courseLeaderboard, currentWr } from '../db/reads';

export function runsRoutes(db: DatabaseSync, hub: EventHub): Hono<Env> {
  const r = new Hono<Env>();

  r.post('/v1/runs', requireToken(db), async (c) => {
    const playerId = c.get('playerId');
    const playerName = c.get('playerName');
    const p = (await c.req.json()) as AttemptPayload;
    if (!p?.attempt_id || !p?.course || !p?.status) return c.json({ error: 'bad payload' }, 400);
    const cc = p.cc ?? 150;
    const seasonId = activeSeasonId(db);
    const courseId = courseIdBySlug(db, slugify(p.course));
    if (courseId === null) return c.json({ error: `unknown course: ${p.course}` }, 400);

    const prevLeader = courseLeaderboard(db, seasonId, courseId, cc)[0] ?? null;
    const prevMine = db.prepare(
      'SELECT total_time_ms FROM runs WHERE season_id=? AND player_id=? AND course_id=? AND cc=? AND is_pb=1'
    ).get(seasonId, playerId, courseId, cc) as { total_time_ms: number } | undefined;
    const prevMineMs = prevMine ? prevMine.total_time_ms : null;
    upsertRun(db, p, playerId, seasonId);

    if (p.status !== 'finished') return c.json({ is_pb: false, rank: null, gap_to_leader_ms: null, gap_to_wr_ms: null });

    recomputeIsPb(db, seasonId, playerId, courseId, cc);
    const lb = courseLeaderboard(db, seasonId, courseId, cc);
    const mine = lb.find(x => x.player_id === playerId) ?? null;
    const newMineMs = mine ? mine.total_time_ms : null;
    const isPb = newMineMs !== null && (prevMineMs === null || newMineMs < prevMineMs);
    const wr = currentWr(db, courseId, cc);
    const leader = lb[0] ?? null;
    const result = {
      is_pb: isPb,
      rank: mine ? mine.rank : null,
      gap_to_leader_ms: mine && leader ? mine.total_time_ms - leader.total_time_ms : null,
      gap_to_wr_ms: mine && wr ? mine.total_time_ms - wr.record_ms : null,
    };

    hub.publish({ type: 'run_finished', player: playerName, course: p.course, cc, total_time: p.total_time ?? null, is_pb: isPb, rank: result.rank });
    if (isPb && p.total_time)
      hub.publish({ type: 'pb_achieved', player: playerName, course: p.course, cc, total_time: p.total_time,
        delta_vs_prev_ms: prevMineMs !== null && newMineMs !== null ? newMineMs - prevMineMs : null, rank: result.rank });
    if (leader && leader.player_id === playerId && prevLeader && prevLeader.player_id !== playerId && p.total_time)
      hub.publish({ type: 'lead_change', course: p.course, cc, new_leader: playerName, prev_leader: prevLeader.display_name, total_time: p.total_time });
    if (wr && mine && mine.total_time_ms < wr.record_ms && p.total_time)
      hub.publish({ type: 'wr_beaten', player: playerName, course: p.course, cc, total_time: p.total_time, wr_time: wr.record_str });

    return c.json(result);
  });

  r.post('/v1/runs/start', requireToken(db), async (c) => {
    const p = await c.req.json() as { course?: string; cc?: number };
    if (p?.course) hub.publish({ type: 'run_started', player: c.get('playerName'), course: p.course, cc: p.cc ?? 150 });
    return c.json({ ok: true });
  });

  return r;
}
```

- [ ] **Step 3: Create `pi/src/api/reads.ts`**

```ts
import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import { activeSeasonId, listSeasons, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';
import { courseLeaderboard, overallLeaderboard, friendsPbs, playerPbs, currentWr } from '../db/reads';

const num = (v: string | undefined, d: number) => (v ? Number(v) : d);

export function readsRoutes(db: DatabaseSync): Hono<Env> {
  const r = new Hono<Env>();
  const season = (c: any) => num(c.req.query('season'), activeSeasonId(db));
  const course = (c: any) => courseIdBySlug(db, slugify(c.req.query('course') ?? ''));

  r.get('/v1/leaderboard', (c) => {
    const cid = course(c); if (cid === null) return c.json({ error: 'unknown course' }, 400);
    return c.json(courseLeaderboard(db, season(c), cid, num(c.req.query('cc'), 150)));
  });
  r.get('/v1/leaderboard/overall', (c) => c.json(overallLeaderboard(db, season(c), num(c.req.query('cc'), 150))));
  r.get('/v1/friends-pbs', (c) => {
    const cid = course(c); if (cid === null) return c.json({ error: 'unknown course' }, 400);
    return c.json(friendsPbs(db, season(c), cid, num(c.req.query('cc'), 150)));
  });
  r.get('/v1/players/:id/pbs', (c) => c.json(playerPbs(db, season(c), Number(c.req.param('id')), num(c.req.query('cc'), 150))));
  r.get('/v1/world-records', (c) => {
    const cid = course(c); if (cid === null) return c.json({ error: 'unknown course' }, 400);
    return c.json(currentWr(db, cid, num(c.req.query('cc'), 150)));
  });
  r.get('/v1/seasons', (c) => c.json(listSeasons(db)));
  return r;
}
```

- [ ] **Step 4: Rewrite `pi/src/api/app.ts` to mount the routes**

```ts
import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import { EventHub } from './events';
import { runsRoutes } from './runs';
import { readsRoutes } from './reads';

export type Env = { Variables: { playerId: number; playerName: string } };

export function createApp(db: DatabaseSync, hub: EventHub): Hono<Env> {
  const app = new Hono<Env>();
  app.get('/health', (c) => c.json({ status: 'ok' }));
  app.route('/', runsRoutes(db, hub));
  app.route('/', readsRoutes(db));
  return app;
}
```

- [ ] **Step 5: Write the reads route test** `pi/src/api/reads.test.ts`

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { createApp } from './app';

function appWith() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,is_pb) VALUES (1,1,1,150,'finished','live',108000,'1:48.000',1)");
  return createApp(db, new EventHub());
}

describe('public reads (no token)', () => {
  it('GET /v1/leaderboard returns rows', async () => {
    const res = await appWith().request('/v1/leaderboard?course=Rainbow%20Road&cc=150');
    expect(res.status).toBe(200);
    expect((await res.json())[0].display_name).toBe('Paul');
  });
  it('GET /v1/seasons works', async () => {
    const res = await appWith().request('/v1/seasons');
    expect(res.status).toBe(200);
    expect((await res.json()).length).toBe(1);
  });
});
```

- [ ] **Step 6: Run all api tests to verify they pass**

Run: `npx vitest run src/api`
Expected: PASS (app.test + runs.test + reads.test).

- [ ] **Step 7: Commit**

```bash
git add pi/src/api/runs.ts pi/src/api/reads.ts pi/src/api/app.ts pi/src/api/runs.test.ts pi/src/api/reads.test.ts
git commit -m "feat(pi): /v1/runs ingest + event derivation + public reads" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: WebSocket event stream + server entry + mint-token CLI

**Files:** Modify `pi/src/api/app.ts` (WS route); create `pi/src/server.ts`, `pi/src/scripts/mintToken.ts`, `pi/README.md`; test `pi/src/api/ws.test.ts`.

- [ ] **Step 1: Write the failing WS test** `pi/src/api/ws.test.ts`

```ts
import { describe, it, expect } from 'vitest';
import { serve } from '@hono/node-server';
import { openDb, applySchema } from '../db/connect';
import { mintToken } from '../db/players';
import { EventHub } from './events';
import { createApp, makeWs } from './app';

function ctx() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1)");
  const hub = new EventHub();
  return { db, hub, app: createApp(db, hub), token: mintToken(db, 'Paul') };
}

describe('WS /v1/events', () => {
  it('delivers a derived event to a connected subscriber', async () => {
    const c = ctx();
    const { injectWebSocket } = makeWs(c.app, c.hub);
    const server = serve({ fetch: c.app.fetch, port: 0 });
    injectWebSocket(server);
    const addr = server.address() as { port: number };

    let wsClient!: WebSocket;
    const evt = await new Promise<any>((resolve) => {
      wsClient = new WebSocket(`ws://127.0.0.1:${addr.port}/v1/events`);
      wsClient.onmessage = (ev) => resolve(JSON.parse((ev as MessageEvent).data.toString()));
      wsClient.onopen = () => {
        fetch(`http://127.0.0.1:${addr.port}/v1/runs`, {
          method: 'POST',
          headers: { 'content-type': 'application/json', authorization: `Bearer ${c.token}` },
          body: JSON.stringify({ attempt_id: 'a1', course: 'Rainbow Road', status: 'finished', total_time: '1:50.000' }),
        });
      };
    });

    expect(['run_finished', 'pb_achieved']).toContain(evt.type);
    // Close the client + open connections first, else server.close()'s callback never fires.
    wsClient.close();
    server.closeAllConnections();
    await new Promise<void>((r) => server.close(() => r()));
  });
});
```

- [ ] **Step 1b: Run it to verify it fails**

Run: `npx vitest run src/api/ws.test.ts`
Expected: FAIL — no `makeWs` / no WS route.

- [ ] **Step 2: Add WS wiring to `pi/src/api/app.ts`**

Append to `app.ts`:

```ts
import { createNodeWebSocket } from '@hono/node-ws';

/** Attach the /v1/events WebSocket route. Returns { injectWebSocket } to call on the Node server. */
export function makeWs(app: Hono<Env>, hub: EventHub) {
  const { injectWebSocket, upgradeWebSocket } = createNodeWebSocket({ app });
  app.get('/v1/events', upgradeWebSocket(() => {
    let unsub = () => {};
    return {
      onOpen(_e: unknown, ws: { send: (data: string) => void }) {
        unsub = hub.subscribe((evt) => ws.send(JSON.stringify(evt)));
      },
      onClose() { unsub(); },
    };
  }));
  return { injectWebSocket };
}
```

`makeWs` takes the same `EventHub` instance passed to `createApp` (so WS subscribers receive the events the routes publish).

- [ ] **Step 3: Run the WS test to verify it passes**

Run: `npx vitest run src/api/ws.test.ts`
Expected: PASS.

- [ ] **Step 4: Create `pi/src/server.ts`**

```ts
import { serve } from '@hono/node-server';
import { openDb, applySchema } from './db/connect';
import { EventHub } from './api/events';
import { createApp, makeWs } from './api/app';

const DB_PATH = process.env.MKW_DB ?? 'mkw.db';
const PORT = Number(process.env.PORT ?? 8787);

const db = openDb(DB_PATH);
applySchema(db);
const hub = new EventHub();
const app = createApp(db, hub);
const { injectWebSocket } = makeWs(app, hub);
const server = serve({ fetch: app.fetch, port: PORT }, (info) => {
  console.log(`[pi] listening on http://127.0.0.1:${info.port}`);
});
injectWebSocket(server);
```

- [ ] **Step 5: Create `pi/src/scripts/mintToken.ts`**

```ts
import { openDb, applySchema } from '../db/connect';
import { mintToken } from '../db/players';

const name = process.argv[2];
if (!name) { console.error('usage: mint-token <player-display-name>'); process.exit(1); }
const db = openDb(process.env.MKW_DB ?? 'mkw.db');
applySchema(db);
const token = mintToken(db, name);
console.log(`Token for ${name} (store it now — not shown again):\n${token}`);
```

- [ ] **Step 6: Create `pi/README.md`**

```markdown
# pi/ — MKW server (sub-project B, Phase 1)

TypeScript/Node (Hono) HTTP API + WS event hub over the sub-project A SQLite store.
Spec: `docs/superpowers/specs/2026-06-04-server-api-and-sync-design.md`.

## Dev
    npm install
    npm test                 # vitest
    npm run dev              # serve on :8787 (MKW_DB=path PORT=n to override)
    npm run mint-token Paul  # issue a player token (printed once)

Uses Node's built-in `node:sqlite` (run scripts pass `--no-warnings`). The DB is
created/seeded by A's importer (`python -m server.importer`); `MKW_DB` points the
server at it.

## Out of scope here
Client write path (engine `run_finalized` + `src-tauri/src/sync.rs`) — separate plan.
The web/overlays (C). Live in-progress telemetry. WR scraper.
```

- [ ] **Step 7: Smoke-test the server manually**

Run (from `pi/`, terminal 1): `npm run dev`
Run (terminal 2): `curl -s http://127.0.0.1:8787/health`
Expected: `{"status":"ok"}`. Stop the server (Ctrl-C).

- [ ] **Step 8: Run the whole suite**

Run: `npx vitest run`
Expected: PASS (all db + api tests).

- [ ] **Step 9: Commit**

```bash
git add pi/src/api/app.ts pi/src/api/ws.test.ts pi/src/server.ts pi/src/scripts/mintToken.ts pi/README.md
git commit -m "feat(pi): WS event stream + server entry + mint-token CLI" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the executor

- **Run from `pi/`.** `npm install` first (no native build — `node:sqlite` is built in).
- **`node:sqlite` prints an `ExperimentalWarning`** under vitest — harmless. Run scripts already pass `--no-warnings`.
- **Schema is A's `server/schema.sql`** (single source); Tasks 2 + 4 add `auth_token_hash` and `attempt_id` to it. After each schema edit, re-run A's Python tests (`python -m pytest tests/test_server_importer.py -q`) to confirm the importer is unaffected.
- **Don't add `packages/shared` yet** — extract shared types when C (the web app) needs to import them.
- This plan is **Phase-1 server only**. The client write path (engine `run_finalized` emit + `src-tauri/src/sync.rs` outbox/uploader + Sync settings tab) is a separate plan, written next.
```

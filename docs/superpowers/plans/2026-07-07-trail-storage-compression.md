# Trail Storage Compression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Pi's one-row-per-point `run_points` table with per-run compressed blobs (`run_trails`), losslessly — every point bit-exact — for a measured ~4× storage reduction.

**Architecture:** A pure codec (`trailCodec.ts`: varint Δt + lap RLE + byte-plane-transposed float64 columns + brotli q11) under a thin DB access layer (`trails.ts`: `insertTrail`/`getRunPoints`). All five per-run readers and both writers switch to the access layer; the one SQL aggregate (`MAX(t_ms)`) becomes a stored `max_t_ms` column. A resumable boot migration converts existing rows run-by-run, bit-verifying each before deleting its rows, and drops `run_points` when empty.

**Tech Stack:** Node 24 (`node:sqlite` `DatabaseSync`, `node:zlib` brotli — NO zstd), TypeScript run via tsx (no build), vitest (colocated `*.test.ts`), Hono (untouched).

**Spec:** `docs/superpowers/specs/2026-07-07-trail-storage-compression-design.md` — read it first.

## Global Constraints

- **Losslessness is bit-exact:** decoded `cx`/`cy`/`score` must have identical float64 BITS (compare via `Buffer.writeDoubleLE` bytes, never `==` alone), identical `t_ms`/`lap`, identical `ORDER BY t_ms` sequence. The values are full-mantissa doubles — no arithmetic may ever touch them; move bytes only.
- **Scope:** Pi server (`pi/`), `server/schema.sql`, `server/reset_season0.py`, docs. Do NOT touch `mkw_tracker/`, `src/`, `src-tauri/`, `web/`. Upload wire format and every API response shape stay unchanged.
- Blob payload layout is **pinned by the spec** (varint = unsigned LEB128, low group first; byte planes ordered LSB→MSB of the little-endian f64; brotli quality 11): `codec` column value `1`.
- Runaway guard unchanged: trails with `max t_ms > OVER_LIMIT_MS` (11 min) are not stored; boundary inclusive.
- Empty trails are never stored (no `run_trails` row) — matches today's "no rows" semantics.
- npm/vitest commands run from `pi/`; `git` commands run from the **repo root** (commit paths are root-relative). Tests: `npx vitest run <file>`; full suite `npm test`; keep `npm run typecheck` clean (non-gating but required here).
- Commit after every task (messages given per task). Work on branch `trail-compression` off `main`.
- **STOP at Task 10's user checkpoint** — the user must eye-confirm the rehearsal before anything is deployed. Never run the migration or VACUUM against a non-copy database.

## File Map

| File | Role |
|---|---|
| `pi/src/db/trailCodec.ts` (new) | Pure codec: pack/unpack, encode/decode (brotli), bit-equality assert. No DB. |
| `pi/src/db/trails.ts` (new) | DB access: `insertTrail`, `getRunPoints` (blob-first, legacy-rows fallback). |
| `pi/src/db/trailMigrate.ts` (new) | One-time resumable `run_points` → `run_trails` boot migration. |
| `pi/src/scripts/migrateTrailsCli.ts` (new) | Manual migration CLI (`npm run migrate-trails`), optional `--vacuum`. |
| `pi/src/scripts/diffTrails.ts` (new) | Bit-compares every run's trail between two DBs (`npm run diff-trails`). |
| `server/schema.sql` | + `run_trails` DDL (Task 2); − `run_points` DDL + index (Task 8). |
| `pi/src/db/ingest.ts` | Writers → `insertTrail`; malformed trail dropped loudly, run kept. |
| `pi/src/db/reads.ts`, `db/courseModels.ts`, `presence/pace.ts`, `stats/completion.ts` | Readers → `getRunPoints`; `EXISTS(run_points)` → `EXISTS(run_trails)`. |
| `pi/src/stats/resolve.ts` + `stats/metrics.ts` | `POINTS_JOIN` → `run_trails`; `SUM(pt.maxt)` → `SUM(pt.max_t_ms)`. |
| `pi/src/server.ts` | Call `migrateTrails(db)` after the other one-time migrations. |
| `pi/src/scripts/wipeRuns.ts`, `server/reset_season0.py` | Ops table lists / integrity guards ported. |
| `pi/package.json` | Two new script entries. |
| Tests | `trailCodec.test.ts`, `trails.test.ts`, `trailMigrate.test.ts` (new); ports in `schema.test.ts`, `ingest.test.ts`, `db/reads.test.ts`, `api/reads.test.ts`, `courseModels.test.ts`, `pace.test.ts`, `stats/completion.test.ts`. |
| Docs | `docs/replay-format.md`, `docs/database-schema.md`, `docs/pi-deploy.md`, root `CLAUDE.md`. |

---

### Task 0: Branch

- [ ] **Step 1:** `git checkout -b trail-compression main`

---

### Task 1: Trail codec (pure, no DB)

**Files:**
- Create: `pi/src/db/trailCodec.ts`
- Test: `pi/src/db/trailCodec.test.ts`

**Interfaces:**
- Produces: `type TrailPoint = { t_ms: number; cx: number; cy: number; score: number; lap: number | null }`; `CODEC_BROTLI_V1 = 1`; `packTrail(pts: TrailPoint[]): Buffer`; `unpackTrail(buf: Buffer): TrailPoint[]`; `encodeTrail(pts: TrailPoint[]): Buffer`; `decodeTrail(data: Uint8Array): TrailPoint[]`; `assertTrailsIdentical(a: TrailPoint[], b: TrailPoint[]): void` (throws on first mismatch). Every later task consumes these exact names.

- [ ] **Step 1: Write the failing test** — `pi/src/db/trailCodec.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { assertTrailsIdentical, decodeTrail, encodeTrail, packTrail, unpackTrail, type TrailPoint } from './trailCodec';

// Hand-computed golden vector — pins varint flavour (unsigned LEB128) and byte-plane
// order (LSB→MSB) so a reimplementation can't silently change the format.
const GOLDEN_PTS: TrailPoint[] = [
  { t_ms: 0, cx: 1.5, cy: -2.5, score: 0.5, lap: null },
  { t_ms: 40, cx: 1.5, cy: -2.5, score: 0.5, lap: 1 },
];
// head: n=2 | flags=0 | t0=0 | Δ=40 | lapRLE (null,1)(1,1) = ff 01 01 01
// planes: 1.5→…f8 3f, -2.5→…04 c0, 0.5→…e0 3f (each: 12 zero bytes then 2+2 plane bytes)
const GOLDEN_HEX =
  '02000028ff010101' +
  '000000000000000000000000f8f83f3f' +
  '0000000000000000000000000404c0c0' +
  '000000000000000000000000e0e03f3f';

// deterministic PRNG so failures reproduce (Math.random forbidden in fixtures)
function rnd(seed: number) { let s = seed >>> 0; return () => (s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32; }
function randomTrail(n: number, seed: number): TrailPoint[] {
  const r = rnd(seed); const pts: TrailPoint[] = []; let t = Math.floor(r() * 1000);
  for (let i = 0; i < n; i++) {
    t += 1 + Math.floor(r() * 9000);
    pts.push({ t_ms: t, cx: (r() - 0.5) * 4000, cy: (r() - 0.5) * 4000,
               score: r(), lap: r() < 0.1 ? null : 1 + Math.floor(r() * 7) });
  }
  return pts;
}

describe('trailCodec', () => {
  it('packTrail matches the golden vector', () => {
    expect(packTrail(GOLDEN_PTS).toString('hex')).toBe(GOLDEN_HEX);
  });

  it('unpackTrail inverts the golden vector', () => {
    assertTrailsIdentical(unpackTrail(Buffer.from(GOLDEN_HEX, 'hex')), GOLDEN_PTS);
  });

  it('encode→decode round-trips random trails bit-exactly (sizes cross varint boundaries)', () => {
    for (const [n, seed] of [[1, 1], [2, 2], [257, 3], [5000, 4]] as const)
      assertTrailsIdentical(decodeTrail(encodeTrail(randomTrail(n, seed))), randomTrail(n, seed));
  });

  it('round-trips full-entropy EMA-like doubles bit-exactly', () => {
    let cx = 960.0, cy = 540.0; const pts: TrailPoint[] = [];
    for (let i = 0; i < 2000; i++) {
      cx += 0.35 * ((((i * 7919) % 100) - 50) - cx * 0.001);
      cy += 0.35 * ((((i * 104729) % 100) - 50) - cy * 0.001);
      pts.push({ t_ms: i * 40, cx, cy, score: 0.4 + 0.6 / (1 + (i % 13)), lap: 1 + Math.floor(i / 500) });
    }
    assertTrailsIdentical(decodeTrail(encodeTrail(pts)), pts);
  });

  it('rejects malformed trails instead of packing them', () => {
    const p = (over: Partial<TrailPoint>): TrailPoint[] => [
      { t_ms: 0, cx: 1, cy: 2, score: 0.5, lap: 1 },
      { t_ms: 40, cx: 1, cy: 2, score: 0.5, lap: 1, ...over } as TrailPoint,
    ];
    expect(() => packTrail([])).toThrow('empty');
    expect(() => packTrail(p({ t_ms: 0 }))).toThrow('strictly increasing');   // duplicate t
    expect(() => packTrail(p({ t_ms: -5 }))).toThrow();                       // negative
    expect(() => packTrail(p({ t_ms: 40.5 }))).toThrow();                     // non-integer
    expect(() => packTrail(p({ lap: 255 }))).toThrow('bad lap');
    expect(() => packTrail(p({ cx: Infinity }))).toThrow('finite');
  });
});
```

- [ ] **Step 2: Run to verify it fails** — `npx vitest run src/db/trailCodec.test.ts` → FAIL (cannot resolve `./trailCodec`).

- [ ] **Step 3: Implement** — `pi/src/db/trailCodec.ts`:

```ts
// Lossless at-rest trail codec (v1). Spec: docs/superpowers/specs/2026-07-07-trail-storage-compression-design.md
// The floats are full-mantissa doubles; they are moved as BYTES only — no arithmetic ever
// touches them, so bit-exactness is structural. NaN/Inf cannot exist in stored trails
// (JSON can't carry them and SQLite turns NaN into NULL against a NOT NULL column), so
// packTrail treats non-finite input as malformed.
import * as zlib from 'node:zlib';

/** One trail point, exactly the row shape readers consume today. */
export type TrailPoint = { t_ms: number; cx: number; cy: number; score: number; lap: number | null };

/** `run_trails.codec` value: v1 packed payload, brotli-compressed. */
export const CODEC_BROTLI_V1 = 1;

const LAP_NULL = 255;

function pushVarint(out: number[], v: number): void {
  if (!Number.isSafeInteger(v) || v < 0) throw new Error(`varint out of range: ${v}`);
  while (v > 127) { out.push((v & 127) | 128); v = Math.floor(v / 128); }
  out.push(v);
}

function readVarint(buf: Buffer, pos: { i: number }): number {
  let v = 0, shift = 1, b: number;
  do {
    b = buf[pos.i++];
    if (b === undefined) throw new Error('varint past end of buffer');
    v += (b & 127) * shift; shift *= 128;
  } while (b & 128);
  if (!Number.isSafeInteger(v)) throw new Error('varint overflow');
  return v;
}

/** n float64s → byte-plane-transposed buffer: plane k holds byte k (LSB→MSB) of every value. */
function shuffleF64(vals: number[]): Buffer {
  const n = vals.length, flat = Buffer.alloc(n * 8), out = Buffer.alloc(n * 8);
  for (let i = 0; i < n; i++) flat.writeDoubleLE(vals[i], i * 8);
  for (let i = 0; i < n; i++) for (let k = 0; k < 8; k++) out[k * n + i] = flat[i * 8 + k];
  return out;
}

function unshuffleF64(buf: Buffer, n: number): number[] {
  const flat = Buffer.alloc(n * 8);
  for (let i = 0; i < n; i++) for (let k = 0; k < 8; k++) flat[i * 8 + k] = buf[k * n + i];
  const out = new Array<number>(n);
  for (let i = 0; i < n; i++) out[i] = flat.readDoubleLE(i * 8);
  return out;
}

/** Uncompressed v1 payload. Throws on malformed input (n=0, non-monotonic/negative/
 *  non-integer t_ms, lap outside null∪[0,254], non-finite floats) rather than pack it. */
export function packTrail(pts: TrailPoint[]): Buffer {
  if (pts.length === 0) throw new Error('empty trail');
  const head: number[] = [];
  pushVarint(head, pts.length);
  head.push(0);                                       // flags: reserved, always 0 in v1
  let prev = -1;
  for (const p of pts) {
    if (!Number.isSafeInteger(p.t_ms) || p.t_ms < 0) throw new Error(`bad t_ms: ${p.t_ms}`);
    if (p.t_ms <= prev) throw new Error(`t_ms not strictly increasing at ${p.t_ms}`);
    if (!Number.isFinite(p.cx) || !Number.isFinite(p.cy) || !Number.isFinite(p.score))
      throw new Error('non-finite coordinate/score');
    pushVarint(head, prev < 0 ? p.t_ms : p.t_ms - prev);
    prev = p.t_ms;
  }
  let i = 0;
  while (i < pts.length) {                            // lap RLE: (u8 value, varint count)
    const v = pts[i].lap;
    if (v !== null && (!Number.isInteger(v) || v < 0 || v > 254)) throw new Error(`bad lap: ${v}`);
    let j = i;
    while (j < pts.length && pts[j].lap === v) j++;
    head.push(v === null ? LAP_NULL : v);
    pushVarint(head, j - i);
    i = j;
  }
  return Buffer.concat([
    Buffer.from(head),
    shuffleF64(pts.map((p) => p.cx)),
    shuffleF64(pts.map((p) => p.cy)),
    shuffleF64(pts.map((p) => p.score)),
  ]);
}

export function unpackTrail(buf: Buffer): TrailPoint[] {
  const pos = { i: 0 };
  const n = readVarint(buf, pos);
  if (n === 0) throw new Error('empty trail blob');
  pos.i++;                                            // flags (reserved)
  const t = new Array<number>(n);
  for (let k = 0; k < n; k++) t[k] = k === 0 ? readVarint(buf, pos) : t[k - 1] + readVarint(buf, pos);
  const lap = new Array<number | null>(n);
  let filled = 0;
  while (filled < n) {
    const byte = buf[pos.i++];
    if (byte === undefined) throw new Error('lap RLE past end of buffer');
    const count = readVarint(buf, pos);
    if (filled + count > n) throw new Error('lap RLE overrun');
    const v = byte === LAP_NULL ? null : byte;
    for (let k = 0; k < count; k++) lap[filled++] = v;
  }
  if (buf.length !== pos.i + n * 24) throw new Error(`blob length ${buf.length} != expected ${pos.i + n * 24}`);
  const cx = unshuffleF64(buf.subarray(pos.i, pos.i + n * 8), n);
  const cy = unshuffleF64(buf.subarray(pos.i + n * 8, pos.i + n * 16), n);
  const score = unshuffleF64(buf.subarray(pos.i + n * 16, pos.i + n * 24), n);
  return t.map((t_ms, k) => ({ t_ms, cx: cx[k], cy: cy[k], score: score[k], lap: lap[k] }));
}

export function encodeTrail(pts: TrailPoint[]): Buffer {
  const packed = packTrail(pts);
  return zlib.brotliCompressSync(packed, { params: {
    [zlib.constants.BROTLI_PARAM_QUALITY]: 11,
    [zlib.constants.BROTLI_PARAM_SIZE_HINT]: packed.length,
  } });
}

export function decodeTrail(data: Uint8Array): TrailPoint[] {
  return unpackTrail(zlib.brotliDecompressSync(data));
}

/** Bit-exact equality gate (float64 BITS, not ==). Throws at the first mismatch. */
export function assertTrailsIdentical(a: TrailPoint[], b: TrailPoint[]): void {
  if (a.length !== b.length) throw new Error(`length ${a.length} != ${b.length}`);
  const ba = Buffer.alloc(8), bb = Buffer.alloc(8);
  for (let i = 0; i < a.length; i++) {
    if (a[i].t_ms !== b[i].t_ms || a[i].lap !== b[i].lap) throw new Error(`t/lap mismatch at ${i}`);
    for (const f of ['cx', 'cy', 'score'] as const) {
      ba.writeDoubleLE(a[i][f], 0); bb.writeDoubleLE(b[i][f], 0);
      if (!ba.equals(bb)) throw new Error(`${f} bits mismatch at ${i}`);
    }
  }
}
```

- [ ] **Step 4: Run to verify pass** — `npx vitest run src/db/trailCodec.test.ts` → all PASS.
- [ ] **Step 5: Typecheck** — `npm run typecheck` → clean.
- [ ] **Step 6: Commit**

```bash
git add pi/src/db/trailCodec.ts pi/src/db/trailCodec.test.ts
git commit -m "feat(pi): lossless trail codec v1 (varint dt + lap RLE + byte-shuffled f64 + brotli)"
```

---

### Task 2: `run_trails` schema

**Files:**
- Modify: `server/schema.sql` (after the `run_laps` block, ~line 64; KEEP `run_points` + its index for now — removed in Task 8)
- Modify: `pi/src/db/schema.test.ts:4`

**Interfaces:**
- Produces: table `run_trails(run_id INTEGER PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE, codec INTEGER NOT NULL, n INTEGER NOT NULL, max_t_ms INTEGER NOT NULL, data BLOB NOT NULL)`. All later tasks rely on these exact column names.

- [ ] **Step 1: Failing test** — in `pi/src/db/schema.test.ts` line 4, add `'run_trails'` to `TABLES`:

```ts
const TABLES = ['seasons','players','season_rosters','courses','runs','run_laps','run_points','run_trails','world_records','ghost_imports'];
```

- [ ] **Step 2: Run** — `npx vitest run src/db/schema.test.ts` → FAIL (`run_trails` missing).
- [ ] **Step 3: Implement** — in `server/schema.sql`, directly after the `run_laps` CREATE block (before `run_points`), insert:

```sql
CREATE TABLE IF NOT EXISTS run_trails (
    run_id   INTEGER PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    codec    INTEGER NOT NULL,          -- 1 = v1 packed payload, brotli-compressed
    n        INTEGER NOT NULL,          -- point count (SQL-visible)
    max_t_ms INTEGER NOT NULL,          -- final t_ms (SQL-visible; replaces MAX(t_ms) scans)
    data     BLOB NOT NULL
);
```

- [ ] **Step 4: Run** — `npx vitest run src/db/schema.test.ts` → PASS.
- [ ] **Step 5: Commit**

```bash
git add server/schema.sql pi/src/db/schema.test.ts
git commit -m "feat(schema): run_trails per-run compressed trail blobs (run_points kept until migration)"
```

---

### Task 3: DB access layer

**Files:**
- Create: `pi/src/db/trails.ts`
- Test: `pi/src/db/trails.test.ts`

**Interfaces:**
- Consumes: Task 1 codec, Task 2 table.
- Produces: `insertTrail(db: DatabaseSync, runId: number, pts: TrailPoint[]): void` (throws on malformed/empty `pts` — caller decides policy); `getRunPoints(db: DatabaseSync, runId: number): TrailPoint[]` (`[]` when the run has no trail). All reader/writer tasks use exactly these.

- [ ] **Step 1: Failing test** — `pi/src/db/trails.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { getRunPoints, insertTrail } from './trails';
import type { TrailPoint } from './trailCodec';

// Self-contained legacy DDL: schema.sql stops creating run_points in a later task,
// so the fallback tests build it themselves (IF NOT EXISTS keeps this valid both ways).
const LEGACY_DDL = `CREATE TABLE IF NOT EXISTS run_points (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    t_ms INTEGER NOT NULL, cx REAL NOT NULL, cy REAL NOT NULL,
    score REAL NOT NULL DEFAULT 1.0, lap INTEGER);`;

function base() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  db.exec("INSERT INTO runs(id,attempt_id,season_id,player_id,course_id,cc,status,provenance) VALUES (10,'a',1,1,1,150,'finished','live')");
  return db;
}

const PTS: TrailPoint[] = [
  { t_ms: 0, cx: 100.25, cy: 200.5, score: 0.9, lap: 1 },
  { t_ms: 40, cx: 101.125, cy: 201.75, score: 0.95, lap: null },
];

describe('insertTrail / getRunPoints', () => {
  it('round-trips a trail and fills codec/n/max_t_ms', () => {
    const db = base();
    insertTrail(db, 10, PTS);
    expect(getRunPoints(db, 10)).toEqual(PTS);
    expect(db.prepare('SELECT codec, n, max_t_ms FROM run_trails WHERE run_id=10').get())
      .toEqual({ codec: 1, n: 2, max_t_ms: 40 });
  });

  it('returns [] for a run with no trail', () => {
    expect(getRunPoints(base(), 10)).toEqual([]);
  });

  it('falls back to legacy run_points rows while that table exists', () => {
    const db = base();
    db.exec(LEGACY_DDL);
    db.exec('INSERT INTO run_points(run_id,t_ms,cx,cy,score,lap) VALUES (10,0,1.5,2.5,0.9,1),(10,16,1.75,2.25,0.8,NULL)');
    expect(getRunPoints(db, 10)).toEqual([
      { t_ms: 0, cx: 1.5, cy: 2.5, score: 0.9, lap: 1 },
      { t_ms: 16, cx: 1.75, cy: 2.25, score: 0.8, lap: null },
    ]);
  });

  it('returns [] when neither a blob nor the legacy table exists', () => {
    const db = base();
    db.exec('DROP TABLE IF EXISTS run_points');
    expect(getRunPoints(db, 10)).toEqual([]);
  });

  it('blob cascades away when its run is deleted', () => {
    const db = base();
    insertTrail(db, 10, PTS);
    db.exec('DELETE FROM runs WHERE id=10');
    expect((db.prepare('SELECT COUNT(*) c FROM run_trails').get() as { c: number }).c).toBe(0);
  });
});
```

- [ ] **Step 2: Run** — `npx vitest run src/db/trails.test.ts` → FAIL (cannot resolve `./trails`).
- [ ] **Step 3: Implement** — `pi/src/db/trails.ts`:

```ts
import type { DatabaseSync } from 'node:sqlite';
import { CODEC_BROTLI_V1, decodeTrail, encodeTrail, type TrailPoint } from './trailCodec';

/** Encode + store a run's trail. Throws on a malformed/empty trail (see packTrail);
 *  the caller decides drop-vs-fail policy. */
export function insertTrail(db: DatabaseSync, runId: number, pts: TrailPoint[]): void {
  const data = encodeTrail(pts);
  db.prepare('INSERT INTO run_trails(run_id, codec, n, max_t_ms, data) VALUES (?,?,?,?,?)')
    .run(runId, CODEC_BROTLI_V1, pts.length, pts[pts.length - 1].t_ms, data);
}

/** A run's full trail in t order — decoded blob, or [] when the run has no trail.
 *  While the legacy run_points table still exists (interrupted-migration window only),
 *  falls back to reading its rows; the fallback dead-codes once the table is dropped. */
export function getRunPoints(db: DatabaseSync, runId: number): TrailPoint[] {
  const row = db.prepare('SELECT codec, data FROM run_trails WHERE run_id=?').get(runId) as
    { codec: number; data: Uint8Array } | undefined;
  if (row) {
    if (row.codec !== CODEC_BROTLI_V1) throw new Error(`unknown trail codec ${row.codec} for run ${runId}`);
    return decodeTrail(row.data);
  }
  try {
    return db.prepare('SELECT t_ms, cx, cy, score, lap FROM run_points WHERE run_id=? ORDER BY t_ms')
      .all(runId) as TrailPoint[];
  } catch {
    return [];   // run_points gone (normal post-migration state) → genuinely trail-less run
  }
}
```

- [ ] **Step 4: Run** — `npx vitest run src/db/trails.test.ts` → PASS. Then `npm run typecheck` → clean.
- [ ] **Step 5: Commit**

```bash
git add pi/src/db/trails.ts pi/src/db/trails.test.ts
git commit -m "feat(pi): trail access layer (insertTrail/getRunPoints, legacy-rows fallback)"
```

---

### Task 4: Ingest writes blobs

**Files:**
- Modify: `pi/src/db/ingest.ts` (points loop in `upsertRun` ~lines 50–57; `hasPts` + points loop in `enrichRunFromGhost` ~lines 106–114)
- Test: `pi/src/db/ingest.test.ts`

**Interfaces:**
- Consumes: `insertTrail`, `getRunPoints`, `TrailPoint`.
- Produces: unchanged exports (`upsertRun`, `enrichRunFromGhost`, `OVER_LIMIT_MS`); trails now land in `run_trails`.

- [ ] **Step 1: Update tests to blob expectations** — in `pi/src/db/ingest.test.ts`:

Add imports after line 3:

```ts
import { getRunPoints, insertTrail } from './trails';
```

Replace the run_points assertions:
- Line 52 (`inserts a live finished run…`): replace the `run_points` COUNT expect with:

```ts
    expect((db.prepare('SELECT COUNT(*) c FROM run_trails WHERE run_id=?').get(runId) as any).c).toBe(1);
    expect(getRunPoints(db, runId).length).toBe(2);
```

- Lines 61–62 (lap-stamp test): replace the SELECT + expect with:

```ts
    expect(getRunPoints(db, runId).map((r) => r.lap)).toEqual([1, 2, null]);
```

- Line 103 (runaway test): replace with `expect(getRunPoints(db, runId)).toEqual([]);`
- Line 112 (boundary test): replace with `expect(getRunPoints(db, runId).length).toBe(2);`
- Line 141 (ghost enrich adds trail): replace with `expect(getRunPoints(db, 50).length).toBe(1);`
- Lines 166 + 172–173 (`enrich keeps an existing trail`): replace the raw `INSERT INTO run_points` exec with:

```ts
    insertTrail(db, 61, [{ t_ms: 0, cx: 5, cy: 5, score: 1, lap: 1 }]);
```

and the two final expects with:

```ts
    expect(getRunPoints(db, 61).length).toBe(1);
    expect(getRunPoints(db, 61)[0].cx).toBe(5);  // original intact
```

Append a new test inside `describe('upsertRun', …)`:

```ts
  it('drops a malformed (non-monotonic t) trail but keeps the run', () => {
    const db = base();
    const runId = upsertRun(db, {
      attempt_id: 'm1', course: 'Rainbow Road', status: 'reset',
      points: [[100, 1, 2, 0.9, 1], [100, 1.1, 2.1, 0.9, 1]],   // duplicate t_ms
    } as any, 1, 1);
    expect((db.prepare('SELECT COUNT(*) c FROM runs WHERE id=?').get(runId) as any).c).toBe(1);
    expect(getRunPoints(db, runId)).toEqual([]);
  });
```

- [ ] **Step 2: Run** — `npx vitest run src/db/ingest.test.ts` → FAIL (`run_trails` COUNT is 0; malformed trail currently stored via legacy-rows fallback).
- [ ] **Step 3: Implement** — in `pi/src/db/ingest.ts`:

Add imports after line 3:

```ts
import { insertTrail } from './trails';
import type { TrailPoint } from './trailCodec';
```

Add above `upsertRun`:

```ts
/** Store a payload trail as a run_trails blob. A malformed trail (e.g. non-monotonic
 *  t_ms — the engine never emits one) is dropped like a runaway: the run + laps
 *  persist, the trail doesn't, with a loud log. Returns whether a trail was stored. */
function storeTrail(db: DatabaseSync, runId: number, pts: NonNullable<AttemptPayload['points']>): boolean {
  const rows: TrailPoint[] = pts.map(([t, cx, cy, score, lap]) => ({ t_ms: t, cx, cy, score, lap: lap ?? null }));
  try {
    insertTrail(db, runId, rows);
    return true;
  } catch (e) {
    console.error(`[ingest] dropping malformed trail for run ${runId}:`, e);
    return false;
  }
}
```

In `upsertRun`, replace the points block (keep the runaway comment, lines ~50–57):

```ts
    // Runaway guard: a recording past OVER_LIMIT_MS is a stuck capture — keep the run + laps,
    // but don't store its trail. Boundary inclusive (exactly at the limit is still stored).
    const pts = p.points ?? [];
    const maxT = pts.reduce((m, pt) => Math.max(m, pt[0]), 0);
    if (pts.length > 0 && maxT <= OVER_LIMIT_MS) storeTrail(db, runId, pts);
```

In `enrichRunFromGhost`, replace the `hasPts`/points block (~lines 106–114):

```ts
    const hasPts = (db.prepare('SELECT COUNT(*) c FROM run_trails WHERE run_id=?').get(runId) as { c: number }).c > 0;
    const pts = p.points ?? [];
    const maxT = pts.reduce((m, pt) => Math.max(m, pt[0]), 0);
    let trailAdded = false;
    if (!hasPts && pts.length > 0 && maxT <= OVER_LIMIT_MS) trailAdded = storeTrail(db, runId, pts);
```

- [ ] **Step 4: Run** — `npx vitest run src/db/ingest.test.ts` → PASS. `npm run typecheck` → clean.
- [ ] **Step 5: Commit**

```bash
git add pi/src/db/ingest.ts pi/src/db/ingest.test.ts
git commit -m "feat(pi): ingest stores trails as run_trails blobs (malformed trails dropped loudly)"
```

---

### Task 5: Trail-serving readers (`db/reads.ts`)

**Files:**
- Modify: `pi/src/db/reads.ts` (`courseTrails` ~97–107, `playerTrails` ~194–221)
- Test: `pi/src/db/reads.test.ts` (fixtures ~79–80, ~121), `pi/src/api/reads.test.ts` (fixtures ~58, ~115)

**Interfaces:**
- Consumes: `getRunPoints`, `insertTrail`.
- Produces: `courseTrails` / `playerTrails` signatures and response shapes unchanged.

- [ ] **Step 1: Port fixtures to blobs** — in `pi/src/db/reads.test.ts` add `import { insertTrail } from './trails';` after the existing imports, then replace lines 79–80 (`seededTrails`) with:

```ts
    insertTrail(db, 10, [
      { t_ms: 0, cx: 100, cy: 200, score: 0.9, lap: null },
      { t_ms: 16, cx: 101, cy: 201, score: 0.95, lap: null },
    ]);
    insertTrail(db, 20, [{ t_ms: 0, cx: 300, cy: 400, score: 0.8, lap: null }]);
```

and line 121 (`db5`) with:

```ts
    insertTrail(db, 10, [{ t_ms: 0, cx: 1, cy: 1, score: 0.9, lap: null }]);
    insertTrail(db, 20, [{ t_ms: 0, cx: 2, cy: 2, score: 0.9, lap: null }]);
    insertTrail(db, 30, [{ t_ms: 0, cx: 3, cy: 3, score: 0.9, lap: null }]);
```

In `pi/src/api/reads.test.ts` add `import { insertTrail } from '../db/trails';`, replace line 58 (`trailsDb`) with:

```ts
  insertTrail(db, 10, [{ t_ms: 0, cx: 100, cy: 200, score: 0.9, lap: null }]);
  insertTrail(db, 20, [{ t_ms: 0, cx: 300, cy: 400, score: 0.8, lap: null }]);
```

and line 115 (`/v1/players/:id/trails` fixture) with:

```ts
    insertTrail(db, 10, [{ t_ms: 0, cx: 1, cy: 1, score: 0.9, lap: null }]);
    insertTrail(db, 20, [{ t_ms: 0, cx: 2, cy: 2, score: 0.9, lap: null }]);
```

- [ ] **Step 2: Run** — `npx vitest run src/db/reads.test.ts src/api/reads.test.ts` → trail tests FAIL (readers still SELECT the now-empty `run_points`).
- [ ] **Step 3: Implement** — in `pi/src/db/reads.ts` add `import { getRunPoints } from './trails';` after the existing imports.

In `courseTrails` delete the `ptStmt` line (97) and replace the loop body's fetch (99):

```ts
  for (const r of runs) {
    const pts = getRunPoints(db, r.id);
    if (pts.length === 0) continue;   // legacy / point-less PB: no trail
```

In `playerTrails` change the `base` EXISTS clause (~196) to:

```ts
                  AND EXISTS (SELECT 1 FROM run_trails rt WHERE rt.run_id = runs.id)`;
```

delete the `ptStmt` line (~213) and replace the loop fetch (~216):

```ts
  for (const r of rows) {
    const pts = getRunPoints(db, r.id);
    if (pts.length === 0) continue;   // legacy / point-less run: no trail
```

(The `pts.map((p) => [p.t_ms, p.cx, p.cy, p.score])` lines stay exactly as they are.)

- [ ] **Step 4: Run** — `npx vitest run src/db/reads.test.ts src/api/reads.test.ts` → PASS. `npm run typecheck` → clean.
- [ ] **Step 5: Commit**

```bash
git add pi/src/db/reads.ts pi/src/db/reads.test.ts pi/src/api/reads.test.ts
git commit -m "feat(pi): courseTrails/playerTrails read run_trails blobs (shapes unchanged)"
```

---

### Task 6: Analytics readers + `driving_time`

**Files:**
- Modify: `pi/src/db/courseModels.ts` (~43–46), `pi/src/presence/pace.ts` (~62–64), `pi/src/stats/completion.ts` (~54, 66), `pi/src/stats/resolve.ts:16`, `pi/src/stats/metrics.ts:57`
- Test: `pi/src/db/courseModels.test.ts` (~45–49), `pi/src/presence/pace.test.ts` (~56–57), `pi/src/stats/completion.test.ts` (~32–34)

**Interfaces:**
- Consumes: `getRunPoints`, `insertTrail`, `TrailPoint`.
- Produces: all function signatures unchanged; `driving_time` metric now reads `run_trails.max_t_ms`.

- [ ] **Step 1: Port fixtures** —

`pi/src/db/courseModels.test.ts`: add `import { insertTrail } from './trails';` and `import type { TrailPoint } from './trailCodec';`; replace lines 45–49 (the `pts` prepared-statement loop) with:

```ts
    const pts: TrailPoint[] = [];
    for (let i = 0; i < 600; i++) {        // two 30s laps around a circle
      const t = i * 100, lap = t < 30000 ? 1 : 2, f = (t % 30000) / 30000;
      pts.push({ t_ms: t, cx: 200 + 100 * Math.cos(2 * Math.PI * f),
                 cy: 200 + 100 * Math.sin(2 * Math.PI * f), score: 1, lap });
    }
    insertTrail(d, 1, pts);
```

`pi/src/presence/pace.test.ts`: add `import { insertTrail } from '../db/trails';`; replace lines 56–57 (the `ptStmt` pair) with:

```ts
  insertTrail(d, id, pts.map(([t, x, y, lap]) => ({ t_ms: t, cx: x, cy: y, score: 1, lap })));
```

`pi/src/stats/completion.test.ts`: add `import { insertTrail } from '../db/trails';`; replace the `addPoints` helper (lines 32–34) with:

```ts
function addPoints(d: DatabaseSync, runId: number, pts: { cx: number; cy: number; t_ms: number }[]) {
  if (pts.length === 0) return;   // old row-loop was a no-op on []; keep that
  insertTrail(d, runId, pts.map((p) => ({ t_ms: p.t_ms, cx: p.cx, cy: p.cy, score: 1, lap: null })));
}
```

- [ ] **Step 2: Run** — `npx vitest run src/db/courseModels.test.ts src/presence/pace.test.ts src/stats/completion.test.ts` → FAIL (readers still on `run_points`).
- [ ] **Step 3: Implement** —

`pi/src/db/courseModels.ts`: add `import { getRunPoints } from './trails';`; in `rebuildCourseModel` change the EXISTS clause (line 43) to:

```ts
       AND EXISTS (SELECT 1 FROM run_trails p WHERE p.run_id=r.id)
```

delete the `ptsStmt` line (46) and change the `inputs` map's points field (52) to:

```ts
    return { playerId: r.player_id, lapCumMs: cum, points: getRunPoints(db, r.id) as RunInput['points'] };
```

`pi/src/presence/pace.ts`: add `import { getRunPoints } from '../db/trails';`; replace the fetch in `buildCurve` (lines 62–63) with:

```ts
    const pts = getRunPoints(db, runId);
```

`pi/src/stats/completion.ts`: add `import { getRunPoints } from '../db/trails';`; delete the `ptsStmt` line (54) and change the per-reset fetch (66) to:

```ts
    const pts = getRunPoints(db, reset.id);
```

`pi/src/stats/resolve.ts` line 16:

```ts
const POINTS_JOIN = 'LEFT JOIN run_trails pt ON pt.run_id = r.id';
```

`pi/src/stats/metrics.ts` line 57 — change only the value string:

```ts
  { id: 'driving_time',    kind: 'race', value: 'SUM(pt.max_t_ms)',                                       statuses: 'all',        joins: ['points'] },
```

- [ ] **Step 4: Run** — `npx vitest run src/db/courseModels.test.ts src/presence/pace.test.ts src/stats/completion.test.ts src/stats` → PASS. Then `grep -rn "driving_time" pi/src --include="*.test.ts"` — if any test seeds points for it, port that fixture the same way (none known). `npm run typecheck` → clean.
- [ ] **Step 5: Commit**

```bash
git add pi/src/db/courseModels.ts pi/src/db/courseModels.test.ts pi/src/presence/pace.ts pi/src/presence/pace.test.ts pi/src/stats/completion.ts pi/src/stats/completion.test.ts pi/src/stats/resolve.ts pi/src/stats/metrics.ts
git commit -m "feat(pi): course models, pace, completion + driving_time read run_trails"
```

---

### Task 7: Boot migration + CLIs

**Files:**
- Create: `pi/src/db/trailMigrate.ts`, `pi/src/scripts/migrateTrailsCli.ts`, `pi/src/scripts/diffTrails.ts`
- Modify: `pi/src/server.ts` (import + call after line 26), `pi/package.json` (2 script entries)
- Test: `pi/src/db/trailMigrate.test.ts`

**Interfaces:**
- Consumes: codec + `insertTrail`/`getRunPoints`.
- Produces: `migrateTrails(db: DatabaseSync): { migrated: number; failed: number; orphaned: number; dropped: boolean }`; npm scripts `migrate-trails` (arg: db path, flag `--vacuum`) and `diff-trails` (args: two db paths).

- [ ] **Step 1: Failing test** — `pi/src/db/trailMigrate.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { migrateTrails } from './trailMigrate';
import { getRunPoints, insertTrail } from './trails';
import type { TrailPoint } from './trailCodec';

// Legacy DDL built by the test itself: schema.sql stops creating run_points in a later
// task, and these tests must keep passing after that (IF NOT EXISTS covers both stages).
const LEGACY_DDL = `CREATE TABLE IF NOT EXISTS run_points (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    t_ms INTEGER NOT NULL, cx REAL NOT NULL, cy REAL NOT NULL,
    score REAL NOT NULL DEFAULT 1.0, lap INTEGER);
  CREATE INDEX IF NOT EXISTS idx_run_points_run ON run_points(run_id);`;

function legacyDb(nRuns: number, ptsPerRun: number) {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec(LEGACY_DDL);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  const runStmt = db.prepare("INSERT INTO runs(id,attempt_id,season_id,player_id,course_id,cc,status,provenance) VALUES (?,?,1,1,1,150,'finished','live')");
  const ptStmt = db.prepare('INSERT INTO run_points(run_id,t_ms,cx,cy,score,lap) VALUES (?,?,?,?,?,?)');
  for (let r = 1; r <= nRuns; r++) {
    runStmt.run(r, `a${r}`);
    for (let i = 0; i < ptsPerRun; i++)
      ptStmt.run(r, i * 40, 100 + r + i * 0.34567, 200 - i * 0.11111, 0.5 + (i % 5) / 10, i % 7 === 0 ? null : 1 + (i % 3));
  }
  return db;
}
const legacyRows = (db: ReturnType<typeof openDb>, r: number) =>
  db.prepare('SELECT t_ms, cx, cy, score, lap FROM run_points WHERE run_id=? ORDER BY t_ms').all(r) as TrailPoint[];

describe('migrateTrails', () => {
  it('migrates every run bit-exactly, deletes rows, drops the table', () => {
    const db = legacyDb(3, 50);
    const want = [1, 2, 3].map((r) => legacyRows(db, r));
    expect(migrateTrails(db)).toEqual({ migrated: 3, failed: 0, orphaned: 0, dropped: true });
    for (const r of [1, 2, 3]) expect(getRunPoints(db, r)).toEqual(want[r - 1]);
    expect(db.prepare("SELECT 1 FROM sqlite_master WHERE name='run_points'").get()).toBeUndefined();
  });

  it('is a no-op when run_points is absent (fresh or already-migrated DB)', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec('DROP TABLE IF EXISTS run_points');
    expect(migrateTrails(db)).toEqual({ migrated: 0, failed: 0, orphaned: 0, dropped: false });
  });

  it('resumes after an interrupted pass (already-blobbed runs skipped)', () => {
    const db = legacyDb(2, 10);
    const rows1 = legacyRows(db, 1);
    insertTrail(db, 1, rows1);                       // simulate prior pass on run 1…
    db.exec('DELETE FROM run_points WHERE run_id=1');
    const res = migrateTrails(db);
    expect(res.migrated).toBe(1);                     // …only run 2 migrates now
    expect(res.dropped).toBe(true);
    expect(getRunPoints(db, 1)).toEqual(rows1);
  });

  it('keeps rows + table when a run fails verification', () => {
    const db = legacyDb(2, 10);
    db.exec('INSERT INTO run_points(run_id,t_ms,cx,cy,score,lap) VALUES (1, 0, 9, 9, 1, 1)');  // duplicate t_ms=0 → encode throws
    const res = migrateTrails(db);
    expect(res.failed).toBe(1);
    expect(res.migrated).toBe(1);
    expect(res.dropped).toBe(false);
    expect((db.prepare('SELECT COUNT(*) c FROM run_points WHERE run_id=1').get() as any).c).toBe(11);
    expect(getRunPoints(db, 2).length).toBe(10);      // run 2 serves from its blob
  });

  it('orphan rows (run_id not in runs) block the drop, are counted, never deleted', () => {
    const db = legacyDb(1, 5);
    db.exec('PRAGMA foreign_keys=OFF');
    db.exec('INSERT INTO run_points(run_id,t_ms,cx,cy,score,lap) VALUES (999, 0, 1, 1, 1, 1)');
    db.exec('PRAGMA foreign_keys=ON');
    expect(migrateTrails(db)).toEqual({ migrated: 1, failed: 0, orphaned: 1, dropped: false });
    expect((db.prepare('SELECT COUNT(*) c FROM run_points').get() as any).c).toBe(1);
  });
});
```

- [ ] **Step 2: Run** — `npx vitest run src/db/trailMigrate.test.ts` → FAIL (module missing).
- [ ] **Step 3: Implement** — `pi/src/db/trailMigrate.ts`:

```ts
import type { DatabaseSync } from 'node:sqlite';
import { CODEC_BROTLI_V1, assertTrailsIdentical, decodeTrail, encodeTrail, type TrailPoint } from './trailCodec';

export type TrailMigration = { migrated: number; failed: number; orphaned: number; dropped: boolean };

/** One-time, resumable run_points → run_trails migration (runs at boot, before listen).
 *  Per run: read rows in t order → encode → decode → BIT-VERIFY against the rows → insert
 *  blob + delete rows in one transaction. A verify failure keeps that run's rows. Orphan
 *  rows (run_id not in runs) are never deleted. The table is dropped only when empty —
 *  space is reclaimed by a later manual VACUUM (see docs/pi-deploy.md). */
export function migrateTrails(db: DatabaseSync): TrailMigration {
  const res: TrailMigration = { migrated: 0, failed: 0, orphaned: 0, dropped: false };
  if (!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_points'").get()) return res;
  const ids = db.prepare(
    `SELECT DISTINCT rp.run_id FROM run_points rp
     JOIN runs r ON r.id = rp.run_id
     WHERE NOT EXISTS (SELECT 1 FROM run_trails rt WHERE rt.run_id = rp.run_id)`
  ).all() as { run_id: number }[];
  const rowsStmt = db.prepare('SELECT t_ms, cx, cy, score, lap FROM run_points WHERE run_id=? ORDER BY t_ms');
  for (const { run_id } of ids) {
    const rows = rowsStmt.all(run_id) as TrailPoint[];
    try {
      const blob = encodeTrail(rows);
      assertTrailsIdentical(rows, decodeTrail(blob));
      db.exec('BEGIN');
      db.prepare('INSERT INTO run_trails(run_id, codec, n, max_t_ms, data) VALUES (?,?,?,?,?)')
        .run(run_id, CODEC_BROTLI_V1, rows.length, rows[rows.length - 1].t_ms, blob);
      db.prepare('DELETE FROM run_points WHERE run_id=?').run(run_id);
      db.exec('COMMIT');
      res.migrated++;
    } catch (e) {
      try { db.exec('ROLLBACK'); } catch { /* encode/verify threw before BEGIN */ }
      res.failed++;
      console.error(`[trails] migration verify FAILED for run ${run_id} — rows kept:`, e);
    }
  }
  res.orphaned = (db.prepare(
    'SELECT COUNT(DISTINCT run_id) c FROM run_points WHERE run_id NOT IN (SELECT id FROM runs)'
  ).get() as { c: number }).c;
  if (!(db.prepare('SELECT EXISTS(SELECT 1 FROM run_points) e').get() as { e: number }).e) {
    db.exec('DROP TABLE run_points');
    res.dropped = true;
  }
  if (res.migrated || res.failed || res.orphaned)
    console.log(`[trails] migration: ${res.migrated} migrated, ${res.failed} failed, ${res.orphaned} orphaned run_ids; `
      + (res.dropped ? 'run_points dropped — run VACUUM to reclaim space.' : 'run_points KEPT.'));
  return res;
}
```

`pi/src/scripts/migrateTrailsCli.ts`:

```ts
// Manual run_points → run_trails migration (same routine the server runs at boot).
// Usage: npm run migrate-trails -- <path.db> [--vacuum]     (defaults to $MKW_DB / mkw.db)
import { openDb, applySchema } from '../db/connect';
import { migrateTrails } from '../db/trailMigrate';

const args = process.argv.slice(2).filter((a) => a !== '--vacuum');
const db = openDb(args[0] ?? process.env.MKW_DB ?? 'mkw.db');
applySchema(db);
const res = migrateTrails(db);
console.log(res);
if (process.argv.includes('--vacuum')) { console.log('VACUUM…'); db.exec('VACUUM'); }
if (res.failed > 0) process.exitCode = 1;
```

`pi/src/scripts/diffTrails.ts`:

```ts
// Bit-compares every run's trail between two DBs, reading through the SAME access layer
// the server uses (blob or legacy rows) — proves a migrated DB serves exactly what the
// original served. Opens read-write so WAL-sidecar copies self-recover: run it on
// COPIES, never on a live DB. Usage: npm run diff-trails -- <a.db> <b.db>
import { DatabaseSync } from 'node:sqlite';
import { assertTrailsIdentical } from '../db/trailCodec';
import { getRunPoints } from '../db/trails';

function trailRunIds(db: DatabaseSync): number[] {
  const ids = new Set<number>();
  try { for (const r of db.prepare('SELECT DISTINCT run_id id FROM run_points').all() as { id: number }[]) ids.add(r.id); } catch { /* dropped */ }
  try { for (const r of db.prepare('SELECT run_id id FROM run_trails').all() as { id: number }[]) ids.add(r.id); } catch { /* pre-schema */ }
  return [...ids].sort((x, y) => x - y);
}

function main() {
  const [a, b] = process.argv.slice(2);
  if (!a || !b) { console.error('usage: npm run diff-trails -- <a.db> <b.db>'); process.exitCode = 1; return; }
  const dbA = new DatabaseSync(a);   // read-write on purpose: recovers a copied WAL sidecar
  const dbB = new DatabaseSync(b);
  const idsA = trailRunIds(dbA), idsB = trailRunIds(dbB);
  if (idsA.length !== idsB.length || idsA.some((v, i) => v !== idsB[i])) {
    console.error(`run-id sets differ: ${idsA.length} vs ${idsB.length}`); process.exitCode = 1; return;
  }
  let pts = 0;
  for (const id of idsA) {
    const ta = getRunPoints(dbA, id), tb = getRunPoints(dbB, id);
    try { assertTrailsIdentical(ta, tb); } catch (e) {
      console.error(`run ${id}: MISMATCH — ${(e as Error).message}`); process.exitCode = 1; return;
    }
    pts += ta.length;
  }
  console.log(`OK: ${idsA.length} runs, ${pts} points bit-identical.`);
}
main();
```

`pi/package.json` — add to `scripts` (after `"wipe-runs"`):

```json
    "migrate-trails": "node --no-warnings --import tsx src/scripts/migrateTrailsCli.ts",
    "diff-trails": "node --no-warnings --import tsx src/scripts/diffTrails.ts",
```

`pi/src/server.ts` — add to the imports:

```ts
import { migrateTrails } from './db/trailMigrate';
```

and after line 26 (`migratePlayerRenames(db);`), before the `backfillActivity` line:

```ts
migrateTrails(db);             // one-time: run_points rows → run_trails blobs (bit-verified; see docs/replay-format.md)
```

- [ ] **Step 4: Run** — `npx vitest run src/db/trailMigrate.test.ts` → PASS. `npm run typecheck` → clean.
- [ ] **Step 5: Commit**

```bash
git add pi/src/db/trailMigrate.ts pi/src/db/trailMigrate.test.ts pi/src/scripts/migrateTrailsCli.ts pi/src/scripts/diffTrails.ts pi/src/server.ts pi/package.json
git commit -m "feat(pi): bit-verified resumable run_points->run_trails boot migration + CLIs"
```

---

### Task 8: Retire `run_points` from schema + ops

**Files:**
- Modify: `server/schema.sql` (remove `run_points` CREATE block ~66–73 and the `idx_run_points_run` index line ~156)
- Modify: `pi/src/db/schema.test.ts:4` (drop `'run_points'` from TABLES)
- Modify: `pi/src/scripts/wipeRuns.ts` (lines 2, 19, 23 comment)
- Modify: `server/reset_season0.py` (all `run_points` references → `run_trails`)
- Do NOT touch `pi/src/db/connect.ts:48` (`ALTER TABLE run_points ADD COLUMN lap`) — pre-migration legacy DBs still need it; it's try/caught and becomes a harmless no-op everywhere else.

- [ ] **Step 1: Failing test** — in `pi/src/db/schema.test.ts` line 4 remove `'run_points'`:

```ts
const TABLES = ['seasons','players','season_rosters','courses','runs','run_laps','run_trails','world_records','ghost_imports'];
```

Also add a new test in the same describe:

```ts
  it('fresh DBs no longer create the legacy run_points table', () => {
    const db = openDb(':memory:');
    applySchema(db);
    expect(db.prepare("SELECT 1 FROM sqlite_master WHERE name='run_points'").get()).toBeUndefined();
  });
```

- [ ] **Step 2: Run** — `npx vitest run src/db/schema.test.ts` → the new test FAILS (schema still creates it).
- [ ] **Step 3: Implement** —

`server/schema.sql`: delete the whole `CREATE TABLE IF NOT EXISTS run_points (…);` block and the line `CREATE INDEX IF NOT EXISTS idx_run_points_run   ON run_points(run_id);`.

`pi/src/scripts/wipeRuns.ts`: line 2 comment → `// Deletes ALL recorded runs (run_laps/run_trails cascade), course models and`; line 19 → `const tables = ['runs', 'run_laps', 'run_trails', 'course_models', 'player_alignment'];`; line 23 comment → `db.exec('DELETE FROM runs');             // run_laps + run_trails cascade`.

`server/reset_season0.py` — port every `run_points` reference (the script's integrity guards now count blob points; it requires an already-migrated DB, which the boot migration guarantees):
- Docstring: `positional mapping (run_points)` → `positional mapping (run_trails)`; `A run_points/run_laps row-count check` → `A run_trails/run_laps count check`.
- Both CTEs `WITH pts AS (SELECT run_id, COUNT(*) n FROM run_points GROUP BY run_id)` (in `recompute_is_pb` and the post-change report) → `WITH pts AS (SELECT run_id, n FROM run_trails)`.
- `pts_before`/`pts_after`: `SELECT COUNT(*) FROM run_points` → `SELECT COALESCE(SUM(n),0) FROM run_trails`.
- Invariant guard: `id IN (SELECT run_id FROM run_points)` → `id IN (SELECT run_id FROM run_trails)`.
- `orphan_pts`: `SELECT COUNT(*) FROM run_points WHERE run_id NOT IN (SELECT id FROM runs)` → `SELECT COUNT(*) FROM run_trails WHERE run_id NOT IN (SELECT id FROM runs)`.
- Report print `run_points {pts_before}->{pts_after}` → `trail points {pts_before}->{pts_after}`.
- Verify: `grep -n run_points server/reset_season0.py` → no matches.

- [ ] **Step 4: Run everything** — `npm test` → full pi suite PASS (codec/trails/migrate tests self-create the legacy table where needed). `npm run typecheck` → clean. Then `grep -rn "run_points" pi/src server/*.py server/schema.sql` — expected remaining hits ONLY in: `trails.ts` (fallback), `trailMigrate.ts`, `trails.test.ts` / `trailMigrate.test.ts` (LEGACY_DDL fixtures), `connect.ts:48` (guarded ALTER). Anything else is a missed port — fix it.
- [ ] **Step 5: Commit**

```bash
git add server/schema.sql pi/src/db/schema.test.ts pi/src/scripts/wipeRuns.ts server/reset_season0.py
git commit -m "feat(schema): retire run_points from fresh schema; port wipe-runs + reset_season0 guards"
```

---

### Task 9: Docs

**Files:**
- Modify: `docs/replay-format.md`, `docs/database-schema.md`, `docs/pi-deploy.md`, root `CLAUDE.md`

- [ ] **Step 1: `docs/replay-format.md`** — in the "Notes / gotchas" bullet that reads "`run_points` is ~63% of the live DB and grows unbounded on the Pi by design", replace the sentence with: "Trails dominate the live DB and grow unbounded by design; at rest they're stored ~4× smaller as per-run `run_trails` blobs (below), still bit-exact." Then append a new section at the end:

```markdown
## At-rest storage on the Pi (`run_trails`)

Points are stored per run as a single compressed blob — **losslessly** (identical float64
bits, order, laps). One row per run:

| column | meaning |
|---|---|
| `run_id` | PK, FK → `runs(id)` ON DELETE CASCADE |
| `codec` | `1` = v1 payload, brotli-compressed |
| `n` | point count |
| `max_t_ms` | final `t_ms` (feeds `driving_time` etc. without decoding) |
| `data` | blob |

v1 payload (before brotli, little-endian; varint = unsigned LEB128):

```
varint n | u8 flags (0) | varint t0, varint Δt ×(n−1) | lap RLE (u8 value, varint count; 255=NULL)
| cx: n×f64 byte-plane-transposed (planes LSB→MSB) | cy: same | score: same
```

Codec: `pi/src/db/trailCodec.ts` (golden-vector test pins the format). Access:
`pi/src/db/trails.ts` (`insertTrail` / `getRunPoints`). The legacy one-row-per-point
`run_points` table is converted at boot by `pi/src/db/trailMigrate.ts` — each run is
encoded, decoded, **bit-compared against its own rows**, then swapped in one transaction;
the table is dropped when empty. `npm run diff-trails -- a.db b.db` bit-compares two DBs.
Measured ~13 B/pt vs ~53 B/pt as rows (dev DB 68→18 MiB). Do NOT add lossy steps —
see the spec's dead-ends list (`docs/superpowers/specs/2026-07-07-trail-storage-compression-design.md`).
```

- [ ] **Step 2: `docs/database-schema.md`** — find the `run_points` table section and replace it with a `run_trails` section:

```markdown
### `run_trails` — minimap trail, one compressed blob per run

| column | type | notes |
|---|---|---|
| `run_id` | INTEGER PK | FK → `runs(id)` ON DELETE CASCADE |
| `codec` | INTEGER | `1` = v1 payload + brotli (see `docs/replay-format.md`) |
| `n` | INTEGER | point count |
| `max_t_ms` | INTEGER | final race-clock ms (SQL aggregates use this, not a decode) |
| `data` | BLOB | lossless packed trail — full-resolution floats, bit-exact |

Replaces the legacy `run_points` row-per-point table (~4× smaller); existing DBs are
converted by a bit-verified boot migration (`pi/src/db/trailMigrate.ts`). Read/write only
via `pi/src/db/trails.ts`.
```

Update any other `run_points` mentions in that file to `run_trails` (grep the file).

- [ ] **Step 3: `docs/pi-deploy.md`** — add under the update/deploy section:

```markdown
### One-time: trail storage migration (v2.7)

The first boot after this deploy converts `run_points` rows into `run_trails` blobs
(bit-verified per run; resumable; logs `[trails] migration: …`). Order of operations:

1. Back up the DB first: `cp ~/mkw-data/mkw.db ~/mkw-data/mkw.db.pretrails-bak`
2. Deploy the tag as usual; watch the boot log for `[trails] migration: N migrated, 0 failed`.
3. Spot-check trails on the site, then reclaim the space (server stopped, once):
   `cd ~/apps/mkw/pi && npm run migrate-trails -- ~/mkw-data/mkw.db --vacuum`
   (the migration itself is already done; this just VACUUMs — needs free disk ≈ the old DB size).
4. Rollback = restore the backup. Blobs also decode back to exact rows at any time
   (`npm run diff-trails` proves equality), so nothing is unrecoverable.
```

(Adjust the clone path in step 3 to match the paths this doc already uses.)

- [ ] **Step 4: root `CLAUDE.md`** — in the "Data flow" line, change ``(`runs`, `run_laps`, `run_points`, …)`` to ``(`runs`, `run_laps`, `run_trails`, …)``.

- [ ] **Step 5: Commit**

```bash
git add docs/replay-format.md docs/database-schema.md docs/pi-deploy.md CLAUDE.md
git commit -m "docs: run_trails at-rest trail format, schema + deploy runbook updates"
```

---

### Task 10: Full-data rehearsal + USER EYE CHECK (stop point)

No repo files change in this task (except fixes if something fails). Never touch `pi/mkw.db` itself — copies only.

- [ ] **Step 1: Full suite** — from `pi/`: `npm test` → all PASS; `npm run typecheck` → clean.
- [ ] **Step 2: Rehearse on copies of the real dev DB** (bash; server NOT running; the original copy stays unmigrated so Step 3 has something to diff against):

```bash
R=/c/Users/Paul/AppData/Local/Temp/trail-rehearsal; rm -rf "$R"; mkdir -p "$R"
cp /c/development/mkw-split-rewrite/pi/mkw.db "$R/orig.db"
cp /c/development/mkw-split-rewrite/pi/mkw.db-wal "$R/orig.db-wal" 2>/dev/null || true
cp "$R/orig.db" "$R/migrated.db"
cp "$R/orig.db-wal" "$R/migrated.db-wal" 2>/dev/null || true
cd /c/development/mkw-split-rewrite/pi
npm run migrate-trails -- "$R/migrated.db" --vacuum
```

Expected: `[trails] migration: ~1756 migrated, 0 failed, 0 orphaned run_ids; run_points dropped …` then `{ migrated: ~1756, failed: 0, orphaned: 0, dropped: true }`. (Run count grows with new data; `failed` MUST be 0.)

- [ ] **Step 3: Bit-diff original vs migrated**:

```bash
npm run diff-trails -- "$R/orig.db" "$R/migrated.db"
```

Expected: `OK: <runs> runs, <~1.36M> points bit-identical.` Also compare sizes: `ls -la "$R"` — migrated.db should be roughly 4× smaller (~74 MiB → ~20 MiB incl. non-trail tables).

- [ ] **Step 4: Boot the server on the migrated copy** (smoke):

```bash
MKW_DB="$R/migrated.db" PORT=8790 npm run dev
```

Expected: boots clean, `[pi] listening on http://127.0.0.1:8790`, no `[trails]` errors (migration is a no-op — already done). `curl http://127.0.0.1:8790/health` → OK. Stop it.

- [ ] **Step 5: STOP — hand to the user for the eye check.** Report the rehearsal numbers and ask the user to:
  1. Run the local Pi on the ORIGINAL copy (`MKW_DB=$R/orig.db PORT=8791 npm run dev`) and on the MIGRATED copy (`MKW_DB=$R/migrated.db PORT=8790 npm run dev`), point the site/desktop dev builds at each, and eyeball course trails, the live/turf views, and desktop `--history` replays side by side.
  2. Approve (or reject) shipping: tag + deploy per `docs/pi-deploy.md`'s new "trail storage migration" section.

Do NOT merge to `main`, tag, or deploy anything until the user approves. After approval: merge `trail-compression` → `main`, then follow the runbook (backup → tag deploy → watch boot log → VACUUM).

---

## Execution notes for the implementer

- The engine (`mkw_tracker/`), Rust (`src-tauri/`), and website (`web/`) are correct as-is — if a change there seems needed, stop and re-read the spec; the answer is no.
- `npm install` (not `ci`) if node_modules is missing; pi has no build step (tsx).
- If any vitest port fails unexpectedly, check whether the fixture still inserts into `run_points` — after Task 8 that table doesn't exist in `applySchema` DBs; fixtures must use `insertTrail` (or their own LEGACY_DDL for migration/fallback tests).
- `pi/mkw.db*`, `*.db` copies, and anything under the rehearsal temp dir must never be committed.

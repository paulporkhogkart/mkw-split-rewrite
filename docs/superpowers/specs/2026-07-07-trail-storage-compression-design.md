# Trail Storage Compression — Design

**Date:** 2026-07-07 · **Surface:** Pi server (`pi/`) + schema (`server/schema.sql`) · **Status:** approved design, pending spec review

## Problem

Replay trails (minimap dots) are stored on the Pi as **one SQLite row per point** in
`run_points(run_id, t_ms, cx REAL, cy REAL, score REAL, lap)` plus `idx_run_points_run`.
Measured on real data this costs **~53 bytes/point** and is **86–92% of the whole DB file**
(dev DB: 68.3 of 73.9 MiB across 1,355,644 points; prod snapshot: 9.9 of 11.5 MiB across
197,097 points). Growth is ~1–2 GB/yr, unbounded by deliberate choice.

Lossy reduction (decimation / densify / SED / quantization) was explored previously and is
**permanently rejected** — derived analytics (course-model raster, pace curve) need dense
full-resolution float input (see memory `gotchas-race-replay`, branch
`replay-trail-optimization`, unmerged). This design is the complementary, untouched path:
**pack the exact same values into fewer bytes.**

## Goal & requirements

Compress trail storage **losslessly**: every stored point must round-trip **bit-exactly**
(identical float64 bits for `cx`/`cy`/`score`, identical `t_ms`/`lap`, identical
`ORDER BY t_ms` sequence).

Acceptance criteria (user-named):

1. **End-of-run position/lap stats keep working** — `stats/completion.ts` replays each reset
   run's full trail (`cx, cy, t_ms, lap` in t-order) through the course-model projector to
   compute final completion %. The decoded trail must be the identical sequence. The
   `driving_time` metric (`SUM` of each run's final `t_ms`) must stay exact.
2. **Live race % completion keeps working** — the live projector consumes in-memory presence
   frames (it never reads `run_points`); its inputs from storage are course models
   (`courseModels.ts`, rebuilt from full trails on every finished upload) and pace curves
   (`presence/pace.ts`, PB trail). Both must read identical data via the new decode path.

Non-goals: no change to the upload wire format (`run_finalized` → `POST /v1/runs` stays JSON),
no change to any API response shape, no desktop/website/bot changes, no precision loss of any
kind, no retention policy.

## Measured evidence (prototype, verified bit-exact on all real data)

Scratchpad lab (`trail_lab.mjs`) run against copies of `pi/mkw.db` (dev) and
`temp/mkw-prod.db` (prod snapshot). Every candidate was **decode-verified bit-for-bit against
the reader-visible sequence for every point in both DBs** before being reported.

| Option | dev (1.36M pts) | prod (197k pts) | bytes/pt | ratio |
|---|---|---|---|---|
| A) current rows + index | 68.33 MiB | 9.93 MiB | 52.8–52.9 | 1× |
| B) `WITHOUT ROWID` PK(run_id,t_ms), no index | 50.08 MiB | 7.32 MiB | 38.7–39.0 | 1.36× |
| **C) per-run blob, shuffle + brotli q11 (chosen)** | **17.81 MiB** | **2.47 MiB** | **13.2–13.8** | **3.84–4.02×** |

Decode: 0.16 ms/run. Encode (brotli q11, once per finalized run): tens of ms — negligible at
race cadence. Projected growth: ~1–2 GB/yr → **~0.3–0.5 GB/yr**.

Data facts the format relies on (validated on all 1.55M points): `t_ms` strictly increasing
within a run (0 ties, 0 negatives; median delta 40 ms, max seen 9,046 ms), `lap` is a small
int or NULL in long runs (RLE-friendly), `cx`/`cy`/`score` are full-mantissa float64
(EMA-smoothed / NCC scores) — treated as opaque 8-byte values, never re-derived.

## Design

### Codec v1 (`pi/src/db/trailCodec.ts`)

`encodeTrail(pts) → Buffer` / `decodeTrail(buf) → pts`, where `pts` is
`{ t_ms: number; cx: number; cy: number; score: number; lap: number | null }[]` in t-order —
the same row shape readers use today.

Uncompressed payload layout (then brotli-compressed, quality 11, LE throughout):

```
varint n                      point count (n ≥ 1; empty trails are never stored)
u8     flags                  0 in v1 (reserved)
varint t[0]                   first t_ms (≥ 0)
varint Δt × (n−1)             t deltas (> 0; strict monotonicity is asserted at encode)
RLE    lap                    pairs (u8 value, varint count) summing to n; 255 = NULL
f64×n  cx   — byte-plane transposed (8 planes of n bytes)
f64×n  cy   — byte-plane transposed
f64×n  score — byte-plane transposed
```

(`varint` = unsigned LEB128 — 7 payload bits per byte, high bit = continue, least-significant
group first. Byte planes are ordered LSB → MSB of the little-endian f64. Both are pinned by a
golden-vector test.)

Byte-plane transposition groups the floats' sign/exponent/high-mantissa bytes, which is where
the shared structure lives; brotli does the entropy coding. Floats are moved as raw bytes only
— no arithmetic ever touches them, so bit-exactness is structural, not incidental.

Encode asserts its invariants (n ≥ 1, strictly increasing t, lap ∈ [0,254] ∪ NULL) and
throws rather than store a malformed trail. Decode is total for well-formed blobs.

### Schema (`server/schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS run_trails (
    run_id   INTEGER PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    codec    INTEGER NOT NULL,          -- 1 = v1 payload, brotli-compressed
    n        INTEGER NOT NULL,          -- point count (SQL-visible)
    max_t_ms INTEGER NOT NULL,          -- final t_ms (SQL-visible; replaces MAX(t_ms) scans)
    data     BLOB NOT NULL
);
```

`run_points` DDL is removed from `schema.sql` (fresh DBs never create it). No extra index —
`run_id` is the PK. `n` and `max_t_ms` are the only fields SQL aggregates need today; the
final coordinate/lap are the last entry of the decoded trail (adding `end_cx/end_cy/end_lap`
columns later is trivial if a SQL-only stat ever wants them — YAGNI now).

### Access layer

- `getRunPoints(db, runId)` in `pi/src/db/` — fetch blob → decode → rows. While the legacy
  table still exists (interrupted-migration window only), falls back to
  `SELECT … FROM run_points … ORDER BY t_ms` when no blob row is found; the fallback
  dead-codes once `run_points` is dropped.
- `insertTrail(db, runId, pts)` — encode + INSERT (also used by tests).

Call-site changes (all Pi-only, response shapes unchanged):

| Site | Today | After |
|---|---|---|
| `db/reads.ts` `courseTrails` / `playerTrails` | per-run SELECT | `getRunPoints` |
| `db/courseModels.ts` model rebuild window | per-run SELECT + `EXISTS(run_points)` | `getRunPoints` + `EXISTS(run_trails)` |
| `presence/pace.ts` PB trail | per-run SELECT | `getRunPoints` |
| `stats/completion.ts` reset replay | per-run SELECT | `getRunPoints` |
| `stats/resolve.ts` `POINTS_JOIN` (`driving_time`) | `MAX(t_ms) GROUP BY run_id` subquery | `LEFT JOIN run_trails pt … SUM(pt.max_t_ms)` |
| `db/ingest.ts` `upsertRun` / `enrichRunFromGhost` | per-point INSERT loop | `insertTrail` (runaway guard `OVER_LIMIT_MS` and has-points checks unchanged in behavior) |
| `scripts/wipeRuns.ts` | table list has `run_points` | `run_trails` |
| `server/reset_season0.py` | `run_points` count/EXISTS integrity guards | same guards against `run_trails`/`n` |

### Migration (one-time, resumable, reversible)

Runs at boot in `server.ts` after `applySchema`, **before the server starts listening**
(follows the existing one-time-migration pattern):

1. If `run_points` doesn't exist → no-op.
2. For each run with rows and no `run_trails` entry: read rows in t-order → encode → decode →
   **bit-verify against the just-read rows** → in one transaction insert the blob and delete
   that run's rows. A verify failure skips that run (rows kept), logs loudly, and is counted.
3. When `run_points` is empty and no failures: `DROP TABLE run_points` (+ its index). With
   failures: keep the table (the `getRunPoints` fallback still serves those runs) and report.
4. `VACUUM` is **manual** per the deploy runbook (space is only reclaimed then; needs ~old-DB
   headroom). Boot logs a reminder while unreclaimed.

Interrupted migration self-heals next boot (step 2 is per-run transactional). Prod-scale cost
(~200 runs) is well under a minute on the Pi; dev-scale (~1,800 runs) a few minutes, once.
Rollback: the runbook's pre-deploy DB backup, **plus** the format itself is reversible — a
decode-and-reinsert script can reconstruct `run_points` bit-exactly from blobs at any time.

Known, accepted nuance: during an *interrupted* migration window, `EXISTS(run_trails)` sites
and `driving_time` omit not-yet-migrated runs (trail lists shorter, sums smaller) until the
next boot completes the sweep. Normal deploys never expose this state — migration finishes
before listen.

### Tests & docs

- Codec unit tests: golden vector; property round-trip (random trails; single point; NULL/mixed
  laps; large t gaps; long runs); invariant rejection (empty, non-monotonic t, lap > 254).
- Migration test: build legacy `run_points` fixture → migrate → decoded-vs-original
  bit-compare → table dropped; interrupted-migration resume; verify-failure path keeps rows.
- Port existing test setups that `INSERT INTO run_points` (completion, pace, courseModels,
  reads ×2, ingest, api/reads) to the `insertTrail` helper; `schema.test.ts` TABLES list swaps
  `run_points` → `run_trails`.
- Docs: `docs/replay-format.md` gains an "at-rest storage" section (blob layout above);
  `docs/database-schema.md`, root `CLAUDE.md` data-flow line, `pi/CLAUDE.md` if it names the
  table; deploy runbook gains the backup → deploy → migrate-at-boot → eye-check → VACUUM steps.

### Verification & eye-confirmation (before anything ships)

1. **Machine:** codec tests + migration self-verification (above) — every historical point
   bit-compared before its rows are deleted. Already rehearsed in the scratchpad lab on both
   real DBs: 1,552,741 points, zero mismatches.
2. **Local rehearsal:** migrate a **copy** of the dev DB; run two local Pi servers (original
   vs migrated); byte-diff the JSON of the trail-serving endpoints for every course/player —
   must be identical.
3. **Eye check (user):** with the migrated local server, load the site's trail views and the
   desktop `--history` replay and compare against the original-DB server side by side.
4. **Deploy:** runbook order — backup `~/mkw-data/mkw.db` → tag deploy → boot migrates →
   spot-check live site → `VACUUM` when convenient.

## Alternatives considered

- **B) `WITHOUT ROWID` re-key** (38.7 B/pt, 1.36×): minimal change, keeps per-point SQL, but
  leaves ~3× on the table. Rejected as primary; superseded by C.
- **File/page-level compression** (ZIPVFS, sqlite-zstd, fs-level): not loadable/controllable
  under stock `node:sqlite`, operationally fragile on the Pi. Rejected.
- **Dead ends measured, do not re-try:** XOR/Gorilla predictive coding (≤0.1% gain over plain
  byte-shuffle); float32 for `score` (only ~5/1756 runs have all-f32-exact scores; ~0 gain
  after compression); JSON+brotli (2.2× worse than binary layout); zstd (≈brotli11 −0.1 B/pt,
  not worth Node-version coupling; Pi pins Node 24, brotli is built-in since Node 11).
  ~13 B/pt is near the entropy floor of full-mantissa f64 coordinate pairs — materially better
  requires precision loss, which is permanently off the table.

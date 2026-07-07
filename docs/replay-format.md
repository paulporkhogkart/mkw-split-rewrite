# Replay / Trail Format

How a race and its minimap trail travel from the desktop engine to the Pi server.

The engine does **not** write a `.mkwreplay` file. On race end it emits an IPC `run_finalized`
message (`mkw_tracker/lifecycle/race.py` → `mkw_tracker/ipc/protocol.py`); Rust uploads that
payload to `POST /v1/runs` on the Pi, which stores it across `runs` / `run_laps` / `run_trails`
(see `docs/database-schema.md`).

## `run_finalized` / upload payload (`AttemptPayload`)

```json
{
  "attempt_id": "a1b2c3…",         // uuid hex, unique per attempt
  "course": "mario_circuit",        // slug
  "cc": 150,
  "status": "finished",             // "reset" | "dnf" | "finished"
  "provenance": "live",             // "live" | "legacy_import" | "carryover"
  "source": null,                   // "ghost" for ghost-PB imports, else null
  "character": "mario",
  "kart": "standard",
  "costume": "base",
  "started_at": "2026-06-20T14:55:00Z",
  "ended_at":   "2026-06-20T14:57:34Z",
  "total_time": "2:34.567",
  "total_laps": 3,                  // transported but NOT persisted server-side
  "laps": [
    { "lap": 1, "time_ms": 51234, "time_str": "0:51.234", "coins": 4, "shrooms": 1 }
  ],
  "coins_gained": 12,
  "coins_lost": 3,
  "mushrooms_used": 2,
  "points": [
    [0,    1542.3, 678.1, 0.997, 1],
    [16,   1543.1, 677.8, 0.994, 1],
    [32,   1544.0, 677.5, 0.991, 1]
  ]
}
```

## Point format — **5-element** array `[t_ms, cx, cy, score, lap]`

| Index | Type | Description |
|---|---|---|
| 0 | int | Race-clock elapsed ms — **t=0 is GO** (`RaceTimer`, not wall/screen time). Countdown points (`t<0`) are dropped; pre-anchor points are back-stamped. |
| 1 | float | X in full 1080p px |
| 2 | float | Y in full 1080p px |
| 3 | float | NCC match score (0.0–1.0) |
| 4 | int \| null | 1-based HUD lap stamp (added 2026-06). **Optional** — legacy payloads are 4-tuples; may be `null` when the lap counter wasn't yet read. |

Wire type: `pi/src/db/types.ts` — `Point = [number, number, number, number, (number|null)?]`.

## Notes / gotchas

- One point per detection frame (~25.6 Hz); **no decimation** — full-res float is a deliberate
  choice (decimation was explored and rejected; see the memory archive). Trails dominate the live
  DB and grow unbounded by design; at rest they're stored ~4× smaller as per-run `run_trails`
  blobs (below), still bit-exact.
- 11-minute runaway guard: if the trail exceeds `OVER_LIMIT_MS` the run+laps are stored but the
  entire `points` array is dropped at ingest.
- `reset` and `dnf` runs still store their full trail. `run_laps.lap_time_ms` is a per-lap
  **duration**, not cumulative.
- Stale reference: the `protocol.py` `emit_run_finalized` docstring still shows a 4-tuple — the
  emitted data is the 5-tuple above.

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

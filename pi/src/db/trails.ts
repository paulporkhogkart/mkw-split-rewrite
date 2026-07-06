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
 *  falls back to reading its rows (existence-checked so real failures surface loudly);
 *  an absent table = genuinely trail-less run. The fallback dead-codes once the table is dropped. */
export function getRunPoints(db: DatabaseSync, runId: number): TrailPoint[] {
  const row = db.prepare('SELECT codec, data FROM run_trails WHERE run_id=?').get(runId) as
    { codec: number; data: Uint8Array } | undefined;
  if (row) {
    if (row.codec !== CODEC_BROTLI_V1) throw new Error(`unknown trail codec ${row.codec} for run ${runId}`);
    return decodeTrail(row.data);
  }
  // Legacy fallback (interrupted-migration window + pre-drop DBs). Existence-checked so a
  // genuinely broken query still throws loudly; an absent table = genuinely trail-less run.
  if (!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_points'").get()) return [];
  return db.prepare('SELECT t_ms, cx, cy, score, lap FROM run_points WHERE run_id=? ORDER BY t_ms')
    .all(runId) as TrailPoint[];
}

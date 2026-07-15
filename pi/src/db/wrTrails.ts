import type { DatabaseSync } from 'node:sqlite';
import { CODEC_BROTLI_V1, decodeTrail, encodeTrail, type TrailPoint } from './trailCodec';

/** Wire shape for GET /v1/wr-trails. `points` are 4-tuples [t_ms, cx, cy, score] — the stored
 *  5th `lap` field is dropped, matching the existing run-trail serializers (db/reads.ts:104). */
export type WrTrailRow = {
  wr_id: number; holder_name: string | null; record_ms: number; record_str: string;
  achieved_at: string | null; is_current: number; video_url: string | null;
  points: [number, number, number, number][];
};

/** Encode + store a WR's trail, replacing any existing one (a re-processed WR overwrites).
 *  Throws on an empty trail (packTrail); the caller decides drop-vs-fail policy. */
export function insertWrTrail(db: DatabaseSync, wrId: number, pts: TrailPoint[]): void {
  const data = encodeTrail(pts);
  db.prepare('INSERT OR REPLACE INTO wr_trails(wr_id, codec, n, max_t_ms, data) VALUES (?,?,?,?,?)')
    .run(wrId, CODEC_BROTLI_V1, pts.length, pts[pts.length - 1].t_ms, data);
}

/** A WR's full trail in t order, or [] when it has none. */
export function getWrTrail(db: DatabaseSync, wrId: number): TrailPoint[] {
  const row = db.prepare('SELECT codec, data FROM wr_trails WHERE wr_id=?').get(wrId) as
    { codec: number; data: Uint8Array } | undefined;
  if (!row) return [];
  if (row.codec !== CODEC_BROTLI_V1) throw new Error(`unknown trail codec ${row.codec} for wr ${wrId}`);
  return decodeTrail(row.data);
}

/** Every trailed WR for a course, fastest first. Soft-removed WRs are excluded. */
export function courseWrTrails(db: DatabaseSync, courseId: number, cc: number): WrTrailRow[] {
  const rows = db.prepare(
    `SELECT w.id AS wr_id, w.holder_name, w.record_ms, w.record_str,
            w.achieved_at, w.is_current, w.video_url
     FROM world_records w JOIN wr_trails t ON t.wr_id = w.id
     WHERE w.course_id=? AND w.cc=? AND w.removed_at IS NULL
     ORDER BY w.record_ms ASC, w.id ASC`
  ).all(courseId, cc) as Omit<WrTrailRow, 'points'>[];
  return rows.map((r) => ({
    ...r,
    points: getWrTrail(db, r.wr_id).map((p) => [p.t_ms, p.cx, p.cy, p.score] as [number, number, number, number]),
  }));
}

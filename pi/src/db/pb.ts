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

/** Total time (ms) of the player's PB run for a (season,player,course,cc) scope, or null. */
export function pbMsFor(db: DatabaseSync, seasonId: number, playerId: number, courseId: number, cc: number): number | null {
  const row = db.prepare(
    "SELECT total_time_ms FROM runs WHERE season_id=? AND player_id=? AND course_id=? AND cc=? AND is_pb=1 LIMIT 1"
  ).get(seasonId, playerId, courseId, cc) as { total_time_ms: number } | undefined;
  return row ? row.total_time_ms : null;
}

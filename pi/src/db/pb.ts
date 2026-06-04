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

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

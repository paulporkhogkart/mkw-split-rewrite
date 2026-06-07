import type { DatabaseSync } from 'node:sqlite';
import { courseLeaderboard } from './reads';

export type Standing = { player_id: number; display_name: string; total_ms: number; tracks: number; points: number };

/** Overall standings across all courses for (season,cc): each roster player's summed PB time, the
 *  number of courses they have a PB on, and golf points = sum of their per-course rank. Ranked by
 *  (total_ms, points). Ports legacy PersonalBest.get_total_leaderboard (points = sum of positions). */
export function overallStandings(db: DatabaseSync, seasonId: number, cc: number): Standing[] {
  const courses = db.prepare('SELECT DISTINCT course_id FROM runs WHERE season_id=? AND cc=? AND is_pb=1')
    .all(seasonId, cc) as { course_id: number }[];
  const acc = new Map<number, { name: string; total: number; tracks: number; points: number }>();
  for (const { course_id } of courses) {
    const lb = courseLeaderboard(db, seasonId, course_id, cc);   // already ranked fastest-first
    lb.forEach((row, i) => {
      const cur = acc.get(row.player_id) ?? { name: row.display_name, total: 0, tracks: 0, points: 0 };
      cur.total += row.total_time_ms;
      cur.tracks += 1;
      cur.points += i + 1;          // rank on this course
      acc.set(row.player_id, cur);
    });
  }
  const rows = [...acc.entries()].map(([player_id, v]) => ({
    player_id, display_name: v.name, total_ms: v.total, tracks: v.tracks, points: v.points,
  }));
  rows.sort((a, b) => a.total_ms - b.total_ms || a.points - b.points);
  return rows;
}

/** Sum of the current WR record_ms across all courses that have one (for the overall WR aggregate row). */
export function wrAggregate(db: DatabaseSync, cc: number): { total_ms: number; count: number } {
  const row = db.prepare('SELECT COALESCE(SUM(record_ms),0) total, COUNT(*) n FROM world_records WHERE cc=? AND is_current=1')
    .get(cc) as { total: number; n: number };
  return { total_ms: row.total, count: row.n };
}

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

export type NemesisDatum = { track_name: string; diff_ms: number; ahead_player: string };

/** Courses where `playerId` is behind, vs a specific `targetId` or (when null) the course leader.
 *  diff_ms = player's PB - the comparison PB (positive = player is behind). Sorted largest gap first.
 *  Only courses where the player has a PB (and, for targeted, the target also has one) are included.
 *  Ports legacy _calculate_nemesis_data (discord_bot.py:380-452). */
export function nemesisRows(db: DatabaseSync, seasonId: number, cc: number,
                            playerId: number, targetId: number | null): NemesisDatum[] {
  const courses = db.prepare(
    `SELECT c.id, c.display_name FROM courses c
     WHERE EXISTS (SELECT 1 FROM runs r WHERE r.season_id=? AND r.cc=? AND r.course_id=c.id AND r.player_id=? AND r.is_pb=1)`
  ).all(seasonId, cc, playerId) as { id: number; display_name: string }[];
  const out: NemesisDatum[] = [];
  for (const c of courses) {
    const lb = courseLeaderboard(db, seasonId, c.id, cc);
    const mine = lb.find((r) => r.player_id === playerId);
    if (!mine) continue;
    let ahead: { name: string; ms: number } | null = null;
    if (targetId != null) {
      const t = lb.find((r) => r.player_id === targetId);
      if (!t) continue;                       // target has no time here -> skip
      ahead = { name: t.display_name, ms: t.total_time_ms };
    } else {
      let leader = lb[0];
      if (leader.player_id === playerId) leader = lb[1];   // compare to 2nd when player leads
      if (!leader) continue;
      ahead = { name: leader.display_name, ms: leader.total_time_ms };
    }
    out.push({ track_name: c.display_name, diff_ms: mine.total_time_ms - ahead.ms, ahead_player: ahead.name });
  }
  out.sort((a, b) => b.diff_ms - a.diff_ms);
  return out;
}

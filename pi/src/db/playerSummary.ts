import type { DatabaseSync } from 'node:sqlite';
import { courseLeaderboard, currentWr } from './reads';

export interface PbRow {
  slug: string; course: string; cc: number;
  your_ms: number; your_rank: number; field_size: number;
  wr_ms: number | null; off_wr_pct: number | null;
  next_rank_ms: number | null; gap_to_next_ms: number | null;
  leader_ms: number; leader_off_wr_pct: number | null; leads: boolean;
}

/** One rich row per course the player has a PB on: their rank in the field, the PB directly
 *  above them (for GOLF), the course leader (for TURF), and WR gaps (for TIME + kernel). */
export function playerPbRows(db: DatabaseSync, seasonId: number, cc: number, playerId: number): PbRow[] {
  const mine = db.prepare(
    `SELECT r.course_id, c.slug, c.display_name, r.total_time_ms
     FROM runs r JOIN courses c ON c.id = r.course_id
     WHERE r.season_id=? AND r.player_id=? AND r.cc=? AND r.is_pb=1 AND r.total_time_ms IS NOT NULL
     ORDER BY c.display_name`
  ).all(seasonId, playerId, cc) as { course_id: number; slug: string; display_name: string; total_time_ms: number }[];

  const rows: PbRow[] = [];
  for (const m of mine) {
    const lb = courseLeaderboard(db, seasonId, m.course_id, cc);
    const meIdx = lb.findIndex((r) => r.player_id === playerId);
    if (meIdx < 0) continue;
    const your_rank = meIdx + 1;
    const leads = your_rank === 1;
    const wr = currentWr(db, m.course_id, cc);
    const wr_ms: number | null = wr ? wr.record_ms : null;
    const off = (ms: number): number | null => (wr_ms != null ? ((ms - wr_ms) / wr_ms) * 100 : null);
    const next_rank_ms = leads ? null : lb[meIdx - 1].total_time_ms;
    const leader_ms = lb[0].total_time_ms;
    rows.push({
      slug: m.slug, course: m.display_name, cc,
      your_ms: m.total_time_ms, your_rank, field_size: lb.length,
      wr_ms, off_wr_pct: off(m.total_time_ms),
      next_rank_ms,
      gap_to_next_ms: next_rank_ms != null ? m.total_time_ms - next_rank_ms : null,
      leader_ms, leader_off_wr_pct: off(leader_ms), leads,
    });
  }
  return rows;
}

import type { DatabaseSync } from 'node:sqlite';
import { courseLeaderboard, currentWr } from './reads';
import { overallStandings } from './leaderboards';
import { territoryOwners } from './reads';

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

export interface Headline {
  turf:  { pct: number; rank: number | null };
  time:  { total_ms: number; rank: number | null };
  golf:  { points: number; rank: number | null };
  offwr: { avg_pct: number | null; rank: number | null };
}

/** Mean % off the current WR across each player's WR-covered PBs (season+cc). */
export function avgOffWrByPlayer(db: DatabaseSync, seasonId: number, cc: number): Map<number, number> {
  const rows = db.prepare(
    `SELECT r.player_id AS pid, AVG((r.total_time_ms - w.record_ms) * 100.0 / w.record_ms) AS avg_pct
     FROM runs r
     JOIN world_records w ON w.course_id = r.course_id AND w.cc = r.cc AND w.is_current = 1 AND w.removed_at IS NULL
     WHERE r.season_id=? AND r.cc=? AND r.is_pb=1 AND r.total_time_ms IS NOT NULL
     GROUP BY r.player_id`
  ).all(seasonId, cc) as { pid: number; avg_pct: number }[];
  const m = new Map<number, number>();
  for (const r of rows) m.set(r.pid, r.avg_pct);
  return m;
}

export function playerHeadline(db: DatabaseSync, seasonId: number, cc: number, playerId: number): Headline {
  const rankOf = (i: number): number | null => (i >= 0 ? i + 1 : null);
  const standings = overallStandings(db, seasonId, cc); // {player_id, display_name, total_ms, tracks, points}

  const byTime = [...standings].sort((a, b) => a.total_ms - b.total_ms || a.points - b.points);
  const timeRank = rankOf(byTime.findIndex((s) => s.player_id === playerId));
  const byGolf = [...standings].sort((a, b) => a.points - b.points || a.total_ms - b.total_ms);
  const golfRank = rankOf(byGolf.findIndex((s) => s.player_id === playerId));
  const me = standings.find((s) => s.player_id === playerId);

  // Turf: owned-course counts, ranked (owned desc, total_ms asc, name asc) — matches web turf.js.
  const owners = territoryOwners(db, seasonId, cc);
  const totalCourses = owners.length;
  const ownCount = new Map<number, number>();
  for (const o of owners) if (o.owner_player_id != null) ownCount.set(o.owner_player_id, (ownCount.get(o.owner_player_id) ?? 0) + 1);
  const turfRows = standings
    .map((s) => ({ id: s.player_id, name: s.display_name, owned: ownCount.get(s.player_id) ?? 0, total: s.total_ms }))
    .sort((a, b) => b.owned - a.owned || a.total - b.total || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  const turfRank = rankOf(turfRows.findIndex((r) => r.id === playerId));
  const owned = ownCount.get(playerId) ?? 0;
  const turfPct = totalCourses ? Math.round((owned / totalCourses) * 100) : 0;

  // % off WR: rank all players ascending (lower = sharper).
  const avg = avgOffWrByPlayer(db, seasonId, cc);
  const withAvg = [...avg.entries()].map(([id, v]) => ({ id, v })).sort((a, b) => a.v - b.v);
  const offIdx = withAvg.findIndex((x) => x.id === playerId);
  const offwr = offIdx >= 0 ? { avg_pct: withAvg[offIdx].v, rank: offIdx + 1 } : { avg_pct: null, rank: null };

  return {
    turf: { pct: turfPct, rank: turfRank },
    time: { total_ms: me?.total_ms ?? 0, rank: timeRank },
    golf: { points: me?.points ?? 0, rank: golfRank },
    offwr,
  };
}

import type { DatabaseSync } from 'node:sqlite';

type FinRow = { name: string; ms: number; ended_at: string };
function finishedRuns(db: DatabaseSync, seasonId: number, courseId: number, cc: number): FinRow[] {
  return db.prepare(
    `SELECT p.display_name AS name, r.total_time_ms AS ms, r.ended_at AS ended_at
     FROM runs r JOIN players p ON p.id = r.player_id
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.status='finished'
       AND r.total_time_ms IS NOT NULL AND r.ended_at IS NOT NULL
     ORDER BY r.ended_at ASC, r.id ASC`
  ).all(seasonId, courseId, cc) as FinRow[];
}

export interface ProgressionPoint { t: number; player: string; ms: number; }
export function recordProgression(db: DatabaseSync, seasonId: number, courseId: number, cc: number): ProgressionPoint[] {
  const out: ProgressionPoint[] = [];
  let best = Infinity;
  for (const r of finishedRuns(db, seasonId, courseId, cc)) {
    const t = Date.parse(r.ended_at);
    if (r.ms < best && Number.isFinite(t)) { best = r.ms; out.push({ t, player: r.name, ms: r.ms }); }
  }
  return out;
}

export interface Reign { player: string; from: number; to: number | null; ms: number | null; }
export function courseReigns(db: DatabaseSync, seasonId: number, courseId: number, cc: number): Reign[] {
  const best = new Map<string, number>();
  let leader: string | null = null, reignStart = 0;
  const reigns: Reign[] = [];
  for (const r of finishedRuns(db, seasonId, courseId, cc)) {
    const t = Date.parse(r.ended_at);
    if (!Number.isFinite(t)) continue;
    const cur = best.get(r.name);
    if (cur === undefined || r.ms < cur) best.set(r.name, r.ms);
    let lname: string | null = null, lmin = Infinity;
    for (const [n, m] of best) if (m < lmin) { lmin = m; lname = n; }
    if (lname !== leader) {
      if (leader !== null) reigns.push({ player: leader, from: reignStart, to: t, ms: t - reignStart });
      leader = lname; reignStart = t;
    }
  }
  if (leader !== null) reigns.push({ player: leader, from: reignStart, to: null, ms: null });
  return reigns;
}

export interface WrRow { t: number | null; holder_name: string | null; record_ms: number; video_url: string | null; }
export function wrHistoryRows(db: DatabaseSync, courseId: number, cc: number): WrRow[] {
  const rows = db.prepare(
    `SELECT holder_name, record_ms, video_url, achieved_at FROM world_records
     WHERE course_id=? AND cc=? AND removed_at IS NULL ORDER BY achieved_at ASC, id ASC`
  ).all(courseId, cc) as { holder_name: string | null; record_ms: number; video_url: string | null; achieved_at: string | null }[];
  return rows.map((r) => {
    const t = r.achieved_at ? Date.parse(r.achieved_at) : NaN;
    return { t: Number.isFinite(t) ? t : null, holder_name: r.holder_name, record_ms: r.record_ms, video_url: r.video_url };
  });
}

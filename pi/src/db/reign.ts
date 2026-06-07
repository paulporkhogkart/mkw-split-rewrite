import type { DatabaseSync } from 'node:sqlite';

export type ReignInfo = {
  previous_holder: string | null;
  reign_ms: number | null;
  is_same_person: boolean;
} | null;

/** Reign of the holder being dethroned (prevHolder), from world_records history.
 *  Walks newest->oldest; the reign starts at the oldest contiguous prevHolder row.
 *  Graceful: null duration when timestamps are missing. */
export function wrReign(
  db: DatabaseSync, courseId: number, cc: number,
  prevHolder: string | null, newHolder: string | null,
): ReignInfo {
  if (!prevHolder) return null;
  const rows = db.prepare(
    `SELECT holder_name, achieved_at FROM world_records
     WHERE course_id=? AND cc=? ORDER BY achieved_at DESC, id DESC`
  ).all(courseId, cc) as { holder_name: string | null; achieved_at: string | null }[];

  let reignStart: string | null = null;
  for (const r of rows) {
    if (r.holder_name === prevHolder) reignStart = r.achieved_at ?? reignStart;
    else if (reignStart !== null) break;   // passed the contiguous prevHolder block
  }
  const is_same_person = newHolder != null && newHolder === prevHolder;
  if (!reignStart) return { previous_holder: prevHolder, reign_ms: null, is_same_person };
  const ms = Date.now() - Date.parse(reignStart);
  return { previous_holder: prevHolder, reign_ms: Number.isFinite(ms) && ms >= 0 ? ms : null, is_same_person };
}

/** Reign of the course's current champion (excluding `excludeRunId`, the just-inserted PB).
 *  Best-times only improve, so the leaderboard is monotonic and a player's reign = the time
 *  since the lead last changed TO them. Single forward pass over finished runs: track each
 *  player's running best, and whenever the overall leader changes, reset the reign start to
 *  that run's timestamp. The leader at the end of the pass is the pre-new-PB champion. */
export function trackReign(
  db: DatabaseSync, seasonId: number, courseId: number, cc: number,
  newPlayer: string, excludeRunId: number,
): ReignInfo {
  const runs = db.prepare(
    `SELECT p.display_name AS name, r.total_time_ms AS ms, r.ended_at AS ended_at
     FROM runs r JOIN players p ON p.id = r.player_id
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.status='finished'
       AND r.total_time_ms IS NOT NULL AND r.ended_at IS NOT NULL AND r.id != ?
     ORDER BY r.ended_at ASC, r.id ASC`
  ).all(seasonId, courseId, cc, excludeRunId) as { name: string; ms: number; ended_at: string }[];
  if (runs.length === 0) return { previous_holder: null, reign_ms: null, is_same_person: false };

  const best = new Map<string, number>();
  let leader: string | null = null;
  let reignStart: string | null = null;
  for (const r of runs) {
    const cur = best.get(r.name);
    if (cur === undefined || r.ms < cur) best.set(r.name, r.ms);
    let lname: string | null = null;
    let lmin = Infinity;
    for (const [n, m] of best) if (m < lmin) { lmin = m; lname = n; }
    if (lname !== leader) { leader = lname; reignStart = r.ended_at; }   // the lead changed here
  }

  const is_same_person = leader === newPlayer;
  const reign_ms = reignStart ? Date.now() - Date.parse(reignStart) : null;
  return {
    previous_holder: leader,
    reign_ms: reign_ms != null && reign_ms >= 0 ? reign_ms : null,
    is_same_person,
  };
}

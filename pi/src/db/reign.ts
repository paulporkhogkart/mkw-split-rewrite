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

/** The current course leader's reign (no exclusion) — for the /leaderboard track footer.
 *  Delegates to trackReign with excludeRunId=-1 (no run excluded) and newPlayer=''
 *  so is_same_person is always false; previous_holder is the current leader. */
export function courseLeaderReign(db: DatabaseSync, seasonId: number, courseId: number, cc: number): ReignInfo {
  return trackReign(db, seasonId, courseId, cc, '', -1);
}

export type OverallReign = { leader: string | null; reign_ms: number | null };

/** How long the current overall leader has held the top of the OVERALL standings. Forward-replay of
 *  all finished runs: maintain each player's per-course best, recompute the overall leader (min summed
 *  total, points tiebreak) after each run, and reset the reign start when the overall leader changes.
 *  Mirrors legacy get_overall_reign_duration but in one pass. Graceful null when timestamps missing. */
export function overallReign(db: DatabaseSync, seasonId: number, cc: number): OverallReign {
  const runs = db.prepare(
    `SELECT p.display_name AS name, r.course_id AS course, r.total_time_ms AS ms, r.ended_at AS ended_at
     FROM runs r JOIN players p ON p.id = r.player_id
     WHERE r.season_id=? AND r.cc=? AND r.status='finished' AND r.total_time_ms IS NOT NULL AND r.ended_at IS NOT NULL
     ORDER BY r.ended_at ASC, r.id ASC`
  ).all(seasonId, cc) as { name: string; course: number; ms: number; ended_at: string }[];
  if (runs.length === 0) return { leader: null, reign_ms: null };

  const best = new Map<string, Map<number, number>>();   // player -> (course -> best ms)
  let leader: string | null = null;
  let reignStart: string | null = null;
  const overallLeader = (): string | null => {
    let lname: string | null = null, lmin = Infinity, lpts = Infinity;
    // rank each course to compute points; cheap at friend-group scale.
    const courseSet = new Set<number>();
    for (const m of best.values()) for (const c of m.keys()) courseSet.add(c);
    const totals = new Map<string, { total: number; pts: number }>();
    for (const [name, m] of best) totals.set(name, { total: [...m.values()].reduce((a, b) => a + b, 0), pts: 0 });
    for (const c of courseSet) {
      const ranked = [...best.entries()].filter(([, m]) => m.has(c))
        .map(([name, m]) => ({ name, ms: m.get(c)! })).sort((a, b) => a.ms - b.ms);
      ranked.forEach((e, i) => { totals.get(e.name)!.pts += i + 1; });
    }
    for (const [name, t] of totals) if (t.total < lmin || (t.total === lmin && t.pts < lpts)) { lmin = t.total; lpts = t.pts; lname = name; }
    return lname;
  };
  for (const r of runs) {
    let m = best.get(r.name); if (!m) { m = new Map(); best.set(r.name, m); }
    const cur = m.get(r.course);
    if (cur === undefined || r.ms < cur) m.set(r.course, r.ms);
    const l = overallLeader();
    if (l !== leader) { leader = l; reignStart = r.ended_at; }
  }
  const reign_ms = reignStart ? Date.now() - Date.parse(reignStart) : null;
  return { leader, reign_ms: reign_ms != null && reign_ms >= 0 ? reign_ms : null };
}

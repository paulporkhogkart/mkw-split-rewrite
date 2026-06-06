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

export function myPbs(db: DatabaseSync, seasonId: number, playerId: number) {
  return db.prepare(
    `SELECT c.slug AS course_slug, r.cc, r.total_time_ms
     FROM runs r JOIN courses c ON c.id = r.course_id
     WHERE r.season_id=? AND r.player_id=? AND r.is_pb=1 AND r.total_time_ms IS NOT NULL
     ORDER BY c.slug`
  ).all(seasonId, playerId);
}

/** The caller's PB on a course: total + per-lap cumulative splits {lap_index: lap_time_ms}.
 *  Empty splits (and possibly null total) when there is no live PB, or the PB is a
 *  legacy total-time-only run with no stored laps. Mirrors the engine's old emit_pb_splits. */
export function myPbSplits(db: DatabaseSync, seasonId: number, playerId: number, courseId: number, cc: number):
    { total_ms: number | null; splits: Record<number, number> } {
  const run = db.prepare(
    `SELECT id, total_time_ms FROM runs
     WHERE season_id=? AND player_id=? AND course_id=? AND cc=? AND is_pb=1`
  ).get(seasonId, playerId, courseId, cc) as { id: number; total_time_ms: number | null } | undefined;
  if (!run) return { total_ms: null, splits: {} };
  const laps = db.prepare(
    `SELECT lap_index, lap_time_ms FROM run_laps WHERE run_id=? ORDER BY lap_index`
  ).all(run.id) as { lap_index: number; lap_time_ms: number | null }[];
  const splits: Record<number, number> = {};
  for (const l of laps) if (l.lap_time_ms != null) splits[l.lap_index] = l.lap_time_ms;
  return { total_ms: run.total_time_ms ?? null, splits };
}

export type Trail = { player_id: number; player: string; total_ms: number | null; is_me: boolean; points: number[][] };

/** Every roster player's PB trail for a course (fastest first), each tagged is_me when
 *  it matches meId. Point-less PBs (legacy total-time-only) are omitted - no trail. */
export function courseTrails(db: DatabaseSync, seasonId: number, courseId: number, cc: number, meId: number | null): Trail[] {
  const runs = db.prepare(
    `SELECT r.id, r.player_id, p.display_name, r.total_time_ms
     FROM runs r JOIN players p ON p.id = r.player_id
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.is_pb=1
     ORDER BY r.total_time_ms ASC`
  ).all(seasonId, courseId, cc) as { id: number; player_id: number; display_name: string; total_time_ms: number | null }[];
  const out: Trail[] = [];
  const ptStmt = db.prepare(`SELECT t_ms, cx, cy, score FROM run_points WHERE run_id=? ORDER BY t_ms`);
  for (const r of runs) {
    const pts = ptStmt.all(r.id) as { t_ms: number; cx: number; cy: number; score: number }[];
    if (pts.length === 0) continue;   // legacy / point-less PB: no trail
    out.push({
      player_id: r.player_id, player: r.display_name, total_ms: r.total_time_ms ?? null,
      is_me: meId != null && r.player_id === meId,
      points: pts.map((p) => [p.t_ms, p.cx, p.cy, p.score]),
    });
  }
  return out;
}

export type RosterRow = { player_id: number; display_name: string };

/** The active season's roster (players), for the per-player trail settings list. */
export function roster(db: DatabaseSync, seasonId: number): RosterRow[] {
  return db.prepare(
    `SELECT p.id AS player_id, p.display_name
     FROM season_rosters sr JOIN players p ON p.id = sr.player_id
     WHERE sr.season_id=?
     ORDER BY p.display_name`
  ).all(seasonId) as RosterRow[];
}

export type TrailMode = 'none' | 'pbs' | 'best' | 'last' | 'last_pb' | 'all';
export type PlayerTrailRun = { run_id: number; total_ms: number | null; status: string; is_pb: boolean; points: number[][] };
type TrailRow = { id: number; total_time_ms: number | null; status: string; is_pb: number };

/** One player's selected runs' trails for a course, ordered by rank (rank 0 first) so the
 *  client can fade by index. Modes: pbs (the is_pb run), best (fastest N finished), last
 *  (newest N), last_pb (newest N plus the PB if it's older than that N), all (newest first).
 *  Each run carries is_pb so the client can accent the PB. Runs with no points (legacy
 *  total-only) are omitted. */
export function playerTrails(db: DatabaseSync, seasonId: number, playerId: number,
                             courseId: number, cc: number, mode: TrailMode, n: number): PlayerTrailRun[] {
  if (mode === 'none') return [];
  // EXISTS(points) up front so LIMIT counts only runs that actually have a trail
  // (legacy total-only runs have no points and must not consume a best/last slot).
  const base = `SELECT id, total_time_ms, status, is_pb FROM runs
                WHERE season_id=? AND player_id=? AND course_id=? AND cc=?
                  AND EXISTS (SELECT 1 FROM run_points rp WHERE rp.run_id = runs.id)`;
  const key = [seasonId, playerId, courseId, cc];
  const lim = Math.max(1, n);
  let rows: TrailRow[];
  if (mode === 'pbs') {
    rows = db.prepare(`${base} AND is_pb=1`).all(...key) as TrailRow[];
  } else if (mode === 'best') {
    rows = db.prepare(`${base} AND status='finished' AND total_time_ms IS NOT NULL ORDER BY total_time_ms ASC LIMIT ?`).all(...key, lim) as TrailRow[];
  } else if (mode === 'all') {
    rows = db.prepare(`${base} ORDER BY COALESCE(ended_at, started_at, '') DESC, id DESC`).all(...key) as TrailRow[];
  } else { // 'last' and 'last_pb' both take the newest N first
    rows = db.prepare(`${base} ORDER BY COALESCE(ended_at, started_at, '') DESC, id DESC LIMIT ?`).all(...key, lim) as TrailRow[];
    if (mode === 'last_pb' && !rows.some((r) => r.is_pb === 1)) {
      const pb = db.prepare(`${base} AND is_pb=1`).get(...key) as TrailRow | undefined;
      if (pb) rows.push(pb);   // append the PB when it's older than the last N
    }
  }
  const ptStmt = db.prepare(`SELECT t_ms, cx, cy, score FROM run_points WHERE run_id=? ORDER BY t_ms`);
  const out: PlayerTrailRun[] = [];
  for (const r of rows) {
    const pts = ptStmt.all(r.id) as { t_ms: number; cx: number; cy: number; score: number }[];
    if (pts.length === 0) continue;   // legacy / point-less run: no trail
    out.push({ run_id: r.id, total_ms: r.total_time_ms ?? null, status: r.status, is_pb: r.is_pb === 1,
               points: pts.map((p) => [p.t_ms, p.cx, p.cy, p.score]) });
  }
  return out;
}

import type { DatabaseSync } from 'node:sqlite';
import { courseIdBySlug } from './seasons';
import { courseLeaderboard, currentWr } from './reads';
import { recordProgression, courseReigns, wrHistoryRows, ProgressionPoint, Reign, WrRow } from './courseHistory';

export interface CourseSplits {
  laps: number;
  perPlayer: { player_id: number; display_name: string; color: string | null; best: (number | null)[] }[];
  fieldIdeal: (number | null)[];
}

/** Each player's fastest lap-duration per lap index over finished runs, plus the per-lap
 *  field ideal (min across players). `best`/`fieldIdeal` are length `laps` (max lap seen). */
export function courseSplits(db: DatabaseSync, seasonId: number, courseId: number, cc: number): CourseSplits {
  const rows = db.prepare(
    `SELECT r.player_id AS pid, p.display_name AS name, p.color AS color,
            rl.lap_index AS lap, MIN(rl.lap_time_ms) AS best
     FROM run_laps rl
     JOIN runs r ON r.id = rl.run_id
     JOIN players p ON p.id = r.player_id
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.status='finished' AND rl.lap_time_ms IS NOT NULL
     GROUP BY r.player_id, rl.lap_index`
  ).all(seasonId, courseId, cc) as { pid: number; name: string; color: string | null; lap: number; best: number }[];

  const laps = rows.reduce((m, r) => Math.max(m, r.lap), 0);
  const byPlayer = new Map<number, { name: string; color: string | null; best: (number | null)[] }>();
  for (const r of rows) {
    let e = byPlayer.get(r.pid);
    if (!e) { e = { name: r.name, color: r.color, best: new Array(laps).fill(null) }; byPlayer.set(r.pid, e); }
    e.best[r.lap - 1] = r.best;
  }
  const perPlayer = [...byPlayer.entries()]
    .map(([player_id, e]) => ({ player_id, display_name: e.name, color: e.color, best: e.best }))
    .sort((a, b) => (a.display_name < b.display_name ? -1 : a.display_name > b.display_name ? 1 : 0));
  const fieldIdeal: (number | null)[] = new Array(laps).fill(null);
  for (let l = 0; l < laps; l++)
    for (const p of perPlayer) { const v = p.best[l]; if (v != null && (fieldIdeal[l] == null || v < fieldIdeal[l]!)) fieldIdeal[l] = v; }
  return { laps, perPlayer, fieldIdeal };
}

export interface CourseLeaderRow {
  player_id: number; display_name: string; color: string | null;
  total_time_ms: number; total_time_str: string | null; rank: number;
}
export interface CourseSummary {
  profile: { slug: string; display_name: string };
  wr: { holder_name: string | null; record_ms: number; record_str: string | null; video_url: string | null; character: string | null; vehicle: string | null } | null;
  leaderboard: CourseLeaderRow[];
  splits: CourseSplits;
  history: { recordProgression: ProgressionPoint[]; reigns: Reign[]; wrHistory: WrRow[] };
}

/** The per-track hub payload, resolving :slug via courseIdBySlug. Null on unknown slug. */
export function courseSummary(db: DatabaseSync, seasonId: number, cc: number, slug: string): CourseSummary | null {
  const courseId = courseIdBySlug(db, slug);
  if (courseId == null) return null;
  const course = db.prepare('SELECT slug, display_name FROM courses WHERE id=?').get(courseId) as { slug: string; display_name: string };
  const colors = new Map<number, string | null>();
  for (const p of db.prepare('SELECT id, color FROM players').all() as { id: number; color: string | null }[]) colors.set(p.id, p.color);
  const leaderboard: CourseLeaderRow[] = courseLeaderboard(db, seasonId, courseId, cc)
    .map((r) => ({ player_id: r.player_id, display_name: r.display_name, color: colors.get(r.player_id) ?? null, total_time_ms: r.total_time_ms, total_time_str: r.total_time_str, rank: r.rank }));
  const wr = currentWr(db, courseId, cc);
  return {
    profile: { slug: course.slug, display_name: course.display_name },
    wr: wr ? { holder_name: wr.holder_name, record_ms: wr.record_ms, record_str: wr.record_str, video_url: wr.video_url, character: wr.character, vehicle: wr.vehicle } : null,
    leaderboard,
    splits: courseSplits(db, seasonId, courseId, cc),
    history: {
      recordProgression: recordProgression(db, seasonId, courseId, cc),
      reigns: courseReigns(db, seasonId, courseId, cc),
      wrHistory: wrHistoryRows(db, courseId, cc),
    },
  };
}

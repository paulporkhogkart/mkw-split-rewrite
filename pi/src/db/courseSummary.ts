import type { DatabaseSync } from 'node:sqlite';

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

import type { DatabaseSync } from 'node:sqlite';
import type { AttemptPayload } from './types';
import { slugify } from './slug';

/** Recordings longer than this (ms) are runaway captures (e.g. a stuck screen), not real
 *  attempts. Keep the run + laps for the record, but drop the (huge) trail. 11 minutes. */
export const OVER_LIMIT_MS = 11 * 60 * 1000;

export function timeToMs(t?: string | null): number | null {
  if (!t) return null;
  const m = /^(\d+):(\d{2})\.(\d{3})$/.exec(t.trim());
  if (!m) return null;
  return Number(m[1]) * 60000 + Number(m[2]) * 1000 + Number(m[3]);
}

/** Insert (or replace by attempt_id) a live attempt. Returns the run id. Caller resolves course_id externally; here we re-resolve from slug for safety. */
export function upsertRun(db: DatabaseSync, p: AttemptPayload, playerId: number, seasonId: number): number {
  const slug = slugify(p.course);
  const course = db.prepare('SELECT id FROM courses WHERE slug=?').get(slug) as { id: number } | undefined;
  if (!course) throw new Error(`unknown course: ${p.course} (${slug})`);
  const cc = p.cc ?? 150;
  const totalMs = timeToMs(p.total_time);

  db.exec('BEGIN');
  try {
    // Idempotency: clear any prior run for this attempt_id (children cascade via FK).
    const prior = db.prepare(
      "SELECT id FROM runs WHERE provenance='live' AND attempt_id=?"
    ).get(p.attempt_id) as { id: number } | undefined;
    if (prior) db.prepare('DELETE FROM runs WHERE id=?').run(prior.id);

    const info = db.prepare(
      `INSERT INTO runs(attempt_id, season_id, player_id, course_id, cc, status, provenance,
                        started_at, ended_at, total_time_ms, total_time_str, character, kart, costume,
                        coins_gained, coins_lost, mushrooms_used, is_pb)
       VALUES (?,?,?,?,?,?, 'live', ?,?,?,?,?,?,?, ?,?,?, 0)`
    ).run(p.attempt_id, seasonId, playerId, course.id, cc, p.status,
          p.started_at ?? null, p.ended_at ?? null, totalMs, p.total_time ?? null,
          p.character ?? null, p.kart ?? null, p.costume ?? null,
          p.coins_gained ?? null, p.coins_lost ?? null, p.mushrooms_used ?? null);
    const runId = Number(info.lastInsertRowid);

    const lapStmt = db.prepare(
      'INSERT INTO run_laps(run_id, lap_index, lap_time_ms, lap_time_str, coins, shrooms) VALUES (?,?,?,?,?,?)'
    );
    for (const lap of p.laps ?? []) lapStmt.run(runId, lap.lap, lap.time_ms, lap.time_str ?? null, lap.coins ?? null, lap.shrooms ?? null);

    // Runaway guard: a recording past OVER_LIMIT_MS is a stuck capture — keep the run + laps,
    // but don't store its trail. Boundary inclusive (exactly at the limit is still stored).
    const pts = p.points ?? [];
    const maxT = pts.reduce((m, pt) => Math.max(m, pt[0]), 0);
    if (maxT <= OVER_LIMIT_MS) {
      const ptStmt = db.prepare('INSERT INTO run_points(run_id, t_ms, cx, cy, score, lap) VALUES (?,?,?,?,?,?)');
      for (const [t, cx, cy, sc, lap] of pts) ptStmt.run(runId, t, cx, cy, sc, lap ?? null);
    }

    db.exec('COMMIT');
    return runId;
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }
}

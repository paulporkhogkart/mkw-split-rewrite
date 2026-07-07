import type { DatabaseSync } from 'node:sqlite';
import type { AttemptPayload } from './types';
import { slugify } from './slug';
import { insertTrail } from './trails';
import type { TrailPoint } from './trailCodec';


/** Recordings longer than this (ms) are runaway captures (e.g. a stuck screen), not real
 *  attempts. Keep the run + laps for the record, but drop the (huge) trail. 11 minutes. */
export const OVER_LIMIT_MS = 11 * 60 * 1000;

/** Store a payload trail as a run_trails blob. A malformed trail (e.g. non-monotonic
 *  t_ms — the engine never emits one) is dropped like a runaway: the run + laps
 *  persist, the trail doesn't, with a loud log. Returns whether a trail was stored. */
function storeTrail(db: DatabaseSync, runId: number, pts: NonNullable<AttemptPayload['points']>): boolean {
  const rows: TrailPoint[] = pts.map(([t, cx, cy, score, lap]) => ({ t_ms: t, cx, cy, score, lap: lap ?? null }));
  try {
    insertTrail(db, runId, rows);
    return true;
  } catch (e) {
    console.error(`[ingest] dropping malformed trail for run ${runId}:`, e);
    return false;
  }
}

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
                        coins_gained, coins_lost, mushrooms_used, is_pb, source)
       VALUES (?,?,?,?,?,?, 'live', ?,?,?,?,?,?,?, ?,?,?, 0, ?)`
    ).run(p.attempt_id, seasonId, playerId, course.id, cc, p.status,
          p.started_at ?? null, p.ended_at ?? null, totalMs, p.total_time ?? null,
          p.character ?? null, p.kart ?? null, p.costume ?? null,
          p.coins_gained ?? null, p.coins_lost ?? null, p.mushrooms_used ?? null,
          p.source ?? null);
    const runId = Number(info.lastInsertRowid);

    const lapStmt = db.prepare(
      'INSERT INTO run_laps(run_id, lap_index, lap_time_ms, lap_time_str, coins, shrooms) VALUES (?,?,?,?,?,?)'
    );
    for (const lap of p.laps ?? []) lapStmt.run(runId, lap.lap, lap.time_ms, lap.time_str ?? null, lap.coins ?? null, lap.shrooms ?? null);

    // Runaway guard: a recording past OVER_LIMIT_MS is a stuck capture — keep the run + laps,
    // but don't store its trail. Boundary inclusive (exactly at the limit is still stored).
    const pts = p.points ?? [];
    const maxT = pts.reduce((m, pt) => Math.max(m, pt[0]), 0);
    if (pts.length > 0 && maxT <= OVER_LIMIT_MS) storeTrail(db, runId, pts);

    db.exec('COMMIT');
    return runId;
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }
}

/** The id of an existing finished run with identical total_time_ms for this
 *  (season, player, course, cc), or null. The ghost dedup key. */
export function findGhostMatch(db: DatabaseSync, seasonId: number, playerId: number,
                               courseId: number, cc: number, totalMs: number | null): number | null {
  if (totalMs === null) return null;
  const row = db.prepare(
    `SELECT id FROM runs WHERE season_id=? AND player_id=? AND course_id=? AND cc=?
     AND status='finished' AND total_time_ms=? ORDER BY id LIMIT 1`
  ).get(seasonId, playerId, courseId, cc, totalMs) as { id: number } | undefined;
  return row ? row.id : null;
}

/** Gap-fill an existing run from a ghost payload: fill null character/kart/costume,
 *  add run_laps + a trail (run_trails) ONLY if the run has none, set coin/mushroom totals
 *  only where null, and mark source='ghost'. Never overwrites existing data. */
export function enrichRunFromGhost(db: DatabaseSync, runId: number, p: AttemptPayload): { trailAdded: boolean } {
  db.exec('BEGIN');
  try {
    db.prepare(
      `UPDATE runs SET
         character      = COALESCE(character, ?),
         kart           = COALESCE(kart, ?),
         costume        = COALESCE(costume, ?),
         coins_gained   = COALESCE(coins_gained, ?),
         coins_lost     = COALESCE(coins_lost, ?),
         mushrooms_used = COALESCE(mushrooms_used, ?),
         source         = 'ghost'
       WHERE id=?`
    ).run(p.character ?? null, p.kart ?? null, p.costume ?? null,
          p.coins_gained ?? null, p.coins_lost ?? null, p.mushrooms_used ?? null, runId);

    const hasLaps = (db.prepare('SELECT COUNT(*) c FROM run_laps WHERE run_id=?').get(runId) as { c: number }).c > 0;
    if (!hasLaps) {
      const lapStmt = db.prepare(
        'INSERT INTO run_laps(run_id, lap_index, lap_time_ms, lap_time_str, coins, shrooms) VALUES (?,?,?,?,?,?)'
      );
      for (const lap of p.laps ?? []) lapStmt.run(runId, lap.lap, lap.time_ms, lap.time_str ?? null, lap.coins ?? null, lap.shrooms ?? null);
    }

    const hasPts = (db.prepare('SELECT COUNT(*) c FROM run_trails WHERE run_id=?').get(runId) as { c: number }).c > 0;
    const pts = p.points ?? [];
    const maxT = pts.reduce((m, pt) => Math.max(m, pt[0]), 0);
    let trailAdded = false;
    if (!hasPts && pts.length > 0 && maxT <= OVER_LIMIT_MS) trailAdded = storeTrail(db, runId, pts);
    db.exec('COMMIT');
    return { trailAdded };
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }
}

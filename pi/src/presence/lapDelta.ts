// pi/src/presence/lapDelta.ts
// LiveSplit-style per-lap delta: computed only from the completed laps' digit-read
// durations (presence frame splits_ms) against the PB run's run_laps. The card holds
// the latest value until the next lap line. `gained` = this lap beat the PB run's
// same lap; `gold` = it beat the player's best-ever time for that lap (any finished
// run); ahead/behind is the sign of delta_ms. Colour mapping happens client-side.
import type { DatabaseSync } from 'node:sqlite';
import { activeSeasonId, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';
import { pbRunFor } from '../db/pb';

export interface LapDeltaResult { lap: number; delta_ms: number; gained: boolean; gold: boolean; }

export type LapDelta = ((
  playerId: number, course: string | null | undefined, splitsMs: number[] | null | undefined,
) => LapDeltaResult | null) & {
  /** Drop a course's cached PB laps + golds (chained into the model-rebuild
   *  invalidation, which fires on every finished trailed upload - keeps golds
   *  fresh across a session without per-frame queries). */
  invalidateCourse(courseId: number): void;
};

export function makeLapDelta(db: DatabaseSync, cc = 150): LapDelta {
  // Per (course, player): the PB run's lap durations (keyed by run id, so a new PB
  // self-heals) + lazily-filled best-ever segment per lap (one query per lap line).
  const cache = new Map<string, { runId: number; pbLaps: number[]; golds: Map<number, number | null> }>();

  const fn = ((playerId, course, splitsMs) => {
    if (!course || !splitsMs || splitsMs.length === 0) return null;
    const courseId = courseIdBySlug(db, slugify(course));
    if (courseId == null) return null;
    const season = activeSeasonId(db);
    const pb = pbRunFor(db, season, playerId, courseId, cc);
    if (!pb) return null;
    const key = `${courseId}:${playerId}`;
    let entry = cache.get(key);
    if (!entry || entry.runId !== pb.id) {
      const pbLaps = (db.prepare(
        'SELECT lap_time_ms FROM run_laps WHERE run_id=? ORDER BY lap_index'
      ).all(pb.id) as { lap_time_ms: number }[]).map((r) => r.lap_time_ms);
      entry = { runId: pb.id, pbLaps, golds: new Map() };
      cache.set(key, entry);
    }
    // No per-lap data on the PB (e.g. a carryover seed): no comparison to run.
    const k = Math.min(splitsMs.length, entry.pbLaps.length);
    if (k === 0) return null;
    let live = 0, ref = 0;
    for (let i = 0; i < k; i++) { live += splitsMs[i]; ref += entry.pbLaps[i]; }
    let gold = entry.golds.get(k);
    if (gold === undefined) {
      const row = db.prepare(
        `SELECT MIN(rl.lap_time_ms) AS m FROM run_laps rl JOIN runs r ON r.id = rl.run_id
         WHERE r.season_id=? AND r.player_id=? AND r.course_id=? AND r.cc=?
           AND r.status='finished' AND rl.lap_index=?`
      ).get(season, playerId, courseId, cc, k) as { m: number | null };
      gold = row.m;
      entry.golds.set(k, gold);
    }
    return {
      lap: k,
      delta_ms: live - ref,
      gained: splitsMs[k - 1] < entry.pbLaps[k - 1],
      gold: gold != null && splitsMs[k - 1] < gold,
    };
  }) as LapDelta;

  fn.invalidateCourse = (courseId: number) => {
    const prefix = `${courseId}:`;
    for (const key of [...cache.keys()]) if (key.startsWith(prefix)) cache.delete(key);
  };
  return fn;
}
